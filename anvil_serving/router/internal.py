"""Common internal representation + the Backend seam.

The front door (``front_door.py``) translates each wire dialect (Anthropic
Messages / OpenAI Chat Completions) into a single ``InternalRequest`` and hands
it to one injectable :class:`Backend`. The backend is dialect-agnostic: it
yields answer text and, when reported, distinct reasoning deltas; the dialect
layer re-frames those deltas into the caller's native SSE on the way out.

Stdlib-only by design (no third-party deps). This module defines:

* :class:`Message` / :class:`InternalRequest` — the normalized request shape.
* :class:`Backend` — a ``typing.Protocol`` seam (M0). A later task (T011)
  formalizes the seam registry; here it is minimal but real.
* :func:`flatten_content` / :func:`estimate_tokens` — small normalization helpers.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Union, TYPE_CHECKING

from typing import Protocol, runtime_checkable

if TYPE_CHECKING:
    from .decision_log import TokenUsage


class DialectError(Exception):
    """A dialect rejected a JSON-parseable request (e.g. a missing required
    field). The front door converts it into an HTTP error with the carried
    status and error-type, so dialects can speak their own error vocabulary
    (Anthropic uses ``invalid_request_error``) without importing http.server.
    """

    def __init__(self, status: int, etype: str, message: str):
        super().__init__(message)
        self.status = status
        self.etype = etype
        self.message = message


class BackendClientError(Exception):
    """A backend rejected a request for a caller-correctable reason.

    ``message`` is safe to return to the caller. Backend implementations must
    keep upstream hosts, credentials, response bodies, and provider details in
    server-side logs rather than attaching them to this exception.
    """

    def __init__(self, status: int, etype: str, message: str):
        if status < 400 or status > 499:
            raise ValueError("BackendClientError status must be a 4xx code")
        super().__init__(message)
        self.status = status
        self.etype = etype
        self.message = message


class NoAvailableTierError(Exception):
    """A configured direct route cannot dispatch to its single tier."""

    def __init__(
        self,
        model: Optional[str],
        candidates: Sequence[str],
        *,
        kind: str = "unbound",
    ):
        self.model = model
        self.candidates = tuple(candidates)
        self.kind = kind
        detail = {
            "unknown_model": "model alias is not configured",
            "over_context": "request exceeds the configured tier context window",
            "media_limit": "request exceeds the configured tier media limits",
            "unsupported_tools": "configured tier does not support tools",
            "unavailable": "configured tier is not ready",
            "unbound": "configured tier has no bound backend",
        }.get(kind, "configured tier could not serve the request")
        super().__init__(
            f"{detail}: model={model!r}, tiers={list(self.candidates)!r}"
        )


@dataclass
class Message:
    """A single normalized chat message: a role and flattened text content."""

    role: str
    content: str


@dataclass
class InternalRequest:
    """Dialect-neutral request handed to a :class:`Backend`.

    Both wire schemas normalize into this. ``raw`` keeps the original parsed
    body so the relay can preserve dialect-specific fields
    without re-parsing; ``dialect`` records which front door admitted it.
    """

    model: str
    messages: List[Message]
    system: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stop: Optional[List[str]] = None
    stream: bool = False
    dialect: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelDelta:
    """One ordered model-output delta.

    ``text`` is user-visible answer text. ``reasoning`` is provider-reported
    reasoning that must remain a distinct structured field and must never be
    concatenated into ``text``.
    """

    text: Optional[str] = None
    reasoning: Optional[str] = None

BackendDelta = Union[str, ModelDelta]


@dataclass
class StructuredResult:
    """Structured fields from a backend response, carried as a per-thread side channel.

    A relay backend populates a ``threading.local`` during each ``generate()``
    call. After the generator is fully drained, the dialect layer reads
    ``get_last_structured()`` to preserve upstream ``finish_reason``,
    ``tool_calls``, token usage, and reasoning in the direct response.

    ``finish_reason``: raw upstream stop reason, passed through verbatim.
      Anthropic: ``"end_turn"`` / ``"tool_use"`` / ``"max_tokens"`` / ``"stop_sequence"``.
      OpenAI: ``"stop"`` / ``"tool_calls"`` / ``"length"``.
      Dialects translate to their own wire values when rendering.

    ``tool_calls``: normalized list — each dict has:
      ``"name"`` (str), ``"id"`` (str),
      ``"arguments"`` (str — JSON string from OpenAI; dict — already-parsed from Anthropic).

    ``usage``: the upstream's REAL token accounting, normalized to
    ``{"input_tokens": int, "output_tokens": int}`` (Anthropic wire names;
    OpenAI's ``prompt_tokens``/``completion_tokens`` are mapped in). When the
    upstream reports reasoning-token accounting, the optional
    ``reasoning_tokens`` key carries it. When it reports prompt-cache
    accounting (OpenAI-compatible engines such as vLLM with
    ``--enable-prompt-tokens-details`` emit
    ``prompt_tokens_details.cached_tokens``; Anthropic emits
    ``cache_read_input_tokens``), the optional ``cache_read_input_tokens`` key
    carries it. Optional counters are absent, never zero-filled, when the
    upstream omits them. ``None`` means the upstream reported no usage at all.
    Harnesses use these numbers for context management and cache-hit
    visibility, so passing the real counts through matters.

    ``reasoning``: provider-reported reasoning, kept distinct from visible
    answer text and rendered only by compatible dialects.
    """

    finish_reason: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    usage: Optional[Dict[str, int]] = None
    reasoning: Optional[str] = None
    # Accounting metadata stays separate from the legacy dialect wire counts.
    # Completion metadata does not change legacy wire-result equality.
    normalized_usage: Optional[TokenUsage] = field(default=None, compare=False)


@runtime_checkable
class Backend(Protocol):
    """The inference seam: turn an :class:`InternalRequest` into model deltas.

    Implementations yield short answer strings or :class:`ModelDelta` values
    when reasoning must remain separate from answer text. Streaming vs.
    non-streaming framing is the dialect's job, not the backend's.
    Trusted/in-process only — no plugin loading here (M0).
    """

    def generate(self, request: InternalRequest) -> Iterator[BackendDelta]:
        ...


def flatten_content(content: Any) -> str:
    """Normalize a wire ``content`` field to a plain string.

    Both dialects allow ``content`` to be either a bare string or a list of
    content blocks (``[{"type": "text", "text": "..."}, ...]``). For M0 we keep
    only text; non-text blocks (images, tool_use/tool_result) are dropped from
    the normalized text — they remain available in ``InternalRequest.raw``.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping) and "text" in block:
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return str(content)


