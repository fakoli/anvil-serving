from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import stat

import pytest

from anvil_serving.connect import cli
from anvil_serving.connect.manage import RunResult
from anvil_serving.native_memory_gateway import lifecycle


def declaration() -> dict:
    return {
        "schema": lifecycle.SCHEMA,
        "deployment_id": "personal",
        "identity": {"user": "anm-personal", "uid": 62140, "group": "anm-personal", "gid": 62140},
        "runtime": {"source": "/private/runtime.pyz", "sha256": "1" * 64, "python_sha256": "2" * 64},
        "config": {"source": "/private/native-memory.json", "sha256": "3" * 64},
        "credentials": {"memory": "/private/memory.json", "memory_sha256": "4" * 64,
                        "introspection": "/private/introspection.secret", "introspection_sha256": "5" * 64},
        "authority": {"manager": "/opt/anvil-connect/bin/anvil-connect-ctl", "manager_sha256": "6" * 64,
                      "manifest": "/etc/anvil-connect/deployment.json", "resource": "pi-web"},
        "limits": {"memory_max_bytes": 268435456, "tasks_max": 64},
        "_python": "/usr/bin/python3.11",
        "_connect_root": "/etc/anvil-connect/rendered",
        "_authority_lock": "/etc/anvil-connect/.rendered.anvil-connect.lock",
        "_authority_state_directories": ("/data/connect/idp", "/data/connect/gateway"),
    }


def paths(tmp_path: Path) -> dict[str, Path]:
    unit_root = tmp_path / "units"
    artifact = tmp_path / "opt/personal"
    config = tmp_path / "etc/personal"
    runtime = tmp_path / "run/personal"
    state = tmp_path / "state/personal"
    for parent in (unit_root, artifact.parent, config.parent, state.parent):
        parent.mkdir(parents=True, exist_ok=True)
    return {
        "artifact_dir": artifact, "artifact": artifact / "gateway.pyz",
        "config_dir": config, "config": config / "native-memory.json",
        "memory_credential": config / "memory-key.credential",
        "introspection_credential": config / "introspection-secret.credential",
        "runtime_dir": runtime, "authority_dir": runtime / "authority",
        "authority": runtime / "authority/current.json",
        "state_dir": state, "receipt": state / "receipt.json",
        "service_unit": unit_root / "anvil-native-memory-personal.service",
        "authority_unit": unit_root / "anvil-native-memory-personal-authority.service",
        "timer_unit": unit_root / "anvil-native-memory-personal-authority.timer",
    }


def test_render_is_closed_three_unit_set_and_never_embeds_credentials(tmp_path):
    value = declaration()
    rendered = lifecycle.render_units(value, paths(tmp_path))
    assert set(rendered) == {
        "anvil-native-memory-personal.service",
        "anvil-native-memory-personal-authority.service",
        "anvil-native-memory-personal-authority.timer",
    }
    gateway = rendered["anvil-native-memory-personal.service"].decode()
    assert "LoadCredential=memory-key:" + str(paths(tmp_path)["memory_credential"]) in gateway
    assert "LoadCredential=introspection-secret:" + str(paths(tmp_path)["introspection_credential"]) in gateway
    assert "NoNewPrivileges=true" in gateway and "ProtectSystem=strict" in gateway
    assert "CapabilityBoundingSet=\n" in gateway and "MemoryMax=268435456" in gateway
    assert "AmbientCapabilities=\n" in gateway
    assert "MemorySwapMax=0" in gateway
    authority = rendered["anvil-native-memory-personal-authority.service"].decode()
    assert '"native-authority"' in authority and '"--reader-gid" "62140"' in authority
    assert "RuntimeDirectoryPreserve=yes" in authority
    assert "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER CAP_SETUID CAP_SETGID CAP_KILL\n" in authority
    assert "AmbientCapabilities=CAP_SETUID\n" in authority
    assert 'ReadWritePaths="' + str(paths(tmp_path)["authority_dir"]) + '" "/etc/anvil-connect/.rendered.anvil-connect.lock" "/data/connect/idp" "/data/connect/gateway"\n' in authority
    assert "secret-value" not in "".join(item.decode() for item in rendered.values())


