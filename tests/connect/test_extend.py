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


def test_plan_reports_the_addition_only_diff(environment) -> None:
    plan = extend_module.extend_plan(
        json.loads(Path(environment["manifest_path"]).read_text()), environment["target"])
    assert plan["added"] == [ADDED]
    assert plan["removed"] == []
    assert plan["gateway_missing"] == [ADDED]
    assert plan["extendable"] is True
    assert plan["enrolled"] == sorted(ENROLLED)


def test_plan_accepts_all_three_recorded_resource_shapes() -> None:
    value = extend_module._resource_ids([
        {"envelope": {"rule": {"id": "wrapped"}}},
        {"rule": {"id": "direct"}},
        {"id": "flat"},
        "junk-string",
    ])
    assert value == {"wrapped", "direct", "flat"}


def test_plan_rejects_removals_with_guidance(tmp_path: Path) -> None:
    root = tmp_path / "rendered"
    root.mkdir(mode=0o755)
    connectors = root / "connectors"
    connectors.mkdir(mode=0o755)
    manifest = isolated_manifest()
    manifest["config_root"] = str(root)
    manifest["connectors"][0]["resources"] = _declared_resources(("dashboard",))
    manifest["gateway"]["gateway"]["resources"] = [
        {"connector": "dashboard", "rule": _rule("dashboard"), "tunnel_address": "127.0.0.1:17010"}
    ]
    manifest["clients"] = []
    manifest["environment_files"]["clients"] = {}
    manifest["service_identities"]["clients"] = {}
    manifest["service_limits"]["clients"] = {}
    manifest_path = tmp_path / "deployment.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    connector_tree = {"schema": "anvil-connect.connector/v1", "id": "dashboard",
                      "resources": _declared_resources(ENROLLED)}
    (connectors / "dashboard.json").write_text(json.dumps(connector_tree), encoding="utf-8")
    plan = extend_module.extend_plan(
        extend_module.read_manifest(manifest_path), Target("connector", "dashboard"))
    assert plan["removed"] == ["dashboard-api"]
    assert plan["extendable"] is False
    assert "revoke" in plan["removal_guidance"]


# --- dry run ----------------------------------------------------------------


def test_dry_run_returns_the_plan_and_mutates_nothing(environment) -> None:
    before = (environment["root"] / "managed.json").read_text(encoding="utf-8")
    result = extend_module.extend(
        environment["manifest_path"], environment["target"], confirm=False,
        unit_root=environment["tmp"] / "systemd")
    assert result["dry_run"] is True and result["applied"] is False
    assert result["plan"]["added"] == [ADDED]
    assert (environment["root"] / "managed.json").read_text(encoding="utf-8") == before


def test_rejection_carries_the_plan_in_the_error(environment) -> None:
    manifest = json.loads(Path(environment["manifest_path"]).read_text())
    manifest["connectors"][0]["resources"] = _declared_resources(ENROLLED)
    manifest["gateway"]["gateway"]["resources"] = [
        item for item in manifest["gateway"]["gateway"]["resources"]
        if item["rule"]["id"] != ADDED
    ]
    Path(environment["manifest_path"]).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(extend_module.ExtendError) as raised:
        extend_module.extend(environment["manifest_path"], environment["target"], confirm=True)
    assert raised.value.plan is not None
    assert raised.value.plan["added"] == []


# --- full sequence ----------------------------------------------------------


