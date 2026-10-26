import json
import math
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest


def policy(tmp_path):
    from anvil_serving.media.memory_guard import load_policy
    path = tmp_path / "guard.toml"
    path.write_text('schema = "anvil-serving.media-memory-guard/v1"\nlease_path = "' + (tmp_path / "lease.json").as_posix() + '"\n')
    return load_policy(path)


def write_lease(settings, **changes):
    from anvil_serving.media.memory_guard import write_signal
    value = {"schema": "anvil-serving.host-memory-signal/v1", "policy_sha256": settings["sha256"],
             "sampled_at": time.time(), "allow": True, "reason": "", "available_gib": 20,
             "commit_free_gib": 30, "low_seconds": 0}
    value.update(changes)
    if any(isinstance(item, float) and not math.isfinite(item) for item in value.values()):
        Path(settings["lease_path"]).write_text(json.dumps(value))
    else:
        write_signal(settings["lease_path"], value)


def test_pressure_boundaries_match_the_verified_windows_trial_guard(tmp_path):
    from anvil_serving.media.memory_guard import pressure_reason
    settings = policy(tmp_path)
    assert pressure_reason(12, 30, 0, settings) == ""
    assert pressure_reason(7, 30, 10, settings) == ""
    assert pressure_reason(12, 16, 0, settings) == ""
    assert pressure_reason(3, 30, 0, settings)
    assert pressure_reason(12, 15, 0, settings)
    assert pressure_reason(7, 30, 15, settings)


@pytest.mark.parametrize("changes", [
    {"allow": False, "reason": "host memory pressure"},
    {"sampled_at": 0}, {"sampled_at": float("nan")},
    {"sampled_at": time.time() + 3600}, {"policy_sha256": "other"},
    {"available_gib": 0}, {"commit_free_gib": float("nan")},
    {"allow": "true"},
])
def test_unsafe_stale_or_wrong_policy_signal_fails_closed(tmp_path, changes):
    from anvil_serving.media.memory_guard import signal_reason
    settings = policy(tmp_path)
    write_lease(settings, **changes)
    assert signal_reason(settings)


def test_valid_signal_and_atomic_replacement(tmp_path):
    from anvil_serving.media.memory_guard import signal_reason
    settings = policy(tmp_path)
    write_lease(settings)
    assert signal_reason(settings) == ""
    write_lease(settings, allow=False, reason="guard stopped")
    assert signal_reason(settings) == "guard stopped"
    assert not list(tmp_path.glob(".signal-*"))


def test_signal_writer_refuses_non_json_counter_values(tmp_path):
    from anvil_serving.media.memory_guard import write_signal
    with pytest.raises(ValueError):
        write_signal(tmp_path / "invalid.json", {"available_gib": float("nan")})
    assert not list(tmp_path.glob(".signal-*"))


def test_supervisor_refuses_start_without_a_fresh_signal(tmp_path, monkeypatch):
    from anvil_serving.media.memory_guard import supervise
    settings = policy(tmp_path)
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("started unsafe worker"))
    assert supervise(settings, [sys.executable, "-c", "pass"]) == 74


def test_signal_loss_terminates_only_the_supervised_child(tmp_path, monkeypatch):
    from anvil_serving.media.memory_guard import supervise
    settings = policy(tmp_path)
    write_lease(settings)
    processes = []
    spawn = subprocess.Popen
    def recording_spawn(*args, **kwargs):
        child = spawn(*args, **kwargs)
        processes.append(child)
        return child
    monkeypatch.setattr(subprocess, "Popen", recording_spawn)
    def lose_signal():
        while not processes:
            time.sleep(0.01)
        write_lease(settings, allow=False, reason="host memory pressure")
    updater = threading.Thread(target=lose_signal)
    updater.start()
    try:
        assert supervise(settings, [sys.executable, "-c", "import time; time.sleep(30)"], poll_seconds=0.02) == 74
        assert len(processes) == 1 and processes[0].poll() is not None
    finally:
        for child in processes:
            if child.poll() is None:
                child.kill()
                child.wait()
        updater.join(2)


def test_policy_rejects_unknown_fields_and_invalid_threshold_order(tmp_path):
    from anvil_serving.media.memory_guard import load_policy
    for extra in ('unknown = 1', 'critical_available_gib = 9\nsoft_available_gib = 8'):
        path = tmp_path / "invalid.toml"
        path.write_text('schema = "anvil-serving.media-memory-guard/v1"\nlease_path = "x"\n' + extra)
        with pytest.raises(ValueError):
            load_policy(path)