def test_preview_performs_no_systemd_or_identity_mutation(monkeypatch, tmp_path):
    value = declaration()
    monkeypatch.setattr(lifecycle, "load_declaration", lambda _: (value, "a" * 64))
    monkeypatch.setattr(lifecycle.os, "geteuid", lambda: 0)
    calls = []
    result = lifecycle.operate("install", "/private/declaration.json", runner=lambda *args, **kwargs: calls.append(args))
    assert result["applied"] is False
    assert calls == []


@pytest.mark.parametrize("loaded", (False, True))
def test_timer_state_accepts_systemd_metadata_without_service_only_main_pid(loaded):
    fields = {
        "LoadState": "loaded" if loaded else "not-found", "FragmentPath": "", "DropInPaths": "",
        "ActiveState": "active" if loaded else "inactive", "SubState": "waiting" if loaded else "dead",
        "UnitFileState": "enabled" if loaded else "",
    }
    def run(argv, timeout=30, identity=None):
        assert "MainPID" not in argv[2]
        return RunResult(0 if loaded else 1, "".join(f"{key}={value}\n" for key, value in fields.items()).encode(), b"")
    assert lifecycle._state(run, "anvil-native-memory-personal-authority.timer") == fields


@pytest.mark.parametrize("missing", ("MainPID", "DropInPaths", "UnitFileState"))
def test_service_state_still_requires_complete_systemd_metadata(missing):
    fields = {
        "LoadState": "loaded", "FragmentPath": "/etc/systemd/system/owned.service", "DropInPaths": "",
        "ActiveState": "active", "SubState": "running", "UnitFileState": "enabled", "MainPID": "42",
    }
    del fields[missing]
    def run(argv, timeout=30, identity=None):
        return RunResult(0, "".join(f"{key}={value}\n" for key, value in fields.items()).encode(), b"")
    with pytest.raises(lifecycle.LifecycleError, match="managed unit state is invalid"):
        lifecycle._state(run, "anvil-native-memory-personal.service")


def test_python_allows_normal_0777_symlink_mode_but_pins_resolved_regular_target(monkeypatch):
    class Link:
        def resolve(self, strict=False):
            assert strict is True
            return Path("/usr/bin/python3.11")
        def lstat(self):
            return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0)
    seen = []
    monkeypatch.setattr(lifecycle, "_PYTHON_LINK", Link())
    monkeypatch.setattr(lifecycle, "_executable", lambda path, digest, maximum: seen.append((path, digest, maximum)))
    assert lifecycle._python("a" * 64) == "/usr/bin/python3.11"
    assert seen == [(Path("/usr/bin/python3.11"), "a" * 64, 64 * 1024 * 1024)]


def test_executable_reader_accepts_root_0755_and_rejects_digest_drift(monkeypatch):
    raw = b"executable"
    regular = SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=0, st_gid=0, st_nlink=1,
                              st_size=len(raw), st_dev=1, st_ino=2, st_mtime_ns=3)
    directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
    monkeypatch.setattr(Path, "lstat", lambda self: directory)
    monkeypatch.setattr(lifecycle.os, "open", lambda *args: 7)
    monkeypatch.setattr(lifecycle.os, "fstat", lambda fd: regular)
    chunks = iter((raw, b""))
    monkeypatch.setattr(lifecycle.os, "read", lambda fd, count: next(chunks))
    monkeypatch.setattr(lifecycle.os, "close", lambda fd: None)
    digest = lifecycle._sha(raw)
    assert lifecycle._executable("/usr/bin/tool", digest, 1024) == raw
    chunks = iter((raw, b""))
    with pytest.raises(lifecycle.LifecycleError, match="provenance"):
        lifecycle._executable("/usr/bin/tool", "a" * 64, 1024)


