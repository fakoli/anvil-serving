"""The immediate backup trigger executes only the verified installed worker."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving.connect import cli, manage, user_schedule
from anvil_serving.operator_output import UsageError


@pytest.fixture
def installed_worker(tmp_path, monkeypatch):
    prefix = tmp_path / "custom-installation"
    release_name = "releases/0.2.0-" + "a" * 12
    release = prefix / release_name
    manager = release / "bin/anvil-connect-ctl"
    manager.parent.mkdir(parents=True)
    manager.write_bytes(b"synthetic immutable manager")
    manager.chmod(0o755)
    bundle = json.dumps({"files": {"bin/anvil-connect-ctl": {
        "mode": 0o755, "size": manager.stat().st_size,
        "sha256": hashlib.sha256(manager.read_bytes()).hexdigest()}}}).encode()
    (release / "bundle.json").write_bytes(bundle)
    (release / "installation.json").write_text(json.dumps({
        "schema": "anvil-connect.installation/v1", "role": "gateway", "release": release_name,
        "files": ["bin/anvil-connect-ctl"], "manifest_sha256": hashlib.sha256(bundle).hexdigest()}))
    alias = tmp_path / "selected-manager"
    alias.symlink_to(manager)
    monkeypatch.setattr(user_schedule, "_MANAGER", alias)
    monkeypatch.setattr(user_schedule, "_installed_directory", lambda *_: None)
    monkeypatch.setattr(user_schedule, "_installed_manager_file", lambda *_: None)
    monkeypatch.setattr(manage, "_safe_root_ancestors", lambda *_: None)
    monkeypatch.setattr(user_schedule, "_require_root", lambda: None)
    monkeypatch.setattr(manage, "_deployment_lock", lambda *_: pytest.fail("parent must not acquire deployment lock"))
    manifest = tmp_path / "deployment.json"
    manifest.write_text("{}")
    root = tmp_path / "rendered"
    data = {"config_root": str(root), "gateway": {"state_directory": str(tmp_path / "state")}}
    monkeypatch.setattr(user_schedule, "_manifest", lambda path: (data, root, tmp_path / "backups"))
    units = tmp_path / "units"
    units.mkdir()
    unit = units / user_schedule._SERVICE
    unit.write_bytes(user_schedule._service(manager, (tmp_path,)))
    unit.chmod(0o644)
    original_lstat = Path.lstat
    def root_owned_metadata(path):
        value = original_lstat(path)
        if path in {alias, unit}:
            fields = list(value)
            fields[4] = 0
            return os.stat_result(fields)
        return value
    monkeypatch.setattr(Path, "lstat", root_owned_metadata)
    before = dict(zip(user_schedule._BACKUP_PROPERTIES,
                      ("failed", "failed", "0", "0", "", "exit-code", "1", "2", "100", "1" * 32, "no")))
    after = {**before, "ActiveState": "inactive", "SubState": "dead", "Result": "success",
             "ExecMainStatus": "0", "ExecMainStartTimestampMonotonic": "200", "InvocationID": "2" * 32}
    fixture = SimpleNamespace(manager=manager, alias=alias, manifest=manifest, units=units, unit=unit,
                              before=before, after=after, calls=[], started=False, failure=None,
                              dropins="", preview_ok=True, after_preview=None)
    def runner(argv, timeout, identity):
        assert identity is None
        fixture.calls.append((argv, timeout))
        if argv[:3] == (manage._SYSTEMCTL, "show", "--property=LoadState,FragmentPath,DropInPaths"):
            return manage.RunResult(0, f"LoadState=loaded\nFragmentPath={unit}\nDropInPaths={fixture.dropins}\n".encode())
        if argv[:2] == (manage._SYSTEMCTL, "show"):
            state = fixture.after if fixture.started else fixture.before
            return manage.RunResult(0, "".join(f"{key}={value}\n" for key, value in state.items()).encode())
        if argv == (str(manager), "users", "backup", "--include-gateway", "--dry-run"):
            if fixture.after_preview:
                fixture.after_preview()
            return manage.RunResult(0, json.dumps({"ok": fixture.preview_ok, "data": {
                "applied": False, "operation": "backup", "include_gateway": True}}).encode())
        assert argv == (manage._SYSTEMCTL, "start", user_schedule._SERVICE)
        assert timeout == 130
        fixture.started = True
        if fixture.failure:
            raise fixture.failure
        return manage.RunResult(0)
    fixture.runner = runner
    fixture.run = lambda **kwargs: user_schedule.run_backup(str(manifest), runner=runner, unit_root=units, **kwargs)
    return fixture


def test_scheduled_backup_previews_selected_custom_prefix_then_verifies_new_success(installed_worker):
    fixture = installed_worker
    assert user_schedule._selected_manager() == fixture.manager
    preview = fixture.run()
    assert preview["applied"] is False and preview["completion_verified"] is False
    assert not fixture.started
    result = fixture.run(apply=True)
    assert result["completion_verified"] is True and result["exit_status"] == 0
    assert result["invocation_id"] == fixture.after["InvocationID"]
    assert result["previous_invocation_id"] == fixture.before["InvocationID"]
    mutations = [args for args, _ in fixture.calls if args[:2] == (manage._SYSTEMCTL, "start")]
    assert mutations == [(manage._SYSTEMCTL, "start", user_schedule._SERVICE)]


@pytest.mark.parametrize("failure", ["unit-command", "unit-mode", "bundle-digest", "dropins", "queued", "running", "reload", "preview", "manifest-drift", "alias-drift"])
def test_scheduled_backup_rejects_custody_or_state_drift_before_start(installed_worker, failure):
    fixture = installed_worker
    if failure == "unit-command":
        fixture.unit.write_bytes(fixture.unit.read_bytes().replace(b" --confirm", b" --scheduled --confirm"))
    elif failure == "unit-mode":
        fixture.unit.chmod(0o666)
    elif failure == "bundle-digest":
        fixture.manager.write_bytes(b"different manager")
    elif failure == "dropins":
        fixture.dropins = "/run/systemd/system/unexpected.conf"
    elif failure == "queued":
        fixture.before["Job"] = "123"
    elif failure == "running":
        fixture.before.update(ActiveState="activating", SubState="start", ControlPID="123")
    elif failure == "reload":
        fixture.before["NeedDaemonReload"] = "yes"
    elif failure == "preview":
        fixture.preview_ok = False
    elif failure == "manifest-drift":
        fixture.after_preview = lambda: fixture.manifest.write_text('{"changed": true}')
    else:
        fixture.after_preview = fixture.alias.unlink
    with pytest.raises((RuntimeError, OSError, UsageError)):
        fixture.run(apply=True)
    assert not fixture.started


@pytest.mark.parametrize("failure", ["timeout", "interrupted", "failed-result", "still-running", "stale-invocation", "stale-start", "post-observation"])
def test_scheduled_backup_preserves_uncertain_or_failed_execution_without_retry(installed_worker, failure):
    fixture = installed_worker
    if failure == "timeout":
        fixture.failure = manage.ManageError("synthetic timeout")
    elif failure == "interrupted":
        fixture.failure = KeyboardInterrupt()
    elif failure == "failed-result":
        fixture.after.update(ActiveState="failed", SubState="failed", Result="exit-code", ExecMainStatus="2")
    elif failure == "still-running":
        fixture.after.update(ActiveState="activating", SubState="start", ControlPID="321")
    elif failure == "stale-invocation":
        fixture.after["InvocationID"] = fixture.before["InvocationID"]
    elif failure == "stale-start":
        fixture.after["ExecMainStartTimestampMonotonic"] = fixture.before["ExecMainStartTimestampMonotonic"]
    else:
        fixture.after.pop("InvocationID")
    with pytest.raises(manage.ManageError) as caught:
        fixture.run(apply=True)
    assert caught.value.may_have_executed is True
    assert caught.value.recovery["completion_verified"] is False
    assert "Do not retry" in caught.value.recovery["recovery_hint"]
    actions = [args[1] for args, _ in fixture.calls if args[0] == manage._SYSTEMCTL and args[1] != "show"]
    assert actions == ["start"]


def test_scheduled_backup_requires_canonical_manifest_before_subprocess(monkeypatch):
    monkeypatch.setattr(user_schedule, "_require_root", lambda: None)
    monkeypatch.setattr(user_schedule, "_selected_manager", lambda: Path("/synthetic/manager"))
    with pytest.raises(UsageError):
        user_schedule.run_backup("/other/deployment.json", runner=lambda *_: pytest.fail("no subprocess allowed"))


@pytest.mark.parametrize("flags,applied", [([], False), (["--confirm"], True), (["--confirm", "--dry-run"], False)])
def test_scheduled_backup_cli_preserves_confirmation(monkeypatch, flags, applied):
    calls = []
    monkeypatch.setattr(user_schedule, "run_backup", lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    result = cli.dispatch(["users", "backup", "--scheduled", *flags])
    assert result.error is None
    assert calls == [((user_schedule.DEFAULT_MANIFEST,), {"apply": applied})]


def test_scheduled_backup_cli_rejects_ambiguous_options_before_dispatch(monkeypatch):
    monkeypatch.setattr(user_schedule, "run_backup", lambda *_args, **_kwargs: pytest.fail("must not dispatch"))
    result = cli.dispatch(["users", "backup", "--scheduled", "--include-gateway", "--confirm"])
    assert result.error is not None


def test_product_cli_forwards_scheduled_backup_to_same_guarded_entrypoint(monkeypatch, capsys):
    from anvil_serving import cli as product_cli
    calls = []
    monkeypatch.setattr(user_schedule, "run_backup", lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    assert product_cli.main(["connect", "users", "backup", "--scheduled", "--confirm", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert calls == [((user_schedule.DEFAULT_MANIFEST,), {"apply": False})]
