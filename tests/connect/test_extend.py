"""Managed connector resource-extension tests.

The rendered generation is a real staged render of a valid isolated manifest;
every host boundary (systemd, admin socket, native binary calls, fingerprint
issuance) is injected, so the orchestration, rollback, and evidence behavior
are proven without root or a live deployment.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
import sys

import pytest

from anvil_serving.connect import extend as extend_module
from anvil_serving.connect.manage import Target
from tests.connect.test_render import isolated_manifest

# The Connect manifest reader requires POSIX no-follow descriptors; extend
# runs on POSIX only, like the rest of the family.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the Connect manifest reader requires POSIX no-follow descriptors",
)

ENROLLED = ("dashboard", "dashboard-api")
ADDED = "reports"
DECLARED = (*ENROLLED, ADDED)
GEN_OLD = "a" * 64
GEN_NEW = "b" * 64
NATIVE = "c" * 64
CADDY = "d" * 64
AUTHELIA = "e" * 64
FINGERPRINT = "F" * 43


def _rule(resource_id: str) -> dict[str, object]:
    return {
        "id": resource_id, "host": f"{resource_id}.example.test", "path_prefix": "/",
        "methods": ["GET", "POST"], "access": "browser", "native_auth": "passthrough",
        "limits": {"request_bytes": 1048576, "concurrent": 8, "buffer_bytes": 65536,
                    "idle_seconds": 30, "duration_seconds": 300},
    }


def _declared_resources(hosts: tuple[str, ...]) -> list[dict[str, object]]:
    return [
        {"envelope": {"rule": _rule(host), "listen": f"127.0.0.1:{18090 + i}",
                      "origin_url": "http://127.0.0.1:19000", "token_env": ""},
         "reverse_address": f"127.0.0.1:{17010 + i}"}
        for i, host in enumerate(hosts)
    ]


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@pytest.fixture()
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One rendered owned generation plus a three-resource declaration."""
    root = tmp_path / "rendered"
    root.mkdir(mode=0o755)
    connectors = root / "connectors"
    connectors.mkdir(mode=0o755)
    manifest = isolated_manifest()
    manifest["config_root"] = str(root)
    manifest["connectors"][0]["resources"] = _declared_resources(DECLARED)
    manifest["connectors"][0]["state_directory"] = str(tmp_path / "state-dashboard")
    manifest["gateway"]["state_directory"] = str(tmp_path / "state-gateway")
    manifest["gateway"]["gateway"]["resources"] = [
        {"connector": "dashboard", "rule": _rule(host), "tunnel_address": f"127.0.0.1:{17010 + i}"}
        for i, host in enumerate(DECLARED)
    ]
    manifest["clients"] = []
    manifest["environment_files"]["clients"] = {}
    manifest["service_identities"]["clients"] = {}
    manifest["service_limits"]["clients"] = {}
    manifest_path = tmp_path / "deployment.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "state-gateway").mkdir(mode=0o700)

    connector_tree = {"schema": "anvil-connect.connector/v1", "id": "dashboard",
                      "resources": _declared_resources(ENROLLED)}
    gateway_tree = {"schema": "anvil-connect.gateway/v1", "gateway": {"resources": [
        {"rule": _rule(host), "connector": "dashboard", "tunnel_address": f"127.0.0.1:{17010 + i}"}
        for i, host in enumerate(ENROLLED)
    ]}}
    (connectors / "dashboard.json").write_text(json.dumps(connector_tree), encoding="utf-8")
    (root / "gateway.json").write_text(json.dumps(gateway_tree), encoding="utf-8")
    tracked = {
        "connectors/dashboard.json": _sha(json.dumps(connector_tree).encode()),
        "gateway.json": _sha(json.dumps(gateway_tree).encode()),
    }
    managed = {"schema": "anvil-connect.ownership/v1", "generation": GEN_OLD, "files": tracked}
    (root / "managed.json").write_text(json.dumps(managed), encoding="utf-8")
    record = {"schema": "anvil-connect.activation/v1", "generation": GEN_OLD,
              "native_sha256": NATIVE, "components": {"caddy": CADDY, "authelia": AUTHELIA}}
    (tmp_path / "activation.json").write_text(json.dumps(record), encoding="utf-8")
    os.replace(tmp_path / "activation.json", root.parent / ".rendered.anvil-connect-activation.json")
    (tmp_path / "systemd").mkdir()

    monkeypatch.setattr("anvil_serving.connect.manage._validate_data", lambda *a, **k: {
        "digests": {"native": NATIVE, "caddy": CADDY, "authelia": AUTHELIA}})
    # Most sequence tests isolate orchestration from host NSS; the dedicated
    # binding test supplies real declared service identities and file handoff.
    monkeypatch.setattr("anvil_serving.connect.manage._target_identity", lambda *a, **k: None)
    monkeypatch.setattr("anvil_serving.connect.manage._role_service_identity", lambda *a, **k: None)
    return {
        "manifest_path": manifest_path, "target": Target("connector", "dashboard"),
        "root": root, "tmp": tmp_path,
    }


