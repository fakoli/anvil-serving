import json

import pytest

from anvil_serving import mcp, serves
from anvil_serving.control_plane.mcp.tools import router as router_tools
from anvil_serving.control_plane.mcp.tools import serves as serves_tools


MODE_MANIFEST = """
[[gpu_roles]]
id = "dark-compute-a"
vram_mib = 97887

[[gpu_roles]]
id = "dark-compute-b"
vram_mib = 97887

[[serve]]
name = "split-a"
container = "split-a"
runtime = "docker"
port = 30001
model = "split-a-local"
engine = "vllm"
gpu_role = "dark-compute-a"
vram_mib = 80000
groups = ["split-stack"]

[[serve]]
name = "split-b"
container = "split-b"
runtime = "docker"
port = 30002
model = "split-b-local"
engine = "vllm"
gpu_role = "dark-compute-b"
vram_mib = 80000
groups = ["split-stack"]

[[serve]]
name = "tp2"
container = "tp2"
runtime = "docker"
port = 30003
model = "candidate-local"
engine = "vllm"
gpu_roles = ["dark-compute-a", "dark-compute-b"]
vram_mib = 90000
operating_mode = "dual-gpu-exclusive"
tensor_parallel_size = 2
"""


def test_client_catalog_sync_schema_is_secret_reference_only_and_guarded():
    properties = mcp.TOOLS["client_catalog_sync"]["inputSchema"]["properties"]
    assert "api_key" not in properties
    assert {
        "api_key_env",
        "clients",
        "hermes_config",
        "hermes_bin",
        "hermes_home",
        "hermes_profiles",
        "dry_run",
        "confirm",
        "restart_openclaw_on_change",
        "restart_hermes_on_change",
    } <= set(properties)
    with pytest.raises(mcp.ToolError, match="raw api_key"):
        mcp.tool_client_catalog_sync({"api_key": "never"})


def test_hermes_media_sync_schema_accepts_only_secret_references():
    properties = mcp.TOOLS["hermes_media_sync"]["inputSchema"]["properties"]
    assert "mcp_url" not in properties
    assert "token" not in properties
    assert {
        "mcp_url_env",
        "token_env",
        "hermes_profiles",
        "restart_hermes_on_change",
        "dry_run",
        "confirm",
    } <= set(properties)


def test_serves_mode_status_reports_structured_exclusive_ownership(
    tmp_path, monkeypatch,
):
    manifest = tmp_path / "serves.toml"
    manifest.write_text(MODE_MANIFEST, encoding="utf-8")
    monkeypatch.setattr(
        serves,
        "docker_state",
        lambda container, **kwargs: "running" if container == "tp2" else "absent",
    )
    result = mcp.tool_serves_mode({"action": "status", "manifest": str(manifest)})
    assert result["ok"]
    mode = result["data"]["operating_mode"]
    assert mode["mode"] == "dual-gpu-exclusive"
    assert mode["exclusive_owner"] == "tp2"
    assert mode["gpu_roles"] == ["dark-compute-a", "dark-compute-b"]
    assert mode["tensor_parallel_size"] == 2
    assert mode["blocked_workloads"] == ["split-a", "split-b"]
    assert mode["gpu_ownership"] == [
        {"gpu_role": "dark-compute-a", "owners": ["tp2"]},
        {"gpu_role": "dark-compute-b", "owners": ["tp2"]},
    ]


def test_serves_mode_live_enter_forwards_preserve_on_failure(
    tmp_path, monkeypatch,
):
    manifest = tmp_path / "serves.toml"
    manifest.write_text(MODE_MANIFEST, encoding="utf-8")
    monkeypatch.setattr(serves, "docker_state", lambda *args, **kwargs: "absent")
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(serves_tools, "_run_argv", run)
    result = mcp.tool_serves_mode({
        "action": "enter",
        "manifest": str(manifest),
        "target": "tp2",
        "restore_group": "split-stack",
        "dry_run": False,
        "confirm": True,
        "human_approved": True,
        "preserve_on_failure": True,
    })

    assert result["ok"]
    assert result["data"]["preserve_on_failure"] is True
    assert "--preserve-on-failure" in seen["argv"]
    assert seen["kwargs"]["confirm"] is True


