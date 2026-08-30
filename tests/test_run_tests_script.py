import subprocess

from scripts import run_tests


def test_runner_uses_unique_base_temp_and_preserves_pytest_exit(monkeypatch):
    seen = {}
    monkeypatch.setattr(run_tests.tempfile, "mkdtemp", lambda **_kwargs: "C:/temp/unique")

    def run(argv):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(
        run_tests.subprocess,
        "run",
        run,
    )
    removed = []
    monkeypatch.setattr(
        run_tests.shutil,
        "rmtree",
        lambda path, **kwargs: removed.append((path, kwargs)),
    )

    assert run_tests.main(["tests/test_models.py", "-q"]) == 7
    assert seen["argv"][-2:] == ["--basetemp", "C:/temp/unique"]
    assert removed == [("C:/temp/unique", {"ignore_errors": True})]


def test_runner_rejects_explicit_basetemp(capsys):
    assert run_tests.main(["tests/", "--basetemp=elsewhere"]) == 2
    assert "owns --basetemp" in capsys.readouterr().err
