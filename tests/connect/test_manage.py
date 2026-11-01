from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import re
import socket
import stat
import sys
import tempfile
from pathlib import Path

import pytest

from anvil_serving.connect import manage
from anvil_serving.connect.render import notification_template_path, render as render_config

if sys.platform == "linux":
    import grp
    import pwd
else:  # Keep Windows collection independent of POSIX account modules.
    grp = pwd = None


_LINUX_AMD64 = sys.platform == "linux" and platform.machine().lower() in {"x86_64", "amd64"}
_ROOT_WITH_UID_DROP = _LINUX_AMD64 and hasattr(os, "geteuid") and os.geteuid() == 0
pytestmark = pytest.mark.skipif(not _LINUX_AMD64, reason="Connect lifecycle tests require Linux amd64")

ROOT = Path(__file__).parents[2]


@pytest.fixture(autouse=True)
def no_stability_delay(monkeypatch):
    monkeypatch.setattr(manage.time, "sleep", lambda _: None)


class SyntheticRunner:
    def __init__(self, *, fail_daemon_reload: bool = False, fail_start: str | None = None, active: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[float] = []
        self.fail_daemon_reload = fail_daemon_reload
        self.fail_start = fail_start
        self.active = active
        self.unit_root: Path | None = None

    def __call__(self, argv: tuple[str, ...], timeout: float, identity: manage.ServiceIdentity | None) -> manage.RunResult:
        self.calls.append(argv)
        self.timeouts.append(timeout)
        if len(argv) >= 2 and argv[1] == "admin":
            return manage.RunResult(0, _gateway_status())
        if argv == ("/usr/bin/systemctl", "daemon-reload") and self.fail_daemon_reload:
            self.fail_daemon_reload = False
            return manage.RunResult(1, b"sensitive failed output")
        if argv[:3] == ("/usr/bin/systemctl", "show", "--property=LoadState,FragmentPath,DropInPaths"):
            if self.unit_root is None or not (self.unit_root / argv[-1]).exists():
                return manage.RunResult(0, b"LoadState=not-found\nFragmentPath=\nDropInPaths=\n")
            return manage.RunResult(0, f"LoadState=loaded\nFragmentPath={self.unit_root / argv[-1]}\nDropInPaths=\n".encode())
        if argv[:3] == ("/usr/bin/systemctl", "show", "--property=ActiveState,UnitFileState"):
            return manage.RunResult(0, b"ActiveState=active\nUnitFileState=enabled\n" if self.active else b"ActiveState=inactive\nUnitFileState=disabled\n")
        if argv[:3] == ("/usr/bin/systemctl", "show", "--property=ActiveState,SubState,MainPID,FragmentPath,DropInPaths"):
            return manage.RunResult(0, f"ActiveState=active\nSubState=running\nMainPID=123\nFragmentPath={self.unit_root / argv[-1]}\nDropInPaths=\n".encode())
        if argv[:3] == ("/usr/bin/systemctl", "show", "--property=Id,ActiveState,SubState,UnitFileState"):
            return manage.RunResult(0, b"Id=managed.service\nActiveState=active\nSubState=running\nUnitFileState=enabled\n")
        if argv[0] == "/usr/bin/journalctl":
            return manage.RunResult(0, b'{"MESSAGE":"Authorization: Bearer must-not-escape"}\n')
        if self.fail_start is not None and argv[-1] == self.fail_start and argv[1] in {"enable", "restart"}:
            return manage.RunResult(1)
        return manage.RunResult(0)


def _gateway_status() -> bytes:
    return json.dumps({
        "operation": "status", "epoch": "a" * 64, "secret": "", "key_id": "", "principal": "", "grants": [],
        "invitation": "", "installation": "", "role": "", "resources": [], "generation": 0,
        "fingerprint": "",
        "status": {"id": "", "status": "", "fingerprint": "", "epoch": "", "generation": 0, "resources": []},
    }).encode()


def _executable(path: Path, content: bytes) -> str:
    path.write_bytes(content)
    path.chmod(0o755)
    return hashlib.sha256(content).hexdigest()


def service_account() -> str:
    current = pwd.getpwuid(os.geteuid())
    if current.pw_uid != 0:
        try:
            if current.pw_gid != 0 and grp.getgrnam(current.pw_name).gr_gid == current.pw_gid:
                return current.pw_name
        except KeyError:
            pass
    for item in pwd.getpwall():
        try:
            if item.pw_uid != 0 and item.pw_gid != 0 and grp.getgrnam(item.pw_name).gr_gid == item.pw_gid:
                return item.pw_name
        except KeyError:
            continue
    pytest.skip("no safe non-root same-named service account")


def deployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict, Path]:
    value = json.loads((ROOT / "connect/examples/deployment.json").read_text())
    native = tmp_path / "anvil-connect"
    caddy = tmp_path / "caddy"
    authelia = tmp_path / "authelia"
    _executable(native, b"native-v1")
    caddy_digest = _executable(caddy, b"caddy-v1")
    authelia_digest = _executable(authelia, b"authelia-v1")
    value["binary"] = str(native)
    value["components"] = {"caddy": str(caddy), "authelia": str(authelia)}
    value["config_root"] = str(tmp_path / "rendered")
    value["gateway"]["state_directory"] = str(tmp_path / "gateway-state")
    value["authelia"]["state_directory"] = str(tmp_path / "authelia-state")
    value.pop("service_user")
    value["caddy"]["state_directory"] = str(tmp_path / "caddy-state")
    value["service_identities"] = {
        "gateway": {"uid": 1201, "gid": 2201},
        "edge": {"uid": 1202, "gid": 2202},
        "idp": {"uid": 1203, "gid": 2203},
        "connectors": {"dashboard": {"uid": 1204, "gid": 2204}},
        "clients": {"dashboard-api": {"uid": 1205, "gid": 2205}},
        "ingress": {"group_id": 2290, "directory": str(tmp_path / "ingress")},
    }
    value["service_limits"] = {
        "gateway": {"memory_max_bytes": 805306368, "tasks_max": 128},
        "edge": {"memory_max_bytes": 536870912, "tasks_max": 64},
        "idp": {"memory_max_bytes": 536870912, "tasks_max": 64},
        "connectors": {"dashboard": {"memory_max_bytes": 402653184, "tasks_max": 64}},
        "clients": {"dashboard-api": {"memory_max_bytes": 268435456, "tasks_max": 32}},
    }
    for section, name in (("gateway", "gateway.env"),):
        value["environment_files"][section] = str(tmp_path / name)
    for name in value["environment_files"]["connectors"]:
        value["environment_files"]["connectors"][name] = str(tmp_path / (name + ".connector.env"))
    for name in value["environment_files"]["clients"]:
        value["environment_files"]["clients"][name] = str(tmp_path / (name + ".client.env"))
    for env in [value["environment_files"]["gateway"], *value["environment_files"]["connectors"].values(), *value["environment_files"]["clients"].values()]:
        Path(env).write_text("DECLARED_ONLY=1\n", encoding="utf-8")
        Path(env).chmod(0o600)
    environment_paths = {Path(value["environment_files"]["gateway"]), *(Path(item) for item in value["environment_files"]["connectors"].values()), *(Path(item) for item in value["environment_files"]["clients"].values())}
    original_lstat = Path.lstat

    def root_owned_environment_lstat(path: Path):
        info = original_lstat(path)
        if path in environment_paths:
            return os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink,
                                   0, info.st_gid, info.st_size, info.st_atime, info.st_mtime, info.st_ctime))
        return info

    monkeypatch.setattr(Path, "lstat", root_owned_environment_lstat)
    monkeypatch.setattr(manage, "_safe_consumed_file", lambda *_, **__: None)
    monkeypatch.setattr(manage, "_validate_isolated_runtime", lambda *_: None)
    monkeypatch.setattr(manage, "_safe_root_ancestors", lambda *_: None)
    monkeypatch.setattr(manage, "_role_service_identity", lambda *_: None)
    manifest = tmp_path / "deployment.json"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(manage, "_component_lock", lambda: {"caddy": caddy_digest, "authelia": authelia_digest})
    return manifest, value, native


