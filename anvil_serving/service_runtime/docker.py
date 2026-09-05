"""Bounded Docker supervision for exact, declared service identities.

This adapter deliberately manages already-declared containers only.  Creating a
container or rendering a Compose definition remains the responsibility of the
serve/recipe lifecycle that owns those declarations.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from ..operator_output import redact
from ..serve_recipes import RECIPE_MANAGED_LABEL, RECIPE_MANAGED_VALUE
from .contracts import ServiceError


_TIMEOUT_SECONDS = 15
_MAX_OUTPUT_BYTES = 1024 * 1024
_MAX_LOG_TAIL = 5_000
_MAX_DISCOVERED = 128
_CONTAINER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_OWNERSHIP_LABEL = RECIPE_MANAGED_LABEL
_OWNERSHIP_VALUE = RECIPE_MANAGED_VALUE
_VALID_STARTUP_POLICIES = frozenset(("always", "unless-stopped", "no"))
_ENABLED_STARTUP_POLICIES = frozenset(("always", "unless-stopped"))


class Adapter:
    """Read Docker state and construct commands without executing mutations."""

    def __init__(self, *, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run):
        self._run = run

    def verify_context(self) -> None:
        """Refuse Docker CLI operations that would target a remote daemon."""
        context = os.environ.get("DOCKER_CONTEXT", "")
        docker_host = os.environ.get("DOCKER_HOST", "")
        if context:
            if len(context) > 256 or context.startswith("-") or _has_control_character(context):
                raise ServiceError("invalid_context", "Docker context selection is invalid")
            endpoint = self._context_endpoint(["docker", "context", "inspect", context], context)
        elif docker_host:
            endpoint = docker_host
        else:
            endpoint = self._context_endpoint(["docker", "context", "inspect"])
        if not _is_local_endpoint(endpoint):
            raise ServiceError(
                "remote_context", "Docker service lifecycle requires a local daemon context"
            )

    def _context_endpoint(self, argv: list[str], expected_name: str | None = None) -> str:
        result = self._command(argv)
        if result is None:
            raise ServiceError(
                "supervisor_unreachable", "Docker is unavailable while checking its context"
            )
        if result.returncode:
            raise ServiceError("supervisor_failed", "Docker context inspection failed")
        return _context_endpoint(result.stdout, expected_name=expected_name)

    def inspect(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        """Return a bounded observation after verifying the declared identity."""
        container = _container(binding)
        result = self._command(["docker", "inspect", container])
        if result is None:
            return _unavailable("unreachable")
        if result.returncode:
            return _failed_inspect(result.stderr)
        row = _single_inspection(result.stdout)
        _verify_identity(binding, row)
        state = row.get("State")
        if not isinstance(state, Mapping):
            raise ServiceError("malformed_response", "Docker inspect returned no container state")
        status = _safe_state(state.get("Status"))
        running = state.get("Running")
        if type(running) is not bool:
            raise ServiceError("malformed_response", "Docker inspect returned a non-boolean State.Running")
        restart_policy = _restart_policy(row)
        container_id = row.get("Id")
        if not isinstance(container_id, str) or not _CONTAINER_ID.fullmatch(container_id):
            raise ServiceError("malformed_response", "Docker inspect returned an invalid container identity")
        pid = state.get("Pid")
        return {
            "registered": True,
            "running": running,
            "enabled": restart_policy != "no",
            "pid": pid if running and isinstance(pid, int) and pid > 0 else None,
            "state": status,
            "identity": container_id,
            "manager": "docker",
        }

    def describe(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        """Describe only declared, non-secret identity and policy metadata."""
        labels = _identity_labels(binding)
        policy = _startup_policy(binding)
        return {
            "manager": "docker",
            "container": _container(binding),
            "image_id": _image_id(binding),
            "identity_labels": labels,
            "startup_policy": policy,
            "stop_policy_scope": _stop_scope(policy),
        }

    def plan(self, binding: Mapping[str, Any], action: str, observed: Mapping[str, Any]) -> list[list[str]]:
        """Plan one idempotent supervisor action using an inspected immutable ID."""
        if action not in {"up", "down", "restart", "enable", "disable"}:
            raise ServiceError("unsupported_action", "unsupported Docker service action")
        registered = observed.get("registered")
        if action == "down" and registered is False:
            return []
        if registered is False:
            raise ServiceError("missing", "declared Docker container is not registered")
        if registered is not True:
            raise ServiceError("unknown_state", "Docker registration state is not known")
        identity = observed.get("identity")
        if not isinstance(identity, str) or not _CONTAINER_ID.fullmatch(identity):
            raise ServiceError("identity_required", "Docker action requires an immutable container identity")
        running = observed.get("running")
        enabled = observed.get("enabled")
        if action in {"up", "down", "restart"} and type(running) is not bool:
            raise ServiceError("unknown_state", "Docker running state is not known")
        if action in {"enable", "disable"} and type(enabled) is not bool:
            raise ServiceError("unknown_state", "Docker startup policy is not known")
        if action == "up":
            return [] if running is True else [["docker", "start", identity]]
        if action == "down":
            return [] if running is False else [["docker", "stop", identity]]
        if action == "restart":
            return [["docker", "restart", identity]]
        if action == "enable":
            return [] if enabled is True else [["docker", "update", "--restart", _enable_policy(binding), identity]]
        return [] if enabled is False else [["docker", "update", "--restart", "no", identity]]

    def logs(self, binding: Mapping[str, Any], tail: int) -> list[str]:
        """Read a bounded number of logs after an exact identity reinspection."""
        if isinstance(tail, bool) or not isinstance(tail, int) or not 1 <= tail <= _MAX_LOG_TAIL:
            raise ServiceError("invalid_tail", "Docker log tail must be an integer from 1 through 5000")
        observed = self.inspect(binding)
        identity = observed.get("identity")
        if observed.get("registered") is not True or not isinstance(identity, str):
            raise ServiceError("missing", "declared Docker container is not registered")
        result = self._command(["docker", "logs", "--tail", str(tail), identity])
        if result is None:
            raise ServiceError("supervisor_unreachable", "Docker is unavailable while reading service logs")
        if result.returncode:
            raise ServiceError("supervisor_failed", "Docker could not read declared service logs")
        lines = (result.stdout or "").splitlines() + (result.stderr or "").splitlines()
        return [str(redact(line)) for line in lines][-tail:]

    def discover(self) -> list[dict[str, Any]]:
        """Read only containers that carry Anvil's ownership label."""
        result = self._command([
            "docker", "ps", "-a", "--no-trunc", "--filter",
            "label=%s=%s" % (_OWNERSHIP_LABEL, _OWNERSHIP_VALUE),
            "--format", "{{.ID}}",
        ])
        if result is None:
            raise ServiceError("supervisor_unreachable", "Docker is unavailable for service discovery")
        if result.returncode:
            raise ServiceError("supervisor_failed", "Docker service discovery failed")
        identities = _discovered_ids(result.stdout)
        if not identities:
            return []
        inspected = self._command(["docker", "inspect", *identities])
        if inspected is None:
            raise ServiceError("supervisor_unreachable", "Docker is unavailable for service discovery")
        if inspected.returncode:
            raise ServiceError("supervisor_failed", "Docker could not inspect discovered services")
        rows = _inspection_rows(inspected.stdout)
        records = [_discovered_record(row) for row in rows]
        records = [record for record in records if record is not None]
        return sorted(records, key=lambda record: (record["container"].casefold(), record["identity"]))

    def _command(self, argv: list[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            result = self._run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_TIMEOUT_SECONDS,
            )
            stdout, stderr = result.stdout or "", result.stderr or ""
            if (
                not isinstance(stdout, str)
                or not isinstance(stderr, str)
                or len(stdout.encode("utf-8")) + len(stderr.encode("utf-8")) > _MAX_OUTPUT_BYTES
            ):
                raise ServiceError("output_too_large", "Docker command output exceeds the safety bound")
            return result
        except subprocess.TimeoutExpired as exc:
            raise ServiceError("timeout", "Docker command timed out") from exc
        except (FileNotFoundError, OSError):
            return None


def _container(binding: Mapping[str, Any]) -> str:
    value = binding.get("container")
    if not isinstance(value, str) or not _CONTAINER_NAME.fullmatch(value):
        raise ServiceError("invalid_binding", "Docker binding requires one exact container name")
    return value


def _context_endpoint(output: str | None, *, expected_name: str | None = None) -> str:
    try:
        rows = json.loads(output or "")
    except json.JSONDecodeError as exc:
        raise ServiceError("malformed_response", "Docker context inspection returned invalid JSON") from exc
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
        raise ServiceError(
            "malformed_response", "Docker context inspection returned unexpected metadata"
        )
    row = rows[0]
    if expected_name is not None and row.get("Name") != expected_name:
        raise ServiceError(
            "malformed_response", "Docker context inspection returned a different context"
        )
    endpoints = row.get("Endpoints")
    docker = endpoints.get("docker") if isinstance(endpoints, Mapping) else None
    endpoint = docker.get("Host") if isinstance(docker, Mapping) else None
    if (
        not isinstance(endpoint, str)
        or not endpoint
        or len(endpoint) > 4096
        or _has_control_character(endpoint)
    ):
        raise ServiceError(
            "malformed_response", "Docker context inspection returned an invalid daemon endpoint"
        )
    return endpoint


def _has_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _is_local_endpoint(endpoint: str) -> bool:
    if len(endpoint) > 4096 or _has_control_character(endpoint):
        return False
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return False
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        return False
    if parsed.scheme == "unix":
        return not parsed.netloc and parsed.path.startswith("/") and parsed.path != "/"
    if parsed.scheme == "npipe" and not parsed.netloc:
        prefix = "//./pipe/"
        pipe_name = parsed.path[len(prefix):] if parsed.path.casefold().startswith(prefix) else ""
        return bool(pipe_name) and "/" not in pipe_name and "\\" not in pipe_name
    return False


def _image_id(binding: Mapping[str, Any]) -> str:
    value = binding.get("image_id")
    if not isinstance(value, str) or not _IMAGE_ID.fullmatch(value):
        raise ServiceError("invalid_binding", "Docker binding requires a pinned image ID")
    return value


def _identity_labels(binding: Mapping[str, Any]) -> dict[str, str]:
    values = binding.get("identity_labels")
    if not isinstance(values, Mapping) or not values:
        raise ServiceError("invalid_binding", "Docker binding requires identity labels")
    labels: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not isinstance(value, str) or not key or not value:
            raise ServiceError("invalid_binding", "Docker identity labels must be non-empty strings")
        labels[key] = value
    return dict(sorted(labels.items()))


def _startup_policy(binding: Mapping[str, Any]) -> str:
    value = binding.get("startup_policy", "unless-stopped")
    if value not in _VALID_STARTUP_POLICIES:
        raise ServiceError("invalid_binding", "Docker startup policy is invalid")
    return value


def _enable_policy(binding: Mapping[str, Any]) -> str:
    policy = _startup_policy(binding)
    return policy if policy in _ENABLED_STARTUP_POLICIES else "unless-stopped"


def _stop_scope(policy: str) -> str:
    if policy == "always":
        return "until an explicit docker start or a Docker daemon restart"
    return "until an explicit docker start or restart-policy change"


def _single_inspection(output: str | None) -> Mapping[str, Any]:
    rows = _inspection_rows(output)
    if len(rows) != 1:
        raise ServiceError("malformed_response", "Docker inspect returned an unexpected container count")
    return rows[0]


def _inspection_rows(output: str | None) -> list[Mapping[str, Any]]:
    try:
        rows = json.loads(output or "")
    except json.JSONDecodeError as exc:
        raise ServiceError("malformed_response", "Docker inspect returned invalid JSON") from exc
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise ServiceError("malformed_response", "Docker inspect returned invalid container metadata")
    return list(rows)


def _verify_identity(binding: Mapping[str, Any], row: Mapping[str, Any]) -> None:
    expected_container = _container(binding)
    actual_name = row.get("Name")
    if not isinstance(actual_name, str) or actual_name.lstrip("/") != expected_container:
        raise ServiceError("identity_mismatch", "Docker container name does not match the declared identity")
    if row.get("Image") != _image_id(binding):
        raise ServiceError("identity_mismatch", "Docker container image does not match the pinned image")
    config = row.get("Config")
    labels = config.get("Labels") if isinstance(config, Mapping) else None
    if not isinstance(labels, Mapping):
        raise ServiceError("identity_mismatch", "Docker container has no declared identity labels")
    for key, value in _identity_labels(binding).items():
        if labels.get(key) != value:
            raise ServiceError("identity_mismatch", "Docker container identity label does not match declaration")


def _restart_policy(row: Mapping[str, Any]) -> str:
    host_config = row.get("HostConfig")
    policy = host_config.get("RestartPolicy") if isinstance(host_config, Mapping) else None
    name = policy.get("Name") if isinstance(policy, Mapping) else ""
    return name if isinstance(name, str) and name else "no"


def _safe_state(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        return "unknown"
    return value.casefold()


def _failed_inspect(stderr: str | None) -> dict[str, Any]:
    message = (stderr or "").casefold()
    if "no such container" in message or "not found" in message:
        return {
            "registered": False, "running": False, "enabled": None, "pid": None,
            "state": "absent", "identity": None, "manager": "docker",
        }
    if "permission denied" in message or "access is denied" in message:
        return _unavailable("inaccessible")
    return _unavailable("unreachable")


def _unavailable(state: str) -> dict[str, Any]:
    return {
        "registered": None, "running": None, "enabled": None, "pid": None,
        "state": state, "identity": None, "manager": "docker",
    }


def _discovered_ids(output: str | None) -> list[str]:
    values: list[str] = []
    for line in (output or "").splitlines():
        identity = line.strip()
        if not _CONTAINER_ID.fullmatch(identity):
            raise ServiceError("malformed_response", "Docker discovery returned an invalid container identity")
        if identity not in values:
            values.append(identity)
    if len(values) > _MAX_DISCOVERED:
        raise ServiceError("discovery_limit", "Docker service discovery exceeds the container safety limit")
    return values


def _discovered_record(row: Mapping[str, Any]) -> dict[str, Any] | None:
    config = row.get("Config")
    labels = config.get("Labels") if isinstance(config, Mapping) else None
    if not isinstance(labels, Mapping) or labels.get(_OWNERSHIP_LABEL) != _OWNERSHIP_VALUE:
        return None
    identity = row.get("Id")
    name = row.get("Name")
    image_id = row.get("Image")
    state = row.get("State")
    if (
        not isinstance(identity, str) or not _CONTAINER_ID.fullmatch(identity)
        or not isinstance(name, str) or not _CONTAINER_NAME.fullmatch(name.lstrip("/"))
        or not isinstance(image_id, str) or not _IMAGE_ID.fullmatch(image_id)
        or not isinstance(state, Mapping)
    ):
        return None
    identity_labels = {
        key: value for key, value in labels.items()
        if isinstance(key, str) and isinstance(value, str) and key.startswith("io.anvil-serving.")
    }
    return {
        "manager": "docker",
        "container": name.lstrip("/"),
        "identity": identity,
        "image_id": image_id,
        "identity_labels": dict(sorted(identity_labels.items())),
        "state": _safe_state(state.get("Status")),
        "running": state.get("Running") if type(state.get("Running")) is bool else None,
    }
