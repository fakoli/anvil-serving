"""CLI and assembly checks retained for direct local serving."""
from __future__ import annotations

from pathlib import Path

import pytest

from anvil_serving import cli
import anvil_serving.router.serve as router_serve
from anvil_serving.router.config import ConfigError, Tier, load
from anvil_serving.router.serve import (
    _warn_if_public_bind,
    build_backend_for_tier,
    build_backends,
    build_server,
    resolve_config_path,
)


_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "example.toml"


def test_router_run_help_documents_optional_config_home(capsys):
    assert cli.main(["router", "run", "--help"]) == 0
    output = capsys.readouterr().out
    assert "--config" in output
    assert "[--config PATH]" in output


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


def test_local_direct_backends_build_without_cloud_credentials():
    backends, skipped = build_backends(load(_CONFIG), env={})

    assert set(backends) == {
        "primary-local",
        "auxiliary-local",
        "ocr-local",
        "vision-local",
    }
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
