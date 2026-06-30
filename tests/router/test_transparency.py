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

import pytest

from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.config import RouterConfig, Tier
from anvil_serving.router.decision_log import (
    AttemptRecord,
    DecisionRecord,
    compute_cost_usd,
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
    # Content-bearing strings live in AttemptRecord.detail and .verify_reason,
    # which the transparency helpers must NOT surface. Putting the sentinels in
    # those real fields makes the non-leak assertions meaningful (not vacuous).
    secret_detail = "PLEASE-LEAK-MY-SECRET-DETAIL-quicksort-for-acme"
    secret_reason = "malformed-diff-line-+AWS_SECRET=hunter2"
    rec = DecisionRecord(
        work_class="bounded-edit",
        requested_tiers=("fast-local", "cloud"),
        attempts=(
            AttemptRecord("fast-local", False, secret_reason, 24, 0, "fallback", secret_detail),
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

    # Neither the per-attempt detail nor the verifier reason content is surfaced.
    for leak in (secret_detail, secret_reason, "verify passed"):
        assert leak not in line, leak
        assert leak not in rendered_meta, leak

    # Every space-separated field in the line is `label=value` with a non-empty
    # value — only tier ids ('>' / '-' allowed) or integers, never free text.
    for field in line.split(" "):
        key, _, value = field.partition("=")
        assert key and value, f"malformed audit field: {field!r}"


def test_decision_line_sanitizes_intent_log_injection():
    # intent can be caller-derived (the raw wire model string). A newline/space in
    # it must NOT inject a second audit line or break the key=value grammar.
    rec = DecisionRecord(
        work_class="bounded-edit",
        requested_tiers=("cloud",),
        attempts=(),
        served_tier="cloud",
        total_prompt_tokens=0,
        total_completion_tokens=0,
        fell_back=False,
        intent="chat\nintent=spoofed served=cloud verify=pass fell_back=false",
    )
    line = decision_line(rec)
    assert "\n" not in line  # single line — no injected second line
    # Exactly 8 label=value fields, each non-empty (the grammar held).
    fields = line.split(" ")
    assert len(fields) == 8, fields
    for field in fields:
        key, _, value = field.partition("=")
        assert key and value, f"malformed: {field!r}"
    # The spoofed 'served=cloud' from the injection is NOT a separate field: there
    # is exactly ONE served= field, carrying the real served tier.
    assert sum(f.startswith("served=") for f in fields) == 1


def test_decision_line_empty_tiers_renders_dash():
    # An exhausted record with NO candidates (route_with_fallback's empty-tiers
    # path) must render tiers=- (placeholder), not a value-less tiers=.
    rec = DecisionRecord(
        work_class=None, requested_tiers=(), attempts=(), served_tier=None,
        total_prompt_tokens=0, total_completion_tokens=0, fell_back=False,
    )
    line = decision_line(rec)
    assert "tiers=-" in line
    for field in line.split(" "):
        key, _, value = field.partition("=")
        assert key and value, f"malformed: {field!r}"


def test_decision_line_sanitizes_operator_tier_id_with_space():
    # An operator-set tier id containing a space must not break the grammar.
    rec = DecisionRecord(
        work_class="chat", requested_tiers=("fast local", "cloud"), attempts=(),
        served_tier="cloud", total_prompt_tokens=0, total_completion_tokens=0,
        fell_back=False,
    )
    line = decision_line(rec)
    assert "tiers=fast_local>cloud" in line  # space collapsed to '_'
    assert len(line.split(" ")) == 8


def test_tiers_tried_is_attempts_not_requested_pool():
    # tiers_tried is what ACTUALLY ran (record.attempts), which can be SHORTER
    # than the requested candidate pool (e.g. the first tier served).
    rec = DecisionRecord(
        work_class="chat",
        requested_tiers=("fast-local", "heavy-local", "cloud"),  # 3 offered
        attempts=(_attempt("fast-local", "served", passed=True, reason="verify passed"),),
        served_tier="fast-local",
        total_prompt_tokens=24,
        total_completion_tokens=10,
        fell_back=False,
    )
    meta = response_metadata(rec)
    assert meta["tiers_tried"] == ("fast-local",)  # only what ran
    assert meta["tiers_tried"] != rec.requested_tiers  # distinct from the pool


# --------------------------------------------------------------------------- #
# integration: real FallbackResult.record feeds the transparency surface
# --------------------------------------------------------------------------- #
def _make_tier(
    tier_id: str,
    privacy: str,
    *,
    cost_input_per_mtok=None,
    cost_output_per_mtok=None,
) -> Tier:
    return Tier(
        id=tier_id,
        base_url="https://example.test",
        dialect="anthropic" if privacy == "cloud" else "openai",
        context_limit=32_000,
        privacy=privacy,
        tool_support=True,
        auth_env="ANVIL_TEST_KEY",
        cost_input_per_mtok=cost_input_per_mtok,
        cost_output_per_mtok=cost_output_per_mtok,
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
# T003 LIVE-PATH: route_with_fallback's finalize() computes + records cost_usd.
# These exercise the PRODUCTION code path (finalize()), NOT a hand-built record —
# the gap the earlier tests missed (cost_usd defaulted to 0.0 in production).
# --------------------------------------------------------------------------- #
def _cost_request() -> InternalRequest:
    return InternalRequest(
        model="anvil/planning",
        system="You are a careful coding assistant",
        messages=[Message("user", "Please design the parser module for me")],
    )


def test_live_finalize_records_nonzero_cost_for_cost_bearing_served_tier():
    # A metered cloud tier WITH cost fields serves the request; the real finalize()
    # path must compute cost_usd = (prompt*in + completion*out)/1e6 > 0 from the
    # served tier's cost fields and the recorded token totals.
    cloud = _make_tier("cloud", "cloud", cost_input_per_mtok=3.0, cost_output_per_mtok=15.0)
    config = RouterConfig(tiers=(cloud,), presets={}, mapping_version="test")
    decision = RoutingDecision(tiers=("cloud",), work_class="planning")
    passing = StaticBackend(["Here", " is", " the", " design"])

    result = route_with_fallback(
        _cost_request(), decision, config, lambda tier: passing
    )
    rec = result.record

    assert rec.served_tier == "cloud"
    assert rec.cost_usd > 0.0, "live finalize() must record a non-zero cost for a metered tier"
    # The recorded cost is exactly the formula applied to the served tier's cost
    # fields and the record's own token totals (no hardcoded magic number).
    expected = compute_cost_usd(cloud, rec.total_prompt_tokens, rec.total_completion_tokens)
    assert rec.cost_usd == pytest.approx(expected)
    assert expected > 0.0  # guard: tokens and rates are both non-zero


def test_live_finalize_records_zero_cost_for_local_served_tier():
    # A local tier with NO cost fields serves -> cost_usd == 0.0 on the live path.
    local = _make_tier("fast-local", "local")  # no cost fields
    config = RouterConfig(tiers=(local,), presets={}, mapping_version="test")
    decision = RoutingDecision(tiers=("fast-local",), work_class="chat")
    passing = StaticBackend(["Local", " answer"])

    result = route_with_fallback(
        _cost_request(), decision, config, lambda tier: passing
    )
    rec = result.record

    assert rec.served_tier == "fast-local"
    assert rec.cost_usd == 0.0


def test_live_finalize_records_zero_cost_when_exhausted():
    # Every candidate fails -> nothing served -> cost_usd == 0.0 (exhausted path),
    # even though the (failed) candidate had cost fields.
    cloud = _make_tier("cloud", "cloud", cost_input_per_mtok=3.0, cost_output_per_mtok=15.0)
    config = RouterConfig(tiers=(cloud,), presets={}, mapping_version="test")
    decision = RoutingDecision(tiers=("cloud",), work_class="planning")
    failing = StaticBackend([""])  # empty completion -> NonEmptyContent fails

    result = route_with_fallback(
        _cost_request(), decision, config, lambda tier: failing
    )
    rec = result.record

    assert result.exhausted is True
    assert rec.served_tier is None
    assert rec.cost_usd == 0.0


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


# --------------------------------------------------------------------------- #
# T003: cost dimension — cost_usd on DecisionRecord + compute_cost_usd helper
# --------------------------------------------------------------------------- #
class _MockTier:
    """Minimal tier stub for cost-helper tests (no Tier import needed)."""

    def __init__(self, cost_input=None, cost_output=None):
        self.cost_input_per_mtok = cost_input
        self.cost_output_per_mtok = cost_output


def test_compute_cost_usd_formula():
    """compute_cost_usd applies the /1M formula correctly."""
    tier = _MockTier(cost_input=3.0, cost_output=15.0)
    # 1000 input + 500 output tokens
    # = (3.0 * 1000 + 15.0 * 500) / 1_000_000
    # = (3000 + 7500) / 1_000_000 = 0.0105
    cost = compute_cost_usd(tier, 1_000, 500)
    assert cost == pytest.approx(0.0105)


def test_compute_cost_usd_local_tier_returns_zero():
    """A tier with no cost fields (local) returns 0.0."""
    tier = _MockTier()  # both fields None
    assert compute_cost_usd(tier, 100_000, 5_000) == 0.0


def test_compute_cost_usd_partial_fields():
    """Only input cost set — output contributes 0."""
    tier = _MockTier(cost_input=3.0, cost_output=None)
    cost = compute_cost_usd(tier, 1_000_000, 999_999)
    assert cost == pytest.approx(3.0)  # 3.0 * 1e6 / 1e6


def test_decision_record_cost_usd_defaults_to_zero():
    """DecisionRecord.cost_usd defaults to 0.0 (backward-compat, local tiers)."""
    rec = DecisionRecord(
        work_class="chat",
        requested_tiers=("fast-local",),
        attempts=(_attempt("fast-local", "served", passed=True, reason="verify passed"),),
        served_tier="fast-local",
        total_prompt_tokens=50,
        total_completion_tokens=20,
        fell_back=False,
    )
    assert rec.cost_usd == 0.0


def test_decision_record_local_route_cost_usd_zero():
    """A local-only route records cost_usd == 0.0."""
    rec = DecisionRecord(
        work_class="bounded-edit",
        requested_tiers=("fast-local",),
        attempts=(_attempt("fast-local", "served", passed=True, reason="verify passed"),),
        served_tier="fast-local",
        total_prompt_tokens=120,
        total_completion_tokens=90,
        fell_back=False,
        cost_usd=0.0,
    )
    assert rec.cost_usd == 0.0


def test_decision_record_cloud_route_has_nonzero_cost_usd():
    """A metered-cloud route records a non-zero cost_usd (cost × tokens)."""
    # Simulate the caller computing cost before building the record.
    cloud_tier = _MockTier(cost_input=3.0, cost_output=15.0)
    prompt_tokens = 1_000
    completion_tokens = 500
    cost = compute_cost_usd(cloud_tier, prompt_tokens, completion_tokens)

    rec = DecisionRecord(
        work_class="planning",
        requested_tiers=("cloud",),
        attempts=(_attempt("cloud", "served", passed=True, reason="verify passed"),),
        served_tier="cloud",
        total_prompt_tokens=prompt_tokens,
        total_completion_tokens=completion_tokens,
        fell_back=False,
        cost_usd=cost,
    )
    assert rec.cost_usd > 0.0
    assert rec.cost_usd == pytest.approx(0.0105)  # (3.0*1000 + 15.0*500) / 1e6


def test_cost_usd_recorded_on_fallback_to_cloud():
    """A fallback-to-cloud record carries the cloud-tier cost (not the failed local cost)."""
    cloud_tier = _MockTier(cost_input=3.0, cost_output=15.0)
    prompt_tokens = 24
    completion_tokens = 10
    cost = compute_cost_usd(cloud_tier, prompt_tokens, completion_tokens)

    rec = _fell_back_record()
    # Rebuild with the computed cost.
    rec_with_cost = DecisionRecord(
        work_class=rec.work_class,
        requested_tiers=rec.requested_tiers,
        attempts=rec.attempts,
        served_tier=rec.served_tier,
        total_prompt_tokens=rec.total_prompt_tokens,
        total_completion_tokens=rec.total_completion_tokens,
        fell_back=rec.fell_back,
        intent=rec.intent,
        cost_usd=cost,
    )
    assert rec_with_cost.cost_usd > 0.0
    assert rec_with_cost.fell_back is True
    assert rec_with_cost.served_tier == "cloud"
