"""Budget / thrash-guard tests (harness-router:T009, acceptance criterion AC2).

Sustained failure must respect all three escalation guards so it can neither
thrash forever nor overrun the session cost ceiling:

* **Retry cap** — more candidate tiers than ``max_attempts`` still stops after at
  most ``max_attempts`` real attempts; the loop terminates; the result is
  exhausted.
* **Circuit breaker** — a tier that fails ``circuit_threshold`` times across calls
  (with a shared breaker dict) is thereafter SKIPPED (``skipped-circuit``), with
  no backend call.
* **Per-session token budget** — with a low ceiling, escalation STOPS after the
  first attempt: a ``budget-stop`` record appears, no further tier is attempted,
  and the running total never overruns past the one in-flight attempt.

Hermetic and stdlib-only. Every backend here FAILS verify (empty completion ->
``NonEmptyContent`` hard-fails) so escalation is always forced.
"""
from __future__ import annotations

from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.config import RouterConfig, Tier
from anvil_serving.router.fallback import (
    Budget,
    RoutingDecision,
    route_with_fallback,
)
from anvil_serving.router.internal import InternalRequest, Message, estimate_tokens


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def make_tier(tier_id: str) -> Tier:
    return Tier(
        id=tier_id,
        base_url="https://example.test",
        dialect="openai",
        context_limit=32_000,
        privacy="local",
        tool_support=True,
        auth_env="ANVIL_TEST_KEY",
    )


def make_config(*tier_ids: str) -> RouterConfig:
    return RouterConfig(
        tiers=tuple(make_tier(t) for t in tier_ids),
        presets={},
        mapping_version="test",
    )


def make_request(text: str = "implement the feature now") -> InternalRequest:
    return InternalRequest(
        model="anvil/quick-edit",
        system="You are a coding assistant",
        messages=[Message("user", text)],
    )


# Every attempt fails verify -> escalation is always forced.
FAILING = StaticBackend([""])
ALWAYS_FAILING = lambda tier: FAILING

REAL_OUTCOMES = ("served", "fallback", "error")


def _prompt_tokens(request: InternalRequest) -> int:
    return estimate_tokens([request.system or ""] + [m.content for m in request.messages])


# --------------------------------------------------------------------------- #
# retry cap
# --------------------------------------------------------------------------- #
def test_retry_cap_bounds_attempts_and_terminates():
    config = make_config("t1", "t2", "t3", "t4", "t5")
    decision = RoutingDecision(tiers=("t1", "t2", "t3", "t4", "t5"), work_class="chat")
    budget = Budget(max_attempts=3, circuit_threshold=99)  # isolate the retry cap

    result = route_with_fallback(
        make_request(), decision, config, ALWAYS_FAILING, budget=budget
    )

    real = [a for a in result.record.attempts if a.outcome in REAL_OUTCOMES]
    assert len(real) <= budget.max_attempts
    assert len(real) == budget.max_attempts  # exactly the cap, given 5 > 3 tiers
    assert result.exhausted is True
    assert result.served_tier is None
    # Loop terminated short of the full candidate pool.
    assert len(result.record.attempts) == budget.max_attempts


# --------------------------------------------------------------------------- #
# circuit breaker (shared across calls)
# --------------------------------------------------------------------------- #
def test_circuit_breaker_opens_after_threshold_then_skips():
    config = make_config("t1")
    decision = RoutingDecision(tiers=("t1",), work_class="chat")
    budget = Budget(max_attempts=10, circuit_threshold=2)
    breaker: dict[str, int] = {}

    # Call 1: t1 fails -> one consecutive failure recorded.
    r1 = route_with_fallback(
        make_request(), decision, config, ALWAYS_FAILING, budget=budget, breaker=breaker
    )
    assert r1.record.attempts[0].outcome == "fallback"
    assert breaker["t1"] == 1

    # Call 2: t1 fails again -> reaches the threshold.
    r2 = route_with_fallback(
        make_request(), decision, config, ALWAYS_FAILING, budget=budget, breaker=breaker
    )
    assert r2.record.attempts[0].outcome == "fallback"
    assert breaker["t1"] == 2

    # Call 3: circuit now open -> t1 is skipped without a backend call.
    r3 = route_with_fallback(
        make_request(), decision, config, ALWAYS_FAILING, budget=budget, breaker=breaker
    )
    assert r3.record.attempts[0].outcome == "skipped-circuit"
    assert r3.record.attempts[0].prompt_tokens == 0
    assert r3.record.attempts[0].completion_tokens == 0
    assert r3.exhausted is True
    # A skip does not add another consecutive failure.
    assert breaker["t1"] == 2


def test_circuit_skip_counts_against_retry_cap_and_terminates():
    # Two tiers both with open circuits + a high retry cap: the loop must still
    # terminate (skips are bounded by max_attempts, not just by failures).
    config = make_config("t1", "t2")
    decision = RoutingDecision(tiers=("t1", "t2"), work_class="chat")
    budget = Budget(max_attempts=5, circuit_threshold=1)
    breaker = {"t1": 5, "t2": 5}

    result = route_with_fallback(
        make_request(), decision, config, ALWAYS_FAILING, budget=budget, breaker=breaker
    )

    assert [a.outcome for a in result.record.attempts] == ["skipped-circuit", "skipped-circuit"]
    assert result.exhausted is True
    assert result.served_tier is None


# --------------------------------------------------------------------------- #
# per-session token budget ceiling
# --------------------------------------------------------------------------- #
def test_budget_ceiling_stops_escalation_after_first_attempt():
    request = make_request()
    prompt_tokens = _prompt_tokens(request)
    assert prompt_tokens > 0  # sanity: the request carries real tokens

    config = make_config("t1", "t2", "t3")
    decision = RoutingDecision(tiers=("t1", "t2", "t3"), work_class="chat")
    # Ceiling chosen so the FIRST attempt proceeds (0 + prompt < ceiling) but the
    # SECOND would reach it (prompt + prompt >= ceiling, since FAILING adds no
    # completion tokens). max_attempts kept high so the budget, not the cap, stops.
    budget = Budget(max_total_tokens=prompt_tokens + 1, max_attempts=10)

    result = route_with_fallback(
        make_request(), decision, config, ALWAYS_FAILING, budget=budget
    )

    outcomes = [a.outcome for a in result.record.attempts]
    # First tier really attempted (and failed), second tier hit the budget stop;
    # the THIRD tier was never attempted -> escalation stopped at the ceiling.
    assert outcomes == ["fallback", "budget-stop"]
    assert result.exhausted is True
    assert result.served_tier is None

    # The running total never overran the ceiling by more than the single
    # in-flight attempt that was already counted.
    consumed = result.record.total_prompt_tokens + result.record.total_completion_tokens
    assert consumed < budget.max_total_tokens
    # Only the one real attempt contributed to the accounted tokens.
    real = [a for a in result.record.attempts if a.outcome in REAL_OUTCOMES]
    assert len(real) == 1


def test_zero_budget_is_unlimited():
    # max_total_tokens == 0 means "no ceiling": a passing tier still serves.
    config = make_config("t1")
    decision = RoutingDecision(tiers=("t1",), work_class="chat")
    passing = StaticBackend(["ok", " done"])

    result = route_with_fallback(
        make_request(), decision, config, lambda tier: passing,
        budget=Budget(max_total_tokens=0),
    )

    assert result.served_tier == "t1"
    assert result.exhausted is False
    assert not any(a.outcome == "budget-stop" for a in result.record.attempts)
