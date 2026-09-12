from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import re
import stat
import sys
import tempfile
from types import SimpleNamespace
from pathlib import Path

import pytest

from anvil_serving.connect import manage
from anvil_serving.connect.render import render as render_config

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
    monkeypatch.setattr(manage, "_validate_isolated_runtime", lambda *_: None)
    monkeypatch.setattr(manage, "_safe_root_ancestors", lambda *_: None)
    monkeypatch.setattr(manage, "_role_service_identity", lambda *_: None)
    manifest = tmp_path / "deployment.json"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(manage, "_component_lock", lambda: {"caddy": caddy_digest, "authelia": authelia_digest})
    return manifest, value, native


def test_gateway_status_parser_accepts_native_omitted_field_shape() -> None:
    manage._closed_gateway_status(_gateway_status())


@pytest.mark.parametrize("mutation", ("false-generation", "false-nested-generation", "unknown", "secret"))
def test_gateway_status_parser_rejects_non_native_output(mutation: str) -> None:
    value = json.loads(_gateway_status())
    if mutation == "false-generation":
        value["generation"] = False
    elif mutation == "false-nested-generation":
        value["status"]["generation"] = False
    elif mutation == "unknown":
        value["control_host"] = ""
    else:
        value["secret"] = "unexpected"
    with pytest.raises(manage.ManageError, match="gateway readiness response"):
        manage._closed_gateway_status(json.dumps(value).encode())


@pytest.mark.parametrize("raw", (
    _gateway_status().rstrip(b"}") + b',"operation":"status"}',
    _gateway_status() + b"trailing",
))
def test_gateway_status_parser_rejects_duplicate_or_trailing_data(raw: bytes) -> None:
    with pytest.raises(manage.ManageError, match="gateway readiness response"):
        manage._closed_gateway_status(raw)


def test_gateway_readiness_rejects_limited_native_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, value, native = deployment(tmp_path, monkeypatch)

    def limited(argv, timeout, identity):  # type: ignore[no-untyped-def]
        assert argv[:2] == (str(native), "admin")
        return manage.RunResult(0, b"", output_limited=True)

    with pytest.raises(manage.ManageError, match="output bound"):
        manage._gateway_ready(value, limited)


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


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        (manage.up, (Path("/missing/deployment.json"), manage.Target("gateway"))),
        (manage.up_many, (Path("/missing/deployment.json"), (manage.Target("gateway"),))),
    ],
)
def test_up_rejects_unsupported_platform_before_manifest_read(
    monkeypatch: pytest.MonkeyPatch, operation, arguments
) -> None:
    monkeypatch.setattr(manage, "supported_platform", lambda: False)
    with pytest.raises(manage.UnsupportedPlatformError):
        operation(*arguments)


def test_validate_and_render_preview_never_stage_or_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    runner = SyntheticRunner()
    checked = manage.validate(manifest, manage.Target("connector", "dashboard"), runner=runner)
    assert checked["applied"] is False
    assert checked["targets"] == ["connector:dashboard"]
    assert not Path(value["config_root"]).exists()
    preview = manage.render(manifest, runner=runner)
    assert preview["applied"] is False
    assert preview["plan"]["state"] == "absent"
    assert not Path(value["config_root"]).exists()
    assert not any(call[0] == "/usr/bin/systemctl" and call[1] != "show" for call in runner.calls)


def test_render_apply_only_stages_after_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    result = manage.render(manifest, apply=True, runner=SyntheticRunner())
    assert result["stage"]["state"] == "staged"
    assert not Path(value["config_root"]).exists()
    assert (Path(result["stage"]["path"]) / "managed.json").is_file()


def test_gateway_first_up_activates_only_gateway_units_in_oidc_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir()
    units.chmod(0o755)
    runner = SyntheticRunner()
    runner.unit_root = units
    result = manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    assert result["activated"] is True
    active = Path(value["config_root"])
    assert active.is_dir()
    assert (active / "managed.json").is_file()
    assert (units / "anvil-connect-gateway.service").is_file()
    assert not (units / "anvil-connect-connector-dashboard.service").exists()
    starts = [call[-1] for call in runner.calls if call[:3] == ("/usr/bin/systemctl", "enable", "--now")]
    assert starts == ["anvil-connect-authelia.service", "anvil-connect-caddy.service", "anvil-connect-gateway.service"]
    record = json.loads((tmp_path / ".rendered.anvil-connect-activation.json").read_text())
    assert record["native_sha256"] == hashlib.sha256(b"native-v1").hexdigest()


def test_up_refuses_global_change_to_unselected_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir()
    units.chmod(0o755)
    runner = SyntheticRunner()
    runner.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    changed = copy.deepcopy(value)
    changed["connectors"][0]["resources"][0]["envelope"]["origin_url"] = "http://127.0.0.1:19082"
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(manage.ManageError, match="unselected target"):
        manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    assert json.loads((Path(value["config_root"]) / "connectors/dashboard.json").read_text())["resources"][0]["envelope"]["origin_url"] == "http://127.0.0.1:18080"


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


