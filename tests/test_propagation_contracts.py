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


@pytest.mark.parametrize(
    "raw, code",
    [
        ('{"schema":"anvil-propagation/v1","schema":"anvil-propagation/v1"}', "malformed_payload"),
        (json.dumps(_contract(generation=0)), "malformed_payload"),
        (json.dumps(_contract(generation=True)), "malformed_payload"),
        (json.dumps(_contract(targets=[])), "malformed_payload"),
        (json.dumps(_contract(unknown=True)), "malformed_payload"),
        (b"{" + b"x" * MAX_CONTRACT_BYTES + b"}", "payload_too_large"),
    ],
    ids=("duplicate-schema", "zero-generation", "boolean-generation", "empty-targets", "unknown-field", "oversized-payload"),
)
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
    assert intent_rows["items"]["properties"]["effect_set_digest"]["pattern"] == "^[0-9a-f]{64}$"
    assert intent_rows["items"]["properties"]["targets"]["maxItems"] == 128
    assert pending["result_schema"]["properties"]["next_cursor"]["type"] == ["string", "null"]

    recorded = operations["propagation.dispatch.record.v1"]["result_schema"]
    assert recorded["properties"]["recorded"] == {"type": "boolean"}
    convergence = operations["fleet.propagation.convergence.v1"]["result_schema"]
    assert convergence["properties"]["changed"] == {"type": "integer", "minimum": 0}
    assert convergence["properties"]["reloads"] == {"type": "integer", "minimum": 0}

    status = operations["fleet.propagation.status.v1"]["result_schema"]
    outcome = status["properties"]["outcomes"]
    receipt_refs = status["properties"]["receipt_refs"]
    assert outcome["maxItems"] == receipt_refs["maxItems"] == 8
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
    if type(value) is int and value > declaration.get("maximum", value):
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
        "intents": [{"intent_id": "intent-1", "workflow_id": "workflow-1", "contract_digest": _DIGEST,
                     "target_set_digest": _DIGEST, "effect_set_digest": _DIGEST,
                     "issued_at": "2026-09-27T12:00:00Z", "deadline_at": "2026-09-28T12:00:00Z",
                     "targets": [{"target_id": "target-1", "check_set_digest": _DIGEST}]}],
        "next_cursor": None,
    }
    status = {**_page_context(), "outcomes": [_offline_row()], "receipt_refs": []}
    assert _matches_declared_schema(pending, operations["propagation.dispatch.pending.v1"]["result_schema"])
    assert _matches_declared_schema(status, operations["fleet.propagation.status.v1"]["result_schema"])


def _page_context():
    return {"contract_digest": _DIGEST, "target_set_digest": "b" * 64,
            "target_count": 1, "snapshot_id": "snapshot-1",
            "observed_at": "2026-09-27T12:00:00Z", "total_items": 1,
            "next_cursor": None}


def _offline_row():
    return {
        "target_id": "target-1", "installation_id": "installation-1",
        "profile_id": "profile-1", "runtime_id": "runtime-1",
        "outcome": "pending", "applied": False, "verified": False,
        "desired_revision": "revision-1", "applied_revision": None,
        "verified_revision": None, "last_contact_at": None, "observed_at": None,
        "age_seconds": None, "freshness": "unknown", "pending_reason": "offline",
        "session_state": {"files": "pending", "new_session": "pending", "existing_session": "pending"},
        "metric_coverage": "unsupported", "receipt_ref": None,
    }


def test_pending_and_historical_status_remain_expressible_without_receipt():
    operations = {op["name"]: op for op in capability_declaration()["operations"]}
    schema = operations["propagation.status.v1"]["result_schema"]
    status = {**_page_context(), "targets": [_offline_row()], "state": "pending", "all_targets_verified": False}
    assert _matches_declared_schema(status, schema)
    historical = status["targets"][0]
    historical.update(applied=True, verified=True, applied_revision="revision-1",
                      verified_revision="revision-1", outcome="success", freshness="stale",
                      observed_at="2026-09-26T12:00:00Z", age_seconds=86400)
    assert _matches_declared_schema(status, schema)
    for required in ("desired_revision", "applied_revision", "verified_revision", "last_contact_at",
                     "observed_at", "age_seconds", "freshness", "pending_reason", "session_state", "metric_coverage"):
        incomplete = {**status, "targets": [{key: val for key, val in historical.items() if key != required}]}
        assert not _matches_declared_schema(incomplete, schema)