class FakeRunner:
    """Answer systemctl interactions; record every argv."""

    def __init__(self, unit_root: Path | None = None) -> None:
        self.calls: list[list[str]] = []
        self.states: dict[str, str] = {}
        self.file_states: dict[str, str] = {}
        self.unit_root = unit_root or Path("/etc/systemd/system")
        self.gateway_ready_error: Exception | None = None

    def __call__(self, argv: list[str], timeout: float | None = None,
                 identity: object = None) -> "extend_module.manage.RunResult":
        self.calls.append(list(argv))
        if len(argv) >= 3 and str(argv[0]).endswith("systemctl"):
            if argv[1] in {"stop", "disable"}:
                self.states[argv[-1]] = "inactive"
            elif argv[1] in {"start", "restart", "enable"}:
                self.states[argv[-1]] = "active"
        if len(argv) >= 2 and str(argv[0]).endswith("systemctl") and argv[1] == "show":
            properties = next((a for a in argv if a.startswith("--property=")), "")
            unit = argv[-1]
            if "LoadState" in properties:
                fragment = self.unit_root / unit
                stdout = (
                    f"LoadState={'loaded' if fragment.exists() else 'not-found'}\n"
                    f"FragmentPath={fragment if fragment.exists() else ''}\nDropInPaths=\n"
                )
            else:
                stdout = (
                    f"ActiveState={self.states.get(unit, 'active')}\n"
                    f"UnitFileState={self.file_states.get(unit, 'enabled')}\n"
                )
            return extend_module.manage.RunResult(0, stdout=stdout.encode())
        return extend_module.manage.RunResult(0)


@pytest.fixture()
def runner(environment) -> FakeRunner:
    return FakeRunner(unit_root=environment["tmp"] / "systemd")


# --- plan -------------------------------------------------------------------


# --- dry run ----------------------------------------------------------------


def test_dry_run_returns_the_plan_and_mutates_nothing(environment) -> None:
    before = (environment["root"] / "managed.json").read_text(encoding="utf-8")
    result = extend_module.extend(
        environment["manifest_path"], environment["target"], confirm=False,
        unit_root=environment["tmp"] / "systemd")
    assert result["dry_run"] is True and result["applied"] is False
    assert result["plan"]["added"] == [ADDED]
    assert (environment["root"] / "managed.json").read_text(encoding="utf-8") == before


# --- full sequence ----------------------------------------------------------


@pytest.fixture()
def enrollment_log(monkeypatch: pytest.MonkeyPatch, environment):
    """Record admin/native calls; the invite writes its bundle as the IdP would."""
    calls: list[dict[str, object]] = []
    authority = {
        "id": "dashboard", "status": "active", "fingerprint": FINGERPRINT,
        "epoch": "a" * 64, "generation": 1, "resources": sorted(ENROLLED),
    }
    connector_identity = {
        "id": "dashboard", "status": "enrolled", "fingerprint": FINGERPRINT,
        "epoch": "a" * 64, "generation": 2, "resources": sorted(DECLARED),
    }

    def fake_admin(manifest_path, *, request_path, output_path=None, apply, runner=None):
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        calls.append({"admin": request["operation"], "request": request})
        if request["operation"] == "installation-status" and output_path is not None:
            Path(output_path).write_text(json.dumps({"status": authority}), encoding="utf-8")
            Path(output_path).chmod(0o600)
        elif request["operation"] == "installation-revoke":
            if authority["status"] != "revoked":
                authority.update(status="revoked", generation=authority["generation"] + 1)
        if output_path is not None and request["operation"] == "invite":
            authority.update(status="invited", generation=authority["generation"] + 1,
                             resources=sorted(DECLARED), fingerprint="")
            Path(output_path).write_text(json.dumps({
                "invitation": "aci1." + "1" * 16 + "." + "A" * 43,
                "control_host": "connect-control.example.test",
                "tunnel_host": "connect-tunnel.example.test",
                "inner_ca_pem": "-----BEGIN CERTIFICATE-----",
            }), encoding="utf-8")
            Path(output_path).chmod(0o600)
        return {}

    monkeypatch.setattr(extend_module.manage, "admin", fake_admin)
    def fake_native_init(manifest_path, target, *, bundle, apply, runner=None):
        calls.append({"native_init": str(bundle)})
        authority.update(status="pending", fingerprint=FINGERPRINT)
        connector_identity.update(status="enrolled", generation=authority["generation"])
        Path(bundle).unlink()

    def fake_native_reenroll(manifest_path, target, *, prior, bundle, apply, runner=None):
        calls.append({"native_reenroll": {"prior": str(prior), "bundle": str(bundle)}})
        assert json.loads(Path(prior).read_text()) == {
            "id": "dashboard", "fingerprint": FINGERPRINT, "epoch": "a" * 64,
            "generation": 1, "resources": sorted(ENROLLED),
        }
        connector_identity.update(status="pending", generation=authority["generation"], resources=sorted(DECLARED))

    monkeypatch.setattr(extend_module.manage, "native_init", fake_native_init)
    monkeypatch.setattr(extend_module.manage, "native_reenroll", fake_native_reenroll)
    monkeypatch.setattr(extend_module.manage, "identity",
                        lambda manifest_path, target, runner=None:
                        calls.append({"identity": True}) or {"identity": dict(connector_identity)})
    return calls


