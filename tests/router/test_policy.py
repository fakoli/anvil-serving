"""Tests for the residency-aware routing policy (harness-router:T005).

Proves the acceptance criteria against ``configs/example-with-cloud.toml`` (the
opt-in cloud config) because these tests exercise cloud routing logic — deny gates,
planning-goes-cloud, residency deferral behind cloud, etc.  The shipped *default*
config (``example.toml``) is local-only (T001 / ADR-0001); see test_config.py for
tests that assert the local-only default topology.

  AC1 - a ``deny`` ``(tier, work_class)`` is NEVER in the routed result; a
        planning intent yields only the cloud tier, a bounded-edit intent keeps
        fast-local.
  AC2 - the candidate pool is config-derived (subset of ``intent.candidate_tiers``)
        and changing the config preset pool changes the routed pool.
Plus the optional hard-constraint filter and robustness on a missing pool id.
(The residency reorder of AC3 is proved in test_residency.py.)
"""
from __future__ import annotations

import pathlib
from types import MappingProxyType

import pytest

from anvil_serving.router.config import load
from anvil_serving.router.intent import Intent, resolve
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.policy import Needs, RoutingDecision, route
from anvil_serving.router.profile_store import (
    HIGH_RISK_LOCAL_CLASSES,
    ProfileEntry,
    ProfileStore,
    default_profile,
)

# CWD-independent: example-with-cloud.toml at <repo>/configs/; this file is at
# <repo>/tests/router/test_policy.py (parents[2] == repo root).
# These tests exercise cloud routing behavior, so they use the opt-in cloud config.
# The local-only default (example.toml) is tested in test_config.py (T001).
EXAMPLE = pathlib.Path(__file__).resolve().parents[2] / "configs" / "example-with-cloud.toml"
CONFIG = load(str(EXAMPLE))
PROFILE = default_profile()


def _req(model, text="hello there", *, system=None, raw=None):
    return InternalRequest(
        model=model,
        messages=[Message("user", text)],
        system=system,
        raw=raw if raw is not None else {},
    )


def _intent(work_class, candidate_tiers, *, preset=None, source="test", ambiguous=False):
    """Directly build an Intent with a chosen pool (for pool/deny isolation)."""
    return Intent(
        work_class=work_class,
        preset=preset,
        source=source,
        candidate_tiers=tuple(candidate_tiers),
        ambiguous=ambiguous,
        decision=MappingProxyType({}),
    )


# ── AC1: a denied (tier, work_class) is never routed ─────────────────────────
def test_ac1_planning_routes_only_cloud():
    # The example's planning preset pool is already ("cloud",); the result must
    # contain neither local tier.
    intent = resolve(_req("planning"), CONFIG)
    dec = route(intent, CONFIG, PROFILE)
    assert isinstance(dec, RoutingDecision)
    assert "fast-local" not in dec.tiers
    assert "heavy-local" not in dec.tiers
    assert dec.tiers == ("cloud",)


def test_ac1_planning_deny_filter_strips_locals_even_when_pooled():
    # Force both locals into the pool: the deny filter (not the preset) must drop
    # them, leaving only cloud. This exercises the gate directly.
    intent = _intent("planning", ("fast-local", "heavy-local", "cloud"))
    dec = route(intent, CONFIG, PROFILE)
    assert dec.tiers == ("cloud",)
    assert set(dec.notes["dropped_by_deny"]) == {"fast-local", "heavy-local"}


def test_ac1_bounded_edit_keeps_fast_local():
    # quick-edit preset -> bounded-edit work class -> fast-local is allow.
    intent = resolve(_req("quick-edit"), CONFIG)
    dec = route(intent, CONFIG, PROFILE)
    assert "fast-local" in dec.tiers
    assert dec.tiers[0] == "fast-local"  # cost order: fast first


def test_ac1_deny_entry_never_routed_direct_store(tmp_path):
    # A hand-built store where (heavy-local, review) is deny: heavy-local must be
    # absent for a review intent even though the example profile allows it.
    # Uses a temp config with metered_cloud=["review"] so cloud remains a candidate
    # for this work-class and can confirm the quality-gate allows it while deny drops
    # the denied tier.
    body = """\
[router]
mapping_version = "test.deny.review"
metered_cloud = ["review"]

[[router.tiers]]
id            = "heavy-local"
base_url      = "http://127.0.0.1:30000/v1"
dialect       = "openai"
context_limit = 131072
privacy       = "local"
tool_support  = true
auth_env      = "ANVIL_HEAVY_LOCAL_KEY"

[[router.tiers]]
id            = "cloud"
base_url      = "https://api.anthropic.com"
dialect       = "anthropic"
context_limit = 200000
privacy       = "cloud"
tool_support  = true
auth_env      = "ANTHROPIC_API_KEY"

[router.presets]
review = ["heavy-local", "cloud"]
"""
    p = tmp_path / "deny-review.toml"
    p.write_text(body, encoding="utf-8")
    cfg = load(str(p))
    profile = ProfileStore({("heavy-local", "review"): ProfileEntry("deny", 0.2, 1, None)})
    intent = _intent("review", ("heavy-local", "cloud"))
    dec = route(intent, cfg, profile)
    assert "heavy-local" not in dec.tiers
    assert "heavy-local" in dec.notes["dropped_by_deny"]
    assert dec.tiers == ("cloud",)  # cloud unmeasured -> allow (is_cloud), kept


