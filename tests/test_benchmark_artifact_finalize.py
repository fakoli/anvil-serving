import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "skills"
    / "anvil-serving-benchmark-docs"
    / "scripts"
    / "finalize_artifact_set.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("artifact_finalize", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source(
    tmp_path: Path,
    artifact_path: str = "evidence.txt",
    *,
    output: str = "artifact-manifest.json",
    size_policy: str = "each retained public artifact is under 5 MiB",
    size_policy_verified: bool = True,
) -> Path:
    module = _module()
    artifact = Path(artifact_path)
    if not artifact.is_absolute() and ".." not in artifact.parts:
        artifact = tmp_path / artifact
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("evidence\n", encoding="utf-8")
    else:
        (tmp_path / "evidence.txt").write_text("evidence\n", encoding="utf-8")
    roles = []
    for role in module.ROLES:
        roles.append(
            {
                "role": role,
                "status": "retained",
                "files": [artifact_path],
                "reason": "fixture",
            }
        )
    source = tmp_path / "artifact-manifest-source.json"
    source.write_text(
        json.dumps(
            {
                "schema": module.SOURCE_SCHEMA,
                "output": output,
                "campaign": {"promotion_authorized": False},
                "native_evidence_schemas": ["fixture/v1"],
                "artifact_roles": roles,
                "bundle_size_policy_verified": size_policy_verified,
                "size_policy": size_policy,
            }
        ),
        encoding="utf-8",
    )
    return source


def test_finalizer_hashes_files_and_is_deterministic(tmp_path):
    module = _module()
    source = _source(tmp_path)

    output, manifest = module.finalize(source)
    first_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    module.finalize(source)

    assert first_hash == hashlib.sha256(output.read_bytes()).hexdigest()
    assert manifest["schema"] == "anvil-serving.benchmark-artifact-set/v1"
    assert manifest["generated_from"] == source.name
    item = manifest["artifact_roles"][0]["files"][0]
    assert item["bytes"] == len((tmp_path / "evidence.txt").read_bytes())
    assert len(item["sha256"]) == 64


def test_finalizer_rejects_paths_outside_artifact_directory(tmp_path):
    module = _module()
    source = _source(tmp_path, "../outside.txt")

    with pytest.raises(ValueError, match="canonical relative path"):
        module.finalize(source)


def test_finalizer_rejects_absolute_in_base_artifact_path(tmp_path):
    module = _module()
    source = _source(tmp_path, str(tmp_path / "evidence.txt"))

    with pytest.raises(ValueError, match="canonical relative path"):
        module.finalize(source)


def test_finalizer_rejects_absolute_in_base_output_path(tmp_path):
    module = _module()
    source = _source(tmp_path, output=str(tmp_path / "artifact-manifest.json"))

    with pytest.raises(ValueError, match="canonical relative path"):
        module.finalize(source)


def test_finalizer_rejects_source_as_output(tmp_path):
    module = _module()
    source = _source(tmp_path, output="artifact-manifest-source.json")

    with pytest.raises(ValueError, match="source and output paths must differ"):
        module.finalize(source)


def test_finalizer_rejects_retained_artifact_as_output(tmp_path):
    module = _module()
    source = _source(
        tmp_path,
        artifact_path="evidence.json",
        output="evidence.json",
    )

    with pytest.raises(ValueError, match="cannot hash itself"):
        module.finalize(source)


def test_finalizer_rejects_unmarked_existing_output(tmp_path):
    module = _module()
    source = _source(tmp_path, output="existing.json")
    (tmp_path / "existing.json").write_text(
        json.dumps({"schema": "unrelated/v1"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="unmarked output file"):
        module.finalize(source)


def test_finalizer_rejects_output_without_json_suffix(tmp_path):
    module = _module()
    source = _source(tmp_path, output="artifact-manifest.txt")

    with pytest.raises(ValueError, match="must use the .json suffix"):
        module.finalize(source)


def test_finalizer_enforces_declared_size_policy(tmp_path):
    module = _module()
    source = _source(
        tmp_path,
        size_policy="each retained public artifact is under 1 MiB",
    )
    with (tmp_path / "evidence.txt").open("wb") as stream:
        stream.truncate(1024 * 1024)

    with pytest.raises(ValueError, match="violates size_policy"):
        module.finalize(source)


def test_finalizer_rejects_unenforceable_verified_size_policy(tmp_path):
    module = _module()
    source = _source(tmp_path, size_policy="fixture")

    with pytest.raises(ValueError, match="verified size_policy must use"):
        module.finalize(source)


def test_qwen_pro6000_campaign_manifest_hashes_match_retained_files():
    evidence_dir = (
        ROOT
        / "docs"
        / "findings"
        / "2026-09-04-qwen38-27b-pro6000-possibility-evidence"
    )
    manifest = json.loads(
        (evidence_dir / "artifact-manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["schema"] == "anvil-serving.benchmark-artifact-set/v1"
    assert manifest["campaign"]["promotion_authorized"] is False
    assert [item["role"] for item in manifest["artifact_roles"]] == list(
        _module().ROLES
    )
    assert all(item["status"] != "pending" for item in manifest["artifact_roles"])
    for role in manifest["artifact_roles"]:
        for item in role["files"]:
            path = evidence_dir / item["path"]
            payload = path.read_bytes()
            assert len(payload) == item["bytes"]
            assert hashlib.sha256(payload).hexdigest() == item["sha256"]
