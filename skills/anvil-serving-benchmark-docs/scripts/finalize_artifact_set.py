#!/usr/bin/env python3
"""Finalize a benchmark artifact-set manifest with exact hashes and sizes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
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


def _json_loads(text: str, *, label: str) -> Any:
    duplicates: list[str] = []

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    value = json.loads(text, object_pairs_hook=object_pairs)
    if duplicates:
        raise ValueError(f"{label}: duplicate JSON keys: {', '.join(duplicates)}")
    return value


def _read(path: Path) -> dict[str, Any]:
    value = _json_loads(path.read_text(encoding="utf-8"), label=str(path))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    _validate_serialized_json(value, label=str(path))
    return value


def _assert_no_symlink(base: Path, relative: str, *, label: str) -> Path:
    current = base
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} cannot be a symlink: {relative}")
    return current


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
    lexical_candidate = _assert_no_symlink(base, posix_path.as_posix(), label=label)
    candidate = lexical_candidate.resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes artifact directory: {relative}") from exc
    return candidate, posix_path.as_posix()


def _validate_serialized_json(value: Any, *, label: str) -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            _validate_serialized_json(child, label=label)
        return
    if isinstance(value, list):
        for child in value:
            _validate_serialized_json(child, label=label)
        return
    if isinstance(value, str):
        stripped = value.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                nested = _json_loads(stripped, label=f"{label} serialized value")
            except json.JSONDecodeError:
                return
            _validate_serialized_json(nested, label=label)


def _legacy_plaintext_files(source: dict[str, Any], base: Path) -> set[str]:
    declared = source.get("legacy_plaintext_files", [])
    if not isinstance(declared, list):
        raise ValueError("legacy_plaintext_files must be an array")
    result: set[str] = set()
    for item in declared:
        if not isinstance(item, dict) or set(item) != {"path", "reason"}:
            raise ValueError("each legacy plaintext file needs path and reason")
        relative = item["path"]
        reason = item["reason"]
        if not isinstance(relative, str) or not isinstance(reason, str) or not reason.strip():
            raise ValueError("legacy plaintext file path and reason must be non-empty strings")
        path, normalized = _relative_child(base, relative, label="legacy plaintext file")
        if path.suffix.lower() != ".json":
            raise ValueError("legacy plaintext file must use the .json suffix")
        if normalized in result:
            raise ValueError(f"legacy plaintext file is declared more than once: {normalized}")
        result.add(normalized)
    return result


def _validate_artifact_payload(payload: bytes, path: Path, relative: str, legacy_plaintext: set[str]) -> None:
    if path.suffix.lower() != ".json":
        return
    text = payload.decode("utf-8")
    try:
        value = _json_loads(text, label=relative)
    except json.JSONDecodeError:
        if relative in legacy_plaintext:
            return
        raise ValueError(
            f"{relative}: .json artifact is malformed; declare an explicit legacy plaintext compatibility exception if applicable"
        ) from None
    if relative in legacy_plaintext:
        raise ValueError(f"{relative}: legacy plaintext exception is unnecessary")
    _validate_serialized_json(value, label=relative)


def _assert_closed_inventory(
    base: Path, *, source_path: Path, output_path: Path, declared: set[str]
) -> None:
    ignored = {source_path.resolve(), output_path.resolve()}
    for path in base.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"artifact directory cannot contain a symlink: {path.relative_to(base)}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"artifact is not a regular file: {path.relative_to(base)}")
        if path.resolve() in ignored:
            continue
        relative = path.relative_to(base).as_posix()
        if relative not in declared:
            raise ValueError(f"closed inventory has undeclared artifact: {relative}")


def _assert_output_target(path: Path) -> None:
    if path.suffix.lower() != ".json":
        raise ValueError(f"output must use the .json suffix: {path.name}")
    if not path.exists():
        return
    if path.is_symlink():
        raise ValueError(f"output target cannot be a symlink: {path}")
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
    if source_path.is_symlink():
        raise ValueError(f"source cannot be a symlink: {source_path}")
    source_path = source_path.resolve()
    snapshots = {source_path: source_path.stat()}
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
    legacy_plaintext = _legacy_plaintext_files(source, base)
    if source.get("closed_inventory", True) is not True:
        raise ValueError("closed_inventory must be true")

    roles = source.get("artifact_roles")
    if not isinstance(roles, list):
        raise ValueError("artifact_roles must be an array")
    actual_roles = tuple(item.get("role") for item in roles if isinstance(item, dict))
    if actual_roles != ROLES:
        raise ValueError(f"artifact roles must be exactly: {', '.join(ROLES)}")

    finalized_roles: list[dict[str, Any]] = []
    declared_files: set[str] = set()
    for item in roles:
        status = item.get("status")
        files = item.get("files")
        reason = item.get("reason")
        if status not in STATUSES:
            raise ValueError(f"invalid role status: {status}")
        if status == "pending":
            raise ValueError(f"final artifact set cannot retain pending role: {item['role']}")
        if not isinstance(files, list) or not all(isinstance(p, str) for p in files):
            raise ValueError("role files must be an array of relative strings")
        if not isinstance(reason, str):
            raise ValueError("each role needs a reason")
        if status == "retained" and not files:
            raise ValueError(f"retained role has no files: {item['role']}")
        if status != "retained" and files:
            raise ValueError(f"non-retained role has files: {item['role']}")
        entries = []
        role_files: set[str] = set()
        for relative in files:
            path, normalized_relative = _relative_child(
                base, relative, label="role file"
            )
            if path == output_path:
                raise ValueError("artifact manifest cannot hash itself")
            if not path.is_file():
                raise ValueError(f"role file is not a regular file: {normalized_relative}")
            if normalized_relative in role_files:
                raise ValueError(
                    f"artifact path is declared more than once in role {item['role']}: "
                    f"{normalized_relative}"
                )
            role_files.add(normalized_relative)
            declared_files.add(normalized_relative)
            metadata = path.stat()
            if max_artifact_bytes is not None and metadata.st_size >= max_artifact_bytes:
                raise ValueError(f"retained artifact violates size_policy: {normalized_relative}")
            snapshots.setdefault(path, metadata)
            with path.open("rb") as handle:
                payload = handle.read(max_artifact_bytes + 1 if max_artifact_bytes is not None else -1)
            _validate_artifact_payload(payload, path, normalized_relative, legacy_plaintext)
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

    if not legacy_plaintext.issubset(declared_files):
        missing = ", ".join(sorted(legacy_plaintext - declared_files))
        raise ValueError(f"legacy plaintext file is not retained: {missing}")
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
    for path, before in snapshots.items():
        after = path.lstat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ):
            raise ValueError(f"artifact changed during finalization: {path.name}")
    _assert_closed_inventory(
        base,
        source_path=source_path,
        output_path=output_path,
        declared=declared_files,
    )
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=output_path.parent,
            prefix=f".{output_path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(output, handle, indent=2, sort_keys=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
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