def test_remove_refuses_unit_drift_before_stopping_any_service(monkeypatch, tmp_path):
    value, owned = declaration(), paths(tmp_path)
    units = lifecycle.render_units(value, owned)
    for name, raw in units.items():
        target = lifecycle._unit_path(owned, name)
        target.write_bytes(raw)
    owned["service_unit"].write_text("foreign")
    monkeypatch.setattr(lifecycle, "_receipt", lambda _: {
        "schema": lifecycle.SCHEMA, "declaration_sha256": "a" * 64, "phase": "ready", "installed_at": 1})
    monkeypatch.setattr(lifecycle, "_identity", lambda _: (True, True))
    @contextmanager
    def unlocked():
        yield
    monkeypatch.setattr(lifecycle, "_lock", unlocked)
    calls = []
    with pytest.raises(lifecycle.LifecycleError, match="unit changed"):
        lifecycle._remove(value, "a" * 64, lambda argv, **kwargs: calls.append(argv), owned, True)
    assert calls == []


def test_remove_refuses_owned_file_drift_before_stopping_any_service(monkeypatch, tmp_path):
    value, owned = declaration(), paths(tmp_path)
    units = lifecycle.render_units(value, owned)
    for name, raw in units.items():
        lifecycle._unit_path(owned, name).write_bytes(raw)
    owned["artifact_dir"].mkdir()
    owned["artifact"].write_bytes(b"drift")
    monkeypatch.setattr(lifecycle, "_receipt", lambda _: {
        "schema": lifecycle.SCHEMA, "declaration_sha256": "a" * 64, "phase": "ready", "installed_at": 1})
    monkeypatch.setattr(lifecycle, "_identity", lambda _: (True, True))
    monkeypatch.setattr(lifecycle, "_state", lambda runner, name: {
        "LoadState": "loaded", "FragmentPath": str(lifecycle._unit_path(owned, name)), "DropInPaths": "",
        "ActiveState": "active", "SubState": "running", "MainPID": "42", "UnitFileState": "enabled"})
    monkeypatch.setattr(lifecycle, "_owned_digest", lambda *args, **kwargs: False)
    @contextmanager
    def unlocked():
        yield
    monkeypatch.setattr(lifecycle, "_lock", unlocked)
    calls = []
    with pytest.raises(lifecycle.LifecycleError, match="file changed"):
        lifecycle._remove(value, "a" * 64, lambda argv, **kwargs: calls.append(argv), owned, True)
    assert calls == []


@pytest.mark.parametrize("value", ("/safe/%n", "/safe/$TOKEN"))
def test_unit_arguments_reject_systemd_expansion(value):
    with pytest.raises(lifecycle.LifecycleError, match="unit argument"):
        lifecycle._quote(value)


def test_status_never_claims_installed_when_effective_policy_drifts(monkeypatch, tmp_path):
    value, owned = declaration(), paths(tmp_path)
    for name, raw in lifecycle.render_units(value, owned).items():
        lifecycle._unit_path(owned, name).write_bytes(raw)
    monkeypatch.setattr(lifecycle, "_state", lambda runner, name: {
        "LoadState": "loaded", "FragmentPath": str(lifecycle._unit_path(owned, name)), "DropInPaths": "",
        "ActiveState": "active", "SubState": "running", "MainPID": "42", "UnitFileState": "enabled"})
    monkeypatch.setattr(lifecycle, "_gateway_effective", lambda *args: False)
    monkeypatch.setattr(lifecycle, "_receipt", lambda _: {
        "schema": lifecycle.SCHEMA, "declaration_sha256": "a" * 64, "phase": "ready", "installed_at": 1})
    result = lifecycle._status(value, "a" * 64, None, owned)
    assert result["receipt_ready"] is True and result["ready"] is False and result["installed"] is False
    assert result["units"][owned["service_unit"].name]["effective"] is False


