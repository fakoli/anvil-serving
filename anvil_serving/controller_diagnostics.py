"""Bounded local controller diagnostics and their fixed managed CLI.

The module exposes only metadata-safe inspection and projected audit logs for
one explicitly named local controller container.  It deliberately has no
generic command runner, raw log mode, or remote daemon selection.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import ipaddress
import json
import math
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from .operator_output import CommandResult, OperatorError, TransportError, UsageError


SCHEMA_VERSION = "controller-diagnostics/v1"
MAX_CAPTURE_BYTES = 256 * 1024
CHILD_DEADLINE_SECONDS = 10.0
CLEANUP_SECONDS = 1.0
DEFAULT_LOG_TAIL = 100
_CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_STATES = frozenset(("ok", "unsupported", "unavailable", "timeout", "output-limit", "malformed"))
_CAPTURE_STATES = frozenset(("ok", "unavailable", "timeout", "output-limit", "malformed"))
_INSPECT_KEYS = frozenset(
    (
        "container_id",
        "running",
        "exit_code",
        "health",
        "compose_service",
        "configured_bindings",
        "observed_bindings",
    )
)
_BINDING_KEYS = frozenset(("HostIp", "HostPort"))
_PORT_KEY_RE = re.compile(r"([0-9]+)/tcp")
_MAX_BINDINGS = 64
_MAX_AUDIT_LINE_BYTES = 16 * 1024
_MAX_AUDIT_KEYS = 32
_MAX_EVENTS = 200
_MAX_COUNTER = 8 * 1024 * 1024
_KNOWN_OPERATIONS = frozenset(("health", "healthz", "tools/list", "tools/call", "mcp"))
_KNOWN_EVENTS = frozenset(("operation_interrupted_recovered", "audit_file_write_failed"))
_KNOWN_ERROR_CODES = frozenset(
    (
        "authentication_error",
        "authorization_scope_denied",
        "origin_not_allowed",
        "header_mismatch",
        "unknown_tool",
        "request_timeout",
        "payload_too_large",
        "internal_error",
    )
)
_EVENT_KEYS = frozenset(("operation", "event", "error_code", "status", "elapsed_ms"))
_PUBLIC_COMMON_KEYS = frozenset(
    ("schema_version", "kind", "state", "error_code", "container_id", "truncated")
)
_PUBLIC_INSPECT_KEYS = _PUBLIC_COMMON_KEYS | frozenset(
    ("running", "exit_code", "health", "configured_bindings", "observed_bindings")
)
_PUBLIC_LOGS_KEYS = _PUBLIC_COMMON_KEYS | frozenset(
    (
        "events",
        "line_count",
        "returned_events",
        "rejected_lines",
        "unknown_fields",
        "unknown_codes",
        "counters_saturated",
    )
)
_BIND_CLASSES = frozenset(("loopback", "wildcard", "private", "public", "unknown"))
_INSPECT_TEMPLATE = (
    '{"container_id":{{json .Id}},'
    '"running":{{json .State.Running}},'
    '"exit_code":{{json .State.ExitCode}},'
    '"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}"none"{{end}},'
    '"compose_service":{{json (index .Config.Labels "com.docker.compose.service")}},'
    '"configured_bindings":{{json .HostConfig.PortBindings}},'
    '"observed_bindings":{{json .NetworkSettings.Ports}}}'
)

_PRIVATE_V4 = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
)
_UNKNOWN_V4 = tuple(
    ipaddress.ip_network(value)
    for value in (
        "0.0.0.0/8",
        "169.254.0.0/16",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/3",
    )
)
_PRIVATE_V6 = ipaddress.ip_network("fc00::/7")
_PUBLIC_V6 = ipaddress.ip_network("2000::/3")
_DOCUMENTATION_V6 = ipaddress.ip_network("2001:db8::/32")

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


def _public_failure() -> ValueError:
    return ValueError("invalid diagnostic public result")


def _public_int(value: object, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise _public_failure()
    return value


def _public_port(value: object) -> int:
    port = _public_int(value, 65535)
    if port < 1:
        raise _public_failure()
    return port


def _validate_public_common(value: object, expected_kind: str) -> dict[str, object]:
    if type(value) is not dict or type(expected_kind) is not str or expected_kind not in {"inspect", "logs"}:
        raise _public_failure()
    if any(type(key) is not str for key in value):
        raise _public_failure()
    schema_version = value.get("schema_version")
    if type(schema_version) is not str or schema_version != SCHEMA_VERSION:
        raise _public_failure()
    kind = value.get("kind")
    if type(kind) is not str or kind != expected_kind:
        raise _public_failure()
    state = value.get("state")
    if type(state) is not str or state not in _STATES:
        raise _public_failure()
    error_code = value.get("error_code")
    expected_error = None if state == "ok" else "diagnostic_" + state.replace("-", "_")
    if error_code is not None and type(error_code) is not str:
        raise _public_failure()
    if error_code != expected_error:
        raise _public_failure()
    container_id = value.get("container_id")
    if container_id is not None and (
        type(container_id) is not str or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
    ):
        raise _public_failure()
    if state == "ok" and container_id is None:
        raise _public_failure()
    truncated = value.get("truncated")
    if type(truncated) is not bool or (state in {"timeout", "output-limit"} and not truncated):
        raise _public_failure()
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": expected_kind,
        "state": state,
        "error_code": error_code,
        "container_id": container_id,
        "truncated": truncated,
    }


def _validate_public_binding(value: object) -> dict[str, object]:
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or set(value) != {"container_port", "host_port", "bind_class"}
    ):
        raise _public_failure()
    bind_class = value["bind_class"]
    if type(bind_class) is not str or bind_class not in _BIND_CLASSES:
        raise _public_failure()
    return {
        "container_port": _public_port(value["container_port"]),
        "host_port": _public_port(value["host_port"]),
        "bind_class": bind_class,
    }


def _validate_public_event(value: object) -> dict[str, object]:
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or not set(value).issubset(_EVENT_KEYS)
    ):
        raise _public_failure()
    result: dict[str, object] = {}
    identity = False
    for key, allowed in (("operation", _KNOWN_OPERATIONS), ("event", _KNOWN_EVENTS), ("error_code", _KNOWN_ERROR_CODES)):
        if key in value:
            item = value[key]
            if type(item) is not str or item not in allowed:
                raise _public_failure()
            result[key] = item
            identity = identity or key in {"operation", "event"}
    if not identity:
        raise _public_failure()
    if "status" in value:
        result["status"] = _public_int(value["status"], 599)
        if result["status"] < 100:
            raise _public_failure()
    if "elapsed_ms" in value:
        elapsed = value["elapsed_ms"]
        if type(elapsed) not in {int, float} or (type(elapsed) is float and not math.isfinite(elapsed)):
            raise _public_failure()
        if not 0 <= elapsed <= 3_600_000:
            raise _public_failure()
        result["elapsed_ms"] = elapsed
    return result


def validate_public_result(value: object, *, expected_kind: str) -> dict[str, object]:
    """Return a fresh exact v1 public projection or raise a fixed error.

    This is the boundary for local command wrappers and controller transport
    payloads.  It accepts only built-in containers and scalars so an object
    supplied by a remote transport cannot run custom mapping/list behavior
    while being copied into an operator-facing envelope.
    """

    common = _validate_public_common(value, expected_kind)
    assert type(value) is dict
    state = common["state"]
    if expected_kind == "inspect":
        if set(value) != _PUBLIC_INSPECT_KEYS:
            raise _public_failure()
        if state != "ok":
            if any(value[key] is not None for key in ("running", "exit_code", "health")):
                raise _public_failure()
            configured = value["configured_bindings"]
            observed = value["observed_bindings"]
            if type(configured) is not list or configured or type(observed) is not list or observed:
                raise _public_failure()
            return {
                **common,
                "running": None,
                "exit_code": None,
                "health": None,
                "configured_bindings": [],
                "observed_bindings": [],
            }
        if type(value["running"]) is not bool:
            raise _public_failure()
        exit_code = _public_int(value["exit_code"], 255)
        health = value["health"]
        if type(health) is not str or health not in {"healthy", "unhealthy", "starting", "none"}:
            raise _public_failure()
        bindings: dict[str, list[dict[str, object]]] = {}
        for key in ("configured_bindings", "observed_bindings"):
            raw = value[key]
            if type(raw) is not list or len(raw) > _MAX_BINDINGS:
                raise _public_failure()
            bindings[key] = [_validate_public_binding(item) for item in raw]
        return {**common, "running": value["running"], "exit_code": exit_code, "health": health, **bindings}

    if set(value) != _PUBLIC_LOGS_KEYS:
        raise _public_failure()
    if state != "ok":
        raw_events = value["events"]
        if type(raw_events) is not list or raw_events:
            raise _public_failure()
        counters = {
            key: _public_int(value[key], _MAX_COUNTER)
            for key in (
                "line_count",
                "returned_events",
                "rejected_lines",
                "unknown_fields",
                "unknown_codes",
            )
        }
        saturated = value["counters_saturated"]
        if (
            any(counter != 0 for counter in counters.values())
            or type(saturated) is not bool
            or saturated
        ):
            raise _public_failure()
        return {
            **common,
            "events": [],
            "line_count": 0,
            "returned_events": 0,
            "rejected_lines": 0,
            "unknown_fields": 0,
            "unknown_codes": 0,
            "counters_saturated": False,
        }
    raw_events = value["events"]
    if type(raw_events) is not list or len(raw_events) > _MAX_EVENTS:
        raise _public_failure()
    events = [_validate_public_event(item) for item in raw_events]
    counters = {key: _public_int(value[key], _MAX_COUNTER) for key in ("line_count", "returned_events", "rejected_lines", "unknown_fields", "unknown_codes")}
    saturated = value["counters_saturated"]
    if (
        counters["returned_events"] != len(events)
        or counters["line_count"] < counters["returned_events"] + counters["rejected_lines"]
        or type(saturated) is not bool
        or (saturated and _MAX_COUNTER not in counters.values())
    ):
        raise _public_failure()
    return {**common, "events": events, **counters, "counters_saturated": saturated}


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
    retain_stdout_on_error: bool = False,
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
    retain_stdout = (
        retain_stdout_on_error
        and state == "unavailable"
        and not timed_out
        and not overflow
        and not read_error
        and cleanup_ok
        and type(exit_code) is int
        and exit_code != 0
    )
    if state != "ok":
        retained_stdout = bytes(buffers["stdout"]) if retain_stdout else b""
        buffers["stdout"].clear()
        buffers["stderr"].clear()
        return ChildCapture(
            state,
            retained_stdout,
            b"",
            state in {"timeout", "output-limit"},
        )
    return ChildCapture("ok", bytes(buffers["stdout"]), bytes(buffers["stderr"]), False)


def _inspect_result(
    state: str,
    *,
    container_id: Optional[str] = None,
    truncated: bool = False,
    running: Optional[bool] = None,
    exit_code: Optional[int] = None,
    health: Optional[str] = None,
    configured_bindings: Optional[list[dict[str, object]]] = None,
    observed_bindings: Optional[list[dict[str, object]]] = None,
) -> dict[str, object]:
    result = safe_result("inspect", state, container_id=container_id, truncated=truncated)
    result.update(
        {
            "running": running if state == "ok" else None,
            "exit_code": exit_code if state == "ok" else None,
            "health": health if state == "ok" else None,
            "configured_bindings": configured_bindings if state == "ok" else [],
            "observed_bindings": observed_bindings if state == "ok" else [],
        }
    )
    return result


def _logs_result(
    state: str,
    *,
    container_id: Optional[str] = None,
    truncated: bool = False,
    events: Optional[list[dict[str, object]]] = None,
    line_count: int = 0,
    rejected_lines: int = 0,
    unknown_fields: int = 0,
    unknown_codes: int = 0,
    counters_saturated: bool = False,
) -> dict[str, object]:
    result = safe_result("logs", state, container_id=container_id, truncated=truncated)
    projected = events if state == "ok" and events is not None else []
    result.update(
        {
            "events": projected,
            "line_count": line_count if state == "ok" else 0,
            "returned_events": len(projected),
            "rejected_lines": rejected_lines if state == "ok" else 0,
            "unknown_fields": unknown_fields if state == "ok" else 0,
            "unknown_codes": unknown_codes if state == "ok" else 0,
            "counters_saturated": counters_saturated if state == "ok" else False,
        }
    )
    return result


def _invoke_capture(
    argv: tuple[str, ...],
    *,
    merged: bool,
    capture: Callable[..., ChildCapture],
    process_factory: ProcessFactory,
    monotonic: Clock,
    environment: Optional[Mapping[str, str]],
) -> ChildCapture:
    try:
        result = capture(
            argv,
            merged=merged,
            process_factory=process_factory,
            monotonic=monotonic,
            environment=environment,
        )
    except Exception:
        return ChildCapture("unavailable", b"", b"", False)
    if (
        type(result) is not ChildCapture
        or type(result.state) is not str
        or result.state not in _CAPTURE_STATES
        or type(result.stdout) is not bytes
        or type(result.stderr) is not bytes
        or type(result.truncated) is not bool
    ):
        return ChildCapture("unavailable", b"", b"", False)
    if len(result.stdout) + len(result.stderr) > MAX_CAPTURE_BYTES:
        return ChildCapture("output-limit", b"", b"", True)
    if result.state != "ok":
        return ChildCapture(
            result.state,
            b"",
            b"",
            result.state in {"timeout", "output-limit"},
        )
    if result.truncated or (merged and result.stderr):
        return ChildCapture("malformed", b"", b"", False)
    return result


def _strict_object(raw: bytes) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[object, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if type(key) is not str or key in result:
                raise ValueError
            result[key] = value
        return result

    def reject_constant(_value: str) -> object:
        raise ValueError

    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=reject_duplicates,
        parse_constant=reject_constant,
    )
    if type(value) is not dict:
        raise ValueError
    return value


def _strict_port(value: object) -> int:
    if type(value) is not str or re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError
    return port


def _bind_class(value: object) -> str:
    if type(value) is not str:
        raise ValueError
    if value == "":
        return "wildcard"
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return "unknown"
    if address.is_unspecified:
        return "wildcard"
    if isinstance(address, ipaddress.IPv4Address):
        if address in ipaddress.ip_network("127.0.0.0/8"):
            return "loopback"
        if any(address in network for network in _PRIVATE_V4):
            return "private"
        if any(address in network for network in _UNKNOWN_V4):
            return "unknown"
        return "public"
    if address == ipaddress.ip_address("::1"):
        return "loopback"
    if address in _PRIVATE_V6:
        return "private"
    if address in _DOCUMENTATION_V6 or address not in _PUBLIC_V6:
        return "unknown"
    return "public"


def _project_bindings(value: object) -> list[dict[str, object]]:
    if type(value) is not dict:
        raise ValueError
    result: list[dict[str, object]] = []
    for raw_container_port, bindings in value.items():
        if type(raw_container_port) is not str:
            raise ValueError
        match = _PORT_KEY_RE.fullmatch(raw_container_port)
        if match is None:
            raise ValueError
        container_port = _strict_port(match.group(1))
        if bindings is None:
            continue
        if type(bindings) is not list:
            raise ValueError
        for binding in bindings:
            if len(result) >= _MAX_BINDINGS:
                raise OverflowError
            if type(binding) is not dict or set(binding) != _BINDING_KEYS:
                raise ValueError
            result.append(
                {
                    "container_port": container_port,
                    "host_port": _strict_port(binding["HostPort"]),
                    "bind_class": _bind_class(binding["HostIp"]),
                }
            )
    result.sort(key=lambda row: (row["container_port"], row["host_port"], row["bind_class"]))
    return result


def inspect_controller(
    container: object,
    *,
    platform: Optional[str] = None,
    process_factory: ProcessFactory = subprocess.Popen,
    monotonic: Clock = time.monotonic,
    environment: Optional[Mapping[str, str]] = None,
    _capture: Callable[..., ChildCapture] = _capture_fixed_child,
) -> dict[str, object]:
    """Inspect one local Docker controller through a fixed metadata-only template."""

    try:
        name = validate_container_name(container)
    except Exception:
        return _inspect_result("malformed")
    try:
        prefix = local_docker_prefix(platform=platform)
    except Exception:
        return _inspect_result("unsupported")
    capture = _invoke_capture(
        prefix + ("inspect", "--format", _INSPECT_TEMPLATE, name),
        merged=False,
        capture=_capture,
        process_factory=process_factory,
        monotonic=monotonic,
        environment=environment,
    )
    if capture.state != "ok":
        return _inspect_result(capture.state, truncated=capture.truncated)
    container_id: Optional[str] = None
    try:
        document = _strict_object(capture.stdout)
        if set(document) != _INSPECT_KEYS:
            raise ValueError
        candidate_id = document["container_id"]
        if type(candidate_id) is not str or re.fullmatch(r"[0-9a-f]{64}", candidate_id) is None:
            raise ValueError
        container_id = candidate_id
        service = document["compose_service"]
        if service is None or (type(service) is str and service != "controller"):
            return _inspect_result("unsupported", container_id=container_id)
        if type(service) is not str:
            raise ValueError
        running = document["running"]
        exit_code = document["exit_code"]
        health = document["health"]
        if type(running) is not bool or type(exit_code) is not int or not 0 <= exit_code <= 255:
            raise ValueError
        if type(health) is not str or health not in {"healthy", "unhealthy", "starting", "none"}:
            raise ValueError
        configured = _project_bindings(document["configured_bindings"])
        observed = _project_bindings(document["observed_bindings"])
    except OverflowError:
        return _inspect_result("output-limit", container_id=container_id, truncated=True)
    except Exception:
        return _inspect_result("malformed", container_id=container_id)
    return _inspect_result(
        "ok",
        container_id=container_id,
        running=running,
        exit_code=exit_code,
        health=health,
        configured_bindings=configured,
        observed_bindings=observed,
    )


def _audit_lines(raw: bytes) -> list[bytes]:
    if not raw:
        return []
    lines = raw.split(b"\n")
    if raw.endswith(b"\n"):
        lines.pop()
    return lines


def _increment(counters: dict[str, int], name: str, amount: int = 1) -> bool:
    current = counters[name]
    updated = min(_MAX_COUNTER, current + amount)
    counters[name] = updated
    return updated < current + amount


def _project_audit_line(raw: bytes) -> tuple[Optional[dict[str, object]], int, int]:
    if len(raw) > _MAX_AUDIT_LINE_BYTES:
        raise ValueError
    if raw.endswith(b"\r"):
        raw = raw[:-1]
    document = _strict_object(raw)
    if len(document) > _MAX_AUDIT_KEYS:
        raise ValueError
    unknown_fields = len(set(document) - _EVENT_KEYS)
    unknown_codes = 0
    projected: dict[str, object] = {}
    known_identity = False
    for key, allowed in (
        ("operation", _KNOWN_OPERATIONS),
        ("event", _KNOWN_EVENTS),
        ("error_code", _KNOWN_ERROR_CODES),
    ):
        if key not in document:
            continue
        value = document[key]
        if type(value) is not str or value not in allowed:
            unknown_codes += 1
        else:
            projected[key] = value
            if key in {"operation", "event"}:
                known_identity = True
    if unknown_codes or not known_identity:
        return None, unknown_fields, unknown_codes
    if "status" in document:
        status = document["status"]
        if type(status) is not int or not 100 <= status <= 599:
            return None, unknown_fields, unknown_codes
        projected["status"] = status
    if "elapsed_ms" in document:
        elapsed = document["elapsed_ms"]
        if type(elapsed) not in {int, float}:
            return None, unknown_fields, unknown_codes
        if type(elapsed) is float and not math.isfinite(elapsed):
            return None, unknown_fields, unknown_codes
        if not 0 <= elapsed <= 3_600_000:
            return None, unknown_fields, unknown_codes
        projected["elapsed_ms"] = elapsed
    return projected, unknown_fields, 0


def controller_logs(
    container: object,
    tail: object = DEFAULT_LOG_TAIL,
    *,
    platform: Optional[str] = None,
    process_factory: ProcessFactory = subprocess.Popen,
    monotonic: Clock = time.monotonic,
    environment: Optional[Mapping[str, str]] = None,
    _capture: Callable[..., ChildCapture] = _capture_fixed_child,
) -> dict[str, object]:
    """Return bounded allowlisted audit events for one validated controller."""

    try:
        name = validate_container_name(container)
        bounded_tail = validate_log_tail(tail)
    except Exception:
        return _logs_result("malformed")
    inspected = inspect_controller(
        name,
        platform=platform,
        process_factory=process_factory,
        monotonic=monotonic,
        environment=environment,
        _capture=_capture,
    )
    if inspected["state"] != "ok":
        return _logs_result(
            inspected["state"],
            container_id=inspected["container_id"],
            truncated=inspected["truncated"],
        )
    container_id = inspected["container_id"]
    try:
        prefix = local_docker_prefix(platform=platform)
    except Exception:
        return _logs_result("unsupported", container_id=container_id)
    capture = _invoke_capture(
        prefix + ("logs", "--tail", str(bounded_tail), container_id),
        merged=True,
        capture=_capture,
        process_factory=process_factory,
        monotonic=monotonic,
        environment=environment,
    )
    if capture.state != "ok":
        return _logs_result(
            capture.state,
            container_id=container_id,
            truncated=capture.truncated,
        )

    counters = {
        "line_count": 0,
        "rejected_lines": 0,
        "unknown_fields": 0,
        "unknown_codes": 0,
    }
    saturated = False
    events: list[dict[str, object]] = []
    truncated = False
    for raw_line in _audit_lines(capture.stdout):
        saturated = _increment(counters, "line_count") or saturated
        try:
            event, unknown_fields, unknown_codes = _project_audit_line(raw_line)
        except Exception:
            saturated = _increment(counters, "rejected_lines") or saturated
            continue
        saturated = _increment(counters, "unknown_fields", unknown_fields) or saturated
        saturated = _increment(counters, "unknown_codes", unknown_codes) or saturated
        if event is None:
            saturated = _increment(counters, "rejected_lines") or saturated
            continue
        if len(events) < _MAX_EVENTS:
            events.append(event)
        else:
            truncated = True
    return _logs_result(
        "ok",
        container_id=container_id,
        truncated=truncated,
        events=events,
        line_count=counters["line_count"],
        rejected_lines=counters["rejected_lines"],
        unknown_fields=counters["unknown_fields"],
        unknown_codes=counters["unknown_codes"],
        counters_saturated=saturated,
    )


def _container_argument(value: str) -> str:
    try:
        return validate_container_name(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a Docker name or ID") from exc


def _tail_argument(value: str) -> int:
    if type(value) is not str or re.fullmatch(r"[0-9]+", value) is None:
        raise argparse.ArgumentTypeError("must be an integer between 1 and 200")
    try:
        return validate_log_tail(int(value, 10))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be an integer between 1 and 200") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anvil-serving controller",
        description="Read-only, metadata-only local controller diagnostics.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser(
        "inspect",
        help="Read-only metadata inspection for one controller container.",
        allow_abbrev=False,
    )
    inspect.add_argument(
        "--container", required=True, type=_container_argument, metavar="NAME",
        help="required local controller Docker name or immutable ID",
    )
    logs = commands.add_parser(
        "logs",
        help="Read bounded metadata-only controller audit events.",
        allow_abbrev=False,
    )
    logs.add_argument(
        "--container", required=True, type=_container_argument, metavar="NAME",
        help="required local controller Docker name or immutable ID",
    )
    logs.add_argument(
        "--tail", type=_tail_argument, default=DEFAULT_LOG_TAIL, metavar="N",
        help="metadata audit event tail (1..200; default: 100)",
    )
    return parser


def run(argv: Optional[list[str]] = None) -> dict[str, object]:
    """Execute one exact managed diagnostic without printing side effects."""
    args = _parser().parse_args(argv)
    if args.command == "inspect":
        return inspect_controller(args.container)
    return controller_logs(args.container, args.tail)


def command(argv: Optional[list[str]] = None) -> CommandResult | int:
    """Return one validated diagnostic result for the root command dispatcher.

    The focused ``main`` keeps its historical argparse behavior.  This adapter
    is deliberately narrower: invalid parser output is discarded before it can
    become a root CLI envelope. Root dispatch is the sole owner of help handling.
    """

    tokens = [] if argv is None else list(argv)
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            result = run(tokens)
    except SystemExit:
        return CommandResult(
            error=UsageError(
                "invalid diagnostic arguments", code="invalid_diagnostic_arguments"
            )
        )
    except Exception:
        return CommandResult(
            error=TransportError(
                "controller diagnostic transport failed",
                code="controller_diagnostic_transport_failed",
            )
        )
    expected_kind = tokens[0] if tokens else ""
    try:
        public = validate_public_result(result, expected_kind=expected_kind)
    except ValueError:
        return CommandResult(
            error=TransportError(
                "controller diagnostic response invalid",
                code="controller_diagnostic_response_invalid",
            )
        )
    human = json.dumps(public, sort_keys=True, ensure_ascii=True) + "\n"
    if public["state"] == "ok":
        return CommandResult(data=public, human_stdout=human)
    return CommandResult(
        data=public,
        error=OperatorError(
            "controller diagnostic returned a non-ok state",
            code=public["error_code"],
        ),
        human_stdout=human,
    )


def main(argv: Optional[list[str]] = None) -> int:
    """Print one exact safe result; parser failures retain argparse's exit 2."""
    try:
        result = run(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return 0 if result.get("state") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
