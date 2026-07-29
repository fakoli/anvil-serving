import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "anvil-serving-kernel-tuning" / "SKILL.md"
REFERENCE = SKILL.parent / "references" / "tune-contract.md"
WRAPPER = ROOT / ".agents" / "skills" / "anvil-serving-kernel-tuning" / "SKILL.md"
OPENAI_YAML = SKILL.parent / "agents" / "openai.yaml"
AGENTS = ROOT / "AGENTS.md"
TUNES = ROOT / "configs" / "kernel-tunes"
GITATTRIBUTES = ROOT / ".gitattributes"


def test_kernel_tuning_skill_requires_paired_end_to_end_evidence():
    text = SKILL.read_text(encoding="utf-8")
    for phrase in (
        "default and tuned",
        "three warmed",
        "end-to-end",
        "warning disappeared",
        "Promotion remains separately human-gated",
        "do not restart it",
    ):
        assert phrase in text
    assert "skill-local tuner scripts" in text
    assert not (SKILL.parent / "scripts").exists()


def test_kernel_tune_contract_pins_storage_identity_and_activation():
    text = REFERENCE.read_text(encoding="utf-8")
    for phrase in (
        "configs/kernel-tunes/<engine>/<engine-revision>/<gpu-slug>/",
        "kernel-tune-manifest/v1",
        "VLLM_TUNED_CONFIG_FOLDER",
        "Storage is inert",
        "tensor-parallel size",
        "SHA-256",
        "short portable repository filename",
        "exact engine-required runtime filename",
    ):
        assert phrase in text


def test_kernel_tuning_discovery_surfaces_point_to_canonical_skill():
    wrapper = WRAPPER.read_text(encoding="utf-8")
    agents = AGENTS.read_text(encoding="utf-8")
    metadata = OPENAI_YAML.read_text(encoding="utf-8")
    assert "../../../skills/anvil-serving-kernel-tuning/SKILL.md" in wrapper
    assert len(wrapper.splitlines()) < 20
    assert "skills/anvil-serving-kernel-tuning/SKILL.md" in agents
    assert "configs/kernel-tunes/<engine>/<engine-revision>/<gpu-slug>/" in agents
    assert "$anvil-serving-kernel-tuning" in metadata


def test_kernel_tune_artifacts_are_byte_bound_and_windows_checkout_safe():
    assert "configs/kernel-tunes/** -text" in GITATTRIBUTES.read_text(encoding="utf-8")
    manifests = list(TUNES.glob("*/*/*/manifest.json"))
    assert manifests
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifact = ROOT / manifest["artifact"]["path"]
        relative = artifact.relative_to(ROOT).as_posix()
        assert len(relative) <= 220, relative
        assert artifact.name == manifest["artifact"]["repository_filename"]
        assert artifact.name != manifest["artifact"]["engine_required_filename"]
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == manifest["artifact"]["sha256"]