def test_status_never_claims_ready_when_gateway_is_failed(monkeypatch, tmp_path):
    value, owned = declaration(), paths(tmp_path)
    for name, raw in lifecycle.render_units(value, owned).items():
        lifecycle._unit_path(owned, name).write_bytes(raw)
    def state(runner, name):
        failed = name == owned["service_unit"].name
        return {
            "LoadState": "loaded", "FragmentPath": str(lifecycle._unit_path(owned, name)), "DropInPaths": "",
            "ActiveState": "failed" if failed else "active", "SubState": "failed" if failed else "running",
            "MainPID": "0" if failed else "42", "UnitFileState": "enabled"}
    monkeypatch.setattr(lifecycle, "_state", state)
    monkeypatch.setattr(lifecycle, "_gateway_effective", lambda *args: True)
    monkeypatch.setattr(lifecycle, "_receipt", lambda _: {
        "schema": lifecycle.SCHEMA, "declaration_sha256": "a" * 64, "phase": "ready", "installed_at": 1})
    monkeypatch.setattr(lifecycle, "_identity", lambda _: (True, True))
    monkeypatch.setattr(lifecycle, "_owned_digest", lambda *args, **kwargs: True)
    result = lifecycle._status(value, "a" * 64, None, owned)
    assert result["receipt_ready"] is True
    assert result["ready"] is False and result["installed"] is False
    assert result["units"][owned["service_unit"].name]["operational"] is False


@pytest.mark.parametrize("rollback_failure", (False, True))
@pytest.mark.parametrize("userdel_removes_group", (False, True))
def test_failed_start_rolls_back_only_files_and_identity_created_by_attempt(monkeypatch, tmp_path, rollback_failure, userdel_removes_group):
    value, owned = declaration(), paths(tmp_path)
    identity = {"user": False, "group": False}
    calls = []
    monkeypatch.setattr(lifecycle, "_protected", lambda path, maximum: b"runtime" if "runtime" in str(path) else b"config")
    monkeypatch.setattr(lifecycle, "_file", lambda path, digest, maximum: b"credential")
    monkeypatch.setattr(lifecycle, "_artifact", lambda path, digest, maximum: b"runtime")
    monkeypatch.setattr(lifecycle, "_identity", lambda _: (identity["user"], identity["group"]))
    monkeypatch.setattr(lifecycle.os, "chown", lambda *args: None)
    def mkdir(path, mode, gid=0):
        created = not path.exists()
        path.mkdir(parents=False, exist_ok=True)
        return created
    def write(path, raw, mode, gid=0):
        created = not path.exists()
        path.write_bytes(raw)
        return created
    monkeypatch.setattr(lifecycle, "_mkdir", mkdir)
    monkeypatch.setattr(lifecycle, "_write", write)
    monkeypatch.setattr(lifecycle, "_owned_regular", lambda *args, **kwargs: True)
    monkeypatch.setattr(lifecycle, "_directory", lambda *args, **kwargs: None)
    @contextmanager
    def unlocked():
        yield
    monkeypatch.setattr(lifecycle, "_lock", unlocked)
    monkeypatch.setattr(lifecycle, "_receipt", lambda path: None)
    monkeypatch.setattr(lifecycle, "_preflight_install", lambda *args: (False, False))
    @contextmanager
    def deployment_lock(root):
        yield
    monkeypatch.setattr(lifecycle.manage, "_deployment_lock", deployment_lock)
    monkeypatch.setattr(lifecycle, "_state", lambda *args: {
        "LoadState": "loaded", "FragmentPath": "owned", "DropInPaths": "", "ActiveState": "inactive",
        "SubState": "dead", "MainPID": "0", "UnitFileState": "disabled"})
    monkeypatch.setattr(lifecycle, "_no_processes", lambda uid: True)
    def save(path, digest, phase, installed_at):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(phase)
    monkeypatch.setattr(lifecycle, "_save_receipt", save)
    def run(argv, timeout=30, identity_arg=None):
        calls.append(tuple(argv))
        if argv[0] == "/usr/sbin/groupadd":
            identity["group"] = True
        elif argv[0] == "/usr/sbin/useradd":
            identity["user"] = True
        elif argv[0] == "/usr/sbin/userdel":
            identity["user"] = False
            if userdel_removes_group:
                identity["group"] = False
        elif argv[0] == "/usr/sbin/groupdel":
            assert identity["group"], "userdel already removed the group"
            identity["group"] = False
        if tuple(argv[:2]) == (lifecycle._SYSTEMCTL, "start") and argv[-1] == owned["authority_unit"].name:
            owned["authority_dir"].mkdir(parents=True)
            owned["authority"].write_bytes(b"authority")
        if tuple(argv[:3]) == (lifecycle._SYSTEMCTL, "enable", "--now") and argv[-1].endswith(".service"):
            return RunResult(1, b"", b"failed")
        if (rollback_failure and tuple(argv[:3]) == (lifecycle._SYSTEMCTL, "disable", "--now")
                and argv[-1].endswith(".service")):
            return RunResult(1, b"", b"rollback failed")
        return RunResult(0, b"", b"")
    expected = "rollback failed" if rollback_failure else "gateway failed"
    with pytest.raises(lifecycle.LifecycleError, match=expected):
        lifecycle._install(value, "a" * 64, run, owned, True)
    assert owned["receipt"].exists() is rollback_failure
    assert owned["service_unit"].exists() is rollback_failure
    assert identity == ({"user": True, "group": True} if rollback_failure else {"user": False, "group": False})
    assert (lifecycle._SYSTEMCTL, "disable", "--now", owned["service_unit"].name) in calls
    if not rollback_failure:
        assert (lifecycle._SYSTEMCTL, "stop", owned["authority_unit"].name) in calls
        assert not owned["runtime_dir"].exists()


