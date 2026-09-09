from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from anvil_serving.connect import manage, recovery
from anvil_serving.connect.config import ManifestError


ROOT = Path(__file__).parents[2]


@pytest.fixture
def declaration(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    data = json.loads((ROOT / "connect/examples/deployment.json").read_text())
    binary = tmp_path / "native"
    binary.write_bytes(b"fixture executable, never run")
    binary.chmod(0o755)
    data["binary"] = str(binary)
    data["config_root"] = str(tmp_path / "config")
    data["gateway"]["state_directory"] = str(tmp_path / "current-state")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(data))
    backup = tmp_path / "backup"
    backup.write_bytes(b"private fixture archive; native validation is a separate gate")
    backup.chmod(0o600)
    monkeypatch.setattr(manage, "_service_identity", lambda data: None)
    return manifest, data, hashlib.sha256(binary.read_bytes()).hexdigest()


def test_restore_preview_is_no_write_and_binds_native_digest(declaration, tmp_path):
    manifest, data, native = declaration
    destination = tmp_path / "fresh"
    result = recovery.restore(manifest, input_path=tmp_path / "backup", destination=destination, sha256="a" * 64, native_sha256=native)
    assert result["applied"] is False and result["grants"] == "disabled"
    assert result["historical_credentials"] == "discarded"
    assert result["config_change_required"] is True
    assert not destination.exists() and not Path(data["config_root"]).exists()
    with pytest.raises(manage.ManageError, match="approved recovery digest"):
        recovery.restore(manifest, input_path=tmp_path / "backup", destination=destination, sha256="a" * 64, native_sha256="0" * 64, apply=True)
    assert not destination.exists()


def test_restore_invokes_only_exact_fresh_native_declaration(declaration, tmp_path):
    manifest, data, native = declaration
    destination, backup = tmp_path / "fresh", tmp_path / "backup"
    original = manifest.read_bytes()
    calls = []
    def runner(argv, timeout, identity):
        calls.append(argv)
        assert argv[:2] == (data["binary"], "restore")
        declaration = json.loads(Path(argv[3]).read_text())
        assert declaration == data["gateway"] | {"state_directory": str(destination)}
        assert argv[4:] == ("--input", str(backup), "--sha256", "a" * 64)
        assert identity is None
        return manage.RunResult(0, json.dumps({"mode": "gateway", "status": "restored", "sha256": "a" * 64, "grants": "disabled"}).encode())
    result = recovery.restore(manifest, input_path=backup, destination=destination, sha256="a" * 64, native_sha256=native, apply=True, runner=runner)
    assert result["applied"] is True and len(calls) == 1
    assert manifest.read_bytes() == original and not Path(data["config_root"]).exists()
    assert not Path(calls[0][3]).exists()


@pytest.mark.parametrize("mutation", ["private-output", "wrong-digest", "failed"])
def test_ambiguous_restore_result_reports_partial_without_echo(declaration, tmp_path, mutation):
    manifest, _, native = declaration
    def runner(*args):
        value = {"mode": "gateway", "status": "restored", "sha256": "a" * 64, "grants": "disabled"}
        if mutation == "private-output":
            value["secret"] = "private-sentinel"
        if mutation == "wrong-digest":
            value["sha256"] = "b" * 64
        return manage.RunResult(1 if mutation == "failed" else 0, json.dumps(value).encode(), b"private-sentinel")
    with pytest.raises(manage.ManageError) as failure:
        recovery.restore(manifest, input_path=tmp_path / "backup", destination=tmp_path / "fresh", sha256="a" * 64, native_sha256=native, apply=True, runner=runner)
    assert failure.value.may_have_executed is True
    assert "private-sentinel" not in str(failure.value)


def test_recovery_rejects_noncanonical_paths_and_existing_destination(declaration, tmp_path):
    manifest, _, native = declaration
    for destination in ("relative", "//etc/fresh", "/a/../fresh", "/a\nfresh"):
        with pytest.raises(ManifestError):
            recovery.restore(manifest, input_path=tmp_path / "backup", destination=destination, sha256="a" * 64, native_sha256=native)
    existing = tmp_path / "existing"
    existing.mkdir(mode=0o700)
    with pytest.raises(manage.ManageError, match="already exists"):
        recovery.restore(manifest, input_path=tmp_path / "backup", destination=existing, sha256="a" * 64, native_sha256=native)


def test_backup_checks_active_binding_and_never_returns_private_payload(declaration, tmp_path, monkeypatch):
    manifest, data, native = declaration
    checks, calls = [], []
    monkeypatch.setattr(manage, "_current", lambda data: checks.append("current"))
    monkeypatch.setattr(manage, "_verified_binaries", lambda data, target: {"native": native})
    monkeypatch.setattr(manage, "_bound_active", lambda data, target, hashes: checks.append("bound"))
    def runner(argv, timeout, identity):
        calls.append(argv)
        return manage.RunResult(0, json.dumps({"mode": "gateway", "status": "backup-created", "sha256": "a" * 64}).encode())
    output = tmp_path / "output-backup"
    preview = recovery.backup(manifest, output_path=output, runner=runner)
    assert preview["applied"] is False and checks == ["current", "bound"] and not calls
    result = recovery.backup(manifest, output_path=output, apply=True, runner=runner)
    assert result["sha256"] == "a" * 64 and result["retain_digest_separately"] is True
    assert calls == [(data["binary"], "backup", "--config", str(Path(data["config_root"]) / "gateway.json"), "--output", str(output))]
