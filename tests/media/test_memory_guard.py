import json
import math
from pathlib import Path
import subprocess
import sys
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


def test_valid_signal_and_atomic_replacement(tmp_path):
    from anvil_serving.media.memory_guard import signal_reason
    settings = policy(tmp_path)
    write_lease(settings)
    assert signal_reason(settings) == ""
    write_lease(settings, allow=False, reason="guard stopped")
    assert signal_reason(settings) == "guard stopped"
    assert not list(tmp_path.glob(".signal-*"))


def test_supervisor_anchors_deadline_before_spawn_and_overrides_inherited_metadata(tmp_path, monkeypatch):
    from anvil_serving.media import memory_guard
    settings = policy(tmp_path)
    write_lease(settings)
    settings.update(admission_seconds=600, admission_margin_seconds=30)
    clock, children = [10**12], []
    monkeypatch.setattr(memory_guard.time, "monotonic_ns", lambda: clock[0])
    monkeypatch.setenv(memory_guard.worker_runtime.ENVIRONMENT, "untrusted inherited metadata")
    def spawn(_command, **kwargs):
        value = json.loads(kwargs["env"][memory_guard.worker_runtime.ENVIRONMENT])
        assert value["startedNs"] == clock[0]
        clock[0] = value["deadlineNs"]
        child = SimpleNamespace(pid=123, poll=lambda: None, kill=lambda: children.append("killed"), wait=lambda **_: 0)
        return child
    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(memory_guard.os, "killpg", lambda *_: children.append("killed"), raising=False)
    assert memory_guard.supervise(settings, [sys.executable, "-c", "pass"]) == 74
    assert children == ["killed"]


def test_install_preview_does_not_touch_registry_or_spawn(tmp_path, monkeypatch, capsys):
    from anvil_serving.media import memory_guard
    settings = policy(tmp_path)
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("spawned guard"))
    assert memory_guard.main(["install", "--config", str(tmp_path / "guard.toml")]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert settings["sha256"]


def test_publisher_survives_bounded_replace_denial_without_refreshing_old_signal(tmp_path, monkeypatch, capsys):
    from anvil_serving.media import memory_guard
    settings = policy(tmp_path)
    write_lease(settings)
    previous = Path(settings["lease_path"]).read_bytes()
    original_writer, attempts = memory_guard.write_signal, []
    kernel = SimpleNamespace(CreateMutexW=lambda *_: 123, CloseHandle=lambda *_: None)
    monkeypatch.setattr(memory_guard.sys, "platform", "win32")
    monkeypatch.setattr(memory_guard.ctypes, "WinDLL", lambda *_a, **_k: kernel, raising=False)
    monkeypatch.setattr(memory_guard.ctypes, "set_last_error", lambda _: None, raising=False)
    monkeypatch.setattr(memory_guard.ctypes, "get_last_error", lambda: 0, raising=False)
    monkeypatch.setattr(memory_guard, "windows_memory", lambda: (20, 30))
    monkeypatch.setattr(memory_guard.time, "sleep", lambda _: None)
    def write(path, value):
        attempts.append(value)
        if len(attempts) < 3:
            assert Path(path).read_bytes() == previous
            raise PermissionError("reader still holds file")
        original_writer(path, value)
        Path(path).with_suffix(".stop").touch()
    monkeypatch.setattr(memory_guard, "write_signal", write)
    assert memory_guard.run_host(settings) == 0
    assert len(attempts) == 4 and attempts[2]["allow"]
    assert attempts[3]["reason"] == "guard stopped"
    assert capsys.readouterr().err.count("signal replacement denied") == 1


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