def test_up_many_coordinates_gateway_and_connector_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    runner = SyntheticRunner(); runner.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    changed = copy.deepcopy(value)
    changed["gateway"]["gateway"]["max_concurrent"] = 63
    changed["connectors"][0]["resources"][0]["envelope"]["origin_url"] = "http://127.0.0.1:19082"
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    result = manage.up_many(manifest, (manage.Target("connector", "dashboard"), manage.Target("gateway")), apply=True, runner=runner, unit_root=units)
    assert result["targets"] == ["gateway", "connector:dashboard"]
    root = Path(value["config_root"])
    assert json.loads((root / "gateway.json").read_text())["gateway"]["max_concurrent"] == 63
    assert json.loads((root / "connectors/dashboard.json").read_text())["resources"][0]["envelope"]["origin_url"] == "http://127.0.0.1:19082"


def test_up_many_upgrade_requires_prior_bytes_and_new_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, native = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    runner = SyntheticRunner(); runner.unit_root = units
    all_targets = manage._targets(value, None)
    manage.up_many(manifest, all_targets, apply=True, runner=runner, unit_root=units)
    new_native = tmp_path / "anvil-connect-v2"
    new_caddy = tmp_path / "caddy-v2"
    new_authelia = tmp_path / "authelia-v2"
    native_digest = _executable(new_native, b"native-v2")
    caddy_digest = _executable(new_caddy, b"caddy-v2")
    authelia_digest = _executable(new_authelia, b"authelia-v2")
    changed = copy.deepcopy(value)
    changed["binary"] = str(new_native)
    changed["components"] = {"caddy": str(new_caddy), "authelia": str(new_authelia)}
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    monkeypatch.setattr(manage, "_component_lock", lambda: {"caddy": caddy_digest, "authelia": authelia_digest})
    with pytest.raises(manage.ManageError, match="binary changed"):
        manage.up_many(manifest, all_targets, apply=True, runner=runner, unit_root=units)
    result = manage.up_many(manifest, all_targets, upgrade=True, apply=True, runner=runner, unit_root=units)
    assert result["upgrade"] is True
    record = json.loads((tmp_path / ".rendered.anvil-connect-activation.json").read_text())
    assert record["native_sha256"] == native_digest
    assert record["components"] == {"caddy": caddy_digest, "authelia": authelia_digest}
    assert hashlib.sha256(native.read_bytes()).hexdigest() == hashlib.sha256(b"native-v1").hexdigest()


def test_up_many_waits_for_gateway_admin_before_starting_connector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, native = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    initial = SyntheticRunner(); initial.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=initial, unit_root=units)
    changed = copy.deepcopy(value)
    changed["gateway"]["gateway"]["max_concurrent"] = 63
    changed["connectors"][0]["resources"][0]["envelope"]["origin_url"] = "http://127.0.0.1:19082"
    manifest.write_text(json.dumps(changed), encoding="utf-8")

    class DelayedGatewayRunner(SyntheticRunner):
        def __init__(self) -> None:
            super().__init__()
            self.status_calls = 0

        def __call__(self, argv, timeout, identity):  # type: ignore[no-untyped-def]
            if argv[:2] == (str(native), "admin"):
                self.calls.append(argv)
                self.status_calls += 1
                if self.status_calls == 1:
                    return manage.RunResult(1)
                return manage.RunResult(0, _gateway_status())
            return super().__call__(argv, timeout, identity)

    runner = DelayedGatewayRunner(); runner.unit_root = units
    manage.up_many(manifest, (manage.Target("gateway"), manage.Target("connector", "dashboard")), apply=True, runner=runner, unit_root=units)
    gateway_start = next(index for index, call in enumerate(runner.calls) if call[-1] == "anvil-connect-gateway.service" and call[1] in {"enable", "restart"})
    second_status = [index for index, call in enumerate(runner.calls) if call[:2] == (str(native), "admin")][1]
    connector_start = next(index for index, call in enumerate(runner.calls) if call[-1] == "anvil-connect-connector-dashboard.service" and call[1] in {"enable", "restart"})
    assert runner.status_calls == 2
    assert gateway_start < second_status < connector_start


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


def test_up_many_refuses_same_path_binary_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, native = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    runner = SyntheticRunner(); runner.unit_root = units
    all_targets = manage._targets(value, None)
    manage.up_many(manifest, all_targets, apply=True, runner=runner, unit_root=units)
    _executable(native, b"native-replaced-in-place")
    with pytest.raises(manage.ManageError, match="prior native executable"):
        manage.up_many(manifest, all_targets, upgrade=True, apply=True, runner=runner, unit_root=units)


