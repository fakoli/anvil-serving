"""A protected installed profile is required before controller owner tools exist."""

import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from anvil_serving.control_plane.controller.propagation_bootstrap import build_propagation_service
from anvil_serving.control_plane.controller.propagation_job_store import ExecutionProfile
from anvil_serving.control_plane.controller.errors import ControllerError
from anvil_serving.control_plane.controller import cli as controller_cli
from anvil_serving.control_plane.controller import server as controller_server
from tests.test_propagation_contracts import _contract
from anvil_serving.control_plane.propagation import parse_contract
from tests.test_controller import _authorization_policy, _request, running_controller


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="the owner bootstrap runs on Linux")


def _write(path, raw):
    path.write_bytes(raw)
    path.chmod(0o600)
    return str(path)


def _profile(tmp_path, *, mode="effects"):
    ledger = tmp_path / "owner.sqlite3"
    _write(ledger, b"")
    approved = tmp_path / "approved.json"
    canonical = parse_contract(_contract(authority_mode=mode)).canonical
    _write(approved, canonical)
    script = tmp_path / "fixed.py"
    script.write_text(
        "import json,sys\nr=json.load(sys.stdin)\n"
        "print(json.dumps({'schema':r['schema'],'profile_id':r['profile_id'],"
        "'contract_digest':r['contract_digest'],'modes':"
        "['preview','status','verify','convergence','recovery','reconcile']}))\n")
    script.chmod(0o644)
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    system_python = "/usr/bin/python3"
    python_digest = hashlib.sha256(Path(system_python).read_bytes()).hexdigest()
    execution = ExecutionProfile("profile-1", "a" * 64, (system_python, str(script)),
        python_digest, artifact_pins=((str(script), digest),), cwd=str(tmp_path))
    readback = ExecutionProfile("reader-1", "b" * 64, (system_python, str(script)),
        python_digest, artifact_pins=((str(script), digest),), cwd=str(tmp_path), budget_seconds=5)
    token = tmp_path / "control-token"
    _write(token, ("c" * 64 + "\n").encode())
    value = {"schema": "anvil-serving.propagation-owner/v1", "mode": mode,
             "ledger_path": str(ledger),
             "approved_contract_path": str(approved),
             "approved_contract_sha256": hashlib.sha256(canonical).hexdigest(),
             "approval_ref": "approval-1", "execution_profile": execution.private_value(),
             "readback_profile": readback.private_value(), "executor_issuer": "executor-1",
             "activation": {"activation_ref": "activation-1", "catalog_sha256": "a" * 64,
                            "owners": [{"container_id": "d" * 64, "runtime_digest": "e" * 64,
                                        "served_identity": "model-exact", "bound_port": 9123}],
                            "router_base_url": "http://127.0.0.1:8000/v1",
                            "router_token_env": "SYNTHETIC_ROUTER_TOKEN"},
             "workflow_control": {"socket_path": str(tmp_path / "worker.sock"),
                                  "token_file": str(token), "expected_peer_uid": os.getuid(),
                                  "expected_peer_gid": os.getgid()}}
    path = tmp_path / "owner-profile.json"
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    _write(path, raw)
    return path, value, hashlib.sha256(raw).hexdigest()


def test_bootstrap_builds_real_owner_only_from_protected_pins(tmp_path):
    path, value, digest = _profile(tmp_path)
    service = build_propagation_service(str(path), digest)
    assert service.profile.profile_id == "profile-1"
    assert service.profile.mode == "effects"
    assert service.profile.owner_profile_digest == digest
    assert service.jobs.profiles["profile-1"].digest == "a" * 64
    assert service.profile.contract_lookup("approval-1") == parse_contract(_contract()).canonical
    assert controller_cli._build_parser().parse_args([
        "serve", "--propagation-profile", str(path),
        "--propagation-profile-sha256", digest,
    ]).propagation_profile == str(path)
    assert value["executor_issuer"] == service.profile.executor_issuer


def test_serve_constructs_owner_before_advertising_tools(tmp_path, monkeypatch):
    path, _, digest = _profile(tmp_path)
    seen = []
    class FakeServer:
        server_address = ("127.0.0.1", 0)
    def factory(**kwargs):
        seen.append(kwargs)
        return FakeServer()
    monkeypatch.setattr(controller_server, "serve_until_signal", lambda *_args, **_kwargs: None)
    assert controller_server.serve(propagation_profile_path=str(path),
        propagation_profile_sha256=digest, server_factory=factory) == 0
    assert seen[0]["propagation_service"].profile.profile_id == "profile-1"
    seen.clear()
    with pytest.raises(ControllerError, match="protected propagation owner profile is unavailable"):
        controller_server.serve(propagation_profile_path=str(path), server_factory=factory)
    assert not seen


