"""Private one-shot process supervisor used by Connect qualification.

It emits one small JSON envelope and never forwards fixture output.  Only this
short-lived process becomes a subreaper, so it never owns caller siblings.
"""
from __future__ import annotations

import ctypes
import errno
from dataclasses import dataclass
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
from typing import Any

_MAX_OUTPUT = 64 * 1024
_MAX_CHILDREN = 64
_PROC_CHILDREN_MAX = 64 * 1024


class ProcFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class Child:
    pid: int
    starttime: str
    pidfd: int


def _identity(pid: int) -> tuple[int, str] | None:
    try:
        waited, _ = os.waitpid(pid, os.WNOHANG)
        if waited == pid:
            return None
    except ChildProcessError:
        pass
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except OSError as exc:
        if exc.errno in {errno.ENOENT, errno.ESRCH}:
            return None
        raise ProcFailure("proc stat") from exc
    try:
        right = raw.rsplit(") ", 1)[1].split()
        if right[0] == "Z":
            return None
        return pid, right[19]  # starttime, field 22
    except IndexError as exc:
        raise ProcFailure("proc stat") from exc


def _children(pid: int) -> list[int]:
    try:
        with Path(f"/proc/{pid}/task/{pid}/children").open("rb") as handle:
            raw = handle.read(_PROC_CHILDREN_MAX + 1)
    except OSError as exc:
        if exc.errno in {errno.ENOENT, errno.ESRCH}:
            return []
        raise ProcFailure("proc children") from exc
    if len(raw) > _PROC_CHILDREN_MAX:
        raise ProcFailure("proc children")
    try:
        return [int(item) for item in raw.split()]
    except ValueError as exc:
        raise ProcFailure("proc children") from exc


class Children:
    def __init__(self, limit: int = _MAX_CHILDREN) -> None:
        if type(limit) is not int or not 1 <= limit <= _MAX_CHILDREN:
            raise RuntimeError("child limit")
        self.limit = limit
        self.known: dict[tuple[int, str], Child] = {}
        self.libc = ctypes.CDLL(None, use_errno=True)
        previous = ctypes.c_int()
        if self.libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0 or self.libc.prctl(36, 1, 0, 0, 0) != 0:
            raise RuntimeError("subreaper unavailable")
        self.previous = previous.value

    def close(self) -> None:
        for child in self.known.values():
            try:
                os.close(child.pidfd)
            except OSError:
                pass
        self.known.clear()
        self.libc.prctl(36, self.previous, 0, 0, 0)

    def _discover(self, pid: int) -> Child | None:
        identity = _identity(pid)
        if identity is None:
            return None
        existing = self.known.get(identity)
        if existing is not None:
            return existing
        try:
            descriptor = os.pidfd_open(pid, 0)
        except ProcessLookupError:
            return None
        except OSError as exc:
            raise ProcFailure("pidfd") from exc
        if _identity(pid) != identity:
            os.close(descriptor)
            return None
        child = Child(pid, identity[1], descriptor)
        self.known[identity] = child
        return child

    def scan(self) -> tuple[list[Child], bool]:
        direct = _children(os.getpid())
        # New children are first so a finite flood is handled in batches rather
        # than continually re-signalling the first TERM-ignoring processes.
        known_pids = {child.pid for child in self.known.values()}
        todo = [pid for pid in direct if pid not in known_pids] + [pid for pid in direct if pid in known_pids]
        discovered: list[Child] = []
        visited: set[int] = set()
        exceeded = False
        while todo:
            pid = todo.pop(0)
            if pid in visited:
                continue
            visited.add(pid)
            child = self._discover(pid)
            if child is None:
                continue
            discovered.append(child)
            if len(discovered) >= self.limit:
                exceeded = bool(todo)
                break
            descendants = _children(pid)
            todo = [item for item in descendants if item not in known_pids] + todo + [item for item in descendants if item in known_pids]
        active: list[Child] = []
        for key, child in list(self.known.items()):
            if _identity(child.pid) == (child.pid, child.starttime):
                active.append(child)
            else:
                try:
                    os.close(child.pidfd)
                except OSError:
                    pass
                del self.known[key]
        return active, exceeded

    @staticmethod
    def _signal(children: list[Child], sig: int) -> None:
        for child in children:
            try:
                # A pidfd is tied to the discovered process instance; unlike a
                # numeric PID it cannot signal a later reuse.
                signal.pidfd_send_signal(child.pidfd, sig)
            except ProcessLookupError:
                pass
            except OSError as exc:
                raise ProcFailure("pidfd signal") from exc

    def wait_empty(self, timeout: float) -> tuple[bool, bool]:
        deadline = time.monotonic() + timeout
        limited = False
        while time.monotonic() < deadline:
            active, exceeded = self.scan()
            limited |= exceeded
            if not active and not exceeded:
                return True, limited
            time.sleep(0.025)
        active, exceeded = self.scan()
        return not active and not exceeded, limited | exceeded

    def cleanup(self, timeout: float) -> tuple[bool, bool, bool]:
        """Drain finite trees in batches; return empty, used-kill, over-limit."""
        deadline = time.monotonic() + timeout
        limited = False
        escalated = False
        killed = False
        while time.monotonic() < deadline:
            active, exceeded = self.scan()
            limited |= exceeded
            if not active and not exceeded:
                return True, escalated, limited
            self._signal(active, signal.SIGKILL if killed else signal.SIGTERM)
            if not killed and time.monotonic() + 0.25 >= deadline:
                killed = True
                escalated = True
            time.sleep(0.025)
        # A final bounded kill-and-rescan makes post-SIGKILL reaping explicit.
        for _ in range(8):
            active, exceeded = self.scan()
            limited |= exceeded
            if not active and not exceeded:
                return True, escalated, limited
            self._signal(active, signal.SIGKILL)
            escalated = True
            time.sleep(0.025)
        active, exceeded = self.scan()
        return not active and not exceeded, escalated, limited | exceeded


