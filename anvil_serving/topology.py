"""Versioned, offline topology schema for operator commands.

This module deliberately records declared infrastructure identity only.  It
does not discover hosts, resolve DNS, read credentials, or persist mutable
runtime observations such as GPU indexes and container IDs.
"""
from __future__ import annotations

import copy
import ipaddress
import hashlib
import json
import os
import re
import socket
import tomllib
import urllib.parse
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


SCHEMA_VERSION = 1
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SECRET_VALUE_RE = re.compile(
    r"(?:\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}|\bhf_[A-Za-z0-9]{8,}|"
    r"\bgh[opsu]_[A-Za-z0-9_]{8,}|\bgithub_pat_[A-Za-z0-9_]{8,}|"
    r"\bxox[baprs]-[A-Za-z0-9-]{8,}|\b(?:AKIA|ASIA|AIDA|AROA)[0-9A-Z]{16}\b|"
    r"(?<![A-Za-z0-9/+=])(?=[A-Za-z0-9/+=]{0,39}[/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=]))"
)
_SECRET_KEYS = frozenset(
    {
        "access_key",
        "access_key_id",
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "credential",
        "credentials",
        "password",
        "private_key",
        "secret",
        "secret_access_key",
        "token",
    }
)
_SECRET_KEY_PARTS = frozenset(
    {"apikey", "authorization", "credential", "credentials", "password", "secret", "token"}
)
_SECRET_KEY_PART_SEQUENCES = tuple(
    tuple(name.split("_")) for name in sorted(_SECRET_KEYS) if "_" in name
)
_RUNTIME_STATE_KEYS = {"container_id", "device_index", "health", "reachability", "status"}
_TRANSPORT_KINDS = {"controller", "remote", "ssh"}
_AUTH_REQUIRED_TRANSPORT_KINDS = frozenset({"controller", "remote"})
_RESOURCE_WORKLOADS = frozenset({"service", "model", "llm", "stt", "tts", "experimental-model"})
_MODEL_WORKLOADS = frozenset({"model", "llm", "stt", "tts"})
_HOSTNAME_RE = re.compile(
    r"(?=.{1,254}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$"
)
_NUMERIC_DOTTED_HOST_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)+$")
_FIELD_NAME_SEPARATOR_RE = re.compile(r"[^A-Za-z0-9]+")
_CAMEL_CASE_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ACRONYM_CASE_BOUNDARY_RE = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_MODEL_WORKLOAD_MARKERS = frozenset(
    {"asr", "embedding", "embeddings", "inference", "llm", "model", "models", "reranker", "stt", "tts"}
)
_MODEL_WORKLOAD_PHRASES = (("speech", "to", "text"), ("text", "to", "speech"), ("transcription",))
_ADJACENT_MODEL_WORKLOAD_SUFFIXES = ("endpoint", "service", "server", "worker", "api")
_MODEL_WORKLOAD_MARKERS_BY_LENGTH = tuple(sorted(_MODEL_WORKLOAD_MARKERS, key=len, reverse=True))
_MODEL_WORKLOAD_VERSION_RE = re.compile(r"^v[0-9]+$")
_MODEL_WORKLOAD_VERSION_PREFIX_RE = re.compile(r"^v[0-9]+")
_GPU_UUID_RE = re.compile(r"^GPU-[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
_SSH_USERINFO_DELIMITERS = frozenset({":", ";", "@", "/", "\\", "?", "#"})
_MAX_PERCENT_DECODE_PASSES = 4
_MAX_NESTED_STRUCTURE_DEPTH = 64
_MAX_NESTED_STRUCTURE_NODES = 10_000
_MAX_CONTAINER_ITEMS = 10_000
_MAX_TOPOLOGY_FILE_BYTES = 1024 * 1024
_MAX_TOPOLOGY_SCALAR_CHARS = 64 * 1024
_SNAPSHOT_INVALID = object()
_TOPOLOGY_FIELDS = frozenset(
    {
        "schema_version",
        "id",
        "command_host",
        "command_runtime",
        "capacity_policies",
        "hosts",
        "runtimes",
        "resources",
        "gpu_roles",
        "transports",
    }
)
_CAPACITY_POLICY_FIELDS = frozenset({"id", "allow_model_workloads", "allow_experimental_model_workloads"})
_HOST_FIELDS = frozenset({"id", "roles", "address", "capacity_policy", "os"})
_HOST_OSES = frozenset({"linux", "macos", "windows"})
_RUNTIME_FIELDS = frozenset({"id", "host", "role"})
_RESOURCE_FIELDS = frozenset(
    {"id", "role", "host", "runtime", "endpoint", "endpoint_kind", "path", "gpu_role", "workload"}
)
_GPU_ROLE_FIELDS = frozenset({"id", "host", "runtime", "uuid"})
_TRANSPORT_FIELDS = frozenset(
    {
        "id",
        "kind",
        "host",
        "runtime",
        "endpoint",
        "auth_env",
        "allowed_operations",
        "host_key_fingerprint",
        "known_hosts_path",
        "allow_unauthenticated_loopback",
    }
)


@dataclass(frozen=True)
class TopologyError:
    """A validation failure tied to a TOML field path."""

    path: str
    message: str
    code: str = "invalid"


class TopologyValidationError(ValueError):
    """Raised by strict topology loaders when validation finds errors."""

    def __init__(self, errors: tuple[TopologyError, ...]):
        self.errors = errors
        super().__init__("; ".join(f"{error.path}: {error.message}" for error in errors))


class TopologyResolutionError(ValueError):
    """Raised when D004 command identity cannot be resolved deterministically."""


@dataclass(frozen=True)
class CapacityPolicy:
    id: str
    allow_model_workloads: bool = False
    allow_experimental_model_workloads: bool = False


@dataclass(frozen=True)
class Host:
    id: str
    roles: tuple[str, ...]
    address: str | None = None
    capacity_policy: str | None = None
    os: str | None = None


@dataclass(frozen=True)
class Runtime:
    id: str
    host: str
    role: str


@dataclass(frozen=True)
class Resource:
    id: str
    role: str
    host: str
    runtime: str
    endpoint: str | None = None
    endpoint_kind: str | None = None
    path: str | None = None
    gpu_role: str | None = None
    workload: str = "service"
    workload_explicit: bool = False


@dataclass(frozen=True)
class GpuRole:
    id: str
    host: str
    runtime: str
    uuid: str


@dataclass(frozen=True)
class Transport:
    id: str
    kind: str
    host: str
    runtime: str
    endpoint: str
    auth_env: str | None = None
    allowed_operations: tuple[str, ...] = ()
    host_key_fingerprint: str | None = None
    known_hosts_path: str | None = None
    allow_unauthenticated_loopback: bool = False


@dataclass(frozen=True)
class CommandIdentity:
    """The command host/runtime selected by D004 precedence."""

    host: Host
    runtime: Runtime
    host_source: str
    runtime_source: str


@dataclass(frozen=True)
class Topology:
    id: str
    schema_version: int
    hosts: tuple[Host, ...]
    runtimes: tuple[Runtime, ...]
    resources: tuple[Resource, ...]
    gpu_roles: tuple[GpuRole, ...]
    transports: tuple[Transport, ...]
    capacity_policies: tuple[CapacityPolicy, ...]
    command_host: str | None = None
    command_runtime: str | None = None

    def host(self, host_id: str) -> Host:
        return _index(self.hosts, host_id, "host")

    def runtime(self, runtime_id: str) -> Runtime:
        return _index(self.runtimes, runtime_id, "runtime")

    def resource(self, resource_id: str) -> Resource:
        return _index(self.resources, resource_id, "resource")

    def gpu_role(self, role_id: str) -> GpuRole:
        return _index(self.gpu_roles, role_id, "GPU role")

    def transport(self, transport_id: str) -> Transport:
        return _index(self.transports, transport_id, "transport")

    def capacity_policy(self, policy_id: str) -> CapacityPolicy:
        return _index(self.capacity_policies, policy_id, "capacity policy")

    def resource_owner(self, role: str) -> Resource:
        matches = tuple(resource for resource in self.resources if resource.role == role)
        if len(matches) != 1:
            raise TopologyResolutionError(
                f"resource role {role!r} has {len(matches)} declared owners; select a target explicitly"
            )
        return matches[0]


def topology_snapshot_identity(topology: Topology) -> str:
    """Return a deterministic identity for all validated topology content."""
    payload = json.dumps(
        asdict(topology),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class TopologyValidationResult:
    topology: Topology | None
    errors: tuple[TopologyError, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_topology(data: Mapping[str, Any]) -> TopologyValidationResult:
    """Validate already-parsed TOML data without network or credential access."""
    errors: list[TopologyError] = []
    if _snapshot_value_kind(data) != "mapping":
        _error(errors, "$", "topology document must be a TOML table", "type")
        return TopologyValidationResult(None, tuple(errors))

    data = _snapshot_topology_data(data, errors)
    if data is None:
        return TopologyValidationResult(None, tuple(errors))

    _reject_secrets(data, "$", errors)
    _reject_unknown_keys(data, "$", _TOPOLOGY_FIELDS, errors)
    version = _schema_version(data, errors)
    topology_id = _required_id(data, "id", "$", errors)
    command_host = _optional_reference(data, "command_host", "host", "$", errors)
    command_runtime = _optional_reference(data, "command_runtime", "runtime", "$", errors)

    policies = _parse_capacity_policies(data.get("capacity_policies", []), errors)
    hosts = _parse_hosts(data.get("hosts", []), errors)
    runtimes = _parse_runtimes(data.get("runtimes", []), errors)
    resources = _parse_resources(data.get("resources", []), errors)
    gpu_roles = _parse_gpu_roles(data.get("gpu_roles", []), errors)
    transports = _parse_transports(data.get("transports", []), errors)

    _validate_ids(policies, "capacity_policies", errors)
    _validate_ids(hosts, "hosts", errors)
    _validate_ids(runtimes, "runtimes", errors)
    _validate_ids(resources, "resources", errors)
    _validate_ids(gpu_roles, "gpu_roles", errors)
    _validate_gpu_uuids(gpu_roles, errors)
    _validate_ids(transports, "transports", errors)
    _validate_references(
        hosts,
        runtimes,
        resources,
        gpu_roles,
        transports,
        policies,
        command_host,
        command_runtime,
        errors,
    )

    if errors:
        return TopologyValidationResult(None, tuple(errors))
    assert version is not None and topology_id is not None
    return TopologyValidationResult(
        Topology(
            id=topology_id,
            schema_version=version,
            hosts=tuple(host for _, host in hosts),
            runtimes=tuple(runtime for _, runtime in runtimes),
            resources=tuple(resource for _, resource in resources),
            gpu_roles=tuple(role for _, role in gpu_roles),
            transports=tuple(transport for _, transport in transports),
            capacity_policies=tuple(policy for _, policy in policies),
            command_host=command_host,
            command_runtime=command_runtime,
        ),
        (),
    )


def parse_topology(data: Mapping[str, Any]) -> Topology:
    """Return a validated topology or raise structured validation errors."""
    result = validate_topology(data)
    if result.errors:
        raise TopologyValidationError(result.errors)
    assert result.topology is not None
    return result.topology


def _read_topology_toml(path: str | os.PathLike[str]) -> tuple[Mapping[str, Any] | None, TopologyError | None]:
    if not isinstance(path, (str, os.PathLike)):
        return None, TopologyError("$", "topology path must be a string or os.PathLike value", "read")
    try:
        filesystem_path = os.fspath(path)
        with open(filesystem_path, "rb") as handle:
            raw = handle.read(_MAX_TOPOLOGY_FILE_BYTES + 1)
        if len(raw) > _MAX_TOPOLOGY_FILE_BYTES:
            return None, TopologyError("$", "topology file exceeds the maximum byte size", "resource")
        return tomllib.loads(raw.decode("utf-8")), None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        return None, TopologyError("$", str(exc), "toml")
    except Exception as exc:
        return None, TopologyError("$", str(exc), "read")


def _merge_topology_overlay(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            merged[key] = _merge_topology_overlay(current, value)
        elif isinstance(current, list) and isinstance(value, list) and all(
            isinstance(item, Mapping) and isinstance(item.get("id"), str)
            for item in (*current, *value)
        ):
            rows = copy.deepcopy(current)
            positions = {row["id"]: index for index, row in enumerate(rows)}
            for overlay_row in value:
                row_id = overlay_row["id"]
                if row_id in positions:
                    rows[positions[row_id]] = _merge_topology_overlay(
                        rows[positions[row_id]], overlay_row
                    )
                else:
                    positions[row_id] = len(rows)
                    rows.append(copy.deepcopy(dict(overlay_row)))
            merged[key] = rows
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_topology_result(
    path: str | os.PathLike[str], overlay_path: str | os.PathLike[str] | None = None
) -> TopologyValidationResult:
    """Load TOML and validate it offline, including parse/read failures as errors."""
    data, error = _read_topology_toml(path)
    if error is not None:
        return TopologyValidationResult(None, (error,))
    assert data is not None
    if overlay_path is not None:
        overlay, overlay_error = _read_topology_toml(overlay_path)
        if overlay_error is not None:
            return TopologyValidationResult(None, (overlay_error,))
        assert overlay is not None
        overlay_errors: list[TopologyError] = []
        overlay = _snapshot_topology_data(overlay, overlay_errors)
        if overlay_errors:
            return TopologyValidationResult(None, tuple(overlay_errors))
        assert overlay is not None
        data = _merge_topology_overlay(data, overlay)
    return validate_topology(data)


def load_topology(path: str, overlay_path: str | None = None) -> Topology:
    """Strict TOML loader for commands that require a valid topology."""
    result = load_topology_result(path, overlay_path)
    if result.errors:
        raise TopologyValidationError(result.errors)
    assert result.topology is not None
    return result.topology


def resolve_command_identity(
    topology: Topology,
    *,
    command_host: str | None = None,
    command_runtime: str | None = None,
    environment: Mapping[str, str] | None = None,
    offline: bool = False,
) -> CommandIdentity | None:
    """Resolve D004 command identity without guessing from the local machine.

    Explicit command values win over environment values, which win over the
    selected topology/overlay values.  Offline commands opt out entirely.
    """
    if offline:
        return None
    environment = os.environ if environment is None else environment
    host_ref, host_source = _precedence(
        command_host,
        environment.get("ANVIL_COMMAND_HOST"),
        topology.command_host,
        "command host",
    )
    runtime_ref, runtime_source = _precedence(
        command_runtime,
        environment.get("ANVIL_COMMAND_RUNTIME"),
        topology.command_runtime,
        "command runtime",
    )
    host_id = host_ref if host_source == "topology" else _runtime_reference(host_ref, "host")
    runtime_id = runtime_ref if runtime_source == "topology" else _runtime_reference(runtime_ref, "runtime")
    try:
        host = topology.host(host_id)
        runtime = topology.runtime(runtime_id)
    except KeyError as exc:
        raise TopologyResolutionError(str(exc)) from None
    if runtime.host != host.id:
        raise TopologyResolutionError(
            f"command runtime {runtime.id!r} belongs to host {runtime.host!r}, not {host.id!r}"
        )
    return CommandIdentity(host, runtime, host_source, runtime_source)


def _parse_capacity_policies(raw: object, errors: list[TopologyError]) -> list[tuple[int, CapacityPolicy]]:
    records = _records(raw, "capacity_policies", errors)
    parsed: list[tuple[int, CapacityPolicy]] = []
    for index, record in records:
        path = f"capacity_policies[{index}]"
        _reject_unknown_keys(record, path, _CAPACITY_POLICY_FIELDS, errors)
        policy_id = _required_id(record, "id", path, errors)
        allow = _optional_bool(record, "allow_model_workloads", path, errors, False)
        experimental = _optional_bool(record, "allow_experimental_model_workloads", path, errors, False)
        if policy_id is not None:
            parsed.append((index, CapacityPolicy(policy_id, allow, experimental)))
    return parsed


def _parse_hosts(raw: object, errors: list[TopologyError]) -> list[tuple[int, Host]]:
    records = _records(raw, "hosts", errors)
    parsed: list[tuple[int, Host]] = []
    for index, record in records:
        path = f"hosts[{index}]"
        _reject_unknown_keys(record, path, _HOST_FIELDS, errors)
        host_id = _required_id(record, "id", path, errors)
        roles = _required_roles(record, "roles", path, errors)
        address = _optional_host(record, "address", path, errors)
        policy = _optional_id(record, "capacity_policy", path, errors)
        host_os = _optional_string(record, "os", path, errors)
        if host_os is not None and host_os not in _HOST_OSES:
            _error(errors, f"{path}.os", f"must be one of {sorted(_HOST_OSES)}", "value")
        if host_id is not None and roles is not None:
            parsed.append((index, Host(host_id, roles, address, policy, host_os)))
    return parsed


def _parse_runtimes(raw: object, errors: list[TopologyError]) -> list[tuple[int, Runtime]]:
    records = _records(raw, "runtimes", errors)
    parsed: list[tuple[int, Runtime]] = []
    for index, record in records:
        path = f"runtimes[{index}]"
        _reject_unknown_keys(record, path, _RUNTIME_FIELDS | _RUNTIME_STATE_KEYS, errors)
        _reject_runtime_state(record, path, errors)
        runtime_id = _required_id(record, "id", path, errors)
        host = _required_id(record, "host", path, errors)
        role = _required_id(record, "role", path, errors)
        if runtime_id is not None and host is not None and role is not None:
            parsed.append((index, Runtime(runtime_id, host, role)))
    return parsed


def _parse_resources(raw: object, errors: list[TopologyError]) -> list[tuple[int, Resource]]:
    records = _records(raw, "resources", errors)
    parsed: list[tuple[int, Resource]] = []
    for index, record in records:
        path = f"resources[{index}]"
        _reject_unknown_keys(record, path, _RESOURCE_FIELDS, errors)
        resource_id = _required_id(record, "id", path, errors)
        role = _required_id(record, "role", path, errors)
        host = _required_id(record, "host", path, errors)
        runtime = _required_id(record, "runtime", path, errors)
        endpoint = _optional_url(record, "endpoint", path, errors, {"http", "https"})
        endpoint_kind = _optional_id(record, "endpoint_kind", path, errors)
        resource_path = _optional_string(record, "path", path, errors)
        gpu_role = _optional_id(record, "gpu_role", path, errors)
        workload, workload_explicit = _resource_workload(record, resource_id, role, gpu_role, path, errors)
        if None not in (resource_id, role, host, runtime, workload):
            parsed.append(
                (
                    index,
                    Resource(
                        resource_id,
                        role,
                        host,
                        runtime,
                        endpoint,
                        endpoint_kind,
                        resource_path,
                        gpu_role,
                        workload,
                        workload_explicit,
                    ),
                )
            )
    return parsed


def _parse_gpu_roles(raw: object, errors: list[TopologyError]) -> list[tuple[int, GpuRole]]:
    records = _records(raw, "gpu_roles", errors)
    parsed: list[tuple[int, GpuRole]] = []
    for index, record in records:
        path = f"gpu_roles[{index}]"
        _reject_unknown_keys(record, path, _GPU_ROLE_FIELDS | _RUNTIME_STATE_KEYS, errors)
        _reject_runtime_state(record, path, errors)
        role_id = _required_id(record, "id", path, errors)
        host = _required_id(record, "host", path, errors)
        runtime = _required_id(record, "runtime", path, errors)
        uuid = _required_gpu_uuid(record, "uuid", path, errors)
        if None not in (role_id, host, runtime, uuid):
            parsed.append((index, GpuRole(role_id, host, runtime, uuid)))
    return parsed


def _parse_transports(raw: object, errors: list[TopologyError]) -> list[tuple[int, Transport]]:
    records = _records(raw, "transports", errors)
    parsed: list[tuple[int, Transport]] = []
    for index, record in records:
        path = f"transports[{index}]"
        _reject_unknown_keys(record, path, _TRANSPORT_FIELDS, errors)
        transport_id = _required_id(record, "id", path, errors)
        kind = _required_string(record, "kind", path, errors)
        if kind is not None and kind not in _TRANSPORT_KINDS:
            _error(errors, f"{path}.kind", f"must be one of {sorted(_TRANSPORT_KINDS)}", "value")
        host = _required_id(record, "host", path, errors)
        runtime = _required_id(record, "runtime", path, errors)
        schemes = {"http", "https"} if kind in _AUTH_REQUIRED_TRANSPORT_KINDS else {"ssh"}
        endpoint = _required_url(record, "endpoint", path, errors, schemes)
        auth_env = _optional_env(record, "auth_env", path, errors)
        allowed = _optional_roles(record, "allowed_operations", path, errors, ())
        fingerprint = _optional_string(record, "host_key_fingerprint", path, errors)
        known_hosts = _optional_string(record, "known_hosts_path", path, errors)
        if kind == "ssh":
            if fingerprint is None:
                _error(
                    errors,
                    f"{path}.host_key_fingerprint",
                    "SSH transports require a host-key fingerprint",
                    "required",
                )
            if known_hosts is None:
                _error(
                    errors,
                    f"{path}.known_hosts_path",
                    "SSH transports require a known_hosts path",
                    "required",
                )
        allow_unauthenticated_loopback = _optional_bool(
            record, "allow_unauthenticated_loopback", path, errors, False
        )
        if None not in (transport_id, kind, host, runtime, endpoint):
            parsed.append(
                (
                    index,
                    Transport(
                        transport_id,
                        kind,
                        host,
                        runtime,
                        endpoint,
                        auth_env,
                        allowed,
                        fingerprint,
                        known_hosts,
                        allow_unauthenticated_loopback,
                    ),
                )
            )
    return parsed


def _validate_references(
    hosts: list[tuple[int, Host]],
    runtimes: list[tuple[int, Runtime]],
    resources: list[tuple[int, Resource]],
    gpu_roles: list[tuple[int, GpuRole]],
    transports: list[tuple[int, Transport]],
    policies: list[tuple[int, CapacityPolicy]],
    command_host: str | None,
    command_runtime: str | None,
    errors: list[TopologyError],
) -> None:
    host_ids = {host.id for _, host in hosts}
    runtime_ids = {runtime.id for _, runtime in runtimes}
    gpu_by_id = {role.id: role for _, role in gpu_roles}
    policy_by_id = {policy.id: policy for _, policy in policies}
    policy_ids = set(policy_by_id)
    host_by_id = {host.id: host for _, host in hosts}
    runtime_by_id = {runtime.id: runtime for _, runtime in runtimes}
    resource_owners: dict[str, list[tuple[int, Resource]]] = {}
    gpu_owners: dict[str, list[Resource]] = {}
    for index, resource in resources:
        resource_owners.setdefault(resource.role, []).append((index, resource))
        if resource.gpu_role:
            gpu_owners.setdefault(resource.gpu_role, []).append(resource)
    for index, host in hosts:
        if host.capacity_policy and host.capacity_policy not in policy_ids:
            _reference_error(errors, f"hosts[{index}].capacity_policy", "capacity policy", host.capacity_policy)
    for index, runtime in runtimes:
        _check_reference(errors, f"runtimes[{index}].host", "host", runtime.host, host_ids)
    for index, resource in resources:
        _check_reference(errors, f"resources[{index}].host", "host", resource.host, host_ids)
        _check_reference(errors, f"resources[{index}].runtime", "runtime", resource.runtime, runtime_ids)
        if resource.gpu_role:
            gpu_role = gpu_by_id.get(resource.gpu_role)
            if gpu_role is None:
                _reference_error(errors, f"resources[{index}].gpu_role", "GPU role", resource.gpu_role)
            elif gpu_role.host != resource.host or gpu_role.runtime != resource.runtime:
                _error(
                    errors,
                    f"resources[{index}].gpu_role",
                    f"GPU role {gpu_role.id!r} belongs to host/runtime "
                    f"{gpu_role.host!r}/{gpu_role.runtime!r}, not {resource.host!r}/{resource.runtime!r}",
                    "reference",
                )
    for index, role in gpu_roles:
        _check_reference(errors, f"gpu_roles[{index}].host", "host", role.host, host_ids)
        _check_reference(errors, f"gpu_roles[{index}].runtime", "runtime", role.runtime, runtime_ids)
    for index, transport in transports:
        _check_reference(errors, f"transports[{index}].host", "host", transport.host, host_ids)
        _check_reference(errors, f"transports[{index}].runtime", "runtime", transport.runtime, runtime_ids)
    for index, resource in resources:
        runtime = runtime_by_id.get(resource.runtime)
        if runtime is not None and runtime.host != resource.host:
            _error(
                errors,
                f"resources[{index}].runtime",
                f"runtime {runtime.id!r} belongs to host {runtime.host!r}, not {resource.host!r}",
                "reference",
            )
    for index, resource in resources:
        host = host_by_id.get(resource.host)
        policy = policy_by_id.get(host.capacity_policy) if host is not None else None
        if policy is not None and not policy.allow_model_workloads and not resource.workload_explicit:
            _error(
                errors,
                f"resources[{index}].workload",
                f"host {host.id!r} capacity policy {policy.id!r} requires an explicit resource workload",
                "required",
            )
        if resource.workload == "experimental-model" and (
            policy is None or not policy.allow_experimental_model_workloads
        ):
            if host is None:
                policy_message = "experimental model workloads require an attached capacity policy"
            elif policy is None:
                policy_message = (
                    f"host {host.id!r} must attach a capacity policy that explicitly allows experimental model workloads"
                )
            else:
                policy_message = (
                    f"host {host.id!r} capacity policy {policy.id!r} does not allow experimental model workloads"
                )
            _error(
                errors,
                f"resources[{index}].host",
                policy_message,
                "capacity_policy",
            )
        elif resource.workload in _MODEL_WORKLOADS and host is not None and (
            policy is None or not policy.allow_model_workloads
        ):
            if policy is None:
                policy_message = f"host {host.id!r} must attach a capacity policy that explicitly allows model workloads"
            else:
                policy_message = f"host {host.id!r} capacity policy {policy.id!r} does not allow model workloads"
            _error(errors, f"resources[{index}].host", policy_message, "capacity_policy")
    for index, role in gpu_roles:
        runtime = runtime_by_id.get(role.runtime)
        if runtime is not None and runtime.host != role.host:
            _error(
                errors,
                f"gpu_roles[{index}].runtime",
                f"runtime {runtime.id!r} belongs to host {runtime.host!r}, not {role.host!r}",
                "reference",
            )
        host = host_by_id.get(role.host)
        policy = policy_by_id.get(host.capacity_policy) if host is not None else None
        owners = gpu_owners.get(role.id, [])
        experimental_assignment = (
            policy is not None
            and policy.allow_experimental_model_workloads
            and owners
            and all(resource.workload == "experimental-model" for resource in owners)
        )
        if host is not None and (policy is None or (not policy.allow_model_workloads and not experimental_assignment)):
            if policy is None:
                policy_message = (
                    f"host {host.id!r} must attach a capacity policy that explicitly allows GPU model workloads"
                )
            else:
                policy_message = f"host {host.id!r} capacity policy {policy.id!r} does not allow GPU model workloads"
            _error(
                errors,
                f"gpu_roles[{index}].host",
                policy_message,
                "capacity_policy",
            )
    for index, transport in transports:
        runtime = runtime_by_id.get(transport.runtime)
        if runtime is not None and runtime.host != transport.host:
            _error(
                errors,
                f"transports[{index}].runtime",
                f"runtime {runtime.id!r} belongs to host {runtime.host!r}, not {transport.host!r}",
                "reference",
            )
        allow_unauthenticated_loopback = _allows_unauthenticated_loopback(transport)
        if transport.allow_unauthenticated_loopback and not allow_unauthenticated_loopback:
            _error(
                errors,
                f"transports[{index}].allow_unauthenticated_loopback",
                "is only allowed for controller transports at canonical 127.0.0.1 loopback endpoints",
                "auth",
            )
        if (
            transport.kind in _AUTH_REQUIRED_TRANSPORT_KINDS
            and not transport.auth_env
            and not allow_unauthenticated_loopback
        ):
            _error(
                errors,
                f"transports[{index}].auth_env",
                f"{transport.kind} transports require an authentication environment variable",
                "required",
            )
    if command_host:
        _check_reference(errors, "command_host", "host", command_host, host_ids)
    if command_runtime:
        _check_reference(errors, "command_runtime", "runtime", command_runtime, runtime_ids)
    if command_host and command_runtime:
        runtime = runtime_by_id.get(command_runtime)
        if runtime is not None and runtime.host != command_host:
            _error(
                errors,
                "command_runtime",
                f"command runtime {runtime.id!r} belongs to host {runtime.host!r}, not {command_host!r}",
                "reference",
            )
    for role, owners in resource_owners.items():
        hosts_with_multiple = {
            resource.host
            for _, resource in owners
            if sum(1 for _, candidate in owners if candidate.host == resource.host) > 1
        }
        if hosts_with_multiple:
            for index, resource in owners:
                if resource.host not in hosts_with_multiple:
                    continue
                _error(
                    errors,
                    f"resources[{index}].role",
                    f"role {role!r} has multiple owners on host {resource.host!r}",
                    "ambiguous_owner",
                )


def _allows_unauthenticated_loopback(transport: Transport) -> bool:
    return (
        transport.kind == "controller"
        and transport.allow_unauthenticated_loopback
        and _is_canonical_loopback_url(transport.endpoint)
    )


def _is_canonical_loopback_url(value: str) -> bool:
    try:
        return urllib.parse.urlparse(value).hostname == "127.0.0.1"
    except ValueError:
        return False


def _snapshot_topology_data(data: Mapping[str, Any], errors: list[TopologyError]) -> dict[str, Any] | None:
    """Detach untrusted input into bounded built-in containers and scalars."""
    root: list[object] = [None]
    active: set[int] = set()
    copied: dict[int, object] = {}
    stack: list[tuple[str, object, str, int, dict[str, object] | list[object] | None, str | int | None]] = [
        ("visit", data, "$", 0, root, 0)
    ]
    node_count = 0
    pending_paths = 1

    while stack:
        action, current, path, depth, parent, key = stack.pop()
        if action == "leave":
            active.discard(id(current))
            continue

        pending_paths -= 1
        node_count += 1
        kind = _snapshot_value_kind(current)

        if kind not in {"mapping", "array"}:
            assert parent is not None
            scalar = _snapshot_scalar(current, path, errors)
            if scalar is _SNAPSHOT_INVALID:
                return None
            parent[key] = scalar
            continue

        identity = id(current)
        if identity in active:
            container_kind = "mappings" if kind == "mapping" else "arrays"
            _error(errors, path, f"must not contain cyclic {container_kind}", "cycle")
            continue
        if identity in copied:
            assert parent is not None
            parent[key] = copied[identity]
            continue
        if depth >= _MAX_NESTED_STRUCTURE_DEPTH:
            _error(errors, path, "nested topology data exceeds the maximum depth", "depth")
            continue

        remaining_budget = _MAX_NESTED_STRUCTURE_NODES - node_count - pending_paths
        item_budget = min(_MAX_CONTAINER_ITEMS, remaining_budget)
        if kind == "mapping":
            child_items = _snapshot_mapping_items(current, path, errors, item_budget)
            if child_items is None:
                return None
            copied_current: dict[str, object] | list[object] = {}
        else:
            child_items = _snapshot_array_items(current, path, errors, item_budget)
            if child_items is None:
                return None
            copied_current = [None] * len(child_items)

        assert parent is not None
        parent[key] = copied_current
        copied[identity] = copied_current
        active.add(identity)
        stack.append(("leave", current, path, depth, None, None))
        pending_paths += len(child_items)
        for child_key, child, child_path in reversed(child_items):
            stack.append(("visit", child, child_path, depth + 1, copied_current, child_key))

    if errors or type(root[0]) is not dict:
        return None
    return root[0]


def _snapshot_value_kind(value: object) -> str | None:
    """Classify untrusted input through ``type`` without reading ``__class__``."""
    value_type = type(value)
    if value_type is dict or issubclass(value_type, Mapping):
        return "mapping"
    if value_type is list or issubclass(value_type, list):
        return "array"
    if value_type is str or issubclass(value_type, str):
        return "string"
    if value_type is bool:
        return "bool"
    if value_type is int or issubclass(value_type, int):
        return "int"
    if value_type is float or issubclass(value_type, float):
        return "float"
    if value is None:
        return "none"
    return None


def _snapshot_scalar(value: object, path: str, errors: list[TopologyError]) -> object:
    """Copy supported scalar subclasses without invoking their override hooks."""
    kind = _snapshot_value_kind(value)
    if type(value) in {str, int, float, bool, type(None)}:
        scalar = value
        if isinstance(scalar, str) and len(scalar) > _MAX_TOPOLOGY_SCALAR_CHARS:
            _error(errors, path, "topology string exceeds the maximum length", "resource")
            return _SNAPSHOT_INVALID
        return scalar
    try:
        if kind == "string":
            scalar = str.__str__(value)
            if len(scalar) > _MAX_TOPOLOGY_SCALAR_CHARS:
                _error(errors, path, "topology string exceeds the maximum length", "resource")
                return _SNAPSHOT_INVALID
            return scalar
        if kind == "int":
            return int.__int__(value)
        if kind == "float":
            return float.__float__(value)
    except Exception:
        _error(errors, path, "could not snapshot topology scalar", "read")
        return _SNAPSHOT_INVALID
    _error(errors, path, "topology values must use built-in scalar types", "type")
    return _SNAPSHOT_INVALID


def _snapshot_mapping_key(value: object, path: str, errors: list[TopologyError]) -> str | None:
    """Require plain string keys without invoking arbitrary ``__str__`` methods."""
    if type(value) is str:
        key = value
        if len(key) > _MAX_TOPOLOGY_SCALAR_CHARS:
            _error(errors, path, "topology mapping key exceeds the maximum length", "resource")
            return None
        return key
    if _snapshot_value_kind(value) == "string":
        try:
            key = str.__str__(value)
            if len(key) > _MAX_TOPOLOGY_SCALAR_CHARS:
                _error(errors, path, "topology mapping key exceeds the maximum length", "resource")
                return None
            return key
        except Exception:
            _error(errors, path, "could not snapshot topology mapping key", "read")
            return None
    _error(errors, path, "topology mapping keys must be strings", "type")
    return None


def _snapshot_mapping_items(
    value: Mapping[str, Any], path: str, errors: list[TopologyError], item_budget: int
) -> list[tuple[str, object, str]] | None:
    """Read an untrusted mapping once and normalize its keys for later validation."""
    try:
        iterator = iter(value.items())
    except Exception:
        _error(errors, path, "could not read topology mapping", "read")
        return None

    items: list[tuple[str, object, str]] = []
    keys: set[str] = set()
    try:
        for count, item in enumerate(iterator, start=1):
            if count > item_budget:
                _error(errors, path, "nested topology data exceeds the resource limit", "resource")
                return None
            key, child = item
            key_text = _snapshot_mapping_key(key, path, errors)
            if key_text is None:
                return None
            child_path = _field_path(path, key_text)
            if key_text in keys:
                _error(errors, child_path, "topology mapping keys must normalize uniquely", "read")
                return None
            keys.add(key_text)
            items.append((key_text, child, child_path))
    except Exception:
        _error(errors, path, "could not read topology mapping", "read")
        return None
    return items


def _snapshot_array_items(
    value: list[object], path: str, errors: list[TopologyError], item_budget: int
) -> list[tuple[int, object, str]] | None:
    """Read an untrusted array once before traversing its detached contents."""
    try:
        iterator = iter(value)
    except Exception:
        _error(errors, path, "could not read topology array", "read")
        return None

    items: list[tuple[int, object, str]] = []
    try:
        for index, child in enumerate(iterator):
            if index + 1 > item_budget:
                _error(errors, path, "nested topology data exceeds the resource limit", "resource")
                return None
            items.append((index, child, f"{path}[{index}]"))
    except Exception:
        _error(errors, path, "could not read topology array", "read")
        return None
    return items


def _schema_version(data: Mapping[str, Any], errors: list[TopologyError]) -> int | None:
    value = data.get("schema_version")
    if isinstance(value, bool) or not isinstance(value, int):
        _error(errors, "schema_version", "must be an integer", "type")
        return None
    if value != SCHEMA_VERSION:
        _error(errors, "schema_version", f"unknown schema version {value!r}", "version")
        return None
    return value


def _records(raw: object, path: str, errors: list[TopologyError]) -> list[tuple[int, Mapping[str, Any]]]:
    if not isinstance(raw, list):
        _error(errors, path, "must be an array of TOML tables", "type")
        return []
    records: list[tuple[int, Mapping[str, Any]]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            _error(errors, f"{path}[{index}]", "must be a TOML table", "type")
        else:
            records.append((index, item))
    return records


def _required_id(record: Mapping[str, Any], key: str, path: str, errors: list[TopologyError]) -> str | None:
    value = record.get(key)
    field = f"{path}.{key}" if path != "$" else key
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        _error(errors, field, "must be a non-empty identifier", "type")
        return None
    return value


def _optional_id(record: Mapping[str, Any], key: str, path: str, errors: list[TopologyError]) -> str | None:
    if key not in record:
        return None
    return _required_id(record, key, path, errors)


def _required_string(record: Mapping[str, Any], key: str, path: str, errors: list[TopologyError]) -> str | None:
    value = record.get(key)
    field = _field_path(path, key)
    if not isinstance(value, str) or not value:
        _error(errors, field, "must be a non-empty string", "type")
        return None
    return value


def _required_gpu_uuid(record: Mapping[str, Any], key: str, path: str, errors: list[TopologyError]) -> str | None:
    value = _required_string(record, key, path, errors)
    if value is not None and (_has_gpu_uuid_whitespace_or_controls(value) or not _GPU_UUID_RE.fullmatch(value)):
        _error(
            errors,
            _field_path(path, key),
            "must use the NVIDIA GPU- UUID form with hexadecimal 8-4-4-4-12 groups",
            "value",
        )
        return None
    return _canonical_gpu_uuid(value) if value is not None else None


def _optional_string(record: Mapping[str, Any], key: str, path: str, errors: list[TopologyError]) -> str | None:
    if key not in record:
        return None
    return _required_string(record, key, path, errors)


def _optional_bool(
    record: Mapping[str, Any], key: str, path: str, errors: list[TopologyError], default: bool
) -> bool:
    value = record.get(key, default)
    if not isinstance(value, bool):
        _error(errors, _field_path(path, key), "must be true or false", "type")
        return default
    return value


def _required_roles(
    record: Mapping[str, Any], key: str, path: str, errors: list[TopologyError]
) -> tuple[str, ...] | None:
    if key not in record:
        _error(errors, _field_path(path, key), "is required", "required")
        return None
    return _optional_roles(record, key, path, errors, None)


def _optional_roles(
    record: Mapping[str, Any],
    key: str,
    path: str,
    errors: list[TopologyError],
    default: tuple[str, ...] | None,
) -> tuple[str, ...]:
    if key not in record:
        return () if default is None else default
    value = record[key]
    if not isinstance(value, list) or any(not isinstance(item, str) or not _ID_RE.fullmatch(item) for item in value):
        _error(errors, _field_path(path, key), "must be an array of identifiers", "type")
        return ()
    if len(set(value)) != len(value):
        _error(errors, _field_path(path, key), "must not contain duplicate roles", "duplicate")
    return tuple(value)


def _optional_host(record: Mapping[str, Any], key: str, path: str, errors: list[TopologyError]) -> str | None:
    value = _optional_string(record, key, path, errors)
    if value is not None:
        _validate_host(value, f"{path}.{key}", errors)
    return value


def _optional_reference(
    record: Mapping[str, Any], key: str, prefix: str, path: str, errors: list[TopologyError]
) -> str | None:
    value = _optional_string(record, key, path, errors)
    if value is None:
        return None
    expected = f"{prefix}:"
    if not value.startswith(expected) or not _ID_RE.fullmatch(value[len(expected) :]):
        _error(errors, f"{path}.{key}" if path != "$" else key, f"must be a {expected}<id> reference", "reference")
        return None
    return value[len(expected) :]


def _optional_env(record: Mapping[str, Any], key: str, path: str, errors: list[TopologyError]) -> str | None:
    value = _optional_string(record, key, path, errors)
    if value is not None and not _ENV_RE.fullmatch(value):
        _error(errors, f"{path}.{key}", "must name an environment variable", "type")
        return None
    return value


def _resource_workload(
    record: Mapping[str, Any],
    resource_id: str | None,
    role: str | None,
    gpu_role: str | None,
    path: str,
    errors: list[TopologyError],
) -> tuple[str | None, bool]:
    inferred = _infer_resource_workload(resource_id, role, gpu_role)
    if "workload" not in record:
        return inferred, False
    workload = _required_string(record, "workload", path, errors)
    if workload is not None and workload not in _RESOURCE_WORKLOADS:
        _error(errors, f"{path}.workload", f"must be one of {sorted(_RESOURCE_WORKLOADS)}", "value")
        return None, True
    if workload == "service" and inferred in _MODEL_WORKLOADS:
        _error(
            errors,
            f"{path}.workload",
            "explicit service workload conflicts with model-signaling resource identity",
            "conflict",
        )
        return inferred, True
    return workload, True


def _infer_resource_workload(resource_id: str | None, role: str | None, gpu_role: str | None) -> str:
    if gpu_role or any(_looks_like_model_workload(value) for value in (resource_id, role) if value is not None):
        return "model"
    return "service"


def _looks_like_model_workload(value: str) -> bool:
    parts = _tokenize_workload_identity(value)
    if "proxy" in parts:
        return False
    return bool(set(parts) & _MODEL_WORKLOAD_MARKERS) or any(
        _contains_token_phrase(parts, phrase) for phrase in _MODEL_WORKLOAD_PHRASES
    )


def _tokenize_workload_identity(value: str) -> tuple[str, ...]:
    """Split normal identifier boundaries plus bounded adjacent model signals.

    Only a known model marker joined to an exact operational suffix or a
    ``v<digits>`` version is split.  This avoids substring inference for
    ordinary service names while recognizing compact names such as ``LLMAPI``.
    """
    tokens: list[str] = []
    for token in _normalize_field_name(value).split("_"):
        tokens.extend(_split_adjacent_model_workload_token(token))
    for raw_token in _FIELD_NAME_SEPARATOR_RE.split(value):
        marker = _adjacent_model_workload_marker(raw_token)
        if marker is not None:
            tokens.append(marker)
    return tuple(tokens)


def _split_adjacent_model_workload_token(token: str) -> tuple[str, ...]:
    for marker in _MODEL_WORKLOAD_MARKERS_BY_LENGTH:
        if not token.startswith(marker):
            continue
        suffixes = _adjacent_model_workload_suffixes(token[len(marker) :])
        if suffixes is not None:
            return (marker, *suffixes)
    return (token,)


def _adjacent_model_workload_marker(token: str) -> str | None:
    """Recognize a compact marker without trusting arbitrary marker prefixes."""
    token_casefolded = token.casefold()
    for marker in _MODEL_WORKLOAD_MARKERS_BY_LENGTH:
        if not token_casefolded.startswith(marker):
            continue
        if _adjacent_model_workload_suffixes(token_casefolded[len(marker) :]) is not None:
            return marker
    return None


def _adjacent_model_workload_suffixes(value: str) -> tuple[str, ...] | None:
    """Split a version plus bounded exact operational suffixes, or suffixes alone."""
    if not value:
        return None
    if _MODEL_WORKLOAD_VERSION_RE.fullmatch(value):
        return (value,)
    version = _MODEL_WORKLOAD_VERSION_PREFIX_RE.match(value)
    if version is not None:
        suffixes = _adjacent_model_workload_suffix_chain(value, version.end())
        return (version.group(), *suffixes) if suffixes is not None else None
    return _adjacent_model_workload_suffix_chain(value)


def _adjacent_model_workload_suffix_chain(value: str, start: int = 0) -> tuple[str, ...] | None:
    """Split an exact chain of operational suffixes without substring inference."""
    suffixes: list[str] = []
    position = start
    while position < len(value):
        suffix = next(
            (item for item in _ADJACENT_MODEL_WORKLOAD_SUFFIXES if value.startswith(item, position)), None
        )
        if suffix is None:
            return None
        suffixes.append(suffix)
        position += len(suffix)
    return tuple(suffixes) if suffixes else None


def _contains_token_phrase(tokens: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    return any(tokens[index : index + len(phrase)] == phrase for index in range(len(tokens) - len(phrase) + 1))


def _required_url(
    record: Mapping[str, Any], key: str, path: str, errors: list[TopologyError], schemes: set[str]
) -> str | None:
    value = _required_string(record, key, path, errors)
    if value is not None:
        _validate_url(value, f"{path}.{key}", schemes, errors)
    return value


def _optional_url(
    record: Mapping[str, Any], key: str, path: str, errors: list[TopologyError], schemes: set[str]
) -> str | None:
    value = _optional_string(record, key, path, errors)
    if value is not None:
        _validate_url(value, f"{path}.{key}", schemes, errors)
    return value


def _validate_url(value: str, path: str, schemes: set[str], errors: list[TopologyError]) -> None:
    if _has_raw_url_control_characters(value):
        _error(errors, path, f"must be a {sorted(schemes)} URL without control characters", "url")
        return
    if any(character.isspace() for character in value):
        _error(errors, path, f"must be a {sorted(schemes)} URL without whitespace", "url")
        return
    try:
        parsed = urllib.parse.urlparse(value)
    except ValueError:
        _error(errors, path, f"must be a {sorted(schemes)} URL", "url")
        return
    invalid_ssh_userinfo = parsed.scheme == "ssh" and _has_invalid_ssh_userinfo(parsed.netloc)
    if invalid_ssh_userinfo or _has_url_authority_credentials(parsed):
        _error(errors, path, "must not embed credentials, query strings, or fragments", "credential")
        return
    if parsed.query or parsed.fragment or "?" in value or "#" in value:
        _error(errors, path, "must not embed credentials, query strings, or fragments", "credential")
        return
    try:
        hostname = parsed.hostname
    except ValueError:
        _error(errors, path, f"must be a {sorted(schemes)} URL", "url")
        return
    if parsed.scheme not in schemes or not parsed.netloc or not hostname:
        _error(errors, path, f"must be a {sorted(schemes)} URL", "url")
        return
    decoded_authority = _decode_percent_encoded_layers(parsed.netloc)
    if decoded_authority is None:
        _error(errors, path, "must use valid percent encoding within the decode limit", "url")
        return
    if any("\\" in candidate or _has_url_control_characters(candidate) for candidate in decoded_authority):
        _error(errors, path, f"must be a {sorted(schemes)} URL without encoded controls or backslashes", "url")
        return
    if parsed.netloc.endswith(":"):
        _error(errors, path, "port must be an integer from 1 to 65535", "port")
        return
    try:
        port = parsed.port
    except ValueError:
        _error(errors, path, "port must be an integer from 1 to 65535", "port")
        return
    if port is not None and not 1 <= port <= 65535:
        _error(errors, path, "port must be an integer from 1 to 65535", "port")
    endpoint_path, parameters = _split_url_path_parameters(parsed.path, parsed.params)
    decoded_path = _validate_url_path(endpoint_path, path, errors)
    if decoded_path is None:
        return
    for parameter in parameters:
        if _validate_url_path(parameter, path, errors) is None:
            return
    _validate_url_parameters(parameters, path, errors)
    for decoded_candidate in decoded_path:
        _, decoded_parameters = _split_url_path_parameters(decoded_candidate, "")
        _validate_url_parameters(decoded_parameters, path, errors)
    _validate_host(hostname, path, errors)


def _has_url_authority_credentials(parsed: urllib.parse.ParseResult) -> bool:
    return parsed.username == "" or parsed.password is not None or (
        parsed.username is not None and parsed.scheme != "ssh"
    )


def _split_url_path_parameters(path: str, parsed_params: str) -> tuple[str, tuple[str, ...]]:
    """Separate raw semicolon components before decoding path data."""
    endpoint_path, delimiter, inline_parameters = path.partition(";")
    parameters = inline_parameters.split(";") if delimiter else []
    if parsed_params:
        parameters.extend(parsed_params.split(";"))
    return endpoint_path, tuple(parameters)


def _validate_url_path(value: str, path: str, errors: list[TopologyError]) -> tuple[str, ...] | None:
    decoded_layers = _decode_percent_encoded_layers(value)
    if decoded_layers is None:
        _error(errors, path, "path must contain valid percent encoding within the decode limit", "url")
        return None
    if any("\\" in candidate for candidate in decoded_layers):
        _error(errors, path, "path must not contain backslashes", "url")
        return None
    if any(_has_url_control_characters(candidate) for candidate in decoded_layers):
        _error(errors, path, "path must not contain control characters", "url")
        return None
    return decoded_layers


def _validate_url_parameters(parameters: tuple[str, ...], path: str, errors: list[TopologyError]) -> None:
    for parameter in parameters:
        decoded_parameters = _decode_percent_encoded_layers(parameter)
        if decoded_parameters is None:
            continue
        for decoded_parameter in decoded_parameters:
            for segment in decoded_parameter.split(";"):
                name, _, _ = segment.partition("=")
                if _is_credential_parameter_name(name):
                    _error(errors, path, "must not embed credential parameters", "credential")


def _has_invalid_ssh_userinfo(netloc: str) -> bool:
    if "@" not in netloc:
        return False
    if netloc.count("@") != 1:
        return True
    userinfo, _, _ = netloc.partition("@")
    if not userinfo:
        return True
    decoded_layers = _decode_percent_encoded_layers(userinfo)
    if decoded_layers is None:
        return True
    return any(
        not decoded
        or any(character in _SSH_USERINFO_DELIMITERS for character in decoded)
        or _has_url_control_characters(decoded)
        or any(unicodedata.category(character) == "Cf" for character in decoded)
        for decoded in decoded_layers
    )


def _decode_percent_encoded_text(value: str) -> str | None:
    try:
        return urllib.parse.unquote_to_bytes(value).decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None


def _decode_percent_encoded_layers(value: str) -> tuple[str, ...] | None:
    """Decode nested percent encoding to a fixed point, rejecting over-budget input."""
    if _has_invalid_percent_escape(value):
        return None
    layers = [value]
    current = value
    for _ in range(_MAX_PERCENT_DECODE_PASSES):
        decoded = _decode_percent_encoded_text(current)
        if decoded is None:
            return None
        if decoded == current:
            return tuple(layers)
        layers.append(decoded)
        current = decoded
        if not _has_percent_escape(current):
            return tuple(layers)
    return None


def _has_percent_escape(value: str) -> bool:
    return any(character == "%" and _is_hex_pair(value[index + 1 : index + 3]) for index, character in enumerate(value))


def _has_raw_url_control_characters(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _has_url_control_characters(value: str) -> bool:
    return any(ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in value)


def _has_invalid_percent_escape(value: str) -> bool:
    return any(
        character == "%" and (index + 2 >= len(value) or not _is_hex_pair(value[index + 1 : index + 3]))
        for index, character in enumerate(value)
    )


def _is_hex_pair(value: str) -> bool:
    return len(value) == 2 and all(character in "0123456789abcdefABCDEF" for character in value)


def _validate_host(value: str, path: str, errors: list[TopologyError]) -> None:
    if not value or any(character.isspace() for character in value):
        _error(errors, path, "must be a valid hostname or IP address without whitespace", "host")
        return
    if value.rstrip(".").lower() == "localhost":
        _error(errors, path, "must use 127.0.0.1, not localhost", "localhost")
        return
    if value.endswith("."):
        try:
            address = ipaddress.ip_address(value.rstrip("."))
        except ValueError:
            _error(errors, path, "must not use a trailing-dot host alias", "host")
        else:
            mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
            if address.is_unspecified or (mapped is not None and mapped.is_unspecified):
                _error(errors, path, "must not use an unspecified wildcard host address", "host")
            elif address.is_loopback or (mapped is not None and mapped.is_loopback):
                _error(errors, path, "must use canonical 127.0.0.1 for loopback", "localhost")
            else:
                _error(errors, path, "must use a canonical IP address", "host")
            return
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        if (
            _is_uri_like_host(value)
            or _is_noncanonical_ipv4(value)
            or _is_numeric_dotted_host(value)
            or not _HOSTNAME_RE.fullmatch(value)
        ):
            _error(errors, path, "must be a valid hostname or canonical IP address", "host")
        return
    mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    if address.is_unspecified or (mapped is not None and mapped.is_unspecified):
        _error(errors, path, "must not use an unspecified wildcard host address", "host")
    elif (address.is_loopback or (mapped is not None and mapped.is_loopback)) and value != "127.0.0.1":
        _error(errors, path, "must use canonical 127.0.0.1 for loopback", "localhost")
    elif value != str(address):
        _error(errors, path, "must use a canonical IP address", "host")


def _is_uri_like_host(value: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return True
    return bool(parsed.scheme or parsed.netloc or parsed.query or parsed.fragment)


def _is_noncanonical_ipv4(value: str) -> bool:
    try:
        socket.inet_aton(value)
    except (OSError, ValueError, UnicodeError):
        return False
    return True


def _is_numeric_dotted_host(value: str) -> bool:
    return bool(_NUMERIC_DOTTED_HOST_RE.fullmatch(value))


def _reject_secrets(
    value: object, path: str, errors: list[TopologyError]
) -> None:
    active: set[int] = set()
    visited: set[int] = set()
    stack: list[tuple[str, object, str, int]] = [("visit", value, path, 0)]
    node_count = 0
    while stack:
        action, current, current_path, depth = stack.pop()
        if action == "leave":
            active.remove(id(current))
            continue
        if isinstance(current, Mapping) or isinstance(current, list):
            identity = id(current)
            if identity in active:
                kind = "mappings" if isinstance(current, Mapping) else "arrays"
                _error(errors, current_path, f"must not contain cyclic {kind}", "cycle")
                continue
            if identity in visited:
                continue
            if depth >= _MAX_NESTED_STRUCTURE_DEPTH:
                _error(errors, current_path, "nested topology data exceeds the maximum depth", "depth")
                continue
            if node_count >= _MAX_NESTED_STRUCTURE_NODES:
                _error(errors, current_path, "nested topology data exceeds the resource limit", "resource")
                return
            node_count += 1
            visited.add(identity)
            active.add(identity)
            stack.append(("leave", current, current_path, depth))
            if isinstance(current, Mapping):
                children = list(current.items())
                for key, child in reversed(children):
                    child_path = _field_path(current_path, key)
                    if _is_secret_key(str(key)):
                        _error(errors, child_path, "credentials must be environment-variable references", "credential")
                    stack.append(("visit", child, child_path, depth + 1))
            else:
                for index in range(len(current) - 1, -1, -1):
                    stack.append(("visit", current[index], f"{current_path}[{index}]", depth + 1))
        elif isinstance(current, str) and _SECRET_VALUE_RE.search(current):
            _error(errors, current_path, "looks like a credential value", "credential")


def _is_secret_key(key: str) -> bool:
    return _is_credential_key(key, allow_environment_reference=True)


def _is_credential_parameter_name(key: str) -> bool:
    """Recognize sensitive URL parameter names; ``*_env`` is not safe in a URL."""
    return _is_credential_key(key, allow_environment_reference=False)


def _is_credential_key(key: str, *, allow_environment_reference: bool) -> bool:
    normalized = _normalize_credential_key(key)
    if allow_environment_reference and normalized.endswith("_env"):
        return False
    parts = tuple(part for part in normalized.split("_") if part)
    return bool(set(parts) & _SECRET_KEY_PARTS) or normalized in _SECRET_KEYS or any(
        _contains_credential_part_sequence(parts, sequence) for sequence in _SECRET_KEY_PART_SEQUENCES
    )


def _contains_credential_part_sequence(parts: tuple[str, ...], sequence: tuple[str, ...]) -> bool:
    """Match compound credential names at normalized field boundaries only."""
    return any(parts[index : index + len(sequence)] == sequence for index in range(len(parts) - len(sequence) + 1))


def _normalize_credential_key(value: str) -> str:
    """Fold credential-name ignorables without accepting renamed schema fields.

    Schema-key matching keeps the original spelling. Credential matching
    first applies NFKC, then removes C0/C1 controls, format controls, the
    combining grapheme joiner, and Unicode variation selectors. Other
    combining marks remain significant. This catches compatibility-form and
    invisible credential-name smuggling without normalizing unrelated
    percent-encoded URL path text.
    """
    compatibility_value = unicodedata.normalize("NFKC", value)
    visible_value = "".join(
        character for character in compatibility_value if not _is_credential_ignorable(character)
    )
    return _normalize_field_name(visible_value)


def _is_credential_ignorable(character: str) -> bool:
    """Return whether a character is ignored only for credential-name matching."""
    codepoint = ord(character)
    return (
        _is_control_character(character)
        or unicodedata.category(character) == "Cf"
        or codepoint == 0x034F
        or 0x180B <= codepoint <= 0x180D
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
    )


def _normalize_field_name(value: str) -> str:
    camel_case = _CAMEL_CASE_BOUNDARY_RE.sub("_", value)
    acronym_aware = _ACRONYM_CASE_BOUNDARY_RE.sub("_", camel_case)
    return _FIELD_NAME_SEPARATOR_RE.sub("_", acronym_aware).strip("_").lower()


def _reject_unknown_keys(
    record: Mapping[str, Any], path: str, allowed: frozenset[str], errors: list[TopologyError]
) -> None:
    for key in record:
        if key in allowed:
            continue
        field = _field_path(path, key)
        if _is_secret_key(str(key).lower()):
            continue
        else:
            _error(errors, field, "unknown schema field", "unknown")


def _field_path(path: str, key: object) -> str:
    key_text = str(key)
    if _ID_RE.fullmatch(key_text):
        return key_text if path == "$" else f"{path}.{key_text}"
    quoted_key = json.dumps(key_text, ensure_ascii=True)
    return f"[{quoted_key}]" if path == "$" else f"{path}[{quoted_key}]"


def _reject_runtime_state(record: Mapping[str, Any], path: str, errors: list[TopologyError]) -> None:
    for key in record:
        if str(key).lower() in _RUNTIME_STATE_KEYS:
            _error(
                errors,
                _field_path(path, key),
                "runtime observations are not stable topology identity",
                "runtime_state",
            )


def _validate_ids(items: list[tuple[int, Any]], path: str, errors: list[TopologyError]) -> None:
    seen: set[str] = set()
    for index, item in items:
        if item.id in seen:
            _error(errors, f"{path}[{index}].id", f"duplicate id {item.id!r}", "duplicate")
        seen.add(item.id)


def _validate_gpu_uuids(items: list[tuple[int, GpuRole]], errors: list[TopologyError]) -> None:
    seen: set[str] = set()
    for index, role in items:
        canonical_uuid = _canonical_gpu_uuid(role.uuid)
        if canonical_uuid in seen:
            _error(errors, f"gpu_roles[{index}].uuid", f"duplicate GPU UUID {role.uuid!r}", "duplicate")
        seen.add(canonical_uuid)


def _has_gpu_uuid_whitespace_or_controls(value: str) -> bool:
    return any(character.isspace() or _is_control_character(character) or unicodedata.category(character) == "Cf" for character in value)


def _canonical_gpu_uuid(value: str) -> str:
    return f"GPU-{value[4:].lower()}"


def _is_control_character(value: str) -> bool:
    return ord(value) < 0x20 or 0x7F <= ord(value) <= 0x9F


def _check_reference(
    errors: list[TopologyError], path: str, kind: str, value: str, valid_values: set[str]
) -> None:
    if value not in valid_values:
        _reference_error(errors, path, kind, value)


def _reference_error(errors: list[TopologyError], path: str, kind: str, value: str) -> None:
    _error(errors, path, f"unknown {kind} reference {value!r}", "reference")


def _error(errors: list[TopologyError], path: str, message: str, code: str) -> None:
    errors.append(TopologyError(path, message, code))


def _index(items: tuple[Any, ...], item_id: str, kind: str) -> Any:
    for item in items:
        if item.id == item_id:
            return item
    raise KeyError(f"unknown {kind} id: {item_id!r}")


def _precedence(
    explicit: str | None, environment: str | None, topology_value: str | None, label: str
) -> tuple[str, str]:
    for value, source in ((explicit, "explicit"), (environment, "environment"), (topology_value, "topology")):
        if value:
            return value, source
    raise TopologyResolutionError(f"{label} is required; set an explicit value, environment variable, or topology default")


def _runtime_reference(value: str, prefix: str) -> str:
    expected = f"{prefix}:"
    if not value.startswith(expected) or not _ID_RE.fullmatch(value[len(expected) :]):
        raise TopologyResolutionError(f"{prefix} reference must use {expected}<id>, got {value!r}")
    return value[len(expected) :]