def test_install_preview_does_not_touch_registry_or_spawn(tmp_path, monkeypatch, capsys):
    from anvil_serving.media import memory_guard
    settings = policy(tmp_path)
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("spawned guard"))
    assert memory_guard.main(["install", "--config", str(tmp_path / "guard.toml")]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert settings["sha256"]


def test_guard_mutex_is_global_and_duplicate_does_not_replace_signal(tmp_path, monkeypatch):
    from anvil_serving.media import memory_guard
    settings = policy(tmp_path)
    write_lease(settings)
    before = Path(settings["lease_path"]).read_bytes()
    names = []
    def create(_security, _owner, name):
        names.append(name)
        return 123
    kernel = SimpleNamespace(CreateMutexW=create, CloseHandle=lambda _handle: None)
    monkeypatch.setattr(memory_guard.sys, "platform", "win32")
    monkeypatch.setattr(memory_guard.ctypes, "WinDLL", lambda *_a, **_k: kernel, raising=False)
    monkeypatch.setattr(memory_guard.ctypes, "set_last_error", lambda _code: None, raising=False)
    monkeypatch.setattr(memory_guard.ctypes, "get_last_error", lambda: 183, raising=False)
    assert memory_guard.run_host(settings) == 0
    assert names[0].startswith("Global\\AnvilMediaMemoryGuard-")
    assert Path(settings["lease_path"]).read_bytes() == before


@pytest.mark.parametrize("stopped_signal", [False, True])
def test_install_removes_foreign_explicit_permissions_before_start(tmp_path, monkeypatch, stopped_signal):
    from anvil_serving.media import memory_guard
    home = tmp_path / "home"
    parent = home / ".anvil-serving/guard"
    parent.mkdir(parents=True)
    config = tmp_path / "guard.toml"
    config.write_text('schema = "anvil-serving.media-memory-guard/v1"\nlease_path = "' + (parent / "lease.json").as_posix() + '"\n')
    settings = memory_guard.load_policy(config)
    executable = home / "client/python.exe"
    executable.parent.mkdir()
    executable.with_name("pythonw.exe").touch()
    (parent / ".anvil-media-memory-guard").write_text("owned guard")
    outsider = {"directory": True, "file": True}
    owners = {str(parent): "foreign", str(parent / ".anvil-media-memory-guard"): "foreign"}
    commands = []
    def run(argv, **kwargs):
        commands.append(argv)
        if argv[0] == "whoami":
            return subprocess.CompletedProcess(argv, 0, '"owner","S-1-5-21-123"\n', '')
        if argv[0] == "icacls" and "/setowner" in argv:
            owners[argv[1]] = argv[argv.index("/setowner") + 1]
        if argv[0] == "icacls" and "/reset" in argv and argv[1] == str(parent):
            outsider["directory"] = False
        if argv[0] == "icacls" and "/reset" in argv and argv[1] == str(parent / ".anvil-media-memory-guard"):
            outsider["file"] = False
        return subprocess.CompletedProcess(argv, 0, '', '')
    def spawn(*_args, **_kwargs):
        assert not any(outsider.values()), "foreign explicit ACE survives /grant:r"
        assert set(owners.values()) == {"*S-1-5-21-123"}, "foreign owner retains WRITE_DAC"
        write_lease(settings, publisher_pid=77, allow=not stopped_signal,
                    reason="guard stopped" if stopped_signal else "",
                    sampled_at=time.time() - .1 if stopped_signal else time.time())
        return SimpleNamespace(poll=lambda: None)
    class Key:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
    def missing(*_args): raise FileNotFoundError
    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace(
        HKEY_CURRENT_USER=1, REG_SZ=1, CreateKey=lambda *_args: Key(),
        QueryValueEx=missing, SetValueEx=lambda *_args: None,
    ))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(memory_guard.sys, "platform", "win32")
    monkeypatch.setattr(memory_guard.sys, "executable", str(executable))
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    monkeypatch.setattr(memory_guard.time, "sleep", lambda _seconds: write_lease(settings, publisher_pid=77))
    result = memory_guard.install(settings, config)
    assert result["signal_fresh"] is True
    assert result["safe"] is True
    reset = next(i for i, args in enumerate(commands) if "/reset" in args)
    grant = next(i for i, args in enumerate(commands) if "/grant:r" in args)
    assert reset < grant
