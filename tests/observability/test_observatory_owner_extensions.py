import json
from contextlib import contextmanager
import urllib.request

import pytest

from anvil_serving import reservations, serves
from anvil_serving.control_plane.mcp.errors import ToolError
from anvil_serving.control_plane.mcp.tools.models import tool_recipe_settings
from anvil_serving.control_plane.mcp.tools.router import _tier_candidate
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


def test_router_candidate_changes_only_closed_tier_fields():
    raw = b'''[router]\nrelay_timeout = 30\n\n[[router.tiers]]\nid = "primary"\nmax_concurrency = 2\nmax_output_tokens = 4096\nurl = "http://127.0.0.1:30000/v1"\n'''
    candidate, values = _tier_candidate(
        raw, "primary", {"max_concurrency": 4, "max_output_tokens": 8192}
    )
    assert b"max_concurrency = 4" in candidate
    assert b"max_output_tokens = 8192" in candidate
    assert b'url = "http://127.0.0.1:30000/v1"' in candidate
    assert values["max_concurrency"] == 4
    with pytest.raises(ToolError):
        _tier_candidate(raw, "primary", {"url": 1})


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


def test_chat_probe_has_one_exact_bounded_request_and_structured_result():
    requests = []

    def opened(request, timeout):
        requests.append((request, timeout))
        return _Response({"choices": [{"message": {"content": "READY"}, "finish_reason": "stop"}]})

    result = serves.probe_serve(
        {"name": "glm", "engine": "sglang", "model": "org/glm", "served_name": "glm",
         "port": 30000, "stack": "serving"},
        text="Reply with the single word READY.", timeout=17, _open=opened,
    )
    assert result["recognized_excerpt"] == "READY"
    assert result["request"] == {"max_tokens": 256, "temperature": 0}
    assert result["finish_reason"] == "stop"
    assert result["incomplete"] is False
    assert len(requests) == 1
    request, timeout = requests[0]
    assert request.full_url == "http://127.0.0.1:30000/v1/chat/completions"
    assert timeout == 17
    payload = json.loads(request.data)
    assert payload == {
        "model": "glm",
        "messages": [{"role": "user", "content": "Reply with the single word READY."}],
        "max_tokens": 256,
        "temperature": 0,
    }


def test_chat_probe_reports_output_limit_as_incomplete_evidence():
    result = serves.probe_serve(
        {"name": "glm", "engine": "sglang", "model": "org/glm", "port": 30000},
        text="Reply with the single word READY.", max_tokens=1,
        _open=lambda request, timeout: _Response({
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}]
        }),
    )
    assert result["recognized_excerpt"] == ""
    assert result["finish_reason"] == "length"
    assert result["incomplete"] is True


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


def test_safe_probe_opener_preserves_post_request(monkeypatch):
    captured = {}

    def opened(request, *args, **kwargs):
        captured.update(request=request, args=args, kwargs=kwargs)
        return object()

    monkeypatch.setattr(urllib.request, "urlopen", opened)
    request = urllib.request.Request(
        "http://127.0.0.1:30000/v1/chat/completions",
        data=b'{"max_tokens":256}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    response = serves_tools._open_safe_probe_request(request, timeout=60)
    assert response is not None
    forwarded = captured["request"]
    assert forwarded.full_url == request.full_url
    assert forwarded.data == request.data
    assert forwarded.get_method() == "POST"
    assert forwarded.get_header("Content-type") == "application/json"
    assert captured["kwargs"] == {"timeout": 60}
