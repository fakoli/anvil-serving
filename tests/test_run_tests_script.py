import json
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
    monkeypatch.setattr(run_tests.tempfile, "mkdtemp", lambda **_kwargs: str(tmp_path / "pytest"))
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
