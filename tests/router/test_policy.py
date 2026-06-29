"""Tests for the residency-aware routing policy (harness-router:T005).

Proves the acceptance criteria against the real ``configs/example.toml``:
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

from anvil_serving.router.config import load
from anvil_serving.router.intent import Intent, resolve
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.policy import Needs, RoutingDecision, route
from anvil_serving.router.profile_store import (
    ProfileEntry,
    ProfileStore,
    default_profile,
)

# CWD-independent: example.toml at <repo>/configs/example.toml; this file is at
# <repo>/tests/router/test_policy.py (parents[2] == repo root).
EXAMPLE = pathlib.Path(__file__).resolve().parents[2] / "configs" / "example.toml"
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


def test_ac1_deny_entry_never_routed_direct_store():
    # A hand-built store where (heavy-local, review) is deny: heavy-local must be
    # absent for a review intent even though the example profile allows it.
    profile = ProfileStore({("heavy-local", "review"): ProfileEntry("deny", 0.2, 1, None)})
    intent = _intent("review", ("heavy-local", "cloud"))
    dec = route(intent, CONFIG, profile)
    assert "heavy-local" not in dec.tiers
    assert "heavy-local" in dec.notes["dropped_by_deny"]
    assert dec.tiers == ("cloud",)  # cloud unmeasured -> allow-with-verify, kept


# ── AC2: the pool comes from config, not hard-coded ──────────────────────────
def test_ac2_result_is_subset_of_candidate_pool():
    for model in ("planning", "quick-edit", "review", "long-context", "chat"):
        intent = resolve(_req(model), CONFIG)
        dec = route(intent, CONFIG, PROFILE)
        assert set(dec.tiers) <= set(intent.candidate_tiers), model


def test_ac2_routed_pool_follows_config_preset(tmp_path):
    # A temp config whose planning pool is two cloud tiers in a non-default order;
    # route() must follow it rather than any baked-in default.
    body = """\
[router]
mapping_version = "test.0"

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
    intent = resolve(_req("quick-edit"), CONFIG)  # pool [fast, heavy, cloud]
    dec = route(intent, CONFIG, PROFILE, needs=Needs(min_context=100000))
    assert "fast-local" not in dec.tiers  # ctx 32768 < 100000
    assert "heavy-local" in dec.tiers     # 131072 fits
    assert "cloud" in dec.tiers           # 200000 fits
    assert "fast-local" in dec.notes["dropped_by_constraint"]


def test_constraint_needs_tools_drops_no_tool_tier(tmp_path):
    body = """\
[router]
mapping_version = "test.0"

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


# ── None work class skips the deny filter (custom preset trusts the pool) ─────
def test_none_work_class_skips_deny_filter():
    intent = _intent(None, ("fast-local", "cloud"))
    dec = route(intent, CONFIG, PROFILE)
    assert dec.tiers == ("fast-local", "cloud")
    assert dec.notes["dropped_by_deny"] == ()


# ── robustness: a pool id absent from config is dropped + noted, never raised ──
def test_missing_pool_id_dropped_and_noted():
    intent = _intent("chat", ("ghost", "cloud"))
    dec = route(intent, CONFIG, PROFILE)
    assert "ghost" not in dec.tiers
    assert "ghost" in dec.notes["dropped_missing"]
    assert dec.tiers == ("cloud",)


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
