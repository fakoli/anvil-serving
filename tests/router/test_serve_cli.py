"""CLI and assembly checks retained for direct local serving."""
from __future__ import annotations

from pathlib import Path
import http.client
import json
import threading
from types import SimpleNamespace

import pytest

from anvil_serving import cli
from anvil_serving.control_plane.authorization import AuthorizationError
import anvil_serving.router.serve as router_serve
from anvil_serving.router.config import ConfigError, Tier, load
from anvil_serving.router.serve import (
    _warn_if_public_bind,
    build_backend_for_tier,
    build_backends,
    build_server,
    resolve_config_path,
)
from anvil_serving.router.front_door import OperatorRoute
from anvil_serving.control_plane.authorization import WORKLOADS_READ
from tests.router.helpers import StaticBackend


_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "example.toml"


def test_router_run_help_documents_optional_config_home(capsys):
    assert cli.main(["router", "run", "--help"]) == 0
    output = capsys.readouterr().out
    assert "--config" in output
    assert "[--config PATH]" in output
    assert "--authorization-policy" in output


def test_router_run_resolves_config_home_before_cwd(tmp_path, monkeypatch):
    home = tmp_path / "operator-home"
    home.mkdir()
    config = home / "router.toml"
    config.write_text("[router]\n[router.model_routes]\nllm.primary = 'primary'\n[[router.tiers]]\nid = 'primary'\nbase_url = 'http://127.0.0.1:30000/v1'\nmodel = 'm'\ndialect = 'openai'\ncontext_limit = 1\nprivacy = 'local'\ntool_support = true\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "router.toml").write_text("[router]\n", encoding="utf-8")
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(home))
    assert resolve_config_path() == str(config)


def test_router_run_uses_discovered_config_home(tmp_path, monkeypatch):
    home = tmp_path / "operator-home"
    home.mkdir()
    config = home / "router.toml"
    config.write_text("[router]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(home))
    seen = {}
    monkeypatch.setattr(
        router_serve,
        "serve",
        lambda path, **kwargs: seen.update(path=path, **kwargs),
    )

    assert router_serve.main([]) == 0
    assert seen["path"] == str(config)
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 8000


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


def test_build_server_loads_optional_policy_once_with_explicit_environment(tmp_path, monkeypatch):
    config, backends = _configured_server(tmp_path)
    environment = {"ROUTER_TEST_TOKEN": "legacy-router-token"}
    calls = []

    def load_once(path, *, env, legacy_token):
        calls.append((path, env, legacy_token))
        return None

    monkeypatch.setattr(router_serve, "load_authorization_policy", load_once)
    server = build_server(
        str(config), port=0, backends=backends, env=environment,
        authorization_policy="synthetic-policy.json",
    )
    try:
        assert calls == [("synthetic-policy.json", environment, "legacy-router-token")]
    finally:
        server.server_close()


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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
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


def test_build_server_forwards_valid_policy_and_injected_operator_route(tmp_path):
    config, backends = _configured_server(tmp_path)
    policy_path = tmp_path / "operator-policy.json"
    policy_path.write_text(
        json.dumps({"schema_version": 1, "clients": [{
            "id": "reader", "scopes": [WORKLOADS_READ], "credential_env": "READ_TOKEN",
        }]}),
        encoding="utf-8",
    )
    calls = []
    server = build_server(
        str(config), port=0, backends=backends,
        env={"ROUTER_TEST_TOKEN": "legacy-router-token", "READ_TOKEN": "workloads-token-12345"},
        authorization_policy=str(policy_path),
        operator_routes=[OperatorRoute(
            "GET", "/v1/operator/workloads", WORKLOADS_READ,
            lambda query: calls.append(query) or b'{"ok":true}',
        )],
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        connection = http.client.HTTPConnection(host, port, timeout=5)
        connection.request(
            "GET", "/v1/operator/workloads?from=build-server",
            headers={"Authorization": "Bearer workloads-token-12345"},
        )
        response = connection.getresponse()
        assert (response.status, response.read()) == (200, b'{"ok":true}')
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert calls == ["from=build-server"]


def test_local_direct_backends_build_without_cloud_credentials():
    backends, skipped = build_backends(load(_CONFIG), env={})

    assert set(backends) == {"primary-local", "omni-local"}
    assert skipped == []


def test_direct_builder_rejects_cloud_tier():
    cloud = Tier(
        id="cloud", base_url="https://api.example.test/v1", model="remote",
        dialect="openai", context_limit=1024, privacy="cloud", tool_support=True,
        auth_env=None,
    )

    with pytest.raises(ConfigError, match="privacy='local'"):
        build_backend_for_tier(cloud, env={})


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.1"])
def test_public_bind_warns(host, capsys):
    _warn_if_public_bind(host, authed=False)
    assert "WARNING" in capsys.readouterr().err


def test_loopback_bind_is_silent(capsys):
    _warn_if_public_bind("127.0.0.1")
    assert capsys.readouterr().err == ""


def test_build_server_rejects_missing_configured_bearer_token(tmp_path):
    config = tmp_path / "router.toml"
    config.write_text(_CONFIG.read_text(encoding="utf-8") + "\n[server]\nauth_env = 'ANVIL_ROUTER_TOKEN'\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="ANVIL_ROUTER_TOKEN"):
        build_server(str(config), env={}, backends={})