# ── AC2: the pool comes from config, not hard-coded ──────────────────────────
def test_ac2_result_is_subset_of_candidate_pool():
    for model in ("planning", "quick-edit", "review", "long-context", "chat"):
        intent = resolve(_req(model), CONFIG)
        dec = route(intent, CONFIG, PROFILE)
        assert set(dec.tiers) <= set(intent.candidate_tiers), model


def test_ac2_routed_pool_follows_config_preset(tmp_path):
    # A temp config whose planning pool is two cloud tiers in a non-default order;
    # route() must follow it rather than any baked-in default.
    # metered_cloud=["planning"] is required so the cloud tiers are candidates.
    body = """\
[router]
mapping_version = "test.0"
metered_cloud = ["planning"]

[[router.tiers]]
id            = "fast-local"
base_url      = "http://127.0.0.1:30001/v1"
dialect       = "openai"
context_limit = 32768
privacy       = "local"
tool_support  = true
auth_env      = "ANVIL_FAST_LOCAL_KEY"

[[router.tiers]]
id            = "heavy-local"
base_url      = "http://127.0.0.1:30000/v1"
dialect       = "openai"
context_limit = 131072
privacy       = "local"
tool_support  = true
auth_env      = "ANVIL_HEAVY_LOCAL_KEY"

[[router.tiers]]
id            = "cloud"
base_url      = "https://api.anthropic.com"
dialect       = "anthropic"
context_limit = 200000
privacy       = "cloud"
tool_support  = true
auth_env      = "ANTHROPIC_API_KEY"

[[router.tiers]]
id            = "cloud2"
base_url      = "https://api.example2/v1"
dialect       = "openai"
context_limit = 150000
privacy       = "cloud"
tool_support  = true
auth_env      = "CLOUD2_KEY"

[router.presets]
planning = ["cloud2", "cloud"]
"""
    p = tmp_path / "alt.toml"
    p.write_text(body, encoding="utf-8")
    alt = load(str(p))

    base = route(resolve(_req("planning"), CONFIG), CONFIG, PROFILE)
    assert base.tiers == ("cloud",)

    alt_intent = resolve(_req("planning"), alt)
    alt_dec = route(alt_intent, alt, PROFILE)
    # Both cloud tiers are allow for planning; the routed pool tracks the config.
    assert alt_dec.tiers == ("cloud2", "cloud")
    assert alt_dec.tiers != base.tiers


# ── hard-constraint filter ───────────────────────────────────────────────────
def test_constraint_min_context_drops_fast_local():
    # quick-edit -> work_class=bounded-edit; pool [fast, heavy, cloud].
    # metered_cloud=["planning"] in example-with-cloud.toml, so cloud is gated
    # for bounded-edit.  The constraint filter still drops fast-local for ctx;
    # heavy-local (131072 >= 100000) remains in the result.
    intent = resolve(_req("quick-edit"), CONFIG)  # pool [fast, heavy, cloud]
    dec = route(intent, CONFIG, PROFILE, needs=Needs(min_context=100000))
    assert "fast-local" not in dec.tiers  # ctx 32768 < 100000
    assert "heavy-local" in dec.tiers     # 131072 fits
    # Cloud is gated for bounded-edit (not in metered_cloud).
    assert "cloud" not in dec.tiers
    assert "fast-local" in dec.notes["dropped_by_constraint"]
    assert "cloud" in dec.notes["dropped_by_metered_gate"]


def test_constraint_needs_tools_drops_no_tool_tier(tmp_path):
    # quick-edit -> work_class=bounded-edit.  metered_cloud=["bounded-edit"] so cloud
    # is a candidate; the constraint filter must then drop the no-tool tier.
    body = """\
[router]
mapping_version = "test.0"
metered_cloud = ["bounded-edit"]

[[router.tiers]]
id            = "no-tools-local"
base_url      = "http://127.0.0.1:30002/v1"
dialect       = "openai"
context_limit = 65536
privacy       = "local"
tool_support  = false
auth_env      = "NO_TOOLS_KEY"

[[router.tiers]]
id            = "cloud"
base_url      = "https://api.anthropic.com"
dialect       = "anthropic"
context_limit = 200000
privacy       = "cloud"
tool_support  = true
auth_env      = "ANTHROPIC_API_KEY"

[router.presets]
quick-edit = ["no-tools-local", "cloud"]
"""
    p = tmp_path / "tools.toml"
    p.write_text(body, encoding="utf-8")
    cfg = load(str(p))
    intent = resolve(_req("quick-edit"), cfg)
    dec = route(intent, cfg, PROFILE, needs=Needs(needs_tools=True))
    assert "no-tools-local" not in dec.tiers
    assert "no-tools-local" in dec.notes["dropped_by_constraint"]
    assert dec.tiers == ("cloud",)


