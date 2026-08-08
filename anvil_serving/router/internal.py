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

    A relay backend populates a ``threading.local`` during each ``generate()``
    call. After the generator is fully drained, the dialect layer reads
    ``get_last_structured()`` to preserve upstream ``finish_reason``,
    ``tool_calls``, and token usage in the direct response.

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
    when the upstream reported none. Harnesses use these numbers for context
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
