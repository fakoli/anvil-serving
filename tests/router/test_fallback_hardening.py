"""Hardening tests for the fallback path (#45 seam isolation + drain caps, #52 circuit breaker).

These tests pin the NEW robustness guarantees added by the hardening sprint:

Seam isolation (#45)
====================
A  A hanging verifier → request completes (verify-fail after budget), does NOT hang.
B  A raising observer/log seam → request still served (no crash).
C  A raising response_view_factory → falls back to default view, request served.

Drain byte caps (#45)
=====================
D  A tier producing > max_buffer_bytes → overflow outcome, no unbounded buffer.
   The next candidate (if any) is tried.

Circuit breaker — session-scoped, thread-safe, with decay (#52)
================================================================
E  Opens after circuit_threshold consecutive failures across requests.
F  After cooldown the circuit half-opens; a success closes it (self-heal).
G  Concurrent access does NOT corrupt state (thread-safety smoke test).

All tests use hermetic in-process backends (StaticBackend / custom) and a plain
RouterConfig so no network calls are made.
"""
from __future__ import annotations

import threading
import time
from typing import Iterator, List


from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.config import RouterConfig, Tier
from anvil_serving.router.fallback import (
    Budget,
    CircuitBreaker,
    RoutingDecision,
    route_with_fallback,
)
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.verify import ResponseView, VerifyResult


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def make_tier(tier_id: str, privacy: str = "local") -> Tier:
    return Tier(
        id=tier_id,
        base_url="https://example.test",
        dialect="openai" if privacy == "local" else "anthropic",
        context_limit=32_000,
        privacy=privacy,
        tool_support=True,
        auth_env="ANVIL_TEST_KEY",
    )


def make_config(*tier_ids_or_tiers) -> RouterConfig:
    tiers = tuple(
        t if isinstance(t, Tier) else make_tier(t)
        for t in tier_ids_or_tiers
    )
    return RouterConfig(tiers=tiers, presets={}, mapping_version="test")


def make_request() -> InternalRequest:
    return InternalRequest(
        model="anvil/quick-edit",
        system="You are a coding assistant",
        messages=[Message("user", "help me")],
    )


PASSING = StaticBackend(["ok response"])
FAILING = StaticBackend([""])  # empty → NonEmptyContent fails


# --------------------------------------------------------------------------- #
# A — Hanging verifier seam (#45)
# --------------------------------------------------------------------------- #
class HangingVerifier:
    """A verifier that blocks forever on its FIRST call, then passes on subsequent calls.

    Simulates a verifier that hangs on the first tier but not on the fallback:
    the daemon-thread guard must time out the first call and allow the fallback
    tier's verifier run to succeed normally.
    """
    name = "hanging"

    def __init__(self) -> None:
        self._calls = 0

    def verify(self, response: ResponseView) -> VerifyResult:
        self._calls += 1
        if self._calls == 1:
            # First call (local tier): block indefinitely; the timeout guard fires.
            time.sleep(9999)
        return VerifyResult(self.name, True, 1.0, "pass")


def test_hanging_verifier_times_out_and_completes():
    """A verifier that never returns must NOT hang the request.

    With a short verifier_timeout, route_with_fallback must complete and treat
    the hung verifier as a verify-failure (which escalates to the next tier).
    """
    config = make_config("local", make_tier("cloud", "cloud"))
    decision = RoutingDecision(tiers=("local", "cloud"), work_class="chat")

    # local → PASSING text, but the HangingVerifier blocks; cloud is the fallback.
    backends = {
        "local": PASSING,
        "cloud": StaticBackend(["cloud-served"]),
    }

    result = route_with_fallback(
        make_request(),
        decision,
        config,
        backend_for=lambda tier: backends[tier.id],
        verifiers=[HangingVerifier()],
        budget=Budget(max_attempts=3, circuit_threshold=99),
        verifier_timeout=0.1,  # short timeout so the test completes quickly
    )

    # The hung verifier was treated as a verify-fail; cloud served the response.
    assert result.served_tier == "cloud", (
        f"expected cloud to serve after hanging verifier timed out; "
        f"got served_tier={result.served_tier!r}, exhausted={result.exhausted}"
    )
    assert result.text == "cloud-served"
    assert result.record.fell_back is True
    assert result.record.attempts[0].outcome == "fallback"
    assert "verifier_timeout" in result.record.attempts[0].verify_reason


