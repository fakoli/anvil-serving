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
