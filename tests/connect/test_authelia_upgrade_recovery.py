"""Independent interruption coverage for durable Authelia upgrades."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import json
import os
import sqlite3
from pathlib import Path

import pytest

from anvil_serving.connect import manage
from anvil_serving.connect import user_backup
from tests.connect.test_manage import _LINUX_AMD64, SyntheticRunner, _executable, deployment


pytestmark = pytest.mark.skipif(not _LINUX_AMD64, reason="Connect lifecycle tests require Linux amd64")


class RecordingRunner(SyntheticRunner):
    """Capture the installed IdP unit bytes at every provider start."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.authelia_start_units: list[bytes] = []

    def __call__(self, argv, timeout, identity):
        if (self.unit_root is not None and argv[-1] == "anvil-connect-authelia.service"
                and (argv[1] in {"start", "restart"} or (argv[1] == "enable" and "--now" in argv))):
            self.authelia_start_units.append((self.unit_root / argv[-1]).read_bytes())
        return super().__call__(argv, timeout, identity)


def _prepared_upgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    users_file = tmp_path / "users.yml"
    users_file.write_text("users: {}\n")
    users_file.chmod(0o600)
    storage_key = tmp_path / "storage-key"
    storage_key.write_bytes(b"synthetic storage key")
    storage_key.chmod(0o600)
    state = Path(value["authelia"]["state_directory"])
    state.mkdir(mode=0o700)
    database = state / "authelia.sqlite3"
    sqlite3.connect(database).close()
    database.chmod(0o600)
    value["authelia"]["users_file"] = str(users_file)
    value["authelia"]["storage_encryption_key_file"] = str(storage_key)
    manifest.write_text(json.dumps(value))
    units = tmp_path / "units"
    units.mkdir(mode=0o755)
    initial = SyntheticRunner()
    initial.unit_root = units
    targets = manage._targets(value, None)
    manage.up_many(manifest, targets, apply=True, runner=initial, unit_root=units)

    archive = tmp_path / "backups" / "auth-upgrade.zip"
    archive.parent.mkdir(mode=0o700)
    archive.write_bytes(b"synthetic validated archive")
    archive.chmod(0o600)
    receipt = {"file": str(archive), "sha256": "a" * 64}
    monkeypatch.setattr(manage, "_safe_authelia_users_file", lambda *_: users_file)
    monkeypatch.setattr(user_backup, "snapshot", lambda *_args, **_kwargs: receipt)
    monkeypatch.setattr(user_backup, "read_snapshot", lambda *_args, **_kwargs: {"users.yml": b"users: {}\n"})
    original_identity = manage._role_service_identity

    def identity(data, role, identifier=None):
        if role == "idp":
            return manage.ServiceIdentity(os.geteuid(), os.getegid())
        return original_identity(data, role, identifier)

    monkeypatch.setattr(manage, "_role_service_identity", identity)
    new_native = tmp_path / "anvil-connect-v2"
    new_caddy = tmp_path / "caddy-v2"
    new_authelia = tmp_path / "authelia-v2"
    _executable(new_native, b"native-v2")
    caddy_digest = _executable(new_caddy, b"caddy-v2")
    authelia_digest = _executable(new_authelia, b"authelia-v2")
    changed = copy.deepcopy(value)
    changed["binary"] = str(new_native)
    changed["components"] = {"caddy": str(new_caddy), "authelia": str(new_authelia)}
    manifest.write_text(json.dumps(changed))
    monkeypatch.setattr(manage, "_component_lock", lambda: {"caddy": caddy_digest, "authelia": authelia_digest})
    return manifest, value, changed, targets, units


def _retry(manifest, targets, units):
    runner = RecordingRunner()
    runner.unit_root = units
    result = manage.up_many(manifest, targets, upgrade=True, apply=True, runner=runner, unit_root=units)
    return result, runner


