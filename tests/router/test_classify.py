"""Tests for the Tier-0 work-class classifier (harness-router:T003).

Proves each heuristic fires with the documented class/confidence, that stated
intent (keywords) outranks the structural ``tools``/``thinking`` hints, that
matching is word-boundary (not substring), and — the load-bearing property —
that :func:`classify` NEVER raises on degenerate input (empty messages, ``None``
content, a non-dict ``raw``, missing fields).
"""
from __future__ import annotations

from anvil_serving.router.classify import (
    WORK_CLASSES,
    Classification,
    classify,
)
from anvil_serving.router.internal import InternalRequest, Message


def _req(text="", *, system=None, raw=None, model="x"):
    """Build a minimal InternalRequest with a single user turn."""
    return InternalRequest(
        model=model,
        messages=[Message("user", text)],
        system=system,
        raw=raw if raw is not None else {},
    )


# ── individual heuristics ────────────────────────────────────────────────────
def test_long_context_dominates():
    big = " ".join("token" for _ in range(7000))  # 7000 words > 4000 threshold
    c = classify(_req(big))
    assert c.work_class == "long-context"
    assert c.confident is True
    assert c.signals["token_estimate"] == 7000


def test_long_context_threshold_lowered_to_4000():
    # 4500 words: above the new 4000-word bound, below the old 6000 one.
    big = " ".join("token" for _ in range(4500))
    c = classify(_req(big))
    assert c.work_class == "long-context"
    assert c.confident is True


def test_thinking_flag_is_planning():
    c = classify(_req("anything", raw={"thinking": {"type": "enabled"}}))
    assert c.work_class == "planning"
    assert c.confident is True
    assert c.signals["thinking_enabled"] is True


def test_thinking_enabled_with_budget_is_planning():
    c = classify(_req("anything", raw={"thinking": {"type": "enabled",
                                                    "budget_tokens": 1024}}))
    assert c.work_class == "planning"
    assert c.confident is True
    assert c.signals["thinking_enabled"] is True


def test_thinking_disabled_is_not_planning():
    # A harness opting out via {"type": "disabled"} must NOT read as planning.
    c = classify(_req("anything", raw={"thinking": {"type": "disabled"}}))
    assert c.work_class != "planning"
    assert c.work_class == "chat"
    assert c.confident is False
    assert c.signals["thinking_enabled"] is False


def test_tools_present_is_bounded_edit():
    # Tools but no stated-intent keyword -> structural fallback to bounded-edit.
    c = classify(_req("do something", raw={"tools": [{"name": "edit_file"}]}))
    assert c.work_class == "bounded-edit"
    assert c.confident is True
    assert c.signals["has_tools"] is True


def test_keyword_review():
    c = classify(_req("Please review this pull request"))
    assert c.work_class == "review"
    assert c.confident is True
    assert c.signals["matched_keywords"] == ["review"]


def test_keyword_plan():
    c = classify(_req("Help me plan the migration"))
    assert c.work_class == "planning"
    assert c.confident is True


def test_keyword_refactor():
    c = classify(_req("refactor this module"))
    assert c.work_class == "multi-file-refactor"
    assert c.confident is True


def test_keyword_edit():
    c = classify(_req("fix the off-by-one bug"))
    assert c.work_class == "bounded-edit"
    assert c.confident is True


def test_neutral_is_ambiguous_chat():
    c = classify(_req("hello there"))
    assert c.work_class == "chat"
    assert c.confident is False
    assert c.signals["matched_keywords"] == []


# ── word-boundary matching (not substring) ───────────────────────────────────
def test_word_boundary_exchange_not_edit():
    # "exchange" must NOT trigger bounded-edit's "change".
    c = classify(_req("what is the exchange rate"))
    assert c.work_class != "bounded-edit"
    assert c.work_class == "chat"
    assert c.signals["matched_keywords"] == []


def test_word_boundary_planes_not_planning():
    # "planes" must NOT trigger planning's "plan".
    c = classify(_req("how do planes fly"))
    assert c.work_class != "planning"
    assert c.work_class == "chat"


def test_word_boundary_redesigned_not_planning():
    # "redesigned" must NOT trigger planning's "design".
    c = classify(_req("redesigned"))
    assert c.work_class != "planning"
    assert c.work_class == "chat"


# ── stated intent (keywords) outranks structural hints ───────────────────────
def test_keywords_beat_tools():
    # "plan ..." with tools attached stays planning; tools are a harness default.
    c = classify(_req("plan the migration", raw={"tools": [{"name": "edit"}]}))
    assert c.work_class == "planning"
    assert c.confident is True
    assert c.signals["has_tools"] is True


def test_keyword_beats_thinking():
    # A plain edit verb beats a thinking budget: stated intent wins over
    # structural hints (was the OLD inverse priority).
    c = classify(_req("fix the bug", raw={"thinking": {"type": "enabled"}}))
    assert c.work_class == "bounded-edit"
    assert c.confident is True


# ── conflicting / priority resolution ────────────────────────────────────────
def test_review_outranks_plan_but_ambiguous():
    # "review this plan" names two classes; review (higher priority) wins the
    # label, but the conflict makes it NOT confident.
    c = classify(_req("review this plan"))
    assert c.work_class == "review"
    assert c.confident is False
    assert set(c.signals["matched_keywords"]) == {"review", "planning"}


def test_conflicting_keywords_not_confident():
    # "review" (review) + "implement"/"fix" (bounded-edit) -> conflict.
    c = classify(_req("review and implement the fix"))
    assert c.confident is False
    assert c.work_class == "review"  # highest-priority of the matched classes


# ── never-raises on degenerate input ─────────────────────────────────────────
def test_empty_messages_does_not_raise():
    req = InternalRequest(model="x", messages=[])
    c = classify(req)
    assert isinstance(c, Classification)
    assert c.work_class in WORK_CLASSES


def test_none_content_does_not_raise():
    # Message content forced to None despite the str annotation.
    req = InternalRequest(model="x", messages=[Message("user", None)])  # type: ignore[arg-type]
    c = classify(req)
    assert c.work_class in WORK_CLASSES


def test_non_dict_raw_does_not_raise():
    req = InternalRequest(model="x", messages=[Message("user", "hi")])
    req.raw = ["not", "a", "dict"]  # type: ignore[assignment]
    c = classify(req)
    assert c.work_class in WORK_CLASSES


def test_system_only_keyword():
    c = classify(_req("", system="You audit code for security issues"))
    assert c.work_class == "review"
    assert c.confident is True


def test_result_signals_are_readonly():
    c = classify(_req("hello there"))
    try:
        c.signals["token_estimate"] = 999  # type: ignore[index]
    except TypeError:
        return  # MappingProxyType correctly rejects mutation
    raise AssertionError("signals should be a read-only mapping")


def test_classification_is_hashable():
    # signals is field(compare=False, hash=False) so a frozen Classification
    # stays hashable despite carrying a MappingProxyType.
    c = classify(_req("hello there"))
    assert hash(c) == hash(Classification(c.work_class, c.confident, c.signals))
    assert len({c, c}) == 1
