"""Focused behavior checks for the inert propagation v1 value contracts."""

from datetime import datetime, timezone
import json
import re

import pytest

from anvil_serving import mcp
from anvil_serving.control_plane.propagation import (
    ActiveIdentity, ApprovedAuthority, MAX_CONTRACT_BYTES, PropagationContractError,
    ReceiptContext, ReceiptIdentity, admit_contract, authorize_receipt_lookup,
    capability_declaration, parse_contract, parse_receipt,
    effect_scope_digest,
)

_DIGEST = "a" * 64
_NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _contract(**changes):
    value = {"schema": "anvil-propagation/v1", "scope": "scope-1", "revision": "revision-1", "generation": 1, "approval_ref": "approval-1", "approval_digest": _DIGEST, "activation_ref": "activation-1", "activation_digest": _DIGEST, "inputs": {"catalog_digest": _DIGEST, "monitoring_inventory_digest": _DIGEST, "execution_profile_digest": _DIGEST, "artifact_digest": _DIGEST, "installation_inventory_digest": _DIGEST}, "effect_set_digest": _DIGEST, "targets": [{"target_id": "target-1", "installation_id": "installation-1", "profile_id": "profile-1", "runtime_id": "runtime-1", "resource_keys": ["catalog-1"], "expected_identity_ref": "identity-1", "expected_identity_digest": _DIGEST, "checks": ["catalog-equal"], "effects": ["catalog-apply"]}], "execution_profile_ref": "profile-1", "execution_profile_digest": _DIGEST, "session_policy": {"preserve_active_conversations": True, "loaded_state_required": True, "idle_reload": False}, "preview_policy": {"all_required_targets": True, "web_runtime_required": True, "monitoring_required": True}, "issued_at": "2026-09-27T12:00:00Z", "deadline_at": "2026-09-28T12:00:00Z"}
    value.update(changes)
    value["effect_set_digest"] = effect_scope_digest(value)
    return value


def _receipt(**changes):
    value = {"schema": "anvil-propagation/v1", "contract_digest": _DIGEST, "revision": "revision-1", "generation": 1, "job_id": "job-1", "effect_id": "effect-1", "target_id": "target-1", "installation_id": "installation-1", "profile_id": "profile-1", "runtime_id": "runtime-1", "before_digest": _DIGEST, "after_digest": _DIGEST, "applied": True, "verified": True, "observed_at": "2026-09-27T11:59:00Z", "check_set_digest": _DIGEST, "outcome": "success", "failure_code": None, "evidence_ref": "evidence-1", "evidence_digest": _DIGEST, "issuer": "executor-1"}
    value.update(changes)
    return value


def _context():
    return ReceiptContext(_DIGEST, "revision-1", 1, "job-1", "effect-1", "target-1", _DIGEST, ReceiptIdentity("installation-1", "profile-1", "runtime-1"))


def test_contract_is_immutable_after_caller_and_projection_mutation():
    caller = _contract()
    contract = parse_contract(caller)
    caller["targets"][0]["effects"][:] = ["session-idle-reload"]
    projection = contract.value
    projection["targets"][0]["effects"][:] = ["session-idle-reload"]
    assert contract.value["targets"][0]["effects"] == ["catalog-apply"]
    assert contract.digest == parse_contract(_contract()).digest


def test_effect_scope_digest_binds_authority_but_not_execution_window_observation():
    contract = _contract()
    refreshed_window = {**contract, "issued_at": "2026-09-27T13:00:00Z", "deadline_at": "2026-09-28T13:00:00Z"}
    assert effect_scope_digest(contract) == effect_scope_digest(refreshed_window)
    expanded_effect = _contract(targets=[{**contract["targets"][0], "effects": ["monitoring-apply"]}])
    assert effect_scope_digest(contract) != effect_scope_digest(expanded_effect)
    replacement_approval = _contract(approval_ref="approval-2", approval_digest="b" * 64)
    assert effect_scope_digest(contract) == effect_scope_digest(replacement_approval)
    assert parse_contract(contract).digest != parse_contract(replacement_approval).digest
    forged = dict(contract)
    forged["targets"] = [{**contract["targets"][0], "effects": ["monitoring-apply"]}]
    with pytest.raises(PropagationContractError, match="effect_scope_mismatch"):
        parse_contract(forged)