_FIXTURE_MARKER = __import__("re").compile(r"(?:browser_(?:edge|runtime)_fixture_test\.go|browser_edge\.spec\.mjs):[1-9][0-9]{0,4}")
_FAILURE_STAGES = frozenset({"build", "fixture-startup", "browser-launch-cert", "browser-connection", "browser-dns", "browser-navigation", "browser-assertion", "report-parsing", "timeout", "interrupted", "supervisor"})
_CLOSURE_MS_ANNOTATION = "closure_ms"
_STREAM_CLOSURE_TESTS = frozenset({
    "container-gated browser streams close on human disable",
    "container-gated browser streams close on logout",
    "container-gated CLI streams close on human disable",
    "container-gated CLI streams close on browser logout",
})
_CLOSURE_MS = __import__("re").compile(r"(?:0|[1-9][0-9]{0,3})")


def _report_value(output: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(output.decode("utf-8", "strict"))
        return value if isinstance(value, dict) else None
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _fixture_marker(value: Any) -> str | None:
    # Playwright keeps assertion locations as structured fields, not always in
    # its stack string. Retain only an allowlisted public basename and line.
    pending = [value]
    visited = 0
    location_marker = None
    while pending and visited < 4096:
        item = pending.pop()
        visited += 1
        if isinstance(item, dict):
            description = item.get("description")
            if item.get("type") == "diagnostic-location" and isinstance(description, str) and _FIXTURE_MARKER.fullmatch(description):
                return description
            location = item.get("location")
            if "message" in item and isinstance(location, dict):
                filename, line = location.get("file"), location.get("line")
                if isinstance(filename, str) and type(line) is int:
                    marker = filename.replace("\\", "/").rsplit("/", 1)[-1] + ":" + str(line)
                    if location_marker is None and _FIXTURE_MARKER.fullmatch(marker):
                        location_marker = marker
            pending.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            pending.extend(reversed(item))
    if location_marker is not None:
        return location_marker
    for text in _report_strings(value):
        match = _FIXTURE_MARKER.search(text)
        if match:
            return match.group(0)
    return None


def _report_strings(value: Any, limit: int = 64) -> list[str]:
    # These strings are used only for in-memory classification and are never
    # returned, logged, or written to artifacts.
    found: list[str] = []
    pending = [value]
    while pending and len(found) < limit:
        item = pending.pop()
        if isinstance(item, str):
            found.append(item[:1024])
        elif isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return found


def _matching_spec(value: dict[str, Any], expected_name: str) -> dict[str, Any] | None:
    specs: list[dict[str, Any]] = []
    pending: list[Any] = [value.get("suites", [])]
    while pending:
        item = pending.pop()
        if isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            if "tests" in item and "title" in item:
                specs.append(item)
            pending.extend(item.get("suites", []))
            pending.extend(item.get("specs", []))
    matched = [spec for spec in specs if spec.get("title") == expected_name]
    return matched[0] if len(matched) == 1 else None


def _failure_stage(output: bytes, expected_name: str) -> str:
    value = _report_value(output)
    if value is None:
        return "report-parsing"
    marker = _fixture_marker(value)
    if marker is not None and not marker.startswith("browser_edge.spec.mjs:"):
        return "fixture-startup"
    text = "\n".join(_report_strings(value)).lower()
    if "err_name_not_resolved" in text:
        return "browser-dns"
    if any(code in text for code in ("err_connection_refused", "err_connection_closed", "err_connection_timed_out")):
        return "browser-connection"
    if "browser-navigation-failed" in text:
        return "browser-navigation"
    if "fixture build" in text or "go test -c" in text:
        return "build"
    if any(token in text for token in ("fixture did not become ready", "fixture exited", "reverse listener", "tunnel child", "wstunnel")):
        return "fixture-startup"
    if any(token in text for token in ("chromium.launch", "browsertype.launch", "verifyuntrustedcertificate", "unknown fixture certificate", "err_cert", "certificate")):
        return "browser-launch-cert"
    return "browser-assertion" if marker is not None or _matching_spec(value, expected_name) is not None else "report-parsing"


def _skipped_report(output: bytes, expected_name: str) -> bool:
    value = _report_value(output)
    if value is None or value.get("errors") not in ([], None):
        return False
    matched = _matching_spec(value, expected_name)
    if matched is None or matched.get("ok") is not True:
        return False
    tests = matched.get("tests")
    return isinstance(tests, list) and len(tests) == 1 and tests[0].get("status") == "skipped"


def _closure_ms(value: dict[str, Any], expected_name: str) -> int | None:
    """Return one allowlisted closure observation from its exact passed test."""
    matched = _matching_spec(value, expected_name)
    if matched is None:
        raise ValueError
    tests = matched.get("tests")
    if not isinstance(tests, list) or len(tests) != 1 or not isinstance(tests[0], dict):
        raise ValueError
    annotations = tests[0].get("annotations", [])
    if not isinstance(annotations, list):
        raise ValueError
    closure_annotations = [
        annotation for annotation in annotations
        if isinstance(annotation, dict) and annotation.get("type") == _CLOSURE_MS_ANNOTATION
    ]
    requires_measurement = expected_name in _STREAM_CLOSURE_TESTS
    if not requires_measurement:
        if closure_annotations:
            raise ValueError
        return None
    if len(closure_annotations) != 1:
        raise ValueError
    annotation = closure_annotations[0]
    description = annotation.get("description")
    if type(annotation.get("type")) is not str or type(description) is not str or _CLOSURE_MS.fullmatch(description) is None:
        raise ValueError
    measurement = int(description)
    if measurement > 1000:
        raise ValueError
    return measurement


def _valid_report(output: bytes, expected_name: str) -> bool:
    value = _report_value(output)
    if value is None or value.get("errors") not in ([], None):
        return False
    matched = _matching_spec(value, expected_name)
    if matched is None or matched.get("ok") is not True:
        return False
    tests = matched.get("tests")
    if not (isinstance(tests, list) and len(tests) == 1 and tests[0].get("status") == "expected" and isinstance(tests[0].get("results"), list) and bool(tests[0]["results"]) and all(isinstance(result, dict) and result.get("status") == "passed" for result in tests[0]["results"])):
        return False
    try:
        _closure_ms(value, expected_name)
    except ValueError:
        return False
    return True


def _finish(status: str, *, escalated: bool = False, failure_stage: str | None = None, fixture_marker: str | None = None, closure_ms: int | None = None) -> int:
    if failure_stage is not None and failure_stage not in _FAILURE_STAGES:
        failure_stage = "supervisor"
    if fixture_marker is not None and _FIXTURE_MARKER.fullmatch(fixture_marker) is None:
        fixture_marker = None
    if closure_ms is not None and (type(closure_ms) is not int or not 0 <= closure_ms <= 1000):
        status, failure_stage, closure_ms = "runner-failed", "supervisor", None
    if status != "passed":
        closure_ms = None
    sys.stdout.write(json.dumps({"status": status, "escalated": escalated, "failure_stage": failure_stage, "fixture_marker": fixture_marker, "closure_ms": closure_ms}, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


def run(argv: list[str], timeout: float, expected_name: str, *, batch_size: int = _MAX_CHILDREN) -> int:
    if not argv or timeout <= 0 or timeout > 900:
        return _finish("runner-failed")
    children = Children(batch_size)
    interrupted = False
    too_much = False

    def stop(_signal: int, _frame: Any) -> None:
        nonlocal interrupted
        interrupted = True

    old_term = signal.signal(signal.SIGTERM, stop)
    old_int = signal.signal(signal.SIGINT, stop)
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    status = "runner-failed"
    escalated = False
    closure_ms: int | None = None
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
        selector = selectors.DefaultSelector()
        assert process.stdout is not None and process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        output = bytearray()
        stderr_bytes = 0
        deadline = time.monotonic() + timeout
        while selector.get_map() and not interrupted and time.monotonic() < deadline:
            for key, _ in selector.select(min(0.05, max(0.0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stderr":
                    # Node/runtime diagnostics can include synthetic fixture
                    # credentials. Drain them only to enforce a bound.
                    stderr_bytes += len(chunk)
                    if stderr_bytes > _MAX_OUTPUT:
                        too_much = True
                        interrupted = True
                        break
                    continue
                if len(output) + len(chunk) > _MAX_OUTPUT:
                    too_much = True
                    interrupted = True
                    break
                output.extend(chunk)
        if interrupted:
            status = "runner-failed" if too_much else "runner-interrupted"
        elif time.monotonic() >= deadline:
            status = "runner-timeout"
        else:
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
            status = "passed" if process.poll() is not None and process.returncode == 0 else "runner-failed"
        fixture_marker = _fixture_marker(_report_value(bytes(output)) or {})
        failure_stage: str | None = None
        if status == "runner-timeout":
            failure_stage = "timeout"
        elif status == "runner-interrupted":
            failure_stage = "interrupted"
        elif status == "runner-failed":
            failure_stage = _failure_stage(bytes(output), expected_name)
        if status == "passed":
            empty, limited = children.wait_empty(0.5)
            cleanup_escalated = False
            # A clean test leader is not sufficient: a detached descendant
            # that closed its inherited pipes still owns a fixture listener.
            # Drain it before returning the failed result.
            if not empty or limited:
                _drained, cleanup_escalated, _cleanup_limited = children.cleanup(2)
                status = "runner-failed"
                failure_stage = "supervisor"
        else:
            empty, cleanup_escalated, limited = children.cleanup(2)
        if not empty or limited:
            status = "runner-failed"
            failure_stage = "supervisor"
        escalated |= cleanup_escalated
        if status == "passed" and _skipped_report(bytes(output), expected_name):
            status = "skipped"
        elif status == "passed" and not _valid_report(bytes(output), expected_name):
            status = "runner-failed"
            failure_stage = _failure_stage(bytes(output), expected_name)
        elif status == "passed":
            closure_ms = _closure_ms(_report_value(bytes(output)) or {}, expected_name)
        return _finish(status, escalated=escalated, failure_stage=failure_stage, fixture_marker=fixture_marker, closure_ms=closure_ms)
    except Exception:
        try:
            empty, cleanup_escalated, _limited = children.cleanup(2)
            escalated |= cleanup_escalated or not empty
        except Exception:
            escalated = True
        return _finish("runner-failed", escalated=True, failure_stage="supervisor")
    finally:
        if selector is not None:
            selector.close()
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
        children.close()


def main() -> int:
    if len(sys.argv) < 6 or sys.argv[1] != "--timeout" or sys.argv[3] != "--expected-name" or sys.argv[5] != "--":
        return _finish("runner-failed")
    try:
        timeout = float(sys.argv[2])
    except ValueError:
        return _finish("runner-failed")
    return run(sys.argv[6:], timeout, sys.argv[4])


if __name__ == "__main__":
    raise SystemExit(main())
