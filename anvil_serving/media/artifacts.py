"""Authenticated opaque artifact storage with bounded streaming and ranges."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .contracts import MediaArtifact, MediaJob, utc_now
from .errors import MediaError


CHUNK_BYTES = 1024 * 1024
MAX_RANGE_BYTES = 16 * 1024 * 1024
MAX_INLINE_PREVIEW_BYTES = 128 * 1024
_PNG = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class ArtifactPayload:
    artifact: MediaArtifact
    data: bytes
    start: int
    end: int
    total: int


class ArtifactStore:
    """Copy backend bytes into an Anvil-owned, principal-scoped boundary."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise MediaError("artifact_store_unavailable", "artifact store is unavailable", status=500)

    def ingest(
        self,
        job: MediaJob,
        source: BinaryIO,
        *,
        media_type: str,
        max_bytes: int,
        retention_seconds: int,
        now: dt.datetime | None = None,
    ) -> MediaArtifact:
        if max_bytes < 1 or retention_seconds < 1:
            raise MediaError("artifact_policy_invalid", "artifact policy is invalid", status=500)
        artifact_id = "art_" + secrets.token_urlsafe(24)
        temporary: str | None = None
        digest = hashlib.sha256()
        length = 0
        header = bytearray()
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".ingest-", delete=False) as handle:
                temporary = handle.name
                while True:
                    chunk = source.read(min(CHUNK_BYTES, max_bytes - length + 1))
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise MediaError("artifact_invalid", "artifact source did not return bytes")
                    length += len(chunk)
                    if length > max_bytes:
                        raise MediaError("artifact_too_large", "artifact exceeds workflow size policy", status=413)
                    if len(header) < 16:
                        header.extend(chunk[: 16 - len(header)])
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if length < 1 or not _signature_matches(media_type, bytes(header)):
                raise MediaError("artifact_signature_invalid", "artifact bytes do not match the declared media type", status=422)
            created = now or utc_now()
            artifact = MediaArtifact(
                id=artifact_id,
                job_id=job.id,
                principal=job.principal,
                workflow_id=job.workflow_id,
                workflow_version=job.workflow_version,
                media_type=media_type,
                byte_length=length,
                sha256=digest.hexdigest(),
                expires_at=created + dt.timedelta(seconds=retention_seconds),
                source_path=str(self._data_path(artifact_id)),
            )
            os.replace(temporary, self._data_path(artifact_id))
            temporary = None
            self._write_metadata(artifact)
            return artifact
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    def metadata(self, artifact_id: str, *, principal: str, now: dt.datetime | None = None) -> MediaArtifact:
        artifact = self._read_metadata(artifact_id)
        current = now or utc_now()
        if artifact.principal != principal or artifact.expires_at <= current:
            raise MediaError("artifact_not_found", "artifact was not found", status=404)
        path = self._data_path(artifact.id)
        if not path.is_file() or path.stat().st_size != artifact.byte_length:
            raise MediaError("artifact_not_found", "artifact was not found", status=404)
        return artifact

    def read(
        self,
        artifact_id: str,
        *,
        principal: str,
        start: int | None = None,
        end: int | None = None,
        max_bytes: int = MAX_RANGE_BYTES,
        now: dt.datetime | None = None,
    ) -> ArtifactPayload:
        artifact = self.metadata(artifact_id, principal=principal, now=now)
        first = 0 if start is None else start
        last = artifact.byte_length - 1 if end is None else end
        if (
            isinstance(first, bool)
            or isinstance(last, bool)
            or first < 0
            or last < first
            or last >= artifact.byte_length
            or last - first + 1 > max_bytes
        ):
            raise MediaError(
                "artifact_range_invalid",
                "artifact range is invalid or exceeds the response limit",
                status=416,
                details={"total": artifact.byte_length, "maxRangeBytes": max_bytes},
            )
        with self._data_path(artifact.id).open("rb") as handle:
            handle.seek(first)
            data = handle.read(last - first + 1)
        if len(data) != last - first + 1:
            raise MediaError("artifact_not_found", "artifact was not found", status=404)
        return ArtifactPayload(artifact, data, first, last, artifact.byte_length)

    def inline_preview(self, artifact_id: str, *, principal: str, max_bytes: int = MAX_INLINE_PREVIEW_BYTES) -> str | None:
        artifact = self.metadata(artifact_id, principal=principal)
        if not artifact.media_type.startswith("image/") or artifact.byte_length > max_bytes:
            return None
        payload = self.read(artifact_id, principal=principal, max_bytes=max_bytes)
        return f"data:{artifact.media_type};base64,{base64.b64encode(payload.data).decode('ascii')}"

    def prune(self, *, now: dt.datetime | None = None) -> int:
        current = now or utc_now()
        removed = 0
        for metadata in self.root.glob("art_*.json"):
            try:
                artifact = self._read_metadata(metadata.stem)
            except MediaError:
                continue
            if artifact.expires_at > current:
                continue
            for path in (metadata, self._data_path(artifact.id)):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            removed += 1
        return removed

    def _data_path(self, artifact_id: str) -> Path:
        if not artifact_id.startswith("art_") or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for char in artifact_id):
            raise MediaError("artifact_not_found", "artifact was not found", status=404)
        return self.root / f"{artifact_id}.bin"

    def _metadata_path(self, artifact_id: str) -> Path:
        self._data_path(artifact_id)
        return self.root / f"{artifact_id}.json"

    def _write_metadata(self, artifact: MediaArtifact) -> None:
        value = {
            "id": artifact.id,
            "job_id": artifact.job_id,
            "principal": artifact.principal,
            "workflow_id": artifact.workflow_id,
            "workflow_version": artifact.workflow_version,
            "media_type": artifact.media_type,
            "byte_length": artifact.byte_length,
            "sha256": artifact.sha256,
            "expires_at": artifact.expires_at.isoformat(),
        }
        target = self._metadata_path(artifact.id)
        temporary = target.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)

    def _read_metadata(self, artifact_id: str) -> MediaArtifact:
        try:
            path = self._metadata_path(artifact_id)
            if path.stat().st_size > 16384:
                raise MediaError("artifact_not_found", "artifact was not found", status=404)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["expires_at"] = dt.datetime.fromisoformat(value["expires_at"])
            value["source_path"] = str(self._data_path(artifact_id))
            return MediaArtifact(**value)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, MediaError) as exc:
            if isinstance(exc, MediaError) and exc.code == "artifact_not_found":
                raise
            raise MediaError("artifact_not_found", "artifact was not found", status=404) from exc


def _signature_matches(media_type: str, header: bytes) -> bool:
    if media_type == "image/png":
        return header.startswith(_PNG)
    if media_type == "video/mp4":
        return len(header) >= 12 and header[4:8] == b"ftyp"
    return False


__all__ = ["ArtifactPayload", "ArtifactStore"]
