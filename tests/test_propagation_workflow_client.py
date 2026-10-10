"""The controller callback uses a bounded authenticated local transport."""

import json
import os
import socket
import struct
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


def test_control_client_fixture_waits_for_protected_listener(tmp_path):
    tmp_path.chmod(0o700)
    token = tmp_path / "control.token"
    token.write_text("a" * 64)
    token.chmod(0o600)
    path = tmp_path / "control.sock"
    seen, errors = [], []
    bound, release, ready = Event(), Event(), Event()
    server = Thread(
        target=_exchange,
        args=(path, b'{"ok":true,"result":{"state":"pending"}}\n', seen, ready, errors),
        kwargs={"bound": bound, "release": release},
    )
    server.start()
    try:
        assert bound.wait(timeout=2), errors
        assert path.exists()
        assert not ready.wait(timeout=0.05)
        release.set()
        assert ready.wait(timeout=2), errors
        assert _client(path, token).status("workflow-1", "b" * 64) == {"state": "pending"}
    finally:
        release.set()
        server.join(timeout=3)
        assert not server.is_alive()
    assert errors == []
    assert seen[0]["action"] == "status"


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


@pytest.mark.parametrize("pid,uid_delta,gid_delta,accepted", [
    (0, 0, 0, True), (0, 1, 0, False), (0, 0, 1, False), (-1, 0, 0, False),
])
def test_control_client_namespace_invisible_peer_keeps_identity_guards(
    tmp_path, monkeypatch, pid, uid_delta, gid_delta, accepted,
):
    tmp_path.chmod(0o700)
    token = tmp_path / "control.token"
    token.write_text("a" * 64)
    token.chmod(0o600)
    path = tmp_path / "control.sock"
    real_socket = socket.socket
    class NamespaceSocket(real_socket):
        def getsockopt(self, level, option, *args):
            if (level, option) == (socket.SOL_SOCKET, socket.SO_PEERCRED):
                return struct.pack("3i", pid, os.geteuid() + uid_delta, os.getegid() + gid_delta)
            return super().getsockopt(level, option, *args)

    seen = []
    with real_socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        path.chmod(0o600)
        listener.listen(1)
        def serve():
            with listener.accept()[0] as conn:
                conn.settimeout(2)
                data = conn.recv(4096)
                seen.append(data)
                if data:
                    conn.sendall(b'{"ok":true,"result":{"state":"pending"}}\n')
        server = Thread(target=serve)
        server.start()
        try:
            monkeypatch.setattr(callback_client.socket, "socket", NamespaceSocket)
            client = _client(path, token)
            if accepted:
                assert client.status("workflow-1", "b" * 64) == {"state": "pending"}
            else:
                with pytest.raises(PropagationJobError, match="workflow_service_unavailable"):
                    client.status("workflow-1", "b" * 64)
        finally:
            server.join(timeout=3)
        assert not server.is_alive()
    assert bool(seen[0]) is accepted