@pytest.mark.parametrize("raw, code", [
    ('{"schema":"anvil-propagation/v1","schema":"anvil-propagation/v1"}', "malformed_payload"),
    (json.dumps(_contract(generation=0)), "malformed_payload"),
    (json.dumps(_contract(generation=True)), "malformed_payload"),
    (json.dumps(_contract(targets=[])), "malformed_payload"),
    (json.dumps(_contract(unknown=True)), "malformed_payload"),
    (b"{" + b"x" * MAX_CONTRACT_BYTES + b"}", "payload_too_large"),
])
def test_contract_rejects_unapproved_shapes(raw, code):
    with pytest.raises(PropagationContractError, match=code):
        parse_contract(raw)


def test_admission_uses_only_owner_registry_and_observed_active_identity():
    parsed = parse_contract(_contract())
    approved = ApprovedAuthority("approval-1", _DIGEST, parsed.digest)
    assert admit_contract(_contract(), lambda _: approved, ActiveIdentity("activation-1", _DIGEST), _NOW).digest == parsed.digest
    with pytest.raises(PropagationContractError, match="malformed_payload"):
        admit_contract(_contract(approved=True), lambda _: approved, ActiveIdentity("activation-1", _DIGEST), _NOW)
    with pytest.raises(PropagationContractError, match="approval_mismatch"):
        admit_contract(_contract(), lambda _: approved, ActiveIdentity("other", _DIGEST), _NOW)
    with pytest.raises(PropagationContractError, match="approval_expired"):
        admit_contract(_contract(issued_at="2026-09-26T11:00:00Z", deadline_at="2026-09-27T11:00:00Z"), lambda _: approved, ActiveIdentity("activation-1", _DIGEST), _NOW)
    with pytest.raises(PropagationContractError, match="policy_mismatch"):
        admit_contract(_contract(targets=[{**_contract()["targets"][0], "effects": ["session-idle-reload"]}]), lambda _: approved, ActiveIdentity("activation-1", _DIGEST), _NOW)


def test_receipt_binds_context_transport_identity_and_full_lookup_identity():
    assert parse_receipt(_receipt(), context=_context(), authenticated_issuer="executor-1", now=_NOW)["verified"]
    with pytest.raises(PropagationContractError, match="receipt_identity_mismatch"):
        parse_receipt(_receipt(profile_id="other"), context=_context(), authenticated_issuer="executor-1", now=_NOW)
    with pytest.raises(PropagationContractError, match="receipt_identity_mismatch"):
        parse_receipt(_receipt(issuer="forged"), context=_context(), authenticated_issuer="executor-1", now=_NOW)
    with pytest.raises(PropagationContractError, match="stale_receipt"):
        parse_receipt(_receipt(observed_at="2026-09-27T11:54:00Z"), context=_context(), authenticated_issuer="executor-1", now=_NOW)
    with pytest.raises(PropagationContractError, match="receipt_outcome_mismatch"):
        parse_receipt(_receipt(outcome="failed", failure_code="verification-failed"), context=_context(), authenticated_issuer="executor-1", now=_NOW)
    with pytest.raises(PropagationContractError, match="malformed_payload"):
        parse_receipt(_receipt(outcome=[]), context=_context(), authenticated_issuer="executor-1", now=_NOW)
    with pytest.raises(PropagationContractError, match="malformed_payload"):
        parse_receipt(_receipt(observed_at="2026-09-27T11:59:00Z\n"), context=_context(), authenticated_issuer="executor-1", now=_NOW)
    with pytest.raises(PropagationContractError, match="receipt_lookup_denied"):
        authorize_receipt_lookup(ReceiptIdentity("installation-1", "other", "runtime-1"), _context().identity)


def test_capabilities_match_the_twelve_versioned_inert_owner_operations():
    operations = capability_declaration()["operations"]
    assert [operation["name"] for operation in operations] == ["propagation.accept.v1", "propagation.status.v1", "propagation.resume.v1", "propagation.cancel.v1", "propagation.dispatch.pending.v1", "propagation.dispatch.record.v1", "fleet.propagation.preview.v1", "fleet.propagation.submit.v1", "fleet.propagation.status.v1", "fleet.propagation.verify.v1", "fleet.propagation.convergence.v1", "fleet.propagation.cancel.v1"]
    assert all(
        not operation["available"]
        and operation["input_schema"] != operation["result_schema"]
        and operation["max_result_bytes"] == 64 * 1024
        for operation in operations
    )


