"""Focused behavior checks for the inert propagation v1 value contracts."""

from datetime import datetime, timezone
import json

import pytest

from anvil_serving.control_plane.propagation import (
    ActiveIdentity, ApprovedAuthority, MAX_CONTRACT_BYTES, PropagationContractError,
    ReceiptContext, ReceiptIdentity, admit_contract, authorize_receipt_lookup,
    capability_declaration, parse_contract, parse_receipt,
)

_DIGEST = "a" * 64
_NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _contract(**changes):
    value = {"schema": "anvil-propagation/v1", "scope": "scope-1", "revision": "revision-1", "generation": 1, "approval_ref": "approval-1", "approval_digest": _DIGEST, "activation_ref": "activation-1", "activation_digest": _DIGEST, "inputs": {"catalog_digest": _DIGEST, "monitoring_inventory_digest": _DIGEST, "execution_profile_digest": _DIGEST, "artifact_digest": _DIGEST, "installation_inventory_digest": _DIGEST}, "effect_set_digest": _DIGEST, "targets": [{"target_id": "target-1", "installation_id": "installation-1", "profile_id": "profile-1", "runtime_id": "runtime-1", "resource_keys": ["catalog-1"], "expected_identity_ref": "identity-1", "expected_identity_digest": _DIGEST, "checks": ["catalog-equal"], "effects": ["catalog-apply"]}], "execution_profile_ref": "profile-1", "execution_profile_digest": _DIGEST, "session_policy": {"preserve_active_conversations": True, "loaded_state_required": True, "idle_reload": False}, "preview_policy": {"all_required_targets": True, "web_runtime_required": True, "monitoring_required": True}, "issued_at": "2026-09-27T12:00:00Z", "deadline_at": "2026-09-28T12:00:00Z"}
    value.update(changes)
    return value


def _receipt(**changes):
    value = {"schema": "anvil-propagation/v1", "contract_digest": _DIGEST, "revision": "revision-1", "generation": 1, "job_id": "job-1", "effect_id": "effect-1", "target_id": "target-1", "installation_id": "installation-1", "profile_id": "profile-1", "runtime_id": "runtime-1", "before_digest": _DIGEST, "after_digest": _DIGEST, "applied": True, "verified": True, "observed_at": "2026-09-27T11:59:00Z", "check_set_digest": _DIGEST, "outcome": "success", "failure_code": None, "evidence_ref": "evidence-1", "evidence_digest": _DIGEST, "issuer": "executor-1"}
    value.update(changes)
    return value


def _context():
    return ReceiptContext(_DIGEST, "revision-1", 1, "job-1", "effect-1", ReceiptIdentity("installation-1", "profile-1", "runtime-1"))


def test_contract_is_immutable_after_caller_and_projection_mutation():
    caller = _contract()
    contract = parse_contract(caller)
    caller["targets"][0]["effects"][:] = ["session-idle-reload"]
    projection = contract.value
    projection["targets"][0]["effects"][:] = ["session-idle-reload"]
    assert contract.value["targets"][0]["effects"] == ["catalog-apply"]
    assert contract.digest == parse_contract(_contract()).digest


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
    assert admit_contract(_contract(), lambda _: approved, ActiveIdentity("activation-1", _DIGEST)).digest == parsed.digest
    with pytest.raises(PropagationContractError, match="malformed_payload"):
        admit_contract(_contract(approved=True), lambda _: approved, ActiveIdentity("activation-1", _DIGEST))
    with pytest.raises(PropagationContractError, match="approval_mismatch"):
        admit_contract(_contract(), lambda _: approved, ActiveIdentity("other", _DIGEST))


def test_receipt_binds_context_transport_identity_and_full_lookup_identity():
    assert parse_receipt(_receipt(), context=_context(), authenticated_issuer="executor-1", now=_NOW)["verified"]
    with pytest.raises(PropagationContractError, match="receipt_identity_mismatch"):
        parse_receipt(_receipt(profile_id="other"), context=_context(), authenticated_issuer="executor-1", now=_NOW)
    with pytest.raises(PropagationContractError, match="receipt_identity_mismatch"):
        parse_receipt(_receipt(issuer="forged"), context=_context(), authenticated_issuer="executor-1", now=_NOW)
    with pytest.raises(PropagationContractError, match="stale_receipt"):
        parse_receipt(_receipt(observed_at="2026-09-27T11:54:00Z"), context=_context(), authenticated_issuer="executor-1", now=_NOW)
    with pytest.raises(PropagationContractError, match="receipt_lookup_denied"):
        authorize_receipt_lookup(ReceiptIdentity("installation-1", "other", "runtime-1"), _context().identity)


def test_capabilities_match_the_twelve_versioned_inert_owner_operations():
    operations = capability_declaration()["operations"]
    assert [operation["name"] for operation in operations] == ["propagation.accept.v1", "propagation.status.v1", "propagation.resume.v1", "propagation.cancel.v1", "propagation.dispatch.pending.v1", "propagation.dispatch.record.v1", "fleet.propagation.preview.v1", "fleet.propagation.submit.v1", "fleet.propagation.status.v1", "fleet.propagation.verify.v1", "fleet.propagation.convergence.v1", "fleet.propagation.cancel.v1"]
    assert all(not operation["available"] and operation["input_fields"] != operation["result_fields"] for operation in operations)