def test_hanging_verifier_does_not_hang_when_no_fallback_tier():
    """Even with no fallback, the request must complete (exhausted), not hang."""
    config = make_config("local")
    decision = RoutingDecision(tiers=("local",), work_class="chat")

    start = time.monotonic()
    result = route_with_fallback(
        make_request(),
        decision,
        config,
        backend_for=lambda tier: PASSING,
        verifiers=[HangingVerifier()],
        verifier_timeout=0.1,
    )
    elapsed = time.monotonic() - start

    assert result.exhausted is True
    # Must complete well under 1 second (the hang guard fired at 0.1s).
    assert elapsed < 2.0, f"hanging verifier timed out too slowly: {elapsed:.2f}s"


# --------------------------------------------------------------------------- #
# B — Raising observer/log seam (#45)
# --------------------------------------------------------------------------- #
class RaisingLog:
    """A decision log that raises every time record() is called."""
    def record(self, record) -> None:
        raise RuntimeError("log is broken")


def test_raising_log_does_not_crash_served_response():
    """A raising log seam must not propagate to the caller (response already served)."""
    config = make_config("local")
    decision = RoutingDecision(tiers=("local",), work_class="chat")

    # Must NOT raise; the result must still be served even though log.record() raises.
    result = route_with_fallback(
        make_request(),
        decision,
        config,
        backend_for=lambda tier: PASSING,
        log=RaisingLog(),
    )

    assert result.served_tier == "local"
    assert result.text == "ok response"
    assert result.exhausted is False


def test_raising_log_on_exhausted_result_also_does_not_crash():
    """Raising log on the exhausted path must also not propagate."""
    config = make_config("local")
    decision = RoutingDecision(tiers=("local",), work_class="chat")

    result = route_with_fallback(
        make_request(),
        decision,
        config,
        backend_for=lambda tier: FAILING,
        log=RaisingLog(),
    )

    assert result.exhausted is True  # no crash, just exhausted


# --------------------------------------------------------------------------- #
# C — Raising response_view_factory (#45)
# --------------------------------------------------------------------------- #
def _raising_factory(deltas, request):
    raise ValueError("factory is broken")


def test_raising_response_view_factory_falls_back_to_default():
    """A raising response_view_factory must fall back to build_response_view.

    The request must be served (or failed-over) rather than crashing. A fallback
    to the default view means the verifiers run on the plain text — PASSING text
    should still serve.
    """
    config = make_config("local")
    decision = RoutingDecision(tiers=("local",), work_class="chat")

    result = route_with_fallback(
        make_request(),
        decision,
        config,
        backend_for=lambda tier: PASSING,
        response_view_factory=_raising_factory,
    )

    # The factory raised; the default view was used instead; PASSING text passed.
    assert result.served_tier == "local"
    assert result.text == "ok response"
    assert result.exhausted is False


def test_raising_response_view_factory_with_failing_local_still_falls_back():
    """Even when the factory raises on a FAILING backend, we don't crash."""
    config = make_config("local", make_tier("cloud", "cloud"))
    decision = RoutingDecision(tiers=("local", "cloud"), work_class="chat")
    backends = {
        "local": FAILING,   # empty → fails NonEmptyContent
        "cloud": StaticBackend(["cloud-ok"]),
    }

    result = route_with_fallback(
        make_request(),
        decision,
        config,
        backend_for=lambda tier: backends[tier.id],
        response_view_factory=_raising_factory,
    )

    # local had an empty response → NonEmptyContent fails via default view.
    # cloud served.
    assert result.served_tier == "cloud"
    assert result.text == "cloud-ok"
    assert result.record.fell_back is True