def test_legacy_connector_handoff_retry_after_ownership_failure_keeps_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The root-owned 0700 intermediate is retry-safe after a handoff fault."""
    root = tmp_path / "rendered"
    root.mkdir(mode=0o755)
    data = {"config_root": str(root)}
    identity = extend_module.manage.ServiceIdentity(os.geteuid(), os.getegid())
    monkeypatch.setattr(extend_module.manage, "_role_service_identity", lambda *_args: identity)
    sidecar = tmp_path / ".rendered.anvil-connect-extend"
    sidecar.mkdir(mode=0o711)
    sidecar.chmod(0o711)
    legacy = sidecar / "connector-dashboard"
    legacy.mkdir(mode=0o730)
    legacy.chmod(0o730)
    bundle = legacy / "invitation-retained.json"
    bundle.write_bytes(b'{"opaque":"retained"}')
    bundle.chmod(0o600)
    original_fchown = extend_module.os.fchown
    calls = 0

    def interrupted_fchown(fd: int, uid: int, gid: int) -> None:
        nonlocal calls
        calls += 1
        raise OSError("injected ownership handoff interruption")

    monkeypatch.setattr(extend_module.os, "fchown", interrupted_fchown)
    with pytest.raises(extend_module.ExtendError, match="connector sidecar is unsafe"):
        extend_module._retained_bundle(data, "dashboard", bundle.name)
    assert stat.S_IMODE(legacy.lstat().st_mode) == 0o700
    assert bundle.read_bytes() == b'{"opaque":"retained"}'

    monkeypatch.setattr(extend_module.os, "fchown", original_fchown)
    assert extend_module._retained_bundle(data, "dashboard", bundle.name) == bundle
    assert calls == 1
    assert bundle.read_bytes() == b'{"opaque":"retained"}'


# --- review-gate regressions (Greptile P1s) ---------------------------------


def test_post_redemption_failure_retains_exact_approval_recovery(
    environment, runner: FakeRunner, enrollment_log, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once redeemed, the old resource set is unrecoverable: finish forward."""
    attempts = {"approve": 0}

    def flaky_admin(manifest_path, *, request_path, output_path=None, apply, runner=None):
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        enrollment_log.append({"admin": request["operation"], "request": request})
        if request["operation"] == "invite" and output_path is not None:
            Path(output_path).write_text(json.dumps({
                "invitation": "aci1." + "1" * 16 + "." + "A" * 43,
                "control_host": "connect-control.example.test",
                "tunnel_host": "connect-tunnel.example.test",
                "inner_ca_pem": "-----BEGIN CERTIFICATE-----",
            }), encoding="utf-8")
            Path(output_path).chmod(0o600)
        if request["operation"] == "approve":
            attempts["approve"] += 1
            if attempts["approve"] == 1:
                raise extend_module.ManageError("admin socket transiently unavailable")
        if request["operation"] == "installation-status" and output_path is not None:
            seen = [entry.get("admin") for entry in enrollment_log]
            if "installation-revoke" not in seen[:-1]:
                status, generation, resources, fingerprint = "active", 1, sorted(ENROLLED), FINGERPRINT
            elif "invite" not in seen[:-1]:
                status, generation, resources, fingerprint = "revoked", 2, sorted(ENROLLED), FINGERPRINT
            else:
                status, generation, resources, fingerprint = "pending", 3, sorted(DECLARED), FINGERPRINT
            Path(output_path).write_text(json.dumps({
                "status": {"id": "dashboard", "status": status, "fingerprint": fingerprint,
                           "epoch": "a" * 64, "generation": generation, "resources": resources},
            }), encoding="utf-8")
            Path(output_path).chmod(0o600)
        return {}

    monkeypatch.setattr(extend_module.manage, "admin", flaky_admin)
    monkeypatch.setattr(extend_module.manage, "_gateway_ready", lambda *a, **k: None)
    monkeypatch.setattr(extend_module.manage, "_started_units", lambda *a, **k: None)
    with pytest.raises(extend_module.ExtendError, match="authority changed"):
        extend_module.extend(
            environment["manifest_path"], environment["target"],
            confirm=True, runner=runner, unit_root=environment["tmp"] / "systemd")
    recovery = extend_module._read_recovery(
        json.loads(Path(environment["manifest_path"]).read_text()), "dashboard", sorted(DECLARED))
    assert recovery is not None and recovery["phase"] == "approve-pending"
    record = json.loads((environment["root"].parent / ".rendered.anvil-connect-activation.json").read_text())
    assert record["generation"] != GEN_OLD
    # The rendered generation still carries the extended set (no rollback).
    connector_tree = json.loads((environment["root"] / "connectors" / "dashboard.json").read_text())
    assert extend_module._resource_ids(connector_tree["resources"]) == set(DECLARED)


