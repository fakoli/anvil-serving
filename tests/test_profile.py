"""Tests for the ``eval usage`` operator workflow."""


import pytest

from anvil_serving import profile
from anvil_serving import usage_logs


def test_usage_dry_run_resolves_paths_without_spawning(monkeypatch, tmp_path, capsys):
    logs = tmp_path / "logs"
    out = tmp_path / "out"
    logs.mkdir()
    out.mkdir()

    def boom(*args, **kwargs):
        raise AssertionError("dry-run must not spawn analyzers")

    monkeypatch.setattr(profile.subprocess, "call", boom)
    assert profile.main([
        "--logs-dir", str(logs),
        "--out-dir", str(out),
        "--dry-run",
    ]) == 0
    rendered = capsys.readouterr().out
    assert str(logs) in rendered
    assert str(out / "usage_aggregate.json") in rendered
    assert "deferred: log scan, artifact writes" in rendered


def test_usage_second_replace_failure_rolls_back_existing_pair(
    monkeypatch, tmp_path
):
    logs = tmp_path / "logs"
    out = tmp_path / "out"
    logs.mkdir()
    out.mkdir()
    aggregate = out / "usage_aggregate.json"
    roles = out / "role_split.json"
    aggregate.write_text('{"old": "aggregate"}\n', encoding="utf-8")
    roles.write_text('{"old": "roles"}\n', encoding="utf-8")

    def generate(argv, env, timeout):
        with open(argv[-1], "w", encoding="utf-8") as handle:
            handle.write('{"new": true}\n')
        return 0

    real_replace = profile.os.replace
    replacements = {"count": 0}

    def fail_second_install(source, target):
        if str(target) in {str(aggregate), str(roles)} and ".bak." not in str(source):
            replacements["count"] += 1
            if replacements["count"] == 2:
                raise OSError("second install denied")
        return real_replace(source, target)

    monkeypatch.setattr(profile.subprocess, "call", generate)
    monkeypatch.setattr(profile.os, "replace", fail_second_install)
    assert profile.main(["--logs-dir", str(logs), "--out-dir", str(out)]) == 1
    assert aggregate.read_text(encoding="utf-8") == '{"old": "aggregate"}\n'
    assert roles.read_text(encoding="utf-8") == '{"old": "roles"}\n'
    assert list(out.glob(".*.tmp")) == []


def test_usage_timeout_preserves_existing_pair(monkeypatch, tmp_path, capsys):
    logs = tmp_path / "logs"
    out = tmp_path / "out"
    logs.mkdir()
    out.mkdir()
    aggregate = out / "usage_aggregate.json"
    roles = out / "role_split.json"
    aggregate.write_text('{"old": "aggregate"}\n', encoding="utf-8")
    roles.write_text('{"old": "roles"}\n', encoding="utf-8")

    def timeout(*args, **kwargs):
        raise profile.subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(profile.subprocess, "call", timeout)
    assert profile.main([
        "--logs-dir", str(logs),
        "--out-dir", str(out),
        "--analysis-timeout", "1",
    ]) == 1
    assert "analysis timed out after 1.0s" in capsys.readouterr().err
    assert aggregate.read_text(encoding="utf-8") == '{"old": "aggregate"}\n'
    assert roles.read_text(encoding="utf-8") == '{"old": "roles"}\n'


def test_usage_log_discovery_rejects_oversized_file(monkeypatch, tmp_path):
    log = tmp_path / "session.jsonl"
    log.write_bytes(b"{}\n")
    monkeypatch.setattr(usage_logs, "MAX_LOG_FILE_BYTES", 2)
    with pytest.raises(ValueError, match="log file exceeds"):
        usage_logs.discover_jsonl_logs(tmp_path)
