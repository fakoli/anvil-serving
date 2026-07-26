"""CLI and assembly checks retained for direct local serving."""
from __future__ import annotations

from pathlib import Path

import pytest

from anvil_serving import cli
from anvil_serving.router.config import ConfigError, Tier, load
from anvil_serving.router.serve import _warn_if_public_bind, build_backend_for_tier, build_backends, build_server


_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "example.toml"


def test_router_run_help_documents_explicit_config(capsys):
    assert cli.main(["router", "run", "--help"]) == 0
    output = capsys.readouterr().out
    assert "--config" in output


def test_local_direct_backends_build_without_cloud_credentials():
    backends, skipped = build_backends(load(_CONFIG), env={})

    assert set(backends) == {"heavy-local", "fast-local"}
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
