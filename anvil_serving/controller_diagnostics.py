"""Bounded, local-only subprocess primitives for controller diagnostics.

This module deliberately does not inspect Docker output or expose a generic
command runner.  Later controller diagnostic tasks compose its private capture
primitive with fixed Docker templates and safe projections.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional


SCHEMA_VERSION = "controller-diagnostics/v1"
MAX_CAPTURE_BYTES = 256 * 1024
CHILD_DEADLINE_SECONDS = 10.0
CLEANUP_SECONDS = 1.0
DEFAULT_LOG_TAIL = 100
_CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_STATES = frozenset(("ok", "unsupported", "unavailable", "timeout", "output-limit", "malformed"))

ProcessFactory = Callable[..., Any]
Clock = Callable[[], float]


@dataclass(frozen=True)
class ChildCapture:
    """Internal bounded bytes; never serialize this outside this module."""

    state: str
    stdout: bytes
    stderr: bytes
    truncated: bool


def validate_container_name(value: object) -> str:
    """Validate the only permitted Docker target identifier."""

    if not isinstance(value, str) or not _CONTAINER_RE.fullmatch(value):
        raise ValueError("container must be a Docker name or ID")
    return value


def validate_log_tail(value: object = DEFAULT_LOG_TAIL) -> int:
    """Validate the fixed diagnostic log tail range."""

    if type(value) is not int or not 1 <= value <= 200:
        raise ValueError("tail must be an integer between 1 and 200")
    return value


def local_docker_prefix(*, platform: Optional[str] = None) -> tuple[str, str, str]:
    """Return Docker's fixed local-daemon prefix; other platforms are unsupported."""

    selected = sys.platform if platform is None else platform
    if selected == "win32":
        endpoint = "npipe:////./pipe/docker_engine"
    elif selected == "linux":
        endpoint = "unix:///var/run/docker.sock"
    else:
        raise ValueError("local Docker diagnostics are unsupported on this platform")
    return ("docker", "--host", endpoint)


def safe_result(
    kind: str, state: str, *, container_id: Optional[str] = None, truncated: bool = False
) -> dict[str, object]:
    """Return the exact common safe diagnostic result shape."""

    if (
        type(kind) is not str
        or type(state) is not str
        or kind not in {"inspect", "logs"}
        or state not in _STATES
        or type(truncated) is not bool
    ):
        raise ValueError("invalid diagnostic result")
    if container_id is not None and (
        type(container_id) is not str or not re.fullmatch(r"[0-9a-f]{64}", container_id)
    ):
        raise ValueError("invalid container id")
    if state in {"timeout", "output-limit"}:
        truncated = True
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "state": state,
        "error_code": None if state == "ok" else "diagnostic_" + state.replace("-", "_"),
        "container_id": container_id,
        "truncated": truncated,
    }


def _child_environment(environment: Optional[Mapping[str, str]]) -> dict[str, str]:
    source = os.environ if environment is None else environment
    try:
        return {
            key: value
            for key, value in source.items()
            if isinstance(key, str)
            and isinstance(value, str)
            and not key.upper().startswith("DOCKER_")
        }
    except Exception:
        return {}


def _reap_owned_child(process: Any) -> bool:
    """Use one bounded terminate/kill escalation for the child we created."""

    def polled() -> Optional[bool]:
        try:
            return process.poll() is not None
        except Exception:
            return None

    if polled() is True:
        return True
    try:
        process.terminate()
    except Exception:
        pass
    else:
        try:
            process.wait(timeout=CLEANUP_SECONDS)
            if polled() is True:
                return True
        except Exception:
            pass
    try:
        process.kill()
    except Exception:
        return polled() is True
    try:
        process.wait(timeout=CLEANUP_SECONDS)
    except Exception:
        pass
    return polled() is True


def _capture_fixed_child(
    argv: tuple[str, ...],
    *,
    merged: bool,
    process_factory: ProcessFactory = subprocess.Popen,
    monotonic: Clock = time.monotonic,
    environment: Optional[Mapping[str, str]] = None,
) -> ChildCapture:
    """Capture one internally fixed child without exposing its argv publicly."""

    try:
        process = process_factory(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merged else subprocess.PIPE,
            shell=False,
            env=_child_environment(environment),
            bufsize=0,
        )
    except Exception:
        return ChildCapture("unavailable", b"", b"", False)

    lock = threading.Lock()
    stop = threading.Event()
    done = {"stdout": threading.Event(), "stderr": threading.Event()}
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    used = 0
    overflow = False
    read_error = False

    def read_pipe(name: str, pipe: Any) -> None:
        nonlocal used, overflow, read_error
        try:
            while not stop.is_set():
                chunk = pipe.read(8192)
                if not chunk:
                    return
                with lock:
                    if used + len(chunk) > MAX_CAPTURE_BYTES:
                        overflow = True
                        stop.set()
                        return
                    used += len(chunk)
                    buffers[name].extend(chunk)
        except Exception:
            read_error = True
            stop.set()
        finally:
            done[name].set()

    readers: list[threading.Thread] = []
    pipes: list[Any] = []
    try:
        stdout = process.stdout
        pipes.append(stdout)
        readers.append(threading.Thread(target=read_pipe, args=("stdout", stdout), daemon=True))
        if not merged:
            stderr = process.stderr
            pipes.append(stderr)
            readers.append(threading.Thread(target=read_pipe, args=("stderr", stderr), daemon=True))
        for reader in readers:
            reader.start()
    except Exception:
        stop.set()
        _reap_owned_child(process)
        for pipe in pipes:
            try:
                pipe.close()
            except Exception:
                pass
        for reader in readers:
            if reader.is_alive():
                reader.join(CLEANUP_SECONDS)
        return ChildCapture("unavailable", b"", b"", False)

    try:
        deadline = monotonic() + CHILD_DEADLINE_SECONDS
    except Exception:
        deadline = 0.0
        read_error = True
        stop.set()
    timed_out = False
    cleanup_ok = True
    try:
        while not stop.is_set():
            try:
                exited = process.poll() is not None
                expired = monotonic() >= deadline
            except Exception:
                read_error = True
                stop.set()
                break
            reader_names = ("stdout",) if merged else ("stdout", "stderr")
            if expired:
                timed_out = True
                stop.set()
                break
            if exited and all(done[name].is_set() for name in reader_names):
                break
            time.sleep(0.005)
    finally:
        try:
            needs_reap = process.poll() is None or stop.is_set()
        except Exception:
            needs_reap = True
        if needs_reap:
            cleanup_ok = _reap_owned_child(process)
        for pipe in pipes:
            try:
                pipe.close()
            except Exception:
                cleanup_ok = False
        for reader in readers:
            reader.join(CLEANUP_SECONDS)
            if reader.is_alive():
                cleanup_ok = False
    if not cleanup_ok:
        state = "unavailable"
    elif timed_out:
        state = "timeout"
    elif overflow:
        state = "output-limit"
    elif read_error or not cleanup_ok:
        state = "malformed"
    else:
        try:
            exit_code = process.poll()
        except Exception:
            exit_code = None
            state = "unavailable"
        else:
            state = "ok" if exit_code == 0 else "unavailable"
    if state != "ok":
        buffers["stdout"].clear()
        buffers["stderr"].clear()
        return ChildCapture(state, b"", b"", state in {"timeout", "output-limit"})
    return ChildCapture("ok", bytes(buffers["stdout"]), bytes(buffers["stderr"]), False)