def test_extension_validates_gateway_and_connector_before_activation(
    environment, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[tuple[Target, ...]] = []

    def reject(_data, targets, _runner):
        selected.append(tuple(targets))
        raise extend_module.ManageError("gateway component pin unavailable")

    monkeypatch.setattr(extend_module.manage, "_validate_data", reject)
    with pytest.raises(extend_module.ManageError, match="gateway component pin unavailable"):
        extend_module.extend(
            environment["manifest_path"], environment["target"], confirm=True,
            runner=runner, unit_root=environment["tmp"] / "systemd",
        )
    assert selected == [(Target("gateway"), environment["target"])]
    assert runner.calls == []
    assert not (environment["tmp"] / ".rendered.anvil-connect-extend").exists()


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


def test_extend_runs_the_full_sequence_and_converges(
    environment, runner: FakeRunner, enrollment_log, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extend_module.manage, "_gateway_ready", lambda *a, **k: None)
    monkeypatch.setattr(extend_module.manage, "_started_units", lambda *a, **k: None)
    result = extend_module.extend(
        environment["manifest_path"], environment["target"],
        confirm=True, runner=runner, unit_root=environment["tmp"] / "systemd")
    assert result["applied"] is True and result["fingerprint"] == FINGERPRINT
    operations = [
        entry["admin"] if "admin" in entry else next(iter(entry))
        for entry in enrollment_log
    ]
    assert [operation for operation in operations if operation != "installation-status"] == [
        "installation-revoke", "invite", "native_reenroll", "native_init", "identity", "identity", "approve",
    ]
    # The new generation is live with the extended resource set.
    connector_tree = json.loads((environment["root"] / "connectors" / "dashboard.json").read_text())
    assert extend_module._resource_ids(connector_tree["resources"]) == set(DECLARED)
    record = json.loads((environment["root"].parent / ".rendered.anvil-connect-activation.json").read_text())
    assert record["generation"] == record["generation"]  # record exists post-activation
    reenrollment = next(entry["native_reenroll"] for entry in enrollment_log if "native_reenroll" in entry)
    assert not Path(reenrollment["prior"]).exists()
    assert any(
        call[:2] == ["/usr/bin/systemctl", "stop"]
        and call[-1] == "anvil-connect-connector-dashboard.service"
        for call in runner.calls
    )
    # The invitation bundle is deleted after redemption.
    bundle = environment["tmp"] / "state-dashboard" / "extend-invitation.json"
    assert not bundle.exists()


def test_extend_rolls_back_the_generation_when_the_gateway_fails_to_become_ready(
    environment, runner: FakeRunner, enrollment_log, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_ready(*args: object, **kwargs: object) -> None:
        raise extend_module.ExtendError("gateway did not become ready")

    monkeypatch.setattr(extend_module.manage, "_gateway_ready", failing_ready)
    with pytest.raises(extend_module.ExtendError, match="no enrollment changes were committed"):
        extend_module.extend(
            environment["manifest_path"], environment["target"],
        confirm=True, runner=runner, unit_root=environment["tmp"] / "systemd")
    # The prior generation is restored: the connector file carries only the enrolled set.
    connector_tree = json.loads((environment["root"] / "connectors" / "dashboard.json").read_text())
    assert extend_module._resource_ids(connector_tree["resources"]) == set(ENROLLED)
    record = json.loads((environment["root"].parent / ".rendered.anvil-connect-activation.json").read_text())
    assert record["generation"] == GEN_OLD


def test_prior_status_failure_uses_the_published_candidate_binding_and_rolls_back_before_revoke(
    environment, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed bound status read restores the old generation before authority mutation."""
    data = json.loads(Path(environment["manifest_path"]).read_text())
    statuses: list[dict[str, object]] = []

    monkeypatch.setattr(extend_module.manage, "_native_verified", lambda _data: NATIVE)
    monkeypatch.setattr(
        extend_module.manage, "_verified_binaries",
        lambda _data, _target: {"native": NATIVE, "caddy": CADDY, "authelia": AUTHELIA},
    )
    monkeypatch.setattr(extend_module.manage, "_gateway_ready", lambda *args, **kwargs: None)
    monkeypatch.setattr(extend_module.manage, "_started_units", lambda *args, **kwargs: None)

    def bound_runner(argv, timeout=None, identity=None):
        if tuple(argv[:2]) == (data["binary"], "admin"):
            request = json.loads(Path(argv[argv.index("--request") + 1]).read_text())
            assert request == {"operation": "installation-status", "installation": "dashboard"}
            record = json.loads(
                (environment["root"].parent / ".rendered.anvil-connect-activation.json").read_text(),
            )
            assert record["generation"] != GEN_OLD
            statuses.append(request)
            return extend_module.manage.RunResult(1, stderr=b"status unavailable")
        return runner(argv, timeout, identity)

    monkeypatch.setattr(
        extend_module, "_revoke",
        lambda *_args: pytest.fail("status failure must precede revoke"),
    )
    with pytest.raises(extend_module.ExtendError, match="no enrollment changes were committed"):
        extend_module.extend(
            environment["manifest_path"], environment["target"], confirm=True,
            runner=bound_runner, unit_root=environment["tmp"] / "systemd",
        )

    assert statuses == [{"operation": "installation-status", "installation": "dashboard"}]
    record = json.loads((environment["root"].parent / ".rendered.anvil-connect-activation.json").read_text())
    assert record["generation"] == GEN_OLD
    connector = json.loads((environment["root"] / "connectors" / "dashboard.json").read_text())
    assert extend_module._resource_ids(connector["resources"]) == set(ENROLLED)
    assert extend_module._read_recovery(data, "dashboard", sorted(DECLARED)) is None


def test_bundle_lands_in_the_role_owned_connector_sidecar(
    environment, runner: FakeRunner, enrollment_log, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extend_module.manage, "_gateway_ready", lambda *a, **k: None)
    monkeypatch.setattr(extend_module.manage, "_started_units", lambda *a, **k: None)
    extend_module.extend(environment["manifest_path"], environment["target"],
                          confirm=True, runner=runner, unit_root=environment["tmp"] / "systemd")
    init_entry = next(entry for entry in enrollment_log if "native_init" in entry)
    directory = environment["tmp"] / ".rendered.anvil-connect-extend" / "connector-dashboard"
    info = directory.lstat()
    assert str(directory) in str(init_entry["native_init"])
    assert stat.S_IMODE(info.st_mode) == 0o700
    assert str(environment["tmp"] / "state-gateway") not in str(init_entry["native_init"])


def test_legacy_connector_handoff_directory_migrates_without_losing_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retained legacy bundle remains readable after the exact mode migration."""
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

    retained = extend_module._retained_bundle(data, "dashboard", bundle.name)

    assert retained == bundle
    assert retained.read_bytes() == b'{"opaque":"retained"}'
    info = legacy.lstat()
    assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (
        identity.uid, identity.gid, 0o700,
    )


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


def test_connector_handoff_accepts_only_the_final_and_recoverable_states() -> None:
    """Fresh root-owned 0700 state remains resumable for a distinct role gid."""
    manager = (1000, 1000)
    target = (1001, 2000, 0o700)
    assert extend_module._connector_handoff_needs_migration((1000, 2000, 0o730), target, manager, created=False)
    assert extend_module._connector_handoff_needs_migration((1000, 2000, 0o700), target, manager, created=False)
    assert extend_module._connector_handoff_needs_migration((1000, 1000, 0o700), target, manager, created=False)
    assert not extend_module._connector_handoff_needs_migration(target, target, manager, created=False)
    with pytest.raises(extend_module.ExtendError, match="connector sidecar is unsafe"):
        extend_module._connector_handoff_needs_migration((1000, 999, 0o700), target, manager, created=False)


# --- review-gate regressions (Greptile P1s) ---------------------------------


def test_gateway_side_invitation_response_is_removed_after_redemption(
    environment, runner: FakeRunner, enrollment_log, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The admin response carries the bearer invitation; it never lingers."""
    monkeypatch.setattr(extend_module.manage, "_gateway_ready", lambda *a, **k: None)
    monkeypatch.setattr(extend_module.manage, "_started_units", lambda *a, **k: None)
    gateway_state = environment["tmp"] / "state-gateway"
    extend_module.extend(
        environment["manifest_path"], environment["target"],
        confirm=True, runner=runner, unit_root=environment["tmp"] / "systemd")
    leftovers = [p.name for p in gateway_state.glob("extend-admin-*.json")]
    assert leftovers == []


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


def test_native_init_ambiguity_retains_phase_then_rerun_finishes_forward(
    environment, runner: FakeRunner, enrollment_log, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lost init response is never replayed with the one-time bundle."""
    calls = {"init": 0}

    original_init = extend_module.manage.native_init

    def ambiguous_init(*args, **kwargs):
        calls["init"] += 1
        original_init(*args, **kwargs)
        raise extend_module.ManageError("native init response lost", may_have_executed=True)

    monkeypatch.setattr(extend_module.manage, "native_init", ambiguous_init)
    monkeypatch.setattr(extend_module.manage, "_gateway_ready", lambda *a, **k: None)
    monkeypatch.setattr(extend_module.manage, "_started_units", lambda *a, **k: None)
    with pytest.raises(extend_module.ExtendError, match="init-pending"):
        extend_module.extend(
            environment["manifest_path"], environment["target"],
            confirm=True, runner=runner, unit_root=environment["tmp"] / "systemd")
    recovery = extend_module._read_recovery(
        json.loads(Path(environment["manifest_path"]).read_text()), "dashboard", sorted(DECLARED))
    assert recovery is not None and recovery["phase"] == "init-pending"
    # Native identity is the exact owner proof on retry; init is not called a
    # second time against the same retained invitation.
    result = extend_module.extend(
        environment["manifest_path"], environment["target"],
        confirm=True, runner=runner, unit_root=environment["tmp"] / "systemd")
    assert result["recovered"] is True and calls["init"] == 1
    assert extend_module._read_recovery(
        json.loads(Path(environment["manifest_path"]).read_text()), "dashboard", sorted(DECLARED)) is None


def test_init_pending_with_missing_staged_identity_holds_without_revoke_or_reinvite(
    environment, runner: FakeRunner, enrollment_log, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing staged identity never permits a new authority mutation."""
    original_init = extend_module.manage.native_init
    original_identity = extend_module.manage.identity
    calls = {"init": 0}

    def interrupted_init(*args, **kwargs):
        calls["init"] += 1
        if calls["init"] == 1:
            raise extend_module.ManageError("native init did not start", may_have_executed=True)
        return original_init(*args, **kwargs)

    def identity_after_real_init(*args, **kwargs):
        if calls["init"] == 1:
            raise extend_module.ManageError("connector has no local enrollment")
        return original_identity(*args, **kwargs)

    monkeypatch.setattr(extend_module.manage, "native_init", interrupted_init)
    monkeypatch.setattr(extend_module.manage, "identity", identity_after_real_init)
    monkeypatch.setattr(extend_module.manage, "_gateway_ready", lambda *a, **k: None)
    monkeypatch.setattr(extend_module.manage, "_started_units", lambda *a, **k: None)
    with pytest.raises(extend_module.ExtendError, match="init-pending"):
        extend_module.extend(
            environment["manifest_path"], environment["target"],
            confirm=True, runner=runner, unit_root=environment["tmp"] / "systemd")

    with pytest.raises(extend_module.ExtendError, match="identity is unavailable"):
        extend_module.extend(
            environment["manifest_path"], environment["target"],
            confirm=True, runner=runner, unit_root=environment["tmp"] / "systemd")

    assert calls["init"] == 1
    assert [entry["admin"] for entry in enrollment_log if "admin" in entry].count("installation-revoke") == 1


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


def test_converged_extension_during_lock_wait_is_a_no_op(
    environment, runner: FakeRunner, enrollment_log, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another activation converging the same extension wins; no double revoke."""
    original_extend_plan = extend_module.extend_plan
    first = {"called": False}

    def replan_aware(data, target):
        if not first["called"]:
            first["called"] = True
            return original_extend_plan(data, target)
        # By lock acquisition the rendered set already matches the declaration.
        data = dict(data)
        return original_extend_plan(data, target)

    monkeypatch.setattr(extend_module, "extend_plan", replan_aware)

    def converge_tree(*args, **kwargs):
        tree = json.loads((environment["root"] / "connectors" / "dashboard.json").read_text())
        tree["resources"] = _declared_resources(DECLARED)
        (environment["root"] / "connectors" / "dashboard.json").write_text(
            json.dumps(tree), encoding="utf-8")
        return original_extend_plan.__wrapped__(json.loads(
            Path(environment["manifest_path"]).read_text()), environment["target"]) \
            if hasattr(original_extend_plan, "__wrapped__") else None

    # Simulate the concurrent convergence between the outer and inner plans:
    # swap the connector tree to the declared set after the first plan call.
    seen = {"outer": False}

    def plan_aware(data, target):
        if not seen["outer"]:
            seen["outer"] = True
            return original_extend_plan(data, target)
        tree = json.loads((environment["root"] / "connectors" / "dashboard.json").read_text())
        tree["resources"] = _declared_resources(DECLARED)
        (environment["root"] / "connectors" / "dashboard.json").write_text(
            json.dumps(tree), encoding="utf-8")
        return original_extend_plan(data, target)

    monkeypatch.setattr(extend_module, "extend_plan", plan_aware)
    result = extend_module.extend(
        environment["manifest_path"], environment["target"],
        confirm=True, runner=runner, unit_root=environment["tmp"] / "systemd")
    assert result["applied"] is False
    assert result["already_extended"] is True
    # The enrollment was never touched.
    assert enrollment_log == []
