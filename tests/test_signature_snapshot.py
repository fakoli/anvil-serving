"""The public signature gate must scan committed bytes, never test output."""
import importlib.util
import io
import json
import subprocess
import tarfile
from contextlib import contextmanager
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / ".agents/skills/anvil-serving-secret-hygiene/scripts/semantic_secret_scan.py"
SPEC = importlib.util.spec_from_file_location("semantic_scan", SCRIPT)
scanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)


@pytest.mark.parametrize("scanner_exit", [0, 1, 125])
@pytest.mark.parametrize("aliased_temporary_path", [False, True])
def test_signature_gate_exports_head_and_preserves_scanner_failure(tmp_path, monkeypatch, scanner_exit, aliased_temporary_path):
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
    # Early 3.11 lacks extraction filters; the snapshot must not need extractall.
    def unsupported_extractall(*args, **kwargs):
        raise AssertionError("snapshot must copy only regular files")

    monkeypatch.setattr(scanner.tarfile.TarFile, "extractall", unsupported_extractall)
    if aliased_temporary_path:
        original_temporary = scanner.tempfile.TemporaryDirectory

        @contextmanager
        def temporary_alias(**kwargs):
            with original_temporary(**kwargs) as directory:
                alias = Path(directory) / "child"
                alias.mkdir()
                yield str(alias / "..")

        monkeypatch.setattr(scanner.tempfile, "TemporaryDirectory", temporary_alias)
    result = scanner.scan_signature_snapshot(tmp_path)
    assert result["ok"] is (scanner_exit == 0)
    assert result["exit_code"] == scanner_exit and len(result["head"]) == 40
    assert "sensitive" not in str(result)
    assert len(paths) == 1 and not paths[0].exists()
    (tmp_path / "tracked.txt").write_text("dirty")
    with pytest.raises(ValueError, match="commit tracked changes"):
        scanner.scan_signature_snapshot(tmp_path)
    assert len(paths) == 1


@pytest.mark.parametrize("name,kind", [
    ("../outside", tarfile.REGTYPE), ("/outside", tarfile.REGTYPE),
    ("C:outside", tarfile.REGTYPE), ("..\\outside", tarfile.REGTYPE),
    ("link", tarfile.SYMTYPE), ("hardlink", tarfile.LNKTYPE),
])
def test_signature_snapshot_rejects_unsafe_archive_members(tmp_path, monkeypatch, name, kind):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as bundle:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = "../outside"
        bundle.addfile(member)

    def git(root, *args):
        return {"status": b"", "rev-parse": b"a" * 40,
                "show": b"ghcr.io/gitleaks/gitleaks@sha256:" + b"a" * 64,
                "archive": archive.getvalue()}[args[0]]

    monkeypatch.setattr(scanner, "run_git", git)
    with pytest.raises(ValueError, match="unsupported snapshot archive member"):
        scanner.scan_signature_snapshot(tmp_path)


@pytest.mark.parametrize("depth", [0, 1, 2])
@pytest.mark.parametrize("field", ["container_id", "containerId"])
def test_semantic_scanner_detects_container_identity_without_disclosing_it(depth, field):
    identity = "abc123ef" * 8
    payload = json.dumps({field: identity})
    for _ in range(depth):
        payload = json.dumps({"content": payload})
    findings = scanner.scan_text(payload, "docs/findings/result.json", "test")
    assert [item["kind"] for item in findings] == ["operator-container-id"]
    assert identity not in json.dumps(findings)
    digest = json.dumps({"sha256": identity, "image": "sha256:" + identity,
                         "container_id": "<redacted>"})
    assert scanner.scan_text(digest, "docs/findings/result.json", "test") == []
