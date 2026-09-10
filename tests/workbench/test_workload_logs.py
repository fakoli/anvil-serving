"""Closed owner log dispatch and the browser authorization boundary."""
from types import SimpleNamespace

import pytest

from anvil_serving.observability.dashboard.contracts import ObservatoryError
from anvil_serving.observability.dashboard.controller_adapter import ControllerAdapter
from anvil_serving.workbench_app.service import WorkbenchService


def adapter(kind):
    owner = object.__new__(ControllerAdapter)
    owner._resources = {"workload-a": {"id": "workload-a", "kind": kind, "manifest": "/private/managed.toml", "serve": "model", "service": "voice"}}
    owner._tools = lambda: {"serves_logs"}
    owner._clock = lambda: 123
    owner.read_services = lambda resource: [{"logs": {"status": "available"}}]
    return owner


@pytest.mark.parametrize("kind,tool", [("serve", "serves_logs"), ("service", "host_services_logs")])
def test_logs_use_fixed_binding_bound_and_redact(kind, tool):
    owner = adapter(kind)
    calls = []
    def call(name, arguments):
        calls.append((name, arguments))
        return {"stdout": "token=secret-value\n" + "日" * 65536, "stderr": "", "lines": ["authorization: Bearer secret-value", "ready"]}
    owner._call = call
    result = owner.workload_logs("workload-a")
    assert calls[0][0] == tool
    assert calls[0][1]["tail"] == 200
    assert "secret-value" not in result["text"]
    assert len(result["text"].encode()) <= 65536
    assert "/private/" not in repr(result)
    if kind == "serve":
        assert result["truncated"]
        assert calls[0][1] == {"manifest": "/private/managed.toml", "names": ["model"], "tail": 200, "max_output_bytes": 65536, "timeout_seconds": 10, "follow": False}


def test_missing_service_identity_never_reads_logs():
    owner = adapter("service")
    owner.read_services = lambda _: [{"logs": {"status": "unavailable"}}]
    owner._call = lambda *_: pytest.fail("unverified service must not read logs")
    with pytest.raises(ObservatoryError):
        owner.workload_logs("workload-a")


def test_auth_and_closed_query_precede_owner():
    service = object.__new__(WorkbenchService)
    service.adapter = SimpleNamespace(workload_logs=lambda _: pytest.fail("unauthorized owner call"))
    def denied(*_):
        raise ObservatoryError("forbidden", "Forbidden", 403)
    service.access = SimpleNamespace(permit=denied)
    with pytest.raises(ObservatoryError) as caught:
        service.read("workloads/workload-a/logs", {}, object())
    assert caught.value.status == 403
    with pytest.raises(ObservatoryError) as caught:
        service.read("workloads/workload-a/logs", {"path": "/private"}, object())
    assert caught.value.status == 400
