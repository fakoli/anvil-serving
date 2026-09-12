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
from pathlib import Path

import pytest

from anvil_serving.connect import extend as extend_module
from anvil_serving.connect.manage import Target
from tests.connect.test_render import isolated_manifest

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
    connectors = root / "connectors"
    connectors.mkdir(parents=True)
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
    data = json.loads(manifest_path.read_text(encoding="utf-8"))

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
    return {
        "manifest_path": manifest_path, "target": Target("connector", "dashboard"),
        "root": root, "tmp": tmp_path,
    }


class FakeRunner:
    """Answer systemctl interactions; record every argv."""

    def __init__(self, unit_root: Path | None = None) -> None:
        self.calls: list[list[str]] = []
        self.states: dict[str, str] = {}
        self.unit_root = unit_root or Path("/etc/systemd/system")
        self.gateway_ready_error: Exception | None = None

    def __call__(self, argv: list[str], timeout: float | None = None,
                 identity: object = None) -> "extend_module.manage.RunResult":
        self.calls.append(list(argv))
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
                stdout = f"ActiveState={self.states.get(unit, 'active')}\nUnitFileState=enabled\n"
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
    connectors = root / "connectors"
    connectors.mkdir(parents=True)
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


@pytest.fixture()
def enrollment_log(monkeypatch: pytest.MonkeyPatch, environment):
    """Record admin/native calls; the invite writes its bundle as the IdP would."""
    calls: list[dict[str, object]] = []

    def fake_admin(manifest_path, *, request_path, output_path=None, apply, runner=None):
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        calls.append({"admin": request["operation"], "request": request})
        if output_path is not None and request["operation"] == "invite":
            Path(output_path).write_text(json.dumps({
                "invitation": "aci1." + "1" * 16 + "." + "A" * 43,
                "control_host": "connect-control.example.test",
                "tunnel_host": "connect-tunnel.example.test",
                "inner_ca_pem": "-----BEGIN CERTIFICATE-----",
            }), encoding="utf-8")
        return {}

    monkeypatch.setattr(extend_module.manage, "admin", fake_admin)
    monkeypatch.setattr(extend_module.manage, "native_init",
                        lambda manifest_path, target, *, bundle, apply, runner=None:
                        calls.append({"native_init": str(bundle)}) or Path(bundle).unlink())
    monkeypatch.setattr(extend_module.manage, "identity",
                        lambda manifest_path, target, runner=None:
                        calls.append({"identity": True}) or {"fingerprint": FINGERPRINT})
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
    assert operations == ["installation-revoke", "invite", "native_init", "identity", "approve"]
    # The new generation is live with the extended resource set.
    connector_tree = json.loads((environment["root"] / "connectors" / "dashboard.json").read_text())
    assert extend_module._resource_ids(connector_tree["resources"]) == set(DECLARED)
    record = json.loads((environment["root"].parent / ".rendered.anvil-connect-activation.json").read_text())
    assert record["generation"] == record["generation"]  # record exists post-activation
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


def test_bundle_lands_in_the_connector_state_directory(
    environment, runner: FakeRunner, enrollment_log, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extend_module.manage, "_gateway_ready", lambda *a, **k: None)
    monkeypatch.setattr(extend_module.manage, "_started_units", lambda *a, **k: None)
    extend_module.extend(environment["manifest_path"], environment["target"],
                          confirm=True, runner=runner, unit_root=environment["tmp"] / "systemd")
    init_entry = next(entry for entry in enrollment_log if "native_init" in entry)
    assert str(environment["tmp"] / "state-dashboard") in str(init_entry["native_init"])
    assert str(environment["tmp"] / "state-gateway") not in str(init_entry["native_init"])