"""The default authentication backup schedule only trusts an installed manager."""
from contextlib import nullcontext
import hashlib
import json
import sys

import pytest

from anvil_serving.connect import manage, user_schedule


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="local Linux account administration")


def _release(prefix, version, content=b"#!/usr/bin/env python3\n"):
    release_name = "releases/" + version + "-" + "a" * 12
    release = prefix / release_name
    manager = release / "bin/anvil-connect-ctl"
    manager.parent.mkdir(parents=True)
    manager.write_bytes(content)
    manager.chmod(0o755)
    bundle = {"files": {"bin/anvil-connect-ctl": {"mode": 0o755, "size": manager.stat().st_size,
                                                        "sha256": hashlib.sha256(manager.read_bytes()).hexdigest()}}}
    bundle_raw = json.dumps(bundle).encode()
    (release / "bundle.json").write_bytes(bundle_raw)
    receipt = {"schema": "anvil-connect.installation/v1", "role": "gateway", "release": release_name,
               "files": ["bin/anvil-connect-ctl"], "manifest_sha256": hashlib.sha256(bundle_raw).hexdigest()}
    (release / "installation.json").write_text(json.dumps(receipt))
    return release_name, manager


def _installed_manager(tmp_path, monkeypatch):
    prefix = tmp_path / "opt/anvil-connect"
    release_name, manager = _release(prefix, "1.2.3")
    (prefix / "bin").mkdir()
    (prefix / "current").symlink_to(release_name)
    (prefix / "bin/anvil-connect-ctl").symlink_to("../current/bin/anvil-connect-ctl")
    monkeypatch.setattr(user_schedule, "_PREFIX", prefix)
    monkeypatch.setattr(user_schedule, "_MANAGER", prefix / "bin/anvil-connect-ctl")
    monkeypatch.setattr(manage, "_safe_root_ancestors", lambda *_: None)
    monkeypatch.setattr(user_schedule, "_installed_directory", lambda _: None)
    monkeypatch.setattr(user_schedule, "_installed_manager_file", lambda _: None)
    return manager


@pytest.mark.parametrize("deletions", [False, True])
def test_schedule_writes_exact_owned_units_and_reports_interruption(tmp_path, monkeypatch, deletions):
    prefix = "anvil-connect-user-delete" if deletions else "anvil-connect-auth-backup"
    operation = "process-deletions --confirm" if deletions else "backup --include-gateway --confirm"
    manager = _installed_manager(tmp_path, monkeypatch)
    manifest = tmp_path / "etc/anvil-connect/deployment.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    manifest.chmod(0o600)
    root = tmp_path / "etc/anvil-connect/rendered"
    root.mkdir()
    units = tmp_path / "units"
    units.mkdir()
    monkeypatch.setattr(user_schedule, "DEFAULT_MANIFEST", str(manifest))
    monkeypatch.setattr(user_schedule, "_manifest", lambda _: ({"config_root": str(root), "gateway": {"state_directory": str(tmp_path / "gateway-state")}}, root, manifest.parent / "backups"))
    monkeypatch.setattr(user_schedule, "_require_root", lambda: None)
    monkeypatch.setattr(manage, "_deployment_lock", lambda _: nullcontext())
    monkeypatch.setattr(manage, "_safe_dir", lambda *_: None)

    calls = []
    timer_state = [False, "disabled"]
    def runner(argv, _timeout, _identity):
        calls.append(argv)
        if argv[:3] == (manage._SYSTEMCTL, "show", "--property=ActiveState,UnitFileState"):
            return manage.RunResult(0, f"ActiveState={'active' if timer_state[0] else 'inactive'}\nUnitFileState={timer_state[1]}\n".encode())
        if argv[:3] == (manage._SYSTEMCTL, "show", "--property=LoadState,FragmentPath,DropInPaths"):
            if (units / argv[-1]).exists():
                return manage.RunResult(0, f"LoadState=loaded\nFragmentPath={units / argv[-1]}\nDropInPaths=\n".encode())
            return manage.RunResult(0, b"LoadState=not-found\nFragmentPath=\nDropInPaths=\n")
        if argv == (manage._SYSTEMCTL, "enable", "--now", prefix + ".timer"):
            timer_state[:] = [True, "enabled"]
        return manage.RunResult(0)

    preview = user_schedule.schedule(str(manifest), runner=runner, unit_root=units, deletions=deletions)
    assert preview["manager"] == str(manager)
    assert preview["calendar"] == ("every 10 seconds" if deletions else "daily 03:17 UTC")
    applied = user_schedule.schedule(str(manifest), apply=True, runner=runner, unit_root=units, deletions=deletions)
    service = (units / (prefix + ".service")).read_text()
    timer = (units / (prefix + ".timer")).read_text()
    assert "ExecStart=" + str(manager) + " users " + operation in service
    assert str(tmp_path) in service
    assert "OnUnitInactiveSec=10s" in timer if deletions else "Persistent=true" in timer and applied["retention_days"] == 14
    assert (manage._SYSTEMCTL, "enable", "--now", prefix + ".timer") in calls

    old_service = service
    release_name, replacement = _release(tmp_path / "opt/anvil-connect", "1.2.4", b"#!/usr/bin/env python3\nupdated\n")
    (tmp_path / "opt/anvil-connect/current").unlink()
    (tmp_path / "opt/anvil-connect/current").symlink_to(release_name)
    upgraded = user_schedule.schedule(str(manifest), apply=True, runner=runner, unit_root=units, deletions=deletions)
    assert upgraded["manager"] == str(replacement)
    assert "ExecStart=" + str(replacement) + " users " + operation in (units / (prefix + ".service")).read_text()
    assert (units / (prefix + ".service")).read_bytes() != old_service
    user_schedule.schedule(str(manifest), apply=True, runner=runner, unit_root=units, deletions=deletions)
    assert calls.count((manage._SYSTEMCTL, "enable", "--now", prefix + ".timer")) == 1


