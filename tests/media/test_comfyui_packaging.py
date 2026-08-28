import hashlib
import json
import re
from pathlib import Path

from anvil_serving import serves
from anvil_serving.media.cli import DEFAULT_REGISTRY
from anvil_serving.media.workflows import canonical_digest


ROOT = Path(__file__).parents[2]
EXAMPLE = ROOT / "examples" / "fakoli-dark"
SCAFFOLD = ROOT / "anvil_serving" / "_scaffold_templates"
SOURCE_BUNDLE = ROOT / "configs" / "media" / "workflows"
PACKAGED_BUNDLE = ROOT / "anvil_serving" / "_media_workflows"
MIRRORED_RUNTIME_FILES = (
    "Dockerfile.comfyui",
    "docker-compose.comfyui.yml",
    "install-comfyui-node.sh",
    "serves.comfyui.toml",
)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_public_runtime_scaffold_is_byte_identical_and_identity_free():
    for name in MIRRORED_RUNTIME_FILES:
        assert (EXAMPLE / name).read_bytes() == (SCAFFOLD / name).read_bytes()
    compose = (EXAMPLE / "docker-compose.comfyui.yml").read_text(encoding="utf-8")
    assert '${COMFYUI_PUBLISH:-127.0.0.1}:8188:8188' in compose
    assert "MEDIA_WORKER_GPU_UUID" in compose
    assert "COMPUTE_B_GPU_UUID" not in compose
    assert not re.search(r"GPU-[0-9a-f]{8}-[0-9a-f-]{27,}", compose, re.IGNORECASE)
    assert "100.64." not in compose


def test_runtime_and_custom_node_inputs_are_exactly_pinned():
    lock = _json(SOURCE_BUNDLE / "bundle.lock.json")
    assert re.fullmatch(r"[^@]+@sha256:[0-9a-f]{64}", lock["staging"]["container"])
    runtime = lock["runtime"]
    dockerfile = (EXAMPLE / "Dockerfile.comfyui").read_text(encoding="utf-8")
    installer = (EXAMPLE / "install-comfyui-node.sh").read_bytes()
    assert runtime["container"] in dockerfile
    assert runtime["linux_amd64_manifest_digest"] in dockerfile
    assert runtime["comfyui_release_revision"] in dockerfile
    assert runtime["container_source_revision"] in dockerfile
    assert (
        'git -c safe.directory=/app -C /app rev-parse HEAD)" = "$COMFYUI_RELEASE_REV"'
        in dockerfile
    )
    assert runtime["installer_sha256"] == hashlib.sha256(installer).hexdigest()
    for component in ("torch", "torchvision", "torchaudio"):
        assert runtime[component] in dockerfile
    for node in lock["custom_nodes"]:
        assert re.fullmatch(r"[0-9a-f]{40}", node["revision"])
        assert re.fullmatch(r"[0-9a-f]{64}", node["requirements_sha256"])
        assert node["repository"] in dockerfile
        assert node["revision"] in dockerfile
        assert node["requirements_sha256"] in dockerfile


def test_workflow_bundle_and_required_model_identities_are_pinned():
    source_names = sorted(path.name for path in SOURCE_BUNDLE.glob("*.json"))
    assert source_names == sorted(path.name for path in PACKAGED_BUNDLE.glob("*.json"))
    for name in source_names:
        assert (SOURCE_BUNDLE / name).read_bytes() == (PACKAGED_BUNDLE / name).read_bytes()
    assert Path(DEFAULT_REGISTRY).resolve() == (PACKAGED_BUNDLE / "registry.json").resolve()

    lock = _json(SOURCE_BUNDLE / "bundle.lock.json")
    locked = {(item["id"], item["version"]): item for item in lock["workflows"]}
    for descriptor_path in sorted(SOURCE_BUNDLE.glob("*.json")):
        descriptor = _json(descriptor_path)
        if descriptor.get("schema") != "anvil-serving.media-workflow/v1":
            continue
        graph = _json(SOURCE_BUNDLE / descriptor["graph"])
        entry = locked[(descriptor["id"], descriptor["version"])]
        assert entry["graph_sha256"] == canonical_digest(graph) == descriptor["graph_digest"]
        models = entry["models"]
        assert {Path(model["target"]).name for model in models} == set(
            descriptor["required_models"]
        )
        for model in models:
            assert re.fullmatch(r"[0-9a-f]{40}", model["revision"])
            assert re.fullmatch(r"[0-9a-f]{64}", model["sha256"])
            assert model["size"] > 0
            assert not Path(model["target"]).is_absolute()


def test_managed_serve_is_bounded_healthy_and_lint_clean():
    manifest_path = EXAMPLE / "serves.comfyui.toml"
    manifest = serves.load_manifest(str(manifest_path))
    report = serves.lint_manifest_set(manifest)
    assert report["errors"] == 0
    assert len(manifest) == 1
    serve = manifest[0]
    assert serve["health"] == "/system_stats"
    assert serve["residency"] == "on-demand"
    assert serve["gpu_role"] == "media-compute"
    assert serve["vram_mib"] == 28672
    assert "docker-compose.comfyui.yml" in " ".join(serve["up"])

    compose = (EXAMPLE / "docker-compose.comfyui.yml").read_text(encoding="utf-8")
    assert "http://127.0.0.1:8188/system_stats" in compose
    assert "restart: \"no\"" in compose
    assert compose.count("external: true") == 2
