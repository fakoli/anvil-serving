from pathlib import Path

from anvil_serving import serves


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "anvil-serving-stt-benchmark" / "SKILL.md"
REFERENCE = SKILL.parent / "references" / "corpus-and-evidence.md"
WRAPPER = ROOT / ".agents" / "skills" / "anvil-serving-stt-benchmark" / "SKILL.md"
OPENAI_YAML = SKILL.parent / "agents" / "openai.yaml"
COMPOSE = ROOT / "examples" / "fakoli-dark" / "docker-compose.stt-experiment.yml"
MANIFEST = ROOT / "examples" / "fakoli-dark" / "serves.stt-experiment.toml"
NEMOTRON_DOCKERFILE = (
    ROOT / "examples" / "fakoli-dark" / "stt-experiments" / "nemotron" / "Dockerfile"
)
NEMOTRON_PATCH = NEMOTRON_DOCKERFILE.parent / "patch_transformers_transcription.py"
QWEN_DOCKERFILE = (
    ROOT / "examples" / "fakoli-dark" / "stt-experiments" / "qwen" / "Dockerfile"
)
QWEN_PATCH = QWEN_DOCKERFILE.parent / "patch_vllm_qwen3_asr_transcription.py"


def test_stt_skill_routes_to_real_fail_closed_cli_commands():
    text = SKILL.read_text(encoding="utf-8")
    reference = REFERENCE.read_text(encoding="utf-8")
    for command in (
        "anvil-serving voice corpus prepare",
        "anvil-serving voice corpus validate",
        "anvil-serving voice benchmark --scope stt",
    ):
        assert command in text
    assert "stt-benchmark-evidence/v1" in text
    assert "complete=true" in text
    assert "summary.primary_human" in text
    assert "Fakoli Mini remains" in text
    assert "Do not" in text or "do not" in text
    assert "stt-corpus/v1" in reference
    assert "16-kHz mono WAV or FLAC" in reference


def test_stt_skill_wrapper_is_thin_and_points_at_canonical_skill():
    text = WRAPPER.read_text(encoding="utf-8")
    assert "../../../skills/anvil-serving-stt-benchmark/SKILL.md" in text
    assert len(text.splitlines()) < 20
    assert "$anvil-serving-stt-benchmark" in OPENAI_YAML.read_text(encoding="utf-8")


def test_stt_experiment_serves_are_loopback_only_and_pinned():
    text = COMPOSE.read_text(encoding="utf-8")
    assert '"127.0.0.1:39041:8000"' in text
    assert '"127.0.0.1:39042:8000"' in text
    assert "127.0.0.1:30010" not in text
    assert "f3d333391852ba876df169dcc9ba902d25b6ab0b" in text
    assert "5eb144179a02acc5e5ba31e748d22b0cf3e303b0" in text
    assert "transformers serve" in text
    assert "qwenllm/qwen3-asr@sha256:" in text
    assert "--max-model-len 16384" in text
    assert text.count("NVIDIA_VISIBLE_DEVICES: ${AUXILIARY_GPU_UUID") == 2
    assert "HF_TOKEN" not in text
    entries = serves.load_manifest(str(MANIFEST))
    assert [(item["name"], item["port"], item["engine"]) for item in entries] == [
        ("nemotron35-asr", 39041, "audio"),
        ("qwen3-asr-0.6b", 39042, "audio"),
    ]


def test_stt_experiment_runtime_patches_are_version_pinned_and_fail_closed():
    nemotron_dockerfile = NEMOTRON_DOCKERFILE.read_text(encoding="utf-8")
    nemotron_patch = NEMOTRON_PATCH.read_text(encoding="utf-8")
    qwen_dockerfile = QWEN_DOCKERFILE.read_text(encoding="utf-8")
    qwen_patch = QWEN_PATCH.read_text(encoding="utf-8")
    assert "transformers[serving]==${TRANSFORMERS_VERSION}" in nemotron_dockerfile
    assert "python-multipart==0.0.32" in nemotron_dockerfile
    assert "getattr(generation_output" in nemotron_patch
    assert r"\"sequences\"" in nemotron_patch
    assert "count != 1" in nemotron_patch
    assert "qwenllm/qwen3-asr@sha256:" in qwen_dockerfile
    assert "parse_asr_output" in qwen_patch
    assert "count != 1" in qwen_patch
