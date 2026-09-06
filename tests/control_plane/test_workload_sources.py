import datetime as dt
import os
from types import SimpleNamespace

import pytest

from anvil_serving.control_plane.controller import workload_sources
from anvil_serving.control_plane.controller.store import OperationStore
from anvil_serving.observability.workloads import (
    ResultStatus,
    SourceResult,
    Truncation,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadKind,
    WorkloadOwner,
    WorkloadQuery,
)
from anvil_serving.paths import LOOPBACK_ALIAS_ENV


HOST = "node-a"
NOW = dt.datetime(2026, 9, 5, 12, 0, tzinfo=dt.timezone.utc)
TOKEN = "a" * 16


def _source(owner, now=NOW):
    return SourceResult(owner, ResultStatus.COMPLETE, now, (), Truncation(0, 0))


def _reader(owner, calls):
    def read(path, host, query, now):
        calls.append((path, host, query, now))
        return _source(owner, now)

    return read


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


def test_construction_only_captures_explicit_paths_and_exact_operation_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(workload_sources.os.path, "exists", lambda _: (_ for _ in ()).throw(AssertionError()))
    store = OperationStore(str(tmp_path / "operations.sqlite"))
    readers = workload_sources.build_workload_readers(
        HOST,
        store,
        benchmark_db="benchmark.sqlite",
        media_db="media.sqlite",
        recipe_registry="recipes.toml",
        manifest="serves.toml",
    )
    assert set(readers) == set(WorkloadOwner)
    assert all(readers[owner] is not None for owner in (
        WorkloadOwner.CONTROLLER, WorkloadOwner.BENCHMARK, WorkloadOwner.MEDIA,
        WorkloadOwner.RECIPE, WorkloadOwner.MANIFEST,
    ))
    assert readers[WorkloadOwner.ROUTER] is None


def test_explicit_reader_forwards_once_and_relative_path_is_pinned(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(workload_sources, "read_media_workloads", _reader(WorkloadOwner.MEDIA, calls))
    original = os.getcwd()
    os.chdir(tmp_path)
    try:
        readers = workload_sources.build_workload_readers(HOST, None, media_db="media.sqlite")
        os.chdir(tmp_path.parent)
        result = readers[WorkloadOwner.MEDIA](HOST, WorkloadQuery(), NOW)
    finally:
        os.chdir(original)
    assert result == _source(WorkloadOwner.MEDIA)
    assert calls == [(str(tmp_path / "media.sqlite"), HOST, WorkloadQuery(), NOW)]


@pytest.mark.parametrize(
    "path",
    (None, "", " relative.sqlite ", "~/.anvil-serving/private.sqlite", "bad\npath", "x" * 4097),
)
def test_missing_or_malformed_optional_path_disables_only_its_source(path):
    readers = workload_sources.build_workload_readers(HOST, None, media_db=path)
    assert readers[WorkloadOwner.MEDIA] is None
    assert readers[WorkloadOwner.BENCHMARK] is None


def test_canonical_exclusions_and_wrong_callback_host_do_not_call_owner(monkeypatch):
    calls = []
    monkeypatch.setattr(workload_sources, "read_benchmark_workloads", _reader(WorkloadOwner.BENCHMARK, calls))
    callback = workload_sources.build_workload_readers(HOST, None, benchmark_db="bench.sqlite")[WorkloadOwner.BENCHMARK]
    excluded = callback(HOST, WorkloadQuery(owner=WorkloadOwner.MEDIA), NOW)
    wrong_host = callback("node-b", WorkloadQuery(), NOW)
    assert excluded.status is ResultStatus.COMPLETE and excluded.records == ()
    assert wrong_host.status is ResultStatus.UNAVAILABLE
    assert calls == []


def test_invalid_canonical_arguments_raise_before_owner(monkeypatch):
    calls = []
    monkeypatch.setattr(workload_sources, "read_benchmark_workloads", _reader(WorkloadOwner.BENCHMARK, calls))
    callback = workload_sources.build_workload_readers(HOST, None, benchmark_db="bench.sqlite")[WorkloadOwner.BENCHMARK]
    with pytest.raises(WorkloadError):
        callback(HOST, object(), NOW)
    with pytest.raises(WorkloadError):
        callback(HOST, WorkloadQuery(), object())
    assert calls == []


def test_owner_failure_is_fixed_unavailable_without_private_text(monkeypatch):
    def throwing(*args):
        raise RuntimeError("private path and token")

    monkeypatch.setattr(workload_sources, "list_recipe_workloads", throwing)
    callback = workload_sources.build_workload_readers(HOST, None, recipe_registry="recipes.toml")[WorkloadOwner.RECIPE]
    result = callback(HOST, WorkloadQuery(), NOW)
    assert result.status is ResultStatus.UNAVAILABLE
    assert result.error is WorkloadErrorCode.UNAVAILABLE
    assert "private path" not in repr(result)


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


def test_router_requires_complete_explicit_binding_and_captures_only_two_environment_values(monkeypatch):
    calls = []

    def read(endpoint, auth_env, host, query, now, *, environment):
        calls.append((endpoint, auth_env, host, query, now, environment))
        return _source(WorkloadOwner.ROUTER, now)

    monkeypatch.setattr(workload_sources, "load_topology", lambda path: _router_topology())
    monkeypatch.setattr(workload_sources, "read_router_workloads", read)
    readers = workload_sources.build_workload_readers(
        HOST,
        None,
        router_topology="topology.toml",
        router_resource="router-observation",
        router_auth_env="ROUTER_TOKEN",
        environment={"ROUTER_TOKEN": TOKEN, LOOPBACK_ALIAS_ENV: "router-host", "PRIVATE": "never-copy"},
    )
    assert readers[WorkloadOwner.ROUTER](HOST, WorkloadQuery(), NOW) == _source(WorkloadOwner.ROUTER)
    assert calls == [
        (
            "http://127.0.0.1:8765/v1", "ROUTER_TOKEN", HOST, WorkloadQuery(), NOW,
            {"ROUTER_TOKEN": TOKEN, LOOPBACK_ALIAS_ENV: "router-host"},
        )
    ]
    for missing in ("router_topology", "router_resource", "router_auth_env"):
        arguments = {
            "router_topology": "topology.toml",
            "router_resource": "router-observation",
            "router_auth_env": "ROUTER_TOKEN",
            "environment": {},
        }
        arguments[missing] = None
        assert workload_sources.build_workload_readers(HOST, None, **arguments)[WorkloadOwner.ROUTER] is None


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


def test_router_exclusion_does_not_load_topology(monkeypatch):
    monkeypatch.setattr(workload_sources, "load_topology", lambda path: (_ for _ in ()).throw(AssertionError()))
    callback = workload_sources.build_workload_readers(
        HOST,
        None,
        router_topology="topology.toml",
        router_resource="router-observation",
        router_auth_env="ROUTER_TOKEN",
        environment={},
    )[WorkloadOwner.ROUTER]
    result = callback(HOST, WorkloadQuery(kind=WorkloadKind.MEDIA_JOB), NOW)
    assert result.status is ResultStatus.COMPLETE
