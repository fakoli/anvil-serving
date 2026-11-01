"""CLI and assembly checks retained for direct local serving."""
from __future__ import annotations

from pathlib import Path
import http.client
import json
import threading
from types import SimpleNamespace


from anvil_serving.control_plane.authorization import AuthorizationError
import anvil_serving.router.serve as router_serve
from anvil_serving.router.config import load
from anvil_serving.router.serve import (
    build_backends,
    build_server,
)
from tests.router.helpers import StaticBackend


_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "example.toml"


def test_router_run_forwards_authorization_policy(tmp_path, monkeypatch):
    config = tmp_path / "router.toml"
    config.write_text("[router]\n", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(
        router_serve,
        "serve",
        lambda path, **kwargs: seen.update(path=path, **kwargs),
    )

    assert router_serve.main([
        "--config", str(config), "--authorization-policy", "synthetic-policy.json",
    ]) == 0
    assert seen["authorization_policy"] == "synthetic-policy.json"


def test_serve_forwards_authorization_policy_to_build_server(monkeypatch):
    seen = {}
    fake_server = SimpleNamespace(
        server_address=("127.0.0.1", 0), anvil_tiers=(), anvil_purpose=None,
        anvil_audio=None, anvil_gateway=None,
    )
    monkeypatch.setattr(router_serve, "load_server_config", lambda path: SimpleNamespace(auth_env=None))
    monkeypatch.setattr(
        router_serve, "build_server",
        lambda path, **kwargs: seen.update(path=path, **kwargs) or fake_server,
    )
    monkeypatch.setattr(router_serve, "serve_until_signal", lambda server: None)

    router_serve.serve("synthetic-router.toml", authorization_policy="synthetic-policy.json")
    assert seen["authorization_policy"] == "synthetic-policy.json"


def _configured_server(tmp_path):
    config = tmp_path / "router.toml"
    config.write_text(
        _CONFIG.read_text(encoding="utf-8")
        + "\n[server]\nauth_env = 'ROUTER_TEST_TOKEN'\n",
        encoding="utf-8",
    )
    backends = {
        "primary-local": StaticBackend(["ok"]),
        "omni-local": StaticBackend(["ok"]),
    }
    return config, backends


def test_malformed_optional_policy_leaves_legacy_router_authenticated(tmp_path, monkeypatch):
    config, backends = _configured_server(tmp_path)
    monkeypatch.setattr(
        router_serve,
        "load_authorization_policy",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AuthorizationError("authorization_policy_malformed")
        ),
    )
    server = build_server(
        str(config), port=0, backends=backends,
        env={"ROUTER_TEST_TOKEN": "legacy-router-token"},
        authorization_policy="broken-policy.json",
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        connection = http.client.HTTPConnection(host, port, timeout=5)
        connection.request(
            "POST", "/v1/chat/completions",
            json.dumps({"model": "llm.primary", "messages": [{"role": "user", "content": "hi"}]}),
            {"Authorization": "Bearer legacy-router-token", "Content-Type": "application/json"},
        )
        assert connection.getresponse().status == 200
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_local_direct_backends_build_without_cloud_credentials():
    backends, skipped = build_backends(load(_CONFIG), env={})

    assert set(backends) == {"primary-local", "omni-local"}
    assert skipped == []
