"""Bounded, metadata-only audit records for direct gateway requests.

Records contain route and tier identifiers, outcome labels, token counts,
correlation identifiers, and byte/latency measurements. They never contain
prompts, responses, audio, transcripts, synthesis text, or credentials.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Iterable, Mapping, Optional, Tuple

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
    """One direct tier attempt with content-free outcome metadata."""

    tier_id: str
    succeeded: bool
    reason: str
    prompt_tokens: int
    completion_tokens: int
    outcome: str
    detail: str = ""


@dataclass(frozen=True)
class DecisionRecord:
    """The metadata-only audit record for one direct request."""

    kind: Optional[str]
    requested_tier: str
    attempts: Tuple[AttemptRecord, ...]
    served_tier: Optional[str]
    total_prompt_tokens: int
    total_completion_tokens: int
    route: Optional[str] = None
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
    # ADR-0033: wall-clock creation stamp for durable evidence. Stamped by
    # :meth:`DecisionLog.record` when left at the zero default; aggregate views
    # remain snapshots of the buffer, never historical windows.
    unix_ts: float = 0.0


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


def _safe(token: Optional[str]) -> str:
    """Render a string field safely for the single-line ``label=value`` grammar.

    Collapses any whitespace/newline run and the ``>`` tier separator to ``_``.
    This is load-bearing because ``intent`` can be caller-derived (the raw wire
    ``model`` string): without it, a ``model`` carrying a newline
    would inject a forged second audit line (log injection), and any embedded
    space would break ``key=value`` parsing. Operator-set tier ids and kinds
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

        route=<i|-> kind=<wc|-> served=<tier|-> outcome=<served|failed> \
tier=<t|-> prompt=<n> completion=<n>

    ``verify`` is ``pass`` when a tier served (``served_tier`` is set) else
    ``fail``; ``-`` stands in for a missing/empty route/kind/served/tier.
    Every string field is passed
    through :func:`_safe` so the line is ALWAYS a single, parseable sequence of
    ``label=value`` tokens regardless of caller- or operator-supplied content.
    Only labels and integers — never message text or a verifier's raw reason (R012).
    """
    served = record.served_tier
    line = (
        f"route={_safe(record.route)} "
        f"kind={_safe(record.kind)} "
        f"served={_safe(served)} "
        f"outcome={'served' if served is not None else 'failed'} "
        f"tier={_safe(record.requested_tier)} "
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
        "succeeded": bool(_field(attempt, "succeeded", False)),
        "reason": _summary_safe(_field(attempt, "reason")),
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
    total_prompt = 0
    total_completion = 0
    total_request_bytes = 0
    total_response_bytes = 0
    total_latency_ms = 0
    for record in selected:
        attempts = tuple(_field(record, "attempts", ()) or ())
        attempt_items = [_attempt_summary(attempt) for attempt in attempts]
        served = _summary_safe(_field(record, "served_tier"))
        prompt_tokens = _int_field(record, "total_prompt_tokens")
        completion_tokens = _int_field(record, "total_completion_tokens")
        request_bytes = max(_int_field(record, "request_bytes"), 0)
        response_bytes = max(_int_field(record, "response_bytes"), 0)
        latency_ms = max(_int_field(record, "latency_ms"), 0)
        if served != "-":
            served_counts[served] += 1
        for attempt in attempt_items:
            outcome_counts[attempt["outcome"]] += 1
        total_prompt += prompt_tokens
        total_completion += completion_tokens
        total_request_bytes += request_bytes
        total_response_bytes += response_bytes
        total_latency_ms += latency_ms
        items.append({
            "route": _summary_safe(_field(record, "route")),
            "kind": _summary_safe(_field(record, "kind")),
            "requested_tier": _summary_safe(_field(record, "requested_tier")),
            "served_tier": served,
            "attempts": attempt_items,
            "total_prompt_tokens": prompt_tokens,
            "total_completion_tokens": completion_tokens,
            "request_bytes": request_bytes,
            "response_bytes": response_bytes,
            "latency_ms": latency_ms,
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
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
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

    def __init__(
        self,
        max_records: Optional[int] = DEFAULT_MAX_RECORDS,
        *,
        sink: Optional[Callable[[DecisionRecord], None]] = None,
    ) -> None:
        if max_records is not None and max_records <= 0:
            raise ValueError(f"max_records must be positive or None, got {max_records!r}")
        # deque(maxlen=None) is unbounded — the explicit opt-out.
        self._records: Deque[DecisionRecord] = deque(maxlen=max_records)
        self._lock = threading.Lock()
        # ADR-0033: optional durable sink (JSONL writer). Best-effort — a sink
        # failure never fails the request that produced the record.
        self._sink = sink

    def record(self, record: DecisionRecord) -> None:
        """Append ``record`` to the log, stamping ``unix_ts`` (thread-safe)."""
        if not record.unix_ts:
            record = dataclasses.replace(record, unix_ts=time.time())
        with self._lock:
            self._records.append(record)
        if self._sink is not None:
            try:
                self._sink(record)
            except Exception as exc:
                print(
                    "[anvil] warning decision sink write failed: %s" % type(exc).__name__,
                    file=sys.stderr,
                    flush=True,
                )

    @property
    def capacity(self) -> Optional[int]:
        """The ring-buffer cap, or ``None`` when unbounded."""
        return self._records.maxlen

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


#: Default size cap for one :class:`DecisionLogWriter` generation before the
#: single-rotation ``os.replace`` to ``<path>.1``.
DEFAULT_DECISION_LOG_MAX_BYTES = 64 * 1024 * 1024


class DecisionLogWriter:
    """Append-only, size-capped JSONL sink for decision records (ADR-0033).

    One JSON object per line, from the record's own metadata-only fields —
    the writer serializes :class:`DecisionRecord` dataclasses verbatim, so the
    no-prompt/no-response/no-credential guarantee is the record contract's.
    Rotation keeps exactly one previous generation (``<path>.1``).

    Construction fails when the path is unwritable — a configured evidence
    sink that cannot write is a boot error, not a silent downgrade. Later
    write failures are reported by the caller and never fail requests.
    """

    def __init__(
        self, path: str, *, max_bytes: int = DEFAULT_DECISION_LOG_MAX_BYTES
    ) -> None:
        if max_bytes < 1024:
            raise ValueError("decision log max_bytes must be at least 1024")
        self.path = path
        self.max_bytes = int(max_bytes)
        self._lock = threading.Lock()
        directory = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(directory):
            raise OSError("decision log directory does not exist: %s" % directory)
        with open(path, "a", encoding="utf-8"):
            pass

    def __call__(self, record: DecisionRecord) -> None:
        payload = dataclasses.asdict(record)
        line = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._lock:
            self._rotate_if_needed()
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()

    def _rotate_if_needed(self) -> None:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < self.max_bytes:
            return
        os.replace(self.path, self.path + ".1")
