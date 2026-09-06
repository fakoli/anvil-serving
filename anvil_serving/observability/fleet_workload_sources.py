"""Explicit declared-topology bindings for bounded fleet workload collection."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Mapping
from datetime import datetime

from ..control_plane.authorization import _normalize_credential
from ..topology import Host, Runtime, Topology, Transport, load_topology
from .fleet_workload_collection import normalize_node_workloads
from .fleet_workload_collector import FleetWorkloadCollector
from .probes.controller_workloads import read_controller_workloads
from .workload_collection import build_node_workloads
from .workloads import MAX_NODES, NodeResult, WorkloadError, WorkloadErrorCode, WorkloadQuery


_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z", re.ASCII)
_PATH_LIMIT = 4096
_Reader = Callable[[str, WorkloadQuery, datetime], NodeResult]


def _invalid_topology() -> ValueError:
    return ValueError("invalid workload topology")


def _host_id(value: object) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise _invalid_topology()
    try:
        WorkloadQuery(host=value)
    except WorkloadError:
        raise _invalid_topology() from None
    return value


def _exact_tuple(value: object, item_type: type, limit: int) -> tuple[object, ...]:
    if type(value) is not tuple or len(value) > limit:
        raise _invalid_topology()
    if any(type(item) is not item_type for item in value):
        raise _invalid_topology()
    return value


def _checked_topology(topology: object) -> tuple[tuple[Host, ...], dict[str, Runtime], tuple[Transport, ...]]:
    if type(topology) is not Topology:
        raise _invalid_topology()
    hosts = _exact_tuple(topology.hosts, Host, MAX_NODES)
    runtimes = _exact_tuple(topology.runtimes, Runtime, 4 * MAX_NODES)
    transports = _exact_tuple(topology.transports, Transport, 4 * MAX_NODES)
    host_ids: set[str] = set()
    for host in hosts:
        if _host_id(host.id) in host_ids:
            raise _invalid_topology()
        host_ids.add(host.id)
    runtime_by_id: dict[str, Runtime] = {}
    for runtime in runtimes:
        if (
            type(runtime.id) is not str
            or _ID_RE.fullmatch(runtime.id) is None
            or runtime.id in runtime_by_id
            or runtime.host not in host_ids
        ):
            raise _invalid_topology()
        runtime_by_id[runtime.id] = runtime
    transport_ids: set[str] = set()
    for transport in transports:
        if (
            type(transport.id) is not str
            or _ID_RE.fullmatch(transport.id) is None
            or transport.id in transport_ids
        ):
            raise _invalid_topology()
        transport_ids.add(transport.id)
    return hosts, runtime_by_id, transports


def _workload_operation(operations: object) -> bool:
    if type(operations) is not tuple:
        return False
    return any(type(name) is str and name.replace("_", "-") == "node-workloads" for name in operations)


def _candidate(host: Host, runtime_by_id: Mapping[str, Runtime], transport: Transport) -> bool:
    runtime = runtime_by_id.get(transport.runtime)
    return (
        transport.kind == "controller"
        and _workload_operation(transport.allowed_operations)
        and transport.host == host.id
        and transport.expected_node == host.id
        and runtime is not None
        and runtime.host == host.id
        and type(transport.endpoint) is str
        and bool(transport.endpoint)
        and type(transport.auth_env) is str
        and bool(transport.auth_env)
    )


def _reader(
    host: str,
    endpoint: str,
    auth_env: str,
    environment: Mapping[str, str],
    monotonic: Callable[[], object],
) -> _Reader:
    def callback(request_host: str, query: WorkloadQuery, now: datetime) -> NodeResult:
        baseline = build_node_workloads(request_host, query, now, {})
        checked_query = WorkloadQuery(
            query.owner, query.kind, query.state, query.host, query.active_only,
            query.recent_seconds, query.limit,
        )
        collected = baseline.collection_timestamp
        if request_host != host:
            return normalize_node_workloads(request_host, checked_query, collected, None)
        if checked_query.host is not None and checked_query.host != host:
            return normalize_node_workloads(host, checked_query, collected, None)
        try:
            return read_controller_workloads(
                endpoint, auth_env, host, checked_query, collected,
                environment=environment, monotonic=monotonic,
            )
        except Exception:
            return normalize_node_workloads(host, checked_query, collected, None)

    return callback


def build_fleet_workload_readers(
    topology: Topology,
    *,
    environment: Mapping[str, str] | None = None,
    monotonic: Callable[[], object] = time.monotonic,
) -> dict[str, _Reader | None]:
    """Capture the one declared controller reader, if any, for each host."""
    hosts, runtime_by_id, transports = _checked_topology(topology)
    if not callable(monotonic):
        raise _invalid_topology()
    source: Mapping[str, str] | None
    if environment is None:
        source = os.environ
    elif isinstance(environment, Mapping):
        source = environment
    else:
        source = None
    readers: dict[str, _Reader | None] = {}
    for host in hosts:
        candidates = tuple(
            transport
            for transport in transports
            if transport.kind == "controller"
            and transport.host == host.id
            and _workload_operation(transport.allowed_operations)
        )
        if len(candidates) != 1 or not _candidate(host, runtime_by_id, candidates[0]) or source is None:
            readers[host.id] = None
            continue
        transport = candidates[0]
        assert transport.auth_env is not None
        try:
            value = source.get(transport.auth_env)
        except Exception:
            readers[host.id] = None
            continue
        try:
            token = _normalize_credential(value).decode("ascii")
        except Exception:
            readers[host.id] = None
            continue
        readers[host.id] = _reader(
            host.id, transport.endpoint, transport.auth_env,
            {transport.auth_env: token}, monotonic,
        )
    return readers


def _topology_path(value: object) -> str | None:
    if type(value) is not str or not value or len(value) > _PATH_LIMIT:
        return None
    if value != value.strip() or value.startswith("~") or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    try:
        return os.path.abspath(value)
    except Exception:
        return None


def create_fleet_workload_collector(
    topology_path: str,
    *,
    environment: Mapping[str, str] | None = None,
    monotonic: Callable[[], object] = time.monotonic,
) -> FleetWorkloadCollector:
    """Load one explicit topology path and bind its declared workload readers."""
    path = _topology_path(topology_path)
    if path is None:
        raise WorkloadError(WorkloadErrorCode.UNAVAILABLE, "workload topology is unavailable")
    try:
        topology = load_topology(path)
        readers = build_fleet_workload_readers(
            topology, environment=environment, monotonic=monotonic
        )
        return FleetWorkloadCollector(readers, monotonic=monotonic)
    except Exception:
        raise WorkloadError(WorkloadErrorCode.UNAVAILABLE, "workload topology is unavailable") from None


__all__ = ["build_fleet_workload_readers", "create_fleet_workload_collector"]
