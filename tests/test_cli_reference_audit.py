from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_cli_references.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("audit_cli_references", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit = _load_module()


def _run(*args: str, root: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fixture_scope_is_stable_excludes_history_and_discovers_all_skill_roots():
    result = audit.scan(ROOT, "fixtures")
    assert result.files == tuple(sorted(result.files))
    assert result.skill_roots == tuple(sorted(audit.SKILL_ROOT_LABELS))
    assert not any("docs/findings" in path for path in result.files)
    assert not result.violations
    assert audit.inventory_matches(ROOT, "fixtures", audit.inventory_record(result))


def test_fixture_scope_classifies_active_legacy_invocation_as_violation(tmp_path: Path):
    fixture = ROOT / "tests" / "fixtures" / "cli_reference_audit"
    target = tmp_path / "tests" / "fixtures" / "cli_reference_audit"
    shutil.copytree(fixture, target)
    stale = target / "input" / "skills" / "voice" / "SKILL.md"
    stale.write_text("Run `anvil-serving voice start`.\n", encoding="utf-8")

    result = _run("--scope", "fixtures", "--check", "--json", root=tmp_path)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["violations"][0]["name"] == "voice-start-stop"
    assert payload["violations"][0]["category"] == "skills"


def test_check_fails_closed_on_stale_inventory(tmp_path: Path):
    fixture = ROOT / "tests" / "fixtures" / "cli_reference_audit"
    target = tmp_path / "tests" / "fixtures" / "cli_reference_audit"
    shutil.copytree(fixture, target)
    expected = target / "expected.json"
    value = json.loads(expected.read_text(encoding="utf-8"))
    value["record"]["files_scanned"] += 1
    expected.write_text(json.dumps(value), encoding="utf-8")

    result = _run("--scope", "fixtures", "--check", "--json", root=tmp_path)
    assert result.returncode == 1
    assert json.loads(result.stdout)["inventory_match"] is False


def test_changelog_unreleased_is_active_but_released_sections_are_historical(tmp_path: Path):
    root = tmp_path
    input_root = root / audit.FIXTURE_REL / "input"
    input_root.mkdir(parents=True)
    (input_root / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "### Changed\n\n"
        "- Run `anvil-serving deploy`.\n\n"
        "## [0.1.0] - 2025-01-01\n\n"
        "### Added\n\n"
        "- Run `anvil-serving score`.\n",
        encoding="utf-8",
    )

    result = audit.scan(root, "fixtures")

    assert [(hit.name, hit.allowed) for hit in result.hits if hit.kind == "legacy"] == [
        ("deploy", False),
        ("score", True),
    ]


def test_bounded_reader_rejects_oversized_text(tmp_path: Path):
    path = tmp_path / "large.md"
    path.write_bytes(b"x" * 9)

    with pytest.raises(ValueError, match="exceeds 8 byte"):
        audit._read_text(path, max_bytes=8)


def test_git_index_lookup_has_a_timeout(monkeypatch, tmp_path: Path):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == audit.GIT_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(audit.subprocess, "run", timeout)

    with pytest.raises(subprocess.TimeoutExpired):
        audit._tracked_paths(tmp_path)


def test_atomic_write_preserves_original_on_replace_failure(monkeypatch, tmp_path: Path):
    path = tmp_path / "generated.json"
    path.write_text("original\n", encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("replace interrupted")

    monkeypatch.setattr(audit.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace interrupted"):
        audit._atomic_write_text(path, "replacement\n")

    assert path.read_text(encoding="utf-8") == "original\n"
    assert list(tmp_path.iterdir()) == [path]


def test_check_mode_is_read_only():
    paths = [
        ROOT / "docs" / "CLI.md",
        ROOT / "docs" / "CLI-REFERENCE-AUDIT.json",
        ROOT / "tests" / "fixtures" / "cli_reference_audit" / "expected.json",
    ]
    before = {path: _digest(path) for path in paths}
    result = _run("--scope", "fixtures", "--check")
    assert result.returncode == 0, result.stderr
    assert {path: _digest(path) for path in paths} == before


def test_generated_manifest_index_and_tombstones_match_checked_in_cli_reference():
    assert audit.generated_docs_match(ROOT)
    manifest = json.loads((ROOT / "docs" / "CLI-COMMAND-MANIFEST.json").read_text())
    index = audit.render_manifest_index(manifest)
    rows = [line for line in index.splitlines()[2:] if line.startswith("|")]
    assert len(rows) == sum(bool(record["visible"]) for record in manifest["commands"])
    assert len(rows) == len(set(rows))
    assert "`--follow`" in index

    tombstones = audit.render_tombstones(manifest)
    assert "`serve` | `router run`" in tombstones
    assert "`voice start` | `voice audio up`" in tombstones
    assert "`controller serve --allow-unauthenticated-loopback`" in tombstones


def test_repository_scope_inventories_match():
    tracked = {
        line.replace("\\", "/")
        for line in subprocess.run(
            ["git", "ls-files"], cwd=ROOT, text=True, capture_output=True, check=True
        ).stdout.splitlines()
    }
    for scope in ("docs", "skills", "full"):
        result = audit.scan(ROOT, scope)
        assert not result.violations
        assert set(result.files) <= tracked
        assert not any(path.startswith("tests/fixtures/eval-data/") for path in result.files)
        assert audit.inventory_matches(ROOT, scope, audit.inventory_record(result))
