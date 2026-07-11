"""Bounded typed adapters for local and controller operations.

Transport selection is resolved in :mod:`anvil_serving.targets`.  This module
only executes a declared operation through that selected transport; it never
accepts shell text, an argv list, or a raw credential value.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import base64
import hashlib
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import threading
from types import MappingProxyType
from typing import Any, Optional
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024

_OPERATION_RE = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")
_TRANSPORT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_FORBIDDEN_ARGUMENT_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "argv",
        "authorization",
        "command",
        "credential",
        "credentials",
        "executable",
        "password",
        "private_key",
        "script",
        "secret",
        "shell",
        "stdin",
        "token",
    }
)
_SECRET_KEY_PARTS = frozenset(
    {"api", "apikey", "authorization", "credential", "password", "secret", "token"}
)
_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)\b(bearer\s+)[^\s'\"\\]+"),
    re.compile(
        r"(?i)\b((?:access[_-]?key|api[_-]?key|authorization|client[_-]?secret|"
        r"private[_-]?key|secret[_-]?access[_-]?key|session[_-]?token|x-api-key)"
        r"\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"
    ),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{8,}\b"),
)
_MAX_ARGUMENT_DEPTH = 16
_MAX_ARGUMENT_ITEMS = 256
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")
_OPERATION_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
_OPERATION_STATUSES = frozenset({"running", "succeeded", "failed", "expired", "unknown"})
_OPERATION_STATUS_FIELDS = frozenset(
    {
        "key",
        "request_id",
        "fingerprint",
        "status",
        "created_at",
        "updated_at",
        "expires_at",
        "response",
        "result",
        "error",
    }
)
_SSH_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_SSH_ADAPTER_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./:=+@%-]{1,256}$")
_MAX_KNOWN_HOSTS_BYTES = 4 * 1024 * 1024
_MAX_KNOWN_HOST_LINE_BYTES = 16 * 1024
_MAX_IDENTITY_BYTES = 1024 * 1024
_PIPE_CLOSE_SECONDS = 1.0


class _ProcessOutputOverflow(Exception):
    pass


class _ProcessPipeError(Exception):
    pass


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _urlopen_no_proxy_no_redirect(req: urllib.request.Request, timeout: float):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler(),
    )
    return opener.open(req, timeout=timeout)


def _strict_json_loads(value: str) -> Any:
    def reject_constant(constant: str) -> None:
        raise ValueError("non-finite JSON number: " + constant)

    return json.loads(value, parse_constant=reject_constant)


class TransportError(RuntimeError):
    """A transport failure with execution-state metadata.

    ``execution_state`` tells recovery logic whether the operation definitely
    did not start, failed at the execution host, or may have completed before a
    malformed/truncated response was observed.
    """

    exit_class = "transport"

    def __init__(
        self,
        code: str,
        message: str,
        *,
        execution_state: str = "not_started",
        details: Optional[Mapping[str, object]] = None,
    ) -> None:
        if execution_state not in {
            "not_started",
            "local_failed",
            "remote_failed",
            "partial_result",
        }:
            raise ValueError("unknown execution state %r" % execution_state)
        self.code = code
        self.execution_state = execution_state
        self.details = MappingProxyType(dict(details or {}))
        self.may_have_executed = execution_state in {"remote_failed", "partial_result"}
        super().__init__(message)

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": str(self),
            "execution_state": self.execution_state,
            "may_have_executed": self.may_have_executed,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class Operation:
    """A declared operation and its structured, non-secret arguments."""

    name: str
    arguments: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _OPERATION_RE.fullmatch(self.name):
            raise ValueError("operation name must be a declared identifier")
        if not isinstance(self.arguments, Mapping):
            raise ValueError("operation arguments must be an object")
        arguments = dict(self.arguments)
        _validate_arguments(arguments)
        object.__setattr__(self, "arguments", MappingProxyType(arguments))


@dataclass(frozen=True)
class TransportResult:
    """A bounded result returned by a typed transport adapter."""

    operation: str
    transport: str
    data: Mapping[str, object]
    response_bytes: int = 0

    def __post_init__(self) -> None:
        if self.transport not in {"local", "controller", "ssh"}:
            raise ValueError("unsupported transport %r" % self.transport)
        if not isinstance(self.data, Mapping):
            raise ValueError("transport data must be an object")
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))

    def as_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "transport": self.transport,
            "data": dict(self.data),
            "response_bytes": self.response_bytes,
        }


def redact(value: Any, *secrets: str) -> Any:
    """Return a recursively redacted value safe for diagnostics and output."""
    known = tuple(secret for secret in secrets if secret)
    if isinstance(value, str):
        for secret in known:
            value = value.replace(secret, "<redacted>")
        for pattern in _SECRET_TEXT_PATTERNS:
            value = pattern.sub(
                lambda match: match.group(1) + "<redacted>" if match.lastindex else "<redacted>",
                value,
            )
        return value
    if isinstance(value, list):
        return [redact(item, *known) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item, *known) for item in value)
    if isinstance(value, Mapping):
        rendered: dict[object, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            safe_key = redact(key_text, *known)
            rendered[safe_key] = "<redacted>" if _is_secret_key(key_text) else redact(item, *known)
        return rendered
    return value


class LocalTransport:
    """Dispatch a declared operation to an injected local typed handler."""

    def __init__(
        self,
        handlers: Mapping[str, Callable[[Mapping[str, object]], Mapping[str, object]]],
        *,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._handlers = dict(handlers)
        for name, handler in self._handlers.items():
            if not _OPERATION_RE.fullmatch(name) or not callable(handler):
                raise ValueError("local handlers must map declared operation names to callables")
        _reject_normalization_collisions(self._handlers, "local handler names")
        self.max_response_bytes = _bounded_response_bytes(max_response_bytes)

    def execute(self, operation: Operation) -> TransportResult:
        handler = self._handlers.get(_mcp_operation_name(operation.name))
        if handler is None:
            # Keep injected, non-MCP handlers compatible with their declared
            # operation names while preferring the MCP catalog spelling.
            handler = self._handlers.get(operation.name)
        if handler is None:
            raise TransportError(
                "unknown_operation",
                "operation is not declared for local transport",
                details={"operation": operation.name},
            )
        try:
            data = handler(operation.arguments)
        except TransportError:
            raise
        except Exception as exc:
            raise TransportError(
                "local_operation_failed",
                "local operation failed",
                execution_state="local_failed",
                details={"operation": operation.name, "error": redact(str(exc))},
            ) from None
        if not isinstance(data, Mapping):
            raise TransportError(
                "bad_local_result",
                "local operation returned a non-object result",
                execution_state="partial_result",
                details={"operation": operation.name},
            )
        safe_data = redact(dict(data))
        try:
            response_bytes = len(
                json.dumps(safe_data, separators=(",", ":"), allow_nan=False).encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            raise TransportError(
                "bad_local_result",
                "local operation returned a non-JSON result",
                execution_state="partial_result",
                details={"operation": operation.name, "error": str(exc)},
            ) from None
        if response_bytes > self.max_response_bytes:
            raise TransportError(
                "response_too_large",
                "local operation response exceeds the configured limit",
                execution_state="partial_result",
                details={"max_response_bytes": self.max_response_bytes},
            )
        return TransportResult(operation.name, "local", safe_data, response_bytes)


class ControllerTransport:
    """Call a declared controller operation over bounded authenticated HTTP."""

    def __init__(
        self,
        endpoint: str,
        *,
        auth_env: str,
        allowed_operations: Sequence[str],
        environment: Optional[Mapping[str, str]] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        opener: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.endpoint = _controller_endpoint(endpoint)
        if not isinstance(auth_env, str) or not _ENV_RE.fullmatch(auth_env):
            raise ValueError("auth_env must name an environment variable")
        allowed = tuple(allowed_operations)
        if not allowed or any(
            not isinstance(name, str) or not _OPERATION_RE.fullmatch(name) for name in allowed
        ):
            raise ValueError("allowed_operations must contain declared operation names")
        _reject_normalization_collisions(allowed, "allowed_operations")
        self.auth_env = auth_env
        self.allowed_operations = frozenset(allowed)
        self.environment = os.environ if environment is None else environment
        self.timeout_seconds = _bounded_timeout(timeout_seconds)
        self.max_response_bytes = _bounded_response_bytes(max_response_bytes)
        self._opener = opener or _urlopen_no_proxy_no_redirect

    def execute(
        self,
        operation: Operation,
        *,
        timeout_seconds: Optional[float] = None,
        max_response_bytes: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        idempotency_context: Optional[Mapping[str, str]] = None,
    ) -> TransportResult:
        if operation.name not in self.allowed_operations:
            raise TransportError(
                "unknown_operation",
                "operation is not declared for controller transport",
                details={"operation": operation.name},
            )
        if _confirmed_mutation(operation) and idempotency_key is None:
            raise TransportError(
                "idempotency_key_required",
                "confirmed controller operations require an idempotency key",
                details={"operation": operation.name},
            )
        _validate_controller_endpoint_host(self.endpoint)
        token = self._token()
        timeout = (
            self.timeout_seconds if timeout_seconds is None else _bounded_timeout(timeout_seconds)
        )
        limit = (
            self.max_response_bytes
            if max_response_bytes is None
            else _bounded_response_bytes(max_response_bytes)
        )
        payload: dict[str, object] = {
            "name": _mcp_operation_name(operation.name),
            "arguments": dict(operation.arguments),
        }
        if idempotency_key is not None:
            payload["context"] = _validated_idempotency_context(idempotency_context)
        body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        }
        if idempotency_key is not None:
            headers["X-Anvil-Idempotency-Key"] = _validated_idempotency_key(idempotency_key)
        request = urllib.request.Request(
            self.endpoint + "/tools/call",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with self._opener(request, timeout=timeout) as response:
                raw = _read_bounded(response, limit)
        except TransportError:
            raise
        except urllib.error.HTTPError as exc:
            raise _http_error(exc, token, limit) from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (ConnectionRefusedError, socket.gaierror)):
                raise TransportError(
                    "controller_connect_failed",
                    "controller connection failed before dispatch",
                    details={"endpoint": self.endpoint, "error": redact(str(exc.reason), token)},
                ) from None
            raise TransportError(
                "controller_unavailable",
                "controller request outcome is ambiguous",
                execution_state="partial_result",
                details={"endpoint": self.endpoint, "error": redact(str(exc), token)},
            ) from None
        except (ConnectionRefusedError, socket.gaierror) as exc:
            raise TransportError(
                "controller_connect_failed",
                "controller connection failed before dispatch",
                details={"endpoint": self.endpoint, "error": redact(str(exc), token)},
            ) from None
        except (socket.timeout, TimeoutError) as exc:
            raise TransportError(
                "controller_timeout",
                "controller request timed out",
                execution_state="partial_result",
                details={
                    "endpoint": self.endpoint,
                    "timeout_seconds": timeout,
                    "error": redact(str(exc), token),
                },
            ) from None
        except Exception as exc:
            raise TransportError(
                "controller_unavailable",
                "controller request outcome is ambiguous",
                execution_state="partial_result",
                details={"endpoint": self.endpoint, "error": redact(str(exc), token)},
            ) from None
        if len(raw) > limit:
            raise TransportError(
                "response_too_large",
                "controller response exceeds the configured limit",
                execution_state="partial_result",
                details={"max_response_bytes": limit},
            )
        try:
            parsed = _strict_json_loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise TransportError(
                "bad_controller_response",
                "controller response was not valid JSON",
                execution_state="partial_result",
                details={"error": redact(str(exc), token)},
            ) from None
        if not isinstance(parsed, Mapping):
            raise TransportError(
                "bad_controller_response",
                "controller response must be an object",
                execution_state="partial_result",
            )
        response_data = redact(dict(parsed), token)
        if type(response_data.get("ok")) is not bool:
            raise TransportError(
                "bad_controller_response",
                "controller response must contain a boolean ok field",
                execution_state="partial_result",
            )
        if response_data.get("ok") is False:
            error = response_data.get("error")
            details: dict[str, object] = {"operation": operation.name}
            if isinstance(error, Mapping):
                details["controller_error"] = dict(error)
            raise TransportError(
                "controller_operation_failed",
                "controller reported an operation failure",
                execution_state="remote_failed",
                details=details,
            )
        return TransportResult(operation.name, "controller", response_data, len(raw))

    def operation_status(
        self,
        idempotency_key: str,
        *,
        timeout_seconds: Optional[float] = None,
        max_response_bytes: Optional[int] = None,
    ) -> TransportResult:
        """Read a bounded durable status record without replaying a mutation."""
        key = _validated_idempotency_key(idempotency_key)
        _validate_controller_endpoint_host(self.endpoint)
        token = self._token()
        timeout = (
            self.timeout_seconds if timeout_seconds is None else _bounded_timeout(timeout_seconds)
        )
        limit = (
            self.max_response_bytes
            if max_response_bytes is None
            else _bounded_response_bytes(max_response_bytes)
        )
        request = urllib.request.Request(
            self.endpoint + "/operations/" + urllib.parse.quote(key, safe=""),
            headers={"Accept": "application/json", "Authorization": "Bearer " + token},
            method="GET",
        )
        try:
            with self._opener(request, timeout=timeout) as response:
                raw = _read_bounded(response, limit)
        except TransportError:
            raise
        except urllib.error.HTTPError as exc:
            raise _http_error(exc, token, limit) from None
        except Exception as exc:
            raise TransportError(
                "operation_status_unavailable",
                "operation status request failed",
                details={"endpoint": self.endpoint, "error": redact(str(exc), token)},
            ) from None
        if len(raw) > limit:
            raise TransportError(
                "response_too_large",
                "controller response exceeds the configured limit",
                execution_state="partial_result",
                details={"max_response_bytes": limit},
            )
        try:
            parsed = _strict_json_loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise TransportError(
                "bad_controller_response",
                "controller response was not valid JSON",
                execution_state="partial_result",
                details={"error": redact(str(exc), token)},
            ) from None
        if not isinstance(parsed, Mapping):
            raise TransportError(
                "bad_controller_response",
                "controller response must be an object",
                execution_state="partial_result",
            )
        response_data = _validated_operation_status_response(parsed, expected_key=key)
        return TransportResult(
            "operation-status", "controller", redact(response_data, token), len(raw)
        )

    def _token(self) -> str:
        token = (self.environment.get(self.auth_env) or "").strip()
        if not token:
            raise TransportError(
                "missing_controller_token",
                "controller token environment variable is unset or empty",
                details={"auth_env": self.auth_env},
            )
        return token


class SSHRecoveryTransport:
    """Run fixed bootstrap/recovery adapters over strictly verified OpenSSH."""

    def __init__(
        self,
        endpoint: str,
        *,
        adapters: Mapping[str, Sequence[str]],
        known_hosts_path: str,
        host_key_fingerprint: str,
        identity_file: str,
        transport_id: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        runner: Callable[..., Any] | None = None,
    ) -> None:
        self.host, self.port, self.user = _ssh_endpoint(endpoint)
        self.endpoint = endpoint.rstrip("/")
        if transport_id is not None and (
            not isinstance(transport_id, str) or not _TRANSPORT_ID_RE.fullmatch(transport_id)
        ):
            raise ValueError("transport_id must be a declared identifier")
        self.transport_id = transport_id
        self.adapters = {name: tuple(argv) for name, argv in adapters.items()}
        if not self.adapters:
            raise ValueError("SSH recovery requires at least one declared adapter")
        for name, argv in self.adapters.items():
            if not _OPERATION_RE.fullmatch(name) or not argv:
                raise ValueError(
                    "SSH adapters must map declared operation names to argument arrays"
                )
            if any(
                not isinstance(token, str) or not _SSH_ADAPTER_TOKEN_RE.fullmatch(token)
                for token in argv
            ):
                raise ValueError("SSH adapter arguments contain unsafe shell text")
        self.known_hosts_path = _required_path(known_hosts_path, "known_hosts")
        self.host_key_fingerprint = _ssh_fingerprint(host_key_fingerprint)
        self.identity_file = _required_path(identity_file, "SSH identity")
        self.identity_digest = _identity_digest(self.identity_file)
        self.timeout_seconds = _bounded_timeout(timeout_seconds)
        self.max_response_bytes = _bounded_response_bytes(max_response_bytes)
        self._runner = runner or _run_bounded_process

    def execute(self, operation: Operation) -> TransportResult:
        adapter = self.adapters.get(operation.name)
        if adapter is None:
            raise TransportError(
                "ssh_operation_not_allowed",
                "operation is not declared for SSH recovery",
                details={"operation": operation.name},
            )
        if operation.arguments:
            raise TransportError(
                "ssh_arguments_not_allowed",
                "SSH recovery adapters do not accept runtime command arguments",
                details={"operation": operation.name},
            )
        known_host_line = _verify_known_host(
            self.known_hosts_path, self.host, self.port, self.host_key_fingerprint
        )
        try:
            with (
                _verified_known_hosts_file(known_host_line) as verified_hosts_path,
                _verified_identity_file(
                    self.identity_file, self.identity_digest
                ) as verified_identity_path,
            ):
                argv = [
                    "ssh",
                    "-F",
                    os.devnull,
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "StrictHostKeyChecking=yes",
                    "-o",
                    "UserKnownHostsFile=" + verified_hosts_path,
                    "-o",
                    "GlobalKnownHostsFile=" + os.devnull,
                    "-o",
                    "ProxyCommand=none",
                    "-o",
                    "ProxyJump=none",
                    "-o",
                    "IdentityAgent=none",
                    "-o",
                    "IdentitiesOnly=yes",
                    "-o",
                    "IdentityFile=none",
                    "-o",
                    "IdentityFile=" + verified_identity_path,
                    "-o",
                    "ForwardAgent=no",
                    "-o",
                    "AddKeysToAgent=no",
                    "-o",
                    "CanonicalizeHostname=no",
                    "-o",
                    "ClearAllForwardings=yes",
                    "-o",
                    "PasswordAuthentication=no",
                    "-o",
                    "KbdInteractiveAuthentication=no",
                    "-o",
                    "PreferredAuthentications=publickey",
                    "-o",
                    "ConnectTimeout=" + str(max(1, math.ceil(self.timeout_seconds))),
                    "-p",
                    str(self.port),
                ]
                target = (self.user + "@" if self.user else "") + self.host
                argv.extend(["--", target, *adapter])
                completed = self._runner(
                    argv,
                    timeout=self.timeout_seconds,
                    max_output_bytes=self.max_response_bytes,
                )
        except subprocess.TimeoutExpired:
            raise TransportError(
                "ssh_timeout", "SSH recovery timed out", execution_state="remote_failed"
            ) from None
        except _ProcessOutputOverflow:
            raise TransportError(
                "response_too_large",
                "SSH response exceeds the configured limit",
                execution_state="partial_result",
                details={"max_response_bytes": self.max_response_bytes},
            ) from None
        except _ProcessPipeError as exc:
            raise TransportError(
                "ssh_output_failed",
                "SSH process output could not be collected safely",
                execution_state="partial_result",
                details={"error": str(exc)},
            ) from None
        except OSError as exc:
            raise TransportError(
                "ssh_launch_failed",
                "SSH recovery process could not start",
                details={"error": str(exc)},
            ) from None
        stdout = completed.stdout
        stderr = completed.stderr
        if not isinstance(stdout, bytes) or not isinstance(stderr, bytes):
            raise TransportError(
                "bad_ssh_result",
                "SSH process output must be bytes",
                execution_state="partial_result",
            )
        if len(stdout) + len(stderr) > self.max_response_bytes:
            raise TransportError(
                "response_too_large",
                "SSH response exceeds the configured limit",
                execution_state="partial_result",
            )
        if completed.returncode != 0:
            raise TransportError(
                "ssh_operation_failed",
                "SSH recovery adapter failed",
                execution_state="remote_failed",
                details={
                    "returncode": completed.returncode,
                    "stderr": stderr.decode("utf-8", "replace"),
                },
            )
        data = {"stdout": stdout.decode("utf-8", "replace"), "returncode": 0}
        return TransportResult(operation.name, "ssh", data, len(stdout) + len(stderr))


def execute_plan(
    plan: Any,
    operation: Operation,
    *,
    local: Optional[LocalTransport] = None,
    controller: Optional[ControllerTransport] = None,
    ssh: Optional[SSHRecoveryTransport] = None,
    allow_ssh_fallback: bool = False,
    idempotency_key: Optional[str] = None,
) -> TransportResult:
    """Execute an operation through the already-resolved plan transport."""
    command = getattr(plan, "command", None)
    if command is None or getattr(command, "name", None) != operation.name:
        raise TransportError(
            "operation_plan_mismatch",
            "operation does not match the resolved execution plan",
            details={"operation": operation.name},
        )
    selected = getattr(plan, "transport", None)
    if selected == "local":
        if local is None:
            raise TransportError(
                "local_transport_missing", "local transport adapter is not configured"
            )
        return local.execute(operation)
    if selected == "controller":
        if controller is None:
            raise TransportError(
                "controller_transport_missing", "controller transport adapter is not configured"
            )
        _validate_controller_plan_binding(plan, controller)
        if _confirmed_mutation(operation) and idempotency_key is None:
            raise TransportError(
                "idempotency_key_required",
                "confirmed controller operations require an idempotency key",
                details={"operation": operation.name},
            )
        context = None
        if idempotency_key is not None:
            context = {
                "topology": getattr(plan, "topology_snapshot", None)
                or getattr(plan, "topology_id", None),
                "execution_host": getattr(getattr(plan, "execution_host", None), "id", None),
                "execution_runtime": getattr(getattr(plan, "execution_runtime", None), "id", None),
            }
        try:
            return controller.execute(
                operation,
                idempotency_key=idempotency_key,
                idempotency_context=context,
            )
        except TransportError as exc:
            if not allow_ssh_fallback or exc.code != "controller_connect_failed":
                raise
            if not getattr(command, "recovery_capable", False):
                raise
            if ssh is None:
                raise TransportError(
                    "ssh_transport_missing", "SSH recovery adapter is not configured"
                ) from None
            _validate_ssh_plan_binding(plan, ssh, endpoint_attribute="recovery_transport_endpoint")
            return ssh.execute(operation)
    if selected == "ssh":
        if ssh is None:
            raise TransportError("ssh_transport_missing", "SSH recovery adapter is not configured")
        if not getattr(command, "recovery_capable", False):
            raise TransportError("ssh_operation_not_allowed", "operation is not recovery-capable")
        _validate_ssh_plan_binding(plan, ssh, endpoint_attribute="transport_endpoint")
        return ssh.execute(operation)
    raise TransportError(
        "unsupported_transport",
        "execution plan requires an unsupported transport",
        details={"transport": selected},
    )


def _validate_arguments(value: object, *, depth: int = 0, count: list[int] | None = None) -> None:
    if depth > _MAX_ARGUMENT_DEPTH:
        raise ValueError("operation arguments are nested too deeply")
    if count is None:
        count = [0]
    if isinstance(value, Mapping):
        for key, item in value.items():
            count[0] += 1
            if count[0] > _MAX_ARGUMENT_ITEMS:
                raise ValueError("operation arguments contain too many values")
            if not isinstance(key, str):
                raise ValueError("operation argument names must be strings")
            if _is_forbidden_argument_key(key):
                raise ValueError(
                    "operation arguments must not contain command or credential payloads"
                )
            _validate_arguments(item, depth=depth + 1, count=count)
    elif isinstance(value, (list, tuple)):
        for item in value:
            count[0] += 1
            if count[0] > _MAX_ARGUMENT_ITEMS:
                raise ValueError("operation arguments contain too many values")
            _validate_arguments(item, depth=depth + 1, count=count)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("operation arguments must contain finite numbers")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("operation arguments must contain JSON scalar values")


def _is_secret_key(key: str) -> bool:
    parts = tuple(part for part in re.split(r"[^a-z0-9]+", key.lower()) if part)
    compact = "".join(parts)
    credential_shapes = (
        "accesskey",
        "accesstoken",
        "apikey",
        "authorization",
        "bearertoken",
        "clientsecret",
        "privatekey",
        "refreshtoken",
        "secretaccesskey",
        "sessiontoken",
    )
    return (
        key.lower() in _FORBIDDEN_ARGUMENT_KEYS
        or bool(set(parts) & _SECRET_KEY_PARTS)
        or any(shape in compact for shape in credential_shapes)
    )


def _is_forbidden_argument_key(key: str) -> bool:
    return key.lower() in _FORBIDDEN_ARGUMENT_KEYS or _is_secret_key(key)


def _validated_idempotency_key(value: str) -> str:
    if not isinstance(value, str) or not _IDEMPOTENCY_KEY_RE.fullmatch(value):
        raise ValueError("idempotency key must be a 1-128 character token")
    return value


def _validated_operation_status_response(
    value: Mapping[str, object], *, expected_key: str
) -> dict[str, object]:
    undeclared = set(value) - _OPERATION_STATUS_FIELDS
    if undeclared:
        raise TransportError(
            "bad_controller_response",
            "operation status response contains undeclared fields",
            execution_state="partial_result",
            details={"fields": sorted(str(field) for field in undeclared)},
        )
    key = value.get("key")
    status = value.get("status")
    if not isinstance(key, str) or not _IDEMPOTENCY_KEY_RE.fullmatch(key):
        raise TransportError(
            "bad_controller_response",
            "operation status response must contain a valid string key",
            execution_state="partial_result",
        )
    if key != expected_key:
        raise TransportError(
            "bad_controller_response",
            "operation status response key does not match the requested key",
            execution_state="partial_result",
        )
    if not isinstance(status, str) or status not in _OPERATION_STATUSES:
        raise TransportError(
            "bad_controller_response",
            "operation status response must contain a recognized string status",
            execution_state="partial_result",
        )

    request_id = value.get("request_id")
    if "request_id" in value and (
        not isinstance(request_id, str) or not _REQUEST_ID_RE.fullmatch(request_id)
    ):
        raise TransportError(
            "bad_controller_response",
            "operation status response request_id must be a bounded identifier",
            execution_state="partial_result",
        )
    fingerprint = value.get("fingerprint")
    if "fingerprint" in value and (
        not isinstance(fingerprint, str) or not _OPERATION_FINGERPRINT_RE.fullmatch(fingerprint)
    ):
        raise TransportError(
            "bad_controller_response",
            "operation status response fingerprint must be a SHA-256 digest",
            execution_state="partial_result",
        )
    for field in ("created_at", "updated_at", "expires_at"):
        item = value.get(field)
        if field in value and (
            isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item)
        ):
            raise TransportError(
                "bad_controller_response",
                "operation status response timestamps must be finite numbers",
                execution_state="partial_result",
                details={"field": field},
            )
    for field in ("response", "result", "error"):
        if field in value and not isinstance(value.get(field), Mapping):
            raise TransportError(
                "bad_controller_response",
                "operation status response result fields must be objects",
                execution_state="partial_result",
                details={"field": field},
            )
    return dict(value)


def _confirmed_mutation(operation: Operation) -> bool:
    return (
        operation.arguments.get("confirm") is True
        and operation.arguments.get("dry_run") is not True
    )


def _validated_idempotency_context(value: object) -> dict[str, str]:
    fields = ("topology", "execution_host", "execution_runtime")
    if not isinstance(value, Mapping) or set(value) != set(fields):
        raise ValueError(
            "idempotency context requires topology, execution_host, and execution_runtime"
        )
    context: dict[str, str] = {}
    for field in fields:
        item = value.get(field)
        if not isinstance(item, str) or not _IDEMPOTENCY_KEY_RE.fullmatch(item):
            raise ValueError("idempotency context fields must be bounded identifiers")
        context[field] = item
    return context


def _mcp_operation_name(name: str) -> str:
    """Translate topology operation declarations to MCP catalog identifiers."""
    return name.replace("-", "_")


def _reject_normalization_collisions(
    names: Sequence[str] | Mapping[str, object], label: str
) -> None:
    normalized: dict[str, str] = {}
    for name in names:
        catalog_name = _mcp_operation_name(name)
        existing = normalized.setdefault(catalog_name, name)
        if existing != name:
            raise ValueError(
                "%s must not contain hyphen/underscore normalization collisions" % label
            )


def _is_safe_controller_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if address.is_loopback:
        return True
    if (
        address.is_unspecified
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
    ):
        return False
    if address.version == 4:
        private_networks = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
            ipaddress.ip_network("100.64.0.0/10"),
        )
        return any(address in network for network in private_networks)
    return address in ipaddress.ip_network("fc00::/7")


def _validate_controller_endpoint_host(endpoint: str) -> None:
    """Refuse controller endpoints that could send tokens to public hosts."""
    parsed = urllib.parse.urlparse(endpoint)
    host = parsed.hostname
    if not host:  # Defensive: _controller_endpoint() validated this at construction.
        raise TransportError("unsafe_controller_endpoint", "controller endpoint host is unsafe")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise TransportError(
            "unsafe_controller_endpoint",
            "controller endpoint must use a literal loopback, private, or tailnet IP address",
            details={"host": host},
        ) from None
    if not _is_safe_controller_ip(address):
        raise TransportError(
            "unsafe_controller_endpoint",
            "controller endpoint must use a loopback, private, or tailnet IP address",
            details={"host": host, "addresses": [str(address)]},
        )


def _controller_endpoint(endpoint: str) -> str:
    if not isinstance(endpoint, str):
        raise ValueError("controller endpoint must be an http(s) URL")
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("controller endpoint must be an http(s) URL with a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("controller endpoint must not include credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("controller endpoint must not include a query or fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("controller endpoint must include a valid port") from exc
    if parsed.hostname.lower() == "localhost":
        raise ValueError("use 127.0.0.1 or a private/tailnet host, not localhost")
    return endpoint.rstrip("/")


def _ssh_endpoint(endpoint: str) -> tuple[str, int, str | None]:
    if not isinstance(endpoint, str):
        raise ValueError("SSH endpoint must be an ssh URL")
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "ssh" or not parsed.hostname or parsed.password is not None:
        raise ValueError("SSH endpoint must be a credential-free ssh URL")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("SSH endpoint must not include path, query, or fragment data")
    if parsed.username is not None and not _SSH_USER_RE.fullmatch(parsed.username):
        raise ValueError("SSH endpoint username is invalid")
    try:
        address = ipaddress.ip_address(parsed.hostname)
        port = parsed.port or 22
    except ValueError as exc:
        raise ValueError("SSH endpoint must use a safe literal IP and valid port") from exc
    if not _is_safe_controller_ip(address) or not 1 <= port <= 65535:
        raise ValueError("SSH endpoint must use a safe literal IP and valid port")
    return str(address), port, parsed.username


def _validate_ssh_plan_binding(
    plan: Any,
    ssh: SSHRecoveryTransport,
    *,
    endpoint_attribute: str,
) -> None:
    endpoint = getattr(plan, endpoint_attribute, None)
    if not isinstance(endpoint, str) or endpoint.rstrip("/") != ssh.endpoint:
        raise TransportError(
            "ssh_endpoint_mismatch",
            "SSH adapter endpoint does not match the execution plan",
        )
    execution_host = getattr(plan, "execution_host", None)
    address = getattr(execution_host, "address", None)
    try:
        resolved_address = str(ipaddress.ip_address(address))
    except (TypeError, ValueError):
        resolved_address = None
    if resolved_address != ssh.host:
        raise TransportError(
            "ssh_execution_host_mismatch",
            "SSH adapter host does not match the resolved execution host",
        )
    recovery = endpoint_attribute == "recovery_transport_endpoint"
    transport_id_attribute = "recovery_transport_id" if recovery else "transport_id"
    fingerprint_attribute = (
        "recovery_host_key_fingerprint" if recovery else "transport_host_key_fingerprint"
    )
    known_hosts_attribute = (
        "recovery_known_hosts_path" if recovery else "transport_known_hosts_path"
    )
    plan_transport_id = getattr(plan, transport_id_attribute, None)
    if not isinstance(plan_transport_id, str) or plan_transport_id != ssh.transport_id:
        raise TransportError(
            "ssh_transport_identity_mismatch",
            "SSH adapter identity does not match the selected transport record",
        )
    plan_fingerprint = getattr(plan, fingerprint_attribute, None)
    if not isinstance(plan_fingerprint, str) or not hmac.compare_digest(
        plan_fingerprint.rstrip("="), ssh.host_key_fingerprint
    ):
        raise TransportError(
            "ssh_host_key_binding_mismatch",
            "SSH adapter fingerprint does not match the selected transport record",
        )
    plan_known_hosts = getattr(plan, known_hosts_attribute, None)
    if not isinstance(plan_known_hosts, str) or _normalized_path(
        plan_known_hosts
    ) != _normalized_path(ssh.known_hosts_path):
        raise TransportError(
            "ssh_known_hosts_binding_mismatch",
            "SSH adapter known_hosts path does not match the selected transport record",
        )


def _validate_controller_plan_binding(plan: Any, controller: ControllerTransport) -> None:
    endpoint = getattr(plan, "transport_endpoint", None)
    if not isinstance(endpoint, str) or _controller_endpoint(endpoint) != controller.endpoint:
        raise TransportError(
            "controller_endpoint_mismatch",
            "controller adapter endpoint does not match the execution plan",
        )
    parsed = urllib.parse.urlparse(controller.endpoint)
    endpoint_host = parsed.hostname
    execution_host = getattr(plan, "execution_host", None)
    command_host = getattr(plan, "command_host", None)
    if execution_host is None:
        raise TransportError(
            "controller_execution_host_missing",
            "controller execution plan has no declared execution host",
        )
    address = getattr(execution_host, "address", None)
    try:
        endpoint_address = ipaddress.ip_address(endpoint_host)
    except (TypeError, ValueError):
        endpoint_address = None
    if endpoint_address is not None and endpoint_address.is_loopback:
        if getattr(command_host, "id", None) != getattr(execution_host, "id", None):
            raise TransportError(
                "controller_loopback_host_mismatch",
                "loopback controller endpoint is not on the command host",
            )
        return
    if address is None:
        return
    try:
        owner_matches = ipaddress.ip_address(address) == endpoint_address
    except ValueError:
        owner_matches = str(address).rstrip(".").lower() == str(endpoint_host).rstrip(".").lower()
    if not owner_matches:
        raise TransportError(
            "controller_execution_host_mismatch",
            "controller endpoint does not match the resolved execution host",
        )


def _required_path(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " path is required")
    path = Path(value).expanduser()
    if not path.is_file():
        raise ValueError(label + " file does not exist")
    return str(path)


def _identity_digest(path: str) -> str:
    try:
        with Path(path).open("rb") as handle:
            contents = handle.read(_MAX_IDENTITY_BYTES + 1)
    except OSError as exc:
        raise ValueError("SSH identity file could not be read") from exc
    if len(contents) > _MAX_IDENTITY_BYTES:
        raise ValueError("SSH identity file exceeds the configured safety limit")
    return hashlib.sha256(contents).hexdigest()


def _normalized_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


@contextmanager
def _verified_identity_file(path: str, expected_digest: str):
    temporary_path = None
    try:
        try:
            with Path(path).open("rb") as identity:
                contents = identity.read(_MAX_IDENTITY_BYTES + 1)
        except OSError as exc:
            raise TransportError(
                "ssh_identity_unavailable",
                "SSH identity could not be read before launch",
                details={"error": str(exc)},
            ) from None
        if len(contents) > _MAX_IDENTITY_BYTES:
            raise TransportError(
                "ssh_identity_changed",
                "SSH identity no longer matches the configured client identity",
            )
        actual_digest = hashlib.sha256(contents).hexdigest()
        if not hmac.compare_digest(actual_digest, expected_digest):
            raise TransportError(
                "ssh_identity_changed",
                "SSH identity no longer matches the configured client identity",
            )
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as verified_identity:
            verified_identity.write(contents)
            temporary_path = verified_identity.name
        os.chmod(temporary_path, 0o600)
        yield temporary_path
    finally:
        if temporary_path is not None:
            try:
                Path(temporary_path).unlink(missing_ok=True)
            except OSError as exc:
                raise _ProcessPipeError("verified identity cleanup failed: %s" % exc) from None


def _ssh_fingerprint(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{20,64}", value):
        raise ValueError("a SHA256 SSH host-key fingerprint is required")
    return value.rstrip("=")


def _known_host_matches(pattern: str, host: str, port: int) -> bool:
    target = host if port == 22 else "[%s]:%s" % (host, port)
    if pattern.startswith("|1|"):
        try:
            _, _, salt, digest = pattern.split("|", 3)
            actual = hmac.new(base64.b64decode(salt), target.encode("utf-8"), hashlib.sha1).digest()
            return hmac.compare_digest(actual, base64.b64decode(digest))
        except (ValueError, TypeError):
            return False
    return target in pattern.split(",")


def _verify_known_host(path: str, host: str, port: int, expected: str) -> str:
    found_host = False
    total_bytes = 0
    try:
        with Path(path).open("rb") as known_hosts:
            while raw_line := known_hosts.readline(_MAX_KNOWN_HOST_LINE_BYTES + 1):
                total_bytes += len(raw_line)
                if (
                    total_bytes > _MAX_KNOWN_HOSTS_BYTES
                    or len(raw_line) > _MAX_KNOWN_HOST_LINE_BYTES
                ):
                    raise TransportError(
                        "ssh_known_hosts_too_large",
                        "known_hosts exceeds the configured safety limit",
                    )
                line = raw_line.decode("utf-8").strip()
                fields = line.split()
                if not fields or fields[0].startswith("#") or len(fields) < 3:
                    continue
                offset = 1 if fields[0].startswith("@") else 0
                if len(fields) < offset + 3 or not _known_host_matches(fields[offset], host, port):
                    continue
                found_host = True
                try:
                    raw_key = base64.b64decode(fields[offset + 2], validate=True)
                except (ValueError, TypeError):
                    continue
                actual = "SHA256:" + base64.b64encode(hashlib.sha256(raw_key).digest()).decode(
                    "ascii"
                ).rstrip("=")
                if hmac.compare_digest(actual, expected):
                    return line
    except TransportError:
        raise
    except (OSError, UnicodeError) as exc:
        raise TransportError(
            "ssh_known_hosts_unreadable",
            "known_hosts could not be read safely",
            details={"error": str(exc)},
        ) from None
    code = "ssh_fingerprint_mismatch" if found_host else "ssh_unknown_host"
    message = (
        "SSH host-key fingerprint does not match"
        if found_host
        else "SSH host is absent from known_hosts"
    )
    raise TransportError(code, message)


@contextmanager
def _verified_known_hosts_file(line: str):
    path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", delete=False
        ) as verified_hosts:
            verified_hosts.write(line + "\n")
            path = verified_hosts.name
        os.chmod(path, 0o600)
        yield path
    finally:
        if path is not None:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError as exc:
                raise _ProcessPipeError("verified known_hosts cleanup failed: %s" % exc) from None


def _run_bounded_process(argv: Sequence[str], *, timeout: float, max_output_bytes: int):
    process = subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    output = [bytearray(), bytearray()]
    output_bytes = 0
    overflow = threading.Event()
    lock = threading.Lock()
    reader_errors: list[Exception] = []

    def drain(stream: Any, destination: bytearray) -> None:
        nonlocal output_bytes
        try:
            read = getattr(stream, "read1", stream.read)
            chunk_size = min(8192, max_output_bytes + 1)
            while chunk := read(chunk_size):
                with lock:
                    remaining = max_output_bytes - output_bytes
                    if len(chunk) > remaining:
                        if remaining > 0:
                            destination.extend(chunk[:remaining])
                            output_bytes += remaining
                        overflow.set()
                    else:
                        destination.extend(chunk)
                        output_bytes += len(chunk)
                if overflow.is_set():
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return
        except (OSError, ValueError) as exc:
            reader_errors.append(exc)

    readers = [
        threading.Thread(target=drain, args=(process.stdout, output[0]), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, output[1]), daemon=True),
    ]
    for reader in readers:
        reader.start()
    stuck_reader = False
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise
    finally:
        for reader in readers:
            reader.join(timeout=_PIPE_CLOSE_SECONDS)
        stuck_reader = any(reader.is_alive() for reader in readers)
        if not stuck_reader:
            process.stdout.close()
            process.stderr.close()
    if stuck_reader:
        raise _ProcessPipeError("SSH output pipes did not close after process termination")
    if overflow.is_set():
        raise _ProcessOutputOverflow
    if reader_errors:
        raise _ProcessPipeError(str(reader_errors[0]))
    return subprocess.CompletedProcess(list(argv), returncode, bytes(output[0]), bytes(output[1]))


def _bounded_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("timeout_seconds must be a finite number")
    value = float(value)
    if value <= 0 or value > MAX_TIMEOUT_SECONDS:
        raise ValueError("timeout_seconds must be between 0 and %s" % MAX_TIMEOUT_SECONDS)
    return value


def _bounded_response_bytes(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("max_response_bytes must be an integer")
    if value < 1 or value > MAX_RESPONSE_BYTES:
        raise ValueError("max_response_bytes must be between 1 and %s" % MAX_RESPONSE_BYTES)
    return value


def _read_bounded(response: Any, limit: int) -> bytes:
    raw = response.read(limit + 1)
    if not isinstance(raw, bytes):
        raise TransportError(
            "bad_controller_response",
            "controller response body must be bytes",
            execution_state="partial_result",
        )
    return raw


def _http_error(exc: urllib.error.HTTPError, token: str, limit: int) -> TransportError:
    details: dict[str, object] = {"status": exc.code}
    try:
        raw = exc.read(limit + 1)
    except Exception:
        raw = b""
    if isinstance(raw, bytes) and raw:
        details["body"] = redact(raw[:limit].decode("utf-8", "replace"), token)
        if len(raw) > limit:
            details["body_truncated"] = True
    return TransportError(
        "controller_http_error",
        "controller returned HTTP %s" % exc.code,
        execution_state="remote_failed",
        details=details,
    )
