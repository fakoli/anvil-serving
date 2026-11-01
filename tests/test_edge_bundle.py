from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from anvil_serving import cli, edge_bundle


EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "remote-tailnet"
    / "compose-endpoint.json"
)


def _manifest() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _contains_key(value, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def test_render_contains_no_secret_values_and_router_fragment_is_valid_toml() -> None:
    rendered = edge_bundle.render_bundle(edge_bundle.load_bundle(EXAMPLE))
    serialized = json.dumps(rendered)
    fragment = rendered["files"]["router-tier.toml"]
    parsed = tomllib.loads(fragment)

    assert "tskey-" not in serialized
    assert "ANVIL_TAILSCALE_AUTH_KEY" in serialized
    assert "ANVIL_REMOTE_INFERENCE_TOKEN" in serialized
    assert parsed["router"]["tiers"][0]["base_url"].endswith("/v1")
    assert parsed["router"]["tiers"][0]["health_path"] == "/v1/models"
    assert parsed["router"]["tiers"][0]["model_identity"] is True
    assert parsed["router"]["model_routes"]["llm.remote"] == "remote-inference"
    assert "engine" not in parsed["router"]["tiers"][0]


def test_public_cli_and_command_manifest_expose_only_offline_bundle_actions(capsys) -> None:
    assert cli.main(["edge", "bundle", "--help"]) == 0
    help_text = capsys.readouterr().out
    assert "validate" in help_text
    assert "render" in help_text
    assert "deploy" not in help_text
    assert "enroll" not in help_text
    for action in ("validate", "render"):
        assert cli.main(["edge", "bundle", action, "--help"]) == 0
        assert "--manifest PATH" in capsys.readouterr().out

    assert cli.main(["--command-manifest"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    records = {record["path"]: record for record in manifest["commands"]}
    assert "edge bundle validate" in records
    assert "edge bundle render" in records
    assert records["edge bundle render"]["mutation_class"] == "read"
    for action in ("validate", "render"):
        assert any("--manifest" in option["flags"] for option in records[f"edge bundle {action}"]["options"])


@pytest.mark.parametrize("section,field,value", [
    ("inference", "api_key_container_env", "IGNORED_BY_VLLM"),
    ("inference", "cache_mount", "/etc"),
    ("inference", "cache_mount", "/usr/local/bin"),
    ("inference", "model_revision", "a" * 41),
    ("router", "tool_support", True),
])
def test_adapter_rejects_unenforced_auth_cache_or_capability_contract(section, field, value):
    manifest = _manifest()
    manifest[section][field] = value
    with pytest.raises(edge_bundle.EdgeBundleError):
        edge_bundle.EdgeBundle.from_mapping(manifest)


def test_generated_launch_and_canonical_router_parser_agree(tmp_path):
    from anvil_serving.router.config import load

    manifest = _manifest()
    manifest["inference"]["extra_args"] = ["--enable-auto-tool-choice", "--tool-call-parser", "hermes", "--enable-request-id-headers"]
    manifest["router"]["tool_support"] = True
    rendered = edge_bundle.render_bundle(edge_bundle.EdgeBundle.from_mapping(manifest))
    tier_path = tmp_path / "router.toml"
    tier_path.write_text(rendered["files"]["router-tier.toml"], encoding="utf-8")
    tier = load(str(tier_path)).tiers[0]
    inference = rendered["files"]["compose.json"]["services"]["inference"]
    command = inference["command"]
    assert command[command.index("--max-model-len") + 1] == str(tier.context_limit)
    assert command[command.index("--download-dir") + 1] == "/root/.cache/huggingface"
    assert command[command.index("--tokenizer-revision") + 1] == manifest["inference"]["model_revision"]
    assert tier.tool_support is True
    assert set(inference["environment"]) == {"VLLM_API_KEY"}
