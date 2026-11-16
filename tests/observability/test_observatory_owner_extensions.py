import json
from contextlib import contextmanager

import pytest

from anvil_serving import reservations, serves
from anvil_serving.control_plane.mcp.errors import ToolError
from anvil_serving.control_plane.mcp.tools.models import tool_recipe_settings
from anvil_serving.control_plane.mcp.tools import serves as serves_tools


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _amount=None):
        return json.dumps(self.payload).encode()


def test_recipe_preview_never_writes_and_apply_requires_human_gate(tmp_path):
    registry = tmp_path / "recipes.toml"
    registry.write_text('''schema = "anvil-serving/serve-recipes-v1"\n\n[[recipe]]\nmodel = "org/model"\n[recipe.serve]\nimage = "example/image@sha256:abc"\nstartup_timeout_seconds = 60\nflags = ["--max-model-len 4096", "--max-num-seqs 2"]\n''')
    before = registry.read_bytes()
    result = tool_recipe_settings({
        "action": "preview", "registry": str(registry), "model": "org/model",
        "values": {"maximum_context": 8192},
    })
    assert result["ok"] and result["data"]["candidate"]["maximum_context"] == 8192
    assert registry.read_bytes() == before
    with pytest.raises(ToolError, match="human"):
        tool_recipe_settings({
            "action": "apply", "registry": str(registry), "model": "org/model",
            "values": {"maximum_context": 8192}, "confirm": True, "dry_run": False,
        })
    assert registry.read_bytes() == before


def test_none_engine_is_explicitly_cpu_only_and_not_blocked_by_exclusive_owner(tmp_path):
    manifest = tmp_path / "serves.toml"
    manifest.write_text('''[[serve]]\nname = "observatory"\nruntime = "docker"\nport = 8445\nmodel = "observatory-control-plane"\nengine = "none"\ngpu_inference = false\ncontainer = "anvil-observatory"\nup = "docker compose -f {dir}/compose.yml up -d observatory"\n''')
    (cpu,) = serves.load_manifest(manifest)
    exclusive = {
        "name": "exclusive", "container": "exclusive", "model": "model", "engine": "vllm",
        "runtime": "docker", "port": 30000, "gpu_inference": True,
        "gpu_roles": ["a", "b"], "vram_mib": [1, 1], "operating_mode": "dual-gpu-exclusive",
    }
    assert reservations.deny_exclusive_conflict(
        [exclusive, cpu], [cpu], lambda name: "running" if name == "exclusive" else "absent"
    ) is None
    with pytest.raises(ValueError, match="requires gpu_inference=false"):
        bad = manifest.read_text().replace("gpu_inference = false", "gpu_inference = true")
        manifest.write_text(bad)
        serves.load_manifest(manifest)


def test_owner_probe_holds_shared_lock_and_forwards_exact_bounds(monkeypatch, tmp_path):
    manifest = tmp_path / "serves.toml"
    manifest.write_text("")
    held = False
    received = {}

    @contextmanager
    def owner_lock(role):
        nonlocal held
        assert role == "promotion"
        held = True
        try:
            yield
        finally:
            held = False

    def probe(_serve, **kwargs):
        assert held
        received.update(kwargs)
        return {
            "serve": "glm", "engine": "sglang", "model": "glm",
            "recognized_excerpt": "READY", "finish_reason": "stop", "incomplete": False,
        }

    monkeypatch.setattr(serves_tools, "_load_serves_for_tool", lambda _path: [{
        "name": "glm", "engine": "sglang", "model": "org/glm",
        "container": "glm", "port": 30000,
    }])
    monkeypatch.setattr(serves, "_switch_role_lock", owner_lock)
    monkeypatch.setattr(serves, "docker_state", lambda _container: "running")
    monkeypatch.setattr(serves, "probe_serve", probe)

    result = serves_tools.tool_serves_probe({
        "manifest": str(manifest), "name": "glm",
        "timeout_seconds": 60, "max_tokens": 256,
    })
    assert result["data"]["passed"] is True
    assert received["timeout"] == 60
    assert received["max_tokens"] == 256
    assert received["text"] == "Reply with the single word READY."
    assert held is False
