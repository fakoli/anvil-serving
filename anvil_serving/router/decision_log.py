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
import threading
from collections import Counter, deque
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Deque, Iterable, Mapping, Optional, Tuple

#: Default ring-buffer capacity for :class:`DecisionLog`. One record per routed
#: request; 10k bounds a long-running server's audit memory to the recent
#: window while staying far above what an operator inspects interactively.
DEFAULT_MAX_RECORDS = 10_000
_SUMMARY_SECRET_RE = re.compile(
    r"(?i)(bearer_[A-Za-z0-9._~+/\-]{6,}|bearer\s+[A-Za-z0-9._~+/\-]{6,}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{6,}|"
    r"[A-Z0-9_-]*(?:TOKEN|SECRET|API_KEY|API-KEY|KEY)[A-Z0-9_-]*\s*[:=]\s*[^\s]+)"
)
_CORRELATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


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
    # Estimated $ cost for this request.  0.0 for local tiers (no metered billing).
    # For metered cloud tiers, compute with :func:`compute_cost_usd` and pass here.
    cost_usd: float = 0.0
    # Active serving mode (ADR-0011 / flexibility:T013): "agentic" | "flexibility"
    # | None. Global, set once at build_server time and stamped onto every record so
    # the audit trail (and a captured traffic window replayed by metrics.py)
    # distinguishes the SAME model measured in different modes. None (a --config boot
    # with no mode) leaves existing records byte-for-byte unchanged.
    mode: Optional[str] = None
    # Workbench lineage metadata. These identifiers are supplied by a trusted
    # private harness header and sanitized at the front door; they are never
    # prompt/response content and remain optional for all existing callers.
    request_id: Optional[str] = None
    workbench_run_id: Optional[str] = None
    task_id: Optional[str] = None
    # Content-free transport metadata for binary/purpose gateways.  Audio uses
    # these fields to expose hop volume and elapsed time without retaining the
    # audio payload, base64, transcript, or synthesis input.  They default to
    # zero so existing chat decisions remain byte-for-byte compatible.
    request_bytes: int = 0
    response_bytes: int = 0
    latency_ms: int = 0


def safe_correlation(value: Any) -> Optional[str]:
    """Accept a compact opaque correlation identifier or discard it.

    Decision logs are operator-visible and sometimes exported as line-oriented
    evidence, so caller-controlled values cannot carry whitespace, control
    characters, or arbitrary content. The router treats malformed correlation
    headers as absent rather than failing an otherwise valid model request.
    """
    candidate = str(value or "")
    return candidate if _CORRELATION_RE.fullmatch(candidate) else None


def request_correlation(request: Any) -> dict[str, Optional[str]]:
    """Read the front-door-stamped Workbench lineage from an internal request."""
    raw = getattr(request, "raw", {})
    source = raw.get("_anvil_correlation", {}) if isinstance(raw, Mapping) else {}
    if not isinstance(source, Mapping):
        source = {}
    return {
        "request_id": safe_correlation(source.get("request_id")),
        "workbench_run_id": safe_correlation(source.get("workbench_run_id")),
        "task_id": safe_correlation(source.get("task_id")),
    }


