"""Focused behavior checks for the inert propagation v1 value contracts."""

from datetime import datetime, timezone
import re

import pytest

from anvil_serving import mcp
from anvil_serving.control_plane.propagation import (
    PropagationContractError,
    ReceiptContext, ReceiptIdentity, parse_contract, effect_scope_digest,
)

_DIGEST = "a" * 64
_NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _contract(**changes):
    value = {"schema": "anvil-propagation/v1", "authority_mode": "effects", "scope": "scope-1", "revision": "revision-1", "generation": 1, "approval_ref": "approval-1", "approval_digest": _DIGEST, "activation_ref": "activation-1", "activation_digest": _DIGEST, "inputs": {"catalog_digest": _DIGEST, "monitoring_inventory_digest": _DIGEST, "execution_profile_digest": _DIGEST, "artifact_digest": _DIGEST, "installation_inventory_digest": _DIGEST}, "effect_set_digest": _DIGEST, "targets": [{"target_id": "target-1", "installation_id": "installation-1", "profile_id": "profile-1", "runtime_id": "runtime-1", "resource_keys": ["catalog-1"], "expected_identity_ref": "identity-1", "expected_identity_digest": _DIGEST, "checks": ["catalog-equal"], "effects": ["catalog-apply"]}], "execution_profile_ref": "profile-1", "execution_profile_digest": _DIGEST, "session_policy": {"preserve_active_conversations": True, "loaded_state_required": True, "idle_reload": False}, "preview_policy": {"all_required_targets": True, "web_runtime_required": True, "monitoring_required": True}, "issued_at": "2026-09-27T12:00:00Z", "deadline_at": "2026-09-28T12:00:00Z"}
    value.update(changes)
    value["effect_set_digest"] = effect_scope_digest(value)
    return value


def _receipt(**changes):
    value = {"schema": "anvil-propagation/v1", "contract_digest": _DIGEST, "revision": "revision-1", "generation": 1, "job_id": "job-1", "effect_id": "effect-1", "target_id": "target-1", "installation_id": "installation-1", "profile_id": "profile-1", "runtime_id": "runtime-1", "before_digest": _DIGEST, "after_digest": _DIGEST, "applied": True, "verified": True, "observed_at": "2026-09-27T11:59:00Z", "check_set_digest": _DIGEST, "outcome": "success", "failure_code": None, "evidence_ref": "evidence-1", "evidence_digest": _DIGEST, "issuer": "executor-1"}
    value.update(changes)
    return value


def _context():
    return ReceiptContext(_DIGEST, "revision-1", 1, "job-1", "effect-1", "target-1", _DIGEST, ReceiptIdentity("installation-1", "profile-1", "runtime-1"))


def test_effect_scope_digest_binds_authority_but_not_execution_window_observation():
    contract = _contract()
    refreshed_window = {**contract, "issued_at": "2026-09-27T13:00:00Z", "deadline_at": "2026-09-28T13:00:00Z"}
    assert effect_scope_digest(contract) == effect_scope_digest(refreshed_window)
    expanded_effect = _contract(targets=[{**contract["targets"][0], "effects": ["monitoring-apply"]}])
    assert effect_scope_digest(contract) != effect_scope_digest(expanded_effect)
    replacement_approval = _contract(approval_ref="approval-2", approval_digest="b" * 64)
    assert effect_scope_digest(contract) == effect_scope_digest(replacement_approval)
    assert parse_contract(contract).digest != parse_contract(replacement_approval).digest
    preview_contract = _contract(authority_mode="preview")
    assert effect_scope_digest(contract) == effect_scope_digest(preview_contract)
    assert parse_contract(contract).digest != parse_contract(preview_contract).digest
    forged = dict(contract)
    forged["targets"] = [{**contract["targets"][0], "effects": ["monitoring-apply"]}]
    with pytest.raises(PropagationContractError, match="effect_scope_mismatch"):
        parse_contract(forged)


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


def _page_context():
    return {"contract_digest": _DIGEST, "target_set_digest": "b" * 64,
            "target_count": 1, "snapshot_id": "snapshot-1",
            "observed_at": "2026-09-27T12:00:00Z", "total_items": 1,
            "next_cursor": None}


def _offline_row():
    return {
        "target_id": "target-1", "installation_id": "installation-1",
        "profile_id": "profile-1", "runtime_id": "runtime-1",
        "check_set_digest": _DIGEST,
        "outcome": "pending", "applied": False, "verified": False,
        "desired_revision": "revision-1", "applied_revision": None,
        "verified_revision": None, "last_contact_at": None, "observed_at": None,
        "age_seconds": None, "freshness": "unknown", "pending_reason": "offline",
        "session_state": {"files": "pending", "new_session": "pending", "existing_session": "pending"},
        "metric_coverage": "unsupported", "receipt_ref": None,
    }


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
