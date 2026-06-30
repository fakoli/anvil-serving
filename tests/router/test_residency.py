"""Residency-aware routing: the AC3 anti-thrash property (harness-router:T005).

The local tiers share one multiplexer slot (R013): loading a non-resident local
costs a model swap. The policy DEFERS a non-resident local behind the resident
local and the cloud tiers, so an alternating fast/heavy workload does NOT trigger
a swap on every request. These tests assert the swap count stays bounded.
"""
from __future__ import annotations

import pathlib
from types import MappingProxyType

from anvil_serving.router.config import load
from anvil_serving.router.intent import Intent, resolve
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.policy import local_tier_ids, route
from anvil_serving.router.profile_store import default_profile

EXAMPLE = pathlib.Path(__file__).resolve().parents[2] / "configs" / "example.toml"
CONFIG = load(str(EXAMPLE))
PROFILE = default_profile()
LOCALS = local_tier_ids(CONFIG)


def _req(model, text="hello there"):
    return InternalRequest(model=model, messages=[Message("user", text)], raw={})


def _intent(work_class, candidate_tiers):
    return Intent(
        work_class=work_class,
        preset=None,
        source="test",
        candidate_tiers=tuple(candidate_tiers),
        ambiguous=False,
        decision=MappingProxyType({}),
    )


# ── AC3: alternating fast/heavy workload does not thrash the multiplexer ──────
def test_alternating_workload_bounded_swaps():
    # quick-edit prefers fast-local; long-context prefers heavy-local. Naively
    # honoring each preference would swap the resident local every request.
    resident = None
    swaps = 0
    picks = []
    for i in range(12):
        model = "quick-edit" if i % 2 == 0 else "long-context"
        intent = resolve(_req(model), CONFIG)
        dec = route(intent, CONFIG, PROFILE, residency=resident)
        assert dec.tiers, f"step {i}: empty result"
        top = dec.tiers[0]
        picks.append(top)
        if top in LOCALS and top != resident:
            swaps += 1
            resident = top

    # The first request loads one local model (one unavoidable swap); after that
    # the policy keeps deferring the non-resident local behind cloud, so it never
    # swaps again. Definitely NOT once-per-request (which would be ~6).
    assert swaps <= 2, f"thrashing: {swaps} swaps over {len(picks)} requests; picks={picks}"


def test_alternating_workload_starting_heavy_also_bounded():
    # Symmetry: starting with the heavy-preferring class first must also bound.
    resident = None
    swaps = 0
    for i in range(12):
        model = "long-context" if i % 2 == 0 else "quick-edit"
        intent = resolve(_req(model), CONFIG)
        top = route(intent, CONFIG, PROFILE, residency=resident).tiers[0]
        if top in LOCALS and top != resident:
            swaps += 1
            resident = top
    assert swaps <= 2


# ── direct unit: a resident local defers the OTHER local to last ─────────────
def test_resident_fast_defers_heavy_behind_cloud():
    # Heavy-preferring pool (fast-local also present, allow-with-verify for review)
    # with fast-local resident: heavy-local must land LAST, behind fast and cloud.
    intent = _intent("review", ("fast-local", "heavy-local", "cloud"))
    dec = route(intent, CONFIG, PROFILE, residency="fast-local")
    assert dec.tiers[-1] == "heavy-local"
    assert dec.tiers.index("fast-local") < dec.tiers.index("heavy-local")
    assert dec.tiers.index("cloud") < dec.tiers.index("heavy-local")
    assert "heavy-local" in dec.notes["residency_deferred"]


def test_resident_heavy_defers_fast_behind_cloud():
    intent = _intent("bounded-edit", ("fast-local", "heavy-local", "cloud"))
    dec = route(intent, CONFIG, PROFILE, residency="heavy-local")
    assert dec.tiers[-1] == "fast-local"
    assert dec.tiers.index("heavy-local") < dec.tiers.index("fast-local")
    assert dec.tiers.index("cloud") < dec.tiers.index("fast-local")
    assert "fast-local" in dec.notes["residency_deferred"]


def test_no_residency_leaves_cost_order():
    # No resident local: the first pick loads one model (one unavoidable swap);
    # the cost order (fast -> heavy -> cloud) is left untouched.
    intent = _intent("bounded-edit", ("fast-local", "heavy-local", "cloud"))
    dec = route(intent, CONFIG, PROFILE, residency=None)
    assert dec.tiers == ("fast-local", "heavy-local", "cloud")
    assert dec.notes["residency_deferred"] == ()


def test_cloud_residency_leaves_cost_order():
    # A non-local residency value (e.g. cloud) is not a swap-group member, so the
    # local cost order is preserved.
    intent = _intent("bounded-edit", ("fast-local", "heavy-local", "cloud"))
    dec = route(intent, CONFIG, PROFILE, residency="cloud")
    assert dec.tiers == ("fast-local", "heavy-local", "cloud")
    assert dec.notes["residency_deferred"] == ()


# ── HONEST LIMIT: a local-only disjoint pool is NOT bounded (no fallback) ─────
def test_local_only_pool_can_still_thrash():
    # The swap bound holds only when the pool has the resident-local tier OR a
    # cloud / always-available tier to defer the non-resident local behind. A
    # privacy-strict LOCAL-ONLY pool with disjoint per-class locals (bounded-edit
    # -> [fast-local], long-context -> [heavy-local]) has nothing to defer behind,
    # so the non-resident local is still picked and a swap happens every request.
    # Asserted so the limitation is captured, not hidden (contrast the bounded
    # cloud-inclusive tests above).
    resident = None
    swaps = 0
    picks = []
    n = 12
    for i in range(n):
        if i % 2 == 0:
            intent = _intent("bounded-edit", ("fast-local",))   # fast-local: allow
        else:
            intent = _intent("long-context", ("heavy-local",))  # heavy-local: allow
        dec = route(intent, CONFIG, PROFILE, residency=resident)
        assert dec.tiers, f"step {i}: empty result (deny gate must not strip these)"
        top = dec.tiers[0]
        picks.append(top)
        if top in LOCALS and top != resident:
            swaps += 1
            resident = top
    # One swap per request: the bound does NOT hold for a disjoint local-only pool.
    assert swaps == n, f"expected unbounded thrash (one swap/request), got {swaps}; picks={picks}"
