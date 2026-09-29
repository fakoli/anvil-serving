"""Approved activation refuses drift in the actual Docker start inputs."""

import copy
import io
import json

import pytest

from anvil_serving.control_plane.controller import propagation_active_identity as active
from anvil_serving.control_plane.controller.propagation_job_store import PropagationJobError
from anvil_serving.controller_diagnostics import ChildCapture


def _inspect():
    binding = {"HostIp": "127.0.0.1", "HostPort": "9123"}
    return {"Id": "a" * 64, "Image": "sha256:" + "b" * 64,
            "Args": ["--model", "/approved/model", "--served-model-name", "model-a-exact"],
            "State": {"Running": True, "Status": "running"},
            "Config": {"Entrypoint": ["python"], "Cmd": ["serve"], "Image": "approved@sha256:" + "b" * 64,
                       "Labels": {"io.anvil-serving.managed-by": "anvil-serving-recipe",
                                  "io.anvil-serving.recipe.model": "model/revision",
                                  "io.anvil-serving.recipe.revision": "c" * 40}},
            "Mounts": [{"Source": "/approved/model", "Destination": "/models", "Type": "bind", "RW": False}],
            "HostConfig": {"PortBindings": {"9123/tcp": [binding]}, "DeviceRequests": []},
            "NetworkSettings": {"Ports": {"9123/tcp": [binding.copy()]}}}


class Response(io.BytesIO):
    status = 200


class Opener:
    def __init__(self, document):
        self.document = document

    def open(self, request, timeout):
        assert request.full_url == "http://127.0.0.1:9123/v1/models"
        assert timeout == 5
        return Response(self.document[0])


def test_active_identity_requires_exact_runtime_binding(monkeypatch):
    approved = _inspect()
    observed = copy.deepcopy(approved)
    catalog = {"config_sha256": "d" * 64}
    document = [b'{"data":[{"id":"model-a-exact"}]}']
    def docker(argv, *, merged):
        assert argv == active.local_docker_prefix() + ("inspect", "a" * 64)
        assert merged is False
        return ChildCapture("ok", json.dumps([observed]).encode(), b"", False)
    def opener(*handlers):
        assert any(isinstance(handler, active._NoRedirect) for handler in handlers)
        return Opener(document)
    monkeypatch.setattr(active, "_capture_fixed_child", docker)
    monkeypatch.setattr(active, "fetch_client_catalog", lambda **_: catalog)
    monkeypatch.setattr(active.urllib.request, "build_opener", opener)
    observer = active.ObservedActiveIdentity(
        "activation-1", "d" * 64,
        [{"container_id": approved["Id"], "runtime_digest": active._runtime_digest(approved),
          "served_identity": "model-a-exact", "bound_port": 9123}],
        "http://127.0.0.1:8000/v1", "ROUTER_TOKEN")
    assert observer().activation_digest == observer.digest
    changes = (
        lambda: observed["Args"].__setitem__(1, "/not-approved/model"),
        lambda: observed["Mounts"][0].__setitem__("Source", "/not-approved/model"),
        lambda: observed["NetworkSettings"]["Ports"]["9123/tcp"][0].__setitem__("HostIp", "127.0.0.2"),
        lambda: catalog.update(config_sha256="e" * 64),
        lambda: document.__setitem__(0, b'{"data":[{"id":"other"}]}'),
    )
    for change in changes:
        observed = copy.deepcopy(approved)
        catalog["config_sha256"] = "d" * 64
        document[0] = b'{"data":[{"id":"model-a-exact"}]}'
        change()
        with pytest.raises(PropagationJobError, match="active_identity_unavailable"):
            observer()


def test_active_identity_refuses_unapproved_container_and_redirect():
    with pytest.raises(PropagationJobError, match="active_identity_unavailable"):
        active.ObservedActiveIdentity("activation-1", "d" * 64,
                                      [{"container_id": "short", "runtime_digest": "e" * 64,
                                        "served_identity": "model-a-exact", "bound_port": 9123}],
                                      "http://127.0.0.1:8000/v1", "ROUTER_TOKEN")
    assert active._NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://example.test") is None