@pytest.mark.parametrize("fresh", [False, True])
def test_schedule_rolls_back_timer_after_interruption(tmp_path, monkeypatch, fresh):
    manager = _installed_manager(tmp_path, monkeypatch)
    manifest = tmp_path / "etc/anvil-connect/deployment.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    manifest.chmod(0o600)
    root = tmp_path / "etc/anvil-connect/rendered"
    root.mkdir()
    units = tmp_path / "units"
    units.mkdir()
    monkeypatch.setattr(user_schedule, "DEFAULT_MANIFEST", str(manifest))
    monkeypatch.setattr(user_schedule, "_manifest", lambda _: ({"config_root": str(root), "gateway": {"state_directory": str(tmp_path / "gateway-state")}}, root, manifest.parent / "backups"))
    monkeypatch.setattr(user_schedule, "_require_root", lambda: None)
    monkeypatch.setattr(manage, "_deployment_lock", lambda _: nullcontext())
    monkeypatch.setattr(manage, "_safe_dir", lambda *_: None)
    old_service = user_schedule._service(manager, tuple(sorted({root.parent, manifest.parent, tmp_path}, key=str)))
    if not fresh:
        (units / "anvil-connect-auth-backup.service").write_bytes(old_service)
        (units / "anvil-connect-auth-backup.timer").write_bytes(user_schedule._timer())
    release_name, _ = _release(tmp_path / "opt/anvil-connect", "1.2.4", b"#!/usr/bin/env python3\nupdated\n")
    (tmp_path / "opt/anvil-connect/current").unlink()
    (tmp_path / "opt/anvil-connect/current").symlink_to(release_name)
    calls = []
    timer_state = [False, "disabled"]

    def runner(argv, _timeout, _identity):
        calls.append(argv)
        if argv[:3] == (manage._SYSTEMCTL, "show", "--property=LoadState,FragmentPath,DropInPaths"):
            if (units / argv[-1]).exists():
                return manage.RunResult(0, f"LoadState=loaded\nFragmentPath={units / argv[-1]}\nDropInPaths=\n".encode())
            return manage.RunResult(0, b"LoadState=not-found\nFragmentPath=\nDropInPaths=\n")
        if argv[:3] == (manage._SYSTEMCTL, "show", "--property=ActiveState,UnitFileState"):
            return manage.RunResult(0, f"ActiveState={'active' if timer_state[0] else 'inactive'}\nUnitFileState={timer_state[1]}\n".encode())
        if argv == (manage._SYSTEMCTL, "enable", "--now", "anvil-connect-auth-backup.timer"):
            timer_state[:] = [True, "enabled"]
            raise KeyboardInterrupt
        if argv == (manage._SYSTEMCTL, "disable", "--now", "anvil-connect-auth-backup.timer"):
            timer_state[:] = [False, "disabled"]
        return manage.RunResult(0)

    try:
        user_schedule.schedule(str(manifest), apply=True, runner=runner, unit_root=units)
    except manage.ManageError as exc:
        assert exc.may_have_executed
    else:
        raise AssertionError("interrupted timer activation was accepted")
    assert (manage._SYSTEMCTL, "disable", "--now", "anvil-connect-auth-backup.timer") in calls
    if fresh:
        assert not (units / "anvil-connect-auth-backup.service").exists()
        assert not (units / "anvil-connect-auth-backup.timer").exists()
    else:
        assert (units / "anvil-connect-auth-backup.service").read_bytes() == old_service
        assert (units / "anvil-connect-auth-backup.timer").read_bytes() == user_schedule._timer()
    assert calls.count((manage._SYSTEMCTL, "daemon-reload")) == 2
