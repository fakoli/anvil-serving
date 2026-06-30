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

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, List, Mapping, Optional, Tuple


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
    escalated past it. ``intent`` is the declared-or-inferred intent the caller
    asked for — metadata only, optional, and last so keyword construction without
    it stays backward-compatible (T010 transparency). Producers SHOULD set it to
    the resolved preset id (the closed config vocabulary), not the raw wire
    ``model`` string; :func:`decision_line` sanitizes it regardless, so a
    caller-controlled value can never break or inject into the audit line.
    """

    work_class: Optional[str]
    requested_tiers: Tuple[str, ...]
    attempts: Tuple[AttemptRecord, ...]
    served_tier: Optional[str]
    total_prompt_tokens: int
    total_completion_tokens: int
    fell_back: bool
    intent: Optional[str] = None


# --------------------------------------------------------------------------- #
# transparency surface (T010; QGR §9; R012 metadata-only)
# --------------------------------------------------------------------------- #
# A dialect uses these to make a routed response *name what actually ran* and to
# emit a content-free audit line. They read only the record's existing metadata
# fields — tier ids, the fallback flag, token COUNTS — and never any message
# text, response content, or a verifier's raw reason string (R012).
def served_model(record: DecisionRecord) -> Optional[str]:
    """The real tier id that served, or ``None`` if exhausted.

    What a dialect sets as the response ``model`` so the response names the tier
    that actually ran (QGR §9 transparency), not the abstract intent the caller
    asked for.
    """
    return record.served_tier


def response_metadata(record: DecisionRecord) -> Mapping[str, Any]:
    """The transparent-response block a dialect attaches to a routed reply.

    A read-only mapping naming the ACTUAL served tier and whether a fallback
    occurred (AC1): ``served_tier``, ``fell_back``, ``work_class``, ``intent``,
    ``tiers_tried`` (the tier id of each attempt, in order), and ``exhausted``
    (no tier served). Metadata only — no prompt, response, or secret.
    """
    return MappingProxyType(
        {
            "served_tier": record.served_tier,
            "fell_back": record.fell_back,
            "work_class": record.work_class,
            "intent": record.intent,
            "tiers_tried": tuple(a.tier_id for a in record.attempts),
            "exhausted": record.served_tier is None,
        }
    )


def _safe(token: Optional[str]) -> str:
    """Render a string field safely for the single-line ``label=value`` grammar.

    Collapses any whitespace/newline run and the ``>`` tier separator to ``_``.
    This is load-bearing because ``intent`` can be caller-derived (the raw wire
    ``model`` string): without it, a ``model`` of ``"chat\\nintent=spoofed ..."``
    would inject a forged second audit line (log injection), and any embedded
    space would break ``key=value`` parsing. Operator-set tier ids / work_class
    get the same guarantee. ``None``/empty render as ``-``.
    """
    if not token:
        return "-"
    return re.sub(r"[\s>]+", "_", str(token))


def decision_line(record: DecisionRecord) -> str:
    """A single content-FREE audit line carrying every AC2 field.

    Shape::

        intent=<i|-> work_class=<wc|-> served=<tier|-> verify=<pass|fail> \
fell_back=<true|false> tiers=<t1>t2>t3|-> prompt=<n> completion=<n>

    ``verify`` is ``pass`` when a tier served (``served_tier`` is set) else
    ``fail``; ``-`` stands in for a missing/empty intent/work_class/served/tiers;
    ``tiers`` joins ``requested_tiers`` with ``>``. Every string field is passed
    through :func:`_safe` so the line is ALWAYS a single, parseable sequence of
    ``label=value`` tokens regardless of caller- or operator-supplied content.
    Only labels and integers — never message text or a verifier's raw reason (R012).
    """
    served = record.served_tier
    tiers = ">".join(_safe(t) for t in record.requested_tiers) or "-"
    return (
        f"intent={_safe(record.intent)} "
        f"work_class={_safe(record.work_class)} "
        f"served={_safe(served)} "
        f"verify={'pass' if served is not None else 'fail'} "
        f"fell_back={'true' if record.fell_back else 'false'} "
        f"tiers={tiers} "
        f"prompt={record.total_prompt_tokens} "
        f"completion={record.total_completion_tokens}"
    )


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
