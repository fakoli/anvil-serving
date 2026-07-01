"""Tests for the anvil-serving CLI dispatch — in particular the early
Python-version guard (`anvil_serving.cli._check_python_version`).
"""
from anvil_serving import cli


def test_python_version_guard_blocks_old_interpreter():
    assert cli._check_python_version((3, 10, 0)) == (
        "anvil-serving needs Python >=3.11; you have 3.10"
    )


def test_python_version_guard_blocks_even_older_interpreter():
    assert cli._check_python_version((2, 7, 18)) == (
        "anvil-serving needs Python >=3.11; you have 2.7"
    )


def test_python_version_guard_allows_supported_interpreter():
    assert cli._check_python_version((3, 11, 0)) is None
    assert cli._check_python_version((3, 13, 0)) is None


def test_python_version_guard_blocks_main_under_simulated_old_interpreter(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "version_info", (3, 9, 0))
    rc = cli.main(["--help"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "anvil-serving needs Python >=3.11; you have 3.9" in captured.err