# ── context gate: dropped_by_context note + over-context-of-every-tier ────────
def test_min_context_drop_is_also_recorded_in_dropped_by_context():
    # A min_context drop lands in BOTH dropped_by_constraint (back-compat) and
    # the new, context-specific dropped_by_context bucket; a needs_tools drop
    # does NOT pollute dropped_by_context.
    intent = _intent(None, ("fast-local", "heavy-local"))  # local-only pool
    dec = route(intent, CONFIG, PROFILE, needs=Needs(min_context=100000))
    assert "fast-local" not in dec.tiers          # 32768 < 100000
    assert "heavy-local" in dec.tiers             # 131072 fits
    assert dec.notes["dropped_by_context"] == ("fast-local",)
    assert "fast-local" in dec.notes["dropped_by_constraint"]


def test_over_context_of_every_tier_yields_empty_result():
    # A request larger than EVERY tier's context_limit drops all of them; the
    # result is empty and dropped_by_context lists every tier — with NO deny or
    # metered drops, this is the clean signal the serve boundary maps to a 413.
    intent = _intent(None, ("fast-local", "heavy-local"))
    dec = route(intent, CONFIG, PROFILE, needs=Needs(min_context=200000))
    assert dec.tiers == ()
    assert dec.notes["empty"] is True
    assert set(dec.notes["dropped_by_context"]) == {"fast-local", "heavy-local"}
    assert dec.notes["dropped_by_deny"] == ()
    assert dec.notes["dropped_by_metered_gate"] == ()


def test_conservative_margin_request_just_under_limit_is_not_dropped():
    # A request whose estimated size is just UNDER a tier's context_limit must
    # NOT be dropped (no boundary false-reject). The filter is strict `>`, so at
    # or below the limit the tier is kept.
    intent = _intent(None, ("fast-local",))  # fast-local context_limit = 32768
    below = route(intent, CONFIG, PROFILE, needs=Needs(min_context=32767))
    assert below.tiers == ("fast-local",)
    at = route(intent, CONFIG, PROFILE, needs=Needs(min_context=32768))
    assert at.tiers == ("fast-local",)          # == limit is NOT over
    over = route(intent, CONFIG, PROFILE, needs=Needs(min_context=32769))
    assert over.tiers == ()                     # one past the limit -> dropped
    assert over.notes["dropped_by_context"] == ("fast-local",)


# ── None work class skips the deny filter (custom preset trusts the pool) ─────
def test_none_work_class_skips_deny_filter():
    # A local-only pool isolates the deny-filter behaviour from the T002 metered
    # gate (which now drops a cloud tier for a None work-class too). Both locals
    # survive: the quality deny-filter is skipped for a None work-class.
    intent = _intent(None, ("fast-local", "heavy-local"))
    dec = route(intent, CONFIG, PROFILE)
    assert dec.tiers == ("fast-local", "heavy-local")
    assert dec.notes["dropped_by_deny"] == ()


# ── robustness: a pool id absent from config is dropped + noted, never raised ──
def test_missing_pool_id_dropped_and_noted():
    # work_class="chat" with a local-only fallback so the metered gate doesn't
    # interfere; the test isolates the missing-id drop (ghost is unknown to config).
    intent = _intent("chat", ("ghost", "fast-local"))
    dec = route(intent, CONFIG, PROFILE)
    assert "ghost" not in dec.tiers
    assert "ghost" in dec.notes["dropped_missing"]
    # chat: fast-local is allow; the unknown id is dropped, leaving the local tier.
    assert dec.tiers == ("fast-local",)


def test_empty_result_allowed_and_noted():
    # A pool of only-denied locals for planning collapses to nothing.
    intent = _intent("planning", ("fast-local", "heavy-local"))
    dec = route(intent, CONFIG, PROFILE)
    assert dec.tiers == ()
    assert dec.notes["empty"] is True


def test_decision_is_hashable_and_notes_immutable():
    intent = resolve(_req("quick-edit"), CONFIG)
    dec = route(intent, CONFIG, PROFILE)
    assert hash(dec) is not None  # notes excluded from __hash__
    try:
        dec.notes["x"] = 1
    except TypeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("notes must be a read-only mapping")


# ── ProfileStore fail-closed defaults (FIX A/B/C) ─────────────────────────────
# These exercise the store directly (it has no separate test module; the policy
# tests already import its symbols). THEME: the deny gate fails closed.
def test_unmeasured_local_planning_defaults_deny():
    # An empty store has NO entry for ("gpu0", "planning"): a local tier on the
    # eval-weak planning class must default to DENY (not allow-with-verify), but
    # the same pair as a cloud tier (is_cloud=True) stays allow.
    store = ProfileStore({})
    assert store.decision("gpu0", "planning") == "deny"
    assert store.decision("gpu0", "planning", is_cloud=True) == "allow"
    # multi-file-refactor is the other high-risk local class.
    assert store.decision("gpu0", "multi-file-refactor") == "deny"
    assert "planning" in HIGH_RISK_LOCAL_CLASSES