def test_up_many_start_failure_rolls_back_full_selected_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    runner = SyntheticRunner(); runner.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    root = Path(value["config_root"])
    old_gateway = (root / "gateway.json").read_bytes()
    changed = copy.deepcopy(value)
    changed["gateway"]["gateway"]["max_concurrent"] = 63
    changed["connectors"][0]["resources"][0]["envelope"]["origin_url"] = "http://127.0.0.1:19082"
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    failing = SyntheticRunner(fail_start="anvil-connect-connector-dashboard.service")
    failing.unit_root = units
    with pytest.raises(manage.ManageError) as caught:
        manage.up_many(manifest, (manage.Target("gateway"), manage.Target("connector", "dashboard")), apply=True, runner=failing, unit_root=units)
    assert caught.value.may_have_executed is True
    assert (root / "gateway.json").read_bytes() == old_gateway
    assert not (units / "anvil-connect-connector-dashboard.service").exists()


def test_failed_activation_restores_previous_owned_config_and_unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir()
    units.chmod(0o755)
    runner = SyntheticRunner()
    runner.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    root = Path(value["config_root"])
    before_config = (root / "gateway.json").read_bytes()
    before_unit = (units / "anvil-connect-gateway.service").read_bytes()
    changed = copy.deepcopy(value)
    changed["gateway"]["gateway"]["max_concurrent"] = 63
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    failing = SyntheticRunner(fail_daemon_reload=True)
    failing.unit_root = units
    with pytest.raises(manage.ManageError, match="systemd did not accept"):
        manage.up(manifest, manage.Target("gateway"), apply=True, runner=failing, unit_root=units)
    assert (root / "gateway.json").read_bytes() == before_config
    assert (units / "anvil-connect-gateway.service").read_bytes() == before_unit
    assert not (tmp_path / ".rendered.anvil-connect-rollback").exists()


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


def test_init_bootstraps_from_temporary_declaration_without_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, native = deployment(tmp_path, monkeypatch)
    runner = SyntheticRunner()
    result = manage.native_init(manifest, manage.Target("gateway"), apply=True, runner=runner)
    assert result["native_sha256"] == hashlib.sha256(b"native-v1").hexdigest()
    init = next(call for call in runner.calls if call[:3] == (str(native), "init", "--mode"))
    config = Path(init[init.index("--config") + 1])
    assert config.name == "gateway.json"
    assert not Path(value["config_root"]).exists()
    assert not any(call[0] == "/usr/bin/systemctl" and call[1] in {"enable", "restart"} for call in runner.calls)


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


def test_vendor_fragment_is_rejected_when_destination_is_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)

    class VendorRunner(SyntheticRunner):
        def __call__(self, argv, timeout, identity):  # type: ignore[no-untyped-def]
            if argv[:3] == ("/usr/bin/systemctl", "show", "--property=LoadState,FragmentPath,DropInPaths"):
                return manage.RunResult(0, b"LoadState=loaded\nFragmentPath=/usr/lib/systemd/system/anvil-connect-gateway.service\nDropInPaths=\n")
            return super().__call__(argv, timeout, identity)

    runner = VendorRunner(); runner.unit_root = units
    with pytest.raises(manage.ManageError, match="owned elsewhere"):
        manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    assert not list(units.iterdir())


def test_invalid_systemd_metadata_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)

    class InvalidRunner(SyntheticRunner):
        def __call__(self, argv, timeout, identity):  # type: ignore[no-untyped-def]
            if argv[:3] == ("/usr/bin/systemctl", "show", "--property=LoadState,FragmentPath,DropInPaths"):
                return manage.RunResult(0, b"FragmentPath=\nDropInPaths=\n")
            return super().__call__(argv, timeout, identity)

    with pytest.raises(manage.ManageError, match="metadata"):
        manage.up(manifest, manage.Target("gateway"), apply=True, runner=InvalidRunner(), unit_root=units)


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


def test_bounded_run_kills_grandchild_holding_pipe() -> None:
    import sys
    started = __import__("time").monotonic()
    script = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); sys.exit(0)"
    with pytest.raises(manage.ManageError, match="exceeded"):
        manage._bounded_run((sys.executable, "-c", script), 0.1)
    assert __import__("time").monotonic() - started < 2


def test_bounded_run_waits_for_child_that_closes_output_before_exit() -> None:
    script = "import os,time; os.close(1); os.close(2); time.sleep(0.05)"
    result = manage._bounded_run((sys.executable, "-c", script), 0.5)
    assert result == manage.RunResult(0)


def test_environment_file_requires_exact_owner_only_mode_before_activation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    Path(value["environment_files"]["gateway"]).chmod(0o640)
    runner = SyntheticRunner()
    with pytest.raises(manage.ManageError, match="EnvironmentFile"):
        manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=tmp_path)
    assert runner.calls == []


