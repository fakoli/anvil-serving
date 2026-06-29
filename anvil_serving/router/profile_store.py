"""Quality-profile store: (tier, work-class) -> decision/score (harness-router:T005).

The *quality profile* is a small table keyed by ``(tier_id, work_class)`` whose
entries say how much the router should TRUST a given tier for a given class of
work. The MVP seeds a HAND-AUTHORED static table (PRD R003) grounded in the
planning-capability eval: frontier (cloud) is strong everywhere; the local tiers
are weak on dependency/planning work, so **planning must go cloud**. Scores are
advisory for the MVP — ordering is primarily the config/cost order — but the
``deny`` decisions are load-bearing: the routing policy drops a denied tier.

Defaults FAIL CLOSED for the eval-proven-weak local classes. An explicit entry
always wins (the table is consulted FIRST, for any key — so a stored verdict can
never be dodged by a None/unknown short-circuit). Otherwise, in order: a ``None``
work class (a declared custom preset with no taxonomy mapping) -> ``allow``
(trust the config-declared candidate pool); a ``cloud`` tier (``is_cloud=True``)
-> ``allow`` (the always-available safe tier); an UNMEASURED *local* tier on a
high-risk class (planning, multi-file-refactor) -> ``deny`` (an unmeasured local
is NOT routable for eval-weak work — the safe direction, not a permissive
``allow-with-verify``); any other unmeasured pair -> ``allow-with-verify``
(use-but-verify).

Stdlib-only; mirrors the frozen-dataclass style of ``config.py`` / ``intent.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

# The closed set of per-(tier, work-class) verdicts.
DECISIONS = ("allow", "allow-with-verify", "deny")

# Fallbacks (see module docstring); the resolution ORDER lives in ``decision``.
_DEFAULT_UNKNOWN = "allow-with-verify"    # unmeasured, non-high-risk local
_DEFAULT_NONE_CLASS = "allow"             # custom preset: trust the operator's pool
_DEFAULT_CLOUD = "allow"                  # cloud is the always-available safe tier
_DEFAULT_HIGH_RISK_LOCAL = "deny"         # FAIL CLOSED: unmeasured local, eval-weak class
_DEFAULT_SCORE = 0.5

# Eval-proven-weak-for-local work classes: an UNMEASURED local tier for these
# biases to DENY (not routable), never a permissive allow-with-verify. Grounded
# in the planning-capability eval (docs/findings): the local tiers fail
# dependency/planning work and large multi-file refactors; only cloud is trusted
# there. Seeding (default_profile) already denies the three named locals; this
# makes the same verdict PORTABLE to any unseeded local tier id.
HIGH_RISK_LOCAL_CLASSES = frozenset({"planning", "multi-file-refactor"})

# Hand-authored per-tier quality anchors for the worked example (cloud strong,
# heavy mid, fast weak). ``allow-with-verify`` shades a tier down a notch; a
# ``deny`` pins it low. Advisory only for the MVP.
_TIER_QUALITY = {"cloud": 0.95, "heavy-local": 0.80, "fast-local": 0.65}
_DENY_SCORE = 0.20

# The eval verdicts, per work class: tier_id -> decision. Encodes "planning must
# go cloud" and "fast 32k ctx is too small for long-context".
_SEED_VERDICTS: Mapping[str, Mapping[str, str]] = {
    "planning":            {"fast-local": "deny", "heavy-local": "deny",              "cloud": "allow"},
    "multi-file-refactor": {"fast-local": "deny", "heavy-local": "allow-with-verify", "cloud": "allow"},
    "long-context":        {"fast-local": "deny", "heavy-local": "allow",             "cloud": "allow"},
    "review":              {"fast-local": "allow-with-verify", "heavy-local": "allow", "cloud": "allow"},
    "bounded-edit":        {"fast-local": "allow", "heavy-local": "allow",            "cloud": "allow"},
    "chat":                {"fast-local": "allow", "heavy-local": "allow",            "cloud": "allow"},
}


@dataclass(frozen=True)
class ProfileEntry:
    """One measured/authored verdict for a ``(tier, work-class)`` pair.

    ``decision`` is validated against :data:`DECISIONS` at construction
    (:meth:`__post_init__`): a malformed verdict like ``"DENY"`` or ``"Deny "``
    raises ``ValueError`` and so can never exist. This is what lets the routing
    policy's ``== "deny"`` gate be a trustworthy fail-closed check — it cannot be
    dodged by casing or a typo, because the off-spec value never gets stored.

    ``last_measured`` is ``None`` for a hand-authored seed entry; a later
    self-refinement loop fills it with an ISO timestamp when it measures.
    """

    decision: str
    quality_score: float  # 0.0-1.0; advisory for the MVP
    sample_n: int
    last_measured: Optional[str]  # None for hand-authored seeds

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise ValueError(
                f"ProfileEntry.decision {self.decision!r} is not one of {DECISIONS}; "
                f"verdicts are a closed set so the policy deny gate fails closed"
            )


class ProfileStore:
    """An immutable lookup over ``(tier_id, work_class) -> ProfileEntry``.

    The backing table is copied in and kept private — there is no mutable
    accessor. Lookups apply the fail-closed defaults documented on the module:
    the table is consulted FIRST for any key, then a ``None`` work class is
    ``allow``, a cloud tier is ``allow``, an unmeasured *local* tier on a
    high-risk class is ``deny``, and any other unmeasured pair is
    ``allow-with-verify``. :meth:`entry`, :meth:`decision`, and :meth:`score`
    therefore never disagree for a stored key.
    """

    __slots__ = ("_table",)

    def __init__(self, entries: Mapping[Tuple[str, Optional[str]], ProfileEntry]):
        # Copy so a later mutation of the caller's dict can't leak in.
        self._table = dict(entries)

    def entry(self, tier_id: str, work_class: Optional[str]) -> Optional[ProfileEntry]:
        """Return the stored entry for the pair, or ``None`` if unmeasured."""
        return self._table.get((tier_id, work_class))

    def decision(
        self, tier_id: str, work_class: Optional[str], *, is_cloud: bool = False
    ) -> str:
        """Trust verdict for ``(tier_id, work_class)`` — FAILS CLOSED for local.

        Resolution order:

        1. an explicit stored entry (consulted FIRST, for ANY key incl
           ``(tier, None)``) -> its decision;
        2. ``None`` work class -> ``allow`` (declared custom preset: trust the
           operator's explicit pool);
        3. ``is_cloud`` -> ``allow`` (cloud is the always-available safe tier);
        4. a high-risk class (:data:`HIGH_RISK_LOCAL_CLASSES`) on an UNMEASURED
           *local* tier -> ``deny`` (the safe direction: an unmeasured local is
           not routable for eval-weak work);
        5. otherwise -> ``allow-with-verify`` (use-but-verify).
        """
        e = self._table.get((tier_id, work_class))
        if e is not None:
            return e.decision
        if work_class is None:
            return _DEFAULT_NONE_CLASS
        if is_cloud:
            return _DEFAULT_CLOUD
        if work_class in HIGH_RISK_LOCAL_CLASSES:
            return _DEFAULT_HIGH_RISK_LOCAL
        return _DEFAULT_UNKNOWN

    def score(self, tier_id: str, work_class: Optional[str]) -> float:
        """Advisory quality score in ``[0, 1]``; ``0.5`` if unmeasured.

        Consults the stored entry FIRST (for ANY key incl ``(tier, None)``) so
        ``score`` never disagrees with ``entry``/``decision`` for a stored pair.
        """
        e = self._table.get((tier_id, work_class))
        return e.quality_score if e is not None else _DEFAULT_SCORE


def _seed_entry(tier_id: str, decision: str) -> ProfileEntry:
    """Build a hand-authored seed entry, scoring it off the tier/decision anchors."""
    base = _TIER_QUALITY.get(tier_id, _DEFAULT_SCORE)
    if decision == "allow":
        score = base
    elif decision == "allow-with-verify":
        score = round(base - 0.10, 2)
    else:  # deny
        score = _DENY_SCORE
    return ProfileEntry(
        decision=decision,
        quality_score=score,
        sample_n=1,  # hand-authored: a single authored observation
        last_measured=None,
    )


def default_profile() -> ProfileStore:
    """The hand-authored seed profile for the worked-example tiers.

    Covers ``{fast-local, heavy-local, cloud} x WORK_CLASSES`` with the eval
    verdicts (see :data:`_SEED_VERDICTS`) — including ``planning`` -> ``deny`` on
    *both* locals. Any pair outside this seed falls back to the store defaults,
    so an UNSEEDED local tier (e.g. a ``gpu0`` dropped into a planning pool) also
    ``deny``s on the high-risk classes: the fail-closed verdict is portable, not
    hard-wired to these three named tiers.
    """
    table: dict[Tuple[str, Optional[str]], ProfileEntry] = {}
    for work_class, per_tier in _SEED_VERDICTS.items():
        for tier_id, decision in per_tier.items():
            table[(tier_id, work_class)] = _seed_entry(tier_id, decision)
    return ProfileStore(table)