def test_unmeasured_local_chat_allow_with_verify():
    # A non-high-risk class on an unmeasured local tier is use-but-verify, not
    # deny and not a blind allow.
    store = ProfileStore({})
    assert store.decision("gpu0", "chat") == "allow-with-verify"
    assert store.decision("gpu0", "review") == "allow-with-verify"


def test_table_consulted_for_none_workclass():
    # FIX B: a stored (tier, None) verdict must be honored by decision(), score()
    # AND entry() — the None short-circuit must not hide the table.
    store = ProfileStore({("gpu0", None): ProfileEntry("deny", 0.2, 1, None)})
    assert store.decision("gpu0", None) == "deny"        # not the None->allow default
    assert store.score("gpu0", None) == 0.2              # not the 0.5 default
    assert store.entry("gpu0", None).decision == "deny"  # all three agree
    # A None key with no stored entry still falls back to allow / 0.5.
    assert store.decision("other", None) == "allow"
    assert store.score("other", None) == 0.5


def test_profile_entry_rejects_bad_decision():
    # FIX C: a malformed verdict cannot be constructed, so it cannot exist to be
    # mis-compared by the policy's == "deny" gate.
    with pytest.raises(ValueError):
        ProfileEntry("DENY", 0.2, 1, None)      # wrong case
    with pytest.raises(ValueError):
        ProfileEntry("deny ", 0.2, 1, None)     # trailing space
    with pytest.raises(ValueError):
        ProfileEntry("block", 0.2, 1, None)     # not in the closed set
    # The three valid verdicts construct fine.
    for d in ("allow", "allow-with-verify", "deny"):
        assert ProfileEntry(d, 0.5, 1, None).decision == d


def test_score_and_entry_have_coverage():
    # Seeded pair: entry present, decision/score agree with the seed.
    e = PROFILE.entry("cloud", "planning")
    assert e is not None and e.decision == "allow"
    assert PROFILE.decision("cloud", "planning", is_cloud=True) == "allow"
    assert PROFILE.score("cloud", "planning") == e.quality_score
    # Unseeded pair: no entry, score is the 0.5 fallback.
    assert PROFILE.entry("gpu0", "planning") is None
    assert PROFILE.score("gpu0", "planning") == 0.5
    # None work-class, unseeded: no entry, 0.5.
    assert PROFILE.entry("cloud", None) is None
    assert PROFILE.score("cloud", None) == 0.5


# ── policy: fail-closed wiring, gate visibility, de-dup, robustness ───────────
def _gpu0_planning_config(tmp_path):
    """A config with an UNSEEDED local tier 'gpu0' in the planning pool.

    metered_cloud=["planning"] so cloud is a candidate for planning; the
    quality gate can then prove gpu0 (unmeasured local) is denied while
    cloud (always-available) is kept.
    """
    body = """\
[router]
mapping_version = "test.gpu0"
metered_cloud = ["planning"]

[[router.tiers]]
id            = "gpu0"
base_url      = "http://127.0.0.1:31000/v1"
dialect       = "openai"
context_limit = 32768
privacy       = "local"
tool_support  = true
auth_env      = "GPU0_KEY"

[[router.tiers]]
id            = "cloud"
base_url      = "https://api.anthropic.com"
dialect       = "anthropic"
context_limit = 200000
privacy       = "cloud"
tool_support  = true
auth_env      = "ANTHROPIC_API_KEY"

[router.presets]
planning = ["gpu0", "cloud"]
"""
    p = tmp_path / "gpu0.toml"
    p.write_text(body, encoding="utf-8")
    return load(str(p))


def test_unmeasured_local_planning_tier_denied(tmp_path):
    # Portability: an unseeded local tier in a planning pool is dropped by the
    # fail-closed default; cloud is kept. The default profile has no 'gpu0' entry.
    cfg = _gpu0_planning_config(tmp_path)
    intent = resolve(_req("planning"), cfg)
    dec = route(intent, cfg, PROFILE)
    assert "gpu0" not in dec.tiers
    assert "gpu0" in dec.notes["dropped_by_deny"]
    assert dec.tiers == ("cloud",)


def test_none_workclass_records_gate_off():
    # FIX D: a custom preset (work_class None) is NOT quality-gated, but that
    # bypass is auditable in the notes.
    intent = _intent(None, ("fast-local", "cloud"))
    dec = route(intent, CONFIG, PROFILE)
    assert dec.notes["quality_gate"].startswith("off")
    assert dec.notes["dropped_by_deny"] == ()
    # A gated (work_class present) request records the gate as on.
    gated = route(_intent("chat", ("fast-local", "cloud")), CONFIG, PROFILE)
    assert gated.notes["quality_gate"] == "on"


