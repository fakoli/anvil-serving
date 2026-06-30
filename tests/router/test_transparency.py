"""Transparent-response + decision-log tests (harness-router:T010).

Pins the QGR §9 transparency surface added to ``decision_log``:

* **AC1** — :func:`response_metadata` / :func:`served_model` name the ACTUAL
  served tier and whether a fallback occurred (so a dialect can set the response
  ``model`` to what truly ran, not the abstract intent).
* **AC2** — :func:`decision_line` renders a single audit line carrying intent,
  work-class, served tier, verify verdict, fallback flag, the tier chain, and
  prompt/completion token COUNTS.

Plus the R012 secrets-hygiene contract (no message text, no response content, no
verifier reason string leaks into either surface) and a real-record integration
check against :func:`route_with_fallback`.

Hermetic, stdlib-only (pytest is the only test dep); fixtures are built directly.
"""
from __future__ import annotations

from typing import Iterator

from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.config import RouterConfig, Tier
from anvil_serving.router.decision_log import (
    AttemptRecord,
    DecisionRecord,
    decision_line,
    response_metadata,
    served_model,
)
from anvil_serving.router.fallback import RoutingDecision, route_with_fallback
from anvil_serving.router.internal import InternalRequest, Message


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
def _attempt(tier_id: str, outcome: str, *, passed: bool, reason: str) -> AttemptRecord:
    return AttemptRecord(
        tier_id=tier_id,
        verifier_passed=passed,
        verify_reason=reason,
        prompt_tokens=24,
        completion_tokens=10 if outcome == "served" else 0,
        outcome=outcome,
    )


def _fell_back_record(intent: str = "quick-edit") -> DecisionRecord:
    """A local-fail -> cloud-served record (the canonical fallback shape)."""
    return DecisionRecord(
        work_class="bounded-edit",
        requested_tiers=("fast-local", "cloud"),
        attempts=(
            _attempt("fast-local", "fallback", passed=False, reason="non_empty_content"),
            _attempt("cloud", "served", passed=True, reason="verify passed"),
        ),
        served_tier="cloud",
        total_prompt_tokens=24,
        total_completion_tokens=10,
        fell_back=True,
        intent=intent,
    )


def _exhausted_record() -> DecisionRecord:
    """Every candidate failed: no tier served."""
    return DecisionRecord(
        work_class="bounded-edit",
        requested_tiers=("fast-local", "cloud"),
        attempts=(
            _attempt("fast-local", "fallback", passed=False, reason="non_empty_content"),
            _attempt("cloud", "error", passed=False, reason="backend error: RuntimeError"),
        ),
        served_tier=None,
        total_prompt_tokens=48,
        total_completion_tokens=0,
        fell_back=True,
        intent="quick-edit",
    )


# --------------------------------------------------------------------------- #
# AC1: response metadata names the ACTUAL served tier + the fallback flag
# --------------------------------------------------------------------------- #
def test_ac1_response_metadata_names_served_tier_and_fallback():
    rec = _fell_back_record()
    meta = response_metadata(rec)

    assert meta["served_tier"] == "cloud"
    assert meta["fell_back"] is True
    assert meta["exhausted"] is False
    assert meta["work_class"] == "bounded-edit"
    assert meta["intent"] == "quick-edit"
    assert meta["tiers_tried"] == ("fast-local", "cloud")
    # served_model is what a dialect stamps as the response `model`.
    assert served_model(rec) == "cloud"


def test_ac1_response_metadata_is_read_only():
    # The block a dialect attaches must be immutable (MappingProxyType).
    meta = response_metadata(_fell_back_record())
    try:
        meta["served_tier"] = "tampered"  # type: ignore[index]
    except TypeError:
        pass
    else:  # pragma: no cover - mutation must not be allowed
        raise AssertionError("response_metadata must be read-only")


def test_ac1_exhausted_record_has_no_served_model():
    rec = _exhausted_record()
    meta = response_metadata(rec)

    assert served_model(rec) is None
    assert meta["served_tier"] is None
    assert meta["exhausted"] is True


# --------------------------------------------------------------------------- #
# AC2: the decision line carries every required field
# --------------------------------------------------------------------------- #
def test_ac2_decision_line_carries_all_fields():
    line = decision_line(_fell_back_record())

    assert "intent=quick-edit" in line
    assert "work_class=bounded-edit" in line
    assert "served=cloud" in line
    assert "verify=pass" in line
    assert "fell_back=true" in line
    assert "prompt=24" in line
    assert "completion=10" in line
    # The tier chain is rendered, '>'-joined in request order.
    assert "tiers=fast-local>cloud" in line


def test_ac2_decision_line_exhausted_marks_verify_fail_and_dash_served():
    line = decision_line(_exhausted_record())

    assert "verify=fail" in line
    assert "served=-" in line
    # Counts still render as integers (completion is 0 when nothing served).
    assert "prompt=48" in line
    assert "completion=0" in line


