"""Behavioral tests for durable owner propagation intent and dispatch state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
import threading

import pytest

from anvil_serving.control_plane.controller.propagation_store import (
    MAX_DISPATCH_BYTES,
    MAX_PENDING_ITEMS,
    PropagationIntentError,
    PropagationIntentStore,
    logical_workflow_id,
)
from anvil_serving.control_plane.propagation import (
    ActiveIdentity,
    ApprovedAuthority,
    effect_scope_digest,
    parse_contract,
    PropagationContractError,
)


_NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
_DIGEST = "a" * 64


def _contract(*, scope="scope-1", revision="revision-1", generation=1, target="target-1", effect="catalog-apply"):
    value = {
        "schema": "anvil-propagation/v1", "scope": scope, "revision": revision,
        "generation": generation, "approval_ref": "approval-1", "approval_digest": _DIGEST,
        "activation_ref": "activation-1", "activation_digest": _DIGEST,
        "inputs": {"catalog_digest": _DIGEST, "monitoring_inventory_digest": _DIGEST,
                   "execution_profile_digest": _DIGEST, "artifact_digest": _DIGEST,
                   "installation_inventory_digest": _DIGEST}, "effect_set_digest": _DIGEST,
        "targets": [{"target_id": target, "installation_id": "installation-" + target,
                     "profile_id": "profile-" + target, "runtime_id": "runtime-" + target,
                     "resource_keys": ["catalog-1"], "expected_identity_ref": "identity-" + target,
                     "expected_identity_digest": _DIGEST, "checks": ["catalog-equal"],
                     "effects": [effect]}], "execution_profile_ref": "profile-1",
        "execution_profile_digest": _DIGEST,
        "session_policy": {"preserve_active_conversations": True, "loaded_state_required": True, "idle_reload": False},
        "preview_policy": {"all_required_targets": True, "web_runtime_required": True, "monitoring_required": True},
        "issued_at": "2026-09-27T12:00:00Z", "deadline_at": "2026-09-28T12:00:00Z",
    }
    value["effect_set_digest"] = effect_scope_digest(value)
    return value


def _approval(value):
    parsed = parse_contract(value)
    return lambda reference: ApprovedAuthority(reference, _DIGEST, parsed.digest)


def _admit(
    store, value, request_id="request-1", caller_id="caller-1", now=_NOW,
    identity=ActiveIdentity("activation-1", _DIGEST),
):
    return store.admit(
        value, approval_lookup=_approval(value), active_identity=identity,
        caller_id=caller_id, request_id=request_id, now=now,
    )


def _large_contract(*, scope: str, revision: str, generation: int) -> dict:
    value = _contract(scope=scope, revision=revision, generation=generation)
    targets = []
    for number in range(128):
        suffix = f"{number:03d}-" + "x" * 120
        targets.append({
            "target_id": "t" + suffix,
            "installation_id": "i" + suffix,
            "profile_id": "p" + suffix,
            "runtime_id": "r" + suffix,
            "resource_keys": ["catalog-1"],
            "expected_identity_ref": "e" + suffix,
            "expected_identity_digest": _DIGEST,
            "checks": ["catalog-equal"],
            "effects": ["catalog-apply"],
        })
    value["targets"] = targets
    value["effect_set_digest"] = effect_scope_digest(value)
    return value


def test_duplicate_and_conflicting_admission_bind_permanent_owner_identity(tmp_path):
    store = PropagationIntentStore(tmp_path / "propagation.sqlite3")
    first_value = _contract()
    first = _admit(store, first_value)
    duplicate = _admit(store, first_value)
    assert duplicate == first.__class__(first.intent_id, first.workflow_id, first.contract_digest, True)
    assert first.workflow_id == logical_workflow_id("scope-1", "revision-1")

    with pytest.raises(PropagationIntentError, match="intent_conflict"):
        _admit(store, _contract(revision="revision-2", generation=2), "request-1")
    with pytest.raises(PropagationIntentError, match="intent_conflict"):
        _admit(store, _contract(effect="monitoring-apply"), "request-2", "caller-2")
    assert len(store.pending()["intents"]) == 1


def test_lost_acknowledgement_and_reopen_resolve_the_original_intent(tmp_path):
    path = tmp_path / "propagation.sqlite3"
    store = PropagationIntentStore(path, post_commit=lambda: (_ for _ in ()).throw(RuntimeError("lost reply")))
    accepted = _admit(store, _contract())
    assert accepted.duplicate is True
    reopened = PropagationIntentStore(path)
    again = _admit(reopened, _contract())
    assert again.intent_id == accepted.intent_id and again.duplicate is True
    pending = reopened.pending()
    assert pending["intents"][0]["intent_id"] == accepted.intent_id
    assert reopened.acknowledge(accepted.intent_id, workflow_id=accepted.workflow_id, contract_digest=accepted.contract_digest, now=_NOW)
    assert reopened.acknowledge(accepted.intent_id, workflow_id=accepted.workflow_id, contract_digest=accepted.contract_digest, now=_NOW)
    assert reopened.pending()["intents"] == []


def test_lost_dispatch_acknowledgement_resolves_the_existing_binding(tmp_path):
    path = tmp_path / "propagation.sqlite3"
    accepted = _admit(PropagationIntentStore(path), _contract())
    store = PropagationIntentStore(path, post_ack_commit=lambda: (_ for _ in ()).throw(RuntimeError("lost ack")))
    assert store.acknowledge(accepted.intent_id, workflow_id=accepted.workflow_id, contract_digest=accepted.contract_digest, now=_NOW)
    assert PropagationIntentStore(path).pending()["intents"] == []


def test_concurrent_admission_has_one_intent_and_one_outbox_row(tmp_path):
    path = tmp_path / "propagation.sqlite3"
    first, second = PropagationIntentStore(path), PropagationIntentStore(path)
    gate = threading.Barrier(2)
    results, failures = [], []

    def admit(store):
        try:
            gate.wait()
            results.append(_admit(store, _contract()))
        except Exception as exc:  # pragma: no cover - assertion below reports it
            failures.append(exc)

    threads = [threading.Thread(target=admit, args=(store,)) for store in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert {result.intent_id for result in results} == {results[0].intent_id}
    assert sum(result.duplicate for result in results) == 1
    assert len(first.pending()["intents"]) == 1


def test_terminal_payload_pruning_never_prunes_request_contract_or_scope_authority(tmp_path):
    path = tmp_path / "propagation.sqlite3"
    store = PropagationIntentStore(path)
    accepted = _admit(store, _contract())
    store.mark_terminal(accepted.intent_id, now=_NOW)
    assert store.prune_terminal_payloads(now=_NOW + timedelta(days=91)) == 1
    recovered = _admit(
        PropagationIntentStore(path), _contract(), now=_NOW + timedelta(days=91),
        identity=ActiveIdentity("different-activation", "b" * 64),
    )
    assert recovered.intent_id == accepted.intent_id and recovered.duplicate
    with pytest.raises(PropagationContractError, match="approval_expired"):
        _admit(PropagationIntentStore(path), _contract(revision="revision-fresh", generation=2), "fresh-request", now=_NOW + timedelta(days=91))
    with pytest.raises(PropagationIntentError, match="intent_conflict"):
        _admit(PropagationIntentStore(path), _contract(revision="revision-2", generation=2), "request-1")


def test_stale_generation_and_capacity_refuse_without_evicting_unresolved_intents(tmp_path):
    store = PropagationIntentStore(tmp_path / "propagation.sqlite3", max_active_intents=1)
    first = _admit(store, _contract(generation=2))
    with pytest.raises(PropagationIntentError, match="stale_generation"):
        _admit(store, _contract(revision="revision-0", generation=1), "request-2")
    with pytest.raises(PropagationIntentError, match="storage_capacity"):
        _admit(store, _contract(revision="revision-2", generation=3), "request-3")
    assert [row["intent_id"] for row in store.pending()["intents"]] == [first.intent_id]


def test_pending_pages_are_byte_and_count_bounded_and_acknowledgement_is_bound(tmp_path):
    store = PropagationIntentStore(tmp_path / "propagation.sqlite3", max_active_intents=MAX_PENDING_ITEMS + 2)
    accepted = [
        _admit(store, _contract(revision=f"revision-{number}", generation=number), f"request-{number}")
        for number in range(1, MAX_PENDING_ITEMS + 2)
    ]
    first = store.pending(limit=MAX_PENDING_ITEMS)
    assert len(first["intents"]) == MAX_PENDING_ITEMS
    assert first["next_cursor"] is not None
    assert len(json.dumps(first, ensure_ascii=True, separators=(",", ":")).encode("ascii")) <= MAX_DISPATCH_BYTES
    second = store.pending(first["next_cursor"], limit=MAX_PENDING_ITEMS)
    assert len(second["intents"]) == 1 and second["next_cursor"] is None
    with pytest.raises(PropagationIntentError, match="dispatch_binding_mismatch"):
        store.acknowledge(accepted[0].intent_id, workflow_id="workflow-wrong", contract_digest=accepted[0].contract_digest, now=_NOW)
    assert store.acknowledge(accepted[0].intent_id, workflow_id=accepted[0].workflow_id, contract_digest=accepted[0].contract_digest, now=_NOW)


def test_first_store_migration_is_serialized(tmp_path):
    path = tmp_path / "first.sqlite3"
    gate = threading.Barrier(2)
    stores, failures = [], []

    def construct():
        try:
            gate.wait()
            stores.append(PropagationIntentStore(path))
        except Exception as exc:  # pragma: no cover - assertion gives the failure
            failures.append(exc)

    threads = [threading.Thread(target=construct) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert len(stores) == 2
    assert _admit(stores[0], _contract()).duplicate is False


def test_scope_duplicate_recovers_after_expiry_without_new_admission(tmp_path):
    store = PropagationIntentStore(tmp_path / "propagation.sqlite3")
    accepted = _admit(store, _contract())
    recovered = _admit(
        store, _contract(), request_id="request-2", caller_id="caller-2",
        now=_NOW + timedelta(days=2), identity=ActiveIdentity("changed", "b" * 64),
    )
    assert recovered.intent_id == accepted.intent_id and recovered.duplicate
    with pytest.raises(PropagationContractError, match="approval_expired"):
        _admit(store, _contract(revision="new-revision", generation=2), "request-3", now=_NOW + timedelta(days=2))


def test_scope_duplicate_refuses_real_page_growth_without_tombstone_eviction(tmp_path):
    path = tmp_path / "propagation.sqlite3"
    first_store = PropagationIntentStore(path)
    accepted = _admit(first_store, _contract())
    page_size = sqlite3.connect(path).execute("PRAGMA page_size").fetchone()[0]

    for number in range(1, 513):
        before = path.stat().st_size
        constrained = PropagationIntentStore(path, max_database_bytes=before + page_size - 1)
        try:
            _admit(constrained, _contract(), request_id=f"request-{number + 1}", caller_id=f"caller-{number + 1}")
        except PropagationIntentError as exc:
            assert exc.code == "storage_capacity"
            assert path.stat().st_size == before
            recovered = _admit(first_store, _contract(), request_id=f"request-{number + 1}", caller_id=f"caller-{number + 1}")
            assert recovered.intent_id == accepted.intent_id and recovered.duplicate
            break
        else:
            assert path.stat().st_size <= before + page_size - 1
    else:  # pragma: no cover - SQLite must eventually allocate a tombstone page
        pytest.fail("scope tombstones did not reach a page boundary")


def test_pending_page_counts_the_returned_cursor_and_large_projections(tmp_path):
    store = PropagationIntentStore(tmp_path / "propagation.sqlite3", max_active_intents=4)
    for number in range(1, 5):
        _admit(store, _large_contract(scope=f"large-scope-{number}", revision=f"large-revision-{number}", generation=number), f"large-request-{number}")
    page = store.pending()
    assert 1 <= len(page["intents"]) < MAX_PENDING_ITEMS
    assert page["next_cursor"] is not None
    assert len(json.dumps(page, ensure_ascii=True, separators=(",", ":")).encode("ascii")) <= MAX_DISPATCH_BYTES
    followup = store.pending(page["next_cursor"])
    assert followup["intents"]


def test_generation_beyond_sqlite_integer_range_is_rejected(tmp_path):
    store = PropagationIntentStore(tmp_path / "propagation.sqlite3")
    with pytest.raises(PropagationIntentError, match="malformed_generation"):
        _admit(store, _contract(generation=1 << 63))


def test_new_intent_refuses_actual_page_growth_and_rolls_back_every_binding(tmp_path):
    path = tmp_path / "propagation.sqlite3"
    first_store = PropagationIntentStore(path)
    first = _admit(first_store, _contract())
    before = path.stat().st_size
    page_size = sqlite3.connect(path).execute("PRAGMA page_size").fetchone()[0]
    constrained = PropagationIntentStore(path, max_database_bytes=before + page_size - 1)
    second_value = _contract(scope="scope-2", revision="revision-2", generation=2)

    with pytest.raises(PropagationIntentError, match="storage_capacity"):
        _admit(constrained, second_value, "request-2")
    assert path.stat().st_size == before
    assert _admit(constrained, _contract()).intent_id == first.intent_id
    admitted = _admit(first_store, second_value, "request-2")
    assert admitted.duplicate is False
    assert len(first_store.pending()["intents"]) == 2


def test_pending_rejects_cursor_outside_sqlite_signed_range(tmp_path):
    store = PropagationIntentStore(tmp_path / "propagation.sqlite3")
    with pytest.raises(PropagationIntentError, match="invalid_cursor"):
        store.pending("p1:9999999999999999999")
    assert store.pending("p1:9223372036854775807") == {"intents": [], "next_cursor": None}
