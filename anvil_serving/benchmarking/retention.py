"""Immutable, bounded retention of canonical SWE work artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any

from .artifacts import atomic_write_json, evidence_file_reference


SWE_CANONICAL_RETENTION_SCHEMA = "anvil-serving.swe-canonical-retention/v1"
MAX_SWE_RETAINED_BYTES = 64 * 1024 * 1024
MAX_SWE_RETAINED_FILES = 512
MAX_SWE_RETAINED_MANIFEST_BYTES = 256 * 1024

# The plan is intentionally a work artifact: it captures exact generated command/config
# bytes before execution.  All entries are optional so failed/cancelled runs remain useful.
_CANONICAL_PATHS = (
    "swe-plan.json",
    "anvil-swe-config.yaml",
    "swe-prompt-contract.yaml",
    "predictions.jsonl",
    "mini-output",
    "official-grader",
    "paired-image-ids.json",
)


def _digest(path: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _safe_file(root: str, relative: str) -> str | None:
    if not isinstance(relative, str) or not relative or os.path.isabs(relative):
        return None
    parts = relative.replace("\\", "/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    current = root
    for part in parts:
        current = os.path.join(current, part)
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                return None
        except OSError:
            return None
    return current


def read_retained_swe_artifacts(*, run_path: str) -> dict[str, Any]:
    """Read one bounded immutable canonical snapshot and verify every byte."""
    destination = _safe_file(run_path, "evidence/swe-canonical")
    if destination is None or not stat.S_ISDIR(os.lstat(destination).st_mode):
        raise ValueError("canonical_destination_invalid")
    manifest = _safe_file(destination, "manifest.json")
    if manifest is None:
        raise ValueError("canonical_manifest_invalid")
    try:
        if not stat.S_ISREG(os.lstat(manifest).st_mode):
            raise ValueError("canonical_manifest_invalid")
        with open(manifest, "rb") as handle:
            raw = handle.read(MAX_SWE_RETAINED_MANIFEST_BYTES + 1)
        if len(raw) > MAX_SWE_RETAINED_MANIFEST_BYTES:
            raise ValueError("canonical_manifest_oversized")
        value = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        raise ValueError("canonical_manifest_invalid") from None
    if (not isinstance(value, dict) or value.get("schema") != SWE_CANONICAL_RETENTION_SCHEMA
            or value.get("state") not in {"retained", "incomplete"}
            or not isinstance(value.get("files"), list) or not isinstance(value.get("missing"), list)
            or type(value.get("bytes")) is not int or value["bytes"] < 0 or value.get("source") != "work"):
        raise ValueError("canonical_manifest_invalid")
    if len(value["files"]) > MAX_SWE_RETAINED_FILES or len(value["missing"]) > len(_CANONICAL_PATHS):
        raise ValueError("canonical_retention_bound_exceeded")
    seen: set[str] = set()
    total = 0
    for entry in value["files"]:
        if (not isinstance(entry, dict) or set(entry) != {"path", "bytes", "sha256"}
                or type(entry["bytes"]) is not int or entry["bytes"] < 0
                or not isinstance(entry["sha256"], str)):
            raise ValueError("canonical_manifest_invalid")
        path = entry["path"]
        if not isinstance(path, str) or path in seen or _safe_file(destination, path) is None:
            raise ValueError("canonical_manifest_invalid")
        seen.add(path)
        target = _safe_file(destination, path)
        if (target is None or not stat.S_ISREG(os.lstat(target).st_mode)
                or os.lstat(target).st_size != entry["bytes"]):
            raise ValueError("canonical_manifest_invalid")
        if total + entry["bytes"] > MAX_SWE_RETAINED_BYTES or _digest(target) != (entry["bytes"], entry["sha256"]):
            raise ValueError("canonical_retention_bound_exceeded")
        total += entry["bytes"]
        if total > MAX_SWE_RETAINED_BYTES:
            raise ValueError("canonical_retention_bound_exceeded")
    if total != value["bytes"]:
        raise ValueError("canonical_manifest_invalid")
    return value


def _existing(destination: str) -> dict[str, Any] | None:
    try:
        return read_retained_swe_artifacts(run_path=os.path.dirname(os.path.dirname(destination)))
    except ValueError:
        return None


def _copy_file(source: str, target: str, files: list[dict[str, Any]], total: list[int], root: str) -> None:
    info = os.lstat(source)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("nonregular_file")
    if len(files) >= MAX_SWE_RETAINED_FILES or total[0] + info.st_size > MAX_SWE_RETAINED_BYTES:
        raise ValueError("retention_bound_exceeded")
    relative = os.path.relpath(target, root).replace("\\", "/")
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    size, digest = _digest(target)
    if len(files) >= MAX_SWE_RETAINED_FILES or total[0] + size > MAX_SWE_RETAINED_BYTES:
        raise ValueError("retention_bound_exceeded")
    files.append({"path": relative, "bytes": size, "sha256": digest})
    total[0] += size


def _copy_tree(source: str, target: str, files: list[dict[str, Any]], total: list[int], root: str) -> None:
    info = os.lstat(source)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("non_directory")
    Path(target).mkdir(parents=True, exist_ok=True)
    for current, directories, names in os.walk(source, followlinks=False):
        directories.sort()
        names.sort()
        for name in directories:
            if not stat.S_ISDIR(os.lstat(os.path.join(current, name)).st_mode):
                raise ValueError("nonregular_directory")
        for name in names:
            original = os.path.join(current, name)
            relative = os.path.relpath(original, source)
            _copy_file(original, os.path.join(target, relative), files, total, root)


def retain_swe_artifacts(*, run_path: str, work_path: str) -> dict[str, Any]:
    """Publish one verified SWE snapshot, without ever replacing prior evidence."""
    destination = os.path.join(run_path, "evidence", "swe-canonical")
    existing = _existing(destination) if os.path.isdir(destination) and not os.path.islink(destination) else None
    if existing is not None:
        return {
            "state": existing.get("state", "retained"), "manifest": existing,
            "manifest_reference": evidence_file_reference(
                os.path.join(destination, "manifest.json"), root=run_path
            ), "reused": True,
        }
    if os.path.lexists(destination):
        return {"state": "failed", "reason": "canonical_destination_invalid"}
    if not os.path.isdir(work_path) or os.path.islink(work_path):
        return {"state": "failed", "reason": "work_path_invalid"}
    evidence = os.path.dirname(destination)
    Path(evidence).mkdir(parents=True, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".swe-canonical-", dir=evidence)
    files: list[dict[str, Any]] = []
    missing: list[str] = []
    total = [0]
    try:
        for relative in _CANONICAL_PATHS:
            source = os.path.join(work_path, relative)
            if not os.path.lexists(source):
                missing.append(relative)
                continue
            target = os.path.join(staging, relative)
            mode = os.lstat(source).st_mode
            if stat.S_ISREG(mode):
                _copy_file(source, target, files, total, staging)
            elif stat.S_ISDIR(mode):
                _copy_tree(source, target, files, total, staging)
            else:
                raise ValueError("nonregular_source")
        manifest = {
            "schema": SWE_CANONICAL_RETENTION_SCHEMA,
            "state": "retained" if not missing else "incomplete",
            "files": files,
            "missing": missing,
            "bytes": total[0],
            "source": "work",
        }
        atomic_write_json(os.path.join(staging, "manifest.json"), manifest)
        # The destination did not exist when checked above.  Never replace it if
        # another publisher won the race while this snapshot was being copied.
        if os.path.lexists(destination):
            winner = _existing(destination) if os.path.isdir(destination) else None
            return ({"state": winner.get("state", "retained"), "manifest": winner,
                     "manifest_reference": evidence_file_reference(os.path.join(destination, "manifest.json"), root=run_path), "reused": True}
                    if winner is not None else {"state": "failed", "reason": "canonical_destination_exists"})
        os.replace(staging, destination)
        staging = ""
        return {
            "state": manifest["state"], "manifest": manifest,
            "manifest_reference": evidence_file_reference(
                os.path.join(destination, "manifest.json"), root=run_path
            ), "reused": False,
        }
    except (OSError, ValueError) as exc:
        return {"state": "failed", "reason": str(exc) or type(exc).__name__}
    finally:
        if staging and os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)