def test_signed_gateway_identity_environment_file_is_gateway_only_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    resource = next(item for item in value["gateway"]["gateway"]["resources"] if item["rule"]["access"] == "browser")
    resource["rule"]["native_auth"] = "signed-identity"
    resource.update(identity_key_env="ANVIL_DASHBOARD_IDENTITY_KEY", identity_key_id="dashboard-v1")
    for connector in value["connectors"]:
        for item in connector["resources"]:
            if item["envelope"]["rule"]["id"] == resource["rule"]["id"]:
                item["envelope"]["rule"]["native_auth"] = "signed-identity"
    identity = tmp_path / "gateway-identity.env"
    identity.write_bytes(b"")
    identity.chmod(0o600)
    value["environment_files"]["gateway_identity"] = str(identity)
    manifest.write_text(json.dumps(value), encoding="utf-8")
    original_lstat = Path.lstat

    def root_owned_lstat(path):
        info = original_lstat(path)
        if path == identity:
            return os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink,
                                   0, info.st_gid, info.st_size, info.st_atime, info.st_mtime, info.st_ctime))
        return info

    monkeypatch.setattr(Path, "lstat", root_owned_lstat)

    assert manage.validate(manifest, manage.Target("gateway"), runner=SyntheticRunner())["targets"] == ["gateway"]
    identity.chmod(0o640)
    runner = SyntheticRunner()
    with pytest.raises(manage.ManageError, match="EnvironmentFile"):
        manage.validate(manifest, manage.Target("gateway"), runner=runner)
    assert runner.calls == []
    identity.chmod(0o600)
    identity.unlink()
    assert manage.validate(manifest, manage.Target("connector", "dashboard"), runner=SyntheticRunner())["targets"] == ["connector:dashboard"]
    with pytest.raises(manage.ManageError, match="EnvironmentFile"):
        manage.validate(manifest, manage.Target("gateway"), runner=SyntheticRunner())


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


@pytest.mark.parametrize("status", ("active", "revoked", "unknown", "ENROLLED"))
def test_closed_identity_rejects_non_native_connector_status(status: str) -> None:
    raw = json.dumps({
        "id": "dashboard",
        "status": status,
        "fingerprint": "A" * 43,
        "epoch": "a" * 64,
        "generation": 1,
        "resources": ["dashboard", "dashboard-api"],
    }).encode()
    with pytest.raises(manage.ManageError, match="identity output"):
        manage._closed_identity(raw)


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


def test_connector_validation_uses_only_its_declared_environment_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    Path(value["environment_files"]["gateway"]).unlink()
    for path in value["environment_files"]["clients"].values():
        Path(path).unlink()
    assert manage.validate(manifest, manage.Target("connector", "dashboard"), runner=SyntheticRunner())["targets"] == ["connector:dashboard"]
    with pytest.raises(manage.ManageError, match="EnvironmentFile"):
        manage.render(manifest, runner=SyntheticRunner())


def test_current_operation_refuses_changed_native_binary_and_error_classifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, native = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    runner = SyntheticRunner(); runner.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    native.write_bytes(b"native-v2"); native.chmod(0o755)
    with pytest.raises(manage.ManageError, match="binary changed") as caught:
        manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    assert isinstance(caught.value, RuntimeError) and caught.value.may_have_executed is False


def test_mutating_action_marks_uncertain_execution() -> None:
    def failing(argv, timeout, identity):  # type: ignore[no-untyped-def]
        return manage.RunResult(1)
    with pytest.raises(manage.ManageError) as caught:
        manage._action(failing, ("/fixed/action",), 1, "failed")
    assert caught.value.may_have_executed is True


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