def test_target_is_closed_and_declared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _, _ = deployment(tmp_path, monkeypatch)
    assert manage.Target.parse("gateway") == manage.Target("gateway")
    assert manage.Target.parse("connector:dashboard") == manage.Target("connector", "dashboard")
    assert manage.Target.parse("client:dashboard-api") == manage.Target("client", "dashboard-api")
    for value in ("gateway:x", "connector", "connector:dashboard:extra", "unit:arbitrary.service"):
        with pytest.raises(manage.ManageError):
            manage.Target.parse(value)
    with pytest.raises(manage.ManageError, match="not declared"):
        manage.up(manifest, manage.Target("connector", "missing"))


def test_gateway_validation_rebases_materialized_authelia_templates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    inspected: list[Path] = []

    class InspectingRunner(SyntheticRunner):
        def __call__(self, argv, timeout, identity):  # type: ignore[no-untyped-def]
            if argv[0] == value["components"]["authelia"] and argv[-2:] == ("config", "validate"):
                configuration = Path(argv[argv.index("--config") + 1])
                content = configuration.read_text(encoding="utf-8")
                template_root = configuration.parent / "notification-templates"
                assert f"template_path: '{notification_template_path(configuration.parents[1])}'" in content
                assert notification_template_path(value["config_root"]) not in content
                assert (template_root / "IdentityVerificationJWT.html").is_file()
                assert (template_root / "IdentityVerificationJWT.txt").is_file()
                inspected.append(configuration)
            return super().__call__(argv, timeout, identity)

    assert manage.validate(manifest, manage.Target("gateway"), runner=InspectingRunner())["targets"] == ["gateway"]
    assert len(inspected) == 1
    assert not Path(value["config_root"]).exists()


def test_render_apply_only_stages_after_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    result = manage.render(manifest, apply=True, runner=SyntheticRunner())
    assert result["stage"]["state"] == "staged"
    assert not Path(value["config_root"]).exists()
    assert (Path(result["stage"]["path"]) / "managed.json").is_file()


def test_service_limit_change_is_target_scoped_and_rolled_into_owned_units(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    runner = SyntheticRunner(); runner.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)

    changed = copy.deepcopy(value)
    changed["service_limits"]["connectors"]["dashboard"]["memory_max_bytes"] = 402653185
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(manage.ManageError, match="unselected target"):
        manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)

    changed = copy.deepcopy(value)
    changed["service_limits"]["gateway"]["memory_max_bytes"] = 805306369
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    assert "MemoryMax=805306369" in (units / "anvil-connect-gateway.service").read_text(encoding="utf-8")


