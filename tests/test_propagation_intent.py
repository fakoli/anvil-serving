"""Behavioral tests for durable owner propagation intent and dispatch state."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from anvil_serving.control_plane.controller.propagation_store import (
    MAX_DISPATCH_BYTES,
    MAX_PENDING_ITEMS,
    PropagationIntentError,
    PropagationIntentStore,
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
        "schema": "anvil-propagation/v1", "authority_mode": "effects",
        "scope": scope, "revision": revision,
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


def _sqlite_page_size(path) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("PRAGMA page_size").fetchone()[0]


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


@pytest.mark.parametrize("conflicting", [False, True])
def test_admission_committed_after_replay_lookup_uses_its_digest(tmp_path, monkeypatch, conflicting):
    path = tmp_path / "propagation.sqlite3"
    first, second = PropagationIntentStore(path), PropagationIntentStore(path)
    requested = _contract()
    committed = _contract(effect="monitoring-apply") if conflicting else requested
    resolve = first._resolve_request
    admitted = []

    def commit_between_reads(*args):
        result = resolve(*args)
        if not admitted:
            assert result is None
            # Force the other connection's commit after the optimistic lookup.
            # No sleep or scheduler ordering is needed to reproduce the race.
            admitted.append(_admit(second, committed))
        return result

    monkeypatch.setattr(first, "_resolve_request", commit_between_reads)
    if conflicting:
        with pytest.raises(PropagationIntentError, match="intent_conflict"):
            _admit(first, requested)
    else:
        replay = _admit(first, requested)
        assert replay.duplicate
        assert replay.intent_id == admitted[0].intent_id
        assert replay.contract_digest == admitted[0].contract_digest
        assert replay.workflow_id == admitted[0].workflow_id

    with closing(sqlite3.connect(path)) as connection:
        for table in ("propagation_intents", "propagation_outbox", "propagation_request_tombstones"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
    assert first.contract_bytes(admitted[0].intent_id) == parse_contract(committed).canonical


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
    page_size = _sqlite_page_size(path)

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