def test_connector_first_establishes_gateway_component_pins_later(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    runner = SyntheticRunner(); runner.unit_root = units
    manage.up(manifest, manage.Target("connector", "dashboard"), apply=True, runner=runner, unit_root=units)
    record = json.loads((tmp_path / ".rendered.anvil-connect-activation.json").read_text())
    assert record["components"] == {}
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    record = json.loads((tmp_path / ".rendered.anvil-connect-activation.json").read_text())
    assert set(record["components"]) == {"caddy", "authelia"}
    assert Path(value["config_root"]).is_dir()


def test_connector_update_preserves_existing_gateway_component_pins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    runner = SyntheticRunner(); runner.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    record_path = tmp_path / ".rendered.anvil-connect-activation.json"
    before = json.loads(record_path.read_text())["components"]
    changed = copy.deepcopy(value)
    changed["connectors"][0]["resources"][0]["envelope"]["origin_url"] = "http://127.0.0.1:19082"
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    manage.up(manifest, manage.Target("connector", "dashboard"), apply=True, runner=runner, unit_root=units)
    assert json.loads(record_path.read_text())["components"] == before


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


def test_partial_component_activation_record_is_closed_before_binding_or_upgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(); units.chmod(0o755)
    runner = SyntheticRunner(); runner.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    record_path = tmp_path / ".rendered.anvil-connect-activation.json"
    record = json.loads(record_path.read_text())
    record["components"] = {"caddy": record["components"]["caddy"]}
    record_path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(manage.ManageError, match="activation record is not owned"):
        manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    with pytest.raises(manage.ManageError, match="activation record is not owned"):
        manage.up_many(manifest, manage._targets(value, None), upgrade=True, apply=True, runner=runner, unit_root=units)


def test_role_identity_rejects_root_and_group_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {"service_identities": {}}
    monkeypatch.setattr(manage, "role_identity", lambda *_: (0, 10))
    monkeypatch.setattr(manage.pwd, "getpwuid", lambda _: SimpleNamespace(pw_uid=0, pw_gid=10, pw_name="svc"))
    monkeypatch.setattr(manage.grp, "getgrgid", lambda _: SimpleNamespace(gr_gid=10))
    with pytest.raises(manage.ManageError, match="unsafe"):
        manage._role_service_identity(data, "gateway")
    monkeypatch.setattr(manage, "role_identity", lambda *_: (10, 12))
    monkeypatch.setattr(manage.pwd, "getpwuid", lambda _: SimpleNamespace(pw_uid=10, pw_gid=11, pw_name="svc"))
    monkeypatch.setattr(manage.grp, "getgrgid", lambda _: SimpleNamespace(gr_gid=12))
    with pytest.raises(manage.ManageError, match="unsafe"):
        manage._role_service_identity(data, "gateway")


def test_restore_preserves_enabled_runtime_and_partial_failure() -> None:
    calls: list[tuple[str, ...]] = []
    def runner(argv, timeout, identity):  # type: ignore[no-untyped-def]
        calls.append(argv)
        return manage.RunResult(0)
    manage._restore_running(runner, ("anvil-connect-gateway.service",), {"anvil-connect-gateway.service": (False, "enabled-runtime")})
    assert calls == [("/usr/bin/systemctl", "disable", "--now", "anvil-connect-gateway.service"), ("/usr/bin/systemctl", "enable", "--runtime", "anvil-connect-gateway.service")]

    def failing(argv, timeout, identity):  # type: ignore[no-untyped-def]
        return manage.RunResult(1)
    with pytest.raises(manage.ManageError) as caught:
        manage._restore_running(failing, ("anvil-connect-gateway.service",), {"anvil-connect-gateway.service": (False, "enabled-runtime")})
    assert caught.value.may_have_executed is True


def test_post_exec_unit_failure_does_not_commit_activation(tmp_path: Path, monkeypatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / 'units'
    units.mkdir(mode=0o755)

    class DiesAfterExec(SyntheticRunner):
        def __call__(self, argv, timeout, identity):
            if 'MainPID' in ' '.join(argv):
                return manage.RunResult(0, f'ActiveState=failed\nSubState=failed\nMainPID=0\nFragmentPath={units / argv[-1]}\nDropInPaths=\n'.encode())
            return super().__call__(argv, timeout, identity)

    runner = DiesAfterExec()
    runner.unit_root = units
    with pytest.raises(manage.ManageError, match='startup'):
        manage.up(manifest, manage.Target('gateway'), apply=True, runner=runner, unit_root=units)
    assert not Path(value['config_root']).exists()
    assert list(units.iterdir()) == []


@pytest.mark.parametrize("local", [False, True])
def test_schema_render_consumes_local_fields_without_starting_services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local: bool) -> None:
    from tests.connect.test_render import local_tunnel_manifest

    manifest_path, value, native = deployment(tmp_path, monkeypatch)
    if local:
        value = local_tunnel_manifest(value)
        manifest_path.write_text(json.dumps(value))
    expected = render_config(value)
    seen = {}
    synthetic = SyntheticRunner()

    def inspect(argv, timeout, identity):
        if argv[0] == str(native) and argv[1] == "preflight":
            mode = argv[argv.index("--mode") + 1]
            seen[mode] = Path(argv[argv.index("--config") + 1]).read_text()
        return synthetic(argv, timeout, identity)

    result = manage.render(manifest_path, apply=True, runner=inspect)
    assert result["generation"] == expected["generation"]
    assert seen == {"gateway": expected["files"]["gateway.json"], "connector": expected["files"]["connectors/dashboard.json"],
                    "client": expected["files"]["clients/dashboard-api.json"]}
    staged = Path(result["stage"]["path"])
    assert {name: (staged / name).read_text() for name in expected["files"]} == expected["files"]
    again = manage.render(manifest_path, apply=True, runner=inspect)
    assert again["stage"] == result["stage"]
    assert not Path(value["config_root"]).exists()
    assert not any(call[0] == "/usr/bin/systemctl" for call in synthetic.calls)


@pytest.mark.parametrize("mutation", ["missing", "address", "server_name", "http_host", "null", "proxy"])
def test_local_selection_rejected_before_native_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str) -> None:
    from anvil_serving.connect.config import ManifestError
    from tests.connect.test_render import local_tunnel_manifest

    manifest_path, value, _ = deployment(tmp_path, monkeypatch)
    value = local_tunnel_manifest(value)
    endpoint = value["connectors"][0]["local_tunnel"]
    if mutation == "missing":
        del value["gateway"]["local_tunnel"]
    elif mutation in {"address", "server_name", "http_host"}:
        endpoint[mutation] = "127.0.0.1:18444" if mutation == "address" else "other.example.test"
    elif mutation == "null":
        value["connectors"][0]["local_tunnel"] = None
    else:
        endpoint["proxy"] = "http://127.0.0.1:1080"
    manifest_path.write_text(json.dumps(value))
    runner = SyntheticRunner()
    for operation in (manage.validate, manage.render):
        with pytest.raises(ManifestError, match="must match gateway local_tunnel|null|unknown"):
            operation(manifest_path, runner=runner)
    assert runner.calls == []
    assert not Path(value["config_root"]).exists()


