"""The controller callback uses a bounded authenticated local transport."""

import json
import socket
import sys
from threading import Thread

import pytest

from anvil_serving.control_plane.controller.propagation_job_store import PropagationJobError
from anvil_serving.control_plane.controller.propagation_workflow_client import WorkflowControlClient


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="native owner control socket is Linux-only")


def _exchange(path, response, observed):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        server.listen(1)
        with server.accept()[0] as conn:
            value = bytearray()
            while not value.endswith(b"\n"):
                value.extend(conn.recv(4096))
            observed.append(json.loads(value))
            conn.sendall(response)


def test_control_client_requires_protected_token_and_bounded_response(tmp_path):
    token = tmp_path / "control.token"
    token.write_text("a" * 64)
    token.chmod(0o600)
    path = tmp_path / "control.sock"
    client = WorkflowControlClient(path, token)
    seen = []
    server = Thread(target=_exchange, args=(path, b'{"ok":true,"result":{"state":"requested"}}\n', seen))
    server.start()
    try:
        # Wait for the listener without mutating any owner state.
        for _ in range(1000):
            if path.exists():
                break
            server.join(.001)
        assert client.cancel("workflow-1", "intent-" + "b" * 64, "b" * 64, "caller-1") == {"state": "requested"}
    finally:
        server.join(timeout=2)
    assert seen[0]["token"] == "a" * 64
    assert seen[0]["action"] == "cancel"
    path.unlink()
    token.chmod(0o644)
    with pytest.raises(PropagationJobError, match="workflow_service_unavailable"):
        WorkflowControlClient(path, token)

    token.chmod(0o600)
    client = WorkflowControlClient(path, token)
    server = Thread(target=_exchange, args=(path, b"x" * 4097 + b"\n", []))
    server.start()
    try:
        for _ in range(1000):
            if path.exists():
                break
            server.join(.001)
        with pytest.raises(PropagationJobError, match="workflow_service_unavailable"):
            client.status("workflow-1", "b" * 64)
    finally:
        server.join(timeout=2)
