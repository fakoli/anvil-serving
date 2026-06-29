"""Tests for the Tier-0 work-class classifier (harness-router:T003).

Proves each heuristic fires with the documented class/confidence, and — the
load-bearing property — that :func:`classify` NEVER raises on degenerate input
(empty messages, ``None`` content, a non-dict ``raw``, missing fields).
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
    big = " ".join("token" for _ in range(7000))  # 7000 words > 6000
    c = classify(_req(big))
    assert c.work_class == "long-context"
    assert c.confident is True
    assert c.signals["token_estimate"] > 6000


def test_thinking_flag_is_planning():
    c = classify(_req("anything", raw={"thinking": {"type": "enabled"}}))
    assert c.work_class == "planning"
    assert c.confident is True
    assert c.signals["thinking"] is True


def test_tools_present_is_bounded_edit():
    c = classify(_req("do something", raw={"tools": [{"name": "edit_file"}]}))
    assert c.work_class == "bounded-edit"
    assert c.confident is True
    assert c.signals["has_tools"] is True


def test_keyword_review():
    c = classify(_req("Please review this pull request"))
    assert c.work_class == "review"
    assert c.confident is True
    assert c.signals["matched_keyword"] == "review"


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
    assert c.signals["matched_keyword"] is None


# ── priority ordering ────────────────────────────────────────────────────────
def test_review_outranks_plan():
    # "review this plan" contains both keywords; review must win.
    c = classify(_req("review this plan"))
    assert c.work_class == "review"


def test_thinking_outranks_keyword():
    # A plain edit verb, but an explicit thinking budget -> planning.
    c = classify(_req("fix the bug", raw={"thinking": {"type": "enabled"}}))
    assert c.work_class == "planning"


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
