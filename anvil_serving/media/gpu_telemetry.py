"""Optional, isolated device-wide NVML v1 memory telemetry for the Linux media pool."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
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


class GpuTelemetryDeferred(MediaError):
    """An otherwise healthy read was superseded, or its I/O turn was busy."""

    def __init__(self):
        super().__init__("gpu_telemetry_deferred", "physical GPU telemetry must be retried", status=503)


class GpuTelemetryContained(MediaError):
    """A sample met containment; the caller must prove any benign transition."""

    def __init__(self):
        super().__init__("gpu_telemetry_unavailable", "physical GPU telemetry unavailable", status=503)


@dataclass(frozen=True)
class _Evidence:
    epoch: int
    process: subprocess.Popen | None = None
    started: float | None = None
    breached: bool = False
    contained: bool = False
    provider_failed: bool = False


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
        self._timeout = timeout
        self._lock = threading.Lock()  # Process/socket ownership and provider I/O.
        self._process = None
        self._socket = None
        self._closed = False
        self._frozen = threading.Event()
        # The I/O owner is the sole writer. One immutable reference publishes
        # all evidence at once; readers never block the telemetry deadline.
        self._evidence = _Evidence(0)

    @property
    def epoch(self):
        return self._evidence.epoch

    def start(self):
        """Called by the driver before spawning renderers, never on a request."""
        with self._lock:
            if self._evidence.breached:
                raise _reserve_breached()
            if self._closed or self._evidence.provider_failed or self._socket is not None:
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
                process = subprocess.Popen(
                    _helper_command(child.fileno()), pass_fds=(child.fileno(),),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        except BaseException:
            local.close()
            raise
        self._socket, self._process = local, process

    def _discard(self, *, contained=False):
        previous = self._evidence
        self._evidence = _Evidence(
            previous.epoch + 1, breached=previous.breached, contained=contained,
            provider_failed=previous.provider_failed or not contained,
        )
        sock, self._socket = self._socket, None
        if sock is not None:
            sock.close()
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
                self._discard(contained=True)
                yield
        finally:
            self._frozen.clear()

    @contextmanager
    def validate(self, epoch=None):
        """Check one point-in-time publication without holding a lock over caller work."""
        evidence = self._evidence
        if evidence.breached:
            raise _reserve_breached()
        if (self._frozen.is_set() or self._closed or evidence.started is None
                or evidence.process is None or evidence.process.poll() is not None
                or time.monotonic() - evidence.started >= self._timeout):
            raise _unavailable()
        # A concurrent publication superseded the observed evidence while
        # checking it. Never let an old high observation override a new low.
        if self._evidence is not evidence or (epoch is not None and epoch != evidence.epoch):
            raise GpuTelemetryDeferred()
        if self._frozen.is_set():
            raise _unavailable()
        yield

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
        try:
            return self.sample_with_revision()[1]
        except GpuTelemetryDeferred:
            # The driver cannot defer a physical check beyond its deadline.
            raise _unavailable() from None

    def sample_with_revision(self):
        started = time.monotonic()
        deadline = started + self._timeout

        def remaining():
            t = deadline - time.monotonic()
            if t <= 0:
                raise TimeoutError
            return t

        if self._evidence.breached:
            raise _reserve_breached()
        if self._frozen.is_set():
            raise GpuTelemetryContained()
        if not self._lock.acquire(timeout=self._timeout):
            raise GpuTelemetryDeferred()
        try:
            if self._evidence.breached:
                raise _reserve_breached()
            if self._closed:
                raise _unavailable()
            if self._frozen.is_set() or (self._socket is None and self._evidence.contained):
                raise GpuTelemetryContained()
            if self._socket is None:
                raise _unavailable()
            try:
                before_io = remaining()
            except TimeoutError:
                # If contention exhausted the turn before any byte was sent,
                # there is no partial response to drain or helper to discard.
                raise GpuTelemetryDeferred() from None
            self._socket.settimeout(before_io)
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
            remaining()
            breached = reading[1] < self._reserve
            epoch = self._evidence.epoch + 1
            if self._frozen.is_set() and not breached:
                raise GpuTelemetryContained()
            self._evidence = _Evidence(epoch, self._process, started, breached)
            if breached and self._frozen.is_set():
                raise _reserve_breached()
            return epoch, reading
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
