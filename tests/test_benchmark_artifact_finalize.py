import importlib.util
import json
from pathlib import Path


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
