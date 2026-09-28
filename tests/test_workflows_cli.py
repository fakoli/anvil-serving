"""Operator projection uses owner custody even when Temporal is unavailable."""

import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from anvil_serving import workflows_cli
from anvil_serving.commands import COMMAND_TREE
from anvil_serving.control_plane.propagation import capability_declaration
from anvil_serving.control_plane.propagation_jobs import PropagationJobError
from tests.test_propagation_contracts import _matches_declared_schema
from tests.test_propagation_jobs import ControlledOwner
from tests.test_controller import _authorization_policy, _request, running_controller


@pytest.mark.skipif(sys.platform != "linux", reason="release owner is Linux")
def test_owner_status_keeps_every_target_without_temporal(tmp_path):
    owner = ControlledOwner(tmp_path, count=2)
    profile = owner.service.handle("propagation.profile.v1", {}, caller_id="reader")
    profile_schema = next(op for op in capability_declaration()["operations"]
                          if op["name"] == "propagation.profile.v1")["result_schema"]
    assert _matches_declared_schema(profile, profile_schema)
    assert profile["installed"] and profile["profile_digest"] == owner.profile.profile_digest
    accepted = owner.accept()
    args = {"intent_id": accepted["intent_id"], "cursor": None}
    pending = owner.service.handle("propagation.status.v1", args, caller_id="reader")
    declaration = next(op for op in capability_declaration()["operations"]
                       if op["name"] == "propagation.status.v1")
    assert _matches_declared_schema(pending, declaration["result_schema"])
    assert pending["state"] == "pending" and pending["workflow_progress"] == "unavailable"
    assert pending["workflow_observed_at"] is None and pending["workflow_age_seconds"] is None
    assert pending["target_count"] == len(pending["targets"]) == 2
    assert all(row["pending_reason"] == "not-started" and row["freshness"] == "unknown"
               for row in pending["targets"])
    assert not pending["all_targets_verified"]

    _, job, _ = owner.submit()
    active = owner.service.handle("propagation.status.v1", args, caller_id="reader")
    assert _matches_declared_schema(active, declaration["result_schema"])
    assert active["job_id"] == job["job_id"] and active["state"] == "running"
    assert active["workflow_progress"] == "unavailable"
    assert [row["target_id"] for row in active["targets"]] == [row["target_id"] for row in pending["targets"]]
    owner.service.verify(accepted["intent_id"], job["job_id"])
    complete = owner.service.handle("propagation.status.v1", args, caller_id="reader")
    assert complete["state"] == "completed" and complete["all_targets_verified"]
    owner.service.profile = replace(owner.profile, workflow_status=lambda workflow_id, digest: {
        "state": "running", "observed_at": datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")})
    live = owner.service.handle("propagation.status.v1", args, caller_id="reader")
    assert live["workflow_progress"] == "running" and live["workflow_age_seconds"] is not None
    def unavailable(*_):
        raise TimeoutError()
    owner.service.profile = replace(owner.service.profile, workflow_status=unavailable)
    assert owner.service.handle("propagation.status.v1", args, caller_id="reader")["workflow_progress"] == "unavailable"


def test_short_commands_require_exact_identity_and_confirmation():
    assert next(node for node in COMMAND_TREE.nodes if node.name == "workflows").children
    assert workflows_cli._arguments(["start", "--approval-ref", "approved-1", "--request-id", "retry-1", "--confirm"]) == (
        "start", {"approval_ref": "approved-1", "request_id": "retry-1"})
    for argv in (["start", "--approval-ref", "approved-1", "--request-id", "retry-1"],
                 ["status", "--intent-id", "../untrusted"],
                 ["cancel", "--intent-id", "intent-1", "--expected-digest", "wrong", "--confirm"]):
        with pytest.raises((workflows_cli.SafetyError, workflows_cli.UsageError)):
            workflows_cli._arguments(argv)


def test_start_uses_bounded_authenticated_owner_operation(monkeypatch):
    seen = []
    monkeypatch.setattr(workflows_cli, "_config", lambda: ("https://127.0.0.1:8765", "synthetic-token"))
    def request(url, body, token, **options):
        seen.append((url, body, token, options))
        return {"result": {"structuredContent": {"ok": True, "data": {
            "intent_id": "intent-1", "workflow_id": "workflow-1", "contract_digest": "a" * 64}}}}
    monkeypatch.setattr(workflows_cli, "remote_controller_request", request)
    result = workflows_cli.main(["start", "--approval-ref", "approved-1", "--request-id", "retry-1", "--confirm"])
    assert result.exit_code == 0 and result.data["intent_id"] == "intent-1"
    assert len(seen) == 1 and seen[0][1]["params"]["name"] == "propagation.accept.v1"
    assert seen[0][1]["params"]["arguments"] == {"approval_ref": "approved-1", "request_id": "retry-1"}
    assert seen[0][3]["timeout"] == 15 and seen[0][3]["max_response_bytes"] == 131_072


def test_capability_preflight_requires_exact_available_owner_contract(monkeypatch):
    monkeypatch.setattr(workflows_cli, "_config", lambda: ("https://127.0.0.1:8765", "synthetic-token"))
    declaration = capability_declaration()
    for operation in declaration["operations"]:
        operation["available"] = True
    calls = []
    def request(_url, body, _token, **_options):
        calls.append(body["params"])
        return {"result": {"structuredContent": {"ok": True, "data": declaration}}}
    monkeypatch.setattr(workflows_cli, "remote_controller_request", request)
    ready = workflows_cli.main(["capabilities"])
    assert ready.exit_code == 0 and ready.data["state"] == "ready"
    assert len(ready.data["operations"]) == 14 and ready.data["effects"] == []
    assert calls == [{"name": "propagation_capabilities", "arguments": {},
                      "_meta": {"io.modelcontextprotocol/protocolVersion": workflows_cli.mcp.PROTOCOL_VERSION}}]
    declaration["operations"][0]["available"] = False
    refused = workflows_cli.main(["capabilities"])
    assert refused.exit_code != 0 and refused.error.code == "workflow_owner_capabilities_missing"


@pytest.mark.skipif(sys.platform != "linux", reason="release owner is Linux")
def test_local_release_preview_checks_exact_bytes_without_effects(tmp_path, monkeypatch):
    artifact = tmp_path / "worker.whl"
    artifact.write_bytes(b"synthetic release")
    manifest = {"schema": "anvil-workflows.release/v1", "profile_id": "propagation-v1",
                "profile_digest": "a" * 64, "components": [{"name": "worker", "file": artifact.name,
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
    raw = json.dumps(manifest).encode()
    (tmp_path / "manifest.json").write_bytes(raw)
    monkeypatch.setattr(workflows_cli, "_read_config", lambda: {
        "release_dir": str(tmp_path), "release_digest": hashlib.sha256(raw).hexdigest()})
    preview = workflows_cli.main(["deployment", "preview", "--profile", "propagation-v1"])
    assert preview.exit_code == 0 and preview.data["effects"] == []
    assert preview.data["components"][0]["bytes"] == len(artifact.read_bytes())
    artifact.write_bytes(b"drift")
    refused = workflows_cli.main(["deployment", "preview", "--profile", "propagation-v1"])
    assert refused.exit_code != 0 and refused.error.code == "workflow_release_unavailable"


def test_deployed_profile_verify_requires_exact_installed_digest(monkeypatch):
    preview = {"profile_id": "propagation-v1", "profile_digest": "a" * 64,
               "release_digest": "b" * 64, "components": [], "effects": []}
    monkeypatch.setattr(workflows_cli, "_local_preview", lambda _: preview)
    monkeypatch.setattr(workflows_cli, "_config", lambda: ("owner", "token"))
    monkeypatch.setattr(workflows_cli, "_call", lambda *_: {
        "profile_id": "propagation-v1", "profile_digest": "c" * 64,
        "installed": True, "observed_at": "2026-09-28T05:00:00Z"})
    result = workflows_cli.main(["deployment", "verify", "--profile", "propagation-v1"])
    assert result.exit_code != 0 and result.data["state"] == "drift"
    monkeypatch.setattr(workflows_cli, "_call", lambda *_: {
        "profile_id": "propagation-v1", "profile_digest": "a" * 64,
        "installed": True, "observed_at": "2026-09-28T05:00:00Z"})
    assert workflows_cli.main(["deployment", "verify", "--profile", "propagation-v1"]).data["state"] == "matched"


@pytest.mark.parametrize(("action", "state"), [
    ("resume", "refused"), ("cancel", "uncertain"),
    ("recovery", "failed"), ("recovery", "recovery_required"),
])
def test_attention_outcomes_exit_nonzero_with_owner_data(monkeypatch, action, state):
    monkeypatch.setattr(workflows_cli, "_config", lambda **_: ("owner", "token"))
    monkeypatch.setattr(workflows_cli, "_call", lambda *_: {"state": state})
    argv = (["recovery", "verify", "--profile", "propagation-v1"] if action == "recovery"
            else [action, "--intent-id", "intent-1", "--expected-digest", "a" * 64, "--confirm"])
    result = workflows_cli.main(argv)
    assert result.exit_code != 0 and result.data["state"] == state


@pytest.mark.skipif(sys.platform != "linux", reason="release owner is Linux")
def test_status_refuses_missing_or_mixed_owner_pages(tmp_path, monkeypatch):
    owner = ControlledOwner(tmp_path, count=9)
    accepted = owner.accept()
    first = owner.service.handle("propagation.status.v1", {"intent_id": accepted["intent_id"], "cursor": None}, caller_id="reader")
    assert first["next_cursor"] is not None
    second = owner.service.handle("propagation.status.v1", {"intent_id": accepted["intent_id"], "cursor": first["next_cursor"]}, caller_id="reader")
    pages = iter((first, second))
    monkeypatch.setattr(workflows_cli, "_call", lambda *_: next(pages))
    combined = workflows_cli._status("owner", "token", {"intent_id": accepted["intent_id"], "cursor": None})
    assert combined["target_count"] == len(combined["targets"]) == 9
    pages = iter((first, {**second, "workflow_id": "wrong"}))
    monkeypatch.setattr(workflows_cli, "_call", lambda *_: next(pages))
    with pytest.raises(workflows_cli.SafetyError, match="pages disagree"):
        workflows_cli._status("owner", "token", {"intent_id": accepted["intent_id"], "cursor": None})
    monkeypatch.setattr(workflows_cli, "_call", lambda *_: {**first, "next_cursor": None})
    with pytest.raises(workflows_cli.SafetyError, match="incomplete"):
        workflows_cli._status("owner", "token", {"intent_id": accepted["intent_id"], "cursor": None})


@pytest.mark.skipif(sys.platform != "linux", reason="release owner is Linux")
def test_resume_and_cancel_keep_owner_authority_and_original_identity(tmp_path):
    owner = ControlledOwner(tmp_path)
    accepted = owner.accept()
    args = {"intent_id": accepted["intent_id"], "expected_digest": accepted["contract_digest"]}
    with pytest.raises(PropagationJobError, match="workflow_service_unavailable"):
        owner.service.handle("propagation.resume.v1", args, caller_id="operator")
    seen = []
    owner.service.profile = replace(owner.profile,
        resume_workflow=lambda *identity: seen.append(("resume", identity)) or {
            "attempt_id": "attempt-1", "state": "accepted"},
        cancel_workflow=lambda *identity: seen.append(("cancel", identity)) or {
            "state": "requested"})
    assert owner.service.handle("propagation.resume.v1", args, caller_id="operator") == {
        "attempt_id": "attempt-1", "state": "accepted"}
    assert owner.service.handle("propagation.cancel.v1", args, caller_id="operator") == {
        "state": "requested"}
    assert seen == [(verb, (accepted["intent_id"], accepted["contract_digest"], "operator"))
                    for verb in ("resume", "cancel")]
    with pytest.raises(PropagationJobError, match="intent_conflict"):
        owner.service.handle("propagation.resume.v1", {**args, "expected_digest": "a" * 64}, caller_id="operator")
    owner.service.profile = replace(owner.service.profile,
        now=lambda: datetime.now(timezone.utc) + timedelta(days=2))
    with pytest.raises(PropagationJobError):
        owner.service.handle("propagation.resume.v1", args, caller_id="operator")
    assert owner.service.handle("propagation.cancel.v1", args, caller_id="operator")["state"] == "requested"


@pytest.mark.skipif(sys.platform != "linux", reason="release owner is Linux")
def test_recovery_verification_requires_a_bound_isolated_owner(tmp_path):
    owner = ControlledOwner(tmp_path)
    args = {"profile_id": "profile-1"}
    with pytest.raises(PropagationJobError, match="recovery_verifier_unavailable"):
        owner.service.handle("propagation.recovery.verify.v1", args, caller_id="recovery-operator")
    now = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    owner.service.profile = replace(owner.profile, recovery_evidence=lambda profile, caller: {
        "profile_id": profile, "state": "passed", "evidence_digest": "a" * 64, "verified_at": now})
    result = owner.service.handle("propagation.recovery.verify.v1", args, caller_id="recovery-operator")
    assert result["state"] == "passed" and result["evidence_digest"] == "a" * 64
    with pytest.raises(PropagationJobError, match="profile_mismatch"):
        owner.service.handle("propagation.recovery.verify.v1", {"profile_id": "other"}, caller_id="recovery-operator")


@pytest.mark.skipif(sys.platform != "linux", reason="release owner is Linux")
def test_recovery_scope_is_separate_from_admission_activity_and_status(tmp_path):
    owner = ControlledOwner(tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    owner.service.profile = replace(owner.profile, recovery_evidence=lambda profile, caller: {
        "profile_id": profile, "state": "passed", "evidence_digest": "a" * 64, "verified_at": now})
    rows = [{"id": name, "scopes": ["propagation:" + name], "credential_env": name.upper()}
            for name in ("admission", "activity", "status", "recovery")]
    policy = _authorization_policy(tmp_path, rows)
    env = {name.upper(): "synthetic-" + name for name in ("admission", "activity", "status", "recovery")}
    env["ANVIL_CONTROLLER_TOKEN"] = "synthetic-legacy-controller"
    with running_controller(env=env, authorization_policy=policy, propagation_service=owner.service) as (host, port):
        for role in ("admission", "activity", "status", "recovery"):
            _, _, body, _ = _request(host, port, "POST", "/mcp", {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "propagation.recovery.verify.v1", "arguments": {"profile_id": "profile-1"}}},
                {"Authorization": "Bearer " + env[role.upper()]})
            content = body.get("result", {}).get("structuredContent", {})
            assert (content.get("ok") is True) == (role == "recovery")