def test_duplicate_pool_id_deduped():
    # FIX F: a duplicate tier id must not appear twice in the result; the drop is
    # noted, first-occurrence order is preserved.
    # A local-only pool (both allow for chat) isolates de-duplication from the
    # T002 metered gate (which would otherwise drop a cloud tier here).
    intent = _intent("chat", ("fast-local", "heavy-local", "fast-local"))
    dec = route(intent, CONFIG, PROFILE)
    assert dec.tiers == ("fast-local", "heavy-local")
    assert dec.tiers.count("fast-local") == 1
    assert "fast-local" in dec.notes["dropped_duplicate"]


def test_route_never_raises_on_bad_intent():
    # FIX E: a malformed Intent (candidate_tiers=None) degrades to an empty
    # decision instead of raising.
    bad = Intent(
        work_class="chat",
        preset=None,
        source="test",
        candidate_tiers=None,  # type: ignore[arg-type]
        ambiguous=False,
        decision=MappingProxyType({}),
    )
    dec = route(bad, CONFIG, PROFILE)
    assert isinstance(dec, RoutingDecision)
    assert dec.tiers == ()
    assert dec.notes["empty"] is True


def test_malformed_verdict_cannot_leak():
    # The policy gate compares == "deny"; a casing/typo variant can't leak through
    # because it can't be stored — ProfileEntry validation rejects it at the door.
    with pytest.raises(ValueError):
        ProfileStore({("heavy-local", "review"): ProfileEntry("Deny", 0.2, 1, None)})
    # Only the canonical lowercase "deny" exists, so the gate is trustworthy.
    profile = ProfileStore({("heavy-local", "review"): ProfileEntry("deny", 0.2, 1, None)})
    dec = route(_intent("review", ("heavy-local", "cloud")), CONFIG, profile)
    assert "heavy-local" not in dec.tiers


# ── FIX #5 (record_grade fail-closed default) ─────────────────────────────────
def test_record_grade_new_high_risk_local_pair_defaults_deny():
    """record_grade on a NEW unmeasured (planning, local) pair must default the
    decision to 'deny', matching what decision() would give for the unmeasured pair.

    Before the fix the new-row default was 'allow-with-verify' for ALL classes,
    making a recorded grade on an unmeasured high-risk-local pair MORE permissive
    than the gate's own fail-closed default.
    """
    store = ProfileStore({})
    # Precondition: unmeasured pair defaults to deny.
    assert store.decision("gpu0", "planning") == "deny"
    # Record a grade with no explicit decision — should remain deny.
    store.record_grade("gpu0", "planning", score=0.7)
    e = store.entry("gpu0", "planning")
    assert e is not None
    assert e.decision == "deny", (
        f"record_grade on unmeasured planning/local pair should default to 'deny', "
        f"got {e.decision!r}"
    )


def test_record_grade_new_non_high_risk_pair_defaults_allow_with_verify():
    """record_grade on a NEW unmeasured chat pair defaults to 'allow-with-verify'."""
    store = ProfileStore({})
    assert store.decision("gpu0", "chat") == "allow-with-verify"
    store.record_grade("gpu0", "chat", score=0.8)
    e = store.entry("gpu0", "chat")
    assert e is not None
    assert e.decision == "allow-with-verify"


def test_record_grade_degenerate_weight_no_crash_or_corrupt():
    """A zero or negative weight must not ZeroDivisionError or corrupt the mean.

    The weight is clamped to max(0, weight) in the update path, so a negative
    weight is treated as a no-op for the score (the observation still counts but
    doesn't subtract from the running mean). A zero weight leaves the score unchanged.
    """
    store = ProfileStore({("fast-local", "chat"): ProfileEntry("allow", 0.8, 5, None)})
    # weight=0: w=0, new_n=max(1,5+0)=5, score=(0.8*5 + 0.9*0)/5 = 0.8 (unchanged).
    e = store.record_grade("fast-local", "chat", score=0.9, weight=0)
    assert e is not None
    assert 0.0 <= e.quality_score <= 1.0, f"score {e.quality_score!r} out of range"
    # weight=-10: clamped to w=0, same no-op. No crash, no negative score.
    e2 = store.record_grade("fast-local", "chat", score=0.5, weight=-10)
    assert e2 is not None
    assert 0.0 <= e2.quality_score <= 1.0, f"score {e2.quality_score!r} out of range"


