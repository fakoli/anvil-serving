"""Explicit, read-only workload source bindings for one controller node."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
import os
import re

from ...manifest_workloads import list_manifest_workloads
from ...media.jobs import read_media_workloads
from ...observability.probes.router_workloads import read_router_workloads
from ...observability.workload_collection import build_node_workloads
from ...observability.workloads import (
    ResultStatus,
    SourceResult,
    Truncation,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadKind,
    WorkloadOwner,
    WorkloadQuery,
    workload_id,
)
from ...paths import LOOPBACK_ALIAS_ENV
from ...serve_recipes import list_recipe_workloads
from ...topology import load_topology
from .store import OperationStore, read_benchmark_workloads


_PATH_LIMIT = 4096
_RESOURCE_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z", re.ASCII)
_ENV_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,255}\Z", re.ASCII)

_OWNER_KINDS = {
    WorkloadOwner.CONTROLLER: WorkloadKind.CONTROLLER_OPERATION,
    WorkloadOwner.BENCHMARK: WorkloadKind.BENCHMARK_JOB,
    WorkloadOwner.MEDIA: WorkloadKind.MEDIA_JOB,
    WorkloadOwner.RECIPE: WorkloadKind.RECIPE_SERVE,
    WorkloadOwner.MANIFEST: WorkloadKind.RECIPE_SERVE,
    WorkloadOwner.ROUTER: WorkloadKind.ROUTER_REQUEST,
}


Reader = Callable[[str, WorkloadQuery, datetime], SourceResult]


def _fixed(owner: WorkloadOwner, now: datetime, status: ResultStatus) -> SourceResult:
    if status is ResultStatus.COMPLETE:
        return SourceResult(owner, status, now, (), Truncation(0, 0))
    return SourceResult(
        owner,
        ResultStatus.UNAVAILABLE,
        now,
        (),
        Truncation(0, None),
        WorkloadErrorCode.UNAVAILABLE,
    )


def _captured_path(value: object) -> str | None:
    if type(value) is not str or not value or len(value) > _PATH_LIMIT:
        return None
    if value != value.strip() or value.startswith("~") or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    try:
        return os.path.abspath(value)
    except Exception:
        return None


def _validate_host(host: object) -> str:
    if type(host) is not str:
        raise ValueError("invalid workload host")
    try:
        workload_id(host, WorkloadKind.ROUTER_REQUEST, WorkloadOwner.ROUTER, "validation")
    except WorkloadError:
        raise ValueError("invalid workload host") from None
    return host


def _baseline(host: object, query: object, now: object):
    return build_node_workloads(host, query, now, {})


def _excluded(owner: WorkloadOwner, host: str, query: WorkloadQuery) -> bool:
    return (
        (query.owner is not None and query.owner is not owner)
        or (query.kind is not None and query.kind is not _OWNER_KINDS[owner])
        or (query.host is not None and query.host != host)
    )


def _bound_reader(owner: WorkloadOwner, host: str, reader: Callable[..., SourceResult], *args) -> Reader:
    def callback(request_host: str, query: WorkloadQuery, now: datetime) -> SourceResult:
        baseline = _baseline(request_host, query, now)
        collected = baseline.collection_timestamp
        if request_host != host:
            return _fixed(owner, collected, ResultStatus.UNAVAILABLE)
        if _excluded(owner, host, query):
            return _fixed(owner, collected, ResultStatus.COMPLETE)
        try:
            return reader(*args, host, query, collected)
        except Exception:
            return _fixed(owner, collected, ResultStatus.UNAVAILABLE)

    return callback


def _router_reader(
    host: str,
    topology_path: str,
    resource_id: str,
    auth_env: str,
    environment: Mapping[str, object],
) -> Reader:
    def callback(request_host: str, query: WorkloadQuery, now: datetime) -> SourceResult:
        baseline = _baseline(request_host, query, now)
        collected = baseline.collection_timestamp
        if request_host != host:
            return _fixed(WorkloadOwner.ROUTER, collected, ResultStatus.UNAVAILABLE)
        if _excluded(WorkloadOwner.ROUTER, host, query):
            return _fixed(WorkloadOwner.ROUTER, collected, ResultStatus.COMPLETE)
        try:
            topology = load_topology(topology_path)
            resource = topology.resource(resource_id)
            runtime = topology.runtime(resource.runtime)
            if (
                resource.role != "router-workloads"
                or resource.endpoint_kind != "workloads-v1"
                or resource.host != host
                or resource.workload != "service"
                or runtime.host != host
                or type(resource.endpoint) is not str
            ):
                return _fixed(WorkloadOwner.ROUTER, collected, ResultStatus.UNAVAILABLE)
            return read_router_workloads(
                resource.endpoint,
                auth_env,
                host,
                query,
                collected,
                environment=environment,
            )
        except Exception:
            return _fixed(WorkloadOwner.ROUTER, collected, ResultStatus.UNAVAILABLE)

    return callback


def _router_binding(
    host: str,
    topology: object,
    resource: object,
    auth_env: object,
    environment: Mapping[str, str] | None,
) -> Reader | None:
    topology_path = _captured_path(topology)
    if (
        topology_path is None
        or type(resource) is not str
        or _RESOURCE_ID_RE.fullmatch(resource) is None
        or type(auth_env) is not str
        or _ENV_RE.fullmatch(auth_env) is None
    ):
        return None
    source = os.environ if environment is None else environment
    if not isinstance(source, Mapping):
        return None
    try:
        captured = {
            auth_env: source.get(auth_env),
            LOOPBACK_ALIAS_ENV: source.get(LOOPBACK_ALIAS_ENV),
        }
    except Exception:
        return None
    return _router_reader(host, topology_path, resource, auth_env, captured)


def build_workload_readers(
    host: str,
    operation_store: OperationStore | None,
    *,
    benchmark_db: str | None = None,
    media_db: str | None = None,
    recipe_registry: str | None = None,
    manifest: str | None = None,
    router_topology: str | None = None,
    router_resource: str | None = None,
    router_auth_env: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[WorkloadOwner, Reader | None]:
    """Bind only explicit source owners; construction performs no source I/O."""
    configured_host = _validate_host(host)
    if operation_store is not None and type(operation_store) is not OperationStore:
        raise ValueError("invalid operation store")
    benchmark_path = _captured_path(benchmark_db)
    media_path = _captured_path(media_db)
    recipe_path = _captured_path(recipe_registry)
    manifest_path = _captured_path(manifest)
    readers: dict[WorkloadOwner, Reader | None] = {
        WorkloadOwner.CONTROLLER: (
            _bound_reader(WorkloadOwner.CONTROLLER, configured_host, operation_store.list_workloads)
            if operation_store is not None else None
        ),
        WorkloadOwner.BENCHMARK: (
            _bound_reader(WorkloadOwner.BENCHMARK, configured_host, read_benchmark_workloads, benchmark_path)
            if benchmark_path is not None else None
        ),
        WorkloadOwner.MEDIA: (
            _bound_reader(WorkloadOwner.MEDIA, configured_host, read_media_workloads, media_path)
            if media_path is not None else None
        ),
        WorkloadOwner.RECIPE: (
            _bound_reader(WorkloadOwner.RECIPE, configured_host, list_recipe_workloads, recipe_path)
            if recipe_path is not None else None
        ),
        WorkloadOwner.MANIFEST: (
            _bound_reader(WorkloadOwner.MANIFEST, configured_host, list_manifest_workloads, manifest_path)
            if manifest_path is not None else None
        ),
        WorkloadOwner.ROUTER: _router_binding(
            configured_host,
            router_topology,
            router_resource,
            router_auth_env,
            environment,
        ),
    }
    return readers


__all__ = ["build_workload_readers"]