def test_unchanged_healthy_gateway_converges_without_restart(tmp_path, monkeypatch):
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(mode=0o755)
    runner = SyntheticRunner(active=True)
    runner.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    record = manage._activation_record(Path(value["config_root"])).read_bytes()
    runner.calls.clear()
    result = manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    assert not result["activated"] and result["restarted_units"] == []
    assert not any(call[1] in {"restart", "enable", "stop", "disable"} for call in runner.calls)
    assert manage._activation_record(Path(value["config_root"])).read_bytes() == record


def test_gateway_only_change_preserves_healthy_edge_and_idp(tmp_path, monkeypatch):
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(mode=0o755)
    runner = SyntheticRunner(active=True)
    runner.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    value["gateway"]["gateway"]["max_concurrent"] = 63
    manifest.write_text(json.dumps(value))
    runner.calls.clear()
    preview = manage.up(manifest, manage.Target("gateway"), runner=runner, unit_root=units)
    assert preview["configuration_restarts"] == ["anvil-connect-gateway.service"]
    result = manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    assert result["restarted_units"] == ["anvil-connect-gateway.service"]
    assert not any(call[1] in {"restart", "enable"} and call[-1] in {"anvil-connect-caddy.service", "anvil-connect-authelia.service"}
                   for call in runner.calls)


def test_unhealthy_unchanged_gateway_is_restarted(tmp_path, monkeypatch):
    manifest, value, native = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(mode=0o755)
    initial = SyntheticRunner(active=True)
    initial.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=initial, unit_root=units)

    class FailedAdmin(SyntheticRunner):
        def __call__(self, argv, timeout, identity):
            if argv[:2] == (str(native), "admin") and not any(call[1] == "restart" for call in self.calls):
                self.calls.append(argv)
                return manage.RunResult(1)
            return super().__call__(argv, timeout, identity)

    runner = FailedAdmin(active=True)
    runner.unit_root = units
    preview = manage.up(manifest, manage.Target("gateway"), runner=runner, unit_root=units)
    assert preview["configuration_restarts"] == []
    assert preview["runtime_actions"] == [{"unit": "anvil-connect-gateway.service", "action": "restart", "reason": "runtime check failed"}]
    result = manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    assert result["restarted_units"] == ["anvil-connect-gateway.service"]


def test_local_preview_exposes_references_without_mutation(tmp_path, monkeypatch):
    from tests.connect.test_render import local_tunnel_manifest

    manifest, value, _ = deployment(tmp_path, monkeypatch)
    value = local_tunnel_manifest(value)
    manifest.write_text(json.dumps(value))
    units = tmp_path / "units"
    units.mkdir(mode=0o755)
    runner = SyntheticRunner()
    runner.unit_root = units
    targets = (manage.Target("gateway"), manage.Target("connector", "dashboard"))
    preview = manage.up_many(manifest, targets, runner=runner, unit_root=units)
    assert preview["activation_blockers"] == []
    gateway, connector = preview["tunnel_selections"]
    assert gateway["paths"] == ["public", "local"] and connector["paths"] == ["local"]
    assert gateway["local_tunnel"] == value["gateway"]["local_tunnel"]
    assert "private_key_file" not in connector["local_tunnel"]
    assert list(units.iterdir()) == [] and not Path(value["config_root"]).exists()
    assert not any(call[0] == "/usr/bin/systemctl" and call[1] != "show" for call in runner.calls)


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


def test_new_public_connector_has_no_prior_local_selection(tmp_path, monkeypatch):
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    units = tmp_path / "units"
    units.mkdir(mode=0o755)
    runner = SyntheticRunner(active=True)
    runner.unit_root = units
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=units)
    secondary = copy.deepcopy(value["connectors"][0])
    secondary["id"] = "secondary"
    secondary["state_directory"] = str(tmp_path / "secondary-state")
    resource = value["connectors"][0]["resources"].pop()
    secondary["resources"] = [resource]
    value["connectors"].append(secondary)
    moved_id = resource["envelope"]["rule"]["id"]
    next(item for item in value["gateway"]["gateway"]["resources"] if item["rule"]["id"] == moved_id)["connector"] = "secondary"
    value["service_identities"]["connectors"]["secondary"] = {"uid": 1206, "gid": 2206}
    value["service_limits"]["connectors"]["secondary"] = value["service_limits"]["connectors"]["dashboard"]
    value["environment_files"]["connectors"]["secondary"] = str(tmp_path / "secondary.env")
    monkeypatch.setattr(manage, "_validate_environment_files", lambda *_: None)
    manifest.write_text(json.dumps(value))
    # Existing migration detection requires all declared targets on addition.
    targets = manage._targets(value, None)
    preview = manage.up_many(manifest, targets, runner=runner, unit_root=units)
    assert preview["activation_blockers"] == []
    assert manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)["activated"]
    assert (units / "anvil-connect-connector-secondary.service").is_file()


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


