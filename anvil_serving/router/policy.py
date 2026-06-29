"""Residency-aware routing policy (harness-router:T005).

Takes a resolved :class:`~anvil_serving.router.intent.Intent` plus the quality
:class:`~anvil_serving.router.profile_store.ProfileStore` and turns the
config-derived candidate pool into a final, ORDERED list of tier ids the
dispatcher tries in turn. Three filters and one reorder, in this order:

1. **Hard constraints** (optional :class:`Needs`) — drop a tier that cannot fit
   the requested context window or lacks tool support.
2. **Profile deny** — drop a tier whose ``(tier, work_class)`` verdict is
   ``deny``. This is the eval gate: ``planning`` never routes to a local tier.
3. **Cost order** — preserve the pool order, which already encodes the
   config's cost preference (fast -> heavy -> cloud). Score-based reranking is a
   documented future refinement; keeping config order makes the MVP behavior
   deterministic and cheap.
4. **Residency** — the local tiers form a single-resident swap group (R013):
   only one local model is loaded in the multiplexer at a time. If a local tier
   is currently resident, any OTHER local tier is DEFERRED behind the resident
   local and all cloud tiers, so picking a non-resident local (which forces a
   model swap) becomes a last resort. This keeps an alternating fast/heavy
   workload from thrashing the multiplexer (AC3).

The candidate pool is always config-derived (via ``intent.candidate_tiers``),
never hard-coded (AC2). :func:`route` is robust: a pool id absent from the
config is dropped-and-noted rather than raised, and an empty result is allowed
(the caller falls back) but recorded in the notes.

Stdlib-only; mirrors the frozen-dataclass style of ``config.py`` / ``intent.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional

from .config import RouterConfig
from .intent import Intent
from .profile_store import ProfileStore


@dataclass(frozen=True)
class Needs:
    """Optional hard-constraint inputs for one routing decision.

    ``min_context`` is the minimum context window (in the same unit as a tier's
    ``context_limit``) the request must fit; ``needs_tools`` requires the tier to
    support tool calls. Both default to "no constraint".
    """

    min_context: int = 0
    needs_tools: bool = False


@dataclass(frozen=True)
class RoutingDecision:
    """The final ordered tier list plus an audit trail.

    ``notes`` records what happened (the original pool, tiers dropped by deny /
    by constraint / as missing, residency deferrals, whether the result is
    empty). It is wrapped read-only via ``MappingProxyType`` and **excluded from
    equality and hashing** (``compare=False, hash=False``) so a frozen
    ``RoutingDecision`` stays hashable and two decisions with the same tier order
    compare equal regardless of their audit detail.
    """

    tiers: tuple[str, ...]
    work_class: Optional[str]
    notes: Mapping[str, Any] = field(compare=False, hash=False)


def local_tier_ids(config: RouterConfig) -> frozenset[str]:
    """Ids of the ``privacy == "local"`` tiers — the single-resident swap group (R013)."""
    return frozenset(t.id for t in config.tiers if t.privacy == "local")


def route(
    intent: Intent,
    config: RouterConfig,
    profile: ProfileStore,
    *,
    residency: Optional[str] = None,
    needs: Optional[Needs] = None,
) -> RoutingDecision:
    """Resolve ``intent`` into an ordered, filtered list of candidate tier ids.

    Never raises on a pool id missing from ``config`` (it is dropped and noted);
    an empty result is allowed (and noted). See the module docstring for the
    filter/reorder pipeline. ``residency`` is the currently-resident local tier
    id (if any); ``needs`` carries optional hard constraints.
    """
    work_class = intent.work_class
    pool = tuple(intent.candidate_tiers)  # AC2: config-derived, not hard-coded.
    valid_ids = {t.id for t in config.tiers}

    dropped_missing: list[str] = []
    dropped_by_constraint: list[str] = []
    dropped_by_deny: list[str] = []

    # 0. Drop any pool id the config doesn't know (robustness: never raise).
    survivors: list[str] = []
    for tid in pool:
        if tid in valid_ids:
            survivors.append(tid)
        else:
            dropped_missing.append(tid)

    # 1. Hard-constraint filter (only when needs are supplied).
    if needs is not None:
        kept: list[str] = []
        for tid in survivors:
            t = config.tier(tid)  # safe: tid is in valid_ids
            if needs.min_context > t.context_limit:
                dropped_by_constraint.append(tid)
                continue
            if needs.needs_tools and not t.tool_support:
                dropped_by_constraint.append(tid)
                continue
            kept.append(tid)
        survivors = kept

    # 2. Profile-deny filter (AC1). Skipped for a None work class (custom preset:
    #    trust the config pool — the store has no taxonomy key to gate on).
    if work_class is not None:
        kept = []
        for tid in survivors:
            if profile.decision(tid, work_class) == "deny":
                dropped_by_deny.append(tid)
            else:
                kept.append(tid)
        survivors = kept

    # 3. Order: preserve the pool's config cost order (fast -> heavy -> cloud).
    ordered = survivors

    # 4. Residency reorder (AC3). When a local tier is resident, defer every OTHER
    #    local behind the resident local + all cloud tiers, so choosing a
    #    swap-forcing non-resident local is a last resort.
    locals_ = local_tier_ids(config)
    residency_deferred: list[str] = []
    if residency is not None and residency in locals_:
        deferred = [tid for tid in ordered if tid in locals_ and tid != residency]
        residency_deferred = list(deferred)
        result = [tid for tid in ordered if not (tid in locals_ and tid != residency)] + deferred
    else:
        # residency is None or a cloud id: the first pick loads one local model,
        # an unavoidable single swap. Leave the cost order untouched.
        result = list(ordered)

    notes = MappingProxyType({
        "work_class": work_class,
        "pool": pool,
        "residency": residency,
        "dropped_missing": tuple(dropped_missing),
        "dropped_by_constraint": tuple(dropped_by_constraint),
        "dropped_by_deny": tuple(dropped_by_deny),
        "residency_deferred": tuple(residency_deferred),
        "empty": not result,
    })
    return RoutingDecision(tuple(result), work_class, notes=notes)
