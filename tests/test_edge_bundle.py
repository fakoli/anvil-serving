from __future__ import annotations

import copy
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


def test_example_validates_and_render_is_offline_least_exposure_plan() -> None:
    bundle = edge_bundle.load_bundle(EXAMPLE)
    rendered = edge_bundle.render_bundle(bundle)
    compose = rendered["files"]["compose.json"]
    serve = rendered["files"]["tailscale-config/serve.json"]

    assert rendered["provider_deployment_tested"] is False
    assert not _contains_key(compose, "ports")
    assert not _contains_key(serve, "AllowFunnel")
    assert compose["services"]["inference"]["network_mode"] == "service:tailscale"
    assert compose["services"]["tailscale"]["environment"]["TS_USERSPACE"] == "true"
    assert "TS_STATE_DIR" in compose["services"]["tailscale"]["environment"]
    assert bundle.tailnet.state_volume in compose["volumes"]
    handler = serve["Web"]["${TS_CERT_DOMAIN}:443"]["Handlers"]["/v1"]
    assert handler["Proxy"] == "http://127.0.0.1:8000/v1"


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


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda data: data.update({"secret": "literal"}), "unknown manifest fields"),
        (lambda data: data["tailnet"].update({"funnel": True}), "unknown tailnet fields"),
        (lambda data: data["inference"].update({"port": 0}), "inference.port"),
        (
            lambda data: data["inference"].update({"image": "vllm/vllm-openai:latest"}),
            "inference.image",
        ),
        (
            lambda data: data["inference"].update({"model_revision": "main"}),
            "model_revision",
        ),
    ),
)
def test_strict_schema_rejects_unsafe_or_unpinned_input(mutate, message) -> None:
    data = copy.deepcopy(_manifest())
    mutate(data)
    with pytest.raises(edge_bundle.EdgeBundleError, match=message):
        edge_bundle.EdgeBundle.from_mapping(data)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda data: data["inference"].update(
            {"extra_args": ["--host", "0.0.0.0"]}
        ),
        lambda data: data["inference"].update(
            {"api_key_env": data["tailnet"]["auth_key_env"]}
        ),
        lambda data: data["inference"].update(
            {"cache_volume": data["tailnet"]["state_volume"]}
        ),
        lambda data: data["router"].update(
            {"max_output_tokens": data["router"]["context_limit"] + 1}
        ),
    ),
)
def test_capability_and_resource_boundaries_cannot_be_overridden(mutate) -> None:
    data = copy.deepcopy(_manifest())
    mutate(data)
    with pytest.raises(edge_bundle.EdgeBundleError):
        edge_bundle.EdgeBundle.from_mapping(data)


def test_vllm_adapter_constructs_security_critical_flags() -> None:
    bundle = edge_bundle.EdgeBundle.from_mapping(_manifest())
    command = edge_bundle.render_bundle(bundle)["files"]["compose.json"]["services"][
        "inference"
    ]["command"]

    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "8000"
    assert command[command.index("--revision") + 1] == bundle.inference.model_revision


def test_vast_single_container_target_is_typed_unsupported(tmp_path, capsys) -> None:
    data = _manifest()
    data["target"] = "vast-container"
    path = tmp_path / "vast.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    assert edge_bundle.main(["render", "--manifest", str(path)]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "unsupported"
    assert result["error"]["code"] == "unsupported-target"
    assert result["error"]["target"] == "vast-container"


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


def test_cli_render_prints_valid_nested_json_without_writing(capsys) -> None:
    assert cli.main(["edge", "bundle", "render", "--manifest", str(EXAMPLE)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "preview"
    assert json.loads(json.dumps(result["files"]["compose.json"]))["services"]


@pytest.mark.parametrize("arguments", [
    ["--api_key=PRIVATE"], ["--hos=0.0.0.0"], ["--config", "/tmp/config"],
    ["--served_model_name", "other"], ["--dtype=${PRIVATE}"],
    ["--tensor-parallel-size", "2"], ["--dtype", "auto", "--dtype", "half"],
    ["--tool-call-parser", "../../plugin.py"], ["--gpu-memory-utilization", "nan"],
])
def test_only_bounded_canonical_tuning_flags_are_accepted(arguments):
    manifest = _manifest()
    manifest["inference"]["extra_args"] = arguments
    with pytest.raises(edge_bundle.EdgeBundleError):
        edge_bundle.EdgeBundle.from_mapping(manifest)


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


@pytest.mark.parametrize("raw", [b"\xffPRIVATE", b"[" * 2000 + b"]" * 2000, b" " * (edge_bundle.MAX_MANIFEST_BYTES + 1)], ids=["encoding", "nesting", "size"])
def test_bad_manifest_reads_fail_without_reflecting_content(raw, tmp_path, capsys):
    path = tmp_path / "invalid.json"
    path.write_bytes(raw)
    assert edge_bundle.main(["validate", "--manifest", str(path)]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert "PRIVATE" not in json.dumps(output)


def test_unknown_field_names_are_not_reflected(tmp_path, capsys):
    manifest = _manifest()
    manifest["PRIVATE_CONTENT_AS_KEY"] = "PRIVATE_VALUE"
    path = tmp_path / "unknown.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert edge_bundle.main(["validate", "--manifest", str(path)]) == 2
    assert "PRIVATE" not in capsys.readouterr().out


@pytest.mark.parametrize("old,new", [
    ('"target": "compose"', '"target": "vast-container", "target": "compose"'),
    ('"port": 8000', '"port": 1, "port": 8000'),
])
def test_duplicate_json_members_fail_at_every_depth(old, new, tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(_manifest()).replace(old, new), encoding="utf-8")
    with pytest.raises(edge_bundle.EdgeBundleError):
        edge_bundle.load_bundle(path)


@pytest.mark.parametrize("model", ["model/../../etc", "foo//bar", "foo/.", "model", "/tmp/model"])
def test_model_requires_canonical_remote_repository_identity(model):
    manifest = _manifest()
    manifest["inference"]["served_model"] = model
    with pytest.raises(edge_bundle.EdgeBundleError):
        edge_bundle.EdgeBundle.from_mapping(manifest)


def test_filesystem_errors_do_not_reflect_path(tmp_path, capsys):
    missing = tmp_path / "PRIVATE_TOKEN.json"
    assert edge_bundle.main(["validate", "--manifest", str(missing)]) == 2
    assert "PRIVATE" not in capsys.readouterr().out
