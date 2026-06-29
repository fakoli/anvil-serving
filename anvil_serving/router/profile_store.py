"""Quality-profile store: (tier, work-class) -> decision/score (harness-router:T005).

The *quality profile* is a small table keyed by ``(tier_id, work_class)`` whose
entries say how much the router should TRUST a given tier for a given class of
work. The MVP seeds a HAND-AUTHORED static table (PRD R003) grounded in the
planning-capability eval: frontier (cloud) is strong everywhere; the local tiers
are weak on dependency/planning work, so **planning must go cloud**. Scores are
advisory for the MVP — ordering is primarily the config/cost order — but the
``deny`` decisions are load-bearing: the routing policy drops a denied tier.

Defaults are deliberately conservative and *never* silently ``deny`` an
unmeasured pair: an unknown ``(tier, work_class)`` falls back to
``allow-with-verify`` (use-but-verify), and a ``None`` work class (a declared
custom preset with no taxonomy mapping) falls back to ``allow`` (trust the
config-declared candidate pool).

Stdlib-only; mirrors the frozen-dataclass style of ``config.py`` / ``intent.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

# The closed set of per-(tier, work-class) verdicts.
DECISIONS = ("allow", "allow-with-verify", "deny")

# Fallbacks (see module docstring): an unmeasured pair is use-but-verify, never a
# silent deny; a None work class trusts the config-declared pool.
_DEFAULT_UNKNOWN = "allow-with-verify"
_DEFAULT_NONE_CLASS = "allow"
_DEFAULT_SCORE = 0.5

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

    ``last_measured`` is ``None`` for a hand-authored seed entry; a later
    self-refinement loop fills it with an ISO timestamp when it measures.
    """

    decision: str
    quality_score: float  # 0.0-1.0; advisory for the MVP
    sample_n: int
    last_measured: Optional[str]  # None for hand-authored seeds


class ProfileStore:
    """An immutable lookup over ``(tier_id, work_class) -> ProfileEntry``.

    The backing table is copied in and kept private — there is no mutable
    accessor. Lookups apply the conservative defaults documented on the module:
    an unknown pair is ``allow-with-verify`` (never a silent ``deny``) and a
    ``None`` work class is ``allow``.
    """

    __slots__ = ("_table",)

    def __init__(self, entries: Mapping[Tuple[str, Optional[str]], ProfileEntry]):
        # Copy so a later mutation of the caller's dict can't leak in.
        self._table = dict(entries)

    def entry(self, tier_id: str, work_class: Optional[str]) -> Optional[ProfileEntry]:
        """Return the stored entry for the pair, or ``None`` if unmeasured."""
        return self._table.get((tier_id, work_class))

    def decision(self, tier_id: str, work_class: Optional[str]) -> str:
        """Trust verdict for ``(tier_id, work_class)``.

        ``None`` work class -> ``allow`` (trust the config pool); a measured pair
        -> its decision; an unmeasured pair -> ``allow-with-verify`` (never a
        silent ``deny``).
        """
        if work_class is None:
            return _DEFAULT_NONE_CLASS
        e = self._table.get((tier_id, work_class))
        if e is not None:
            return e.decision
        return _DEFAULT_UNKNOWN

    def score(self, tier_id: str, work_class: Optional[str]) -> float:
        """Advisory quality score in ``[0, 1]``; ``0.5`` if unmeasured/``None``."""
        if work_class is None:
            return _DEFAULT_SCORE
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
    verdicts (see :data:`_SEED_VERDICTS`). Any pair outside this seed falls back
    to the store defaults.
    """
    table: dict[Tuple[str, Optional[str]], ProfileEntry] = {}
    for work_class, per_tier in _SEED_VERDICTS.items():
        for tier_id, decision in per_tier.items():
            table[(tier_id, work_class)] = _seed_entry(tier_id, decision)
    return ProfileStore(table)