def test_openclaw_sync_apply_writes_direct_alias_provider(tmp_path):
    config = tmp_path / "router.toml"
    config.write_text(
        """
[router]

[[router.tiers]]
id = "primary-local"
base_url = "http://127.0.0.1:30002/v1"
model = "primary"
dialect = "openai"
context_limit = 393216
privacy = "local"
tool_support = true
auth_env = "ANVIL_PRIMARY_LOCAL_KEY"

[router.tiers.params.capabilities]
modalities = ["text"]

[[router.tiers]]
id = "omni-local"
base_url = "http://127.0.0.1:30003/v1"
model = "omni"
dialect = "openai"
context_limit = 393216
privacy = "local"
tool_support = true
auth_env = "ANVIL_OMNI_LOCAL_KEY"

[router.tiers.params.capabilities]
modalities = ["text", "image"]

[router.model_routes]
llm.primary = "primary-local"
llm.voice = "primary-local"
vision.ocr = "omni-local"
vision.general = "omni-local"
""".lstrip(),
        encoding="utf-8",
    )
    out = tmp_path / "openclaw.json"

    result = mcp.tool_openclaw_sync({
        "config": str(config),
        "out": str(out),
        "dry_run": False,
        "confirm": True,
    })

    assert result["ok"]
    data = result["data"]
    assert data["applied"] is True
    assert data["preview"]["direct_aliases"] is True
    assert data["preview"]["model_ids"] == [
        "llm.primary",
        "llm.voice",
        "vision.ocr",
        "vision.general",
    ]
    assert data["preview"]["image_model"] == "anvil/vision.general"
    assert "plugin_id" not in data["preview"]
    assert "route_endpoint" not in data["target"]
    assert out.is_file()
    rendered = json.loads(out.read_text(encoding="utf-8"))
    assert rendered["agents"]["defaults"]["imageModel"] == {
        "primary": "anvil/vision.general"
    }


def test_workflow_promotion_requires_a_serves_promotion_result():
    packet = {
        "schema_version": "operator-workflow/v1", "request": "benchmark", "gate_state": "human_required",
        "targets": {}, "tools_used": [], "artifacts": [], "advisory_priors": [],
        "recommendation": "promote", "human_gate_required": True, "promoted": False,
    }
    assert mcp.validate_workflow_packet(packet)["valid"]
    packet["promoted"] = True
    assert not mcp.validate_workflow_packet(packet)["valid"]


def test_router_manage_confirmed_call_forwards_lifecycle_options(tmp_path, monkeypatch):
    compose = tmp_path / "docker-compose.yml"
    env_file = tmp_path / "router.env"
    calls = []
    monkeypatch.setattr(
        router_tools,
        "_run_argv",
        lambda argv, *, confirm, timeout: calls.append((argv, confirm, timeout)) or {"returncode": 0},
    )

    result = mcp.tool_router_manage({
        "action": "up",
        "compose": str(compose),
        "service": "router",
        "env_file": str(env_file),
        "recreate": True,
        "dry_run": False,
        "confirm": True,
    })

    assert result["ok"]
    assert result["data"]["applied"] is True
    assert result["data"]["target"]["compose_project"] == "anvil-serving"
    argv, confirm, timeout = calls[0]
    assert confirm is True
    assert timeout == 300
    assert argv[-2:] == ["--recreate", "--confirm"]
    assert ["--compose", str(compose)] == argv[argv.index("--compose"):argv.index("--compose") + 2]
    assert ["--service", "router"] == argv[argv.index("--service"):argv.index("--service") + 2]
    assert ["--env-file", str(env_file)] == argv[argv.index("--env-file"):argv.index("--env-file") + 2]
    assert result["data"]["lifecycle_command"] == [
        "docker", "compose", "--project-name", "anvil-serving",
        "--env-file", str(env_file), "-f", str(compose), "up", "-d",
        "--no-deps", "--force-recreate", "router",
    ]
