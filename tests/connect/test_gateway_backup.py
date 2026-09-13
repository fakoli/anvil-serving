from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile
import pytest

from anvil_serving.connect import gateway_backup, user_backup
from anvil_serving.connect import manage, recovery


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="local Linux account administration")


def _pair(root: Path, gateway_root: Path, created: datetime, suffix: str) -> tuple[Path, Path, Path]:
    users = b'{"users":{"owner":{"displayname":"Owner","password":"$argon2id$fixture","email":"owner@example.test","groups":[]}}}'
    auth = root / f"auth-{created.strftime('%Y%m%dT%H%M%S%fZ')}-{suffix}.zip"
    record = {"schema": "anvil-connect.auth-backup/v1", "classification": "restricted-authentication", "created_at": created.isoformat(), "encrypted": False, "contains_personal_data": True, "contains_authentication_material": True, "files": {"users.yml": hashlib.sha256(users).hexdigest()}}
    with zipfile.ZipFile(auth, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("record.json", json.dumps(record, sort_keys=True)); archive.writestr("users.yml", users)
    auth.chmod(0o600)
    gateway = gateway_root / ("gateway-" + created.strftime('%Y%m%dT%H%M%S%fZ') + "-" + suffix + ".backup")
    gateway.write_bytes(b"gateway-authority"); gateway.chmod(0o600)
    receipt = root / ("gateway-" + created.strftime('%Y%m%dT%H%M%S%fZ') + "-" + suffix + ".receipt.json")
    receipt.write_text(json.dumps({"schema": "anvil-connect.gateway-backup-receipt/v1", "created_at": created.isoformat(), "auth_file": str(auth), "auth_sha256": hashlib.sha256(auth.read_bytes()).hexdigest(), "gateway_file": str(gateway), "gateway_sha256": hashlib.sha256(gateway.read_bytes()).hexdigest(), "native_sha256": "a" * 64, "gateway_uid": os.geteuid()}))
    receipt.chmod(0o600)
    return auth, gateway, receipt


def test_pair_retention_prunes_only_valid_complete_pairs(tmp_path, monkeypatch):
    monkeypatch.setattr(user_backup.manage, "_safe_root_ancestors", lambda *_: None)
    root, gateway = tmp_path / "auth", tmp_path / "gateway"
    root.mkdir(mode=0o700); gateway.mkdir(mode=0o700)
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    retained = [_pair(root, gateway, now - timedelta(days=day), f"{day:012x}") for day in range(14)]
    old = _pair(root, gateway, now - timedelta(days=14), "0000000000ff")
    user_backup.prune(root, now=now)
    assert all(path.exists() for path in old), "ordinary account backups must preserve paired auth archives"
    result = gateway_backup.prune(root, gateway, os.geteuid(), now=now)
    assert result["retention_days"] == 14 and result["retention_recent_copies"] == 7
    assert all(all(path.exists() for path in pair) for pair in retained)
    assert not any(path.exists() for path in old)


def test_pair_retention_preserves_corrupt_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(user_backup.manage, "_safe_root_ancestors", lambda *_: None)
    root, gateway = tmp_path / "auth", tmp_path / "gateway"
    root.mkdir(mode=0o700); gateway.mkdir(mode=0o700)
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    _, _, receipt = _pair(root, gateway, now - timedelta(days=30), "000000000001")
    receipt.write_text("{}")
    receipt.chmod(0o600)
    assert gateway_backup.prune(root, gateway, os.geteuid(), now=now)["unrecognized_pairs_preserved"] == 1
    assert receipt.exists()


@pytest.mark.parametrize("fail_ready", [False, True])
def test_gateway_snapshot_stops_only_gateway_and_restores_readiness(tmp_path, monkeypatch, fail_ready):
    root, state, auth_root, units = tmp_path / "rendered", tmp_path / "gateway", tmp_path / "auth", tmp_path / "units"
    for path in (root / "systemd", state, auth_root, units, tmp_path / "backups"): path.mkdir(parents=True, exist_ok=True)
    native = tmp_path / "native"; native.write_bytes(b"native"); native.chmod(0o755)
    source = f"[Service]\nExecStart={native} gateway\n".encode(); (root / "systemd" / gateway_backup._UNIT).write_bytes(source); (units / gateway_backup._UNIT).write_bytes(source)
    auth = auth_root / "auth-20260913T120000000000Z-000000000001.zip"; auth.write_bytes(b"auth"); auth.chmod(0o600)
    data = {"config_root": str(root), "binary": str(native), "gateway": {"state_directory": str(state)}, "service_identities": {"gateway": {"uid": os.geteuid(), "gid": os.getegid()}}}
    monkeypatch.setattr(manage, "_safe_private_runtime_directory", lambda *_: None); monkeypatch.setattr(manage, "_safe_root_ancestors", lambda *_: None); monkeypatch.setattr(manage, "_verify_owned_tree", lambda *_: None)
    monkeypatch.setattr(manage, "_role_service_identity", lambda *_: manage.ServiceIdentity(os.geteuid(), os.getegid()))
    monkeypatch.setattr(manage, "_verified_binaries", lambda *_: {"native": "a" * 64}); monkeypatch.setattr(manage, "_bound_active", lambda *_: None)
    monkeypatch.setattr(manage, "_verify_unit", lambda *_: None); monkeypatch.setattr(manage, "_unit_metadata", lambda *_ , **__: None)
    monkeypatch.setattr(recovery, "_private_directory", lambda *_: None); monkeypatch.setattr(gateway_backup, "private_directory", lambda *_ , **__: None)
    active, calls, locked = [True], [], [False]
    @contextmanager
    def lock(_root):
        assert not locked[0]
        locked[0] = True
        try:
            yield
        finally:
            locked[0] = False
    monkeypatch.setattr(manage, "_deployment_lock", lock)
    def runner(argv, *_):
        assert locked[0]
        calls.append(argv)
        if argv[:2] == (manage._SYSTEMCTL, "stop"): active[0] = False
        if argv[:2] == (manage._SYSTEMCTL, "start"): active[0] = True
        if argv[:3] == (manage._SYSTEMCTL, "show", "--property=ActiveState,UnitFileState"):
            return manage.RunResult(0, f"ActiveState={'active' if active[0] else 'inactive'}\nUnitFileState=enabled\n".encode())
        return manage.RunResult(0)
    def backup(_data, output_path, *_):
        assert locked[0]
        assert not active[0]; Path(output_path).write_bytes(b"gateway"); Path(output_path).chmod(0o600)
        return {"sha256": hashlib.sha256(b"gateway").hexdigest(), "native_sha256": "a" * 64}
    def ready(*_):
        assert locked[0]
        calls.append(("ready",))
        if fail_ready:
            raise manage.ManageError("synthetic readiness failure")
    monkeypatch.setattr(recovery, "_backup_locked", backup); monkeypatch.setattr(manage, "_gateway_ready", ready)
    def run():
        return gateway_backup.snapshot(data, str(tmp_path / "deployment.json"), {"file": str(auth), "sha256": "b" * 64}, runner=runner, unit_root=units)
    if fail_ready:
        with pytest.raises(manage.ManageError) as caught:
            run()
        result = caught.value.recovery["gateway_backup"]
        assert caught.value.may_have_executed and Path(result["file"]).exists() and result["sha256"]
    else:
        result = run()
    assert not locked[0]
    assert active[0] and (manage._SYSTEMCTL, "stop", gateway_backup._UNIT) in calls and (manage._SYSTEMCTL, "start", gateway_backup._UNIT) in calls and ("ready",) in calls
    assert Path(result["receipt"]).stat().st_mode & 0o777 == 0o600


def test_gateway_snapshot_restarts_after_native_backup_failure(tmp_path, monkeypatch):
    # Reuse the success fixture's narrow contracts, then make the native step fail.
    # The finally block is the behavior under test: no pair is reported and the
    # exact prior active gateway returns before the error escapes.
    # Kept local to avoid a live service or recovery archive.
    root, state, units = tmp_path / "rendered", tmp_path / "gateway", tmp_path / "units"
    for path in (root / "systemd", state, units, tmp_path / "backups"): path.mkdir(parents=True, exist_ok=True)
    native = tmp_path / "native"; native.write_bytes(b"native"); native.chmod(0o755)
    source = f"[Service]\nExecStart={native} gateway\n".encode(); (root / "systemd" / gateway_backup._UNIT).write_bytes(source); (units / gateway_backup._UNIT).write_bytes(source)
    data = {"config_root": str(root), "binary": str(native), "gateway": {"state_directory": str(state)}, "service_identities": {"gateway": {"uid": os.geteuid(), "gid": os.getegid()}}}
    for name, value in (("_safe_private_runtime_directory", lambda *_: None), ("_safe_root_ancestors", lambda *_: None), ("_verify_owned_tree", lambda *_: None), ("_role_service_identity", lambda *_: manage.ServiceIdentity(os.geteuid(), os.getegid())), ("_verified_binaries", lambda *_: {"native": "a" * 64}), ("_bound_active", lambda *_: None), ("_verify_unit", lambda *_: None), ("_unit_metadata", lambda *_, **__: None), ("_gateway_ready", lambda *_: None)):
        monkeypatch.setattr(manage, name, value)
    monkeypatch.setattr(recovery, "_private_directory", lambda *_: None); monkeypatch.setattr(gateway_backup, "private_directory", lambda *_, **__: None)
    active = [True]
    def runner(argv, *_):
        if argv[:2] == (manage._SYSTEMCTL, "stop"): active[0] = False
        if argv[:2] == (manage._SYSTEMCTL, "start"): active[0] = True
        if argv[:3] == (manage._SYSTEMCTL, "show", "--property=ActiveState,UnitFileState"):
            return manage.RunResult(0, f"ActiveState={'active' if active[0] else 'inactive'}\nUnitFileState=enabled\n".encode())
        return manage.RunResult(0)
    monkeypatch.setattr(recovery, "_backup_locked", lambda *_, **__: (_ for _ in ()).throw(manage.ManageError("synthetic")))
    with pytest.raises(manage.ManageError):
        gateway_backup.snapshot(data, str(tmp_path / "deployment.json"), {"file": "/private/auth.zip", "sha256": "b" * 64}, runner=runner, unit_root=units)
    assert active[0]
