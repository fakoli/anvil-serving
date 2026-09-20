"""Native NInfer manifests retain ordinary managed GPU reservations."""
from anvil_serving.serves import load_manifest
import contextlib
import io
import json
from pathlib import Path
import tomllib
import pytest
from anvil_serving import serves
from anvil_serving.control_plane.mcp.tools import serves as serves_tools


def test_ninfer_manifest_preserves_explicit_identity_and_reservation(tmp_path):
    manifest = tmp_path / "serves.toml"
    manifest.write_text('''[[serve]]
name = "candidate"
container = "anvil-candidate"
runtime = "docker"
port = 8080
model = "candidate-mtp3-32k"
engine = "ninfer"
gpu_role = "compute"
vram_mib = 28511
residency = "on-demand"
health = "/health"
''')
    rows = load_manifest(str(manifest))
    assert rows[0]["engine"] == "ninfer"
    assert rows[0]["model"] == "candidate-mtp3-32k"
    assert rows[0]["gpu_role"] == "compute"
    assert rows[0]["vram_mib"] == 28511


@pytest.mark.parametrize("with_image", [False, True])
def test_ninfer_probe_uses_bounded_chat_contract(tmp_path, with_image):
    photo = tmp_path / "fixture.png"
    photo.write_bytes(b"fixture")
    def opened(request, timeout):
        payload = json.loads(request.data)
        assert request.full_url == "http://127.0.0.1:8080/v1/chat/completions"
        assert payload["model"] == "candidate"
        assert payload["max_tokens"] == 256
        assert payload["temperature"] == 0
        content = payload["messages"][0]["content"]
        if with_image:
            assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        else:
            assert content == "Reply READY"
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "READY"}, "finish_reason": "stop"}]}).encode())
    result = serves.probe_serve({"name": "candidate", "engine": "ninfer", "model": "candidate", "port": 8080}, text="Reply READY", image_path=str(photo) if with_image else None, _open=opened)
    assert result["recognized_excerpt"] == "READY"


@pytest.mark.parametrize("observed,passed", [("READY", True), ("NO", False)])
def test_ninfer_mcp_probe_checks_expectation(monkeypatch, tmp_path, observed, passed):
    monkeypatch.setattr(serves_tools, "_load_serves_for_tool", lambda _: [{"name": "candidate", "engine": "ninfer", "container": "candidate"}])
    monkeypatch.setattr(serves, "docker_state", lambda _: "running")
    monkeypatch.setattr(serves, "_switch_role_lock", lambda _: contextlib.nullcontext())
    monkeypatch.setattr(serves, "probe_serve", lambda *a, **kw: {"recognized_excerpt": observed, "incomplete": False})
    result = serves_tools.tool_serves_probe({"name": "candidate", "manifest": str(tmp_path / "serves.toml")})["data"]
    assert result["passed"] is passed
    assert result["parameters"]["request_kind"] == "chat_completion"
    assert result["parameters"]["expected"] == "READY"


def test_ninfer_baked_recipe_pins_build_and_verifies_startup():
    root = Path(__file__).resolve().parents[1]
    runtime = root / "configs/runtime/ninfer-70434721"
    dockerfile = (runtime / "Dockerfile").read_text()
    entrypoint = (runtime / "entrypoint.sh").read_text()
    recipe = tomllib.loads((root / "configs/qwen38-huihui-ninfer-baked-rtx5090-recipes.toml").read_text())["recipe"][0]
    assert "@sha256:b9f64abf7226fdb3463ca202bc99878ec847171e6c5f77bd34c8d1403fbf1eca" in dockerfile
    assert "--snapshot 20260919T000000Z" in dockerfile
    assert "70434721b1ae29d0616f3de9b376c8a4d91590b5" in dockerfile
    assert "--parallel 4" in dockerfile
    assert "sha256sum --check /opt/provenance/binary.sha256" in entrypoint
    assert "f21f308d3b23ccd627071cd015e413db08deee4356643900518e2b251750fdc2" in entrypoint
    assert not any(x in entrypoint for x in ("apt-get", "git ", "cmake"))
    assert recipe["serve"]["image"] == "anvil-ninfer@sha256:44377d56374ed4b832c2ca6e9265da492cf2ad79a297008bae92f442d27edfb6"
    assert recipe["download"]["revision"] == "181446902fc777c479749e98cf2abf2250263a8d"
    assert {"--max-context 32768", "--max-concurrency 1", "--draft-tokens 3", "--vision"} <= set(recipe["serve"]["flags"])
