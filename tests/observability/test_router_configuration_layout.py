"""Typed edits preserve TOML meaning across supported operator key spellings."""

import hashlib
import json

import pytest

from anvil_serving import router_manage
from anvil_serving.control_plane.mcp.tools import router as owner


def _config(quote, newline="\n"):
    def key(value):
        return quote + value + quote

    text = f'''[{key("router")}]
relay_timeout = 30
[[{key("router")} . {key("tiers")}]] # unchanged secondary tier
id = "secondary"
{key("max_concurrency")} = 3
{key("max_output_tokens")} = 512
[[{key("router")} . {key("tiers")}]] # selected tier
id = "primary"
{key("max_concurrency")} = 1
{key("max_output_tokens")} = 4096
url = "http://127.0.0.1:30000/v1"
[{key("router")}.{key("tiers")}.metadata]
max_output_tokens = 99
'''
    return text.replace("\n", newline).encode()


def test_quoted_layout_preview_has_real_candidate_digest_and_no_writes(tmp_path, monkeypatch):
    config = tmp_path / "router.toml"
    raw = _config('"')
    config.write_bytes(raw)
    baseline = hashlib.sha256(raw).hexdigest()

    def inspected(argv, **_kwargs):
        assert argv == ["docker", "inspect", "--format", "{{json .Mounts}}", "fixture-router"]
        return {"stdout": json.dumps([{
            "Destination": "/etc/anvil/config.toml", "Source": str(config),
            "Type": "bind", "RW": False,
        }])}

    def no_mutation(*_args, **_kwargs):
        pytest.fail("configuration preview attempted a runtime mutation")

    monkeypatch.setattr(owner, "_run_argv", inspected)
    monkeypatch.setattr(router_manage, "installed_fleet_status", lambda **_kwargs: {"config_sha256": baseline})
    for name in ("install_config", "cmd_up", "transition_request"):
        monkeypatch.setattr(router_manage, name, no_mutation)
    result = owner.tool_router_configuration({
        "action": "preview", "config": str(config), "container": "fixture-router",
        "tier": "primary", "values": {"max_output_tokens": 2048},
        "expected_baseline_sha256": baseline,
    })
    assert result["data"]["baseline_sha256"] == baseline
    assert result["data"]["candidate_sha256"] != baseline
    assert result["data"]["configured"] == {"max_concurrency": 1, "max_output_tokens": 2048}
    assert result["data"]["applied"] is False
    assert config.read_bytes() == raw
    assert list(tmp_path.iterdir()) == [config]