# ── FIX #4 (stale row not trusted as allow) ───────────────────────────────────
def test_stale_allow_row_downgraded_to_allow_with_verify():
    """A stale 'allow' row must not be trusted as 'allow'; decision() must return
    'allow-with-verify' so the live verify gate runs.

    A stale 'deny' row stays 'deny' (fail-closed).
    """
    from anvil_serving.router.fingerprint import serve_fingerprint

    store = ProfileStore({
        ("fast-local", "chat"): ProfileEntry("allow", 0.9, 5, None),
        ("fast-local", "review"): ProfileEntry("deny", 0.2, 5, None),
    })
    fp0 = serve_fingerprint({"id": "fast-local", "model": "a"})
    fp1 = serve_fingerprint({"id": "fast-local", "model": "b"})
    store.apply_fingerprint("fast-local", fp0)   # baseline
    store.apply_fingerprint("fast-local", fp1)   # serve changed -> stale

    assert store.is_stale("fast-local", "chat") is True
    assert store.is_stale("fast-local", "review") is True

    # Stale 'allow' downgraded to 'allow-with-verify'.
    assert store.decision("fast-local", "chat") == "allow-with-verify"
    # Stale 'deny' remains 'deny' (fail-closed).
    assert store.decision("fast-local", "review") == "deny"

    # After a fresh grade the row is no longer stale -> 'allow' restored.
    store.record_grade("fast-local", "chat", score=0.85)
    assert store.is_stale("fast-local", "chat") is False
    assert store.decision("fast-local", "chat") == "allow"


def test_stale_allow_row_not_routed_direct_via_policy():
    """policy.route() with a stale 'allow' local row keeps the tier in the result
    (it's not denied) but profile.decision() returns 'allow-with-verify', so the
    serve path runs the verify gate rather than streaming directly.
    """
    from anvil_serving.router.fingerprint import serve_fingerprint

    store = ProfileStore({
        ("fast-local", "chat"): ProfileEntry("allow", 0.9, 5, None),
        ("cloud", "chat"): ProfileEntry("allow", 0.95, 5, None),
    })
    fp0 = serve_fingerprint({"id": "fast-local", "model": "a"})
    fp1 = serve_fingerprint({"id": "fast-local", "model": "b"})
    store.apply_fingerprint("fast-local", fp0)
    store.apply_fingerprint("fast-local", fp1)   # stale

    intent = _intent("chat", ("fast-local", "cloud"))
    dec = route(intent, CONFIG, store)

    # The stale tier is not denied — it's in the routing result.
    assert "fast-local" in dec.tiers
    assert "fast-local" not in dec.notes["dropped_by_deny"]

    # But the profile says allow-with-verify (the serve path should verify).
    assert store.decision("fast-local", "chat") == "allow-with-verify"


# ── FIX #9 (reworked): a caller pin is a PREFERENCE within the gate, never a ──
#    deny BYPASS. intent.source == "pinned" is caller-controlled (the wire
#    `model` naming a tier id), so a pin must never let an untrusted caller reach
#    a tier the profile DENIES for the work-class.
def test_pin_to_allowed_tier_is_honored():
    """A pin to a tier the gate ALLOWS for the work-class is honored (used directly)."""
    # fast-local is 'allow' for chat in the default profile.
    assert PROFILE.decision("fast-local", "chat") == "allow"  # precondition
    intent = _intent("chat", ("fast-local",), source="pinned")
    dec = route(intent, CONFIG, PROFILE)

    # Pin honored: the allowed pinned tier is the routed result.
    assert dec.tiers == ("fast-local",)
    assert "fast-local" not in dec.notes["dropped_by_deny"]
    # Normal gate (no override redirect occurred).
    assert dec.notes["quality_gate"] == "on"


def test_pin_to_denied_tier_routes_to_allowed_tier_not_the_pin():
    """SECURITY: a pin to a tier the gate DENIES must NOT be served by that tier.

    A caller pinning fast-local for multi-file-refactor (which the profile DENIES
    for fast-local) must be routed via the work-class's normal gated pool to an
    ALLOWED tier — never the denied pin. This is the gate-bypass the router exists
    to prevent, now caller-triggerable via the wire `model` field.

    The review pool (multi-file-refactor's gated pool) is (heavy-local, cloud).
    With metered_cloud=["planning"] in the example config, cloud is gated for
    multi-file-refactor, so only heavy-local reaches the final result.
    """
    # Precondition: fast-local is denied for multi-file-refactor; the review pool
    # (the work-class's gated pool) is (heavy-local, cloud).
    assert PROFILE.decision("fast-local", "multi-file-refactor") == "deny"
    intent = _intent("multi-file-refactor", ("fast-local",), source="pinned")
    dec = route(intent, CONFIG, PROFILE)

    # The denied pinned tier is NOT in the result.
    assert "fast-local" not in dec.tiers
    # Routed via the work-class's gated pool; cloud is then gated (not in
    # metered_cloud for multi-file-refactor), so only heavy-local remains.
    assert dec.tiers == ("heavy-local",)
    # The served (first) tier is an allowed tier, not the denied pin.
    assert dec.tiers[0] == "heavy-local"
    assert PROFILE.decision("heavy-local", "multi-file-refactor") != "deny"
    # The override is auditable.
    assert dec.notes["quality_gate"] == (
        "pin fast-local denied for multi-file-refactor; routed via gated pool"
    )


def test_pin_to_denied_planning_tier_routes_to_cloud():
    """A pin to a local tier denied for planning routes to cloud (the gated pool)."""
    assert PROFILE.decision("fast-local", "planning") == "deny"  # precondition
    intent = _intent("planning", ("fast-local",), source="pinned")
    dec = route(intent, CONFIG, PROFILE)

    # fast-local (denied) is not served; the planning gated pool is (cloud,).
    assert "fast-local" not in dec.tiers
    assert dec.tiers == ("cloud",)
    assert "pin fast-local denied for planning" in dec.notes["quality_gate"]


