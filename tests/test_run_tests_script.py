import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts import run_tests


def test_windows_runner_does_not_use_unix_ownership(monkeypatch):
    monkeypatch.setattr(run_tests.sys, "platform", "win32")
    monkeypatch.delattr(run_tests.os, "geteuid", raising=False)
    assert run_tests.untrusted_ancestry(run_tests.Path.cwd()) == []


@pytest.mark.parametrize(("outcome", "state", "code"), [
    (0, "passed", 0), (7, "failed", 7),
    (subprocess.TimeoutExpired("pytest", 1), "timed_out", 124),
    (KeyboardInterrupt(), "interrupted", 130),
])
def test_runner_receipt_preserves_exit_and_stops_interrupted_child(monkeypatch, tmp_path, outcome, state, code):
    seen = {}
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"state":"passed","run_id":"stale"}')
    monkeypatch.setattr(run_tests, "preflight", lambda *_args: [])
    monkeypatch.setattr(run_tests, "source_identity", lambda: {"head": "abc", "dirty": False})
    monkeypatch.setattr(run_tests, "create_test_temporary_directory", lambda: str(tmp_path / "pytest"))
    monkeypatch.setattr(run_tests.os, "killpg", lambda *_args: seen.update(killed=True), raising=False)

    class Process:
        pid = 123

        def __init__(self, argv, **kwargs):
            seen.update(argv=argv, **kwargs)
            assert json.loads(receipt.read_text())["state"] == "running"

        def wait(self, timeout=None):
            if isinstance(outcome, BaseException) and timeout is not None:
                raise outcome
            return outcome if isinstance(outcome, int) else -9

        def poll(self):
            return outcome if isinstance(outcome, int) else None

        def kill(self):
            seen["killed"] = True

    monkeypatch.setattr(run_tests.subprocess, "Popen", Process)
    assert run_tests.main(["--receipt", str(receipt), "--timeout", "1", "tests/test_models.py", "-q"]) == code
    result = json.loads(receipt.read_text())
    assert result["state"] == state and result["exit_code"] == code
    assert result["run_id"] != "stale" and result["finished_at"]
    assert result["source"] == {"head": "abc", "dirty": False}
    assert seen["argv"][-2:] == ["--basetemp", str(tmp_path / "pytest")]
    assert seen["env"]["PATH"].split(run_tests.os.pathsep)[0] == str(run_tests.Path(run_tests.sys.executable).parent)
    assert result["base_temp"] == str(tmp_path / "pytest")
    assert all(seen["env"][name] == result["base_temp"] for name in ("TMPDIR", "TEMP", "TMP"))
    assert seen.get("killed", False) is isinstance(outcome, BaseException)


def test_runner_fails_preflight_before_pytest_and_overwrites_stale_success(monkeypatch, tmp_path):
    monkeypatch.setattr(run_tests, "preflight", lambda *_args: ["untrusted directory"])
    monkeypatch.setattr(run_tests, "source_identity", lambda: {})
    monkeypatch.setattr(run_tests.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("spawned pytest"))
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"state":"passed"}')
    assert run_tests.main(["--receipt", str(receipt)]) == 2
    assert json.loads(receipt.read_text())["state"] == "preflight_failed"