def test_authelia_schema_upgrade_failure_retries_only_new_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A possibly-started new provider retains a bound fix-forward recovery."""
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    initial = SyntheticRunner(); initial.unit_root = units
    targets = manage._targets(value, None)
    manage.up_many(manifest, targets, apply=True, runner=initial, unit_root=units)
    root = Path(value["config_root"])
    old_generation = manage._verify_owned_tree(root)[0]
    old_record = json.loads((tmp_path / ".rendered.anvil-connect-activation.json").read_text())
    new_authelia = tmp_path / "authelia-v2"
    digest = _executable(new_authelia, b"authelia-v2")
    changed = copy.deepcopy(value)
    changed["components"] = {**value["components"], "authelia": str(new_authelia)}
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    monkeypatch.setattr(manage, "_component_lock", lambda: {"caddy": old_record["components"]["caddy"], "authelia": digest})
    users = tmp_path / "users.yml"; users.write_text("users: {}\n")
    archive = tmp_path / "backups" / "auth-test.zip"; archive.parent.mkdir(); archive.write_bytes(b"fixture")
    monkeypatch.setattr(manage, "_safe_authelia_users_file", lambda _: users)
    monkeypatch.setattr(manage, "_changed_authelia_upgrade", lambda data, selected, digests: (old_generation, old_record))
    from anvil_serving.connect import user_backup
    receipt = {"file": str(archive), "sha256": "a" * 64}
    monkeypatch.setattr(user_backup, "snapshot", lambda *_, **__: receipt)
    monkeypatch.setattr(user_backup, "read_snapshot", lambda *_, **__: {"users.yml": b"users: {}\n"})

    failed = SyntheticRunner(fail_start="anvil-connect-authelia.service"); failed.unit_root = units
    with pytest.raises(manage.ManageError):
        manage.up_many(manifest, targets, upgrade=True, apply=True, runner=failed, unit_root=units)
    marker = tmp_path / ".rendered.anvil-connect-authelia-upgrade.json"
    assert marker.is_file()
    assert manage._verify_owned_tree(root)[0] != old_generation
    assert "authelia-v2" in (units / "anvil-connect-authelia.service").read_text()
    assert not any(call and call[0] == value["components"]["authelia"] for call in failed.calls)

    drifted = copy.deepcopy(changed)
    drifted["gateway"]["gateway"]["max_concurrent"] = 63
    manifest.write_text(json.dumps(drifted), encoding="utf-8")
    with pytest.raises(manage.ManageError, match="does not match"):
        manage.up_many(manifest, targets, upgrade=True, apply=True, runner=SyntheticRunner(), unit_root=units)
    manifest.write_text(json.dumps(changed), encoding="utf-8")

    archive.unlink()
    monkeypatch.setattr(user_backup, "read_snapshot", lambda path, **_: (_ for _ in ()).throw(manage.ManageError("missing archive")) if not path.exists() else {"users.yml": b"users: {}\n"})
    with pytest.raises(manage.ManageError, match="missing archive"):
        manage.up_many(manifest, targets, upgrade=True, apply=True, runner=SyntheticRunner(), unit_root=units)
    archive.write_bytes(b"fixture")
    # The pending marker binds the validated declaration, not insignificant
    # JSON serialization details written by an operator's formatter.
    manifest.write_text(json.dumps(changed, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    recovered = SyntheticRunner(); recovered.unit_root = units
    result = manage.up_many(manifest, targets, upgrade=True, apply=True, runner=recovered, unit_root=units)
    assert not marker.exists()
    assert result["authelia_upgrade_recovery_completed"] is True
    record = json.loads((tmp_path / ".rendered.anvil-connect-activation.json").read_text())
    assert record["components"]["authelia"] == digest


def test_authelia_schema_upgrade_recovery_marker_tamper_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    root = Path(value["config_root"])
    root.mkdir()
    marker = tmp_path / ".rendered.anvil-connect-authelia-upgrade.json"
    marker.write_text("{}\n")
    with pytest.raises(manage.ManageError, match="recovery marker"):
        manage._read_authelia_upgrade_marker(root, data=value, digests={"native": "a" * 64, "caddy": "b" * 64, "authelia": "c" * 64}, manifest=manifest, targets=(manage.Target("gateway"),), unit_root=tmp_path)


def test_up_preview_reports_even_an_invalid_authelia_recovery_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    marker = tmp_path / ".rendered.anvil-connect-authelia-upgrade.json"
    marker.symlink_to(tmp_path / "missing-marker")
    preview = manage.up(manifest, manage.Target("gateway"), runner=SyntheticRunner())
    assert preview["activation_blockers"] == [
        "Authelia migration recovery is pending; rerun the identical connect up command with --upgrade --confirm."
    ]


def test_authelia_recovery_marker_read_disappearance_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    root = Path(value["config_root"])
    marker = tmp_path / ".rendered.anvil-connect-authelia-upgrade.json"
    marker.write_text("{}\n"); marker.chmod(0o600)
    original = manage._read_regular
    monkeypatch.setattr(manage, "_read_regular", lambda path, maximum: None if path == marker else original(path, maximum))
    with pytest.raises(manage.ManageError, match="recovery marker"):
        manage._read_authelia_upgrade_marker(root, data=value, digests={"native": "a" * 64, "caddy": "b" * 64, "authelia": "c" * 64}, manifest=manifest, targets=(manage.Target("gateway"),), unit_root=tmp_path)
    assert marker.exists()


def test_authelia_upgrade_pending_receipt_does_not_claim_unknown_backup() -> None:
    blocked = manage._authelia_upgrade_pending(False)
    assert blocked.may_have_executed is False
    assert blocked.recovery == {
        "recovery_required": True,
        "recovery_hint": "Authelia migration recovery is pending; rerun the identical connect up command with --upgrade --confirm",
    }
    pending = manage._authelia_upgrade_pending(True)
    assert pending.recovery["backup_retained"] is True


def test_gateway_readiness_failure_rolls_back_without_starting_connector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, native = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    initial = SyntheticRunner(); initial.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=initial, unit_root=units)
    root = Path(value["config_root"])
    previous_gateway = (root / "gateway.json").read_bytes()
    changed = copy.deepcopy(value)
    changed["gateway"]["gateway"]["max_concurrent"] = 63
    changed["connectors"][0]["resources"][0]["envelope"]["origin_url"] = "http://127.0.0.1:19082"
    manifest.write_text(json.dumps(changed), encoding="utf-8")

    class UnreadyGatewayRunner(SyntheticRunner):
        def __call__(self, argv, timeout, identity):  # type: ignore[no-untyped-def]
            if argv[:2] == (str(native), "admin"):
                self.calls.append(argv)
                return manage.RunResult(1)
            return super().__call__(argv, timeout, identity)

    runner = UnreadyGatewayRunner(); runner.unit_root = units
    with pytest.raises(manage.ManageError, match="gateway did not become ready") as caught:
        manage.up_many(manifest, (manage.Target("gateway"), manage.Target("connector", "dashboard")), apply=True, runner=runner, unit_root=units)
    assert caught.value.may_have_executed is True
    assert (root / "gateway.json").read_bytes() == previous_gateway
    assert not any(call[-1] == "anvil-connect-connector-dashboard.service" and call[1] in {"enable", "restart"} for call in runner.calls)


def test_rollback_waits_for_prior_gateway_before_restoring_dependents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, native = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    initial = SyntheticRunner(active=True); initial.unit_root = units
    targets = manage._targets(value, None)
    manage.up_many(manifest, targets, apply=True, runner=initial, unit_root=units)
    root = Path(value["config_root"])
    old_gateway = (root / "gateway.json").read_bytes()
    old_state = Path(value["gateway"]["state_directory"])
    upgraded = tmp_path / "anvil-connect-v2"
    _executable(upgraded, b"native-v2")
    changed = copy.deepcopy(value)
    changed["binary"] = str(upgraded)
    changed["gateway"]["state_directory"] = str(tmp_path / "gateway-state-v2")
    changed["service_identities"]["gateway"] = {"uid": 1291, "gid": 2291}
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    monkeypatch.setattr(
        manage,
        "_role_service_identity",
        lambda data, *_: manage.ServiceIdentity(data["service_identities"]["gateway"]["uid"], data["service_identities"]["gateway"]["gid"]),
    )

    class DelayedPriorRunner(SyntheticRunner):
        def __init__(self) -> None:
            super().__init__(active=True)
            self.new_probes = 0
            self.prior_probes = 0

        def __call__(self, argv, timeout, identity):  # type: ignore[no-untyped-def]
            if argv[:2] == (str(upgraded), "admin"):
                self.calls.append(argv)
                self.new_probes += 1
                assert identity == manage.ServiceIdentity(1291, 2291)
                return manage.RunResult(1)
            if argv[:2] == (str(native), "admin"):
                self.calls.append(argv)
                self.prior_probes += 1
                assert identity == manage.ServiceIdentity(1201, 2201)
                assert argv[argv.index("--socket") + 1] == str(old_state / "admin.sock")
                return manage.RunResult(1) if self.prior_probes < 3 else manage.RunResult(0, _gateway_status())
            return super().__call__(argv, timeout, identity)

    runner = DelayedPriorRunner(); runner.unit_root = units
    with pytest.raises(manage.ManageError, match="gateway did not become ready"):
        manage.up_many(manifest, (manage.Target("connector", "dashboard"), manage.Target("gateway"), manage.Target("client", "dashboard-api")), upgrade=True, apply=True, runner=runner, unit_root=units)
    assert (root / "gateway.json").read_bytes() == old_gateway
    assert runner.new_probes == 6
    assert runner.prior_probes == 3
    last_new_probe = max(index for index, call in enumerate(runner.calls) if call[:2] == (str(upgraded), "admin"))
    restored = [call[-1] for call in runner.calls[last_new_probe + 1:] if call[:2] == ("/usr/bin/systemctl", "restart")]
    # Only the gateway was touched before readiness failed. Its healthy edge
    # and still-running dependents must retain their original service intent.
    assert restored == ["anvil-connect-gateway.service"]
    assert not any(call == ("/usr/bin/systemctl", "restart", "anvil-connect-connector-dashboard.service") for call in runner.calls)


def test_rollback_refuses_dependent_restore_when_prior_gateway_never_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, native = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    initial = SyntheticRunner(active=True); initial.unit_root = units
    targets = manage._targets(value, None)
    manage.up_many(manifest, targets, apply=True, runner=initial, unit_root=units)
    root = Path(value["config_root"])
    old_gateway = (root / "gateway.json").read_bytes()
    upgraded = tmp_path / "anvil-connect-v2"
    _executable(upgraded, b"native-v2")
    changed = copy.deepcopy(value)
    changed["binary"] = str(upgraded)
    changed["gateway"]["state_directory"] = str(tmp_path / "gateway-state-v2")
    manifest.write_text(json.dumps(changed), encoding="utf-8")

    class NeverReadyRunner(SyntheticRunner):
        def __init__(self) -> None:
            super().__init__(active=True)
            self.prior_probes = 0

        def __call__(self, argv, timeout, identity):  # type: ignore[no-untyped-def]
            if argv[:2] in {(str(upgraded), "admin"), (str(native), "admin")}:
                self.calls.append(argv)
                if argv[0] == str(native):
                    self.prior_probes += 1
                return manage.RunResult(1)
            return super().__call__(argv, timeout, identity)

    runner = NeverReadyRunner(); runner.unit_root = units
    with pytest.raises(manage.ManageError, match="restoration failed") as caught:
        manage.up_many(manifest, targets, upgrade=True, apply=True, runner=runner, unit_root=units)
    assert caught.value.may_have_executed is True
    assert (root / "gateway.json").read_bytes() == old_gateway
    assert runner.prior_probes == 6
    assert not any(call == ("/usr/bin/systemctl", "restart", "anvil-connect-connector-dashboard.service") for call in runner.calls)


def test_foreign_unit_and_dropin_are_never_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir()
    units.chmod(0o755)
    foreign = units / "anvil-connect-gateway.service"
    foreign.write_text("operator unit", encoding="utf-8")
    with pytest.raises(manage.ManageError, match="unowned unit"):
        manage.up(manifest, manage.Target("gateway"), apply=True, runner=SyntheticRunner(), unit_root=units)
    assert foreign.read_text(encoding="utf-8") == "operator unit"


def test_logs_return_metadata_only_and_down_is_target_scoped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir()
    units.chmod(0o755)
    runner = SyntheticRunner()
    runner.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    output = manage.logs(manifest, manage.Target("gateway"), runner=runner)
    assert "must-not-escape" not in json.dumps(output)
    assert len(output["events"]) == 3
    down = manage.down(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    stops = [call[-1] for call in runner.calls if call[:3] == ("/usr/bin/systemctl", "disable", "--now")]
    assert stops[-3:] == ["anvil-connect-gateway.service", "anvil-connect-caddy.service", "anvil-connect-authelia.service"]
    assert down["units"] == stops[-3:]


def test_component_digest_mismatch_refuses_before_native_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _, _ = deployment(tmp_path, monkeypatch)
    monkeypatch.setattr(manage, "_component_lock", lambda: {"caddy": "0" * 64, "authelia": "1" * 64})
    runner = SyntheticRunner()
    with pytest.raises(manage.ManageError, match="digest"):
        manage.validate(manifest, manage.Target("gateway"), runner=runner)
    assert runner.calls == []


def test_reenroll_uses_the_current_bound_connector_declaration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, native = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    runner = SyntheticRunner(); runner.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    prior = tmp_path / "prior.json"
    bundle = tmp_path / "invitation.json"
    prior.write_text(json.dumps({"id": "dashboard", "fingerprint": "A" * 43,
                                 "epoch": "a" * 64, "generation": 1,
                                 "resources": ["dashboard", "dashboard-api"]}))
    bundle.write_text("private invitation")

    result = manage.native_reenroll(
        manifest, manage.Target("connector", "dashboard"), prior=prior, bundle=bundle,
        apply=True, runner=runner,
    )

    assert result["action"] == "re-enroll"
    call = next(call for call in runner.calls if call[:2] == (str(native), "re-enroll"))
    assert Path(call[call.index("--config") + 1]) == Path(value["config_root"]) / "connectors" / "dashboard.json"
    assert call[call.index("--prior") + 1] == str(prior)
    assert call[call.index("--bundle") + 1] == str(bundle)


def test_start_failure_restores_generation_and_preserves_private_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    initial = SyntheticRunner(); initial.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=initial, unit_root=units)
    sentinel = Path(value["gateway"]["state_directory"]) / "revoked-state"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("never lifecycle-owned", encoding="utf-8")
    before = (Path(value["config_root"]) / "gateway.json").read_bytes()
    changed = copy.deepcopy(value)
    changed["gateway"]["gateway"]["max_concurrent"] = 63
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    failing = SyntheticRunner(fail_start="anvil-connect-gateway.service", active=True); failing.unit_root = units
    with pytest.raises(manage.ManageError, match="restoration failed"):
        manage.up(manifest, manage.Target("gateway"), apply=True, runner=failing, unit_root=units)
    assert (Path(value["config_root"]) / "gateway.json").read_bytes() == before
    assert sentinel.read_text(encoding="utf-8") == "never lifecycle-owned"
    assert any(call[:2] == ("/usr/bin/systemctl", "restart") and call[-1] == "anvil-connect-gateway.service" for call in failing.calls)


def test_atomic_write_keeps_occupied_foreign_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "unit.service"
    sentinel = tmp_path / ".unit.service.anvil-connect-fixed.new"
    sentinel.write_bytes(b"foreign")
    monkeypatch.setattr(manage.secrets, "token_hex", lambda _: "fixed")
    with pytest.raises(manage.ManageError, match="write failed"):
        manage._write_atomic(target, b"new")
    assert sentinel.read_bytes() == b"foreign"
    assert not target.exists()


@pytest.mark.parametrize(("mode", "expected"), ((0o644, 0o644), (0o600, 0o600)))
def test_atomic_write_applies_public_and_private_modes_despite_strict_umask(tmp_path: Path, mode: int, expected: int) -> None:
    target = tmp_path / ("public.json" if mode == 0o644 else "private.json")
    previous_umask = os.umask(0o077)
    try:
        manage._write_atomic(target, b"managed\n", mode)
    finally:
        os.umask(previous_umask)
    assert target.read_bytes() == b"managed\n"
    assert stat.S_IMODE(target.stat().st_mode) == expected


@pytest.mark.skipif(not _ROOT_WITH_UID_DROP, reason="requires root to exercise real service-UID status read")
def test_gateway_readiness_status_request_is_readable_by_service_identity_under_strict_umask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pwd

    service = pwd.getpwnam(service_account())
    with tempfile.TemporaryDirectory(prefix="anvil-connect-status-", dir="/tmp") as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        _manifest, value, _native = deployment(root, monkeypatch)
        monkeypatch.setattr(manage, "_role_service_identity", lambda *_: manage.ServiceIdentity(service.pw_uid, service.pw_gid))
        observed: list[Path] = []

        def service_runner(argv, timeout, identity):  # type: ignore[no-untyped-def]
            assert identity == manage.ServiceIdentity(service.pw_uid, service.pw_gid)
            request = Path(argv[-1])
            observed.append(request)
            assert stat.S_IMODE(request.stat().st_mode) == 0o644
            result = manage._bounded_run(("/usr/bin/test", "-r", str(request)), timeout, identity)
            assert result.returncode == 0
            return manage.RunResult(0, _gateway_status())

        previous_umask = os.umask(0o077)
        try:
            manage._gateway_ready(value, service_runner)
        finally:
            os.umask(previous_umask)
        assert len(observed) == 1


@pytest.mark.parametrize("native_status", ("pending", "enrolled"))
def test_identity_accepts_native_local_status_before_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_status: str
) -> None:
    manifest, value, native = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)

    class IdentityRunner(SyntheticRunner):
        def __call__(self, argv, timeout, identity):  # type: ignore[no-untyped-def]
            if argv[:2] == (str(native), "identity"):
                declaration = json.loads(Path(argv[-1]).read_text())
                assert declaration["id"] == "dashboard"
                assert not Path(value["config_root"]).exists()
                # Exact public shape emitted by the native `identity` command
                # from admin.InstallationStatus.  `enrolled` only records that
                # the connector completed its local enrollment; it is not a
                # gateway approval assertion.
                return manage.RunResult(0, json.dumps({"id": "dashboard", "status": native_status, "fingerprint": "A" * 43, "epoch": "a" * 64, "generation": 1, "resources": ["dashboard", "dashboard-api"]}).encode())
            return super().__call__(argv, timeout, identity)

    observed = IdentityRunner(); observed.unit_root = units
    result = manage.identity(manifest, manage.Target("connector", "dashboard"), runner=observed)
    assert result["identity"]["fingerprint"] == "A" * 43
    assert result["identity"]["status"] == native_status
    assert result["native_sha256"] == hashlib.sha256(b"native-v1").hexdigest()
    assert not Path(value["config_root"]).exists()
    assert list(units.iterdir()) == []

    class InvalidIdentityRunner(IdentityRunner):
        def __call__(self, argv, timeout, identity):  # type: ignore[no-untyped-def]
            value = super().__call__(argv, timeout, identity)
            if argv[:2] == (str(native), "identity"):
                return manage.RunResult(0, value.stdout.replace(b'"fingerprint": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"', b'"fingerprint": "A+"'))
            return value

    invalid = InvalidIdentityRunner(); invalid.unit_root = units
    with pytest.raises(manage.ManageError, match="identity output"):
        manage.identity(manifest, manage.Target("connector", "dashboard"), runner=invalid)


def test_admin_preview_accepts_only_real_fingerprint_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    runner = SyntheticRunner(); runner.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"operation": "approve", "installation": "dashboard", "fingerprint": "A" * 43}), encoding="utf-8")
    assert manage.admin(manifest, request_path=request, runner=runner)["scope"] == "installation"
    request.write_text(json.dumps({"operation": "approve", "installation": "dashboard", "fingerprint": "a" * 64}), encoding="utf-8")
    with pytest.raises(manage.ManageError, match="administrative request"):
        manage.admin(manifest, request_path=request, runner=runner)


def test_systemd_action_deadline_exceeds_rendered_caddy_shutdown_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    runner = SyntheticRunner(active=True); runner.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    caddy_restart = ("/usr/bin/systemctl", "restart", "anvil-connect-caddy.service")
    timeout = next(timeout for call, timeout in zip(runner.calls, runner.timeouts) if call == caddy_restart)
    files = render_config(value)["files"]
    grace = json.loads(files["caddy.json"])["apps"]["http"]["grace_period"]
    stop = re.search(r"^TimeoutStopSec=(\d+)$", files["systemd/anvil-connect-caddy.service"], flags=re.MULTILINE)
    assert grace == "15s"
    assert stop is not None
    assert timeout == manage._SYSTEMD_TIMEOUT
    assert timeout > int(stop.group(1)) > int(grace.removesuffix("s"))


@pytest.mark.skipif(not _ROOT_WITH_UID_DROP, reason="requires root to exercise real service-UID drop")
def test_preflight_temporary_declaration_is_readable_after_real_uid_drop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pwd
    service = pwd.getpwnam(service_account())
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    descriptor, name = tempfile.mkstemp(prefix="anvil-connect-preflight-", dir="/tmp")
    checker = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write("#!/bin/sh\nprevious=\nfor value in \"$@\"; do\n  if [ \"$previous\" = --config ]; then test -r \"$value\" || exit 9; fi\n  previous=\"$value\"\ndone\n")
        checker.chmod(0o755)
        value["binary"] = str(checker)
        value["components"] = {"caddy": str(checker), "authelia": str(checker)}
        monkeypatch.setattr(manage, "_role_service_identity", lambda *_: manage.ServiceIdentity(service.pw_uid, service.pw_gid))
        manifest.write_text(json.dumps(value), encoding="utf-8")
        digest = hashlib.sha256(checker.read_bytes()).hexdigest()
        monkeypatch.setattr(manage, "_component_lock", lambda: {"caddy": digest, "authelia": digest})
        assert manage.validate(manifest, manage.Target("gateway"))["targets"] == ["gateway"]
    finally:
        checker.unlink(missing_ok=True)


def test_gateway_pin_write_failure_rolls_back_connector_first_activation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    runner = SyntheticRunner(); runner.unit_root = units
    manage.up(manifest, manage.Target("connector", "dashboard"), apply=True, runner=runner, unit_root=units)
    record_path = tmp_path / ".rendered.anvil-connect-activation.json"
    assert json.loads(record_path.read_text())["components"] == {}
    monkeypatch.setattr(manage, "_write_activation_record", lambda *_: (_ for _ in ()).throw(manage.ManageError("record write failed")))
    with pytest.raises(manage.ManageError) as caught:
        manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    assert caught.value.may_have_executed is True
    assert json.loads(record_path.read_text())["components"] == {}
    assert not (units / "anvil-connect-gateway.service").exists()


def test_notification_template_change_is_gateway_owned_and_restarts_authelia(tmp_path, monkeypatch):
    import importlib

    connect_render = importlib.import_module("anvil_serving.connect.render")

    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(mode=0o755)
    runner = SyntheticRunner(active=True)
    runner.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    original = connect_render._password_setup_templates

    def updated(data):
        templates = original(data)
        name = "authelia/notification-templates/IdentityVerificationJWT.html"
        templates[name] += "<!-- updated -->\r\n"
        return templates

    monkeypatch.setattr(connect_render, "_password_setup_templates", updated)
    runner.calls.clear()
    preview = manage.up(manifest, manage.Target("gateway"), runner=runner, unit_root=units)
    assert preview["configuration_restarts"] == ["anvil-connect-authelia.service"]
    result = manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    assert result["restarted_units"] == ["anvil-connect-authelia.service"]


@pytest.mark.parametrize("retain_listener", [False, True])
def test_cf_recovery_preview_targets_and_fail_closed_gate(tmp_path, monkeypatch, retain_listener):
    from tests.connect.test_render import local_tunnel_manifest

    manifest, value, _ = deployment(tmp_path, monkeypatch)
    local = local_tunnel_manifest(value)
    root = Path(value["config_root"])
    manage._materialize(render_config(local)["files"], root)
    manage._make_public(root)
    recovered = copy.deepcopy(local)
    del recovered["connectors"][0]["local_tunnel"]
    if not retain_listener:
        del recovered["gateway"]["local_tunnel"]
    manifest.write_text(json.dumps(recovered))
    runner = SyntheticRunner()
    runner.unit_root = tmp_path / "units"
    targets = (manage.Target("connector", "dashboard"),)
    if not retain_listener:
        with pytest.raises(manage.ManageError, match="unselected target"):
            manage.up_many(manifest, targets, runner=runner)
        targets = (manage.Target("gateway"), *targets)
    preview = manage.up_many(manifest, targets, runner=runner)
    assert preview["tunnel_selections"][-1]["paths"] == ["public"]
    assert preview["activation_blockers"] == []


def test_bounded_events_are_closed_invocation_scoped_and_not_readiness(tmp_path, monkeypatch):
    from tests.connect.test_render import local_tunnel_manifest

    manifest, value, _ = deployment(tmp_path, monkeypatch)
    value = local_tunnel_manifest(value)
    manifest.write_text(json.dumps(value))
    unit = "anvil-connect-connector-dashboard.service"
    invocation = "a" * 32
    rows = [
        {"MESSAGE": "connect_event path=local resource=dashboard reason=tunnel_established"},
        {"MESSAGE": "2026/09/12 12:00:00 connect_event path=local resource=dashboard reason=tunnel_disconnected"},
        {"MESSAGE": "connect_event path=local resource=dashboard reason=unrecognized"},
        {"MESSAGE": "connect_event path=local resource=dashboard reason=tunnel_established Authorization: Bearer hidden"},
        {"MESSAGE": "connect_event path=local resource=dashboard reason=tunnel_established", "_SYSTEMD_INVOCATION_ID": "b" * 32},
        {"MESSAGE": "connect_event path=local resource=dashboard reason=tunnel_established", "_SYSTEMD_UNIT": "foreign.service"},
        {"MESSAGE": ["connect_event path=local resource=dashboard reason=tunnel_established"]},
    ]

    class EventsRunner(SyntheticRunner):
        def __call__(self, argv, timeout, identity):
            if argv[1:3] == ("show", "--property=InvocationID"):
                return manage.RunResult(0, f"InvocationID={invocation}\n".encode())
            if argv[0] == "/usr/bin/journalctl" and argv[-1].startswith("_SYSTEMD_INVOCATION_ID="):
                return manage.RunResult(0, b"\n".join(json.dumps({"_SYSTEMD_UNIT": unit, "_SYSTEMD_INVOCATION_ID": invocation, **row}).encode() for row in rows))
            return super().__call__(argv, timeout, identity)

    runner = EventsRunner(active=True)
    target = manage.Target("connector", "dashboard")
    result = manage.status(manifest, target, runner=runner)["targets"][0]
    assert result["units"][0]["active"] == "active"
    assert result["tunnel"]["state"] == "degraded" and result["tunnel"]["readiness"] == "unknown"
    assert result["tunnel"]["observations"] == [{"path": "local", "resource": "dashboard", "reason": "tunnel_disconnected"}]
    assert "hidden" not in json.dumps(manage.logs(manifest, target, runner=runner))
    rows[:] = rows[:1]
    observed = manage.status(manifest, target, runner=runner)["targets"][0]["tunnel"]
    assert observed["state"] == observed["readiness"] == "unknown"


def test_status_has_one_aggregate_observation_deadline(tmp_path, monkeypatch):
    manifest, _, _ = deployment(tmp_path, monkeypatch)
    now = [0.0]
    calls = []
    monkeypatch.setattr(manage.time, "monotonic", lambda: now[0])

    def slow(argv, timeout, identity):
        calls.append(argv)
        now[0] += timeout
        return manage.RunResult(1)

    result = manage.status(manifest, runner=slow)
    assert now[0] == 5 and len(calls) == 1
    assert all(unit["active"] == "unknown" for target in result["targets"] for unit in target["units"])
    assert all(target["tunnel"].get("readiness", "unknown") == "unknown" for target in result["targets"])


def test_preview_runtime_checks_share_one_deadline(tmp_path, monkeypatch):
    _, value, _ = deployment(tmp_path, monkeypatch)
    now = [0.0]
    calls = []
    units = tmp_path / "units"
    synthetic = SyntheticRunner(active=True)
    synthetic.unit_root = units
    monkeypatch.setattr(manage.time, "monotonic", lambda: now[0])

    def slow_success(argv, timeout, identity):
        calls.append(argv)
        now[0] += min(2, timeout)
        return synthetic(argv, timeout, identity)

    targets = manage._targets(value, None)
    actions = manage._preview_runtime_actions(value, targets, {"state": "current", "changes": []}, slow_success, units)
    assert now[0] == 5 and len(calls) == 3
    assert actions and all(action["action"] == "check" for action in actions)


class LocalRunner(SyntheticRunner):
    def __init__(self, value, units):
        super().__init__()
        self.value, self.unit_root = value, units
        self.registered = False
        self.entry_listening = True
        self.admit = True
        self.events = []
        self.stale = False

    def __call__(self, argv, timeout, identity):
        self.events.append((argv[1], argv[-1]))
        if argv[0] == manage._SYSTEMCTL and argv[-1] == "anvil-connect-connector-dashboard.service":
            if argv[1] == "stop":
                self.registered = self.stale
            elif argv[1] in {"restart", "enable"}:
                self.registered = self.admit
        if argv[1] == "admin":
            self.calls.append(argv)
            value = json.loads(_gateway_status())
            root = Path(self.value["config_root"])
            gateway = json.loads((root / "gateway.json").read_text())
            connector = json.loads((root / "connectors/dashboard.json").read_text())
            value["entries"] = []
            for path in (["public", "local"] if "local_tunnel" in gateway else ["public"]):
                listening = path == "public" or self.entry_listening
                selected = "local" if "local_tunnel" in connector else "public"
                value["entries"].append({"path": path, "listening": listening,
                    "reason": "entry_listening" if listening else "entry_tls_failed",
                    "resources": [{"resource": r["rule"]["id"], "registrations": int(listening and self.registered and selected == path)}
                                  for r in gateway["gateway"]["resources"]]})
            self.events.append(("snapshot", (self.entry_listening, self.registered)))
            return manage.RunResult(0, json.dumps(value).encode())
        return super().__call__(argv, timeout, identity)


def local_deployment(tmp_path, monkeypatch):
    from tests.connect.test_render import local_tunnel_manifest
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    value = local_tunnel_manifest(value)
    manifest.write_text(json.dumps(value))
    units = tmp_path / "units"
    units.mkdir(mode=0o755)
    monkeypatch.setattr(manage, "_reverse_ports_free", lambda _: True)
    runner = LocalRunner(value, units)
    targets = (manage.Target("gateway"), manage.Target("connector", "dashboard"))
    return manifest, value, units, runner, targets


@pytest.mark.parametrize("fault", ["stale-registration", "foreign-port"])
def test_path_switch_refuses_stale_or_foreign_listener(tmp_path, monkeypatch, fault):
    manifest, value, units, runner, targets = local_deployment(tmp_path, monkeypatch)
    manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    runner.active = True
    runner.events.clear()
    if fault == "stale-registration": runner.stale = True
    else: monkeypatch.setattr(manage, "_reverse_ports_free", lambda _: False)
    del value["connectors"][0]["local_tunnel"]
    manifest.write_text(json.dumps(value))
    with pytest.raises(manage.ManageError):
        manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    assert ("restart", "anvil-connect-connector-dashboard.service") not in runner.events
    assert all(event[0] != "kill" for event in runner.events)
    assert list(tmp_path.glob(".rendered.anvil-connect-*"))


def test_invalid_prior_trust_reports_failed_recovery_and_retains_artifacts(tmp_path, monkeypatch):
    manifest, value, units, runner, targets = local_deployment(tmp_path, monkeypatch)
    manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    runner.active = True
    old = Path(value["config_root"]) / "gateway.json"
    old_bytes = old.read_bytes()
    value["gateway"]["local_tunnel"]["certificate_file"] += ".v2"
    manifest.write_text(json.dumps(value))
    runner.events.clear()
    preflight = manage._native_preflight
    restoring = False
    def gate(data, selected, root, selected_runner, *, initializing=False):
        nonlocal restoring
        if restoring and not data["gateway"]["local_tunnel"]["certificate_file"].endswith(".v2"):
            raise manage.ManageError("native declaration validation failed")
        preflight(data, selected, root, selected_runner)
    monkeypatch.setattr(manage, "_native_preflight", gate)
    def run(argv, timeout, identity):
        nonlocal restoring
        if argv[1] == "restart" and argv[-1] == "anvil-connect-gateway.service" and not restoring:
            restoring = True
            return manage.RunResult(1)
        return runner(argv, timeout, identity)
    with pytest.raises(manage.ManageError, match="recovery failed; recovery artifacts retained"):
        manage.up_many(manifest, targets, apply=True, runner=run, unit_root=units)
    assert old.read_bytes() == old_bytes
    assert list(tmp_path.glob(".rendered.anvil-connect-failed-*"))
    assert ("restart", "anvil-connect-connector-dashboard.service") not in runner.events


def test_cf_recovery_retains_failed_local_entry_without_restarting_gateway(tmp_path, monkeypatch):
    manifest, value, units, runner, targets = local_deployment(tmp_path, monkeypatch)
    manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    runner.active = True
    runner.entry_listening = False
    del value["connectors"][0]["local_tunnel"]
    manifest.write_text(json.dumps(value))
    runner.events.clear()
    result = manage.up(manifest, manage.Target("connector", "dashboard"), apply=True, runner=runner, unit_root=units)
    assert result["activated"] and runner.registered
    assert ("restart", "anvil-connect-gateway.service") not in runner.events


def test_local_status_reports_authoritative_per_resource_readiness(tmp_path, monkeypatch):
    manifest, value, units, runner, targets = local_deployment(tmp_path, monkeypatch)
    manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    result = manage.status(manifest, manage.Target("connector", "dashboard"), runner=runner)["targets"][0]["tunnel"]
    assert result["readiness"] == "ready" and len(result["entries"][0]["resources"]) == 2
    runner.registered = False
    result = manage.status(manifest, manage.Target("connector", "dashboard"), runner=runner)["targets"][0]["tunnel"]
    assert result["readiness"] == "not-ready" and result["state"] == "degraded"


def test_idle_public_connector_does_not_restart_on_unchanged_up(tmp_path, monkeypatch):
    manifest, value, units, runner, targets = local_deployment(tmp_path, monkeypatch)
    del value["gateway"]["local_tunnel"]
    del value["connectors"][0]["local_tunnel"]
    manifest.write_text(json.dumps(value))
    manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    runner.active = True
    runner.registered = False
    runner.calls.clear()

    tunnel = manage.status(manifest, manage.Target("connector", "dashboard"), runner=runner)["targets"][0]["tunnel"]
    result = manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)

    assert tunnel["readiness"] == "idle" and tunnel["state"] == "unknown"
    assert "not origin readiness" in tunnel["evidence"]
    assert not result["activated"] and result["restarted_units"] == []
    assert not any(call[0] == manage._SYSTEMCTL and call[1] != "show" for call in runner.calls)


def test_local_initialization_creates_authority_before_material_preflight(tmp_path, monkeypatch):
    manifest, value, units, runner, targets = local_deployment(tmp_path, monkeypatch)
    manage.native_init(manifest, manage.Target("gateway"), apply=True, runner=runner)
    assert any(c[1] == "validate" for c in runner.calls)
    assert any(c[1] == "init" for c in runner.calls)
    assert not any(c[1] == "preflight" for c in runner.calls)
    def invalid_authorities(argv, timeout, identity):
        if argv[1] == "preflight": return manage.RunResult(1)
        return runner(argv, timeout, identity)
    with pytest.raises(manage.ManageError, match="native declaration validation failed"):
        manage.up_many(manifest, targets, apply=True, runner=invalid_authorities, unit_root=units)
    assert not Path(value["config_root"]).exists()


def test_oidc_gate_uses_total_deadline_and_edge_identity(monkeypatch):
    now = [0.0]
    identity = manage.ServiceIdentity(1201, 2201)
    monkeypatch.setattr(manage.time, "monotonic", lambda: now[0])
    calls = []
    def runner(argv, timeout, selected):
        assert selected == identity and argv[-3:] == ("https://auth.example.test", "127.0.0.1:443", "")
        now[0] += timeout
        calls.append(argv)
        return manage.RunResult(1)
    with pytest.raises(manage.ManageError, match="identity provider discovery"):
        manage._oidc_ready("https://auth.example.test", ":443", runner, identity)
    assert now[0] == 30 and len(calls) == 15
    for issuer, listen in [("https://auth.example.test/path", ":443"), ("https://auth.example.test", "127.0.0.1:99999")]:
        with pytest.raises(manage.ManageError, match="authority"):
            manage._oidc_ready(issuer, listen, runner, identity)
    assert len(calls) == 15


@pytest.fixture(params=[False, True], ids=["ingress", "admin"])
def stale_gateway_socket(tmp_path, monkeypatch, request):
    administrative = request.param
    directory = tmp_path / 'ingress'
    directory.mkdir()
    directory.chmod(0o700 if administrative else 0o2710)
    path = directory / ('admin.sock' if administrative else 'ingress.sock')
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path))
    path.chmod(0o600 if administrative else 0o660)
    data = {'test_administrative': administrative, 'gateway': {'state_directory': str(directory)}, 'service_limits': {}, 'service_identities': {
        'gateway': {'uid': os.getuid(), 'gid': os.getgid()},
        'ingress': {'directory': str(directory), 'group_id': os.getgid()},
    }}
    monkeypatch.setattr(manage, '_safe_root_ancestors', lambda *_: None)
    calls = []

    def runner(argv, timeout, identity):
        calls.append(argv)
        return manage.RunResult(0, b'ActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\n')

    return data, path, runner, calls


def test_stale_ingress_recovery_removes_only_stopped_owned_socket(stale_gateway_socket):
    data, path, runner, calls = stale_gateway_socket
    account = path.parent / 'account-data'
    account.write_bytes(b'preserved')
    assert manage._recover_stale_gateway_socket(data, runner, administrative=data['test_administrative'])
    assert not path.exists() and account.read_bytes() == b'preserved'
    assert calls[0] == ('/usr/bin/systemctl', 'stop', 'anvil-connect-gateway.service')
    calls.clear()
    assert not manage._recover_stale_gateway_socket(data, runner, administrative=data['test_administrative'])
    assert not calls


@pytest.mark.parametrize('unsafe', ['file', 'symlink', 'mode', 'owner'])
def test_stale_ingress_recovery_rejects_unsafe_entry_before_stop(stale_gateway_socket, unsafe):
    data, path, runner, calls = stale_gateway_socket
    if unsafe in {'file', 'symlink'}:
        path.unlink()
        if unsafe == 'file':
            path.write_bytes(b'preserve')
        else:
            path.symlink_to('missing')
    elif unsafe == 'mode':
        path.chmod(0o666)
    else:
        data['service_identities']['gateway']['uid'] += 1
    with pytest.raises(manage.ManageError):
        manage._recover_stale_gateway_socket(data, runner, administrative=data['test_administrative'])
    assert os.path.lexists(path) and not calls


def test_stale_ingress_recovery_rejects_live_listener(stale_gateway_socket):
    data, path, runner, _ = stale_gateway_socket
    path.unlink()
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path))
        path.chmod(0o600 if data['test_administrative'] else 0o660)
        listener.listen()
        with pytest.raises(manage.ManageError, match='still listening'):
            manage._recover_stale_gateway_socket(data, runner, administrative=data['test_administrative'])
        assert path.exists()


@pytest.mark.parametrize('observation', [
    b'ActiveState=activating\nSubState=auto-restart\nMainPID=0\nControlPID=0\n',
    b'ActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=42\n', b'',
    b'ActiveState=active\nActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\n',
    b'ActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\nUnexpected=1\n',
])
def test_stale_ingress_recovery_requires_proven_stop(stale_gateway_socket, observation):
    data, path, _, _ = stale_gateway_socket
    with pytest.raises(manage.ManageError, match='not stopped'):
        manage._recover_stale_gateway_socket(data, lambda *_: manage.RunResult(0, observation), administrative=data['test_administrative'])
    assert path.exists()


def test_stale_ingress_recovery_preserves_replaced_socket(stale_gateway_socket):
    data, path, runner, _ = stale_gateway_socket
    original = path.parent / 'original.sock'

    def replacement(argv, timeout, identity):
        if argv[1] == 'stop':
            path.rename(original)
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(path))
            path.chmod(0o600 if data['test_administrative'] else 0o660)
        return runner(argv, timeout, identity)

    with pytest.raises(manage.ManageError, match='socket changed'):
        manage._recover_stale_gateway_socket(data, replacement, administrative=data['test_administrative'])
    assert original.exists() and path.exists()


def test_gateway_recovery_is_apply_only_and_preserves_other_services(tmp_path, monkeypatch):
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / 'units'
    units.mkdir(mode=0o755)
    runner = SyntheticRunner(active=True)
    runner.unit_root = units
    manage.up(manifest, manage.Target('gateway'), apply=True, runner=runner, unit_root=units)
    runner.calls.clear()
    monkeypatch.setattr(manage, '_runtime_healthy', lambda _d, _t, unit, *_a: unit != 'anvil-connect-gateway.service')
    recovered = []
    monkeypatch.setattr(manage, '_recover_stale_gateway_socket', lambda *_, administrative=False: recovered.append(administrative) or True)
    manage.up(manifest, manage.Target('gateway'), runner=runner, unit_root=units)
    assert not recovered
    result = manage.up(manifest, manage.Target('gateway'), apply=True, runner=runner, unit_root=units)
    assert recovered == [False, True] and result['recovered_gateway_sockets'] == ['ingress', 'admin']
    assert result['restarted_units'] == ['anvil-connect-gateway.service']
    assert not any(call[1] in {'stop', 'restart', 'enable', 'disable'} and call[-1] != 'anvil-connect-gateway.service'
                   for call in runner.calls)