# --------------------------------------------------------------------------- #
# D — Drain byte caps (#45)
# --------------------------------------------------------------------------- #
class ChunkyBackend:
    """Yields ``chunk_size`` bytes × ``num_chunks`` chunks of 'x' characters."""

    def __init__(self, chunk_size: int, num_chunks: int = 1):
        self._chunk = "x" * chunk_size
        self._num_chunks = num_chunks

    def generate(self, request: InternalRequest) -> Iterator[str]:
        for _ in range(self._num_chunks):
            yield self._chunk


def test_overflow_is_treated_as_failure_and_escalates():
    """A tier response exceeding max_buffer_bytes → overflow outcome + fallback."""
    config = make_config("local", make_tier("cloud", "cloud"))
    decision = RoutingDecision(tiers=("local", "cloud"), work_class="chat")
    backends = {
        "local": ChunkyBackend(chunk_size=200, num_chunks=1),   # 200 bytes
        "cloud": StaticBackend(["cloud-ok"]),
    }

    # Cap below the local response size
    result = route_with_fallback(
        make_request(),
        decision,
        config,
        backend_for=lambda tier: backends[tier.id],
        max_buffer_bytes=100,   # 100 bytes cap; local produces 200 bytes
    )

    assert result.served_tier == "cloud"
    assert result.text == "cloud-ok"
    local_attempt = result.record.attempts[0]
    assert local_attempt.outcome == "overflow", (
        f"expected 'overflow' outcome, got {local_attempt.outcome!r}"
    )
    assert "max_buffer_bytes" in local_attempt.verify_reason
    assert result.record.fell_back is True  # overflow escalation records fell_back


def test_overflow_stops_at_cap_not_buffer_entire_response():
    """Drain must STOP at the cap, not buffer the entire oversized response.

    If the drain reads past the cap, the memory cost is unbounded. This test
    yields many large chunks and verifies the drain stops early by checking the
    partial deltas list length / total content size.
    """
    large_chunk = "y" * 1000   # 1 KB per chunk
    num_chunks = 100             # 100 KB total; far above our 10 KB cap

    class EarlyStopCheckBackend:
        """Counts how many chunks were yielded before the drain stopped."""
        chunks_yielded = 0

        def generate(self, request: InternalRequest) -> Iterator[str]:
            for _ in range(num_chunks):
                EarlyStopCheckBackend.chunks_yielded += 1
                yield large_chunk

    config = make_config("local")
    decision = RoutingDecision(tiers=("local",), work_class="chat")

    result = route_with_fallback(
        make_request(),
        decision,
        config,
        backend_for=lambda tier: EarlyStopCheckBackend(),
        max_buffer_bytes=10_000,   # 10 KB cap
    )

    assert result.exhausted is True
    # The drain must have stopped well before exhausting all 100 chunks.
    # (At 1 KB per chunk, the cap of 10 KB should fire around chunk 10-11.)
    assert EarlyStopCheckBackend.chunks_yielded < 20, (
        f"drain did not stop at cap; yielded {EarlyStopCheckBackend.chunks_yielded} chunks"
    )


def test_response_within_cap_is_served_normally():
    """A response UNDER the cap must be served normally (no overflow)."""
    config = make_config("local")
    decision = RoutingDecision(tiers=("local",), work_class="chat")

    result = route_with_fallback(
        make_request(),
        decision,
        config,
        backend_for=lambda tier: ChunkyBackend(chunk_size=50, num_chunks=1),
        max_buffer_bytes=100,  # 50 bytes < 100 cap → no overflow
    )

    assert result.served_tier == "local"
    assert result.exhausted is False
    assert result.record.attempts[0].outcome == "served"


