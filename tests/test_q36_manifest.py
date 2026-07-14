from pathlib import Path

from anvil_serving import serves


ROOT = Path(__file__).resolve().parents[1]


def test_q36_experiment_manifest_uses_truthful_engine_and_managed_compose():
    manifest = ROOT / "examples" / "fakoli-dark" / "serves.q36.toml"

    loaded = serves.load_manifest(manifest)

    assert len(loaded) == 1
    serve = loaded[0]
    assert serve["name"] == "q36-pro6000"
    assert serve["engine"] == "q36"
    assert serve["port"] == 39040
    assert serve["up"][:3] == ["docker", "compose", "-f"]
    assert serve["up"][-2:] == ["--build", "q36-pro6000"]


def test_q36_launcher_validates_mtp_without_shell_evaluation():
    launcher = (
        ROOT / "examples" / "fakoli-dark" / "q36" / "entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert 'case "${Q36_MTP:-0}" in' in launcher
    assert 'case "${Q36_MTP_DEPTH:-1}" in' in launcher
    assert 'set -- "$@" --mtp "${Q36_MTP_DEPTH:-1}"' in launcher
    assert 'exec /opt/q36/q36_server "$@"' in launcher


def test_q36_container_recipe_pins_source_images_model_and_gpu_boundary():
    recipe = ROOT / "examples" / "fakoli-dark"
    dockerfile = (recipe / "q36" / "Dockerfile").read_text(encoding="utf-8")
    compose = (recipe / "docker-compose.q36.yml").read_text(encoding="utf-8")

    assert "458eb018997565445f0ce0a4887ed7cdfeab756b" in dockerfile
    assert "875f52c5ac52680f212e1fa4b9a232cf722f6eea1d1bdf8af15fedf07785e7dd" in dockerfile
    assert dockerfile.count("FROM nvidia/cuda@sha256:") == 2
    assert '"127.0.0.1:39040:8080"' in compose
    assert '"vllm-hfcache:/root/.cache/huggingface:ro"' in compose
    assert "CUDA_VISIBLE_DEVICES: ${Q36_GPU_INDEX:-1}" in compose
    assert "GPU-d0f446cf-1771-414c-e116-a39138798a8c" in compose
    assert "5bc3e238d916f48a861bac2f8a1990a0e9b7e98d" in compose
    assert "Qwen3.6-35B-A3B-MXFP4_MOE.gguf" in compose
