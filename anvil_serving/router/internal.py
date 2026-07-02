"""Common internal representation + the Backend seam.

The front door (``front_door.py``) translates each wire dialect (Anthropic
Messages / OpenAI Chat Completions) into a single ``InternalRequest`` and hands
it to one injectable :class:`Backend`. The backend is dialect-agnostic: it just
yields plain text deltas; the dialect layer re-frames those deltas into the
caller's native SSE on the way out.

Stdlib-only by design (no third-party deps). This module defines:

* :class:`Message` / :class:`InternalRequest` — the normalized request shape.
* :class:`Backend` — a ``typing.Protocol`` seam (M0). A later task (T011)
  formalizes the seam registry; here it is minimal but real.
* :func:`flatten_content` / :func:`estimate_tokens` — small normalization helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence

from typing import Protocol, runtime_checkable


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


class NoAvailableTierError(Exception):
    """No quality-gated tier is BOUND to serve a request's work class.

    Raised by the routing backend (T012) when every tier in the gated/allowed
    candidate list is unbound — e.g. the only tier the quality gate permits for
    ``planning`` is a cloud tier whose credential env var is unset, so it was
    skipped at startup. The router does NOT fall back to an out-of-gate tier:
    availability must never silently override the quality gate, so it raises this
    instead, and the front door renders it as a clean 503 dialect error envelope
    (defined here, alongside :class:`DialectError`, so the front door can catch it
    without importing the routing layer — which would be a cycle).

    Carries the ``work_class`` and the gated ``candidates`` for the operator.
    """

    def __init__(self, work_class: Optional[str], candidates: Sequence[str]):
        self.work_class = work_class
        self.candidates = tuple(candidates)
        cands = list(self.candidates)
        super().__init__(
            f"no quality-gated tier available for work_class={work_class!r}: "
            f"gated candidates {cands} are unbound. Configure that tier's "
            f"credentials/endpoint (set its auth_env, or make the local "
            f"base_url reachable); the router refuses to bypass the quality "
            f"gate by serving from a tier the gate did not allow."
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
    body so later stages (routing, verify) can inspect dialect-specific fields
    without re-parsing; ``dialect`` records which front door admitted it.
    """

    model: str
    messages: List[Message]
    system: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    stream: bool = False
    dialect: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def last_user_text(self) -> str:
        """Text of the most recent ``user`` message (empty if none)."""
        for m in reversed(self.messages):
            if m.role == "user":
                return m.content
        return ""


@dataclass
class StructuredResult:
    """Structured fields from a backend response, carried as a per-thread side channel.

    Backends that surface structured fields (``CloudBackend`` / ``RelayBackend``)
    populate a ``threading.local`` during each ``generate()`` call. After the
    generator is fully drained, callers read ``get_last_structured()`` to build a
    :class:`~anvil_serving.router.verify.ResponseView` with a real
    ``finish_reason`` and ``tool_calls``, making ``NotTruncated`` and
    ``ToolCallJSONValid`` genuinely live on the serve path (#42 / #52).

    ``finish_reason``: raw upstream stop reason, passed through verbatim.
      Anthropic: ``"end_turn"`` / ``"tool_use"`` / ``"max_tokens"`` / ``"stop_sequence"``.
      OpenAI: ``"stop"`` / ``"tool_calls"`` / ``"length"``.
      Dialects translate to their own wire values when rendering.

    ``tool_calls``: normalized list — each dict has:
      ``"name"`` (str), ``"id"`` (str),
      ``"arguments"`` (str — JSON string from OpenAI; dict — already-parsed from Anthropic).

    ``usage``: the upstream's REAL token accounting, normalized to
    ``{"input_tokens": int, "output_tokens": int}`` (Anthropic wire names;
    OpenAI's ``prompt_tokens``/``completion_tokens`` are mapped in).  ``None``
    when the upstream reported none — the dialect layer then falls back to the
    word-count estimate as before.  Harnesses use these numbers for context
    management, so passing the real counts through matters.
    """

    finish_reason: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    usage: Optional[Dict[str, int]] = None


@runtime_checkable
class Backend(Protocol):
    """The inference seam: turn an :class:`InternalRequest` into text deltas.

    Implementations yield the completion as a sequence of short text pieces
    ("tokens"); streaming vs. non-streaming framing is the dialect's job, not
    the backend's. Trusted/in-process only — no plugin loading here (M0).
    """

    def generate(self, request: InternalRequest) -> Iterator[str]:
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


def estimate_tokens(texts: Sequence[str]) -> int:
    """Cheap, deterministic token estimate (NOT a real tokenizer).

    Used only to populate the ``usage`` blocks with plausible integers. Counts
    whitespace-separated words across the given texts.
    """
    total = 0
    for t in texts:
        if t:
            total += len(t.split())
    return total
