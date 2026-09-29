"""The controller callback uses a bounded authenticated local transport."""

import json
import os
import socket
import sys
import time
from threading import Thread

import pytest

from anvil_serving.control_plane.controller.propagation_job_store import PropagationJobError
from anvil_serving.control_plane.controller.propagation_workflow_client import WorkflowControlClient
from anvil_serving.control_plane.controller import propagation_workflow_client as callback_client


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="native owner control socket is Linux-only")


def _client(path, token, *, uid=None, gid=None):
    return WorkflowControlClient(path, token, expected_peer_uid=os.geteuid() if uid is None else uid,
                                 expected_peer_gid=os.getegid() if gid is None else gid)


def _exchange(path, response, observed):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        path.chmod(0o600)
        server.listen(1)
        with server.accept()[0] as conn:
            value = bytearray()
            while not value.endswith(b"\n"):
                value.extend(conn.recv(4096))
            observed.append(json.loads(value))
            conn.sendall(response)


def test_control_client_requires_protected_token_and_bounded_response(tmp_path):
    tmp_path.chmod(0o700)
    token = tmp_path / "control.token"
    token.write_text("a" * 64)
    token.chmod(0o600)
    path = tmp_path / "control.sock"
    client = _client(path, token)
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
        _client(path, token)

    token.chmod(0o600)
    client = _client(path, token)
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


def test_control_client_refuses_replaceable_or_redirected_socket(tmp_path):
    token = tmp_path / "control.token"
    token.write_text("a" * 64)
    token.chmod(0o600)
    trusted = tmp_path / "trusted"
    trusted.mkdir(mode=0o700)
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o777)
    loose.chmod(0o777)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(loose / "listener.sock"))
        server.listen(1)
        (trusted / "control.sock").symlink_to(loose / "listener.sock")
        client = _client(trusted / "control.sock", token)
        with pytest.raises(PropagationJobError, match="workflow_service_unavailable"):
            client.status("workflow-1", "b" * 64)
        server.settimeout(0.1)
        with pytest.raises(TimeoutError):
            server.accept()


def test_control_client_has_one_exchange_deadline(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    token = tmp_path / "control.token"
    token.write_text("a" * 64)
    token.chmod(0o600)
    path = tmp_path / "control.sock"
    monkeypatch.setattr(callback_client, "_EXCHANGE_SECONDS", 0.4)
    def slow_reply():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(path))
            path.chmod(0o600)
            server.listen(1)
            with server.accept()[0] as conn:
                conn.recv(4096)
                conn.sendall(b'{"ok":')
                time.sleep(0.25)
                try:
                    conn.sendall(b'true,"result":')
                    time.sleep(0.25)
                    conn.sendall(b'{"state":"requested"}}\n')
                except BrokenPipeError:
                    pass
    server = Thread(target=slow_reply)
    server.start()
    try:
        for _ in range(1000):
            if path.exists():
                break
            server.join(.001)
        client = _client(path, token)
        with pytest.raises(PropagationJobError, match="workflow_service_unavailable"):
            client.status("workflow-1", "b" * 64)
    finally:
        server.join(timeout=2)


def test_control_client_rejects_unexpected_peer_before_token(tmp_path):
    tmp_path.chmod(0o700)
    token = tmp_path / "control.token"
    token.write_text("a" * 64)
    token.chmod(0o600)
    path = tmp_path / "control.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        path.chmod(0o600)
        server.listen(1)
        with pytest.raises(PropagationJobError, match="workflow_service_unavailable"):
            _client(path, token, uid=os.geteuid() + 1).status("workflow-1", "b" * 64)
        server.settimeout(0.1)
        with pytest.raises(TimeoutError):
            server.accept()
