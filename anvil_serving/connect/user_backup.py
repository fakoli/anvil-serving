"""Bounded private authentication snapshots and non-activating recovery."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import closing
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import tempfile
import time
from urllib.parse import quote
import zipfile

from . import manage
from .config import role_identity, _json_load
from .recovery import _path, _digest
from ..operator_output import OperatorError

_LIMIT = 64 * 1024 * 1024
_NAME = re.compile(r"auth-(?P<created_at>\d{8}T\d{12}Z)-[a-f0-9]{12}\.zip\Z")
_CLASSIFICATION = "restricted-authentication"
_RETENTION_DAYS = 14
_RETENTION_RECENT_COPIES = 7


def private_directory(path: Path, *, create: bool = False) -> None:
    from .users import _invalid
    _path(path)
    if any((p / ".git").exists() for p in (path, *path.parents)):
        raise _invalid("Authentication backups and handoffs must be outside Git in absolute private directories.")
    manage._safe_root_ancestors(path)
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise _invalid("Authentication backup directory must be owner-only (0700).")


def snapshot(data: dict, manifest: str, *, users_raw: bytes) -> dict:
    """Caller holds deployment lock and has stopped Authelia's account writer."""
    from .users import _exclusive, _invalid
    root = Path(manifest).parent / "backups"
    private_directory(root, create=True)
    auth = data["authelia"]
    uid, gid = role_identity(data, "idp")
    members = {"users.yml": users_raw}
    database = Path(auth["state_directory"]) / "authelia.sqlite3"
    if os.path.lexists(database):
        manage._safe_private_runtime_directory(database.parent, uid, gid)
        info = database.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid
                or info.st_mode & 0o022 or info.st_size > _LIMIT):
            raise _invalid("Authelia database has unsafe metadata or exceeds the backup size limit.")
        key = Path(auth["storage_encryption_key_file"])
        manage._safe_consumed_file(key, uid, gid)
        members["storage-key"] = manage._read_regular(key, 4096)
        if not members["storage-key"]:
            raise _invalid("Authelia storage key is unavailable.")
        with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=root) as temporary:
            copied = Path(temporary) / "authelia.sqlite3"
            staged = Path(temporary) / "source.sqlite3"
            # Authelia is stopped. Copy bounded DB/WAL bytes first so SQLite
            # can rebuild shared-memory state only inside this private staging.
            for suffix in ("", "-wal"):
                original = Path(str(database) + suffix)
                if suffix and not os.path.lexists(original):
                    continue
                metadata = original.lstat()
                if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                        or metadata.st_uid != uid or metadata.st_mode & 0o022):
                    raise _invalid("Authelia database or WAL has unsafe metadata.")
                _exclusive(Path(str(staged) + suffix), manage._read_regular(original, _LIMIT) or b"")
            deadline = time.monotonic() + 15
            with closing(sqlite3.connect("file:" + quote(str(staged), safe="/") + "?mode=ro", uri=True, timeout=5)) as source:
                page_size = source.execute("PRAGMA page_size").fetchone()[0]
                def progress(_status, _remaining, total):
                    if time.monotonic() > deadline or total * page_size > _LIMIT:
                        raise _invalid("Authentication snapshot exceeded its time or size bound.")
                with closing(sqlite3.connect(copied)) as destination:
                    source.backup(destination, pages=256, progress=progress, sleep=0.05)
                    destination.execute("PRAGMA journal_mode=DELETE")
            copied.chmod(0o600)
            members["authelia.sqlite3"] = manage._read_regular(copied, _LIMIT)
    if sum(len(value) for value in members.values()) > _LIMIT:
        raise _invalid("Authentication backup exceeds the 64 MiB bound.")
    created_at = datetime.now(timezone.utc)
    record = {"schema": "anvil-connect.auth-backup/v1", "classification": _CLASSIFICATION,
              "created_at": created_at.isoformat(), "encrypted": False,
              "contains_personal_data": True, "contains_authentication_material": True,
              "files": {name: hashlib.sha256(value).hexdigest() for name, value in members.items()}}
    members["record.json"] = (json.dumps(record, sort_keys=True) + "\n").encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
    name = "auth-" + created_at.strftime("%Y%m%dT%H%M%S%fZ") + "-" + secrets.token_hex(6) + ".zip"
    path = root / name
    _exclusive(path, stream.getvalue())
    # Validate an independently decoded copy before discarding an older backup.
    read_snapshot(path)
    # Persist both the archive entry and a newly created backup directory before
    # the caller commits an account change that may need this snapshot.
    for directory in (root, root.parent):
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return {"file": str(path), "sha256": hashlib.sha256(stream.getvalue()).hexdigest(),
            "classification": _CLASSIFICATION, "encrypted": False,
            "retention_days": _RETENTION_DAYS, "retention_recent_copies": _RETENTION_RECENT_COPIES,
            "retention_copies": _RETENTION_RECENT_COPIES}


