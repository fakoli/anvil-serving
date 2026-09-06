from __future__ import annotations

from datetime import datetime, timezone

import pytest

from anvil_serving.observability import fleet_workload_sources as sources
from anvil_serving.observability.workloads import (
    ResultStatus,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadQuery,
)
from anvil_serving.topology import Host, Runtime, Topology, Transport


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
TOKEN = "test-token-test-token"


def _topology(*, transports=(), hosts=("node-a", "node-b")):
    return Topology(
        "fleet", 1, tuple(Host(host, ()) for host in hosts),
        tuple(Runtime(host + "-native", host, "native") for host in hosts),
        (), (), tuple(transports), (),
    )


def _transport(*, host="node-a", expected_node="node-a", operation="node-workloads", identifier="controller-a"):
    return Transport(
        identifier, "controller", host, host + "-native", "http://127.0.0.1:8765",
        "CONTROLLER_TOKEN", (operation,), expected_node=expected_node,
    )


def test_every_declared_host_is_retained_and_only_exact_binding_is_callable(monkeypatch):
    calls = []

    def reader(endpoint, auth_env, host, query, now, *, environment, monotonic):
        calls.append((endpoint, auth_env, host, environment))
        from anvil_serving.observability.workload_collection import build_node_workloads
        return build_node_workloads(host, query, now, {})

    monkeypatch.setattr(sources, "read_controller_workloads", reader)
    readers = sources.build_fleet_workload_readers(
        _topology(transports=(_transport(operation="node_workloads"),)),
        environment={"CONTROLLER_TOKEN": TOKEN, "PRIVATE": "do-not-copy"},
        monotonic=lambda: 0.0,
    )
    assert set(readers) == {"node-a", "node-b"}
    assert readers["node-b"] is None
    assert readers["node-a"]("node-a", WorkloadQuery(), NOW).host == "node-a"
    assert calls == [("http://127.0.0.1:8765", "CONTROLLER_TOKEN", "node-a", {"CONTROLLER_TOKEN": TOKEN})]


@pytest.mark.parametrize("transports", [
    (),
    (_transport(expected_node="node-b"),),
    (_transport(), _transport(identifier="controller-b")),
    (Transport("ssh-a", "ssh", "node-a", "node-a-native", "ssh://127.0.0.1:22", allowed_operations=("node-workloads",)),),
    (_transport(operation="controller-status"),),
])
def test_missing_ambiguous_or_wrong_binding_disables_only_that_host(transports):
    readers = sources.build_fleet_workload_readers(
        _topology(transports=transports), environment={"CONTROLLER_TOKEN": TOKEN}
    )
    assert readers["node-a"] is None
    assert readers["node-b"] is None


def test_topology_shape_is_validated_before_environment_access():
    class _Trap(dict):
        def get(self, key, default=None):
            raise AssertionError(key)

    topology = _topology()
    object.__setattr__(topology, "hosts", [Host("node-a", ())])
    with pytest.raises(ValueError) as error:
        sources.build_fleet_workload_readers(topology, environment=_Trap())
    assert "node-a" not in str(error.value)


def test_callback_exclusion_and_wrong_host_do_not_call_reader(monkeypatch):
    monkeypatch.setattr(
        sources, "read_controller_workloads",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("read")),
    )
    callback = sources.build_fleet_workload_readers(
        _topology(transports=(_transport(),)), environment={"CONTROLLER_TOKEN": TOKEN}
    )["node-a"]
    assert callback is not None
    excluded = callback("node-a", WorkloadQuery(host="node-b"), NOW)
    wrong = callback("node-b", WorkloadQuery(), NOW)
    assert excluded.status is ResultStatus.COMPLETE
    assert wrong.status is ResultStatus.UNAVAILABLE


def test_environment_and_topology_mutation_cannot_change_captured_binding(monkeypatch):
    captured = {"CONTROLLER_TOKEN": TOKEN}
    topology = _topology(transports=(_transport(),))
    calls = []

    def reader(*args, **kwargs):
        calls.append(kwargs["environment"])
        from anvil_serving.observability.workload_collection import build_node_workloads
        return build_node_workloads("node-a", WorkloadQuery(), NOW, {})

    monkeypatch.setattr(sources, "read_controller_workloads", reader)
    callback = sources.build_fleet_workload_readers(topology, environment=captured)["node-a"]
    captured["CONTROLLER_TOKEN"] = "different-token-value"
    object.__setattr__(topology.transports[0], "endpoint", "http://127.0.0.1:9999")
    assert callback is not None
    callback("node-a", WorkloadQuery(), NOW)
    assert calls == [{"CONTROLLER_TOKEN": TOKEN}]


@pytest.mark.parametrize("value", (None, "short", "bad\ncredential"))
def test_missing_or_malformed_captured_credential_disables_only_its_host(value):
    readers = sources.build_fleet_workload_readers(
        _topology(transports=(_transport(),)), environment={"CONTROLLER_TOKEN": value}
    )
    assert readers["node-a"] is None
    assert readers["node-b"] is None


def test_factory_uses_one_explicit_pinned_path_and_configuration_failure_is_unavailable(monkeypatch):
    paths = []
    topology = _topology(transports=())
    monkeypatch.setattr(sources.os.path, "abspath", lambda path: paths.append(path) or "/fixed/topology.toml")
    monkeypatch.setattr(sources, "load_topology", lambda path: topology if path == "/fixed/topology.toml" else None)
    collector = sources.create_fleet_workload_collector("relative.toml", environment={})
    try:
        assert paths == ["relative.toml"]
        assert collector.collect(WorkloadQuery(), NOW).status is ResultStatus.UNAVAILABLE
    finally:
        collector.close()

    monkeypatch.setattr(sources, "load_topology", lambda _path: (_ for _ in ()).throw(RuntimeError("private path")))
    with pytest.raises(WorkloadError) as error:
        sources.create_fleet_workload_collector("bad.toml", environment={})
    assert error.value.code is WorkloadErrorCode.UNAVAILABLE
    assert "private" not in str(error.value)