def test_pin_to_denied_tier_with_all_denied_pool_yields_clean_empty():
    """When the work-class's gated pool is ALSO all-denied, the result is empty.

    A pin to a denied tier whose fall-through pool is itself fully denied yields an
    empty decision (the serve boundary turns this into a clean NoAvailableTierError
    / 503) — never the denied pinned tier, and never a silent serve.
    """
    # A local-only config: planning pool is a single unseeded local tier (denied).
    body = """\
[router]
mapping_version = "test.localonly"

[[router.tiers]]
id            = "gpu0"
base_url      = "http://127.0.0.1:31000/v1"
dialect       = "openai"
context_limit = 32768
privacy       = "local"
tool_support  = true
auth_env      = "GPU0_KEY"

[router.presets]
planning = ["gpu0"]
"""
    import tempfile
    import pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    p = d / "localonly.toml"
    p.write_text(body, encoding="utf-8")
    cfg = load(str(p))

    # Pin gpu0 for planning: denied; fall-through pool = (gpu0,) -> also denied.
    intent = _intent("planning", ("gpu0",), source="pinned")
    dec = route(intent, cfg, PROFILE)

    assert "gpu0" not in dec.tiers
    assert dec.tiers == ()                 # empty -> clean 503 at the serve boundary
    assert dec.notes["empty"] is True
    assert "pin gpu0 denied for planning" in dec.notes["quality_gate"]


def test_non_pinned_planning_still_denied():
    """A non-pinned planning request still hits the deny filter normally."""
    intent = _intent("planning", ("fast-local", "heavy-local", "cloud"), source="declared-preset")
    dec = route(intent, CONFIG, PROFILE)
    assert "fast-local" not in dec.tiers
    assert "heavy-local" not in dec.tiers
    assert dec.tiers == ("cloud",)


# ── T002: per-intent metered-cloud gate (ADR-0001 / advise-and-defer:T002) ────
#
# The metered-cloud gate enforces: a privacy=="cloud" tier is a routing candidate
# ONLY for work-classes listed in RouterConfig.metered_cloud.  An empty/absent
# metered_cloud means cloud is NEVER a candidate.

def _cloud_config(tmp_path, metered_cloud: list, extra_presets: str = "") -> object:
    """Return a RouterConfig with fast-local + cloud tiers and the given metered_cloud."""
    lines = [
        "[router]",
        'mapping_version = "test.mc"',
    ]
    if metered_cloud is not None:
        vals = ", ".join(f'"{w}"' for w in metered_cloud)
        lines.append(f"metered_cloud = [{vals}]")
    lines += [
        "",
        "[[router.tiers]]",
        'id            = "fast-local"',
        'base_url      = "http://127.0.0.1:30001/v1"',
        'dialect       = "openai"',
        "context_limit = 32768",
        'privacy       = "local"',
        "tool_support  = true",
        'auth_env      = "ANVIL_FAST_LOCAL_KEY"',
        "",
        "[[router.tiers]]",
        'id            = "cloud"',
        'base_url      = "https://api.anthropic.com"',
        'dialect       = "anthropic"',
        "context_limit = 200000",
        'privacy       = "cloud"',
        "tool_support  = true",
        'auth_env      = "ANTHROPIC_API_KEY"',
        "",
        "[router.presets]",
        'planning   = ["cloud"]',
        'chat       = ["fast-local", "cloud"]',
        'quick-edit = ["fast-local", "cloud"]',
    ]
    if extra_presets:
        lines.append(extra_presets)
    body = "\n".join(lines) + "\n"
    p = tmp_path / "mc.toml"
    p.write_text(body, encoding="utf-8")
    return load(str(p))


def test_metered_gate_empty_cloud_never_candidate(tmp_path):
    """With metered_cloud=[] (empty), a cloud tier is NEVER in the routed result,
    for every work-class.  This is the ADR-0001 invariant: no global 'use cloud'
    switch; cloud must be explicitly mapped."""
    cfg = _cloud_config(tmp_path, metered_cloud=[])

    for wc, candidate_tiers in [
        ("planning", ("cloud",)),
        ("bounded-edit", ("fast-local", "cloud")),
        ("chat", ("fast-local", "cloud")),
    ]:
        intent = _intent(wc, candidate_tiers)
        dec = route(intent, cfg, PROFILE)
        assert "cloud" not in dec.tiers, f"cloud must never be routed; work_class={wc!r}"
        assert "cloud" in dec.notes["dropped_by_metered_gate"], wc


