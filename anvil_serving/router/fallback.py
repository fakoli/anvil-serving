"""Tier fallback with thrash + budget guards and decision logging (T009).

This is the control-plane that turns a policy's *ordered candidate tiers* into a
*served* response. It walks the candidates in order, and for each one: serves the
request, assembles the streamed deltas into a
:class:`~anvil_serving.router.verify.ResponseView`, and runs the cheap structural
verifier chain (T007). On a PASS it serves; on a FAIL (or a backend that raises)
it escalates to the next tier. Every step is recorded in a
:class:`~anvil_serving.router.decision_log.DecisionRecord` with per-tier token
accounting.

Escalation is *bounded* from three directions so a sustained failure can never
thrash or overspend (PRD AC2):

* **Retry cap** (``Budget.max_attempts``) — at most N real attempts per request.
* **Circuit breaker** (``Budget.circuit_threshold``) — a tier that fails this
  many times *consecutively within the session* has its circuit opened and is
  skipped (no backend call) until something resets it. The breaker state is a
  shared ``dict[tier_id -> consecutive_failures]`` the caller threads across
  requests; absent one, a per-call dict still bounds the single call via the
  retry cap.
* **Per-session token budget** (``Budget.max_total_tokens``) — escalation STOPS
  once attempting the next tier would reach the ceiling. No further tier is
  tried; the running total never overruns by more than the one in-flight attempt
  already counted.

Robustness contract: a backend that raises is a failed *attempt*, never a
propagated exception; an empty candidate list yields an exhausted result, not a
crash. The buffer/verify/commit *streaming* guarantee (no partial local tokens
to the harness) lives in the T008 commit window; this module is the
buffer-then-decide control flow over multiple tiers.

Dependency injection: the tier -> backend mapping is supplied by the caller as
``backend_for`` — this module never hard-wires a tier id to a backend class.

Stdlib-only; frozen-dataclass house style.

Note (scope): the ordered candidate tiers are produced by the routing policy
(its ``RoutingDecision``). That module is not present in this checkout, so a
minimal, duck-type-compatible :class:`RoutingDecision` (carrying ``tiers`` and
``work_class``) is defined here; :func:`route_with_fallback` only reads those two
attributes, so a richer policy decision drops in unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .config import RouterConfig, Tier
from .decision_log import AttemptRecord, DecisionLog, DecisionRecord
from .internal import Backend, InternalRequest, estimate_tokens
from .commit_window import build_response_view
from .verify import Verifier, default_verifiers, run_verifiers


# --------------------------------------------------------------------------- #
# inputs / outputs
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RoutingDecision:
    """The ordered candidate tiers a routing policy selected for a request.

    Minimal, duck-type-compatible stand-in for the policy layer's decision:
    :func:`route_with_fallback` consumes only ``tiers`` (the ORDERED candidate
    tier ids) and ``work_class`` (the profile key, recorded in the audit trail).
    A real policy ``RoutingDecision`` carrying the same two attributes is
    accepted in its place.
    """

    tiers: Tuple[str, ...]
    work_class: Optional[str] = None


@dataclass(frozen=True)
class Budget:
    """Per-session escalation guards.

    * ``max_total_tokens`` — ceiling on (prompt + completion) tokens accounted
      across the request's attempts; ``0`` means unlimited. Escalation stops
      before the attempt that would reach it.
    * ``max_attempts`` — retry cap: the maximum number of candidate tiers the
      loop will consume (real attempts *and* circuit skips both count, so the
      loop always terminates).
    * ``circuit_threshold`` — consecutive per-tier failures, within the session,
      that open a tier's circuit (skipping it thereafter).
    """

    max_total_tokens: int = 0
    max_attempts: int = 3
    circuit_threshold: int = 2


@dataclass(frozen=True)
class FallbackResult:
    """Outcome of a fallback walk.

    ``served_tier`` is the tier that produced the served response (``None`` if
    none did). ``text`` is the served completion (or the last attempt's text when
    exhausted). ``record`` is the full audit trail. ``exhausted`` is True when no
    tier served — every candidate failed, or the retry cap / circuit / budget
    stopped escalation first.
    """

    served_tier: Optional[str]
    text: str
    record: DecisionRecord
    exhausted: bool


def _first_failing_reason(results: Sequence) -> str:
    """The reason of the first hard-failing verifier (for the audit trail)."""
    for r in results:
        if not r.passed:
            return f"{r.verifier}: {r.reason}"
    return "verify failed"


def route_with_fallback(
    request: InternalRequest,
    decision: RoutingDecision,
    config: RouterConfig,
    backend_for: Callable[[Tier], Backend],
    *,
    verifiers: Optional[Sequence[Verifier]] = None,
    budget: Optional[Budget] = None,
    log: Optional[DecisionLog] = None,
    breaker: Optional[Dict[str, int]] = None,
) -> FallbackResult:
    """Walk ``decision.tiers`` in order, serving the first tier that verifies.

    For each candidate tier, in order:

    1. **Retry cap** — stop if the configured ``max_attempts`` is already spent.
    2. **Budget** — if attempting this tier would reach ``max_total_tokens``,
       record a ``budget-stop`` and STOP (no further tier is tried).
    3. **Circuit** — if the tier's circuit is open
       (``breaker[tier] >= circuit_threshold``), record ``skipped-circuit`` and
       move on (this still counts against the retry cap so the loop terminates).
    4. **Attempt** — call ``backend_for(config.tier(tier))`` and drain its
       deltas. A raising backend is a failed attempt (``error``), never a
       propagated exception. Assemble a ``ResponseView`` and run the verifiers:
       PASS -> serve (reset the tier's breaker, return); FAIL -> record
       ``fallback``, bump the breaker, escalate.

    ``backend_for`` is dependency-injected (tier -> backend); this function never
    hard-wires a tier to a backend class. ``breaker`` is the shared per-session
    circuit state; if ``None`` a per-call dict is used (a single call is still
    bounded by ``max_attempts``). Returns a :class:`FallbackResult`; appends the
    :class:`DecisionRecord` to ``log`` when one is given. Never raises for a
    backend fault or an empty candidate list.
    """
    verifiers = list(verifiers) if verifiers is not None else default_verifiers()
    budget = budget if budget is not None else Budget()
    breaker = breaker if breaker is not None else {}
    work_class = getattr(decision, "work_class", None)
    requested_tiers: Tuple[str, ...] = tuple(getattr(decision, "tiers", ()) or ())

    # Prompt-token cost is the same for every candidate (the same request is
    # replayed), so estimate it once. Counts only metadata-free integers.
    prompt_tokens = estimate_tokens(
        [request.system or ""] + [m.content for m in request.messages]
    )

    attempts: List[AttemptRecord] = []
    running_total = 0
    total_prompt = 0
    total_completion = 0
    last_text = ""
    attempt_count = 0

    def finalize(served: Optional[str], text: str, exhausted: bool) -> FallbackResult:
        record = DecisionRecord(
            work_class=work_class,
            requested_tiers=requested_tiers,
            attempts=tuple(attempts),
            served_tier=served,
            total_prompt_tokens=total_prompt,
            total_completion_tokens=total_completion,
            fell_back=any(a.outcome == "fallback" for a in attempts),
        )
        if log is not None:
            log.record(record)
        return FallbackResult(served_tier=served, text=text, record=record, exhausted=exhausted)

    for tier_id in requested_tiers:
        # 1. Retry cap — bound total escalation work.
        if attempt_count >= budget.max_attempts:
            break

        # 2. Budget ceiling — stop before the attempt that would reach it. The
        #    running total never overruns by more than the in-flight attempt
        #    already counted.
        if budget.max_total_tokens and (running_total + prompt_tokens) >= budget.max_total_tokens:
            attempts.append(
                AttemptRecord(
                    tier_id=tier_id,
                    verifier_passed=False,
                    verify_reason="per-session token budget would be exceeded",
                    prompt_tokens=0,
                    completion_tokens=0,
                    outcome="budget-stop",
                    detail=(
                        f"running_total={running_total}, next_prompt={prompt_tokens}, "
                        f"ceiling={budget.max_total_tokens}"
                    ),
                )
            )
            break

        # 3. Circuit breaker — skip a tier whose circuit is open. Counts against
        #    the retry cap so a degenerate config still terminates the loop.
        failures = breaker.get(tier_id, 0)
        if failures >= budget.circuit_threshold:
            attempts.append(
                AttemptRecord(
                    tier_id=tier_id,
                    verifier_passed=False,
                    verify_reason=f"circuit open ({failures} consecutive failures)",
                    prompt_tokens=0,
                    completion_tokens=0,
                    outcome="skipped-circuit",
                )
            )
            attempt_count += 1
            continue

        # 4. Attempt the tier. A raising backend (eager OR mid-stream) is a
        #    failed attempt, never a propagated exception.
        try:
            backend = backend_for(config.tier(tier_id))
            deltas = list(backend.generate(request))
        except Exception as exc:  # noqa: BLE001 - backend fault must not escape
            breaker[tier_id] = breaker.get(tier_id, 0) + 1
            total_prompt += prompt_tokens
            running_total += prompt_tokens
            attempts.append(
                AttemptRecord(
                    tier_id=tier_id,
                    verifier_passed=False,
                    verify_reason=f"backend error: {type(exc).__name__}: {exc}",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=0,
                    outcome="error",
                )
            )
            attempt_count += 1
            continue

        text = "".join(deltas)
        completion_tokens = estimate_tokens([text])
        running_total += prompt_tokens + completion_tokens
        total_prompt += prompt_tokens
        total_completion += completion_tokens
        last_text = text

        view = build_response_view(deltas, request)
        results = run_verifiers(view, verifiers, mode="all")
        passed = all(r.passed for r in results)

        if passed:
            breaker[tier_id] = 0  # clean run resets the consecutive-failure count
            attempts.append(
                AttemptRecord(
                    tier_id=tier_id,
                    verifier_passed=True,
                    verify_reason="verify passed",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    outcome="served",
                )
            )
            return finalize(tier_id, text, exhausted=False)

        # FAIL: record the discard, bump the breaker, escalate.
        breaker[tier_id] = breaker.get(tier_id, 0) + 1
        attempts.append(
            AttemptRecord(
                tier_id=tier_id,
                verifier_passed=False,
                verify_reason=_first_failing_reason(results),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                outcome="fallback",
            )
        )
        attempt_count += 1

    # No tier served — exhausted (all failed, or a guard stopped escalation).
    return finalize(None, last_text, exhausted=True)
