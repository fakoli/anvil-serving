"""Unit tests for :mod:`anvil_serving.router.internal` — ``NoAvailableTierError``.

v0.7.1 split the single "gated candidates ... are unbound" message into two
kinds (Fix 2 of the v0.7.1 hardening pass, driven by a live incident): the
genuinely-unbound case (no change) and the exhausted case (every bound
candidate was actually attempted and failed) — the pre-v0.7.1 message pointed
at "configure credentials/endpoint" for BOTH, which was actively misleading
for the exhausted case and cost real debugging time.

Hermetic and stdlib-only.
"""
from __future__ import annotations

from anvil_serving.router.internal import NoAvailableTierError


def test_default_kind_is_unbound():
    err = NoAvailableTierError("chat", ["fast-local", "cloud"])
    assert err.kind == "unbound"
    assert err.work_class == "chat"
    assert err.candidates == ("fast-local", "cloud")


def test_unbound_message_mentions_credentials_and_endpoint():
    err = NoAvailableTierError("chat", ["fast-local", "cloud"])
    msg = str(err)
    assert "unbound" in msg
    assert "credentials" in msg or "endpoint" in msg


def test_exhausted_kind_message_does_not_blame_credentials():
    err = NoAvailableTierError("chat", ["fast-local", "heavy-local"], kind="exhausted")
    assert err.kind == "exhausted"
    msg = str(err)
    # Must NOT tell the operator to configure credentials/endpoint -- that is
    # the wrong remediation for a tier that was bound and reachable the whole
    # time. (The message MAY explicitly disclaim this, which is fine; the bug
    # was instructing the operator to go configure something that was already
    # correctly configured.)
    assert "configure that tier's" not in msg.lower()
    # Must be accurate: it says the tiers were attempted and failed.
    assert "attempted" in msg.lower()
    assert "fail" in msg.lower()
    assert "bound" in msg.lower() and "reachable" in msg.lower()


def test_exhausted_message_still_carries_work_class_and_candidates():
    err = NoAvailableTierError(
        "bounded-edit", ["fast-local", "heavy-local"], kind="exhausted")
    msg = str(err)
    assert "bounded-edit" in msg
    assert "fast-local" in msg
    assert "heavy-local" in msg


def test_unbound_and_exhausted_messages_are_distinct():
    unbound = str(NoAvailableTierError("chat", ["fast-local"]))
    exhausted = str(NoAvailableTierError("chat", ["fast-local"], kind="exhausted"))
    assert unbound != exhausted


def test_positional_construction_still_works_default_kind():
    # front_door / serve.py call sites and existing tests construct this
    # positionally (work_class, candidates) with no `kind` -- that contract
    # must not break (kind stays a keyword-only param with a default).
    err = NoAvailableTierError("planning", ["cloud"])
    assert err.kind == "unbound"
