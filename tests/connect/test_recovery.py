from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from pathlib import Path

import pytest

from anvil_serving.connect import manage, recovery
from anvil_serving.connect.config import ManifestError
from anvil_serving.connect.render import render_for_inspection


pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"},
    reason="Connect recovery tests require Linux amd64",
)


ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    ("operation", "kwargs"),
    [
        (recovery.backup, {"output_path": Path("/private/output")}),
        (recovery.restore, {
            "input_path": Path("/private/input"),
            "destination": Path("/private/destination"),
            "sha256": "a" * 64,
            "native_sha256": "b" * 64,
        }),
    ],
)
def test_recovery_rejects_unsupported_platform_before_manifest_read(monkeypatch, operation, kwargs):
    monkeypatch.setattr(manage, "supported_platform", lambda: False)
    with pytest.raises(manage.UnsupportedPlatformError):
        operation(Path("/missing/deployment.json"), **kwargs)


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
    data.pop("service_user")
    data["caddy"]["state_directory"] = str(tmp_path / "caddy-state")
    data["service_identities"] = {
        "gateway": {"uid": 1201, "gid": 2201},
        "edge": {"uid": 1202, "gid": 2202},
        "idp": {"uid": 1203, "gid": 2203},
        "connectors": {"dashboard": {"uid": 1204, "gid": 2204}},
        "clients": {"dashboard-api": {"uid": 1205, "gid": 2205}},
        "ingress": {"group_id": 2290, "directory": str(tmp_path / "ingress")},
    }
    data["service_limits"] = {
        "gateway": {"memory_max_bytes": 805306368, "tasks_max": 128},
        "edge": {"memory_max_bytes": 536870912, "tasks_max": 64},
        "idp": {"memory_max_bytes": 536870912, "tasks_max": 64},
        "connectors": {"dashboard": {"memory_max_bytes": 402653184, "tasks_max": 64}},
        "clients": {"dashboard-api": {"memory_max_bytes": 268435456, "tasks_max": 32}},
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(data))
    backup = tmp_path / "backup"
    backup.write_bytes(b"private fixture archive; native validation is a separate gate")
    backup.chmod(0o600)
    monkeypatch.setattr(manage, "_role_service_identity", lambda data, role: None)
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
        assert declaration == data["gateway"] | {
            "state_directory": str(destination),
            "ingress": {
                "directory": data["service_identities"]["ingress"]["directory"],
                "gateway_uid": data["service_identities"]["gateway"]["uid"],
                "edge_uid": data["service_identities"]["edge"]["uid"],
                "group_id": data["service_identities"]["ingress"]["group_id"],
            },
        }
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
    monkeypatch.setattr(recovery, "_recovery_current", lambda data: checks.append("current"))
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


def test_legacy_recovery_identity_is_scoped_and_activation_stays_rejected(tmp_path, monkeypatch):
    legacy = json.loads((ROOT / "connect/examples/deployment.json").read_text())
    legacy["config_root"] = str(tmp_path / "legacy-rendered")
    observed = []
    monkeypatch.setattr(manage, "_legacy_recovery_identity", lambda data: observed.append(data["service_user"]) or None)
    monkeypatch.setattr(manage, "_role_service_identity", lambda *_: pytest.fail("legacy recovery selected a role identity"))
    identity, uid = recovery._recovery_identity(legacy)
    assert identity is None and uid == os.geteuid() and observed == [legacy["service_user"]]
    manifest = tmp_path / "legacy.json"
    manifest.write_text(json.dumps(legacy))
    with pytest.raises(ManifestError, match="isolated service identities"):
        manage.up(manifest, manage.Target("gateway"))



def _legacy_recovery_declaration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict, str]:
    """Create one current legacy generation for offline recovery only."""
    tmp_path.chmod(0o700)
    data = json.loads((ROOT / "connect/examples/deployment.json").read_text(encoding="utf-8"))
    binary = tmp_path / "native"
    binary.write_bytes(b"fixture executable, never run")
    binary.chmod(0o755)
    data["binary"] = str(binary)
    data["config_root"] = str(tmp_path / "legacy-rendered")
    data["gateway"]["state_directory"] = str(tmp_path / "current-state")
    manifest = tmp_path / "legacy.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    root = Path(data["config_root"])
    generated = render_for_inspection(data)
    manage._materialize(generated["files"], root)
    manage._make_public(root)
    monkeypatch.setattr(manage, "_legacy_recovery_identity", lambda _: None)
    return manifest, data, hashlib.sha256(binary.read_bytes()).hexdigest()


def test_legacy_recovery_uses_inspection_only_current_and_temporary_declarations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, data, native = _legacy_recovery_declaration(tmp_path, monkeypatch)
    monkeypatch.setattr(manage, "_verified_binaries", lambda *_: {"native": native})
    monkeypatch.setattr(manage, "_bound_active", lambda *_: None)
    calls: list[tuple[str, ...]] = []

    def runner(argv, timeout, identity):  # type: ignore[no-untyped-def]
        calls.append(argv)
        assert identity is None
        if argv[1] == "backup":
            assert Path(argv[3]) == Path(data["config_root"]) / "gateway.json"
            return manage.RunResult(0, json.dumps({"mode": "gateway", "status": "backup-created", "sha256": "a" * 64}).encode())
        declaration = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        assert declaration["state_directory"] == str(tmp_path / "restored")
        return manage.RunResult(0, json.dumps({"mode": "gateway", "status": "restored", "sha256": "a" * 64, "grants": "disabled"}).encode())

    output = tmp_path / "backup-output"
    preview = recovery.backup(manifest, output_path=output, runner=runner)
    assert preview["applied"] is False and calls == []
    assert recovery.backup(manifest, output_path=output, apply=True, runner=runner)["applied"] is True
    archive = tmp_path / "archive"
    archive.write_bytes(b"private fixture archive")
    archive.chmod(0o600)
    restored = tmp_path / "restored"
    restore_preview = recovery.restore(manifest, input_path=archive, destination=restored, sha256="a" * 64, native_sha256=native, runner=runner)
    assert restore_preview["applied"] is False and len(calls) == 1
    assert recovery.restore(manifest, input_path=archive, destination=restored, sha256="a" * 64, native_sha256=native, apply=True, runner=runner)["applied"] is True
    assert [call[1] for call in calls] == ["backup", "restore"]
    assert not restored.exists()
