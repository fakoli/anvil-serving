"""Hermetic tests for the T016 serve-fingerprint + profile-row staleness.

``serve_fingerprint`` must be a stable digest over a tier's quality-affecting
serve identity (model / endpoint / dialect / context / params) and ONLY that, so
incidental fields don't churn the profile. ``mark_stale_on_change`` must flag the
affected tier's rows stale on a fingerprint change and leave every other tier
untouched. Stdlib only, deterministic, no network.
"""
from __future__ import annotations

import threading

from anvil_serving.router.config import Tier
from anvil_serving.router.fingerprint import (
    identity,
    mark_stale_on_change,
    refresh_fingerprint,
    serve_fingerprint,
)
from anvil_serving.router.profile_store import default_profile

# Every work class the seed profile carries for a single (local) tier.
ALL_CLASSES = {
    "planning",
    "multi-file-refactor",
    "long-context",
    "review",
    "bounded-edit",
    "chat",
}


# ── serve_fingerprint: deterministic, hex, identity-only ───────────────────────
def test_fingerprint_is_deterministic_hex():
    spec = {"id": "t", "model": "m", "base_url": "http://x", "dialect": "openai"}
    fp = serve_fingerprint(spec)
    assert isinstance(fp, str)
    assert len(fp) == 64
    int(fp, 16)  # valid lowercase hex
    # Identical inputs -> identical digest, across separate dicts.
    assert serve_fingerprint(dict(spec)) == fp


def test_fingerprint_changes_with_each_identity_field():
    base = {
        "id": "t",
        "model": "m",
        "base_url": "http://x",
        "dialect": "openai",
        "context_limit": 32000,
        "quantization": "awq",
    }
    ref = serve_fingerprint(base)
    for key, value in [
        ("model", "m2"),
        ("base_url", "http://y"),
        ("dialect", "anthropic"),
        ("context_limit", 8000),
        ("quantization", "fp8"),
    ]:
        changed = dict(base)
        changed[key] = value
        assert serve_fingerprint(changed) != ref, key


def test_fingerprint_ignores_non_identity_fields():
    base = {"id": "t", "model": "m", "base_url": "http://x", "dialect": "openai"}
    noisy = dict(base, latency_ms=12, load=0.93, pid=4242, replica="b7")
    # Operational churn must not invalidate the measured profile.
    assert serve_fingerprint(noisy) == serve_fingerprint(base)
    assert set(identity(noisy)) == {"tier_id", "model", "endpoint", "dialect"}


def test_fingerprint_accepts_tier_object_and_hashes_only_identity():
    t1 = Tier(
        id="fast-local",
        base_url="http://a",
        dialect="openai",
        context_limit=32000,
        privacy="local",
        tool_support=True,
        auth_env="FAST_KEY",
    )
    # A different ENDPOINT changes the fingerprint ...
    t2 = Tier(
        id="fast-local",
        base_url="http://b",
        dialect="openai",
        context_limit=32000,
        privacy="local",
        tool_support=True,
        auth_env="FAST_KEY",
    )
    assert serve_fingerprint(t1) != serve_fingerprint(t2)
    # ... but privacy / tool_support / auth_env are NOT quality identity, so
    # changing them leaves the fingerprint unmoved.
    t3 = Tier(
        id="fast-local",
        base_url="http://a",
        dialect="openai",
        context_limit=32000,
        privacy="cloud",
        tool_support=False,
        auth_env="OTHER_KEY",
    )
    assert serve_fingerprint(t3) == serve_fingerprint(t1)


# ── mark_stale_on_change: change stales the tier; others untouched ─────────────
def test_change_marks_affected_tier_rows_stale_only():
    store = default_profile()
    fp0 = serve_fingerprint({"id": "fast-local", "model": "qwen-A", "base_url": "http://x"})
    fp1 = serve_fingerprint({"id": "fast-local", "model": "qwen-B", "base_url": "http://x"})
    assert fp0 != fp1

    # First association is a baseline: nothing was invalidated.
    assert mark_stale_on_change(store, "fast-local", fp0) == []
    assert store.stale_pairs() == []

    # The change stales EVERY fast-local row ...
    newly = mark_stale_on_change(store, "fast-local", fp1)
    assert set(newly) == ALL_CLASSES
    assert all(tier == "fast-local" for (tier, _wc) in store.stale_pairs())
    assert store.is_stale("fast-local", "review") is True

    # ... and leaves other tiers completely untouched.
    assert store.is_stale("heavy-local", "review") is False
    assert store.is_stale("cloud", "planning") is False

    # Idempotent: re-applying the same fingerprint stales nothing new.
    assert mark_stale_on_change(store, "fast-local", fp1) == []


def test_baseline_then_same_fingerprint_is_noop():
    store = default_profile()
    fp = serve_fingerprint({"id": "heavy-local", "model": "m"})
    assert mark_stale_on_change(store, "heavy-local", fp) == []
    assert mark_stale_on_change(store, "heavy-local", fp) == []
    assert store.stale_pairs() == []


def test_unmeasured_pair_is_not_stale():
    store = default_profile()
    # A pair with no stored row is not "stale" — it falls through to the
    # fail-closed defaults, which already distrust the risky classes.
    assert store.is_stale("gpu0", "planning") is False


def test_refresh_fingerprint_wires_spec_to_staleness():
    store = default_profile()
    spec_a = {"id": "heavy-local", "model": "m1", "base_url": "http://h"}
    spec_b = {"id": "heavy-local", "model": "m2", "base_url": "http://h"}

    assert refresh_fingerprint(store, "heavy-local", spec_a) == []   # baseline
    newly = refresh_fingerprint(store, "heavy-local", spec_b)        # model swapped
    assert set(newly) == ALL_CLASSES
    assert store.is_stale("heavy-local", "planning") is True
    assert store.is_stale("fast-local", "planning") is False        # other tier safe


# ── review FIX 2: stale_pairs() snapshots under the lock (no concurrent-insert race)
def test_stale_pairs_safe_under_concurrent_record_grade():
    """Hammer ``stale_pairs()`` while writers INSERT brand-new keys via
    ``record_grade``. An unlocked iteration over the live dict view would raise
    ``RuntimeError: dictionary changed size during iteration``; the lock-snapshot
    must keep it safe and the returned list sorted."""
    store = default_profile()
    n_keys = 600
    errors: list = []
    done = threading.Event()

    def writer(prefix: str):
        for i in range(n_keys):
            # Each call inserts a previously-unseen (tier, work_class) key.
            store.record_grade("fast-local", f"{prefix}-{i}", score=0.5)

    def reader():
        # Read until all writers finish; any unsafe iteration surfaces here.
        while not done.is_set():
            try:
                pairs = store.stale_pairs()
                assert pairs == sorted(pairs, key=lambda k: (k[0], k[1] or ""))
            except Exception as exc:  # pragma: no cover - the bug we're guarding
                errors.append(exc)
                return

    writers = [threading.Thread(target=writer, args=(p,)) for p in ("a", "b", "c")]
    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in readers:
        t.start()
    for t in writers:
        t.start()
    for t in writers:
        t.join(20)
    done.set()
    for t in readers:
        t.join(5)

    assert not errors, errors
    # Sanity: all the inserted keys landed and none are stale (record_grade clears).
    assert isinstance(store.stale_pairs(), list)
    assert store.entry("fast-local", "a-0") is not None
