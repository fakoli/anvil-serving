#!/usr/bin/env python3
"""Finalize a benchmark artifact-set manifest with exact hashes and sizes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


SOURCE_SCHEMA = "anvil-serving.benchmark-artifact-set-source/v1"
OUTPUT_SCHEMA = "anvil-serving.benchmark-artifact-set/v1"
ROLES = (
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
STATUSES = {"retained", "not-applicable", "missing", "pending"}
SIZE_POLICY_PATTERN = re.compile(
    r"each retained public artifact is under (?P<mib>[1-9][0-9]*) MiB"
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _relative_child(base: Path, relative: str, *, label: str) -> tuple[Path, str]:
    normalized = relative.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(relative)
    if (
        not relative
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(part in {".", ".."} for part in normalized.split("/"))
    ):
        raise ValueError(f"{label} must be a canonical relative path: {relative}")
    candidate = (base / Path(*posix_path.parts)).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes artifact directory: {relative}") from exc
    return candidate, posix_path.as_posix()


def _assert_output_target(path: Path) -> None:
    if path.suffix.lower() != ".json":
        raise ValueError(f"output must use the .json suffix: {path.name}")
    if not path.exists():
        return
    if not path.is_file():
        raise ValueError(f"output target is not a file: {path}")
    existing = _read(path)
    if existing.get("schema") != OUTPUT_SCHEMA:
        raise ValueError(f"refusing to overwrite an unmarked output file: {path}")


def _size_limit(source: dict[str, Any]) -> tuple[bool, str | None, int | None]:
    verified = source.get("bundle_size_policy_verified", False)
    if not isinstance(verified, bool):
        raise ValueError("bundle_size_policy_verified must be a boolean")
    size_policy = source.get("size_policy")
    if size_policy is not None and not isinstance(size_policy, str):
        raise ValueError("size_policy must be a string or null")
    if not verified:
        return verified, size_policy, None
    match = SIZE_POLICY_PATTERN.fullmatch(size_policy or "")
    if match is None:
        raise ValueError(
            "verified size_policy must use: "
            "'each retained public artifact is under <positive integer> MiB'"
        )
    return verified, size_policy, int(match.group("mib")) * 1024 * 1024


def finalize(source_path: Path) -> tuple[Path, dict[str, Any]]:
    source_path = source_path.resolve()
    source = _read(source_path)
    if source.get("schema") != SOURCE_SCHEMA:
        raise ValueError(f"source schema must be {SOURCE_SCHEMA}")
    base = source_path.parent
    output_name = source.get("output", "artifact-manifest.json")
    if not isinstance(output_name, str) or not output_name:
        raise ValueError("output must be a non-empty relative string")
    output_path, _ = _relative_child(base, output_name, label="output")
    if output_path == source_path:
        raise ValueError("source and output paths must differ")
    size_policy_verified, size_policy, max_artifact_bytes = _size_limit(source)

    roles = source.get("artifact_roles")
    if not isinstance(roles, list):
        raise ValueError("artifact_roles must be an array")
    actual_roles = tuple(item.get("role") for item in roles if isinstance(item, dict))
    if actual_roles != ROLES:
        raise ValueError(f"artifact roles must be exactly: {', '.join(ROLES)}")

    finalized_roles: list[dict[str, Any]] = []
    for item in roles:
        status = item.get("status")
        files = item.get("files")
        reason = item.get("reason")
        if status not in STATUSES:
            raise ValueError(f"invalid role status: {status}")
        if not isinstance(files, list) or not all(isinstance(p, str) for p in files):
            raise ValueError("role files must be an array of relative strings")
        if not isinstance(reason, str):
            raise ValueError("each role needs a reason")
        if status == "retained" and not files:
            raise ValueError(f"retained role has no files: {item['role']}")
        if status != "retained" and files:
            raise ValueError(f"non-retained role has files: {item['role']}")
        entries = []
        for relative in files:
            path, normalized_relative = _relative_child(
                base, relative, label="role file"
            )
            if path == output_path:
                raise ValueError("artifact manifest cannot hash itself")
            payload = path.read_bytes()
            if max_artifact_bytes is not None and len(payload) >= max_artifact_bytes:
                raise ValueError(
                    f"retained artifact violates size_policy: {normalized_relative} "
                    f"is {len(payload)} bytes; limit is under {max_artifact_bytes} bytes"
                )
            entries.append(
                {
                    "path": normalized_relative,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                }
            )
        finalized_roles.append(
            {
                "role": item["role"],
                "status": status,
                "files": entries,
                "reason": reason,
            }
        )

    _assert_output_target(output_path)

    output = {
        "schema": OUTPUT_SCHEMA,
        "campaign": source.get("campaign"),
        "native_evidence_schemas": source.get("native_evidence_schemas", []),
        "artifact_roles": finalized_roles,
        "bundle_size_policy_verified": size_policy_verified,
        "size_policy": size_policy,
        "generated_from": source_path.name,
    }
    output_path.write_text(
        json.dumps(output, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return output_path, output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hash and size every file in a benchmark artifact-set source."
    )
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    output, manifest = finalize(args.source)
    print(
        json.dumps(
            {
                "output": str(output),
                "roles": len(manifest["artifact_roles"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
