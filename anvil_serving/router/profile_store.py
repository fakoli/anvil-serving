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

import threading
from dataclasses import dataclass, replace
from typing import List, Mapping, Optional, Tuple

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

    ``stale`` / ``fingerprint`` are the T016 staleness fields (both ADDITIVE,
    defaulted so every T005/T015 construction site keeps working untouched):
    ``fingerprint`` records the serve identity (see
    :func:`~anvil_serving.router.fingerprint.serve_fingerprint`) the row was last
    associated with, and ``stale`` is ``True`` once that identity changed under
    the row — a signal for routing to DISTRUST the row until it is re-measured.
    They are managed by :meth:`ProfileStore.apply_fingerprint` /
    :meth:`ProfileStore.record_grade`, never by the seed/replay paths.
    """

    decision: str
    quality_score: float  # 0.0-1.0; advisory for the MVP
    sample_n: int
    last_measured: Optional[str]  # None for hand-authored seeds
    stale: bool = False  # T016: serve identity changed -> distrust until re-measured
    fingerprint: Optional[str] = None  # T016: serve identity this row was measured under

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise ValueError(
                f"ProfileEntry.decision {self.decision!r} is not one of {DECISIONS}; "
                f"verdicts are a closed set so the policy deny gate fails closed"
            )


class ProfileStore:
    """A lookup over ``(tier_id, work_class) -> ProfileEntry``.

    The backing table is copied in and kept private. The READ surface
    (:meth:`entry`/:meth:`decision`/:meth:`score`) is what routing consults; it
    applies the fail-closed defaults documented on the module: the table is
    consulted FIRST for any key, then a ``None`` work class is ``allow``, a cloud
    tier is ``allow``, an unmeasured *local* tier on a high-risk class is
    ``deny``, and any other unmeasured pair is ``allow-with-verify``.
    :meth:`entry`, :meth:`decision`, and :meth:`score` therefore never disagree
    for a stored key.

    T016 adds a small, *thread-safe* WRITE surface used off the hot path by the
    async calibration sampler and the serve-fingerprint staleness check:
    :meth:`record_grade` (fold a fresh quality grade into a row) and
    :meth:`apply_fingerprint` (stamp/compare a serve identity, marking rows
    stale on a change). Both take an internal :class:`threading.Lock` so
    concurrent background grades cannot corrupt an entry; the read methods are
    single ``dict.get`` lookups (atomic under the GIL) and stay lock-free.
    """

    __slots__ = ("_table", "_lock")

    def __init__(self, entries: Mapping[Tuple[str, Optional[str]], ProfileEntry]):
        # Copy so a later mutation of the caller's dict can't leak in.
        self._table = dict(entries)
        # Guards the read-modify-write of record_grade / apply_fingerprint so
        # concurrent calibration grades (each a background thread) can't race a
        # lost update onto a shared row.
        self._lock = threading.Lock()

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

    # --- T016 write surface (thread-safe; off the hot path) -------------------

    def is_stale(self, tier_id: str, work_class: Optional[str]) -> bool:
        """``True`` if the stored row exists and is flagged stale.

        Unmeasured pairs (no stored row) are NOT stale — they fall through to the
        fail-closed defaults of :meth:`decision`, which already distrust the risky
        ones. Staleness only qualifies a row that *was* measured.
        """
        e = self._table.get((tier_id, work_class))
        return bool(e is not None and e.stale)

    def stale_pairs(self) -> List[Tuple[str, Optional[str]]]:
        """Every ``(tier_id, work_class)`` whose row is currently stale, sorted.

        Snapshots the table UNDER THE LOCK before iterating: a concurrent
        :meth:`record_grade` can insert a brand-new key, and iterating the live
        ``dict`` view would otherwise risk ``RuntimeError: dictionary changed size
        during iteration``. (The ``dict.get`` readers — :meth:`entry`/
        :meth:`decision`/:meth:`score`/:meth:`is_stale` — need no lock; only this
        full traversal does.)
        """
        with self._lock:
            snapshot = list(self._table.items())
        return sorted(
            (k for k, e in snapshot if e.stale),
            key=lambda k: (k[0], k[1] or ""),
        )

    def record_grade(
        self,
        tier_id: str,
        work_class: Optional[str],
        *,
        score: float,
        decision: Optional[str] = None,
        last_measured: Optional[str] = None,
        weight: int = 1,
        submitted_fingerprint: Optional[str] = None,
    ) -> ProfileEntry:
        """Fold a fresh quality ``score`` into the ``(tier_id, work_class)`` row.

        Read-modify-write under the store lock so concurrent background grades
        don't lose an update. Semantics:

        * ``quality_score`` becomes the sample-count-weighted running mean of the
          prior score and the new grade (``weight`` new observations), so a single
          noisy grade can't swing a well-sampled row; ``sample_n`` grows by
          ``weight``.
        * ``decision`` is updated ONLY if the caller passes one explicitly — a
          quality number never silently flips the load-bearing trust verdict (the
          ``deny`` gate). A brand-new row with no decision defaults to
          ``allow-with-verify`` (use-but-verify), never a bare ``allow``.
        * ``stale`` is cleared (the grade re-measured the row) — but ONLY when the
          measurement is still current. ``submitted_fingerprint`` is the serve
          identity that was active when this grade was DISPATCHED; if the row's
          ``fingerprint`` has since advanced past it (a concurrent
          :meth:`apply_fingerprint` stamped a NEW identity + ``stale=True`` while
          this grade was in flight), the grade is from a now-superseded serve and
          MUST NOT clear that fresh staleness — the existing ``stale`` is kept.
          Passing ``None`` (no fingerprint context) clears unconditionally, the
          prior behaviour. ``fingerprint`` itself is carried over (the identity
          was already advanced by :meth:`apply_fingerprint` when the serve
          changed). ``last_measured`` is set when provided, else carried over.

        Returns the new (replaced) entry.
        """
        with self._lock:
            prev = self._table.get((tier_id, work_class))
            if prev is None:
                entry = ProfileEntry(
                    decision=decision if decision is not None else _DEFAULT_UNKNOWN,
                    quality_score=round(float(score), 4),
                    sample_n=max(1, int(weight)),
                    last_measured=last_measured,
                    stale=False,
                    fingerprint=None,
                )
            else:
                new_n = prev.sample_n + int(weight)
                new_score = round(
                    (prev.quality_score * prev.sample_n + float(score) * int(weight))
                    / new_n,
                    4,
                )
                # Don't clear staleness that was set AFTER this grade was
                # submitted: if the serve identity advanced since dispatch, this
                # measurement no longer reflects the current serve.
                superseded = (
                    submitted_fingerprint is not None
                    and prev.fingerprint != submitted_fingerprint
                )
                new_stale = prev.stale if superseded else False
                entry = replace(
                    prev,
                    decision=decision if decision is not None else prev.decision,
                    quality_score=new_score,
                    sample_n=new_n,
                    last_measured=(
                        last_measured if last_measured is not None else prev.last_measured
                    ),
                    stale=new_stale,
                )
            self._table[(tier_id, work_class)] = entry
            return entry

    def apply_fingerprint(self, tier_id: str, fingerprint: str) -> List[Optional[str]]:
        """Associate ``fingerprint`` with every row of ``tier_id`` and mark stale on change.

        For each stored row of ``tier_id`` (across ALL its work classes — a serve
        change affects the whole tier), under the store lock:

        * no prior fingerprint -> adopt ``fingerprint`` as the baseline (NOT
          stale: there was nothing to invalidate);
        * stored fingerprint == ``fingerprint`` -> no-op;
        * stored fingerprint != ``fingerprint`` -> advance to ``fingerprint`` and
          set ``stale = True`` (the serve identity changed; distrust until
          re-measured). The fingerprint is advanced so a repeat call with the same
          new identity is a no-op rather than re-flapping the flag.

        Rows of OTHER tiers are never touched. Returns the work classes whose rows
        were newly marked stale (sorted), so a caller/test can see the blast radius.
        """
        changed: List[Optional[str]] = []
        with self._lock:
            for key, entry in list(self._table.items()):
                tid, work_class = key
                if tid != tier_id:
                    continue
                if entry.fingerprint is None:
                    self._table[key] = replace(entry, fingerprint=fingerprint)
                elif entry.fingerprint != fingerprint:
                    self._table[key] = replace(entry, fingerprint=fingerprint, stale=True)
                    changed.append(work_class)
                # else: identical identity -> leave the row exactly as is.
        return sorted(changed, key=lambda wc: wc or "")


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