def test_interrupted_install_requires_explicit_managed_removal(monkeypatch, tmp_path):
    value, owned = declaration(), paths(tmp_path)
    monkeypatch.setattr(lifecycle, "_receipt", lambda _: {
        "schema": lifecycle.SCHEMA, "declaration_sha256": "a" * 64, "phase": "installing", "installed_at": 1})
    @contextmanager
    def unlocked():
        yield
    monkeypatch.setattr(lifecycle, "_lock", unlocked)
    calls = []
    with pytest.raises(lifecycle.LifecycleError, match="managed removal"):
        lifecycle._install(value, "a" * 64, lambda *args: calls.append(args), owned, True)
    assert calls == []


@pytest.mark.parametrize("userdel_removes_group", (None, False, True))
def test_interrupted_removal_reconciles_only_quiescent_cached_unit_after_unlink(monkeypatch, tmp_path, userdel_removes_group):
    value, owned = declaration(), paths(tmp_path)
    identity = {"user": userdel_removes_group is not None, "group": userdel_removes_group is not None}
    monkeypatch.setattr(lifecycle, "_receipt", lambda _: {
        "schema": lifecycle.SCHEMA, "declaration_sha256": "a" * 64, "phase": "removing", "installed_at": 1})
    monkeypatch.setattr(lifecycle, "_identity", lambda _: (identity["user"], identity["group"]))
    monkeypatch.setattr(lifecycle, "_preflight_remove", lambda *args, **kwargs: None)
    monkeypatch.setattr(lifecycle, "_save_receipt", lambda *args, **kwargs: None)
    monkeypatch.setattr(lifecycle, "_remove_authority_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(lifecycle, "_finish_receipt", lambda *args, **kwargs: None)
    monkeypatch.setattr(lifecycle, "_no_processes", lambda uid: True)
    @contextmanager
    def unlocked():
        yield
    monkeypatch.setattr(lifecycle, "_lock", unlocked)
    calls = []
    def state(runner, name):
        target = lifecycle._unit_path(owned, name)
        return {
            "LoadState": "loaded", "FragmentPath": str(target), "DropInPaths": "",
            "ActiveState": "inactive", "SubState": "dead", "UnitFileState": "disabled",
            **({"MainPID": "0"} if name.endswith(".service") else {})}
    monkeypatch.setattr(lifecycle, "_state", state)
    def run(argv, timeout=30, identity_arg=None):
        calls.append(tuple(argv))
        if argv[0] == "/usr/sbin/userdel":
            identity["user"] = False
            if userdel_removes_group:
                identity["group"] = False
        elif argv[0] == "/usr/sbin/groupdel":
            assert identity["group"], "userdel already removed the group"
            identity["group"] = False
        return RunResult(0, b"", b"")
    result = lifecycle._remove(value, "a" * 64, run, owned, True)
    assert result["applied"] is True
    assert not any(item[1] in {"disable", "stop"} for item in calls)
    assert (lifecycle._SYSTEMCTL, "daemon-reload") in calls
    assert identity == {"user": False, "group": False}


@pytest.mark.parametrize("state,expected", (("vanished", True), ("missing-status", False),
    ("permission-denied", False), ("matching-uid", False), ("other-uid", True), ("malformed", False)))
def test_process_extinction_scan_ignores_only_confirmed_vanished_pids(monkeypatch, tmp_path, state, expected):
    process = tmp_path / "123"
    process.mkdir()
    status = process / "status"
    if state in {"matching-uid", "other-uid"}:
        uid = 62140 if state == "matching-uid" else 1000
        status.write_text(f"Name:\ttest\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    elif state == "malformed":
        status.write_text("Name:\ttest\nUid:\tunknown\n")
    elif state == "vanished":
        process.rmdir()
    original_iterdir, original_read = Path.iterdir, Path.read_text
    monkeypatch.setattr(lifecycle, "_PROC_ROOT", tmp_path)
    monkeypatch.setattr(Path, "iterdir", lambda self: iter([process]) if self == tmp_path else original_iterdir(self))
    def read(self, *args, **kwargs):
        if self == status and state == "permission-denied":
            raise PermissionError("unreadable PID")
        return original_read(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    assert lifecycle._no_processes(62140) is expected


@pytest.mark.parametrize("active_key", ("service_unit", "timer_unit"))
def test_interrupted_removal_rejects_active_cached_unit_after_unlink(monkeypatch, tmp_path, active_key):
    value, owned = declaration(), paths(tmp_path)
    monkeypatch.setattr(lifecycle, "_receipt", lambda _: {
        "schema": lifecycle.SCHEMA, "declaration_sha256": "a" * 64, "phase": "removing", "installed_at": 1})
    monkeypatch.setattr(lifecycle, "_state", lambda runner, name: {
        "LoadState": "loaded", "FragmentPath": str(lifecycle._unit_path(owned, name)), "DropInPaths": "",
        "ActiveState": "active" if name == owned[active_key].name else "inactive",
        "SubState": "running" if name == owned[active_key].name else "dead", "UnitFileState": "disabled",
        **({"MainPID": "42" if name == owned[active_key].name else "0"} if name.endswith(".service") else {})})
    @contextmanager
    def unlocked():
        yield
    monkeypatch.setattr(lifecycle, "_lock", unlocked)
    calls = []
    with pytest.raises(lifecycle.LifecycleError, match="unit state changed"):
        lifecycle._remove(value, "a" * 64, lambda argv, **kwargs: calls.append(tuple(argv)), owned, True)
    assert calls == []


def test_cli_projects_partial_failure_without_raw_error(monkeypatch):
    monkeypatch.setattr("anvil_serving.connect.manage.supported_platform", lambda: True)
    def fail(*args, **kwargs):
        raise lifecycle.LifecycleError("sensitive detail", may_have_executed=True)
    monkeypatch.setattr(lifecycle, "operate", fail)
    result = cli.dispatch(["native-memory-service", "install", "--declaration", "/private/d.json", "--confirm"])
    assert result.error.code == "connect_operation_partial"
    assert "sensitive detail" not in str(result.error)