def test_preflight_detects_missing_interpreter_dependency_and_wrong_source(monkeypatch, tmp_path):
    monkeypatch.setattr(run_tests, "untrusted_ancestry", lambda _cwd: ["untrusted"])
    monkeypatch.setattr(run_tests.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_tests.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(run_tests.sys, "path", list(run_tests.sys.path))
    problems = run_tests.preflight(tmp_path, {"PATH": ""})
    assert len(problems) == 4
    assert any("pytest is missing" in item for item in problems)
    assert any("resolves elsewhere" in item for item in problems)


def test_preflight_rejects_a_different_python_later_on_path(monkeypatch, tmp_path):
    selected = tmp_path / "selected" / "python3"
    selected.parent.mkdir()
    selected.write_text("")
    unrelated = tmp_path / "unrelated" / "python"
    unrelated.parent.mkdir()
    unrelated.write_text("")
    monkeypatch.setattr(run_tests.sys, "executable", str(selected))
    monkeypatch.setattr(run_tests.shutil, "which", lambda *_args, **_kwargs: str(unrelated))
    monkeypatch.setattr(run_tests.importlib.util, "find_spec", lambda _name: type("Spec", (), {"origin": str(tmp_path / "anvil_serving/__init__.py")})())
    monkeypatch.setattr(run_tests, "untrusted_ancestry", lambda _cwd: [])
    problems = run_tests.preflight(tmp_path, {"PATH": str(unrelated.parent)})
    assert any("matching python" in item for item in problems)


def test_runner_rejects_explicit_basetemp(capsys, tmp_path):
    assert run_tests.main(["--receipt", str(tmp_path / "r.json"), "tests/", "--basetemp=elsewhere"]) == 2
    assert "owns --basetemp" in capsys.readouterr().err


@pytest.mark.parametrize("error", [
    OSError("unreadable"), subprocess.CalledProcessError(1, "git"),
    subprocess.TimeoutExpired("git", 10),
])
def test_git_identity_failure_invalidates_receipt_before_lookup(monkeypatch, tmp_path, error):
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"state":"passed","run_id":"stale"}')
    monkeypatch.setattr(run_tests, "preflight", lambda *_args: [])
    monkeypatch.setattr(run_tests.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("spawned pytest"))

    def git(*_args, **kwargs):
        current = json.loads(receipt.read_text())
        assert current["state"] == "preflight" and current["run_id"] != "stale"
        assert kwargs["timeout"] == 10
        raise error

    monkeypatch.setattr(run_tests.subprocess, "check_output", git)
    assert run_tests.main(["--receipt", str(receipt)]) == 2
    result = json.loads(receipt.read_text())
    assert result["state"] == "preflight_failed"
    assert result["source"] is None and result["finished_at"]
    assert "Git source identity" in result["problems"][0]