def test_ac2_decision_line_dashes_for_missing_intent_and_work_class():
    rec = DecisionRecord(
        work_class=None,
        requested_tiers=("cloud",),
        attempts=(),
        served_tier=None,
        total_prompt_tokens=0,
        total_completion_tokens=0,
        fell_back=False,
        intent=None,
    )
    line = decision_line(rec)
    assert "intent=-" in line
    assert "work_class=-" in line
    assert "served=-" in line
    assert "fell_back=false" in line


# --------------------------------------------------------------------------- #
# R012 secrets hygiene: no message/response content in either surface
# --------------------------------------------------------------------------- #
def test_no_content_leaks_into_decision_line_or_metadata():
    # The user's prompt text and a verifier's reason must never appear in the
    # transparency surface — only labels, tier ids, and integer counts.
    secret_prompt = "PLEASE-LEAK-MY-SECRET-PROMPT-TEXT"
    rec = DecisionRecord(
        work_class="bounded-edit",
        requested_tiers=("fast-local", "cloud"),
        attempts=(
            # verify_reason is a content-free verifier NAME (never the raw reason).
            _attempt("fast-local", "fallback", passed=False, reason="non_empty_content"),
            _attempt("cloud", "served", passed=True, reason="verify passed"),
        ),
        served_tier="cloud",
        total_prompt_tokens=24,
        total_completion_tokens=10,
        fell_back=True,
        intent="quick-edit",
    )

    line = decision_line(rec)
    rendered_meta = repr(dict(response_metadata(rec)))

    # The prompt text appears in neither surface.
    assert secret_prompt not in line
    assert secret_prompt not in rendered_meta
    # The verifier reason string is not surfaced either (only labels/integers).
    assert "non_empty_content" not in line
    assert "verify passed" not in line
    assert "non_empty_content" not in rendered_meta

    # Every space-separated field in the line is `label=value` where value holds
    # only tier ids ('>' / '-' allowed) or integers — no free text.
    for field in line.split(" "):
        key, _, value = field.partition("=")
        assert key and value, f"malformed audit field: {field!r}"


# --------------------------------------------------------------------------- #
# integration: real FallbackResult.record feeds the transparency surface
# --------------------------------------------------------------------------- #
def _make_tier(tier_id: str, privacy: str) -> Tier:
    return Tier(
        id=tier_id,
        base_url="https://example.test",
        dialect="anthropic" if privacy == "cloud" else "openai",
        context_limit=32_000,
        privacy=privacy,
        tool_support=True,
        auth_env="ANVIL_TEST_KEY",
    )


def test_integration_real_record_round_trips_through_transparency():
    # Drive route_with_fallback with a fail-then-pass backend pair (as test_fallback
    # does), then feed the REAL record to every transparency helper. intent is None
    # on a real fallback record today, so it must render as "-" with no exception.
    config = RouterConfig(
        tiers=(_make_tier("fast-local", "local"), _make_tier("cloud", "cloud")),
        presets={},
        mapping_version="test",
    )
    decision = RoutingDecision(tiers=("fast-local", "cloud"), work_class="bounded-edit")
    failing_local = StaticBackend([""])  # empty completion -> NonEmptyContent fails
    passing_cloud = StaticBackend(["Here", " is", " the", " answer"])
    request = InternalRequest(
        model="anvil/quick-edit",
        system="You are a careful coding assistant",
        messages=[Message("user", "Please implement the parser for me")],
    )

    result = route_with_fallback(
        request,
        decision,
        config,
        lambda tier: failing_local if tier.privacy == "local" else passing_cloud,
    )
    rec = result.record

    # The helpers operate on the real record without raising.
    assert served_model(rec) == "cloud"
    meta = response_metadata(rec)
    assert meta["served_tier"] == "cloud"
    assert meta["fell_back"] is True
    assert meta["exhausted"] is False
    assert meta["tiers_tried"] == ("fast-local", "cloud")

    line = decision_line(rec)
    assert "served=cloud" in line
    assert "verify=pass" in line
    assert "fell_back=true" in line
    # No declared intent on a real fallback record -> rendered as "-".
    assert "intent=-" in line
    # No prompt content leaks from the real request into the audit line.
    assert "implement the parser" not in line


# --------------------------------------------------------------------------- #
# backward-compat: DecisionRecord still constructs without `intent`
# --------------------------------------------------------------------------- #
def test_backward_compat_intent_defaults_to_none():
    # fallback.py builds DecisionRecord by keyword WITHOUT intent; that must keep
    # working and leave intent=None (rendered as "-").
    rec = DecisionRecord(
        work_class="chat",
        requested_tiers=("cloud",),
        attempts=(_attempt("cloud", "served", passed=True, reason="verify passed"),),
        served_tier="cloud",
        total_prompt_tokens=24,
        total_completion_tokens=10,
        fell_back=False,
    )
    assert rec.intent is None
    assert response_metadata(rec)["intent"] is None
    assert "intent=-" in decision_line(rec)
