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
from anvil_serving.router.verify import ResponseView


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
    # FIX #6: an error escalation (not just a verify-fail) must set fell_back.
    assert result.record.fell_back is True


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


# --------------------------------------------------------------------------- #
# /code-review max regressions
# --------------------------------------------------------------------------- #
def test_empty_verifiers_uses_defaults_not_unconditional_serve():
    # verifiers=[] must mean "use defaults" (matching the T008 commit window), NOT
    # "no gate" — otherwise all([]) == True would serve a failing local
    # unconditionally and void AC1.
    config = make_config(make_tier("fast-local", "local"), make_tier("cloud", "cloud"))
    decision = RoutingDecision(tiers=("fast-local", "cloud"), work_class="chat")

    result = route_with_fallback(
        make_request(), decision, config, local_or_cloud(FAILING, PASSING),
        verifiers=[],
    )
    # The failing local did NOT serve; the default chain ran and forced fallback.
    assert result.served_tier == "cloud"
    assert result.record.attempts[0].outcome == "fallback"


def test_unknown_tier_id_is_config_miss_not_backend_error():
    # A candidate id absent from config is a config miss: outcome "unknown-tier",
    # no token charge, no breaker bump, does not consume the retry cap — and the
    # walk still reaches the valid downstream tier.
    config = make_config(make_tier("cloud", "cloud"))  # no 'ghost' tier
    decision = RoutingDecision(tiers=("ghost", "cloud"), work_class="chat")
    breaker: dict = {}

    result = route_with_fallback(
        make_request(), decision, config, lambda tier: PASSING, breaker=breaker,
    )
    miss = result.record.attempts[0]
    assert miss.tier_id == "ghost"
    assert miss.outcome == "unknown-tier"
    assert miss.prompt_tokens == 0 and miss.completion_tokens == 0
    assert "ghost" not in breaker  # config miss must not open a circuit
    assert result.served_tier == "cloud"  # reached the valid tier


def test_open_local_circuit_does_not_starve_cloud():
    # An open LOCAL circuit must not consume the retry cap and block a healthy
    # cloud tier (the AC1 regression: forced local fail must escalate to cloud).
    config = make_config(make_tier("fast-local", "local"), make_tier("cloud", "cloud"))
    decision = RoutingDecision(tiers=("fast-local", "cloud"), work_class="chat")
    breaker = {"fast-local": 2}  # circuit already open (>= default threshold 2)

    result = route_with_fallback(
        make_request(), decision, config, local_or_cloud(FAILING, PASSING),
        budget=Budget(max_attempts=1), breaker=breaker,
    )
    assert result.record.attempts[0].outcome == "skipped-circuit"
    assert result.served_tier == "cloud"  # cloud still reachable under cap=1


def test_injected_response_view_factory_catches_truncation():
    # With the default text-only view, NotTruncated is inert. An injected factory
    # that surfaces finish_reason makes it fire: a non-empty-but-truncated local
    # response now fails verify and escalates to cloud.
    config = make_config(make_tier("fast-local", "local"), make_tier("cloud", "cloud"))
    decision = RoutingDecision(tiers=("fast-local", "cloud"), work_class="chat")
    truncated = StaticBackend(["a partial answer that got cut"])  # non-empty text

    def truncating_view(deltas, request):
        # Simulate a backend that surfaces finish_reason: the truncated local
        # output is marked "length"; a cleanly-stopped response is "stop".
        text = "".join(deltas)
        finish = "length" if "cut" in text else "stop"
        return ResponseView(text=text, finish_reason=finish)

    result = route_with_fallback(
        make_request(), decision, config, local_or_cloud(truncated, PASSING),
        response_view_factory=truncating_view,
    )
    # finish_reason='length' trips NotTruncated -> local fails -> cloud serves.
    assert result.served_tier == "cloud"
    assert result.record.attempts[0].outcome == "fallback"


def test_policy_decision_integration():
    # A REAL policy.RoutingDecision (T005) drops into route_with_fallback unchanged
    # (duck-typed on .tiers/.work_class) — proves the policy->fallback seam.
    import pathlib

    from anvil_serving.router.config import load as load_config
    from anvil_serving.router.intent import resolve as resolve_intent
    from anvil_serving.router.policy import route as policy_route
    from anvil_serving.router.profile_store import default_profile

    example = pathlib.Path(__file__).resolve().parents[2] / "configs" / "example.toml"
    cfg = load_config(str(example))
    req = InternalRequest(model="quick-edit", messages=[Message("user", "fix the bug")])
    decision = policy_route(resolve_intent(req, cfg), cfg, default_profile())

    result = route_with_fallback(req, decision, cfg, lambda tier: PASSING)
    assert result.served_tier in decision.tiers  # served one of the policy's tiers
    assert result.exhausted is False


# --------------------------------------------------------------------------- #
# FIX #6: fell_back includes errors (not only verify-fail escalations)
# --------------------------------------------------------------------------- #
def test_error_escalation_sets_fell_back():
    """A first-tier that ERRORS then a second tier serves must record fell_back=True.

    Before the fix, fell_back was only True for 'fallback' (verify-fail) attempts;
    a backend crash ('error') would leave fell_back=False even though the request
    clearly escalated past the first tier.
    """
    config = make_config(make_tier("fast-local", "local"), make_tier("cloud", "cloud"))
    decision = RoutingDecision(tiers=("fast-local", "cloud"), work_class="chat")

    result = route_with_fallback(
        make_request(), decision, config, local_or_cloud(RaisingBackend(), PASSING)
    )

    assert result.served_tier == "cloud"
    assert result.record.attempts[0].outcome == "error"
    assert result.record.fell_back is True  # error escalation must be counted


# --------------------------------------------------------------------------- #
# FIX #17: verifier returning None (non-VerifyResult) must not crash the walk
# --------------------------------------------------------------------------- #
def test_verifier_returning_none_treated_as_fail_not_crash():
    """A verifier seam that RETURNS None (instead of raising) must be treated as a
    verify FAILURE — not an AttributeError crash on None.passed.

    This guards the live serve path against misbehaving injected verifiers.
    The verifier returns None only on the FIRST call (fast-local) then passes,
    so cloud can serve and we confirm both: no crash AND the None was a fail.
    """
    from anvil_serving.router.verify import VerifyResult

    class NoneOnFirstCallVerifier:
        """Returns None on the first call (seam violation); passes on subsequent calls."""
        name = "none_on_first"

        def __init__(self):
            self._calls = 0

        def verify(self, response):
            self._calls += 1
            if self._calls == 1:
                return None  # not a VerifyResult — seam contract violation
            return VerifyResult(self.name, True, 1.0, "pass")

    config = make_config(make_tier("fast-local", "local"), make_tier("cloud", "cloud"))
    decision = RoutingDecision(tiers=("fast-local", "cloud"), work_class="chat")

    # If the bug is present, this raises AttributeError: 'NoneType' has no 'passed'.
    result = route_with_fallback(
        make_request(), decision, config,
        local_or_cloud(PASSING, PASSING),
        verifiers=[NoneOnFirstCallVerifier()],
    )

    # The None return was treated as a verify fail (not a crash): fast-local fell back.
    assert result.served_tier == "cloud", (
        f"expected cloud to serve after None-returning verifier failed fast-local; "
        f"got served_tier={result.served_tier!r}, exhausted={result.exhausted}"
    )
    assert result.record.attempts[0].outcome == "fallback"
    assert result.record.fell_back is True