def test_staged_reenrollment_retries_init_only_when_authority_remains_invited(
    environment, runner: FakeRunner, enrollment_log, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash after local staging retries init, never replacement or invitation."""
    original_init = extend_module.manage.native_init
    calls = {"init": 0}
    connector_unit = "anvil-connect-connector-dashboard.service"
    runner.file_states[connector_unit] = "disabled"

    def interrupted_init(*args, **kwargs):
        calls["init"] += 1
        if calls["init"] == 1:
            raise extend_module.ManageError("native init was not dispatched", may_have_executed=True)
        return original_init(*args, **kwargs)

    monkeypatch.setattr(extend_module.manage, "native_init", interrupted_init)
    monkeypatch.setattr(extend_module.manage, "_gateway_ready", lambda *a, **k: None)
    monkeypatch.setattr(extend_module.manage, "_started_units", lambda *a, **k: None)
    with pytest.raises(extend_module.ExtendError, match="init-pending"):
        extend_module.extend(
            environment["manifest_path"], environment["target"],
            confirm=True, runner=runner, unit_root=environment["tmp"] / "systemd")

    result = extend_module.extend(
        environment["manifest_path"], environment["target"],
        confirm=True, runner=runner, unit_root=environment["tmp"] / "systemd")

    assert result["recovered"] is True and calls["init"] == 2
    assert [entry["admin"] for entry in enrollment_log if "admin" in entry].count("installation-revoke") == 1
    assert len([entry for entry in enrollment_log if "native_reenroll" in entry]) == 1
    assert runner.states[connector_unit] == "active"
    assert runner.file_states[connector_unit] == "disabled"
    assert not any(call[:2] == ["/usr/bin/systemctl", "enable"] and call[-1] == connector_unit for call in runner.calls)


def test_invite_failure_after_revoke_keeps_new_generation_and_retained_recovery(
    environment, runner: FakeRunner, enrollment_log, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful revoke is the no-rollback boundary even if invite fails."""
    original_admin = extend_module.manage.admin

    def fail_invite(manifest_path, *, request_path, output_path=None, apply, runner=None):
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        if request["operation"] == "invite":
            raise extend_module.ManageError("gateway invite unavailable")
        return original_admin(manifest_path, request_path=request_path, output_path=output_path, apply=apply, runner=runner)

    monkeypatch.setattr(extend_module.manage, "admin", fail_invite)
    monkeypatch.setattr(extend_module.manage, "_gateway_ready", lambda *a, **k: None)
    monkeypatch.setattr(extend_module.manage, "_started_units", lambda *a, **k: None)
    with pytest.raises(extend_module.ExtendError, match="authority changed"):
        extend_module.extend(
            environment["manifest_path"], environment["target"],
            confirm=True, runner=runner, unit_root=environment["tmp"] / "systemd")
    recovery = extend_module._read_recovery(
        json.loads(Path(environment["manifest_path"]).read_text()), "dashboard", sorted(DECLARED))
    assert recovery is not None and recovery["phase"] == "revoked"
    record = json.loads((environment["root"].parent / ".rendered.anvil-connect-activation.json").read_text())
    assert record["generation"] != GEN_OLD


def _installation_status(status: str, generation: int, resources: set[str], fingerprint: str) -> dict[str, object]:
    return {
        "id": "dashboard", "status": status, "fingerprint": fingerprint,
        "epoch": "a" * 64, "generation": generation, "resources": sorted(resources),
    }


def test_recovery_accepts_only_the_exact_post_revoke_status_after_a_lost_response(
    environment, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A revoke that wrote before its response was lost is not issued twice."""
    data = json.loads(Path(environment["manifest_path"]).read_text())
    prior = _installation_status("active", 1, ENROLLED, FINGERPRINT)
    extend_module._write_recovery(
        data, "dashboard", sorted(DECLARED), "revoke-pending", prior=prior,
        generation=1, fingerprint=FINGERPRINT,
    )
    monkeypatch.setattr(
        extend_module, "_installation_status",
        lambda *_args: _installation_status("revoked", 2, ENROLLED, FINGERPRINT),
    )
    calls: list[str] = []
    monkeypatch.setattr(extend_module, "_revoke", lambda *_args: calls.append("revoke"))

    recovery = extend_module._resolve_recovery_phase(
        data, environment["manifest_path"], "dashboard", sorted(DECLARED), runner,
    )

    assert recovery["phase"] == "revoked"
    assert recovery["generation"] == 2
    assert recovery["fingerprint"] == FINGERPRINT
    assert calls == []


def test_recovery_revokes_an_invitation_when_its_bearer_response_was_lost(
    environment, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write-then-raise invite is retired before a new bearer can be issued."""
    data = json.loads(Path(environment["manifest_path"]).read_text())
    prior = _installation_status("active", 1, ENROLLED, FINGERPRINT)
    extend_module._write_recovery(
        data, "dashboard", sorted(DECLARED), "invite-pending", prior=prior,
        generation=2, fingerprint=FINGERPRINT,
    )
    statuses = iter((
        _installation_status("invited", 3, DECLARED, ""),
        _installation_status("revoked", 4, DECLARED, ""),
    ))
    monkeypatch.setattr(extend_module, "_installation_status", lambda *_args: next(statuses))
    calls: list[str] = []
    monkeypatch.setattr(extend_module, "_revoke", lambda *_args: calls.append("revoke"))

    recovery = extend_module._resolve_recovery_phase(
        data, environment["manifest_path"], "dashboard", sorted(DECLARED), runner,
    )

    assert recovery["phase"] == "revoked"
    assert recovery["generation"] == 4
    assert recovery["fingerprint"] == ""
    assert calls == ["revoke"]


def test_refresh_proves_the_original_local_identity_with_a_public_filtered_declaration(
    environment, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refresh reads only the retained old connector shape, never the new declaration."""
    data = json.loads(Path(environment["manifest_path"]).read_text())
    prior = _installation_status("active", 7, ENROLLED, FINGERPRINT)
    observed: dict[str, object] = {}

    def native_identity(_runner, argv, _timeout, identity=None):
        config = Path(argv[-1])
        root = config.parent
        declaration = json.loads(config.read_text(encoding="utf-8"))
        observed.update(
            argv=argv,
            resources=tuple(
                item["envelope"]["rule"]["id"] for item in declaration["resources"]
            ),
            root_mode=stat.S_IMODE(root.stat().st_mode),
            config_mode=stat.S_IMODE(config.stat().st_mode),
            identity=identity,
        )
        return extend_module.manage.RunResult(
            0, stdout=json.dumps({**prior, "status": "enrolled"}).encode("utf-8"),
        )

    monkeypatch.setattr(extend_module.manage, "_run", native_identity)
    assert extend_module._prior_connector_identity(data, "dashboard", prior, runner) == {
        **prior, "status": "enrolled",
    }
    assert observed["argv"][1:3] == ("identity", "--config")
    assert observed["resources"] == ENROLLED
    assert observed["root_mode"] == 0o755
    assert observed["config_mode"] == 0o644

    def changed_identity(*_args, **_kwargs):
        return extend_module.manage.RunResult(
            0, stdout=json.dumps({
                **prior, "status": "pending", "generation": 9, "resources": list(DECLARED),
            }).encode("utf-8"),
        )

    monkeypatch.setattr(extend_module.manage, "_run", changed_identity)
    with pytest.raises(extend_module.ExtendError, match="prior connector identity changed"):
        extend_module._prior_connector_identity(data, "dashboard", prior, runner)


def test_stale_unstaged_invitation_refreshes_once_after_a_lost_revoke_response(
    environment, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An old untouched invite retires once, preserves prior 7, then stages fresh 11."""
    data = json.loads(Path(environment["manifest_path"]).read_text())
    prior = _installation_status("active", 7, ENROLLED, FINGERPRINT)
    directory, identity = extend_module._role_directory(data, "connector", "dashboard")
    old_bundle = extend_module._write_handoff(
        directory, "invitation-retained.json", b"{}", identity,
    )
    extend_module._write_recovery(
        data, "dashboard", sorted(DECLARED), "refresh-revoke-pending", old_bundle.name,
        prior, 9,
    )
    state = _installation_status("invited", 9, set(DECLARED), "")
    local = {"status": "pending", "generation": 11, "resources": sorted(DECLARED)}
    calls: list[str] = []

    monkeypatch.setattr(extend_module, "_prior_connector_identity", lambda *_args: {**prior, "status": "enrolled"})
    monkeypatch.setattr(extend_module, "_installation_status", lambda *_args: dict(state))

    def revoke(*_args):
        calls.append("revoke")
        state.update(status="revoked", generation=10)
        if len(calls) == 1:
            raise extend_module.ManageError("revoke response lost", may_have_executed=True)

    monkeypatch.setattr(extend_module, "_revoke", revoke)
    with pytest.raises(extend_module.ManageError, match="response lost"):
        extend_module._resolve_recovery_phase(
            data, environment["manifest_path"], "dashboard", sorted(DECLARED), runner,
        )
    retained = extend_module._read_recovery(data, "dashboard", sorted(DECLARED))
    assert retained is not None and retained["phase"] == "refresh-revoke-pending"
    assert retained["prior"] == prior and old_bundle.exists()

    # A retry sees the authoritative post-revoke generation and never repeats
    # the revoke.  It then follows the normal revoked -> invite -> stage path.
    resolved = extend_module._resolve_recovery_phase(
        data, environment["manifest_path"], "dashboard", sorted(DECLARED), runner,
    )
    assert resolved["phase"] == "revoked" and resolved["generation"] == 10
    assert resolved["prior"] == prior and calls == ["revoke"] and not old_bundle.exists()

    def invite(*_args):
        calls.append("invite")
        state.update(status="invited", generation=11, fingerprint="", resources=sorted(DECLARED))
        return extend_module._write_handoff(
            directory, "invitation-fresh.json", b"{}", identity,
        )

    def native_reenroll(_manifest, _target, *, prior, bundle, **_kwargs):
        calls.append("re-enroll")
        assert json.loads(Path(prior).read_text(encoding="utf-8")) == {
            "id": "dashboard", "fingerprint": FINGERPRINT, "epoch": "a" * 64,
            "generation": 7, "resources": sorted(ENROLLED),
        }
        assert Path(bundle).name == "invitation-fresh.json"

    def native_init(*_args, bundle, **_kwargs):
        calls.append("init")
        state.update(status="pending", fingerprint=FINGERPRINT)
        Path(bundle).unlink()

    monkeypatch.setattr(extend_module, "_invite", invite)
    monkeypatch.setattr(extend_module.manage, "native_reenroll", native_reenroll)
    monkeypatch.setattr(extend_module.manage, "native_init", native_init)
    monkeypatch.setattr(extend_module, "_stop_connector", lambda *_args: None)
    monkeypatch.setattr(extend_module, "_start_connector", lambda *_args: None)
    monkeypatch.setattr(extend_module.manage, "_started_units", lambda *_args: None)
    monkeypatch.setattr(
        extend_module, "_connector_identity",
        lambda *_args: {
            "id": "dashboard", "status": local["status"], "fingerprint": FINGERPRINT,
            "epoch": "a" * 64, "generation": local["generation"], "resources": local["resources"],
        },
    )
    monkeypatch.setattr(extend_module, "_admin_exchange", lambda *_args, **_kwargs: calls.append("approve"))

    assert extend_module._resume_enrollment(
        data, environment["manifest_path"], environment["target"], "connector.service",
        (False, "disabled"), sorted(DECLARED), runner, environment["tmp"] / "systemd",
    ) == FINGERPRINT
    assert calls == ["revoke", "invite", "re-enroll", "init", "approve"]
    assert extend_module._read_recovery(data, "dashboard", sorted(DECLARED)) is None


@pytest.mark.parametrize("age,refreshes", [(539, False), (540, True), (-1, True)])
def test_invitation_refresh_uses_manager_recovery_age_not_bundle_mtime(
    environment, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, age: int, refreshes: bool,
) -> None:
    """Connector-owned invitation timestamps cannot influence refresh eligibility."""
    data = json.loads(Path(environment["manifest_path"]).read_text())
    prior = _installation_status("active", 7, ENROLLED, FINGERPRINT)
    directory, identity = extend_module._role_directory(data, "connector", "dashboard")
    bundle = extend_module._write_handoff(directory, "invitation-age.json", b"{}", identity)
    extend_module._write_recovery(
        data, "dashboard", sorted(DECLARED), "re-enroll-pending", bundle.name, prior, 9,
    )
    recovery_path = extend_module._recovery_path(data, "dashboard")
    now = 50_000.0
    os.utime(recovery_path, (now - age, now - age))
    # Make the consuming service's file look arbitrarily old or future-dated;
    # only the manager-owned recovery file may decide the deadline.
    os.utime(bundle, (now + 100_000, now + 100_000))
    recovery = extend_module._read_recovery(data, "dashboard", sorted(DECLARED))
    assert recovery is not None
    monkeypatch.setattr(extend_module, "_resolve_recovery_phase", lambda *_args: recovery)
    monkeypatch.setattr(extend_module, "_prior_connector_identity", lambda *_args: (_ for _ in ()).throw(
        extend_module.ExtendError("refresh-proof"),
    ))
    monkeypatch.setattr(extend_module, "_reenrollment_prior", lambda *_args: bundle)
    monkeypatch.setattr(extend_module, "_stop_connector", lambda *_args: None)
    monkeypatch.setattr(
        extend_module.manage, "native_reenroll",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(extend_module.ExtendError("stage")),
    )
    monkeypatch.setattr(extend_module.time, "time", lambda: now)

    expected = "refresh-proof" if refreshes else "stage"
    with pytest.raises(extend_module.ExtendError, match=expected):
        extend_module._resume_enrollment(
            data, environment["manifest_path"], environment["target"], "connector.service",
            (False, "disabled"), sorted(DECLARED), runner, environment["tmp"] / "systemd",
        )


@pytest.mark.parametrize(
    "bundle_name, prior_status, generation, fingerprint",
    [
        (None, "active", 9, ""),
        ("invitation-retained.json", "active", 9, FINGERPRINT),
        ("invitation-retained.json", "invited", 9, ""),
        ("invitation-retained.json", "active", 7, ""),
    ],
)
def test_refresh_recovery_phase_rejects_incomplete_or_changed_prior_binding(
    environment, bundle_name: str | None, prior_status: str, generation: int, fingerprint: str,
) -> None:
    """Only an untouched active prior plus its retained invitation may refresh."""
    data = json.loads(Path(environment["manifest_path"]).read_text())
    prior = _installation_status(prior_status, 7, ENROLLED, FINGERPRINT if prior_status == "active" else "")
    extend_module._write_recovery(
        data, "dashboard", sorted(DECLARED), "refresh-revoke-pending", bundle_name,
        prior, generation, fingerprint,
    )
    with pytest.raises(extend_module.ExtendError, match="refresh binding is invalid"):
        extend_module._read_recovery(data, "dashboard", sorted(DECLARED))


def test_recovery_does_not_reapprove_an_already_active_installation_after_a_lost_response(
    environment, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approval is non-idempotent, so the exact active status completes it."""
    data = json.loads(Path(environment["manifest_path"]).read_text())
    prior = _installation_status("active", 1, ENROLLED, FINGERPRINT)
    extend_module._write_recovery(
        data, "dashboard", sorted(DECLARED), "approve-pending", prior=prior,
        generation=3, fingerprint=FINGERPRINT,
    )
    monkeypatch.setattr(
        extend_module, "_installation_status",
        lambda *_args: _installation_status("active", 3, DECLARED, FINGERPRINT),
    )

    recovery = extend_module._resolve_recovery_phase(
        data, environment["manifest_path"], "dashboard", sorted(DECLARED), runner,
    )

    assert recovery["phase"] == "approved"
    assert recovery["generation"] == 3


def test_recovery_holds_when_the_authority_status_does_not_match_the_retained_operation(
    environment, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same resources are insufficient evidence when epoch or generation drifts."""
    data = json.loads(Path(environment["manifest_path"]).read_text())
    prior = _installation_status("active", 1, ENROLLED, FINGERPRINT)
    extend_module._write_recovery(
        data, "dashboard", sorted(DECLARED), "revoke-pending", prior=prior,
        generation=1, fingerprint=FINGERPRINT,
    )
    drifted = _installation_status("revoked", 2, ENROLLED, FINGERPRINT)
    drifted["epoch"] = "b" * 64
    monkeypatch.setattr(extend_module, "_installation_status", lambda *_args: drifted)
    monkeypatch.setattr(extend_module, "_revoke", lambda *_args: pytest.fail("must not mutate on drift"))

    with pytest.raises(extend_module.ExtendError, match="cannot prove"):
        extend_module._resolve_recovery_phase(
            data, environment["manifest_path"], "dashboard", sorted(DECLARED), runner,
        )


def test_stale_revoked_recovery_cannot_invite_against_a_newer_installation(
    environment, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retained revoked phase must not widen an unrelated newer authority."""
    data = json.loads(Path(environment["manifest_path"]).read_text())
    prior = _installation_status("active", 1, ENROLLED, FINGERPRINT)
    extend_module._write_recovery(
        data, "dashboard", sorted(DECLARED), "revoked", prior=prior,
        generation=2, fingerprint=FINGERPRINT,
    )
    monkeypatch.setattr(
        extend_module, "_installation_status",
        lambda *_args: _installation_status("revoked", 3, ENROLLED, FINGERPRINT),
    )
    monkeypatch.setattr(extend_module, "_invite", lambda *_args: pytest.fail("must not invite on drift"))

    with pytest.raises(extend_module.ExtendError, match="cannot prove"):
        extend_module._resume_enrollment(
            data, environment["manifest_path"], environment["target"], "connector.service",
            (True, "enabled"), sorted(DECLARED), runner, environment["tmp"] / "systemd",
        )
    assert extend_module._read_recovery(data, "dashboard", sorted(DECLARED)) is not None


def test_stale_approved_recovery_cannot_start_or_clear_after_gateway_drift(
    environment, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completion needs current active authority, not only a local identity."""
    data = json.loads(Path(environment["manifest_path"]).read_text())
    prior = _installation_status("active", 1, ENROLLED, FINGERPRINT)
    extend_module._write_recovery(
        data, "dashboard", sorted(DECLARED), "approved", prior=prior,
        generation=3, fingerprint=FINGERPRINT,
    )
    monkeypatch.setattr(
        extend_module, "_installation_status",
        lambda *_args: _installation_status("active", 4, DECLARED, FINGERPRINT),
    )
    monkeypatch.setattr(
        extend_module, "_apply_unit_lifecycle", lambda *_args, **_kwargs: pytest.fail("must not start on drift"),
    )

    with pytest.raises(extend_module.ExtendError, match="cannot prove"):
        extend_module._resume_enrollment(
            data, environment["manifest_path"], environment["target"], "connector.service",
            (True, "enabled"), sorted(DECLARED), runner, environment["tmp"] / "systemd",
        )
    assert extend_module._read_recovery(data, "dashboard", sorted(DECLARED)) is not None


def test_rollback_restores_disabled_units_to_the_disabled_state(
    environment, runner: FakeRunner, enrollment_log, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed extension never leaves a previously disabled unit enabled."""
    runner.states["anvil-connect-authelia.service"] = "inactive"
    runner.file_states["anvil-connect-authelia.service"] = "disabled"

    def failing_ready(*args: object, **kwargs: object) -> None:
        raise extend_module.ExtendError("gateway did not become ready")

    monkeypatch.setattr(extend_module.manage, "_gateway_ready", failing_ready)
    monkeypatch.setattr(extend_module.manage, "_started_units", lambda *a, **k: None)
    with pytest.raises(extend_module.ExtendError, match="no enrollment changes were committed"):
        extend_module.extend(
            environment["manifest_path"], environment["target"],
            confirm=True, runner=runner, unit_root=environment["tmp"] / "systemd")
    authelia_calls = [
        argv for argv in runner.calls
        if "anvil-connect-authelia.service" in argv and argv[1] in {"disable", "stop", "start", "restart"}
    ]
    verbs = [argv[1] for argv in authelia_calls]
    assert "disable" in verbs  # the boot-persistence was undone, not just stopped
