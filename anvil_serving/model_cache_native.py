"""Read-only inspection and deletion planning for native Hugging Face hub caches.

The native cache surface deliberately reads only the standard ``hub/models--*``
layout.  It never resolves a filesystem link while walking metadata: snapshot
links must point directly at regular files in that repository's ``blobs``
directory.  This makes a later deletion implementation able to use the plan
without trusting a caller-controlled path outside the selected cache.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat
import time
from typing import Any


_REPO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_MAX_REF_BYTES = 1024
_MAX_SCAN_ENTRIES = 20_000
_MAX_SCAN_DEPTH = 32
_MAX_SCAN_SECONDS = 5.0


class NativeCacheError(ValueError):
    """The cache layout cannot be safely inspected or planned for removal."""


class _ScanBudget:
    """Bound metadata traversal before a hostile cache can consume the CLI."""

    def __init__(self) -> None:
        self.entries = 0
        self.deadline = time.monotonic() + _MAX_SCAN_SECONDS

    def consume(self) -> None:
        if time.monotonic() > self.deadline:
            raise NativeCacheError("native cache inspection timed out")
        self.entries += 1
        if self.entries > _MAX_SCAN_ENTRIES:
            raise NativeCacheError("native cache inspection exceeds entry limit")


def _absolute(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _no_symlink_ancestors(path: Path, label: str) -> None:
    """Reject a caller path whose lexical ancestry contains a symlink.

    This is deliberately lexical rather than ``resolve()``: resolving would
    follow exactly the path substitution this inspection surface refuses.
    It narrows, but cannot eliminate, filesystem TOCTOU races; native apply is
    unsupported for that reason as well as ownership concerns.
    """
    current = path
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            # The final directory check reports a missing selected root.  A
            # missing parent cannot be a symlink that redirects this lookup.
            pass
        except OSError as exc:
            raise NativeCacheError("cannot inspect %s ancestry" % label) from exc
        else:
            if stat.S_ISLNK(info.st_mode):
                raise NativeCacheError("%s ancestry must not contain a symlink" % label)
        if current.parent == current:
            return
        current = current.parent


def _directory(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise NativeCacheError("%s does not exist" % label) from exc
    except OSError as exc:
        raise NativeCacheError("cannot inspect %s" % label) from exc
    if stat.S_ISLNK(info.st_mode):
        raise NativeCacheError("%s must not be a symlink" % label)
    if not stat.S_ISDIR(info.st_mode):
        raise NativeCacheError("%s must be a directory" % label)


def _regular(path: Path) -> os.stat_result | None:
    """Return lstat only for a direct regular file; do not follow a link."""
    try:
        info = path.lstat()
    except OSError:
        return None
    return info if stat.S_ISREG(info.st_mode) else None


def _children(path: Path, budget: _ScanBudget) -> list[Path]:
    try:
        children: list[Path] = []
        with os.scandir(path) as entries:
            for item in entries:
                budget.consume()
                children.append(Path(item.path))
        return sorted(children, key=lambda item: item.name)
    except OSError as exc:
        raise NativeCacheError("cannot read cache metadata") from exc


def _relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace(os.sep, "/")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _repo_id(name: str) -> str | None:
    if not name.startswith("models--"):
        return None
    encoded = name[len("models--"):]
    if "--" not in encoded:
        return None
    owner, repo = encoded.split("--", 1)
    candidate = owner + "/" + repo
    return candidate if _valid_repo_id(candidate) else None


def _valid_repo_id(value: str) -> bool:
    if not _REPO_ID_RE.fullmatch(value):
        return False
    owner, repo = value.split("/", 1)
    # HF's models--OWNER--REPO encoding cannot distinguish either segment
    # when it contains this delimiter.
    return "--" not in owner and "--" not in repo


def _snapshot_link(source: Path, repo: Path, blobs: Path) -> tuple[str | None, int | None, str | None]:
    """Return blob name/size or an unsafe reason, without resolving links."""
    try:
        target = os.readlink(source)
    except OSError:
        return None, None, "unreadable link"
    if os.path.isabs(target):
        return None, None, "absolute link"
    candidate = Path(os.path.abspath(os.path.normpath(os.path.join(source.parent, target))))
    if not _inside(candidate, repo):
        return None, None, "link escapes repository"
    if candidate.parent != blobs:
        return None, None, "link does not target a direct blob"
    info = _regular(candidate)
    if info is None:
        return None, None, "missing or non-regular blob target"
    return candidate.name, info.st_size, None


def _scan_snapshot(snapshot: Path, repo: Path, blobs: Path, budget: _ScanBudget) -> dict[str, Any]:
    unsafe: list[str] = []
    blob_names: set[str] = set()
    logical_bytes = 0
    pending = [(snapshot, 0)]
    while pending:
        current, depth = pending.pop()
        if depth > _MAX_SCAN_DEPTH:
            raise NativeCacheError("native cache snapshot exceeds directory depth limit")
        for item in _children(current, budget):
            relative = _relative(item, repo)
            try:
                info = item.lstat()
            except OSError:
                unsafe.append(relative)
                continue
            if stat.S_ISDIR(info.st_mode):
                pending.append((item, depth + 1))
            elif stat.S_ISLNK(info.st_mode):
                blob_name, size, issue = _snapshot_link(item, repo, blobs)
                if issue is not None:
                    unsafe.append(relative)
                else:
                    blob_names.add(blob_name)
                    logical_bytes += int(size)
            else:
                # Snapshot metadata is a link farm.  A copied regular file is
                # neither a safe blob reference nor safely reclaimable.
                unsafe.append(relative)
    return {
        "revision": snapshot.name,
        "logical_bytes": logical_bytes if not unsafe else None,
        "blob_names": sorted(blob_names),
        # A local link farm has no manifest proving every upstream selected
        # artifact was downloaded.  Valid links only prove local integrity.
        "local_link_integrity": "valid" if not unsafe else "unsafe",
        "artifact_completeness": "unverified",
        "unsafe_paths": unsafe,
    }


def _scan_refs(refs: Path, repo: Path, budget: _ScanBudget) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    unsafe: list[str] = []
    pending = [(refs, 0)]
    while pending:
        current, depth = pending.pop()
        if depth > _MAX_SCAN_DEPTH:
            raise NativeCacheError("native cache refs exceed directory depth limit")
        for item in _children(current, budget):
            relative = _relative(item, repo)
            try:
                info = item.lstat()
            except OSError:
                unsafe.append(relative)
                continue
            if stat.S_ISDIR(info.st_mode):
                pending.append((item, depth + 1))
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_REF_BYTES:
                unsafe.append(relative)
                continue
            nofollow = getattr(os, "O_NOFOLLOW", None)
            if nofollow is None:
                unsafe.append(relative)
                continue
            try:
                descriptor = os.open(item, os.O_RDONLY | nofollow)
                try:
                    opened = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                        or opened.st_nlink != 1
                    ):
                        unsafe.append(relative)
                        continue
                    raw = os.read(descriptor, _MAX_REF_BYTES + 1)
                finally:
                    os.close(descriptor)
                value = raw.decode("utf-8").strip()
            except (OSError, UnicodeError):
                unsafe.append(relative)
                continue
            if len(raw) > _MAX_REF_BYTES:
                unsafe.append(relative)
                continue
            if not _REVISION_RE.fullmatch(value):
                unsafe.append(relative)
                continue
            values[_relative(item, refs)] = value
    return values, unsafe


def _blob_size(info: os.stat_result) -> dict[str, Any]:
    blocks = getattr(info, "st_blocks", None)
    allocated = int(blocks) * 512 if isinstance(blocks, int) and blocks >= 0 else None
    return {
        "logical_bytes": int(info.st_size),
        "allocated_bytes": allocated,
        "inode": (int(info.st_dev), int(info.st_ino)),
        "nlink": int(info.st_nlink),
    }


def _scan_blobs(
    blobs: Path, repo: Path, budget: _ScanBudget
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    values: dict[str, dict[str, Any]] = {}
    unsafe: list[str] = []
    for item in _children(blobs, budget):
        relative = _relative(item, repo)
        info = _regular(item)
        if info is None:
            unsafe.append(relative)
            continue
        values[item.name] = _blob_size(info)
    return values, unsafe


def _repository(root: Path, repo: Path, repo_id: str | None, budget: _ScanBudget) -> dict[str, Any]:
    record: dict[str, Any] = {
        "repo_id": repo_id,
        "layout": "standard" if repo_id else "unknown",
        "refs": {},
        "snapshots": [],
        "unique_logical_blob_bytes": None,
        "unique_allocated_blob_bytes": None,
        "allocation_status": "unknown",
        "unsafe_paths": [],
    }
    if repo_id is None:
        record["unsafe_paths"] = [_relative(repo, root)]
        return record
    required = {name: repo / name for name in ("blobs", "snapshots")}
    for name, path in required.items():
        try:
            info = path.lstat()
        except OSError:
            record["layout"] = "unknown"
            record["unsafe_paths"].append(_relative(path, repo))
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            record["layout"] = "unknown"
            record["unsafe_paths"].append(_relative(path, repo))
    if record["layout"] != "standard":
        return record

    blobs, blob_unsafe = _scan_blobs(required["blobs"], repo, budget)
    refs_path = repo / "refs"
    if not os.path.lexists(refs_path):
        refs, ref_unsafe = {}, []
    else:
        try:
            _directory(refs_path, "native repository refs")
        except NativeCacheError:
            record["layout"] = "unknown"
            record["unsafe_paths"].append(_relative(refs_path, repo))
            return record
        refs, ref_unsafe = _scan_refs(refs_path, repo, budget)
    snapshots: list[dict[str, Any]] = []
    snapshot_unsafe: list[str] = []
    for snapshot in _children(required["snapshots"], budget):
        try:
            info = snapshot.lstat()
        except OSError:
            snapshot_unsafe.append(_relative(snapshot, repo))
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or not _REVISION_RE.fullmatch(snapshot.name):
            snapshot_unsafe.append(_relative(snapshot, repo))
            continue
        result = _scan_snapshot(snapshot, repo, required["blobs"], budget)
        snapshots.append(result)
        snapshot_unsafe.extend(result["unsafe_paths"])
    unique_blobs: dict[tuple[int, int], dict[str, Any]] = {}
    for blob in blobs.values():
        unique_blobs.setdefault(blob["inode"], blob)
    for name, blob in blobs.items():
        # Even an alias entirely inside this blob directory survives deleting
        # a selected snapshot's one name.  Inventory can deduplicate it, but
        # a removal preview must not claim those inode blocks reclaimable.
        if blob["nlink"] != 1:
            blob_unsafe.append("blobs/%s" % name)
    allocated = [blob["allocated_bytes"] for blob in unique_blobs.values()]
    record.update(
        refs=refs,
        snapshots=snapshots,
        unique_logical_blob_bytes=sum(blob["logical_bytes"] for blob in unique_blobs.values()),
        unique_allocated_blob_bytes=(
            sum(int(size) for size in allocated) if all(size is not None for size in allocated) else None
        ),
        allocation_status=(
            "known" if all(size is not None for size in allocated) else "unknown"
        ),
        unsafe_paths=sorted(set(blob_unsafe + ref_unsafe + snapshot_unsafe)),
    )
    return record


def _root(cache_dir: str | os.PathLike[str]) -> tuple[Path, Path | None]:
    root = _absolute(cache_dir)
    _no_symlink_ancestors(root, "native cache directory")
    _directory(root, "native cache directory")
    # Accept both HF_HOME (which contains ``hub``) and the direct hub cache
    # directory commonly passed by operators as ~/.cache/huggingface/hub.
    if root.name == "hub":
        return root, root
    hub = root / "hub"
    if not os.path.lexists(hub):
        return root, None
    _directory(hub, "native cache hub directory")
    return root, hub


def inventory(cache_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Return a bounded, read-only inventory of a native HF hub cache."""
    root, hub = _root(cache_dir)
    budget = _ScanBudget()
    usage = shutil.disk_usage(root)
    if hub is None:
        repositories: list[dict[str, Any]] = []
        layout = "unknown"
        unrecognized_entries: list[str] = []
    else:
        repositories = []
        unrecognized_entries = []
        top_unsafe: list[str] = []
        for item in _children(hub, budget):
            try:
                info = item.lstat()
            except OSError:
                top_unsafe.append(_relative(item, root))
                continue
            if item.name == ".locks" and stat.S_ISDIR(info.st_mode):
                continue
            if item.name == "CACHEDIR.TAG" and stat.S_ISREG(info.st_mode):
                continue
            if item.name.startswith("models--"):
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    repositories.append({
                        "repo_id": _repo_id(item.name),
                        "layout": "unknown",
                        "refs": {},
                        "snapshots": [],
                        "unique_logical_blob_bytes": None,
                        "unique_allocated_blob_bytes": None,
                        "allocation_status": "unknown",
                        "unsafe_paths": [_relative(item, root)],
                    })
                else:
                    repositories.append(_repository(root, item, _repo_id(item.name), budget))
            else:
                unrecognized_entries.append(_relative(item, root))
        layout = (
            "standard"
            if (
                not unrecognized_entries
                and not top_unsafe
                and all(row["layout"] == "standard" for row in repositories)
            )
            else "unknown"
        )
    return {
        "schema_version": "model-cache-native-inventory/v1",
        "cache_dir": str(root),
        "layout": layout,
        "disk": {
            "capacity_bytes": usage.total,
            "used_bytes": usage.used,
            "available_bytes": usage.free,
        },
        "repositories": repositories,
        "unrecognized_entries": unrecognized_entries,
        "unsafe_paths": top_unsafe if hub is not None else [],
        "safety_caveat": (
            "Native cache inspection rejects symlinked selected-path ancestry and never "
            "follows metadata links outside a selected repository. It cannot establish "
            "upstream artifact completeness or eliminate filesystem TOCTOU races; native "
            "cache deletion is unsupported."
        ),
    }


