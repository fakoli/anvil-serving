"""Tier-0 work-class classifier (harness-router:T003).

A cheap, deterministic, dependency-free pass over an :class:`InternalRequest`
that labels it with one of :data:`WORK_CLASSES`. It is the *first* signal intent
resolution leans on when the caller did not declare a preset or pin a tier.

Design constraints (load-bearing):

* **Never raises.** Every heuristic is defensively guarded and the whole body is
  wrapped so any unexpected input shape (empty messages, ``None`` content, a
  ``raw`` that is not a dict, missing keys) degrades to a low-confidence
  ``"chat"`` rather than an exception. Routing must always make progress.
* **Confidence == strength of signal.** A positive heuristic match is
  ``confident``; falling through to the default ``"chat"`` is *not* confident,
  which the intent layer reads as "ambiguous -> route to the safer tier".
* **Deterministic.** Pure function of the payload; no clocks, no I/O, no RNG.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, List, Mapping

from .internal import InternalRequest, estimate_tokens

# The closed taxonomy of work classes the router reasons about.
WORK_CLASSES = (
    "chat",
    "bounded-edit",
    "multi-file-refactor",
    "planning",
    "review",
    "long-context",
)

# Above this estimated token count a request is "long-context" regardless of
# what it is asking for: the dominant routing constraint is fitting the window.
_LONG_CONTEXT_TOKENS = 6000

# Keyword fingerprints, in priority order. Each entry is (work_class, phrases).
# The first group whose any phrase is a substring of (system + last user text)
# wins. Order matters: "review" outranks "planning" outranks refactor outranks
# the broad bounded-edit verbs, so a "review this plan" reads as review.
_KEYWORD_RULES = (
    ("review", ("review", "critique", "feedback", "audit")),
    ("planning", ("plan", "design", "architect", "decompose",
                  "break down", "step by step")),
    ("multi-file-refactor", ("refactor", "rename across", "every file",
                             "all files", "across the codebase")),
    ("bounded-edit", ("edit", "fix", "change", "add a", "update the",
                       "implement")),
)


def _safe_messages_text(request: InternalRequest) -> List[str]:
    """Best-effort list of message texts; tolerant of degenerate shapes."""
    texts: List[str] = []
    messages = getattr(request, "messages", None) or []
    for m in messages:
        content = getattr(m, "content", None)
        texts.append(content if isinstance(content, str) else "")
    return texts


def _safe_raw(request: InternalRequest) -> Mapping[str, Any]:
    """Return ``request.raw`` if it is a mapping, else an empty mapping."""
    raw = getattr(request, "raw", None)
    return raw if isinstance(raw, Mapping) else {}


@dataclass(frozen=True)
class Classification:
    """The Tier-0 verdict for one request.

    ``signals`` records which heuristics fired (token estimate, the thinking and
    tools flags, the matched keyword if any) for the decision log; it is wrapped
    in a read-only ``MappingProxyType`` so a frozen ``Classification`` cannot be
    mutated through it.
    """

    work_class: str
    confident: bool
    signals: Mapping[str, Any]


def classify(request: InternalRequest) -> Classification:
    """Label ``request`` with a work class. Cheap, deterministic, never raises.

    Heuristics are applied in priority order; the first to fire wins. Falling
    through to ``"chat"`` is treated as *ambiguous* (``confident=False``).
    """
    try:
        system = getattr(request, "system", None) or ""
        msg_texts = _safe_messages_text(request)
        raw = _safe_raw(request)

        token_estimate = estimate_tokens([system] + msg_texts)
        thinking = bool(raw.get("thinking"))
        tools = raw.get("tools")
        has_tools = bool(tools)

        signals: dict[str, Any] = {
            "token_estimate": token_estimate,
            "thinking": thinking,
            "has_tools": has_tools,
            "matched_keyword": None,
        }

        # 1. Window pressure dominates everything else.
        if token_estimate > _LONG_CONTEXT_TOKENS:
            return Classification("long-context", True, MappingProxyType(signals))

        # 2. An explicit thinking budget is a planning signal.
        if thinking:
            return Classification("planning", True, MappingProxyType(signals))

        # 3. Tools attached -> an agent loop doing bounded edits.
        if has_tools:
            return Classification("bounded-edit", True, MappingProxyType(signals))

        # 4. Keyword fingerprint over system + most recent user turn.
        last_user = getattr(request, "last_user_text", "") or ""
        haystack = (system + " " + last_user).lower()
        for work_class, phrases in _KEYWORD_RULES:
            for phrase in phrases:
                if phrase in haystack:
                    signals["matched_keyword"] = phrase
                    return Classification(work_class, True, MappingProxyType(signals))

        # 5. No strong signal -> chat, and ambiguous (not confident).
        return Classification("chat", False, MappingProxyType(signals))
    except Exception as e:  # pragma: no cover - safety net; must never escape.
        return Classification("chat", False, MappingProxyType({"error": str(e)}))