def test_capability_schemas_have_bounded_semantic_rows_and_scalar_types():
    operations = {operation["name"]: operation for operation in capability_declaration()["operations"]}
    pending = operations["propagation.dispatch.pending.v1"]
    assert pending["input_schema"]["properties"]["cursor"]["type"] == ["string", "null"]
    intent_rows = pending["result_schema"]["properties"]["intents"]
    assert intent_rows["maxItems"] == 100
    assert intent_rows["items"]["properties"]["contract_digest"]["pattern"] == "^[0-9a-f]{64}$"
    assert intent_rows["items"]["properties"]["generation"] == {"type": "integer", "minimum": 1}
    assert pending["result_schema"]["properties"]["next_cursor"]["type"] == ["string", "null"]

    recorded = operations["propagation.dispatch.record.v1"]["result_schema"]
    assert recorded["properties"]["recorded"] == {"type": "boolean"}
    convergence = operations["fleet.propagation.convergence.v1"]["result_schema"]
    assert convergence["properties"]["changed"] == {"type": "integer", "minimum": 0}
    assert convergence["properties"]["reloads"] == {"type": "integer", "minimum": 0}

    status = operations["fleet.propagation.status.v1"]["result_schema"]
    outcome = status["properties"]["outcomes"]
    receipt_refs = status["properties"]["receipt_refs"]
    assert outcome["maxItems"] == receipt_refs["maxItems"] == 128
    assert outcome["items"]["properties"]["verified"] == {"type": "boolean"}
    assert receipt_refs["items"]["properties"]["receipt_digest"]["pattern"] == "^[0-9a-f]{64}$"


def _matches_declared_schema(value, declaration):
    kinds = declaration["type"]
    if not isinstance(kinds, list):
        kinds = [kinds]
    kind_matches = {
        "null": value is None,
        "string": type(value) is str,
        "boolean": type(value) is bool,
        "integer": type(value) is int and not isinstance(value, bool),
        "array": type(value) is list,
        "object": type(value) is dict,
    }
    if not any(kind_matches.get(kind, False) for kind in kinds):
        return False
    if "enum" in declaration and value not in declaration["enum"]:
        return False
    if type(value) is str:
        if len(value) > declaration.get("maxLength", len(value)):
            return False
        if len(value) < declaration.get("minLength", 0):
            return False
        if "pattern" in declaration and re.fullmatch(declaration["pattern"], value) is None:
            return False
    if type(value) is int and value < declaration.get("minimum", value):
        return False
    if type(value) is list:
        return len(value) <= declaration["maxItems"] and all(_matches_declared_schema(item, declaration["items"]) for item in value)
    if type(value) is dict:
        properties = declaration["properties"]
        return set(value) == set(declaration["required"]) and all(
            key in properties and _matches_declared_schema(item, properties[key])
            for key, item in value.items()
        )
    return True


def test_capability_result_samples_match_their_declared_bounded_schemas():
    operations = {operation["name"]: operation for operation in capability_declaration()["operations"]}
    pending = {
        "intents": [{"intent_id": "intent-1", "workflow_id": "workflow-1", "contract_digest": _DIGEST, "scope": "scope-1", "revision": "revision-1", "generation": 1}],
        "next_cursor": None,
    }
    status = {
        "outcomes": [{"target_id": "target-1", "installation_id": "installation-1", "profile_id": "profile-1", "runtime_id": "runtime-1", "outcome": "success", "applied": True, "verified": True, "observed_at": "2026-09-27T12:00:00Z", "receipt_ref": {"target_id": "target-1", "effect_id": "effect-1", "issuer": "executor-1", "receipt_digest": _DIGEST}}],
        "receipt_refs": [{"target_id": "target-1", "effect_id": "effect-1", "issuer": "executor-1", "receipt_digest": _DIGEST}],
    }
    assert _matches_declared_schema(pending, operations["propagation.dispatch.pending.v1"]["result_schema"])
    assert _matches_declared_schema(status, operations["fleet.propagation.status.v1"]["result_schema"])


def test_direct_mcp_uses_only_out_of_band_authenticated_scopes():
    allowed = mcp.call_tool(
        "propagation_capabilities", {},
        caller={"principal": "status-reader", "scopes": ["propagation:status"]},
    )
    assert allowed["ok"] is True
    for caller in (None, {"principal": "legacy", "scopes": ()}, {"principal": "media", "scopes": ["operator:media"]}):
        denied = mcp.call_tool("propagation_capabilities", {}, caller=caller)
        assert denied["ok"] is False
        assert denied["error"]["code"] == "scope_denied"