def test_none_max_buffer_bytes_disables_cap():
    """max_buffer_bytes=None means no cap (backward-compatible with old default)."""
    config = make_config("local")
    decision = RoutingDecision(tiers=("local",), work_class="chat")

    # Would overflow with any finite cap, but cap is disabled.
    result = route_with_fallback(
        make_request(),
        decision,
        config,
        backend_for=lambda tier: ChunkyBackend(chunk_size=1000, num_chunks=100),
        max_buffer_bytes=None,
    )

    assert result.served_tier == "local"
    assert result.exhausted is False


# --------------------------------------------------------------------------- #
# E — Circuit breaker: opens after threshold consecutive failures (#52)
# --------------------------------------------------------------------------- #
def test_circuit_breaker_opens_after_threshold_across_requests():
    """CircuitBreaker opens after circuit_threshold failures across requests.

    The breaker is session-scoped (shared across calls), so failures accumulate
    and the circuit stays open on the next call.
    """
    config = make_config("local")
    decision = RoutingDecision(tiers=("local",), work_class="chat")
    budget = Budget(max_attempts=10, circuit_threshold=2)
    cb = CircuitBreaker(cooldown=9999.0)  # long cooldown so it stays open during test

    # Call 1: fails → 1 consecutive failure.
    r1 = route_with_fallback(
        make_request(), decision, config,
        backend_for=lambda tier: FAILING,
        budget=budget, breaker=cb,
    )
    assert r1.record.attempts[0].outcome == "fallback"
    assert cb.failure_count("local") == 1

    # Call 2: fails → 2 consecutive failures; now at threshold.
    r2 = route_with_fallback(
        make_request(), decision, config,
        backend_for=lambda tier: FAILING,
        budget=budget, breaker=cb,
    )
    assert r2.record.attempts[0].outcome == "fallback"
    assert cb.failure_count("local") == 2

    # Call 3: circuit is now OPEN → tier is skipped.
    r3 = route_with_fallback(
        make_request(), decision, config,
        backend_for=lambda tier: FAILING,
        budget=budget, breaker=cb,
    )
    assert r3.record.attempts[0].outcome == "skipped-circuit"
    assert r3.exhausted is True
    # A skip does not add another failure.
    assert cb.failure_count("local") == 2


# --------------------------------------------------------------------------- #
# F — Circuit breaker: cooldown + half-open + self-heal (#52)
# --------------------------------------------------------------------------- #
def test_circuit_breaker_self_heals_after_cooldown():
    """After the cooldown, the circuit half-opens and a success closes it.

    With a zero-second cooldown, the circuit half-opens immediately after
    being opened; a successful probe closes it (resets failure_count to 0).
    """
    config = make_config("local")
    decision = RoutingDecision(tiers=("local",), work_class="chat")
    budget = Budget(max_attempts=10, circuit_threshold=2)
    cb = CircuitBreaker(cooldown=0.0)  # expire immediately for test speed

    # Drive to OPEN state with 2 failures.
    for _ in range(2):
        route_with_fallback(
            make_request(), decision, config,
            backend_for=lambda tier: FAILING,
            budget=budget, breaker=cb,
        )
    assert cb.failure_count("local") == 2

    # With cooldown=0, the circuit expires immediately.  Next call: the
    # CircuitBreaker grants a half-open probe (is_open returns False once).
    # Use a PASSING backend so the probe succeeds → circuit CLOSES.
    r_probe = route_with_fallback(
        make_request(), decision, config,
        backend_for=lambda tier: PASSING,
        budget=budget, breaker=cb,
    )

    assert r_probe.served_tier == "local"
    assert r_probe.exhausted is False
    # Success → circuit closed (failure_count reset to 0).
    assert cb.failure_count("local") == 0