@pytest.mark.parametrize("failure", ["crlf", "unavailable", "rollover"])
def test_events_fail_unknown_on_invalid_or_changed_invocation(failure):
    unit = "anvil-connect-connector-dashboard.service"
    calls = []

    def runner(argv, timeout, identity):
        calls.append(argv)
        if argv[0] == "/usr/bin/journalctl":
            return manage.RunResult(0, json.dumps({"_SYSTEMD_UNIT": unit, "_SYSTEMD_INVOCATION_ID": "a" * 32,
                                                  "MESSAGE": "connect_event path=public resource=dashboard reason=tunnel_established"}).encode())
        if failure == "unavailable":
            return manage.RunResult(1)
        if failure == "crlf":
            return manage.RunResult(0, ("InvocationID=" + "a" * 32 + "\r\n").encode())
        return manage.RunResult(0, ("InvocationID=" + ("a" if len(calls) == 1 else "b") * 32 + "\n").encode())

    assert manage._native_events(runner, unit) == []


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


def test_local_trust_and_admission_gate_commit_then_converge(tmp_path, monkeypatch):
    manifest, value, units, runner, targets = local_deployment(tmp_path, monkeypatch)
    commit = manage._write_activation_record
    def checked_commit(root, record):
        assert runner.registered and runner.entry_listening
        commit(root, record)
    monkeypatch.setattr(manage, "_write_activation_record", checked_commit)
    manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    connector_start = next(i for i, event in enumerate(runner.events) if event == ("enable", "anvil-connect-connector-dashboard.service"))
    assert ("snapshot", (True, False)) in runner.events[:connector_start]
    assert any(c[1] == "preflight" and "--input" in c for c in runner.calls)
    runner.active = True
    runner.calls.clear()
    result = manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    assert not result["activated"] and result["restarted_units"] == []
    assert not any(c[0] == manage._SYSTEMCTL and c[1] != "show" for c in runner.calls)


@pytest.mark.parametrize("fault", ["trust", "listener", "admission"])
def test_local_failed_gate_never_commits(tmp_path, monkeypatch, fault):
    manifest, value, units, runner, targets = local_deployment(tmp_path, monkeypatch)
    if fault == "listener": runner.entry_listening = False
    if fault == "admission": runner.admit = False
    def run(argv, timeout, identity):
        if fault == "trust" and argv[1] == "preflight": return manage.RunResult(1)
        return runner(argv, timeout, identity)
    with pytest.raises(manage.ManageError):
        manage.up_many(manifest, targets, apply=True, runner=run, unit_root=units)
    assert not manage._activation_record(Path(value["config_root"])).exists()
    if fault in {"trust", "listener"}:
        assert ("enable", "anvil-connect-connector-dashboard.service") not in runner.events


@pytest.mark.parametrize("retain_listener", [False, True])
def test_path_switch_stops_releases_then_replaces(tmp_path, monkeypatch, retain_listener):
    manifest, value, units, runner, targets = local_deployment(tmp_path, monkeypatch)
    manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    runner.active = True
    del value["connectors"][0]["local_tunnel"]
    if not retain_listener: del value["gateway"]["local_tunnel"]
    manifest.write_text(json.dumps(value))
    runner.events.clear()
    released = []
    monkeypatch.setattr(manage, "_reverse_ports_free", lambda _: released.append(len(runner.events)) or True)
    manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    stop = runner.events.index(("stop", "anvil-connect-connector-dashboard.service"))
    start = runner.events.index(("restart", "anvil-connect-connector-dashboard.service"))
    assert stop < released[0] < start
    assert ("snapshot", (True, False)) in runner.events[stop:start]


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


@pytest.mark.parametrize("mutation", ["unknown", "bool-count", "negative", "duplicate", "secret", "missing", "oversize"])
def test_closed_entry_status_rejects_non_native_fields(mutation):
    value = json.loads(_gateway_status())
    entry = {"path": "local", "listening": True, "reason": "entry_listening",
             "resources": [{"resource": "dashboard", "registrations": 1}]}
    value["entries"] = [entry]
    if mutation == "unknown": entry["path"] = "fallback"
    if mutation == "bool-count": entry["resources"][0]["registrations"] = True
    if mutation == "negative": entry["resources"][0]["registrations"] = -1
    if mutation == "duplicate": value["entries"].append(entry)
    if mutation == "secret": entry["authorization"] = "hidden"
    if mutation == "missing": del entry["resources"]
    if mutation == "oversize": entry["resources"] *= 65
    with pytest.raises(manage.ManageError, match="readiness response"):
        manage._closed_gateway_status(json.dumps(value).encode())


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


def test_failed_initial_local_activation_stops_started_services(tmp_path, monkeypatch):
    manifest, value, units, runner, targets = local_deployment(tmp_path, monkeypatch)
    runner.entry_listening = False
    with pytest.raises(manage.ManageError):
        manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    for unit in manage._units(manage.Target("gateway")):
        assert ("disable", unit) in runner.events
    assert not Path(value["config_root"]).exists()


