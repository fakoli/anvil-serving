"""The public signature gate must scan committed bytes, never test output."""
import importlib.util
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / ".agents/skills/anvil-serving-secret-hygiene/scripts/semantic_secret_scan.py"
SPEC = importlib.util.spec_from_file_location("semantic_scan", SCRIPT)
scanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)


@pytest.mark.parametrize("scanner_exit", [0, 1, 125])
def test_signature_gate_exports_head_and_preserves_scanner_failure(tmp_path, monkeypatch, scanner_exit):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init")
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/secret-scan.yml").write_text("image: ghcr.io/gitleaks/gitleaks@sha256:" + "a" * 64)
    (tmp_path / ".gitleaks.toml").write_text("[extend]\nuseDefault = true\n")
    (tmp_path / "tracked.txt").write_text("candidate")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-m", "fixture")
    (tmp_path / "site").mkdir()
    (tmp_path / "site/generated.txt").write_text("untracked test output")
    original_run = subprocess.run
    paths = []

    def run(command, **kwargs):
        if command[0] != "docker":
            return original_run(command, **kwargs)
        mount = Path(command[command.index("--volume") + 1].removesuffix(":/repo:ro"))
        paths.append(mount)
        assert (mount / "tracked.txt").read_text() == "candidate"
        assert not (mount / "site").exists()
        assert "--redact=100" in command and kwargs["capture_output"]
        assert kwargs["timeout"] == 300
        return subprocess.CompletedProcess(command, scanner_exit, b"sensitive stdout", b"sensitive stderr")

    monkeypatch.setattr(scanner.subprocess, "run", run)
    result = scanner.scan_signature_snapshot(tmp_path)
    assert result["ok"] is (scanner_exit == 0)
    assert result["exit_code"] == scanner_exit and len(result["head"]) == 40
    assert "sensitive" not in str(result)
    assert len(paths) == 1 and not paths[0].exists()
    (tmp_path / "tracked.txt").write_text("dirty")
    with pytest.raises(ValueError, match="commit tracked changes"):
        scanner.scan_signature_snapshot(tmp_path)
    assert len(paths) == 1
