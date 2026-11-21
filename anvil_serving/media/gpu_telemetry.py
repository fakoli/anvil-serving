"""Optional, isolated device-wide NVML v1 memory telemetry for the Linux media pool."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

from .errors import MediaError


UUID = re.compile(r"GPU-[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}")
_RESPONSE = struct.Struct("!40sQQ")
_QUERY = b"?"
DEADLINE_SECONDS = 4.0


def valid_options(value):
    return (
        isinstance(value, dict)
        and set(value) == {"provider", "deviceUuid"}
        and value["provider"] == "nvml-v1"
        and isinstance(value["deviceUuid"], str)
        and UUID.fullmatch(value["deviceUuid"]) is not None
    )


def _unavailable():
    return MediaError("gpu_telemetry_unavailable", "physical GPU telemetry unavailable", status=503)


def _reserve_breached():
    return MediaError("worker_pool_refused", "physical GPU reserve was breached", status=503)


def _helper_command(fd):
    return [sys.executable, "-m", __name__, str(fd), str(os.getpid())]


class NvmlSampler:
    """One in-flight read; failure poisons the prestarted helper until a new pool run."""

    def __init__(self, device_uuid, *, reserve_mib=0, timeout=DEADLINE_SECONDS):
        if not isinstance(device_uuid, str) or UUID.fullmatch(device_uuid) is None:
            raise ValueError("invalid GPU UUID")
        if not 0 < timeout <= DEADLINE_SECONDS:
            raise ValueError("invalid GPU telemetry deadline")
        if type(reserve_mib) is not int or reserve_mib < 0:
            raise ValueError("invalid physical GPU reserve")
        self._uuid = device_uuid.lower().encode("ascii")
        self._reserve = reserve_mib
        self._reserve_breached = False
        self._timeout = timeout
        self._lock = threading.Lock()
        self._process = None
        self._socket = None
        self._closed = False
        self._frozen = threading.Event()
        self.epoch = 0

    def start(self):
        """Called by the driver before spawning renderers, never on a request."""
        with self._lock:
            if self._reserve_breached:
                raise _reserve_breached()
            if self._closed or self._socket is not None:
                raise _unavailable()
            try:
                self._start()
            except OSError:
                raise _unavailable() from None

    def _start(self):
        if self._process is not None and self._process.poll() is None:
            # Never launch a replacement while a stuck provider is unreaped.
            raise _unavailable()
        local, child = socket.socketpair()
        try:
            with child:
                self._process = subprocess.Popen(
                    _helper_command(child.fileno()), pass_fds=(child.fileno(),),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        except BaseException:
            local.close()
            raise
        self._socket = local

    def _discard(self):
        self.epoch += 1
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        if self._process is not None:
            if self._process.poll() is None:
                try:
                    self._process.kill()
                except OSError:
                    # Keep custody for the pool's existing descendant sweep.
                    pass
            if self._process.poll() is not None:
                self._process = None

    @contextmanager
    def contain(self):
        """Stop the helper before the pool sweeps native orphans; fence concurrent reads."""
        self._frozen.set()
        try:
            with self._lock:
                self._discard()
                yield
        finally:
            self._frozen.clear()

    @contextmanager
    def validate(self, epoch):
        """An admission may hold this through a DB update, never waiting for a sample."""
        if not self._lock.acquire(blocking=False):
            raise _unavailable()
        try:
            if self._reserve_breached:
                raise _reserve_breached()
            if self._frozen.is_set() or self._closed or self._socket is None or epoch != self.epoch:
                raise _unavailable()
            yield
        finally:
            self._lock.release()

    def close(self):
        self._frozen.set()
        with self._lock:
            self._closed = True
            self._discard()
            if self._process is not None:
                try:
                    self._process.wait(timeout=.25)
                except (subprocess.TimeoutExpired, ChildProcessError):
                    return
                self._process = None

    def sample(self):
        return self.sample_with_revision()[1]

    def sample_with_revision(self):
        deadline = time.monotonic() + self._timeout

        def remaining():
            t = deadline - time.monotonic()
            if t <= 0:
                raise TimeoutError
            return t

        if self._frozen.is_set() or not self._lock.acquire(timeout=self._timeout):
            raise _unavailable()
        try:
            if self._reserve_breached:
                raise _reserve_breached()
            remaining()
            if self._frozen.is_set() or self._closed or self._socket is None:
                raise _unavailable()
            self._socket.settimeout(remaining())
            self._socket.sendall(_QUERY)
            result = bytearray()
            while len(result) < _RESPONSE.size:
                self._socket.settimeout(remaining())
                data = self._socket.recv(_RESPONSE.size - len(result))
                if not data:
                    raise EOFError
                result.extend(data)
            remaining()
            uuid, total, free = _RESPONSE.unpack(result)
            if uuid.lower() != self._uuid or not 0 <= free <= total < (1 << 63) or total < (1 << 20):
                raise ValueError
            reading = total >> 20, free >> 20
            # Publish every valid reading before releasing the provider lock.
            # Observing a breach anywhere fences admitting or submitting work
            # even before the drive thread can acquire the pool lock.
            self.epoch += 1
            self._reserve_breached = reading[1] < self._reserve
            return self.epoch, reading
        except (OSError, EOFError, ValueError):
            self._discard()
            raise _unavailable() from None
        finally:
            self._lock.release()


class _MemoryV1(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in ("total", "free", "used")]


def _native_serve(sock):
    # NVML v1 matches the existing physical free/total shape. Never use CUDA
    # allocator headroom or substitute v2's reserved-memory accounting.
    nvml = ctypes.CDLL("libnvidia-ml.so.1")
    if nvml.nvmlInit_v2():
        return
    count = ctypes.c_uint()
    handle = ctypes.c_void_p()
    while sock.recv(1) == _QUERY:
        if (nvml.nvmlDeviceGetCount_v2(ctypes.byref(count)) or count.value != 1
                or nvml.nvmlDeviceGetHandleByIndex_v2(ctypes.c_uint(0), ctypes.byref(handle))):
            return
        uuid = ctypes.create_string_buffer(96)
        memory = _MemoryV1()
        if (nvml.nvmlDeviceGetUUID(handle, uuid, ctypes.c_uint(len(uuid)))
                or nvml.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(memory))
                or UUID.fullmatch(uuid.value.decode("ascii")) is None):
            return
        sock.sendall(_RESPONSE.pack(uuid.value, memory.total, memory.free))


def _main(fd, parent_pid):
    # The helper owns no renderer children and must die with the pool.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) or os.getppid() != parent_pid:
        return
    with socket.socket(fileno=fd) as sock:
        _native_serve(sock)


if __name__ == "__main__":
    _main(int(sys.argv[1]), int(sys.argv[2]))