@pytest.mark.parametrize("after", [
    {"head": "new", "dirty": False},
    {"head": "old", "dirty": True},
    ValueError("cannot establish Git source identity"),
])
def test_runner_rejects_changed_or_unreadable_end_identity(monkeypatch, tmp_path, after):
    identities = iter([{"head": "old", "dirty": False}, after])

    def identity():
        result = next(identities)
        if isinstance(result, Exception):
            raise result
        return result

    class Process:
        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(run_tests, "source_identity", identity)
    monkeypatch.setattr(run_tests, "preflight", lambda *_args: [])
    monkeypatch.setattr(run_tests.subprocess, "Popen", lambda *_args, **_kwargs: Process())
    receipt = tmp_path / "receipt.json"
    assert run_tests.main(["--receipt", str(receipt)]) == 2
    result = json.loads(receipt.read_text())
    assert result["state"] == ("source_unverified" if isinstance(after, Exception) else "source_changed")
    assert result["exit_code"] == 2 and result["finished_at"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX native custody")
@pytest.mark.parametrize("unsuitable", ["sticky", "git"])
@pytest.mark.parametrize("fallback", ["runtime", "home"])
def test_temporary_root_falls_back_without_changing_existing_parent(monkeypatch, tmp_path, unsuitable, fallback):
    from anvil_serving.propagation_native_acceptance import _custody
    parent = tmp_path / "system-temp"
    parent.mkdir(mode=0o700)
    if unsuitable == "sticky":
        parent.chmod(0o1777)
    else:
        (parent / ".git").write_text("preserve marker")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    runtime = tmp_path / "runtime"
    if fallback == "runtime":
        runtime.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    before = parent.stat().st_mode
    monkeypatch.setattr(run_tests.tempfile, "gettempdir", lambda: str(parent))
    monkeypatch.setattr(run_tests.Path, "home", lambda: home)
    first = Path(run_tests.create_test_temporary_directory())
    second = Path(run_tests.create_test_temporary_directory())
    try:
        assert first != second and first.parent == second.parent == (runtime if fallback == "runtime" else home)
        assert _custody(first, directory=True) == first
        assert first.stat().st_mode & 0o777 == 0o700
        assert parent.stat().st_mode == before
        assert list(parent.iterdir()) == ([parent / ".git"] if unsuitable == "git" else [])
        if unsuitable == "git":
            assert (parent / ".git").read_text() == "preserve marker"
    finally:
        shutil.rmtree(first)
        shutil.rmtree(second)


@pytest.mark.skipif(os.name != "posix", reason="POSIX native custody")
def test_temporary_root_recovers_from_discovery_error_and_symlink_loop(monkeypatch, tmp_path):
    def unavailable():
        raise OSError("system temp unavailable")

    runtime = tmp_path / "runtime"
    runtime.symlink_to(runtime)
    monkeypatch.setattr(run_tests.tempfile, "gettempdir", unavailable)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(run_tests.Path, "home", lambda: tmp_path)
    directory = Path(run_tests.create_test_temporary_directory())
    try:
        assert directory.parent == tmp_path
        assert runtime.is_symlink()
    finally:
        shutil.rmtree(directory)


@pytest.mark.skipif(os.name != "posix", reason="POSIX native custody")
def test_temporary_root_preserves_suitable_configured_parent(monkeypatch, tmp_path):
    monkeypatch.setattr(run_tests.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(run_tests.Path, "home", lambda: pytest.fail("unnecessary fallback"))
    directory = Path(run_tests.create_test_temporary_directory())
    try:
        assert directory.parent == tmp_path
    finally:
        shutil.rmtree(directory)


@pytest.mark.skipif(os.name != "posix", reason="POSIX native custody")
def test_temporary_root_refuses_git_ancestry_in_both_candidates(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    parent = tmp_path / "system-temp"
    parent.mkdir(mode=0o700)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setattr(run_tests.tempfile, "gettempdir", lambda: str(parent))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(parent))
    monkeypatch.setattr(run_tests.Path, "home", lambda: home)
    with pytest.raises(ValueError, match="set TMPDIR.*outside Git"):
        run_tests.create_test_temporary_directory()
    assert list(parent.iterdir()) == list(home.iterdir()) == []


def test_no_suitable_temporary_root_fails_before_pytest(monkeypatch, tmp_path):
    monkeypatch.setattr(run_tests, "preflight", lambda *_: [])
    monkeypatch.setattr(run_tests, "source_identity", lambda: {})
    def refused():
        raise ValueError("no suitable test temporary root")
    monkeypatch.setattr(run_tests, "create_test_temporary_directory", refused)
    monkeypatch.setattr(run_tests.subprocess, "Popen", lambda *_a, **_k: pytest.fail("spawned pytest"))
    receipt = tmp_path / "receipt.json"
    assert run_tests.main(["--receipt", str(receipt)]) == 2
    assert json.loads(receipt.read_text())["state"] == "preflight_failed"


def test_preflight_checks_temporary_root_and_cleans_only_owned_directory(monkeypatch, tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    owned = parent / "owned"
    owned.mkdir()
    retained = parent / "retained"
    retained.write_text("keep")
    monkeypatch.setattr(run_tests, "preflight", lambda *_: [])
    monkeypatch.setattr(run_tests, "source_identity", lambda: {})
    monkeypatch.setattr(run_tests, "create_test_temporary_directory", lambda: str(owned))
    monkeypatch.setattr(run_tests.subprocess, "Popen", lambda *_a, **_k: pytest.fail("spawned pytest"))
    receipt = tmp_path / "receipt.json"
    assert run_tests.main(["--receipt", str(receipt), "--preflight"]) == 0
    assert json.loads(receipt.read_text())["base_temp"] == str(owned)
    assert not owned.exists() and retained.read_text() == "keep"


def test_windows_temporary_root_uses_platform_temp_without_posix_custody(monkeypatch, tmp_path):
    monkeypatch.setattr(run_tests.sys, "platform", "win32")
    monkeypatch.delattr(run_tests.os, "geteuid", raising=False)
    monkeypatch.setattr(run_tests.tempfile, "mkdtemp", lambda **_: str(tmp_path))
    assert run_tests.create_test_temporary_directory() == str(tmp_path)