def test_active_identity_refuses_observation_after_aggregate_deadline(monkeypatch):
    approved = _inspect()
    clock = [0.0]
    monkeypatch.setattr(active, "monotonic", lambda: clock[0])
    def slow_catalog(**_kwargs):
        clock[0] = 21.0
        return {"config_sha256": "d" * 64}
    monkeypatch.setattr(active, "fetch_client_catalog", slow_catalog)
    monkeypatch.setattr(active, "_capture_fixed_child", lambda *_args, **_kwargs: pytest.fail("late Docker observation"))
    observer = active.ObservedActiveIdentity(
        "activation-1", "d" * 64,
        [{"container_id": approved["Id"], "runtime_digest": active._runtime_digest(approved),
          "served_identity": "model-a-exact", "bound_port": 9123}],
        "http://127.0.0.1:8000/v1", "ROUTER_TOKEN")
    with pytest.raises(PropagationJobError, match="active_identity_unavailable"):
        observer()


def test_active_identity_refuses_late_served_identity(monkeypatch):
    approved = _inspect()
    clock = [0.0]
    monkeypatch.setattr(active, "monotonic", lambda: clock[0])
    monkeypatch.setattr(active, "fetch_client_catalog", lambda **_kwargs: {"config_sha256": "d" * 64})
    monkeypatch.setattr(active, "_capture_fixed_child", lambda *_args, **_kwargs:
                        ChildCapture("ok", json.dumps([approved]).encode(), b"", False))
    class SlowResponse(Response):
        def read(self, *_args):
            clock[0] = 21.0
            return b'{"data":[{"id":"model-a-exact"}]}'
    class SlowOpener:
        def open(self, _request, timeout):
            assert timeout == 5
            return SlowResponse()
    monkeypatch.setattr(active.urllib.request, "build_opener", lambda *_handlers: SlowOpener())
    observer = active.ObservedActiveIdentity(
        "activation-1", "d" * 64,
        [{"container_id": approved["Id"], "runtime_digest": active._runtime_digest(approved),
          "served_identity": "model-a-exact", "bound_port": 9123}],
        "http://127.0.0.1:8000/v1", "ROUTER_TOKEN")
    with pytest.raises(PropagationJobError, match="active_identity_unavailable"):
        observer()


def test_remote_observer_requires_exact_fresh_profile_and_never_uses_local_docker(monkeypatch):
    from datetime import datetime, timezone
    from anvil_serving.control_plane.mcp import controller_client

    approved = _inspect()
    observed = active.ObservedActiveIdentity(
        "activation-1", "d" * 64,
        [{"container_id": approved["Id"], "runtime_digest": active._runtime_digest(approved),
          "served_identity": "model-a-exact", "bound_port": 9123}],
        "http://127.0.0.1:8000/v1", "ROUTER_TOKEN",
        observer={"controller_url": "http://127.0.0.1:8766", "token_file": "/protected/token",
                  "profile_sha256": "f" * 64})
    monkeypatch.setattr(controller_client, "resolve_controller_token_file", lambda path: "test-token")
    monkeypatch.setattr(active, "_capture_fixed_child", lambda *_a, **_k: pytest.fail("local Docker used"))
    monkeypatch.setattr(active, "fetch_client_catalog", lambda **_k: pytest.fail("local router token used"))
    data = {"activation_ref": observed.ref, "activation_digest": observed.digest,
            "observer_profile_sha256": "f" * 64,
            "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
    def remote(url, request, token, **kwargs):
        assert url == "http://127.0.0.1:8766" and token == "test-token"
        assert request["params"]["name"] == "propagation.activation.observe.v1"
        assert request["params"]["arguments"] == {}
        return {"result": {"isError": False, "structuredContent": {"ok": True, "data": data}}}
    monkeypatch.setattr(controller_client, "remote_controller_request", remote)
    assert observed().activation_digest == observed.digest
    for field, replacement in (("observer_profile_sha256", "e" * 64),
                               ("activation_digest", "e" * 64),
                               ("observed_at", "2020-01-01T00:00:00Z")):
        original = data[field]
        data[field] = replacement
        with pytest.raises(PropagationJobError, match="active_identity_unavailable"):
            observed()
        data[field] = original