def removal_plan(cache_dir: str | os.PathLike[str], repo_id: str, revision: str) -> dict[str, Any]:
    """Plan exact native snapshot deletion without mutating the filesystem."""
    if not isinstance(repo_id, str) or not _valid_repo_id(repo_id):
        raise NativeCacheError("repository must be an exact OWNER/REPO id")
    if not isinstance(revision, str) or not _REVISION_RE.fullmatch(revision):
        raise NativeCacheError("revision must be exactly 40 lowercase hexadecimal characters")
    root, hub = _root(cache_dir)
    budget = _ScanBudget()
    if hub is None:
        raise NativeCacheError("native cache has no standard hub layout")
    owner, repo_name = repo_id.split("/", 1)
    repo = hub / ("models--%s--%s" % (owner, repo_name))
    if not os.path.lexists(repo):
        return {
            "repo_id": repo_id,
            "revision": revision,
            "snapshot_exists": False,
            "exclusive_blob_names": [],
            "shared_blob_names": [],
            "reclaimable_bytes": 0,
            "refs_to_remove": [],
            "apply": "unsupported_native_cache_ownership",
        }
    _directory(repo, "native repository cache")
    record = _repository(root, repo, repo_id, budget)
    if record["layout"] != "standard" or record["unsafe_paths"]:
        raise NativeCacheError("native cache repository has unsafe or unsupported topology")
    snapshots = {item["revision"]: item for item in record["snapshots"]}
    selected = snapshots.get(revision)
    if selected is None:
        return {
            "repo_id": repo_id,
            "revision": revision,
            "snapshot_exists": False,
            "exclusive_blob_names": [],
            "shared_blob_names": [],
            "reclaimable_bytes": 0,
            "refs_to_remove": [],
            "apply": "unsupported_native_cache_ownership",
        }
    if selected["local_link_integrity"] != "valid":
        raise NativeCacheError("selected native cache snapshot is unsafe")
    selected_blobs = set(selected["blob_names"])
    other_blobs = set().union(*(set(item["blob_names"]) for key, item in snapshots.items() if key != revision))
    blob_sizes = {
        item.name: _blob_size(info)
        for item in _children(repo / "blobs", budget)
        if (info := _regular(item)) is not None
    }
    selected_inodes = {blob_sizes[name]["inode"] for name in selected_blobs}
    other_inodes = {blob_sizes[name]["inode"] for name in other_blobs}
    exclusive_inodes = selected_inodes - other_inodes
    shared_inodes = selected_inodes & other_inodes
    exclusive = sorted(name for name in selected_blobs if blob_sizes[name]["inode"] in exclusive_inodes)
    shared = sorted(name for name in selected_blobs if blob_sizes[name]["inode"] in shared_inodes)
    exclusive_records = {
        blob_sizes[name]["inode"]: blob_sizes[name]
        for name in exclusive
    }
    shared_records = {
        blob_sizes[name]["inode"]: blob_sizes[name]
        for name in shared
    }
    exclusive_allocated = [item["allocated_bytes"] for item in exclusive_records.values()]
    shared_allocated = [item["allocated_bytes"] for item in shared_records.values()]
    allocation_known = all(size is not None for size in exclusive_allocated + shared_allocated)
    return {
        "repo_id": repo_id,
        "revision": revision,
        "snapshot_exists": True,
        "snapshot_logical_bytes": selected["logical_bytes"],
        "artifact_completeness": selected["artifact_completeness"],
        "local_link_integrity": selected["local_link_integrity"],
        "unique_logical_blob_bytes": record["unique_logical_blob_bytes"],
        "unique_allocated_blob_bytes": record["unique_allocated_blob_bytes"],
        "exclusive_blob_names": exclusive,
        "shared_blob_names": shared,
        "exclusive_blob_logical_bytes": sum(item["logical_bytes"] for item in exclusive_records.values()),
        "shared_blob_logical_bytes": sum(item["logical_bytes"] for item in shared_records.values()),
        "exclusive_blob_allocated_bytes": (
            sum(int(size) for size in exclusive_allocated)
            if all(size is not None for size in exclusive_allocated) else None
        ),
        "shared_blob_allocated_bytes": (
            sum(int(size) for size in shared_allocated)
            if all(size is not None for size in shared_allocated) else None
        ),
        "reclaimable_bytes": (
            sum(int(size) for size in exclusive_allocated)
            if all(size is not None for size in exclusive_allocated) else None
        ),
        "allocation_status": "known" if allocation_known else "unknown",
        "reclaimability_caveat": (
            "Allocated bytes are inode-deduplicated st_blocks*512 observations. "
            "APFS copy-on-write or clone accounting can make eventual space release lower."
            if allocation_known else
            "Allocated-byte accounting is unavailable on this filesystem."
        ),
        "refs_to_remove": sorted(name for name, value in record["refs"].items() if value == revision),
        "apply": "unsupported_native_cache_ownership",
    }
