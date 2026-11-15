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
