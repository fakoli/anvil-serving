"""Bounded child execution for the offline qualification guest.

Only the qualification runner supplies argv and descriptors. This module never
reads shell configuration, inherits credentials, or retains ordinary console logs.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import resource
import selectors
import signal
import subprocess
import time

from .qualification import _error

_GiB = 1024 ** 3


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    output: bytes
    elapsed_ms: int
    peak_rss_bytes: int
    output_bytes: int


def _rss(pid: int) -> int:
    # statm belongs to our still-owned, unreaped direct child. Failure to observe
    # a live child is a failed resource check, not a zero-memory measurement.
    raw = Path(f"/proc/{pid}/statm").read_bytes()
    fields = raw.split()
    if len(raw) > 1024 or len(fields) != 7:
        raise OSError("invalid process resource sample")
    return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")


def _terminate(process: subprocess.Popen) -> None:
    # A child that has not been waited cannot have its PID reused. Its new
    # session also identifies the only process group we are allowed to signal.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=2)


def _exited(process: subprocess.Popen) -> bool:
    # Observe without reaping so the PID/group identity remains owned until
    # final cleanup. Popen.poll() would reap an exited leader too early.
    return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def execute(argv: list[str], *, home: Path, timeout: float, cpus: tuple[int, ...],
            pass_fds: tuple[int, ...] = (), cwd: Path | None = None,
            environment: dict[str, str] | None = None, maximum_output: int = 1024 * 1024,
            retained_prefix: bytes | None = None, maximum_record: int = 64 * 1024,
            rss_limit: int = 3 * _GiB, address_limit: int = 8 * _GiB,
            file_limit: int = 9 * _GiB, watched_file: Path | None = None) -> ProcessResult:
    """Run one owned process; every bound violation terminates and fails it.

    Prefix mode retains exactly one line starting with the fixed result marker.
    All console bytes still count against the total output bound. Normal exit
    is required; a success marker followed by a hung child cannot pass.
    """
    if timeout <= 0 or not cpus or maximum_output < 1 or maximum_record < 1:
        raise _error("config-invalid", "VM process limits are invalid")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C", "LC_ALL": "C",
           "XDG_CONFIG_HOME": str(home / "config"), "XDG_CACHE_HOME": str(home / "cache"),
           "XDG_DATA_HOME": str(home / "data")}
    env.update(environment or {})

    def limits() -> None:
        os.sched_setaffinity(0, cpus)
        resource.setrlimit(resource.RLIMIT_AS, (address_limit, address_limit))
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    process = None
    output = bytearray()
    line = bytearray()
    total = peak = 0
    record_seen = False
    started = time.monotonic()
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, cwd=cwd, env=env,
                                   close_fds=True, pass_fds=pass_fds,
                                   start_new_session=True, preexec_fn=limits)
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map() or not _exited(process):
                if time.monotonic() - started > timeout:
                    raise TimeoutError
                if not _exited(process):
                    try:
                        peak = max(peak, _rss(process.pid))
                    except FileNotFoundError:
                        if not _exited(process):
                            raise
                    if peak > rss_limit:
                        raise OSError("process memory bound exceeded")
                if watched_file is not None and watched_file.stat().st_size > file_limit:
                    raise OSError("process disk bound exceeded")
                for key, _ in selector.select(0.1):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > maximum_output:
                        raise OSError("process output bound exceeded")
                    if retained_prefix is None:
                        output.extend(chunk)
                        continue
                    line.extend(chunk)
                    while b"\n" in line:
                        content, _, remainder = line.partition(b"\n")
                        line = bytearray(remainder)
                        if content.startswith(retained_prefix):
                            if record_seen or len(content) > maximum_record:
                                raise OSError("invalid guest result framing")
                            output.extend(content[len(retained_prefix):].rstrip(b"\r"))
                            record_seen = True
                    if len(line) > maximum_record:
                        raise OSError("guest console line bound exceeded")
        # Dispose of any unexpected descendants before reaping the leader.
        # QEMU and the fixed helper tools must not leave background processes.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        code = process.wait(timeout=1)
        if retained_prefix is not None and (not record_seen or line.startswith(retained_prefix)):
            raise OSError("incomplete guest result")
        return ProcessResult(code, bytes(output), round((time.monotonic() - started) * 1000), peak, total)
    except BaseException as exc:
        if process is not None:
            try:
                _terminate(process)
            except (OSError, subprocess.SubprocessError):
                raise _error("runner-failed", "VM child cleanup failed", execution_started=True, stage="cleanup") from None
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise _error("runner-timeout" if isinstance(exc, TimeoutError) else "runner-failed",
                     "VM child exceeded a limit or failed", execution_started=process is not None,
                     stage="execution") from None
    finally:
        if process is not None and process.stdout is not None:
            process.stdout.close()
