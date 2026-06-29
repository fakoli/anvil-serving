"""Tier-fallback tests (harness-router:T009, acceptance criterion AC1).

A forced LOCAL verify-failure must end up serving a CLOUD response, with the
fallback AND the per-tier token counts recorded in the decision log. These cases
also pin the surrounding robustness contract: a passing local serves locally with
no fallback; a backend that *raises* is a failed attempt (the walk proceeds, no
exception escapes); an empty candidate list yields an exhausted result.

Hermetic and stdlib-only (pytest is the only test dep). Backends are the
deterministic in-process :class:`StaticBackend` so pass/fail is exact:

* a FAILING local backend yields an empty completion -> the ``NonEmptyContent``
  verifier hard-fails (the thinking-budget-starvation failure from the eval);
* a PASSING backend yields valid non-empty text -> the default chain passes.
"""
from __future__ import annotations

from typing import Iterator

from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.config import RouterConfig, Tier
from anvil_serving.router.decision_log import DecisionLog
from anvil_serving.router.fallback import (
    Budget,
    RoutingDecision,
    route_with_fallback,
)
from anvil_serving.router.internal import InternalRequest, Message


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
def make_tier(tier_id: str, privacy: str) -> Tier:
    """A minimal valid :class:`Tier` (privacy drives the backend selection)."""
    return Tier(
        id=tier_id,
        base_url="https://example.test",
        dialect="anthropic" if privacy == "cloud" else "openai",
        context_limit=32_000,
        privacy=privacy,
        tool_support=True,
        auth_env="ANVIL_TEST_KEY",
    )


def make_config(*tiers: Tier) -> RouterConfig:
    return RouterConfig(tiers=tuple(tiers), presets={}, mapping_version="test")


def make_request(text: str = "Please implement the parser for me") -> InternalRequest:
    return InternalRequest(
        model="anvil/quick-edit",
        system="You are a careful coding assistant",
        messages=[Message("user", text)],
    )


# A failing local backend: an empty completion -> NonEmptyContent hard-fails.
FAILING = StaticBackend([""])
# A passing backend: valid non-empty multi-delta text -> default chain passes.
PASSING = StaticBackend(["Here", " is", " the", " answer"])
PASSING_TEXT = "Here is the answer"


class RaisingBackend:
    """A backend whose generator raises mid-stream (repo gotcha #1: an OOM-killed
    scheduler / reset connection). The fallback walk must treat this as a failed
    attempt, never let it propagate."""

    def generate(self, request: InternalRequest) -> Iterator[str]:
        raise RuntimeError("scheduler died, exit code -9")
        yield ""  # unreachable; marks generate() a generator function


def local_or_cloud(failing_local, passing_cloud):
    """``backend_for`` that dispatches purely on tier privacy (DI seam)."""
    return lambda tier: failing_local if tier.privacy == "local" else passing_cloud


# --------------------------------------------------------------------------- #
# AC1: forced local fail -> cloud served, fallback + tokens logged
# --------------------------------------------------------------------------- #
def test_forced_local_failure_falls_back_to_cloud_and_logs():
    config = make_config(make_tier("fast-local", "local"), make_tier("cloud", "cloud"))
    decision = RoutingDecision(tiers=("fast-local", "cloud"), work_class="bounded-edit")
    log = DecisionLog()

    result = route_with_fallback(
        make_request(),
        decision,
        config,
        local_or_cloud(FAILING, PASSING),
        log=log,
    )

    # Cloud served the response.
    assert result.served_tier == "cloud"
    assert result.text == PASSING_TEXT
    assert result.exhausted is False

    rec = result.record
    # The fallback happened and is recorded.
    assert rec.fell_back is True
    assert rec.served_tier == "cloud"
    # One AttemptRecord per attempted tier, in order.
    assert tuple(a.tier_id for a in rec.attempts) == ("fast-local", "cloud")
    local_attempt, cloud_attempt = rec.attempts
    assert local_attempt.outcome == "fallback"
    assert local_attempt.verifier_passed is False
    assert cloud_attempt.outcome == "served"
    assert cloud_attempt.verifier_passed is True

    # Token counts were recorded (per-attempt and aggregated).
    assert rec.total_prompt_tokens > 0
    assert rec.total_completion_tokens > 0
    assert local_attempt.prompt_tokens > 0
    assert cloud_attempt.completion_tokens > 0

    # The decision was appended to the log (metadata only).
    assert len(log) == 1
    assert log.last is rec


def test_fallback_reason_names_the_failing_verifier():
    config = make_config(make_tier("fast-local", "local"), make_tier("cloud", "cloud"))
    decision = RoutingDecision(tiers=("fast-local", "cloud"), work_class="chat")

    result = route_with_fallback(
        make_request(), decision, config, local_or_cloud(FAILING, PASSING)
    )

    local_attempt = result.record.attempts[0]
    # The structural verifier that tripped is named in the audit reason.
    assert "non_empty_content" in local_attempt.verify_reason


# --------------------------------------------------------------------------- #
# passing local -> served locally, no fallback
# --------------------------------------------------------------------------- #
def test_passing_local_serves_first_tier_no_fallback():
    config = make_config(make_tier("fast-local", "local"), make_tier("cloud", "cloud"))
    decision = RoutingDecision(tiers=("fast-local", "cloud"), work_class="chat")

    # Both privacies map to a passing backend; the local one should win.
    result = route_with_fallback(
        make_request(), decision, config, lambda tier: PASSING
    )

    assert result.served_tier == "fast-local"
    assert result.text == PASSING_TEXT
    assert result.exhausted is False
    assert result.record.fell_back is False
    assert len(result.record.attempts) == 1
    assert result.record.attempts[0].outcome == "served"


# --------------------------------------------------------------------------- #
# a raising backend is a failed attempt; the walk proceeds; nothing escapes
# --------------------------------------------------------------------------- #
def test_raising_backend_is_an_error_attempt_then_falls_back():
    config = make_config(make_tier("fast-local", "local"), make_tier("cloud", "cloud"))
    decision = RoutingDecision(tiers=("fast-local", "cloud"), work_class="chat")

    # No exception should escape this call.
    result = route_with_fallback(
        make_request(),
        decision,
        config,
        local_or_cloud(RaisingBackend(), PASSING),
    )

    assert result.served_tier == "cloud"
    assert result.text == PASSING_TEXT
    error_attempt = result.record.attempts[0]
    assert error_attempt.outcome == "error"
    assert error_attempt.verifier_passed is False
    assert "RuntimeError" in error_attempt.verify_reason


def test_all_backends_raise_yields_exhausted_no_exception():
    config = make_config(make_tier("fast-local", "local"), make_tier("cloud", "cloud"))
    decision = RoutingDecision(tiers=("fast-local", "cloud"), work_class="chat")

    result = route_with_fallback(
        make_request(), decision, config, lambda tier: RaisingBackend()
    )

    assert result.served_tier is None
    assert result.exhausted is True
    assert all(a.outcome == "error" for a in result.record.attempts)


# --------------------------------------------------------------------------- #
# empty candidate list -> exhausted, no raise
# --------------------------------------------------------------------------- #
def test_empty_tiers_is_exhausted_no_raise():
    config = make_config(make_tier("fast-local", "local"))
    decision = RoutingDecision(tiers=(), work_class="chat")

    result = route_with_fallback(
        make_request(), decision, config, lambda tier: PASSING
    )

    assert result.served_tier is None
    assert result.exhausted is True
    assert result.text == ""
    assert result.record.attempts == ()
    assert result.record.fell_back is False
