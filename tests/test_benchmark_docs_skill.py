import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_SET_TEMPLATES = (
    ROOT / "skills" / "anvil-serving-benchmark-docs" / "templates" / "artifact-set"
)
FINALIZER_PATH = (
    ROOT / "skills" / "anvil-serving-benchmark-docs" / "scripts" / "finalize_artifact_set.py"
)


def _finalizer_module():
    spec = importlib.util.spec_from_file_location("benchmark_finalizer", FINALIZER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_source(*, files: list[str], **extra: object) -> dict:
    roles = [
        {
            "role": role,
            "status": "retained" if index == 0 else "missing",
            "files": files if index == 0 else [],
            "reason": "" if index == 0 else "not retained for focused fixture",
        }
        for index, role in enumerate(
            (
                "evidence-index",
                "source-registry",
                "workload-manifest",
                "run-plan",
                "configuration-and-identity",
                "raw-run-evidence",
                "failures-and-friction",
                "restoration",
                "decision-summary",
                "publication-summary",
            )
        )
    ]
    return {
        "schema": "anvil-serving.benchmark-artifact-set-source/v1",
        "output": "artifact-manifest.json",
        "campaign": {"promotion_authorized": False},
        "native_evidence_schemas": [],
        "artifact_roles": roles,
        **extra,
    }


def _write_finalizer_source(tmp_path: Path, source: dict) -> Path:
    path = tmp_path / "artifact-manifest-source.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    return path


def test_finalizer_rejects_symlink_and_duplicate_serialized_json_keys(tmp_path: Path):
    target = tmp_path / "target.txt"
    target.write_text("retained", encoding="utf-8")
    os.symlink(target, tmp_path / "evidence.txt")
    with pytest.raises(ValueError, match="symlink"):
        _finalizer_module().finalize(
            _write_finalizer_source(tmp_path, _manifest_source(files=["evidence.txt"]))
        )

    (tmp_path / "evidence.txt").unlink()
    (tmp_path / "evidence.json").write_text(
        '{"content": "{\\"metric\\": 1, \\"metric\\": 2}"}', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="duplicate JSON keys"):
        _finalizer_module().finalize(
            _write_finalizer_source(tmp_path, _manifest_source(files=["evidence.json"]))
        )


def test_artifact_set_templates_keep_promotion_closed_and_roles_pending():
    manifest = json.loads(
        (ARTIFACT_SET_TEMPLATES / "artifact-manifest.json").read_text(encoding="utf-8")
    )
    campaign_state = json.loads(
        (ARTIFACT_SET_TEMPLATES / "campaign-state.json").read_text(encoding="utf-8")
    )
    summary = json.loads(
        (ARTIFACT_SET_TEMPLATES / "summary.json").read_text(encoding="utf-8")
    )

    assert manifest["campaign"]["promotion_authorized"] is False
    assert campaign_state["promotion_authorized"] is False
    assert summary["decision"]["promotion_authorized"] is False
    assert [role["role"] for role in manifest["artifact_roles"]] == [
        "evidence-index",
        "source-registry",
        "workload-manifest",
        "run-plan",
        "configuration-and-identity",
        "raw-run-evidence",
        "failures-and-friction",
        "restoration",
        "decision-summary",
        "publication-summary",
    ]
    assert all(role["status"] == "pending" and role["files"] == [] for role in manifest["artifact_roles"])