def prune(root: Path, *, now: datetime | None = None) -> dict:
    """Run after restarting Authelia; invalid archives are never deleted."""
    private_directory(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() != timezone.utc.utcoffset(now):
        raise ValueError("backup retention time must be UTC-aware")
    now = now.astimezone(timezone.utc)
    candidates = []
    preserved = 0
    for candidate in sorted(root.iterdir()):
        if not _NAME.fullmatch(candidate.name):
            continue
        try:
            created_at = _snapshot_created_at(candidate, now=now)
            info = candidate.lstat()
        except (OSError, ValueError, RuntimeError, OperatorError, sqlite3.Error):
            preserved += 1
        else:
            candidates.append((created_at, candidate, (info.st_dev, info.st_ino)))
    candidates.sort()
    retained = {candidate for _, candidate, _ in candidates[-_RETENTION_RECENT_COPIES:]}
    first_day = now.date().toordinal() - (_RETENTION_DAYS - 1)
    newest_by_day = {}
    for created_at, candidate, _ in candidates:
        if created_at.date().toordinal() >= first_day:
            newest_by_day[created_at.date()] = candidate
    retained.update(newest_by_day.values())
    for _, old, inode in candidates:
        if old in retained:
            continue
        info = old.lstat()
        if ((info.st_dev, info.st_ino) == inode and stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600):
            old.unlink()
    return {"retention_days": _RETENTION_DAYS, "retention_recent_copies": _RETENTION_RECENT_COPIES,
            "retention_copies": _RETENTION_RECENT_COPIES, "unrecognized_archives_preserved": preserved}


def _snapshot_raw(path: Path) -> bytes:
    """Read one owner-only archive without decoding its untrusted contents."""
    private_directory(path.parent)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        from .users import _invalid
        raise _invalid("Authentication archive must be an owner-only regular file (0600).")
    return manage._read_regular(path, _LIMIT + 65536) or b""


def _snapshot_created_at(path: Path, *, now: datetime, raw: bytes | None = None) -> datetime:
    """Validate archive contents and bind its timestamp to its authenticated record."""
    from .users import _database, _invalid
    match = _NAME.fullmatch(path.name)
    if match is None:
        raise _invalid("Authentication archive name is invalid.")
    filename_created_at = datetime.strptime(match["created_at"], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
    raw = _snapshot_raw(path) if raw is None else raw
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if set(names) not in ({"record.json", "users.yml"}, {"record.json", "users.yml", "authelia.sqlite3", "storage-key"}):
                raise ValueError
            if len(names) != len(set(names)) or sum(e.file_size for e in entries) > _LIMIT + 65536:
                raise ValueError
            if any(e.compress_type != zipfile.ZIP_STORED for e in entries):
                raise ValueError
            members = {name: archive.read(name) for name in names}
        record = _json_load(members.pop("record.json").decode())
        if (not isinstance(record, dict) or set(record) != {"schema", "classification", "created_at", "encrypted", "contains_personal_data", "contains_authentication_material", "files"}
                or record.get("schema") != "anvil-connect.auth-backup/v1" or record.get("classification") != _CLASSIFICATION
                or record.get("encrypted") is not False or record.get("contains_personal_data") is not True
                or record.get("contains_authentication_material") is not True):
            raise ValueError
        created_at = datetime.fromisoformat(record["created_at"])
        if (created_at.tzinfo is None or created_at.utcoffset() != timezone.utc.utcoffset(created_at)
                or created_at.astimezone(timezone.utc) != filename_created_at or created_at > now):
            raise ValueError
        if record.get("files") != {name: hashlib.sha256(value).hexdigest() for name, value in members.items()}:
            raise ValueError
        _database(members["users.yml"])
        if "authelia.sqlite3" in members:
            with closing(sqlite3.connect(":memory:")) as connection:
                connection.deserialize(members["authelia.sqlite3"])
                deadline = time.monotonic() + 5
                connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, sqlite3.Error, zipfile.BadZipFile) as exc:
        raise _invalid("Authentication archive is invalid or failed its integrity check.") from exc
    return created_at


def read_snapshot(path: Path, *, sha256: str | None = None, now: datetime | None = None) -> dict[str, bytes]:
    from .users import _invalid
    now = now or datetime.now(timezone.utc)
    raw = _snapshot_raw(path)
    if sha256 is not None and not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), _digest(sha256)):
        raise _invalid("Authentication archive does not match the retained checksum.")
    _snapshot_created_at(path, now=now, raw=raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return {name: archive.read(name) for name in archive.namelist() if name != "record.json"}


def restore(archive: str, destination: str, *, sha256: str, apply: bool = False) -> dict:
    """Extract a verified snapshot to a fresh directory; never revive live grants."""
    from .users import _exclusive, _invalid, _require_root
    _require_root()
    root = _path(destination)
    expected = _digest(sha256)
    if os.path.lexists(root):
        raise _invalid("Restore destination must be a fresh absolute directory.")
    private_directory(root.parent)
    members = read_snapshot(_path(archive), sha256=expected)
    result = {"destination": str(root), "classification": _CLASSIFICATION,
              "files": sorted(members), "sha256": expected, "applied": False, "activated": False}
    if apply:
        root.mkdir(mode=0o700)
        try:
            for name, raw in members.items():
                _exclusive(root / name, raw)
        except BaseException as exc:
            from .users import _partial
            raise _partial("Recovery extraction failed; inspect the private destination.", result) from exc
        result["applied"] = True
    return result
