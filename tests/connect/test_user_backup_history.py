"""Retention keeps recoverable history without treating archive names as authority."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import zipfile

import pytest

from anvil_serving.connect import user_backup


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="local Linux account administration")


_USERS = b'{"users":{"owner":{"displayname":"Owner","password":"$argon2id$fixture","email":"owner@example.test","groups":[]}}}'


def _archive(root: Path, created_at: datetime, suffix: str, *, recorded_at: datetime | None = None) -> Path:
    members = {"users.yml": _USERS}
    record = {"schema": "anvil-connect.auth-backup/v1", "classification": "restricted-authentication",
              "created_at": (recorded_at or created_at).isoformat(), "encrypted": False,
              "contains_personal_data": True, "contains_authentication_material": True,
              "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in members.items()}}
    path = root / f"auth-{created_at.strftime('%Y%m%dT%H%M%S%fZ')}-{suffix}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("record.json", json.dumps(record, sort_keys=True))
        archive.writestr("users.yml", _USERS)
    path.chmod(0o600)
    return path


def test_prune_keeps_daily_history_and_recent_events(tmp_path, monkeypatch):
    monkeypatch.setattr(user_backup.manage, "_safe_root_ancestors", lambda *_: None)
    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    dense = [_archive(root, now.replace(hour=1, minute=index), f"{index:012x}") for index in range(10)]
    daily = [_archive(root, now - timedelta(days=day, hours=2), f"{100 + day:012x}") for day in range(1, 14)]
    daily_old = [_archive(root, now - timedelta(days=day, hours=3), f"{200 + day:012x}") for day in range(1, 14)]
    old = _archive(root, now - timedelta(days=14), "0000000000ff")

    result = user_backup.prune(root, now=now)

    assert result["retention_days"] == 14
    assert result["retention_recent_copies"] == 7
    assert {path for path in dense[-7:] + daily if path.exists()} == {path for path in root.glob("auth-*.zip")}
    assert not old.exists() and not any(path.exists() for path in daily_old)


def test_prune_preserves_invalid_mismatched_and_future_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(user_backup.manage, "_safe_root_ancestors", lambda *_: None)
    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    mismatch = _archive(root, now - timedelta(days=30), "000000000001", recorded_at=now - timedelta(days=29))
    future = _archive(root, now + timedelta(seconds=1), "000000000002")
    foreign = root / "auth-20000101T000000000000Z-000000000003.zip"
    foreign.write_bytes(b"foreign")
    foreign.chmod(0o600)

    result = user_backup.prune(root, now=now)

    assert result["unrecognized_archives_preserved"] == 3
    assert mismatch.exists() and future.exists() and foreign.exists()


def test_read_snapshot_checks_supplied_checksum_before_decoding_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(user_backup.manage, "_safe_root_ancestors", lambda *_: None)
    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    archive = root / "auth-20260913T120000000000Z-000000000004.zip"
    archive.write_bytes(b"not a zip archive")
    archive.chmod(0o600)
    monkeypatch.setattr(user_backup, "_snapshot_created_at", lambda *_, **__: (_ for _ in ()).throw(AssertionError("decoded")))

    try:
        user_backup.read_snapshot(archive, sha256="0" * 64)
    except Exception as exc:
        assert getattr(exc, "code", None) == "connect_users_invalid"
    else:
        raise AssertionError("checksum mismatch was accepted")