# --------------------------------------------------------------------------- #
# cost estimation (T003; cost dimension)
# --------------------------------------------------------------------------- #
def compute_cost_usd(tier: Any, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimated USD cost for one request served by ``tier``.

    Uses ``tier.cost_input_per_mtok`` and ``tier.cost_output_per_mtok`` (USD per
    million tokens).  Returns ``0.0`` when either field is ``None`` / unset (which
    is the case for all local tiers — they have no metered billing).

    Pure computation: never blocks or calls any external service.  Safe to call on
    the hot path.  Duck-typed so it works with any object that exposes the two cost
    fields (real :class:`~anvil_serving.router.config.Tier` or a test stub).
    """
    cost_in = getattr(tier, "cost_input_per_mtok", None)
    cost_out = getattr(tier, "cost_output_per_mtok", None)
    if cost_in is None and cost_out is None:
        return 0.0
    return ((cost_in or 0.0) * prompt_tokens + (cost_out or 0.0) * completion_tokens) / 1_000_000


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
    fields = {
            "served_tier": record.served_tier,
            "fell_back": record.fell_back,
            "work_class": record.work_class,
            "intent": record.intent,
            "tiers_tried": tuple(a.tier_id for a in record.attempts),
            "exhausted": record.served_tier is None,
    }
    for name in ("request_id", "workbench_run_id", "task_id"):
        value = safe_correlation(getattr(record, name, None))
        if value is not None:
            fields[name] = value
    return MappingProxyType(fields)


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


def _summary_safe(token: Optional[str]) -> str:
    if not token:
        return "-"
    placeholder = "__ANVIL_REDACTED__"
    safe = _safe(_SUMMARY_SECRET_RE.sub(placeholder, str(token)))
    return safe.replace(placeholder, "<redacted>")


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
    line = (
        f"intent={_safe(record.intent)} "
        f"work_class={_safe(record.work_class)} "
        f"served={_safe(served)} "
        f"verify={'pass' if served is not None else 'fail'} "
        f"fell_back={'true' if record.fell_back else 'false'} "
        f"tiers={tiers} "
        f"prompt={record.total_prompt_tokens} "
        f"completion={record.total_completion_tokens}"
    )
    # Preserve the established eight-field chat audit grammar.  Binary gateway
    # records add only content-free measurements, satisfying observability
    # without putting raw audio or transcript text into container logs.
    request_bytes = max(_int_field(record, "request_bytes"), 0)
    response_bytes = max(_int_field(record, "response_bytes"), 0)
    latency_ms = max(_int_field(record, "latency_ms"), 0)
    if request_bytes or response_bytes or latency_ms:
        line += (
            f" request_bytes={request_bytes}"
            f" response_bytes={response_bytes}"
            f" latency_ms={latency_ms}"
        )
    return line


def _field(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _int_field(record: Any, name: str) -> int:
    value = _field(record, name, 0)
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _attempt_summary(attempt: Any) -> dict:
    return {
        "tier_id": _summary_safe(_field(attempt, "tier_id")),
        "outcome": _summary_safe(_field(attempt, "outcome")),
        "verifier_passed": bool(_field(attempt, "verifier_passed", False)),
        "verify_reason": _summary_safe(_field(attempt, "verify_reason")),
        "prompt_tokens": _int_field(attempt, "prompt_tokens"),
        "completion_tokens": _int_field(attempt, "completion_tokens"),
    }


def summarize_decisions(records: Iterable[Any], *, limit: int = 20) -> dict:
    """Summarize recent routing decisions without prompt, response, or secret text.

    Accepts real :class:`DecisionRecord` objects or JSON-like mappings from a
    captured metadata artifact. Unknown fields are ignored deliberately; the
    output is the safe audit projection only.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    all_records = list(records)
    selected = all_records[-limit:]
    items = []
    served_counts: Counter[str] = Counter()
    outcome_counts: Counter[str] = Counter()
    fallback_count = 0
    total_prompt = 0
    total_completion = 0
    total_cost = 0.0
    total_request_bytes = 0
    total_response_bytes = 0
    total_latency_ms = 0
    for record in selected:
        attempts = tuple(_field(record, "attempts", ()) or ())
        attempt_items = [_attempt_summary(attempt) for attempt in attempts]
        served = _summary_safe(_field(record, "served_tier"))
        fell_back = bool(_field(record, "fell_back", False))
        prompt_tokens = _int_field(record, "total_prompt_tokens")
        completion_tokens = _int_field(record, "total_completion_tokens")
        request_bytes = max(_int_field(record, "request_bytes"), 0)
        response_bytes = max(_int_field(record, "response_bytes"), 0)
        latency_ms = max(_int_field(record, "latency_ms"), 0)
        try:
            cost_usd = float(_field(record, "cost_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            cost_usd = 0.0
        if served != "-":
            served_counts[served] += 1
        if fell_back:
            fallback_count += 1
        for attempt in attempt_items:
            outcome_counts[attempt["outcome"]] += 1
        total_prompt += prompt_tokens
        total_completion += completion_tokens
        total_cost += cost_usd
        total_request_bytes += request_bytes
        total_response_bytes += response_bytes
        total_latency_ms += latency_ms
        requested_tiers = tuple(str(t) for t in (_field(record, "requested_tiers", ()) or ()))
        items.append({
            "intent": _summary_safe(_field(record, "intent")),
            "work_class": _summary_safe(_field(record, "work_class")),
            "requested_tiers": tuple(_summary_safe(t) for t in requested_tiers),
            "served_tier": served,
            "fell_back": fell_back,
            "attempts": attempt_items,
            "total_prompt_tokens": prompt_tokens,
            "total_completion_tokens": completion_tokens,
            "request_bytes": request_bytes,
            "response_bytes": response_bytes,
            "latency_ms": latency_ms,
            "cost_usd": round(cost_usd, 8),
            "mode": _summary_safe(_field(record, "mode")),
            "request_id": _summary_safe(safe_correlation(_field(record, "request_id"))),
            "workbench_run_id": _summary_safe(safe_correlation(_field(record, "workbench_run_id"))),
            "task_id": _summary_safe(safe_correlation(_field(record, "task_id"))),
        })
    return {
        "count": len(items),
        "available": len(all_records),
        "limit": limit,
        "records": items,
        "totals": {
            "fallback_count": fallback_count,
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "cost_usd": round(total_cost, 8),
            "request_bytes": total_request_bytes,
            "response_bytes": total_response_bytes,
            "latency_ms": total_latency_ms,
            "served_tiers": dict(sorted(served_counts.items())),
            "attempt_outcomes": dict(sorted(outcome_counts.items())),
        },
        "omitted_fields": [
            "prompt", "messages", "content", "response", "api_key",
            "authorization", "token", "audio", "audio_b64", "input", "text",
        ],
    }


class DecisionLog:
    """In-memory, bounded store of :class:`DecisionRecord` (no persistence).

    A single session's audit trail. :meth:`record` appends; :attr:`records`
    returns an immutable snapshot (a tuple copy, so a caller cannot mutate the
    internal store); :attr:`last` is the most recent record or ``None``. No
    secrets are stored — see the module docstring.

    **Bounded memory.** The store is a ring buffer capped at ``max_records``
    (default :data:`DEFAULT_MAX_RECORDS`): once full, appending evicts the
    OLDEST record. The router appends one record per request and lives for the
    whole server session, so an unbounded list is a slow memory leak on a
    long-running service — a week of steady harness traffic is hundreds of
    thousands of records. The cap keeps the recent window an operator actually
    inspects; durable full-history storage is a separate (persistence) concern.
    Pass ``max_records=None`` for the old unbounded behaviour (tests,
    short-lived replay tooling).

    **Thread-safety.** The router runs under :class:`~http.server.ThreadingHTTPServer`,
    so :meth:`record` and :attr:`last`/:attr:`records`/:meth:`__len__` can be called
    concurrently from per-request handler threads. A :class:`threading.Lock` guards
    every mutation *and* every read that iterates ``_records``. The lock is held only
    for the minimal critical section (the deque operation itself); no expensive work
    is done under it.
    """

    def __init__(self, max_records: Optional[int] = DEFAULT_MAX_RECORDS) -> None:
        if max_records is not None and max_records <= 0:
            raise ValueError(f"max_records must be positive or None, got {max_records!r}")
        # deque(maxlen=None) is unbounded — the explicit opt-out.
        self._records: Deque[DecisionRecord] = deque(maxlen=max_records)
        self._lock = threading.Lock()

    def record(self, record: DecisionRecord) -> None:
        """Append ``record`` to the log (thread-safe)."""
        with self._lock:
            self._records.append(record)

    @property
    def records(self) -> Tuple[DecisionRecord, ...]:
        """Immutable snapshot of all recorded decisions, oldest first (thread-safe)."""
        with self._lock:
            return tuple(self._records)

    @property
    def last(self) -> Optional[DecisionRecord]:
        """The most recently recorded decision, or ``None`` if the log is empty (thread-safe)."""
        with self._lock:
            return self._records[-1] if self._records else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def summary(self, *, limit: int = 20) -> dict:
        """Safe recent-decision summary over the current immutable snapshot."""
        return summarize_decisions(self.records, limit=limit)