def test_preview_and_verification_can_report_unavailable_targets_without_evidence():
    operations = {op["name"]: op for op in capability_declaration()["operations"]}
    preview = {**_page_context(), "preview_digest": None, "effect_set_digest": _DIGEST,
               "expires_at": "2026-09-27T12:05:00Z", "all_required_ready": False,
               "targets": [{"target_id": "target-1", "installation_id": "installation-1",
                            "profile_id": "profile-1", "runtime_id": "runtime-1", "ready": False,
                            "pending_reason": "offline", "observed_at": None,
                            "observed_digest": None, "permitted_effects": ["catalog-apply"]}]}
    assert _matches_declared_schema(preview, operations["fleet.propagation.preview.v1"]["result_schema"])
    verification = {**_page_context(), "all_targets_verified": False, "receipts": [],
                    "checks": [{"target_id": "target-1", "check_id": "catalog-equal",
                                "outcome": "pending", "pending_reason": "offline",
                                "observed_at": None, "evidence_ref": None, "evidence_digest": None}]}
    assert _matches_declared_schema(verification, operations["fleet.propagation.verify.v1"]["result_schema"])
    for name in ("propagation.status.v1", "fleet.propagation.preview.v1", "fleet.propagation.status.v1",
                 "fleet.propagation.verify.v1", "fleet.propagation.convergence.v1"):
        assert operations[name]["input_schema"]["properties"]["cursor"]["type"] == ["string", "null"]
        fields = operations[name]["result_schema"]["properties"]
        assert {"snapshot_id", "contract_digest", "target_set_digest", "target_count", "total_items", "next_cursor"} <= fields.keys()


def test_full_denominator_fits_bounded_pages_with_maximum_length_identities():
    schema = next(op for op in capability_declaration()["operations"]
                  if op["name"] == "propagation.status.v1")["result_schema"]
    rows = []
    for index in range(128):
        row = _offline_row()
        for field in ("target_id", "installation_id", "profile_id", "runtime_id", "desired_revision", "applied_revision", "verified_revision"):
            row[field] = f"t{index:0127d}"
        row.update(outcome="success", applied=True, verified=True,
                   last_contact_at="2026-09-27T12:00:00Z", observed_at="2026-09-27T12:00:00Z",
                   age_seconds=0, freshness="fresh", pending_reason=None,
                   receipt_ref={"receipt_id": "r" * 128, "target_id": row["target_id"],
                                "effect_id": "e" * 128, "issuer": "i" * 128, "receipt_digest": _DIGEST})
        rows.append(row)
    seen = []
    for offset in range(0, 128, 8):
        page = {**_page_context(), "target_count": 128, "total_items": 128,
                "snapshot_id": "s" * 128, "next_cursor": "c" * 128 if offset + 8 < 128 else None,
                "targets": rows[offset:offset + 8], "state": "completed", "all_targets_verified": True}
        assert _matches_declared_schema(page, schema)
        assert len(json.dumps(page, ensure_ascii=True).encode()) < 64 * 1024
        seen.extend(row["target_id"] for row in page["targets"])
    assert len(set(seen)) == len(seen) == 128
    assert seen == [row["target_id"] for row in rows]
    oversized = {**page, "targets": rows[:9]}
    assert not _matches_declared_schema(oversized, schema)


def test_lone_surrogate_strings_fail_with_fixed_contract_errors():
    with pytest.raises(PropagationContractError, match="malformed_payload"):
        parse_contract("\ud800")
    with pytest.raises(PropagationContractError, match="malformed_payload"):
        parse_receipt("\ud800", context=_context(), authenticated_issuer="executor-1", now=_NOW)


def test_duplicate_target_ids_cannot_alias_different_installations():
    first = _contract()["targets"][0]
    value = _contract(targets=[first, {**first, "installation_id": "installation-2"}])
    with pytest.raises(PropagationContractError, match="malformed_identity"):
        parse_contract(value)


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
