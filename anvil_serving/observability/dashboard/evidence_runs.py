"""Bounded, read-only projections of declared retained benchmark evidence."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import multiprocessing
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading
import time
from typing import Any

from ... import benchmark_evidence
from .contracts import ObservatoryError, canonical, digest, identifier, strict_json
from .run_projection import projected_run_id


SOURCE = "evidence"
MAX_ENTRIES = 50_000
MAX_FILES = 10_000
MAX_ARTIFACT_BYTES = benchmark_evidence.MAX_ARTIFACT_BYTES
MAX_PAGE_READ_BYTES = MAX_ARTIFACT_BYTES
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_RESULT_BYTES = 96 * 1024
MAX_INITIAL_RESULT_BYTES = MAX_MANIFEST_BYTES + MAX_RESULT_BYTES
MAX_PAGE_PROBES = 500
MAX_SNAPSHOTS = 4
SNAPSHOT_TTL_SECONDS = 60.0
DEADLINE_SECONDS = 2.0
_PARENT_BUDGET_SECONDS = 1.8
_WORKER_BUDGET_SECONDS = 1.6
_CURSOR_PREFIX = "e1"
_SECRET_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".kdbx")
_ARTIFACT_ID = re.compile(r"artifact-[a-f0-9]{64}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_DESCRIPTOR_FLAGS = ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK")


def _public_unavailable() -> ObservatoryError:
    return ObservatoryError(
        "owner_unavailable", "The retained evidence source is unavailable.", 503
    )


def _validate_config(config: Mapping[str, Any]) -> tuple[str, str, str]:
    if not isinstance(config, Mapping) or set(config) != {"root", "owner_id", "resource_id"}:
        raise ValueError(
            "evidence runs require only root, owner_id, and resource_id"
        )
    root = config["root"]
    if (type(root) is not str or not root or len(root.encode("utf-8")) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in root)):
        raise ValueError("evidence root is invalid")
    path = Path(root)
    if (not path.is_absolute() or path == Path(path.anchor)
            or any(part in {".", ".."} for part in path.parts)):
        raise ValueError("evidence root must be a declared absolute directory")
    return str(path), identifier(config["owner_id"]), identifier(config["resource_id"])


def _artifact_id(relative: str) -> str:
    return "artifact-" + hashlib.sha256(relative.encode("utf-8")).hexdigest()


def _stamp(nanoseconds: int) -> str:
    return datetime.fromtimestamp(nanoseconds / 1_000_000_000, timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


@contextmanager
def _root_fd(root: str):
    if not all(hasattr(os, flag) for flag in _DESCRIPTOR_FLAGS):
        raise OSError("safe descriptor reads are unavailable")
    path = Path(root)
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("declared root is not a directory")
        yield descriptor
    finally:
        os.close(descriptor)


def _same_file(info: os.stat_result, fingerprint: list[int]) -> bool:
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns] == fingerprint


def _protected_name(name: str) -> bool:
    lowered = name.casefold()
    return (
        lowered in {
            ".git", ".anvil", ".ssh", "credentials", "credential", "secrets", "secret",
            "id_rsa", "id_ed25519",
        }
        or lowered.startswith(".env") or "secret" in lowered or "credential" in lowered
        or lowered.endswith(_SECRET_SUFFIXES)
    )


@contextmanager
def _directory_fd(root: str, parts: tuple[str, ...]):
    with _root_fd(root) as root_descriptor:
        descriptor = os.dup(root_descriptor)
        try:
            for part in parts:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)


def _manifest(root: str, deadline: float) -> dict[str, Any]:
    """Collect only descriptor-relative JSON identities; parsing stays paged."""
    entries_seen = files_seen = manifest_bytes = 0
    partial = False
    truncated = False
    records: list[dict[str, Any]] = []
    directories: list[tuple[str, ...]] = [()]
    while directories:
        if time.monotonic() >= deadline:
            truncated = True
            break
        prefix = directories.pop()
        try:
            with _directory_fd(root, prefix) as directory:
                scan_descriptor = os.dup(directory)
                try:
                    with os.scandir(scan_descriptor) as scan:
                        for entry in scan:
                            if time.monotonic() >= deadline or entries_seen >= MAX_ENTRIES:
                                truncated = True
                                break
                            entries_seen += 1
                            name = entry.name
                            if _protected_name(name):
                                continue
                            try:
                                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                            except OSError:
                                partial = True
                                continue
                            relative_parts = (*prefix, name)
                            relative = "/".join(relative_parts)
                            if len(relative.encode("utf-8")) > 512:
                                partial = True
                                continue
                            if stat.S_ISDIR(info.st_mode):
                                directories.append(relative_parts)
                                continue
                            if not stat.S_ISREG(info.st_mode) or not name.lower().endswith(".json"):
                                continue
                            if files_seen >= MAX_FILES:
                                truncated = True
                                break
                            record = {
                                "id": _artifact_id(relative),
                                "p": relative,
                                "f": [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns],
                            }
                            record_bytes = len(canonical(record)) + 1
                            if manifest_bytes + record_bytes > MAX_MANIFEST_BYTES - 1024:
                                truncated = True
                                break
                            records.append(record)
                            manifest_bytes += record_bytes
                            files_seen += 1
                finally:
                    os.close(scan_descriptor)
        except OSError:
            partial = True
        if truncated:
            break
    records.sort(key=lambda item: (item["f"][3], item["id"]), reverse=True)
    return {
        "records": records, "partial": partial, "truncated": truncated,
        "entries": entries_seen, "files": files_seen,
    }


@contextmanager
def _artifact_fd(root: str, relative: str):
    parts = relative.split("/")
    if (not parts or any(not part or part in {".", ".."} or _protected_name(part)
                         for part in parts)):
        raise OSError("invalid artifact path")
    with _root_fd(root) as root_descriptor:
        descriptor = os.dup(root_descriptor)
        file_descriptor = None
        try:
            for part in parts[:-1]:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = child
            file_descriptor = os.open(
                parts[-1], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=descriptor,
            )
            yield file_descriptor
        finally:
            os.close(descriptor)
            if file_descriptor is not None:
                os.close(file_descriptor)


def _read_artifact(
    root: str, record: Mapping[str, Any], deadline: float,
) -> tuple[dict[str, Any] | None, str, str | None, int]:
    """Return a normalized summary or a public availability state without paths/errors."""
    try:
        relative, fingerprint = record["p"], record["f"]
        if type(relative) is not str or not isinstance(fingerprint, list) or len(fingerprint) != 4:
            return None, "unavailable", None, 0
        with _artifact_fd(root, relative) as descriptor:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                return None, "unavailable", None, 0
            if not _same_file(before, fingerprint):
                return None, "changed", None, 0
            if before.st_size > MAX_ARTIFACT_BYTES:
                return None, "oversize", None, 0
            payload = bytearray()
            while len(payload) <= before.st_size:
                if time.monotonic() >= deadline:
                    return None, "unavailable", None, len(payload)
                block = os.read(descriptor, min(65536, before.st_size + 1 - len(payload)))
                if not block:
                    break
                payload.extend(block)
            if len(payload) != before.st_size or not _same_file(os.fstat(descriptor), fingerprint):
                return None, "changed", None, len(payload)
    except OSError:
        return None, "unavailable", None, 0
    try:
        raw = strict_json(bytes(payload))
        if not isinstance(raw, Mapping):
            return None, "unrecognized", None, len(payload)
        summary = benchmark_evidence.summarize_payload(raw, str(record["id"]) + ".json")
    except (benchmark_evidence.EvidenceError, ObservatoryError, ValueError, RecursionError):
        return None, "unrecognized", None, len(payload)
    return summary, "available", hashlib.sha256(bytes(payload)).hexdigest(), len(payload)


def _text(value: object, maximum: int = 192) -> str | None:
    if type(value) is not str:
        return None
    value = " ".join(value.split())
    return value[:maximum] if value else None


def _compact_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    capacity = summary.get("capacity") if isinstance(summary.get("capacity"), Mapping) else {}
    suites = summary.get("quality", {})
    suites = suites.get("suites", []) if isinstance(suites, Mapping) else []
    return {
        "kind": _text(summary.get("kind"), 48),
        "capacity": {
            key: capacity.get(key) for key in (
                "requests", "completed", "concurrency", "context_tokens", "max_tokens",
                "served_context_tokens", "ttft_p50_ms", "e2e_p50_ms", "aggregate_output_tok_s",
            ) if type(capacity.get(key)) in {int, float} and not isinstance(capacity.get(key), bool)
        },
        "quality": [{
            "name": _text(item.get("name"), 64), "status": _text(item.get("status"), 32),
            "items": item.get("items"), "fully_correct_items": item.get("fully_correct_items"),
            "threshold_passed_items": item.get("threshold_passed_items"),
        } for item in suites[:8] if isinstance(item, Mapping)],
        "validation": "partial" if summary.get("validation_errors") else "valid",
    }


def _row(
    record: Mapping[str, Any], summary: Mapping[str, Any], sha256: str,
    owner_id: str, resource_id: str,
) -> dict[str, Any]:
    evidence_kind = _text(summary.get("evidence_kind"), 48)
    if evidence_kind is None:
        evidence_kind = "external_prior" if summary.get("kind") == "external_prior" else "retained"
    locally_measured = summary.get("locally_measured")
    if type(locally_measured) is not bool:
        locally_measured = evidence_kind != "external_prior"
    native_id = record["id"]
    model = _text(summary.get("model"))
    updated_at = _stamp(record["f"][3])
    return {
        "id": projected_run_id(owner_id, SOURCE, native_id),
        "owner_id": owner_id,
        "source": SOURCE,
        "native_id": native_id,
        "run_id": _text(summary.get("run_id"), 256),
        "kind": "imported",
        "resource_id": resource_id,
        "title": (model + " retained evidence") if model else "Retained benchmark evidence",
        "model": model,
        "suite": _text(summary.get("suite"), 96),
        "profile": _text(summary.get("profile"), 96),
        "native_state": _text(summary.get("completeness"), 48) or "retained",
        "status": "imported",
        "submitted_at": _text(summary.get("recorded_at"), 64),
        "updated_at": updated_at,
        "started_at": None,
        "finished_at": None,
        "observed_at": None,
        "freshness": "fresh",
        "correlation_id": None,
        "evidence_kind": evidence_kind,
        "locally_measured": locally_measured,
        "summary": _compact_summary(summary),
        "evidence_refs": [{"owner_id": owner_id, "artifact_id": native_id, "sha256": sha256}],
    }


def _page(
    root: str, records: list[dict[str, Any]], start: int, limit: int,
    owner_id: str, resource_id: str, deadline: float,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    flags = {"changed": 0, "unavailable": 0, "oversize": 0, "unrecognized": 0}
    # Skipped records can grow metadata after the final admitted row. Reserve
    # its bounded maximum, including the worker envelope, before admitting rows.
    metadata = {"next_index": MAX_FILES,
                "flags": {name: MAX_PAGE_PROBES + 1 for name in flags},
                "exhausted": False, "bytes_read": MAX_PAGE_READ_BYTES}
    bytes_read = 0
    exhausted = False
    index = start
    maximum = min(len(records), start + MAX_PAGE_PROBES)
    while index < maximum and len(items) < limit and time.monotonic() < deadline:
        record = records[index]
        if record["f"][2] > MAX_ARTIFACT_BYTES:
            flags["oversize"] += 1
            index += 1
            continue
        if bytes_read + record["f"][2] > MAX_PAGE_READ_BYTES:
            exhausted = True
            break
        index += 1
        summary, status, sha256, read_bytes = _read_artifact(root, record, deadline)
        bytes_read += read_bytes
        if summary is None or sha256 is None:
            flags[status] = flags.get(status, 0) + 1
            continue
        row = _row(record, summary, sha256, owner_id, resource_id)
        candidate = {"ok": True, "page": {**metadata, "items": [*items, row]}}
        if len(canonical(candidate)) > MAX_RESULT_BYTES:
            index -= 1
            break
        items.append(row)
    if time.monotonic() >= deadline:
        flags["unavailable"] += 1
    return {
        "items": items, "next_index": index, "flags": flags,
        "exhausted": exhausted, "bytes_read": bytes_read,
    }


def _resolve_refs(
    root: str, records: list[dict[str, Any]], refs: list[dict[str, str]], deadline: float,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any], str]], str | None]:
    by_id = {record["id"]: record for record in records}
    resolved = []
    bytes_read = 0
    for ref in refs:
        record = by_id.get(ref["artifact_id"])
        if record is None or bytes_read + record["f"][2] > MAX_PAGE_READ_BYTES:
            return [], "unavailable"
        summary, status, sha256, read_bytes = _read_artifact(root, record, deadline)
        bytes_read += read_bytes
        if summary is None or sha256 is None:
            return [], status
        if sha256 != ref["sha256"]:
            return [], "changed"
        resolved.append((record, summary, sha256))
    return resolved, None


def _ref(value: object) -> dict[str, str] | None:
    if (not isinstance(value, Mapping)
            or set(value) not in ({"artifact_id", "sha256"}, {"owner_id", "artifact_id", "sha256"})):
        return None
    artifact_id, sha256 = value.get("artifact_id"), value.get("sha256")
    if (type(artifact_id) is not str or _ARTIFACT_ID.fullmatch(artifact_id) is None
            or type(sha256) is not str or _SHA256.fullmatch(sha256) is None):
        return None
    return {"artifact_id": artifact_id, "sha256": sha256}


def _eligible_compare(summaries: list[Mapping[str, Any]]) -> bool:
    for summary in summaries:
        if summary.get("locally_measured") is False or summary.get("kind") == "external_prior":
            return False
        if summary.get("completeness") in {"failed", "incomplete"}:
            return False
        if summary.get("kind") == "capacity":
            capacity = summary.get("capacity")
            if not isinstance(capacity, Mapping):
                return False
            requests, completed = capacity.get("requests"), capacity.get("completed")
            if (type(requests) is not int or type(completed) is not int
                    or completed < requests):
                return False
    return True


def _compare_payload(resolved, owner_id: str, resource_id: str) -> dict[str, Any]:
    summaries = [summary for _record, summary, _sha256 in resolved]
    compared = benchmark_evidence.compare_summaries(summaries)
    return {
        "artifacts": [
            _row(record, summary, sha256, owner_id, resource_id)
            for record, summary, sha256 in resolved
        ],
        "comparable": compared["comparable"],
        "differences": sorted(compared["differences"]),
        "unknown_fields": sorted(compared["unknown_fields"]),
        "invalid_artifacts": sorted(compared["invalid_artifacts"]),
    }


def _write_worker_result(path: str, value: Mapping[str, Any]) -> None:
    raw = canonical(value)
    limit = MAX_INITIAL_RESULT_BYTES if "manifest" in value and "page" in value else (
        MAX_MANIFEST_BYTES if "manifest" in value else MAX_RESULT_BYTES
    )
    if len(raw) > limit:
        raw = canonical({"ok": False})
    descriptor = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
    finally:
        os.close(descriptor)


def _evidence_worker(path: str, job: Mapping[str, Any]) -> None:
    try:
        deadline = time.monotonic() + _WORKER_BUDGET_SECONDS
        if job["kind"] == "initial":
            manifest = _manifest(job["root"], deadline)
            result = {"ok": True, "manifest": manifest, "page": _page(
                job["root"], manifest["records"], 0, job["limit"], job["owner_id"],
                job["resource_id"], deadline,
            )}
        elif job["kind"] == "page":
            result = {"ok": True, "page": _page(
                job["root"], job["records"], job["start"], job["limit"], job["owner_id"],
                job["resource_id"], deadline,
            )}
        else:
            manifest = _manifest(job["root"], deadline)
            refs = [_ref(value) for value in job["refs"]]
            if any(value is None for value in refs):
                result = {"ok": True, "error": "invalid"}
            else:
                resolved, error = _resolve_refs(job["root"], manifest["records"], refs, deadline)
                if error is not None:
                    result = {"ok": True, "error": error}
                elif job["kind"] == "detail":
                    record, summary, sha256 = resolved[0]
                    result = {"ok": True, "detail": _row(
                        record, summary, sha256, job["owner_id"], job["resource_id"],
                    )}
                elif not _eligible_compare([summary for _record, summary, _sha256 in resolved]):
                    result = {"ok": True, "error": "ineligible"}
                else:
                    result = {"ok": True, "compare": _compare_payload(
                        resolved, job["owner_id"], job["resource_id"],
                    )}
    except Exception:
        result = {"ok": False}
    try:
        _write_worker_result(path, result)
    except OSError:
        pass


def _read_worker_result(path: str, maximum: int) -> Mapping[str, Any]:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError("invalid worker result")
        raw = bytearray()
        while len(raw) <= info.st_size:
            block = os.read(descriptor, min(65536, info.st_size + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        if len(raw) != info.st_size:
            raise ValueError("incomplete worker result")
        result = strict_json(bytes(raw))
        if not isinstance(result, Mapping) or result.get("ok") is not True:
            raise ValueError("worker failed")
        return result
    finally:
        os.close(descriptor)


def _stop_worker(process) -> bool:
    try:
        if process.pid is None:
            process.close()
            return True
        if process.is_alive():
            process.terminate()
            process.join(timeout=0.05)
        if process.is_alive():
            process.kill()
            process.join(timeout=0.05)
        if process.is_alive():
            return False
        process.join(timeout=0)
        process.close()
        return True
    except (OSError, ValueError):
        return False


class EvidenceRuns:
    """A killable catalog owner; Console owns authorization and stale-page caching."""

    def __init__(
        self, config: Mapping[str, Any], *, clock=time.monotonic,
        worker_target=_evidence_worker,
    ) -> None:
        self.root, self.owner_id, self.resource_id = _validate_config(config)
        self._clock = clock
        self._worker_target = worker_target
        self._snapshots: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._active = None
        self._closed = False
        self._poisoned = False

    def close(self) -> None:
        with self._lock:
            self._closed = True
            process = self._active
            self._snapshots.clear()
        if process is not None and not _stop_worker(process):
            self._poisoned = True

    def detail(self, *, artifact_id: str, sha256: str, authority_key: str) -> dict[str, Any]:
        ref = _ref({"artifact_id": artifact_id, "sha256": sha256})
        if ref is None:
            raise ObservatoryError("invalid_artifact", "Select a retained evidence reference.", 409)
        response = self._owner_read("detail", [ref], authority_key)
        detail = response.get("detail")
        if not isinstance(detail, Mapping):
            raise _public_unavailable()
        result = dict(detail)
        result["observed_at"] = self._observed_at()
        return result

    def compare(self, *, refs: list[dict[str, str]], authority_key: str) -> dict[str, Any]:
        if not isinstance(refs, list) or not 2 <= len(refs) <= 20:
            raise ObservatoryError("invalid_comparison", "Select between two and twenty evidence references.", 409)
        if any(not isinstance(ref, Mapping) for ref in refs):
            raise ObservatoryError("invalid_comparison", "Select distinct retained evidence references.", 409)
        if any("owner_id" in ref and (type(ref["owner_id"]) is not str or ref["owner_id"] != self.owner_id)
               for ref in refs):
            raise ObservatoryError("invalid_comparison", "Select references from this retained source.", 409)
        normalized = [_ref(ref) for ref in refs]
        if any(ref is None for ref in normalized) or len({ref["artifact_id"] for ref in normalized}) != len(refs):
            raise ObservatoryError("invalid_comparison", "Select distinct retained evidence references.", 409)
        response = self._owner_read("compare", normalized, authority_key)
        compared = response.get("compare")
        if not isinstance(compared, Mapping):
            raise _public_unavailable()
        result = dict(compared)
        observed_at = self._observed_at()
        result["observed_at"] = observed_at
        for row in result.get("artifacts", []):
            if isinstance(row, dict):
                row["observed_at"] = observed_at
        return result

    @staticmethod
    def _observed_at() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _owner_read(self, kind: str, refs: list[dict[str, str]], authority_key: str) -> Mapping[str, Any]:
        if type(authority_key) is not str or not authority_key or len(authority_key) > 128:
            raise ObservatoryError("invalid_run_list", "Select a valid run authority.")
        with self._lock:
            if self._closed or self._poisoned or self._active is not None:
                raise _public_unavailable()
        response = self._worker({
            "kind": kind, "root": self.root, "refs": refs,
            "owner_id": self.owner_id, "resource_id": self.resource_id,
        }, MAX_RESULT_BYTES)
        error = response.get("error")
        if error == "changed":
            raise ObservatoryError("evidence_changed", "The retained evidence changed; refresh its reference.", 409)
        if error in {"invalid", "ineligible"}:
            raise ObservatoryError("evidence_ineligible", "The selected evidence cannot be compared.", 409)
        if error is not None:
            raise ObservatoryError("evidence_unavailable", "The selected retained evidence is unavailable.", 409)
        return response

    def page(
        self, *, limit: int = 100, cursor: str | None = None, authority_key: str,
    ) -> dict[str, Any]:
        if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ObservatoryError(
                "invalid_run_list", "Select a list limit between 1 and 100."
            )
        if type(authority_key) is not str or not authority_key or len(authority_key) > 128:
            raise ObservatoryError("invalid_run_list", "Select a valid run authority.")
        with self._lock:
            if self._closed or self._poisoned or self._active is not None:
                raise _public_unavailable()
            self._expire_snapshots()
        if cursor is None:
            response = self._worker({
                "kind": "initial", "root": self.root, "limit": limit,
                "owner_id": self.owner_id, "resource_id": self.resource_id,
            }, MAX_INITIAL_RESULT_BYTES)
            snapshot = self._snapshot(response.get("manifest"), authority_key)
            start = 0
        else:
            snapshot, start = self._cursor(cursor, authority_key)
            response = self._worker({
                "kind": "page", "root": self.root, "records": snapshot["records"],
                "start": start,
                "limit": limit, "owner_id": self.owner_id, "resource_id": self.resource_id,
            }, MAX_RESULT_BYTES)
        page = response.get("page")
        if not isinstance(page, Mapping) or not isinstance(page.get("items"), list):
            raise _public_unavailable()
        next_index = page.get("next_index")
        if type(next_index) is not int or not start <= next_index <= len(snapshot["records"]):
            raise _public_unavailable()
        flags = page.get("flags") if isinstance(page.get("flags"), Mapping) else {}
        exhausted = page.get("exhausted") is True
        partial = snapshot["partial"] or exhausted or any(
            bool(flags.get(name)) for name in ("changed", "unavailable", "oversize")
        )
        next_cursor = (
            self._cursor_text(snapshot, next_index)
            if next_index < len(snapshot["records"]) else None
        )
        observed_at = datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        for row in page["items"]:
            row["observed_at"] = observed_at
        return {
            "items": page["items"], "next_cursor": next_cursor,
            "sources": [{
                "id": self.owner_id, "status": "fresh", "observed_at": observed_at,
                "deadline_seconds": DEADLINE_SECONDS,
                "truncated": snapshot["truncated"],
                "partial": partial,
                "unavailable_artifacts": sum(
                    int(flags.get(name, 0)) for name in ("changed", "unavailable", "oversize")
                ),
                "ignored_json": int(flags.get("unrecognized", 0)),
                "read_budget_exhausted": exhausted,
            }],
        }

    def _snapshot(self, manifest: object, authority_key: str) -> dict[str, Any]:
        if not isinstance(manifest, Mapping) or not isinstance(manifest.get("records"), list):
            raise _public_unavailable()
        records = manifest["records"]
        if len(records) > MAX_FILES or any(not isinstance(item, Mapping) for item in records):
            raise _public_unavailable()
        snapshot = {
            "id": secrets.token_hex(12),
            "authority": digest({"authority": authority_key}),
            "records": [dict(item) for item in records],
            "partial": manifest.get("partial") is True,
            "truncated": manifest.get("truncated") is True,
            "expires": self._clock() + SNAPSHOT_TTL_SECONDS,
        }
        with self._lock:
            if self._closed or self._poisoned:
                raise _public_unavailable()
            self._expire_snapshots()
            self._snapshots[snapshot["id"]] = snapshot
            while len(self._snapshots) > MAX_SNAPSHOTS:
                self._snapshots.popitem(last=False)
        return snapshot

    def _cursor(self, cursor: str, authority_key: str) -> tuple[dict[str, Any], int]:
        if type(cursor) is not str or len(cursor) > 128:
            raise ObservatoryError("invalid_run_list", "Select a valid run cursor.")
        parts = cursor.split(".")
        if (len(parts) != 3 or parts[0] != _CURSOR_PREFIX
                or len(parts[1]) != 24 or not parts[1].isalnum()
                or not parts[2].isdigit()):
            raise ObservatoryError("invalid_run_list", "Select a valid run cursor.")
        with self._lock:
            self._expire_snapshots()
            snapshot = self._snapshots.get(parts[1])
            if snapshot is None:
                raise ObservatoryError(
                    "run_cursor_expired",
                    "Refresh retained evidence to start a new page.", 409,
                )
            if snapshot["authority"] != digest({"authority": authority_key}):
                raise ObservatoryError(
                    "forbidden",
                    "This run cursor is not authorized for the current session.", 403,
                )
            self._snapshots.move_to_end(parts[1])
        start = int(parts[2])
        if start < 0 or start > len(snapshot["records"]):
            raise ObservatoryError("invalid_run_list", "Select a valid run cursor.")
        return snapshot, start

    def _cursor_text(self, snapshot: Mapping[str, Any], index: int) -> str:
        return f"{_CURSOR_PREFIX}.{snapshot['id']}.{index}"

    def _expire_snapshots(self) -> None:
        now = self._clock()
        for key in [key for key, value in self._snapshots.items() if value["expires"] <= now]:
            self._snapshots.pop(key, None)

    def _worker(self, job: Mapping[str, Any], maximum: int) -> Mapping[str, Any]:
        if not all(hasattr(os, flag) for flag in _DESCRIPTOR_FLAGS):
            raise _public_unavailable()
        deadline = self._clock() + _PARENT_BUDGET_SECONDS
        with tempfile.TemporaryDirectory(prefix="anvil-evidence-runs-") as directory:
            descriptor, path = tempfile.mkstemp(prefix="result-", dir=directory)
            os.close(descriptor)
            context = multiprocessing.get_context("spawn")
            process = context.Process(
                target=self._worker_target, args=(path, dict(job)),
                name="observatory-evidence-runs",
            )
            with self._lock:
                if self._closed or self._poisoned or self._active is not None:
                    raise _public_unavailable()
                self._active = process
            try:
                with self._lock:
                    if self._closed or self._poisoned:
                        raise _public_unavailable()
                    process.start()
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise TimeoutError
                process.join(remaining)
                if process.is_alive() or process.exitcode != 0:
                    raise TimeoutError
                return _read_worker_result(path, maximum)
            except (EOFError, OSError, TimeoutError, ValueError, ObservatoryError):
                raise _public_unavailable() from None
            finally:
                stopped = _stop_worker(process)
                with self._lock:
                    if self._active is process:
                        self._active = None
                    if not stopped:
                        self._poisoned = True
