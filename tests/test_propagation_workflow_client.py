"""The controller callback uses a bounded authenticated local transport."""

import json
import os
import socket
import sys
import time
from threading import Event, Thread

import pytest

from anvil_serving.control_plane.controller.propagation_job_store import PropagationJobError
from anvil_serving.control_plane.controller.propagation_workflow_client import WorkflowControlClient
from anvil_serving.control_plane.controller import propagation_workflow_client as callback_client


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="native owner control socket is Linux-only")


def _client(path, token, *, uid=None, gid=None):
    return WorkflowControlClient(path, token, expected_peer_uid=os.geteuid() if uid is None else uid,
                                 expected_peer_gid=os.getegid() if gid is None else gid)


def _exchange(path, response, observed, ready, errors, *, bound=None, release=None):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(path))
            if bound is not None:
                bound.set()
            if release is not None and not release.wait(timeout=2):
                raise TimeoutError("fixture release timed out")
            path.chmod(0o600)
            server.listen(1)
            server.settimeout(2)
            ready.set()
            with server.accept()[0] as conn:
                conn.settimeout(2)
                value = bytearray()
                while not value.endswith(b"\n"):
                    part = conn.recv(4096)
                    if not part:
                        raise EOFError("client closed before request terminator")
                    value.extend(part)
                observed.append(json.loads(value))
                conn.sendall(response)
    except Exception as error:
        errors.append(error)


def test_control_client_requires_protected_token_and_bounded_response(tmp_path):
    tmp_path.chmod(0o700)
    token = tmp_path / "control.token"
    token.write_text("a" * 64)
    token.chmod(0o600)
    path = tmp_path / "control.sock"
    client = _client(path, token)
    seen = []
    ready, errors = Event(), []
    server = Thread(
        target=_exchange,
        args=(path, b'{"ok":true,"result":{"state":"requested"}}\n', seen, ready, errors),
    )
    server.start()
    try:
        assert ready.wait(timeout=2), errors
        assert client.cancel("workflow-1", "intent-" + "b" * 64, "b" * 64, "caller-1") == {"state": "requested"}
    finally:
        server.join(timeout=3)
    assert not server.is_alive()
    assert errors == []
    assert seen[0]["token"] == "a" * 64
    assert seen[0]["action"] == "cancel"
    path.unlink()
    token.chmod(0o644)
    with pytest.raises(PropagationJobError, match="workflow_service_unavailable"):
        _client(path, token)

    token.chmod(0o600)
    client = _client(path, token)
    seen, ready, errors = [], Event(), []
    server = Thread(target=_exchange, args=(path, b"x" * 4097 + b"\n", seen, ready, errors))
    server.start()
    try:
        assert ready.wait(timeout=2), errors
        with pytest.raises(PropagationJobError, match="workflow_service_unavailable"):
            client.status("workflow-1", "b" * 64)
    finally:
        server.join(timeout=3)
    assert not server.is_alive()
    assert errors == []
    assert seen[0]["action"] == "status"


def test_control_client_has_one_exchange_deadline(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    token = tmp_path / "control.token"
    token.write_text("a" * 64)
    token.chmod(0o600)
    path = tmp_path / "control.sock"
    monkeypatch.setattr(callback_client, "_EXCHANGE_SECONDS", 0.4)
    ready, seen, errors = Event(), [], []
    def slow_reply():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(path))
                path.chmod(0o600)
                server.listen(1)
                server.settimeout(2)
                ready.set()
                with server.accept()[0] as conn:
                    conn.settimeout(2)
                    request = bytearray()
                    while not request.endswith(b"\n"):
                        part = conn.recv(4096)
                        if not part:
                            raise EOFError("client closed before request terminator")
                        request.extend(part)
                    seen.append(json.loads(request))
                    conn.sendall(b'{"ok":')
                    time.sleep(0.25)
                    conn.sendall(b'true,"result":')
                    time.sleep(0.25)
                    conn.sendall(b'{"state":"requested"}}\n')
        except BrokenPipeError:
            pass
        except Exception as error:
            errors.append(error)
    server = Thread(target=slow_reply)
    server.start()
    try:
        assert ready.wait(timeout=2), errors
        client = _client(path, token)
        with pytest.raises(PropagationJobError, match="workflow_service_unavailable"):
            client.status("workflow-1", "b" * 64)
    finally:
        server.join(timeout=3)
    assert not server.is_alive()
    assert errors == []
    assert seen[0]["action"] == "status"
