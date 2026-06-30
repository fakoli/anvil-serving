"""Tier-0 work-class classifier (harness-router:T003).

A cheap, deterministic, dependency-free pass over an :class:`InternalRequest`
that labels it with one of :data:`WORK_CLASSES`. It is the *first* signal intent
resolution leans on when the caller did not declare a preset or pin a tier.

Design constraints (load-bearing):

* **Never raises.** Every heuristic is defensively guarded and the whole body is
  wrapped so any unexpected input shape (empty messages, ``None`` content, a
  ``raw`` that is not a dict, missing keys) degrades to a low-confidence
  ``"chat"`` rather than an exception. Routing must always make progress.
* **Confidence == strength of signal.** A single unambiguous match is
  ``confident``; falling through to the default ``"chat"`` — *or* a request whose
  keywords name two conflicting work classes — is *not* confident, which the
  intent layer reads as "ambiguous -> route to the safer tier".
* **Stated intent first, structure last.** Keywords describe what the caller is
  *asking for*; ``tools``/``thinking`` are frequently harness DEFAULTS attached
  to every request, not per-request intent. So the priority is: window pressure,
  then stated intent (keywords), then the structural hints. This stops a harness
  that always sends ``tools`` from collapsing every agentic turn to bounded-edit.
* **Deterministic.** Pure function of the payload; no clocks, no I/O, no RNG.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
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

# Above this estimated WORD count a request is "long-context" regardless of what
# it is asking for: the dominant routing constraint is fitting the window.
# NOTE: ``estimate_tokens`` counts whitespace-separated WORDS, not real tokens;
# dense code/JSON is typically 2-4x more real tokens than words. So this bound is
# in words (a conservative proxy; real tokens are typically higher): ~4000 words
# is roughly 5k-16k real tokens, flagging before a 32k-context tier overflows on
# dense content.
_LONG_CONTEXT_WORDS = 4000

# Keyword fingerprints, in priority order. Each entry is (work_class, phrases).
# Matched with WORD-BOUNDARY regex (not substring), so "change" does not fire on
# "exchange", "plan" on "planes", or "design" on "redesigned". Order encodes
# priority for the conflict tie-break: review > planning > refactor > the broad
# bounded-edit verbs, so a request naming two classes resolves to the higher one
# (but as an *ambiguous* match — see :func:`classify`).
_KEYWORD_PHRASES = (
    ("review", ("review", "critique", "feedback", "audit")),
    # "plan"/"plans"/"planning": word-boundary matching means "plan" alone misses
    # the gerund/plural — the two most natural ways to ask for planning — so list
    # them explicitly. (Planning is the eval-proven local-weak class that must
    # reach cloud; missing it silently leaks the work to a local tier.) The
    # OpenClaw plugin's classify.mjs mirrors this set — keep the two in sync.
    ("planning", ("plan", "plans", "planning", "design", "architect", "decompose",
                  "break down", "step by step", "roadmap")),
    ("multi-file-refactor", ("refactor", "rename across",
                             "across the codebase", "migrate the")),
    ("bounded-edit", ("edit", "fix", "change", "add a",
                      "update the", "implement", "patch")),
)

# Precompile one word-boundary regex per work class from its phrase set.
_KEYWORD_RULES = tuple(
    (
        work_class,
        re.compile(r"\b(?:" + "|".join(re.escape(p) for p in phrases) + r")\b"),
    )
    for work_class, phrases in _KEYWORD_PHRASES
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

    ``signals`` records which heuristics fired (token/word estimate, the
    ``thinking_enabled`` and ``has_tools`` flags, the list of matched keyword
    classes) for the decision log. It is **excluded from equality and hashing**
    (``compare=False, hash=False``) so a frozen ``Classification`` stays HASHABLE
    — the auto-generated ``__hash__`` would otherwise try to hash the
    ``MappingProxyType`` and raise (the same trap ``Intent.decision`` avoids).
    """

    work_class: str
    confident: bool
    signals: Mapping[str, Any] = field(compare=False, hash=False)


def classify(request: InternalRequest) -> Classification:
    """Label ``request`` with a work class. Cheap, deterministic, never raises.

    Priority (first to fire wins):

    1. **long-context** — estimated words over :data:`_LONG_CONTEXT_WORDS`.
    2. **stated intent** — word-boundary keyword scan over system + last user
       turn. Exactly one class matched -> that class, ``confident``. Two or more
       *conflicting* classes matched -> the highest-priority one, but
       ``confident=False`` (the intent layer routes ambiguity to the safer tier).
    3. **thinking enabled** — an active (not ``{"type": "disabled"}``) thinking
       budget -> ``planning``.
    4. **tools present** -> ``bounded-edit`` (an agent loop doing edits). Placed
       AFTER keywords so a "plan ..." request that also carries tools stays
       ``planning``.
    5. **default** -> ``chat`` (ambiguous, not confident).
    """
    try:
        system = getattr(request, "system", None) or ""
        msg_texts = _safe_messages_text(request)
        raw = _safe_raw(request)

        token_estimate = estimate_tokens([system] + msg_texts)

        # ``thinking`` is ENABLED only when present AND not explicitly disabled:
        # a harness may always send ``{"type": "disabled"}`` to opt out.
        thinking_raw = raw.get("thinking")
        thinking_enabled = bool(thinking_raw) and not (
            isinstance(thinking_raw, Mapping)
            and thinking_raw.get("type") == "disabled"
        )

        tools = raw.get("tools")
        has_tools = bool(tools)

        last_user = getattr(request, "last_user_text", "") or ""
        haystack = (system + " " + last_user).lower()
        # One entry per rule that fires; rules are in priority order, and each
        # rule is a distinct work class, so ``matched`` is the distinct set of
        # matched classes, highest priority first.
        matched = [wc for (wc, rx) in _KEYWORD_RULES if rx.search(haystack)]

        signals: dict[str, Any] = {
            "token_estimate": token_estimate,
            "thinking_enabled": thinking_enabled,
            "has_tools": has_tools,
            "matched_keywords": matched,
        }

        # 1. Window pressure dominates everything else.
        if token_estimate > _LONG_CONTEXT_WORDS:
            return Classification("long-context", True, MappingProxyType(signals))

        # 2. Stated intent (keywords) outranks structural hints.
        if len(matched) == 1:
            return Classification(matched[0], True, MappingProxyType(signals))
        if len(matched) > 1:
            # Conflicting intent: highest-priority class, but ambiguous.
            return Classification(matched[0], False, MappingProxyType(signals))

        # 3. An active thinking budget is a planning signal.
        if thinking_enabled:
            return Classification("planning", True, MappingProxyType(signals))

        # 4. Tools attached -> an agent loop doing bounded edits.
        if has_tools:
            return Classification("bounded-edit", True, MappingProxyType(signals))

        # 5. No strong signal -> chat, and ambiguous (not confident).
        return Classification("chat", False, MappingProxyType(signals))
    except Exception as e:  # pragma: no cover - safety net; must never escape.
        return Classification("chat", False, MappingProxyType({"error": str(e)}))