@pytest.mark.parametrize("public", [False, True])
def test_gateway_only_change_preserves_unchanged_connector(tmp_path, monkeypatch, public):
    manifest, value, units, runner, targets = local_deployment(tmp_path, monkeypatch)
    if public:
        del value["connectors"][0]["local_tunnel"]
        manifest.write_text(json.dumps(value))
    manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    runner.active = True
    value["gateway"]["gateway"]["max_concurrent"] += 1
    manifest.write_text(json.dumps(value))
    runner.events.clear()
    commit = manage._write_activation_record
    def checked_commit(root, record):
        assert runner.registered
        commit(root, record)
    monkeypatch.setattr(manage, "_write_activation_record", checked_commit)
    probes = 0
    restarted = False
    def run(argv, timeout, identity):
        nonlocal probes, restarted
        if argv[1:] == ("restart", "anvil-connect-gateway.service"):
            restarted = True
            runner.registered = False
        if restarted and argv[1] == "admin":
            probes += 1
            if probes >= 3: runner.registered = True
        return runner(argv, timeout, identity)
    manage.up(manifest, manage.Target("gateway"), apply=True, runner=run, unit_root=units)
    assert probes >= 3
    assert ("restart", "anvil-connect-gateway.service") in runner.events
    assert ("restart", "anvil-connect-connector-dashboard.service") not in runner.events
    assert ("stop", "anvil-connect-connector-dashboard.service") not in runner.events


def test_local_status_reports_authoritative_per_resource_readiness(tmp_path, monkeypatch):
    manifest, value, units, runner, targets = local_deployment(tmp_path, monkeypatch)
    manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    result = manage.status(manifest, manage.Target("connector", "dashboard"), runner=runner)["targets"][0]["tunnel"]
    assert result["readiness"] == "ready" and len(result["entries"][0]["resources"]) == 2
    runner.registered = False
    result = manage.status(manifest, manage.Target("connector", "dashboard"), runner=runner)["targets"][0]["tunnel"]
    assert result["readiness"] == "not-ready" and result["state"] == "degraded"


def test_gateway_status_requires_admitted_resources_not_just_listener(tmp_path, monkeypatch):
    manifest, value, units, runner, targets = local_deployment(tmp_path, monkeypatch)
    manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    runner.registered = False
    tunnel = manage.status(manifest, manage.Target("gateway"), runner=runner)["targets"][0]["tunnel"]
    assert tunnel["readiness"] == "not-ready" and tunnel["state"] == "degraded"


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


def test_path_switch_checks_replacement_reverse_address_too(tmp_path, monkeypatch):
    manifest, value, units, runner, targets = local_deployment(tmp_path, monkeypatch)
    manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    runner.active = True
    old = value["connectors"][0]["resources"][0]["reverse_address"]
    value["connectors"][0]["resources"][0]["reverse_address"] = "127.0.0.1:17555"
    value["gateway"]["gateway"]["resources"][0]["tunnel_address"] = "127.0.0.1:17555"
    del value["connectors"][0]["local_tunnel"]
    manifest.write_text(json.dumps(value))
    addresses = []
    def free(connectors):
        selected = {r["reverse_address"] for c in connectors for r in c["resources"]}
        addresses.append(selected)
        return "127.0.0.1:17555" not in selected
    monkeypatch.setattr(manage, "_reverse_ports_free", free)
    runner.events.clear()
    with pytest.raises(manage.ManageError):
        manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    assert old in addresses[0] and any("127.0.0.1:17555" in selection for selection in addresses)
    assert ("restart", "anvil-connect-connector-dashboard.service") not in runner.events


def test_failed_local_activation_recovers_prior_trust_and_registrations(tmp_path, monkeypatch):
    manifest, value, units, runner, targets = local_deployment(tmp_path, monkeypatch)
    manage.up_many(manifest, targets, apply=True, runner=runner, unit_root=units)
    root = Path(value["config_root"])
    previous = (root / "gateway.json").read_bytes()
    receipt = manage._activation_record(root).read_bytes()
    runner.active = True
    value["gateway"]["local_tunnel"]["certificate_file"] += ".v2"
    value["connectors"][0]["local_tunnel"]["trust_file"] += ".v2"
    manifest.write_text(json.dumps(value))
    def run(argv, timeout, identity):
        if argv[1:] == ("restart", "anvil-connect-connector-dashboard.service"):
            gateway = json.loads((root / "gateway.json").read_text())
            runner.admit = not gateway["local_tunnel"]["certificate_file"].endswith(".v2")
        return runner(argv, timeout, identity)
    with pytest.raises(manage.ManageError, match="gateway did not become ready"):
        manage.up_many(manifest, targets, apply=True, runner=run, unit_root=units)
    assert (root / "gateway.json").read_bytes() == previous
    assert manage._activation_record(root).read_bytes() == receipt
    assert runner.registered