def test_circuit_breaker_reopens_on_failed_probe():
    """A failed half-open probe re-opens the circuit (resets the cooldown timer)."""
    config = make_config("local")
    decision = RoutingDecision(tiers=("local",), work_class="chat")
    budget = Budget(max_attempts=10, circuit_threshold=2)
    cb = CircuitBreaker(cooldown=0.0)

    # Drive to OPEN.
    for _ in range(2):
        route_with_fallback(
            make_request(), decision, config,
            backend_for=lambda tier: FAILING,
            budget=budget, breaker=cb,
        )
    assert cb.failure_count("local") == 2

    # Probe fails → re-open (failure_count increments to 3).
    r_failed_probe = route_with_fallback(
        make_request(), decision, config,
        backend_for=lambda tier: FAILING,
        budget=budget, breaker=cb,
    )

    # The probe was allowed through (not "skipped-circuit") but it failed.
    assert r_failed_probe.record.attempts[0].outcome == "fallback", (
        "half-open probe attempt must appear as 'fallback', not 'skipped-circuit'"
    )
    assert cb.failure_count("local") == 3  # incremented again


# --------------------------------------------------------------------------- #
# G — Thread-safety smoke test (#52)
# --------------------------------------------------------------------------- #
def test_circuit_breaker_concurrent_access_does_not_corrupt_state():
    """Concurrent record_failure + record_success calls must not corrupt the state.

    Spawns N threads, each doing M alternating failure/success calls, then
    asserts no exception escaped and the final count is consistent.
    """
    cb = CircuitBreaker()
    errors: List[BaseException] = []
    num_threads = 10
    ops_per_thread = 100

    def worker():
        try:
            for i in range(ops_per_thread):
                if i % 2 == 0:
                    cb.record_failure("t1")
                else:
                    cb.record_success("t1")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # No exceptions should have escaped.
    assert not errors, f"thread errors: {errors}"
    # State must be self-consistent: count is 0 (success) or > 0 (failure).
    count = cb.failure_count("t1")
    assert count >= 0, f"negative failure count: {count}"


def test_session_breaker_accumulates_across_multiple_route_calls():
    """The CircuitBreaker accumulates state across separate route_with_fallback calls
    (proving it is genuinely session-scoped, unlike per-call dicts).
    """
    config = make_config("local", make_tier("cloud", "cloud"))
    decision = RoutingDecision(tiers=("local", "cloud"), work_class="chat")
    budget = Budget(max_attempts=5, circuit_threshold=2)
    cb = CircuitBreaker(cooldown=9999.0)  # won't decay during test
    backends = {
        "local": FAILING,
        "cloud": PASSING,
    }

    # First two calls: local fails each time → breaker count reaches threshold.
    for _ in range(2):
        route_with_fallback(
            make_request(), decision, config,
            backend_for=lambda tier: backends[tier.id],
            budget=budget, breaker=cb,
        )
    assert cb.failure_count("local") == 2

    # Third call: local is now skipped (circuit open); cloud serves immediately.
    r = route_with_fallback(
        make_request(), decision, config,
        backend_for=lambda tier: backends[tier.id],
        budget=budget, breaker=cb,
    )

    local_attempt = r.record.attempts[0]
    assert local_attempt.tier_id == "local"
    assert local_attempt.outcome == "skipped-circuit"
    assert r.served_tier == "cloud"


# --------------------------------------------------------------------------- #
# regression: existing dict breaker still works (backward compat)
# --------------------------------------------------------------------------- #
def test_dict_breaker_still_works_after_circuit_breaker_addition():
    """Callers that pass a plain dict breaker (existing API) must still work."""
    config = make_config("local")
    decision = RoutingDecision(tiers=("local",), work_class="chat")
    budget = Budget(max_attempts=10, circuit_threshold=2)
    breaker: dict = {}

    # Two fails to trip the circuit.
    for _ in range(2):
        route_with_fallback(
            make_request(), decision, config,
            backend_for=lambda tier: FAILING,
            budget=budget, breaker=breaker,
        )
    assert breaker.get("local", 0) == 2

    # Third call: skipped.
    r = route_with_fallback(
        make_request(), decision, config,
        backend_for=lambda tier: FAILING,
        budget=budget, breaker=breaker,
    )
    assert r.record.attempts[0].outcome == "skipped-circuit"
