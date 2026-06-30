"""The router decision/audit trail (harness-router:T009; PRD R005).

Records the journey of one routed request — ``intent -> candidate tiers ->
verify verdict -> fallback?`` — together with per-attempt and per-tier token
accounting, so an operator can answer "why did this request end up on the cloud
tier, and what did it cost?" after the fact.

**Secrets hygiene (PRD R012).** This log records *metadata only*: tier ids,
verify verdicts, the failing verifier's NAME, token COUNTS, and outcome labels.
It never stores a full prompt, a full response, or a credential. Crucially, the
``verify_reason`` field holds a content-free LABEL (the verifier name like
``"DiffWellFormed"``, or a status like ``"circuit open (...)"`` / ``"backend
error: TimeoutError"``) — NOT a verifier's raw ``reason`` string, because some
T007 reasons echo the model's content (a malformed diff line, a tool name) and
must never be persisted. The token fields are integer counts, not text. There is
no persistence here (a later task owns durable storage) — in-memory append store.

Stdlib-only; frozen-dataclass house style (mirrors ``config.py`` / ``internal.py``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class AttemptRecord:
    """One tier attempt within a routing decision.

    ``outcome`` is the terminal label for this attempt:

    * ``"served"`` — the tier produced output that passed verify; it served the
      response.
    * ``"fallback"`` — the tier produced output that FAILED verify; the router
      escalated to the next candidate.
    * ``"error"`` — the backend raised (OOM-kill / "scheduler died" / connection
      reset — repo gotcha #1); treated as a failed attempt, never propagated.
    * ``"skipped-circuit"`` — the tier's per-session circuit was open (too many
      consecutive failures), so it was skipped without a backend call.
    * ``"budget-stop"`` — the per-session token budget would be exceeded by
      attempting this tier, so escalation stopped here.
    * ``"unknown-tier"`` — the candidate id is absent from the config (a routing /
      config mismatch, not a backend fault); skipped without a call or token charge.

    Token fields are integer COUNTS (never the text). ``prompt_tokens`` /
    ``completion_tokens`` are 0 for the no-backend-call outcomes
    (``skipped-circuit`` / ``budget-stop``) and ``completion_tokens`` is 0 for an
    ``error`` (the completion never assembled).
    """

    tier_id: str
    verifier_passed: bool
    verify_reason: str
    prompt_tokens: int
    completion_tokens: int
    outcome: str
    detail: str = ""


@dataclass(frozen=True)
class DecisionRecord:
    """The full audit record for one routed request.

    Frozen and hashable: every field is itself hashable (``attempts`` is a tuple
    of frozen :class:`AttemptRecord`). ``requested_tiers`` is the ordered
    candidate pool the policy handed in; ``attempts`` is what actually happened,
    in order. ``total_prompt_tokens`` / ``total_completion_tokens`` sum only the
    attempts that actually called a backend (``served`` / ``fallback`` /
    ``error``) — the no-call outcomes contribute nothing. ``fell_back`` is True
    when at least one tier produced output that failed verify and the router
    escalated past it.
    """

    work_class: Optional[str]
    requested_tiers: Tuple[str, ...]
    attempts: Tuple[AttemptRecord, ...]
    served_tier: Optional[str]
    total_prompt_tokens: int
    total_completion_tokens: int
    fell_back: bool


class DecisionLog:
    """In-memory, append-only store of :class:`DecisionRecord` (no persistence).

    A single session's audit trail. :meth:`record` appends; :attr:`records`
    returns an immutable snapshot (a tuple copy, so a caller cannot mutate the
    internal list); :attr:`last` is the most recent record or ``None``. No
    secrets are stored — see the module docstring.
    """

    def __init__(self) -> None:
        self._records: List[DecisionRecord] = []

    def record(self, record: DecisionRecord) -> None:
        """Append ``record`` to the log."""
        self._records.append(record)

    @property
    def records(self) -> Tuple[DecisionRecord, ...]:
        """Immutable snapshot of all recorded decisions, oldest first."""
        return tuple(self._records)

    @property
    def last(self) -> Optional[DecisionRecord]:
        """The most recently recorded decision, or ``None`` if the log is empty."""
        return self._records[-1] if self._records else None

    def __len__(self) -> int:
        return len(self._records)
