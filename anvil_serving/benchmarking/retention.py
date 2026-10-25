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
MAX_SWE_RETAINED_DIRECTORIES = 512
MAX_SWE_RETAINED_DEPTH = 32
MAX_SWE_RETAINED_MANIFEST_BYTES = 256 * 1024

# The plan is intentionally a work artifact: it captures exact generated command/config
# bytes before execution. Partial snapshots remain useful for failed/cancelled runs.
_CANONICAL_PATHS = (
    "swe-plan.json",
    "anvil-swe-config.yaml",
    "swe-prompt-contract.yaml",
    "predictions.jsonl",
    "mini-output",
    "official-grader",
    "paired-image-ids.json",
)


def _digest(path: str, *, max_bytes: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(min(1024 * 1024, max_bytes - size + 1)):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("canonical_retention_bound_exceeded")
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
    try:
        return _read_retained_swe_artifacts(run_path=run_path)
    except OSError:
        raise ValueError("canonical_manifest_unreadable") from None


def _read_retained_swe_artifacts(*, run_path: str) -> dict[str, Any]:
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
            or value.get("state") not in ("retained", "incomplete")
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
        if total + entry["bytes"] > MAX_SWE_RETAINED_BYTES or _digest(target, max_bytes=entry["bytes"]) != (entry["bytes"], entry["sha256"]):
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
    digest = hashlib.sha256()
    size = 0
    with open(source, "rb") as original, open(target, "xb") as copied:
        while chunk := original.read(min(1024 * 1024, MAX_SWE_RETAINED_BYTES - total[0] - size + 1)):
            size += len(chunk)
            if total[0] + size > MAX_SWE_RETAINED_BYTES:
                raise ValueError("retention_bound_exceeded")
            copied.write(chunk)
            digest.update(chunk)
    files.append({"path": relative, "bytes": size, "sha256": digest.hexdigest()})
    total[0] += size


def _copy_tree(source: str, target: str, files: list[dict[str, Any]], total: list[int], root: str, *, depth: int = 0, directories: list[int] | None = None) -> None:
    info = os.lstat(source)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("non_directory")
    if depth > MAX_SWE_RETAINED_DEPTH:
        raise ValueError("retention_depth_exceeded")
    directories = [0] if directories is None else directories
    directories[0] += 1
    if directories[0] > MAX_SWE_RETAINED_DIRECTORIES:
        raise ValueError("retention_directory_bound_exceeded")
    Path(target).mkdir(parents=True, exist_ok=True)
    with os.scandir(source) as entries:
        for entry in entries:
            mode = entry.stat(follow_symlinks=False).st_mode
            child = os.path.join(source, entry.name)
            copied = os.path.join(target, entry.name)
            if stat.S_ISDIR(mode):
                _copy_tree(child, copied, files, total, root, depth=depth + 1, directories=directories)
            else:
                _copy_file(child, copied, files, total, root)


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
    total = [0]
    directories = [0]
    try:
        for relative in _CANONICAL_PATHS:
            source = os.path.join(work_path, relative)
            if not os.path.lexists(source):
                continue
            target = os.path.join(staging, relative)
            mode = os.lstat(source).st_mode
            if relative in {"mini-output", "official-grader"} and not stat.S_ISDIR(mode):
                raise ValueError("non_directory")
            if relative not in {"mini-output", "official-grader"} and not stat.S_ISREG(mode):
                raise ValueError("nonregular_source")
            if stat.S_ISREG(mode):
                _copy_file(source, target, files, total, staging)
            elif stat.S_ISDIR(mode):
                _copy_tree(source, target, files, total, staging, directories=directories)
            else:
                raise ValueError("nonregular_source")
        required = {"swe-plan.json", "anvil-swe-config.yaml", "predictions.jsonl", "mini-output", "official-grader"}
        present = {entry["path"] for entry in files if entry["bytes"] > 0}
        plan_value = {}
        if "swe-plan.json" in present:
            plan_value = json.loads(Path(os.path.join(staging, "swe-plan.json")).read_text(encoding="utf-8"))
            if not isinstance(plan_value, dict) or plan_value.get("prompt_contract", "legacy/v1") not in ("legacy/v1", "disclosed/v2"):
                raise ValueError("canonical_plan_invalid")
        if plan_value.get("prompt_contract") == "disclosed/v2":
            required.add("swe-prompt-contract.yaml")
        if plan_value.get("paired_image_ids") is not None:
            required.add("paired-image-ids.json")
        if any(path.startswith("mini-output/") and (path.endswith(".traj.json") or path.endswith("/trajectory.json")) for path in present):
            present.add("mini-output")
        if any(path.startswith("official-grader/") and path.count("/") == 1
               and path.endswith(f".{plan_value.get('run_id')}.json") for path in present):
            present.add("official-grader")
        missing = sorted(required - present)
        manifest = {
            "schema": SWE_CANONICAL_RETENTION_SCHEMA,
            "state": "incomplete" if missing else "retained",
            "files": files,
            "missing": missing,
            "bytes": total[0],
            "source": "work",
        }
        atomic_write_json(os.path.join(staging, "manifest.json"), manifest)
        if os.path.getsize(os.path.join(staging, "manifest.json")) > MAX_SWE_RETAINED_MANIFEST_BYTES:
            raise ValueError("canonical_manifest_oversized")
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
