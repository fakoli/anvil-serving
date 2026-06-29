"""Residency-aware routing policy (harness-router:T005).

Takes a resolved :class:`~anvil_serving.router.intent.Intent` plus the quality
:class:`~anvil_serving.router.profile_store.ProfileStore` and turns the
config-derived candidate pool into a final, ORDERED list of tier ids the
dispatcher tries in turn. Three filters and one reorder, in this order:

1. **Hard constraints** (optional :class:`Needs`) — drop a tier that cannot fit
   the requested context window or lacks tool support.
2. **Profile deny (fail-closed)** — drop a tier whose ``(tier, work_class)``
   verdict is ``deny``. This is the eval gate: ``planning`` never routes to a
   local tier. The verdict is resolved WITH the tier's privacy
   (``is_cloud=t.privacy == "cloud"``), so an UNMEASURED *local* tier on an
   eval-proven-weak class (planning, multi-file-refactor) biases to ``deny`` —
   the safe direction — while a cloud tier stays allowed. Skipped for a ``None``
   work class (custom preset: no taxonomy key to gate on; recorded as
   ``quality_gate: "off"`` in the notes so the bypass is auditable).
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

    Never raises (matches ``classify``/``resolve`` posture): a pool id missing
    from ``config`` is dropped and noted, a duplicate pool id is de-duplicated
    and noted, and a malformed ``intent`` degrades to an empty
    :class:`RoutingDecision` with ``notes["error"]`` rather than propagating.
    An empty result is allowed (and noted). See the module docstring for the
    filter/reorder pipeline. ``residency`` is the currently-resident local tier
    id (if any); ``needs`` carries optional hard constraints.

    Quality gating and a declared custom preset: when ``work_class`` is ``None``
    (a custom preset with no taxonomy mapping in ``intent.PRESET_TO_WORK_CLASS``)
    the request is NOT quality-gated — the operator's explicit config pool is
    trusted and the deny filter is skipped — but ``notes["quality_gate"]`` is set
    to ``"off: custom preset has no work-class"`` so the bypass is auditable. To
    gate such a preset, map it to a work class in ``intent.PRESET_TO_WORK_CLASS``.

    Two honest limits of the residency reorder (step 4):

    * Deferring a non-resident local BEHIND cloud is an intentional anti-thrash
      tradeoff, not a free win: under an alternating workload the off-resident
      class is served by *cloud* rather than swapping the multiplexer. A future
      hysteresis policy could instead batch swaps (swap only after N queued
      requests for the non-resident local).
    * The swap bound holds ONLY when the candidate pool contains the
      resident-local tier OR a cloud / always-available tier to defer behind. A
      privacy-strict LOCAL-ONLY pool with disjoint per-class locals (e.g.
      ``quick-edit -> [gpu-a]``, ``long-context -> [gpu-b]``) has no fallback to
      defer behind and can still thrash one swap per request (see
      ``test_local_only_pool_can_still_thrash``).
    """
    try:
        work_class = getattr(intent, "work_class", None)
        # FIX E: tolerate a malformed Intent (missing/None candidate_tiers).
        pool = tuple(getattr(intent, "candidate_tiers", None) or ())  # AC2: config-derived.
        valid_ids = {t.id for t in config.tiers}

        dropped_missing: list[str] = []
        dropped_duplicate: list[str] = []
        dropped_by_constraint: list[str] = []
        dropped_by_deny: list[str] = []

        # 0a. De-dup the pool, preserving first-occurrence order: a duplicate tier
        #     id must never appear twice in the result.
        seen: set[str] = set()
        deduped: list[str] = []
        for tid in pool:
            if tid in seen:
                dropped_duplicate.append(tid)
                continue
            seen.add(tid)
            deduped.append(tid)

        # 0b. Drop any pool id the config doesn't know (robustness: never raise).
        survivors: list[str] = []
        for tid in deduped:
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

        # 2. Profile-deny filter (AC1), fail-closed. Resolved WITH each tier's
        #    privacy so an unmeasured local on a high-risk class denies while
        #    cloud stays allowed. Skipped (gate "off") for a None work class
        #    (FIX D: custom preset trusts the config pool — no taxonomy key).
        if work_class is not None:
            quality_gate = "on"
            kept = []
            for tid in survivors:
                try:
                    is_cloud = config.tier(tid).privacy == "cloud"
                except Exception:
                    # tid was in valid_ids, so this should not happen; if it
                    # somehow does, treat as local -> the deny gate fails closed.
                    is_cloud = False
                if profile.decision(tid, work_class, is_cloud=is_cloud) == "deny":
                    dropped_by_deny.append(tid)
                else:
                    kept.append(tid)
            survivors = kept
        else:
            quality_gate = "off: custom preset has no work-class"

        # 3. Order: preserve the pool's config cost order (fast -> heavy -> cloud).
        ordered = survivors

        # 4. Residency reorder (AC3). When a local tier is resident, defer every
        #    OTHER local behind the resident local + all cloud tiers, so choosing
        #    a swap-forcing non-resident local is a last resort.
        locals_ = local_tier_ids(config)
        residency_deferred: list[str] = []
        if residency is not None and residency in locals_:
            deferred = [tid for tid in ordered if tid in locals_ and tid != residency]
            residency_deferred = list(deferred)
            result = [tid for tid in ordered if not (tid in locals_ and tid != residency)] + deferred
        else:
            # residency is None or a cloud id: the first pick loads one local
            # model, an unavoidable single swap. Leave the cost order untouched.
            result = list(ordered)

        notes = MappingProxyType({
            "work_class": work_class,
            "pool": pool,
            "residency": residency,
            "quality_gate": quality_gate,
            "dropped_missing": tuple(dropped_missing),
            "dropped_duplicate": tuple(dropped_duplicate),
            "dropped_by_constraint": tuple(dropped_by_constraint),
            "dropped_by_deny": tuple(dropped_by_deny),
            "residency_deferred": tuple(residency_deferred),
            "empty": not result,
        })
        return RoutingDecision(tuple(result), work_class, notes=notes)
    except Exception as e:  # pragma: no cover - defensive: a malformed Intent degrades.
        # FIX E: match classify()/resolve() — degrade to an empty decision rather
        # than propagating. Empty result => the caller falls back to the safer tier.
        return RoutingDecision(
            (),
            getattr(intent, "work_class", None),
            notes=MappingProxyType({"error": repr(e), "empty": True}),
        )