def normalize_messages(raw_messages: Any) -> List[Message]:
    """Build a list of :class:`Message` from a wire ``messages`` array."""
    out: List[Message] = []
    if not isinstance(raw_messages, (list, tuple)):
        return out
    for m in raw_messages:
        if isinstance(m, Mapping):
            out.append(Message(str(m.get("role", "user")),
                               flatten_content(m.get("content"))))
    return out


def normalize_stop(value: Any) -> Optional[List[str]]:
    """Normalize a wire ``stop`` field to a list of strings, or ``None`` if absent.

    OpenAI's ``stop`` accepts either a bare string or an array of up to 4
    strings; Anthropic's ``stop_sequences`` is always an array. This collapses
    both wire shapes to ``InternalRequest.stop``'s single internal
    representation (``List[str]``) so ``_build_body`` can re-render either
    dialect's native form without re-inspecting the raw body.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return [value] if value else None
    if isinstance(value, (list, tuple)):
        out = [str(v) for v in value if isinstance(v, str) and v]
        return out or None
    return None


def estimate_tokens(texts: Sequence[str]) -> int:
    """Cheap, deterministic lower-bound token estimate (NOT a real tokenizer).

    Used for ``usage`` blocks and the fail-closed context admission check.
    ``max(words, utf8_bytes // 4)`` over the combined texts: the word count
    alone undercounts CJK, code, and base64 payloads by large factors, while
    bytes/4 is the common transformer-tokenizer floor for such content. Still
    an estimate — genuinely borderline requests are the upstream's to reject.
    """
    words = 0
    utf8_bytes = 0
    for t in texts:
        if t:
            words += len(t.split())
            utf8_bytes += len(t.encode("utf-8"))
    return max(words, utf8_bytes // 4)


class UsageInvocation:
    """One admitted ledger start, with generation/delivery joined before commit.

    Constructed only by trusted admission, never from request JSON. The scope
    provider must own the whole admission domain; absence fails closed.
    """

    def __init__(self, store, run_id, scope, caller, kind, model, *, registry=None, clock=None,
                 parent=None, usage_relation="exclusive", applicability=None, gateway_request_id=None):
        import threading
        import time
        import uuid
        from datetime import datetime, timezone
        from .decision_log import normalize_usage
        from .usage_store import RequestStart, RouteAssociation
        from .purpose import child_request_start, purpose_usage
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.started = time.monotonic()
        self._lock = threading.RLock()
        self.route = RouteAssociation()
        initial = (normalize_usage(None, "openai", applicability) if applicability is not None else
                   purpose_usage(kind) if kind != "chat" else normalize_usage(None, "openai"))
        units = (initial.input.applicability, initial.output.applicability)
        self.domain_id = scope.domain_id if scope is not None else None
        at = self.clock().isoformat(timespec="microseconds").replace("+00:00", "Z")
        self.start = (child_request_start(parent, accepted_at=at, kind=kind, model=model,
                       usage_relation=usage_relation, applicability=units) if parent is not None else
                      RequestStart(str(uuid.uuid4()), run_id, at, caller, kind, model,
                                   attempt_id=str(uuid.uuid4()), input_applicability=units[0], output_applicability=units[1]))
        self.registry = registry
        mutation = registry.usage_mutation() if registry is not None else nullcontext()
        with mutation:
            store.start(self.start, authority_scope=scope)
            self.registry_id = gateway_request_id or "req_" + self.start.request_id.replace("-", "")
            self.token = registry.begin(self.registry_id) if registry is not None else None
            if self.token is not None:
                self.token.activate()
                registry.attach_usage(self.registry_id, self.start)
        self.tokens = normalize_usage(None, "openai", units)
        self.dispatched = False
        self.generation = None
        self.delivery = None
        self.terminal = None

    def failure(self):
        try:
            self.store.record_failure(self.domain_id)
        except Exception:
            pass

    def dispatch(self):
        """Commit the actual transport attempt immediately before invoking it."""
        with self._lock, self.registry.usage_mutation() if self.registry is not None else nullcontext():
            if self.dispatched is True:
                return
            self.store.note_dispatch(self.start.request_id, self.route)
            self.dispatched = True
            if self.registry is not None:
                self.registry.observe_usage(self.registry_id, route=self.route, phase="dispatched")

    def ambiguous_dispatch(self):
        with self._lock:
            if self.dispatched is False:
                self.dispatched = None

    def capture(self, backend, outcome):
        """Called on the generating thread after closing its upstream iterator."""
        from .decision_log import TokenUsage
        from .usage_store import Observation
        with self._lock, self.registry.usage_mutation() if self.registry is not None else nullcontext():
            getter = getattr(backend, "get_last_normalized_usage", None)
            try:
                observed = getter() if callable(getter) else None
                if type(observed) is TokenUsage:
                    if (observed.input.applicability, observed.output.applicability) != (self.start.input_applicability, self.start.output_applicability):
                        raise ValueError("usage applicability mismatch")
                    self.tokens = observed
                if self.dispatched is True:
                    at = self.clock().isoformat(timespec="microseconds").replace("+00:00", "Z")
                    self.store.note_observation(self.start.request_id, Observation(1, at, self.tokens))
            except Exception:
                self.failure()
            self.generation = outcome
            if self.registry is not None:
                self.registry.observe_usage(self.registry_id, tokens=self.tokens, phase="finalizing")

    def finish(self, delivery):
        import time
        from .usage_store import Terminal
        with self._lock, self.registry.usage_mutation() if self.registry is not None else nullcontext():
            if self.terminal is not None:
                return
            self.delivery = delivery
            generation = self.generation or "rejected"
            coverage = ["usage_relation_unknown"] if self.start.usage_relation == "unobserved" else []
            if self.dispatched is None:
                coverage.append("dispatch_uncertain")
            if self.dispatched is not False and any(d.applicability == "applicable" and (d.source != "measured" or d.partial)
                                                    for d in (self.tokens.input, self.tokens.output)):
                coverage.append("usage_incomplete")
            outcome = delivery if delivery != "success" else generation
            at = self.clock().isoformat(timespec="microseconds").replace("+00:00", "Z")
            self.terminal = Terminal(self.start.request_id, at, self.dispatched, generation, delivery, outcome,
                                     self.route, self.tokens, latency_ms=max(0, int((time.monotonic() - self.started) * 1000)),
                                     coverage=tuple(coverage))
            try:
                self.store.finalize(self.terminal)
            except Exception:
                self.failure()
            finally:
                if self.token is not None:
                    from ..observability.workloads import WorkloadOutcome
                    self.token.finish(WorkloadOutcome(delivery))


def usage_invocation(request):
    value = request.raw.get("_anvil_usage")
    return value if type(value) is UsageInvocation else None
