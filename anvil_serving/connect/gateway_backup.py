"""Sequential paired backups for authentication state and gateway authorities."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import re

from . import manage, recovery
from .user_backup import private_directory

_UNIT = "anvil-connect-gateway.service"
_RECEIPT = re.compile(r"gateway-(?P<created_at>\d{8}T\d{12}Z)-[a-f0-9]{12}\.receipt\.json\Z")
_RETENTION_DAYS = 14
_RETENTION_RECENT_COPIES = 7


def _fsync(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def snapshot(data: dict, manifest: str, auth_backup: dict, *, runner=None,
             unit_root: Path = Path("/etc/systemd/system")) -> dict:
    """Capture authorities after the completed authentication snapshot.

    This is intentionally sequential: an auth snapshot can survive a later
    gateway failure, but no pair is reported until both receipts are durable.
    """
    with manage._deployment_lock(Path(data["config_root"])):
        return _snapshot_locked(data, manifest, auth_backup, runner=runner, unit_root=unit_root)


def _snapshot_locked(data: dict, manifest: str, auth_backup: dict, *, runner, unit_root: Path) -> dict:
    target = manage.Target("gateway")
    root = Path(data["config_root"])
    state = Path(data["gateway"]["state_directory"])
    identity = manage._role_service_identity(data, "gateway")
    if identity is None:
        raise manage.ManageError("paired gateway backup requires isolated service identities")
    manage._safe_private_runtime_directory(state, identity.uid, identity.gid)
    # Recovery archives are not runtime state: keep them in a dedicated
    # service-owned sibling so a state reset cannot erase authorities.
    directory = state.parent / "gateway-backups"
    manage._safe_root_ancestors(directory)
    if not directory.exists():
        os.mkdir(directory, 0o700)
        os.chown(directory, identity.uid, identity.gid)
    recovery._private_directory(directory, identity.uid)
    if any((item / ".git").exists() for item in (directory, *directory.parents)):
        raise manage.ManageError("gateway backup directory must be outside Git")
    created = datetime.now(timezone.utc)
    stem = "gateway-" + created.strftime("%Y%m%dT%H%M%S%fZ") + "-" + secrets.token_hex(6)
    archive = directory / (stem + ".backup")
    receipt_root = Path(manifest).parent / "backups"
    private_directory(receipt_root, create=True)
    receipt = receipt_root / (stem + ".receipt.json")
    if receipt.exists():
        raise manage.ManageError("gateway backup receipt already exists")
    manage._verify_owned_tree(root)
    digests = manage._verified_binaries(data, target)
    manage._bound_active(data, target, digests)
    source = manage._read_unit(root / "systemd" / _UNIT)
    if source is None or manage._unit_exec_path(source) != Path(data["binary"]):
        raise manage.ManageError("gateway unit does not match its declared executable")
    manage._verify_unit(unit_root, _UNIT, source)
    manage._unit_metadata(runner, unit_root, (_UNIT,), present=True)
    was_active, enabled = manage._unit_state(runner, _UNIT)
    result = {"file": str(archive), "receipt": str(receipt), "native_sha256": digests["native"],
              "authentication_backup": {"file": auth_backup["file"], "sha256": auth_backup["sha256"]},
              "gateway_was_active": was_active, "gateway_unit_state": enabled}
    stopped = False
    try:
        if was_active:
            stopped = True
            manage._action(runner, (manage._SYSTEMCTL, "stop", _UNIT), 30, "gateway stop failed")
        if manage._unit_state(runner, _UNIT)[0]:
            raise manage.ManageError("gateway is still active")
        native = recovery._backup_locked(data, archive, runner, identity)
        result["sha256"] = native["sha256"]
        record = {"schema": "anvil-connect.gateway-backup-receipt/v1", "created_at": created.isoformat(),
                  "auth_file": auth_backup["file"], "auth_sha256": auth_backup["sha256"],
                  "gateway_file": str(archive), "gateway_sha256": native["sha256"], "native_sha256": digests["native"],
                  "gateway_uid": identity.uid}
        raw = (json.dumps(record, sort_keys=True) + "\n").encode()
        from .users import _exclusive
        _exclusive(receipt, raw)
        _fsync(directory)
        _fsync(directory.parent)
        _fsync(receipt_root)
        _fsync(receipt_root.parent)
        result.update({"sha256": native["sha256"], "receipt_sha256": hashlib.sha256(raw).hexdigest()})
    except BaseException as exc:
        exc.recovery = {"gateway_backup": result}
        raise
    finally:
        if stopped:
            try:
                manage._action(runner, (manage._SYSTEMCTL, "start", _UNIT), 30, "gateway restart failed")
                if not manage._unit_state(runner, _UNIT)[0]:
                    raise manage.ManageError("gateway did not become active", may_have_executed=True)
                manage._gateway_ready(data, runner)
            except BaseException as exc:
                error = manage.ManageError("Gateway recovery failed after backup; inspect the retained files and service health.", may_have_executed=True)
                error.recovery = {"gateway_backup": result}
                raise error from exc
    return result


def prune(receipt_root: Path, gateway_root: Path, expected_uid: int, *, now: datetime | None = None) -> dict:
    """Prune only independently validated auth/gateway receipt pairs."""
    from .user_backup import read_snapshot
    private_directory(receipt_root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() != timezone.utc.utcoffset(now):
        raise ValueError("backup retention time must be UTC-aware")
    candidates, preserved = [], 0
    for receipt in sorted(receipt_root.iterdir()):
        match = _RECEIPT.fullmatch(receipt.name)
        if match is None:
            continue
        try:
            info = receipt.lstat()
            if not (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                    and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= 8192):
                raise ValueError
            raw = manage._read_regular(receipt, 8192)
            record = json.loads(raw.decode())
            required = {"schema", "created_at", "auth_file", "auth_sha256", "gateway_file", "gateway_sha256", "native_sha256", "gateway_uid"}
            if set(record) != required or record["schema"] != "anvil-connect.gateway-backup-receipt/v1":
                raise ValueError
            created = datetime.fromisoformat(record["created_at"])
            named = datetime.strptime(match["created_at"], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
            if created.tzinfo is None or created.utcoffset() != timezone.utc.utcoffset(created) or created.astimezone(timezone.utc) != named or created > now:
                raise ValueError
            auth, gateway = Path(record["auth_file"]), Path(record["gateway_file"])
            stem = receipt.name.removesuffix(".receipt.json")
            if auth.parent != receipt_root or gateway.parent != gateway_root or gateway.name != stem + ".backup":
                raise ValueError
            read_snapshot(auth, sha256=record["auth_sha256"], now=now)
            gateway_info = gateway.lstat()
            if (not isinstance(record["gateway_uid"], int) or isinstance(record["gateway_uid"], bool)
                    or not stat.S_ISREG(gateway_info.st_mode) or gateway_info.st_nlink != 1 or gateway_info.st_uid != expected_uid or record["gateway_uid"] != expected_uid or stat.S_IMODE(gateway_info.st_mode) != 0o600
                    or gateway_info.st_size < 1 or gateway_info.st_size > 1024 * 1024
                    or hashlib.sha256(manage._read_regular(gateway, 1024 * 1024)).hexdigest() != record["gateway_sha256"]
                    or not all(isinstance(record[key], str) and re.fullmatch(r"[0-9a-f]{64}", record[key]) for key in ("auth_sha256", "gateway_sha256", "native_sha256"))):
                raise ValueError
            candidates.append((created, receipt, auth, gateway, (info.st_dev, info.st_ino), (auth.stat().st_dev, auth.stat().st_ino), (gateway_info.st_dev, gateway_info.st_ino)))
        except Exception:
            preserved += 1
    candidates.sort(key=lambda item: item[0])
    retained = {item[1] for item in candidates[-_RETENTION_RECENT_COPIES:]}
    first_day = now.date().toordinal() - (_RETENTION_DAYS - 1)
    newest = {}
    for item in candidates:
        if item[0].date().toordinal() >= first_day:
            newest[item[0].date()] = item[1]
    retained.update(newest.values())
    pruned = 0
    for _, receipt, auth, gateway, receipt_inode, auth_inode, gateway_inode in candidates:
        if receipt in retained:
            continue
        for path, inode in ((receipt, receipt_inode), (auth, auth_inode), (gateway, gateway_inode)):
            info = path.lstat()
            expected_uid_for_path = expected_uid if path == gateway else os.geteuid()
            if ((info.st_dev, info.st_ino) != inode or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != expected_uid_for_path):
                raise RuntimeError("backup pair changed during retention")
            path.unlink()
        pruned += 1
    if pruned:
        _fsync(receipt_root)
        _fsync(gateway_root)
    return {"retention_days": _RETENTION_DAYS, "retention_recent_copies": _RETENTION_RECENT_COPIES,
            "retention_pairs_pruned": pruned, "unrecognized_pairs_preserved": preserved}
