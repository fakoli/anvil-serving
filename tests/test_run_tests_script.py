import os
from pathlib import Path
import shutil

import pytest

from scripts import run_tests


def test_windows_runner_does_not_use_unix_ownership(monkeypatch):
    monkeypatch.setattr(run_tests.sys, "platform", "win32")
    monkeypatch.delattr(run_tests.os, "geteuid", raising=False)
    assert run_tests.untrusted_ancestry(run_tests.Path.cwd()) == []


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


def test_windows_temporary_root_uses_platform_temp_without_posix_custody(monkeypatch, tmp_path):
    monkeypatch.setattr(run_tests.sys, "platform", "win32")
    monkeypatch.delattr(run_tests.os, "geteuid", raising=False)
    monkeypatch.setattr(run_tests.tempfile, "mkdtemp", lambda **_: str(tmp_path))
    assert run_tests.create_test_temporary_directory() == str(tmp_path)


def test_plan_launches_partitions_files_and_isolates_junit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    expected = {"tests/test_a.py", "tests/test_b.py", "tests/c/test_d.py", "tests/c/test_e.py", "tests/test_f.py"}
    for name in expected:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")
    base = tmp_path / "base"
    base.mkdir()
    launches = run_tests.plan_launches(["tests", "-q", "--junitxml=out.xml"], 3, False, base)
    assert len(launches) == 3
    seen = [item for command, _env, _log in launches for item in command if item.endswith(".py")]
    assert sorted(seen) == sorted(expected)
    junit = [item for command, _env, _log in launches for item in command if item.startswith("--junitxml=")]
    assert len(set(junit)) == 3 and all(item.startswith(f"--junitxml={base}") for item in junit)
    assert len({env["TMPDIR"] for _command, env, _log in launches}) == 3