def test_owner_tools_require_a_valid_scoped_policy(tmp_path):
    path, _, digest = _profile(tmp_path)
    service = build_propagation_service(str(path), digest)
    with pytest.raises(ControllerError, match="scoped propagation authorization is unavailable"):
        controller_server.make_server("127.0.0.1", 0, propagation_service=service,
            allow_unauthenticated_loopback=True, env={},
            idempotency_db_path=str(tmp_path / "operations.sqlite3"))


def test_bootstrapped_owner_advertises_only_with_scoped_controller(tmp_path):
    path, _, digest = _profile(tmp_path)
    service = build_propagation_service(str(path), digest)
    policy = _authorization_policy(tmp_path, [
        {"id": "reader", "scopes": ["propagation:status"], "credential_env": "READER"},
    ])
    with running_controller(propagation_service=service, authorization_policy=policy,
                            env={"ANVIL_CONTROLLER_TOKEN": "synthetic-legacy", "READER": "synthetic-reader"}) as (host, port):
        status, _, body, _ = _request(host, port, "GET", "/tools/list",
                                      headers={"Authorization": "Bearer synthetic-reader"})
        assert status == 200
        assert any(tool["name"] == "propagation.profile.v1" for tool in body["tools"])


def test_preview_bootstrap_advertises_only_read_operations(tmp_path):
    path, _, digest = _profile(tmp_path, mode="preview")
    service = build_propagation_service(str(path), digest)
    policy = _authorization_policy(tmp_path, [
        {"id": "operator", "scopes": [
            "propagation:status", "propagation:recovery", "propagation:admission",
            "propagation:dispatch", "propagation:activity",
        ], "credential_env": "OPERATOR"},
    ])
    with running_controller(propagation_service=service, authorization_policy=policy,
                            env={"ANVIL_CONTROLLER_TOKEN": "synthetic-legacy",
                                 "OPERATOR": "synthetic-operator"}) as (host, port):
        status, _, body, _ = _request(host, port, "GET", "/tools/list",
                                      headers={"Authorization": "Bearer synthetic-operator"})
        assert status == 200
        names = {tool["name"] for tool in body["tools"]}
        assert {"propagation_capabilities", "propagation.profile.v1",
                "propagation.preview.v1", "propagation.recovery.verify.v1"} <= names
        assert "propagation.accept.v1" not in names
        assert "fleet.propagation.submit.v1" not in names
        status, _, body, _ = _request(host, port, "POST", "/tools/call",
            {"name": "propagation_capabilities", "arguments": {}},
            {"Authorization": "Bearer synthetic-operator"})
        assert status == 200 and body["ok"]
        available = {item["name"]: item["available"]
                     for item in body["data"]["operations"]}
        assert available["propagation.preview.v1"] is True
        assert available["propagation.accept.v1"] is False
        assert available["fleet.propagation.submit.v1"] is False
        assert service.profile.preview_contract == parse_contract(
            _contract(authority_mode="preview")).canonical


def test_bootstrap_refuses_pinned_reader_without_required_modes(tmp_path):
    path, value, _ = _profile(tmp_path)
    script = Path(value["readback_profile"]["argv"][1])
    script.write_text("print('{}')\n", encoding="utf-8")
    script.chmod(0o644)
    changed = hashlib.sha256(script.read_bytes()).hexdigest()
    for name in ("execution_profile", "readback_profile"):
        value[name]["artifact_pins"][0] = [str(script), changed]
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    _write(path, raw)
    with pytest.raises(ControllerError, match="protected propagation owner profile is unavailable"):
        build_propagation_service(str(path), hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize("damage", ["wrong-pin", "readable-profile", "wrong-contract", "missing-ledger", "changed-executable", "writable-artifact", "readable-ledger", "exposed-ledger-parent", "duplicate-key"])
def test_bootstrap_refuses_incomplete_or_changed_authority(tmp_path, damage):
    path, value, digest = _profile(tmp_path)
    if damage == "wrong-pin":
        digest = "0" * 64
    elif damage == "readable-profile":
        path.chmod(0o644)
    elif damage == "wrong-contract":
        Path(value["approved_contract_path"]).write_bytes(b"{}")
    elif damage == "missing-ledger":
        Path(value["ledger_path"]).unlink()
    elif damage == "changed-executable":
        Path(value["execution_profile"]["argv"][1]).write_text("print('changed')\n")
    elif damage == "writable-artifact":
        Path(value["execution_profile"]["argv"][1]).chmod(0o664)
    elif damage == "readable-ledger":
        Path(value["ledger_path"]).chmod(0o644)
    elif damage == "exposed-ledger-parent":
        tmp_path.chmod(0o755)
    else:
        raw = path.read_bytes().replace(b'"schema":', b'"schema":"duplicate","schema":', 1)
        _write(path, raw)
        digest = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ControllerError, match="protected propagation owner profile is unavailable"):
        build_propagation_service(str(path), digest)