def test_metered_gate_planning_only(tmp_path):
    """With metered_cloud=["planning"], cloud is a candidate ONLY for planning;
    for every other work-class it is gated out."""
    cfg = _cloud_config(tmp_path, metered_cloud=["planning"])

    # planning → cloud IS a candidate (and the only one after quality deny strips locals)
    intent_plan = _intent("planning", ("fast-local", "cloud"))
    dec_plan = route(intent_plan, cfg, PROFILE)
    assert "cloud" in dec_plan.tiers, "cloud must be a candidate for planning"
    assert "cloud" not in dec_plan.notes["dropped_by_metered_gate"]

    # bounded-edit → cloud gated
    intent_edit = _intent("bounded-edit", ("fast-local", "cloud"))
    dec_edit = route(intent_edit, cfg, PROFILE)
    assert "cloud" not in dec_edit.tiers, "cloud must be gated for bounded-edit"
    assert "cloud" in dec_edit.notes["dropped_by_metered_gate"]

    # chat → cloud gated
    intent_chat = _intent("chat", ("fast-local", "cloud"))
    dec_chat = route(intent_chat, cfg, PROFILE)
    assert "cloud" not in dec_chat.tiers, "cloud must be gated for chat"
    assert "cloud" in dec_chat.notes["dropped_by_metered_gate"]


def test_metered_gate_applies_to_none_work_class(tmp_path):
    """COST-SAFETY (closes the work_class=None bypass): a custom preset
    (work_class=None) whose pool names a cloud tier must NOT reach it when that
    cloud is not metered. With metered_cloud=[] the cloud tier is dropped even for
    a None work-class — a None can never appear in metered_cloud, so the gate
    fires. Guarantees 'empty map => cloud is never a candidate', custom presets
    included (ADR-0001 / advise-and-defer:T002)."""
    cfg = _cloud_config(tmp_path, metered_cloud=[])  # empty → cloud never

    # work_class=None: custom-preset mode — the gate STILL applies, cloud dropped,
    # only the local tier survives.
    intent = _intent(None, ("fast-local", "cloud"))
    dec = route(intent, cfg, PROFILE)
    assert "cloud" not in dec.tiers, "cloud must be gated for a None work_class too"
    assert dec.tiers == ("fast-local",)
    assert "cloud" in dec.notes["dropped_by_metered_gate"]


def test_metered_gate_none_work_class_cloud_only_pool_is_empty(tmp_path):
    """COST-SAFETY: a custom preset (work_class=None) whose pool is cloud-ONLY,
    with metered_cloud=[], collapses to an empty decision — the serve boundary
    turns this into a clean 503 rather than ever reaching the metered tier. This
    is the closed hole: a custom preset can no longer slip cloud past an empty map."""
    cfg = _cloud_config(tmp_path, metered_cloud=[])

    intent = _intent(None, ("cloud",))
    dec = route(intent, cfg, PROFILE)
    assert dec.tiers == ()  # empty -> clean 503 at the serve boundary
    assert dec.notes["empty"] is True
    assert "cloud" in dec.notes["dropped_by_metered_gate"]


def test_metered_gate_recorded_in_notes(tmp_path):
    """dropped_by_metered_gate is always present in notes and contains the
    ids of cloud tiers dropped by the gate."""
    cfg = _cloud_config(tmp_path, metered_cloud=[])

    intent = _intent("chat", ("fast-local", "cloud"))
    dec = route(intent, cfg, PROFILE)
    assert "dropped_by_metered_gate" in dec.notes
    assert "cloud" in dec.notes["dropped_by_metered_gate"]
    # fast-local (local tier) is never in the metered gate drop list
    assert "fast-local" not in dec.notes["dropped_by_metered_gate"]


def test_metered_gate_absent_metered_cloud_same_as_empty(tmp_path):
    """A config with no metered_cloud key is identical to metered_cloud=[]:
    cloud is never a candidate (default-safe / ADR-0001)."""
    # _cloud_config with metered_cloud=None omits the key from TOML entirely.
    cfg = _cloud_config(tmp_path, metered_cloud=None)
    assert cfg.metered_cloud == ()  # parsed as empty tuple

    intent = _intent("planning", ("cloud",))
    dec = route(intent, cfg, PROFILE)
    assert "cloud" not in dec.tiers
    assert "cloud" in dec.notes["dropped_by_metered_gate"]


def test_metered_gate_default_config_unaffected():
    """The default local-only config (example.toml) has no cloud tier and
    metered_cloud defaults to (); the gate has no effect on local-only routing."""
    from anvil_serving.router.config import load as _load

    default_cfg_path = str(
        pathlib.Path(__file__).resolve().parents[2] / "configs" / "example.toml"
    )
    cfg = _load(default_cfg_path)
    assert cfg.metered_cloud == ()
    # All tiers are local; route a planning intent through the default config.
    intent_plan = resolve(_req("planning"), cfg)
    dec = route(intent_plan, cfg, PROFILE)
    # No cloud tier present at all: metered gate has nothing to drop.
    assert dec.notes["dropped_by_metered_gate"] == ()
    # Local tiers are still subject to the quality gate (planning -> locals denied).
    # The example.toml planning pool is (heavy-local,) which is denied for planning.
    assert "heavy-local" in dec.notes["dropped_by_deny"]
