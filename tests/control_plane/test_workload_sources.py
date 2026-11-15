import datetime as dt
from types import SimpleNamespace

import pytest

from anvil_serving.control_plane.controller import workload_sources
from anvil_serving.control_plane.controller.store import OperationStore
from anvil_serving.observability.workloads import (
    ResultStatus,
    SourceResult,
    Truncation,
    WorkloadOwner,
    WorkloadQuery,
)


HOST = "node-a"
NOW = dt.datetime(2026, 9, 5, 12, 0, tzinfo=dt.timezone.utc)
TOKEN = "a" * 16


def _source(owner, now=NOW):
    return SourceResult(owner, ResultStatus.COMPLETE, now, (), Truncation(0, 0))


def _router_topology(*, role="router-workloads", endpoint_kind="workloads-v1", host=HOST, runtime_host=HOST):
    resource = SimpleNamespace(
        role=role,
        endpoint_kind=endpoint_kind,
        host=host,
        runtime="router-runtime",
        workload="service",
        endpoint="http://127.0.0.1:8765/v1",
    )
    return SimpleNamespace(
        resource=lambda value: resource if value == "router-observation" else (_ for _ in ()).throw(ValueError()),
        runtime=lambda value: SimpleNamespace(host=runtime_host) if value == "router-runtime" else (_ for _ in ()).throw(ValueError()),
    )


def test_operation_store_must_be_exact_and_is_bound_without_recovery():
    class StoreSubclass(OperationStore):
        pass

    with pytest.raises(ValueError):
        workload_sources.build_workload_readers(HOST, StoreSubclass("operations.sqlite"))

    store = OperationStore("operations.sqlite")
    calls = []

    def read(host, query, now):
        calls.append((host, query, now))
        return _source(WorkloadOwner.CONTROLLER, now)

    store.list_workloads = read
    callback = workload_sources.build_workload_readers(HOST, store)[WorkloadOwner.CONTROLLER]
    assert callback(HOST, WorkloadQuery(), NOW) == _source(WorkloadOwner.CONTROLLER)
    assert calls == [(HOST, WorkloadQuery(), NOW)]


@pytest.mark.parametrize("topology", (
    _router_topology(role="router"),
    _router_topology(endpoint_kind="chat-v1"),
    _router_topology(host="node-b"),
    _router_topology(runtime_host="node-b"),
))
def test_router_runtime_and_resource_mismatch_are_unavailable_without_transport(monkeypatch, topology):
    opened = []
    monkeypatch.setattr(workload_sources, "load_topology", lambda path: topology)
    monkeypatch.setattr(workload_sources, "read_router_workloads", lambda *args, **kwargs: opened.append(args))
    callback = workload_sources.build_workload_readers(
        HOST,
        None,
        router_topology="topology.toml",
        router_resource="router-observation",
        router_auth_env="ROUTER_TOKEN",
        environment={"ROUTER_TOKEN": TOKEN},
    )[WorkloadOwner.ROUTER]
    result = callback(HOST, WorkloadQuery(), NOW)
    assert result.status is ResultStatus.UNAVAILABLE
    assert opened == []