@pytest.mark.parametrize("point", ("prepared", "old-root", "units"))
def test_interrupted_authelia_upgrade_recovers_forward_with_new_provider_only(tmp_path, monkeypatch, point):
    manifest, _old, changed, targets, units = _prepared_upgrade(tmp_path, monkeypatch)
    root = Path(changed["config_root"])
    marker = manage._authelia_upgrade_marker(root)
    original_activate = manage._activate
    original_replace = manage.os.replace
    original_write = manage._write_atomic
    injected = False

    if point == "prepared":
        def interrupt_activate(*args, **kwargs):
            raise KeyboardInterrupt("after durable marker")
        monkeypatch.setattr(manage, "_activate", interrupt_activate)
    elif point == "old-root":
        def interrupt_replace(source, destination):
            nonlocal injected
            original_replace(source, destination)
            if source == root and destination.name.startswith(".rendered.anvil-connect-rollback-"):
                injected = True
                raise KeyboardInterrupt("after old root move")
        monkeypatch.setattr(manage.os, "replace", interrupt_replace)
    else:
        def interrupt_write(path, raw, mode=0o644):
            nonlocal injected
            original_write(path, raw, mode)
            if Path(path).parent == units and not injected:
                injected = True
                raise KeyboardInterrupt("during managed unit writes")
        monkeypatch.setattr(manage, "_write_atomic", interrupt_write)

    runner = SyntheticRunner()
    runner.unit_root = units
    with pytest.raises(KeyboardInterrupt):
        manage.up_many(manifest, targets, upgrade=True, apply=True, runner=runner, unit_root=units)
    assert marker.exists()
    assert point == "prepared" or injected

    monkeypatch.setattr(manage, "_activate", original_activate)
    monkeypatch.setattr(manage.os, "replace", original_replace)
    monkeypatch.setattr(manage, "_write_atomic", original_write)
    result, recovered = _retry(manifest, targets, units)
    assert result["authelia_upgrade_recovery_completed"] is True
    assert not marker.exists()
    assert recovered.authelia_start_units
    assert all(b"authelia-v2" in source for source in recovered.authelia_start_units)
    assert "authelia-v2" in (units / "anvil-connect-authelia.service").read_text()


def test_recovery_refuses_foreign_unit_without_overwrite(tmp_path, monkeypatch):
    manifest, _old, changed, targets, units = _prepared_upgrade(tmp_path, monkeypatch)
    original_activate = manage._activate
    monkeypatch.setattr(manage, "_activate", lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt("prepared")))
    runner = SyntheticRunner(); runner.unit_root = units
    with pytest.raises(KeyboardInterrupt):
        manage.up_many(manifest, targets, upgrade=True, apply=True, runner=runner, unit_root=units)
    monkeypatch.setattr(manage, "_activate", original_activate)
    foreign = units / "anvil-connect-authelia.service"
    foreign.write_bytes(b"foreign unit\n")
    with pytest.raises(manage.ManageError, match="migration recovery is pending"):
        _retry(manifest, targets, units)
    assert foreign.read_bytes() == b"foreign unit\n"


def test_ordinary_up_refuses_upgrade_marker_created_after_outer_check_under_lock(tmp_path, monkeypatch):
    manifest, _old, changed, targets, units = _prepared_upgrade(tmp_path, monkeypatch)
    root = Path(changed["config_root"])
    marker = manage._authelia_upgrade_marker(root)
    before_generation = manage._verify_owned_tree(root)[0]
    before_units = {path.name: path.read_bytes() for path in units.iterdir()}
    original_lock = manage._deployment_lock

    @contextmanager
    def inject_marker(locked_root):
        with original_lock(locked_root):
            if locked_root == root:
                marker.write_text("pending\n")
            yield

    monkeypatch.setattr(manage, "_deployment_lock", inject_marker)
    runner = SyntheticRunner(); runner.unit_root = units
    with pytest.raises(manage.ManageError, match="migration recovery is pending"):
        manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    assert marker.exists()
    assert manage._verify_owned_tree(root)[0] == before_generation
    assert {path.name: path.read_bytes() for path in units.iterdir()} == before_units
    assert not any(call[:2] in {
        (manage._SYSTEMCTL, "daemon-reload"), (manage._SYSTEMCTL, "enable"),
        (manage._SYSTEMCTL, "restart"), (manage._SYSTEMCTL, "stop"),
    } for call in runner.calls)


def test_handled_upgrade_failure_keeps_pending_marker_and_never_starts_old_provider(tmp_path, monkeypatch):
    manifest, _old, changed, targets, units = _prepared_upgrade(tmp_path, monkeypatch)
    original_activate = manage._activate
    monkeypatch.setattr(manage, "_activate", lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt("prepared")))
    interrupted = SyntheticRunner(); interrupted.unit_root = units
    with pytest.raises(KeyboardInterrupt):
        manage.up_many(manifest, targets, upgrade=True, apply=True, runner=interrupted, unit_root=units)
    monkeypatch.setattr(manage, "_activate", original_activate)

    failed = RecordingRunner(fail_start="anvil-connect-authelia.service")
    failed.unit_root = units
    with pytest.raises(manage.ManageError, match="migration recovery is pending"):
        manage.up_many(manifest, targets, upgrade=True, apply=True, runner=failed, unit_root=units)
    assert manage._authelia_upgrade_marker(Path(changed["config_root"])).exists()
    assert failed.authelia_start_units
    assert all(b"authelia-v2" in source for source in failed.authelia_start_units)
