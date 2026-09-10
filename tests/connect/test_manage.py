from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import re
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
    value["service_user"] = service_account()
    for section, name in (("gateway", "gateway.env"),):
        value["environment_files"][section] = str(tmp_path / name)
    for name in value["environment_files"]["connectors"]:
        value["environment_files"]["connectors"][name] = str(tmp_path / (name + ".connector.env"))
    for name in value["environment_files"]["clients"]:
        value["environment_files"]["clients"][name] = str(tmp_path / (name + ".client.env"))
    for env in [value["environment_files"]["gateway"], *value["environment_files"]["connectors"].values(), *value["environment_files"]["clients"].values()]:
        Path(env).write_text("DECLARED_ONLY=1\n", encoding="utf-8")
        Path(env).chmod(0o600)
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
    failing = SyntheticRunner(fail_start="anvil-connect-caddy.service", active=True); failing.unit_root = units
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


def test_bounded_run_kills_grandchild_holding_pipe() -> None:
    import sys
    started = __import__("time").monotonic()
    script = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); sys.exit(0)"
    with pytest.raises(manage.ManageError, match="exceeded"):
        manage._bounded_run((sys.executable, "-c", script), 0.1)
    assert __import__("time").monotonic() - started < 2


def test_environment_file_requires_exact_owner_only_mode_before_activation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, value, _ = deployment(tmp_path, monkeypatch)
    Path(value["environment_files"]["gateway"]).chmod(0o640)
    runner = SyntheticRunner()
    with pytest.raises(manage.ManageError, match="EnvironmentFile"):
        manage.up(manifest, manage.Target("gateway"), apply=True, runner=runner, unit_root=tmp_path)
    assert runner.calls == []


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
        value["service_user"] = service.pw_name
        for path in [value["environment_files"]["gateway"], *value["environment_files"]["connectors"].values(), *value["environment_files"]["clients"].values()]:
            os.chown(path, service.pw_uid, service.pw_gid)
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


def test_service_identity_rejects_root_and_group_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {"service_user": "svc"}
    monkeypatch.setattr(manage.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=0, pw_gid=10))
    monkeypatch.setattr(manage.grp, "getgrnam", lambda _: SimpleNamespace(gr_gid=10))
    with pytest.raises(manage.ManageError, match="unsafe"):
        manage._service_identity(data)
    monkeypatch.setattr(manage.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=10, pw_gid=11))
    monkeypatch.setattr(manage.grp, "getgrnam", lambda _: SimpleNamespace(gr_gid=12))
    with pytest.raises(manage.ManageError, match="unsafe"):
        manage._service_identity(data)


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
