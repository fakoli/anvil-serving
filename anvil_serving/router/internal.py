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

try:  # Protocol is stdlib from 3.8+; runtime_checkable lets isinstance() work.
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover - 3.7 fallback, unused at >=3.9
    from typing_extensions import Protocol, runtime_checkable  # type: ignore


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
