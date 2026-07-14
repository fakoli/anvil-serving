"""Tests for the ``eval usage`` operator workflow."""

import os

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


def test_usage_propagates_child_failure_and_never_prints_success(
    monkeypatch, tmp_path, capsys
):
    logs = tmp_path / "logs"
    out = tmp_path / "out"
    logs.mkdir()
    out.mkdir()
    return_codes = iter([7, 0])
    monkeypatch.setattr(
        profile.subprocess, "call", lambda *args, **kwargs: next(return_codes)
    )

    assert profile.main([
        "--logs-dir", str(logs),
        "--out-dir", str(out),
    ]) == 1
    captured = capsys.readouterr()
    assert "analysis failed (aggregate=7, role-split=0)" in captured.err
    assert "existing outputs were preserved" in captured.err
    assert "wrote " not in captured.out


def test_usage_passes_resolved_log_root_to_both_children(monkeypatch, tmp_path):
    logs = tmp_path / "logs"
    out = tmp_path / "out"
    logs.mkdir()
    out.mkdir()
    calls = []

    def record(argv, env, timeout):
        calls.append((argv, env))
        with open(argv[-1], "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        return 0

    monkeypatch.setattr(profile.subprocess, "call", record)
    assert profile.main([
        "--logs-dir", str(logs),
        "--out-dir", str(out),
    ]) == 0
    assert len(calls) == 2
    assert all(call[1]["ANVIL_CLAUDE_LOGS"] == str(logs) for call in calls)
    assert os.path.dirname(calls[0][0][-1]) == str(out)
    assert os.path.dirname(calls[1][0][-1]) == str(out)
    assert (out / "usage_aggregate.json").is_file()
    assert (out / "role_split.json").is_file()


def test_usage_child_failure_preserves_existing_pair_and_cleans_staging(
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
    calls = {"count": 0}

    def partial(argv, env, timeout):
        calls["count"] += 1
        with open(argv[-1], "w", encoding="utf-8") as handle:
            handle.write('{"new": true}\n')
        return 0 if calls["count"] == 1 else 9

    monkeypatch.setattr(profile.subprocess, "call", partial)
    assert profile.main([
        "--logs-dir", str(logs),
        "--out-dir", str(out),
    ]) == 1
    assert aggregate.read_text(encoding="utf-8") == '{"old": "aggregate"}\n'
    assert roles.read_text(encoding="utf-8") == '{"old": "roles"}\n'
    assert list(out.glob(".*.tmp")) == []


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


def test_usage_log_parser_rejects_oversized_line(monkeypatch, tmp_path):
    log = tmp_path / "session.jsonl"
    log.write_bytes(b'{"large":"value"}\n')
    monkeypatch.setattr(usage_logs, "MAX_LOG_LINE_BYTES", 8)
    with pytest.raises(ValueError, match="log line exceeds"):
        list(usage_logs.iter_json_objects(log))


def test_usage_log_discovery_fails_closed_on_unreadable_directory(
    monkeypatch, tmp_path
):
    def unreadable(_root, *, followlinks, onerror):
        onerror(PermissionError("private logs"))
        return iter(())

    monkeypatch.setattr(usage_logs.os, "walk", unreadable)
    with pytest.raises(OSError, match="cannot scan log directory"):
        usage_logs.discover_jsonl_logs(tmp_path)


def test_usage_backup_failure_cleans_first_temporary_backup(monkeypatch, tmp_path):
    aggregate = tmp_path / "usage_aggregate.json"
    roles = tmp_path / "role_split.json"
    staged_aggregate = tmp_path / "aggregate.tmp"
    staged_roles = tmp_path / "roles.tmp"
    aggregate.write_text('{"old": "aggregate"}\n', encoding="utf-8")
    roles.write_text('{"old": "roles"}\n', encoding="utf-8")
    staged_aggregate.write_text('{"new": "aggregate"}\n', encoding="utf-8")
    staged_roles.write_text('{"new": "roles"}\n', encoding="utf-8")
    real_copy = profile.shutil.copy2
    copies = {"count": 0}

    def fail_second_backup(source, target):
        copies["count"] += 1
        if copies["count"] == 2:
            raise OSError("backup denied")
        return real_copy(source, target)

    monkeypatch.setattr(profile.shutil, "copy2", fail_second_backup)
    with pytest.raises(OSError, match="backup denied"):
        profile._commit_pair(
            staged_aggregate, aggregate, staged_roles, roles
        )
    assert aggregate.read_text(encoding="utf-8") == '{"old": "aggregate"}\n'
    assert roles.read_text(encoding="utf-8") == '{"old": "roles"}\n'
    assert list(tmp_path.glob(".*.tmp")) == []
