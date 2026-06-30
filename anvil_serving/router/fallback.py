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
  skipped (no backend call) until the cooldown expires. Use a session-scoped
  :class:`CircuitBreaker` (passed as ``breaker``) so the state persists across
  requests with correct half-open probe-and-heal behaviour; absent one, a
  per-call dict still bounds the single call via the retry cap.
* **Per-session token budget** (``Budget.max_total_tokens``) — escalation STOPS
  once attempting the next tier would reach the ceiling.

Robustness contract (seam isolation, #45):

* A backend that raises is a failed *attempt*, never a propagated exception.
* A response that exceeds ``max_buffer_bytes`` is treated as a verify-failure
  (overflow) and escalates; the buffer never grows unbounded.
* A verifier that *hangs* is treated as a verify-failure after ``verifier_timeout``
  seconds (daemon-thread guard); it does not hang the request.
* A raising ``response_view_factory`` falls back to the default
  :func:`~anvil_serving.router.commit_window.build_response_view` instead of
  crashing after the backend served.
* A raising observer/log seam (``log.record``) is swallowed and printed to stderr;
  it cannot crash a served response.

An empty candidate list yields an exhausted result, not a crash. The
buffer/verify/commit *streaming* guarantee (no partial local tokens to the
harness) lives in the T008 commit window; this module is the buffer-then-decide
control flow over multiple tiers.

Dependency injection: the tier -> backend mapping is supplied by the caller as
``backend_for`` — this module never hard-wires a tier id to a backend class.

Stdlib-only; frozen-dataclass house style.

Note (policy integration): the ordered candidate tiers are produced by the
routing policy (``policy.route`` -> ``policy.RoutingDecision``, T005).
:func:`route_with_fallback` is intentionally **duck-typed** on its ``decision``:
it reads only ``.tiers`` and ``.work_class``, so a real ``policy.RoutingDecision``
drops in directly (covered by ``test_policy_decision_integration``). The minimal
:class:`RoutingDecision` defined here is the documented consumer contract / a
lightweight stand-in for unit tests; it is NOT a competing policy type.
"""
from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple, Union

from .config import RouterConfig, Tier
from .decision_log import AttemptRecord, DecisionLog, DecisionRecord, compute_cost_usd
from .internal import Backend, InternalRequest, estimate_tokens
from .commit_window import build_response_view
from .verify import VerifyResult, Verifier, default_verifiers, run_verifiers


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
    * ``max_attempts`` — retry cap: the maximum number of REAL backend attempts
      per request (circuit skips and config misses do not count; the finite
      candidate list bounds the loop regardless).
    * ``circuit_threshold`` — consecutive per-tier failures, within the session,
      that open a tier's circuit (skipping it thereafter). When used with a
      session-scoped :class:`CircuitBreaker`, the breaker manages cooldown and
      half-open probing; a plain ``Dict[str, int]`` falls back to the simpler
      count-only behaviour.
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


# --------------------------------------------------------------------------- #
# session-scoped circuit breaker (#52)
# --------------------------------------------------------------------------- #
class CircuitBreaker:
    """Thread-safe per-tier circuit breaker with cooldown and half-open probe.

    Owned and constructed once by :class:`~anvil_serving.router.serve.RoutingBackend`;
    its lifetime spans the entire server session. Thread-safe: all mutable state
    is protected by a single :class:`threading.Lock`.

    Per-tier state machine::

        CLOSED     failures < threshold → normal, no skip
        OPEN       failures >= threshold AND now < last_fail + cooldown → skip
        HALF-OPEN  failures >= threshold AND now >= last_fail + cooldown
                   → grant ONE probe (first thread); others see OPEN

    Probe outcomes:

    * **Success** → CLOSED (failures reset to 0, cooldown timer cleared).
    * **Failure** → OPEN (re-open: last_fail = now, cooldown restarts).

    A transient blip that hits ``circuit_threshold`` on two consecutive requests
    therefore cannot permanently disable a tier: after ``cooldown`` seconds the
    circuit half-opens, the probe succeeds, and the tier is back in rotation.
    """

    #: Default cooldown in seconds between the last failure and the half-open probe.
    DEFAULT_COOLDOWN: float = 60.0

    def __init__(self, cooldown: float = DEFAULT_COOLDOWN) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._failures: Dict[str, int] = {}       # consecutive failure count
        self._last_fail: Dict[str, float] = {}    # monotonic time of last failure
        self._probing: Set[str] = set()           # tiers in a half-open probe
        self.cooldown: float = cooldown

    def is_open(self, tier_id: str, threshold: int) -> bool:
        """Return True if the circuit for *tier_id* is OPEN (should be skipped).

        Side effect when the cooldown has expired and no probe is in flight: the
        calling thread is granted the half-open probe (the tier is marked as
        probing and the method returns *False* so the caller attempts the tier).
        All concurrent callers see *True* (OPEN) while a probe is in flight.
        """
        with self._lock:
            if self._failures.get(tier_id, 0) < threshold:
                return False  # CLOSED — no skip
            last_fail = self._last_fail.get(tier_id, 0.0)
            if time.monotonic() < last_fail + self.cooldown:
                return True  # OPEN — still in cooldown, skip
            # Cooldown expired → half-open window.
            if tier_id in self._probing:
                # A probe is already in flight from another thread; keep others out.
                return True
            # Grant the probe to THIS thread.
            self._probing.add(tier_id)
            return False  # HALF-OPEN — allow the probe through

    def record_failure(self, tier_id: str) -> None:
        """Record a consecutive failure, (re-)opening the circuit."""
        with self._lock:
            self._failures[tier_id] = self._failures.get(tier_id, 0) + 1
            self._last_fail[tier_id] = time.monotonic()
            self._probing.discard(tier_id)

    def record_success(self, tier_id: str) -> None:
        """Record a success, closing the circuit (reset all state for this tier)."""
        with self._lock:
            self._failures[tier_id] = 0
            self._last_fail.pop(tier_id, None)
            self._probing.discard(tier_id)

    def failure_count(self, tier_id: str) -> int:
        """Return the current consecutive failure count (for logging/inspection)."""
        with self._lock:
            return self._failures.get(tier_id, 0)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

#: Default byte cap for draining a tier's response into memory (32 MiB).
_DEFAULT_MAX_BUFFER_BYTES: int = 32 * 1024 * 1024


def _cap_drain(
    gen: Iterator[str],
    max_buffer_bytes: Optional[int],
) -> Tuple[List[str], bool, Optional[BaseException]]:
    """Drain *gen* into a list, optionally capping total UTF-8 byte size.

    Returns ``(deltas, overflowed, error)``.  ``overflowed`` is *True* iff the
    cap was exceeded (draining stops early on overflow).  ``error`` is the
    exception the generator raised mid-stream, or *None* on a clean exhaust.
    A mid-stream raise (e.g. an OOM-killed scheduler — repo gotcha #1) is
    captured here and returned for the caller to fail safe; the partial buffer
    is returned but should be discarded, not served.  The generator is always
    closed (best-effort) on early exit so backends can release resources.
    """
    deltas: List[str] = []
    total = 0
    overflowed = False
    error: Optional[BaseException] = None
    try:
        for delta in gen:
            deltas.append(delta)
            if max_buffer_bytes is not None:
                total += len(delta.encode("utf-8", "surrogatepass"))
                if total > max_buffer_bytes:
                    overflowed = True
                    break
    except Exception as exc:  # noqa: BLE001 - backend mid-stream fault captured, not propagated
        error = exc
    finally:
        # Best-effort close so the backend gets a chance to clean up after an
        # early break (cap exceeded or mid-stream error). close() raising must
        # not mask the real outcome.
        _close = getattr(gen, "close", None)
        if callable(_close):
            try:
                _close()
            except Exception:  # noqa: BLE001 - cleanup must never mask the drain result
                pass
    return deltas, overflowed, error


def _run_verifiers_timed(
    view: object,
    verifiers: Sequence[Verifier],
    mode: str,
    timeout: float,
) -> List[VerifyResult]:
    """Run the verifier chain in a daemon thread; return a failure result on timeout.

    A verifier that hangs (blocks indefinitely) will never escape this function
    after ``timeout`` seconds — the thread is abandoned as a daemon (it cannot
    block process exit) and a synthetic ``VerifyResult(passed=False)`` is
    returned so the fallback walk treats the hung verifier as a verify-failure
    and escalates rather than hanging the request.

    ``run_verifiers`` already backstops any exception a verifier raises into a
    fail verdict; this function adds the latency-budget guard on top.
    """
    results: List[VerifyResult] = []

    def _target() -> None:
        results.extend(run_verifiers(view, verifiers, mode=mode))  # type: ignore[arg-type]

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        # Thread is still running (verifier hung). Abandon it — daemon threads
        # do not block process exit. Return a synthetic failing verdict.
        return [VerifyResult(
            verifier="verifier_timeout",
            passed=False,
            score=0.0,
            reason=(
                f"verifier chain did not complete within {timeout}s budget; "
                "treated as verify-fail"
            ),
        )]
    return results


def _safe_passed(result: object) -> bool:
    """Return the ``passed`` flag, or ``False`` for a non-:class:`VerifyResult`.

    Seam-isolation contract: a verifier that RETURNS a non-VerifyResult (rather
    than raising) is treated as a verify FAILURE, not a crash.  Guards
    :func:`route_with_fallback` against ``AttributeError: 'NoneType' object has
    no attribute 'passed'`` when an injected verifier misbehaves.
    """
    try:
        return bool(result.passed)  # type: ignore[union-attr]
    except AttributeError:
        return False


def _first_failing_reason(results: Sequence) -> str:
    """Name of the first hard-failing verifier (for the audit trail).

    R012 secrets hygiene: record only the verifier's stable NAME, never its
    ``reason`` string — T007 reasons can echo response content (a malformed diff
    line, a tool name/argument, a parse error quoting the model output), which
    must never land in the metadata-only decision log.
    """
    for r in results:
        try:
            if not r.passed:
                return getattr(r, "verifier", "non-VerifyResult")
        except AttributeError:
            return "non-VerifyResult from verifier"
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
    breaker: Optional[Union[Dict[str, int], CircuitBreaker]] = None,
    response_view_factory: Optional[Callable[[Sequence[str], InternalRequest], object]] = None,
    verifier_timeout: Optional[float] = None,
    max_buffer_bytes: Optional[int] = _DEFAULT_MAX_BUFFER_BYTES,
) -> FallbackResult:
    """Walk ``decision.tiers`` in order, serving the first tier that verifies.

    For each candidate tier, in order:

    1. **Retry cap** — stop if the configured ``max_attempts`` is already spent.
    2. **Budget** — if attempting this tier would reach ``max_total_tokens``,
       record a ``budget-stop`` and STOP (no further tier is tried).
    3. **Circuit** — if the tier's circuit is open
       (``breaker[tier] >= circuit_threshold`` for a dict, or
       ``CircuitBreaker.is_open(tier, threshold)`` for the session-scoped
       breaker), record ``skipped-circuit`` and move on. A skip is not a real
       attempt and does not consume the retry cap, so an open local circuit never
       starves a healthy downstream (cloud) tier.
    4. **Attempt** — call ``backend_for(config.tier(tier))`` and drain its
       deltas with an optional byte cap (``max_buffer_bytes``). A raising backend
       is a failed attempt (``error``). An overflow is a failed attempt
       (``overflow``). Assemble a ``ResponseView`` and run the verifiers (with an
       optional ``verifier_timeout`` daemon-thread guard so a hung verifier is
       treated as a fail, not an infinite hang):
       PASS -> serve (reset the tier's breaker, return); FAIL -> record
       ``fallback``, bump the breaker, escalate.

    **Seam isolation:** a raising ``response_view_factory`` falls back to the
    default :func:`~anvil_serving.router.commit_window.build_response_view`
    (the response is already buffered — don't crash now). A raising
    observer/log seam (``log.record``) is swallowed and printed to stderr; it
    cannot crash a served response.

    ``backend_for`` is dependency-injected (tier -> backend); this function never
    hard-wires a tier to a backend class.

    ``breaker`` accepts either a caller-owned ``Dict[str, int]`` (simple count;
    no cooldown/half-open; per-call if ``None`` — still bounded by
    ``max_attempts``) or a session-scoped :class:`CircuitBreaker` (thread-safe,
    with cooldown + half-open probe/self-heal). Pass a :class:`CircuitBreaker`
    from :class:`~anvil_serving.router.serve.RoutingBackend` for production use.

    Returns a :class:`FallbackResult`; appends the :class:`DecisionRecord` to
    ``log`` when one is given. Never raises for a backend fault or an empty
    candidate list.
    """
    # An empty/None verifier sequence means "use the defaults" (matching the T008
    # commit window). A caller cannot accidentally disable the quality gate by
    # passing []: that would make all([]) == True and serve the first tier
    # unconditionally, voiding the verify-and-fallback guarantee (AC1).
    verifiers = list(verifiers) if verifiers else default_verifiers()
    budget = budget if budget is not None else Budget()
    # Normalise breaker: None -> per-call plain dict (still bounded by max_attempts).
    _raw_breaker: Union[Dict[str, int], CircuitBreaker]
    if breaker is None:
        _raw_breaker = {}
    else:
        _raw_breaker = breaker
    _use_cb = isinstance(_raw_breaker, CircuitBreaker)

    # The default response view is text-only (T008 build_response_view), so only
    # text-based verifiers (e.g. NonEmptyContent, CodeParses) fire; checks that
    # need finish_reason / tool_calls (NotTruncated, ToolCallJSONValid) are inert
    # until a caller injects a richer factory built from a backend that surfaces
    # those fields. The seam is here so that wiring is a drop-in, not a rewrite.
    make_view = response_view_factory if response_view_factory is not None else build_response_view
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

    # ---------------------------------------------------------------------- #
    # Breaker dispatch helpers — abstract over dict vs. CircuitBreaker.       #
    # ---------------------------------------------------------------------- #
    def _is_open(tid: str) -> bool:
        if _use_cb:
            return _raw_breaker.is_open(tid, budget.circuit_threshold)  # type: ignore[union-attr]
        return _raw_breaker.get(tid, 0) >= budget.circuit_threshold  # type: ignore[union-attr]

    def _failures_for_log(tid: str) -> int:
        if _use_cb:
            return _raw_breaker.failure_count(tid)  # type: ignore[union-attr]
        return _raw_breaker.get(tid, 0)  # type: ignore[union-attr]

    def _record_failure(tid: str) -> None:
        if _use_cb:
            _raw_breaker.record_failure(tid)  # type: ignore[union-attr]
        else:
            _raw_breaker[tid] = _raw_breaker.get(tid, 0) + 1  # type: ignore[index]

    def _record_success(tid: str) -> None:
        if _use_cb:
            _raw_breaker.record_success(tid)  # type: ignore[union-attr]
        else:
            _raw_breaker[tid] = 0  # type: ignore[index]

    # ---------------------------------------------------------------------- #
    # finalize: build + log the DecisionRecord                                #
    # ---------------------------------------------------------------------- #
    def finalize(served: Optional[str], text: str, exhausted: bool) -> FallbackResult:
        # Cost dimension (ADR-0001 / advise-and-defer:T003): estimate the $ cost of
        # the SERVED response from the served tier's cost fields. Pure arithmetic —
        # never blocks the hot path. 0.0 when nothing served (exhausted) or when the
        # served tier carries no cost fields (e.g. all local tiers). Resolving the
        # tier is wrapped defensively so a cost-estimation hiccup can never break a
        # served response (the served id always resolves in practice — it was used
        # to build the backend — but cost is best-effort, not load-bearing).
        cost_usd = 0.0
        if served is not None:
            try:
                served_tier = config.tier(served)
                cost_usd = compute_cost_usd(served_tier, total_prompt, total_completion)
            except Exception:  # noqa: BLE001 - cost is best-effort, never fatal
                cost_usd = 0.0
        record = DecisionRecord(
            work_class=work_class,
            requested_tiers=requested_tiers,
            attempts=tuple(attempts),
            served_tier=served,
            total_prompt_tokens=total_prompt,
            total_completion_tokens=total_completion,
            fell_back=any(a.outcome in ("fallback", "error", "overflow") for a in attempts),
            cost_usd=cost_usd,
        )
        # Seam isolation: a raising observer/log must not crash a served response.
        if log is not None:
            try:
                log.record(record)
            except Exception as _log_exc:  # noqa: BLE001 - observer fault must not escape
                print(
                    f"[anvil-serving] decision log raised "
                    f"({type(_log_exc).__name__}); result still served",
                    file=sys.stderr,
                    flush=True,
                )
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

        # 3. Circuit breaker — skip a tier whose circuit is open. A skip is NOT a
        #    real attempt: it does NOT consume the retry cap (the finite candidate
        #    list already bounds the loop), so open LOCAL circuits never starve a
        #    healthy downstream tier (e.g. cloud) out of the budget.
        if _is_open(tier_id):
            f_log = _failures_for_log(tier_id)
            attempts.append(
                AttemptRecord(
                    tier_id=tier_id,
                    verifier_passed=False,
                    verify_reason=f"circuit open ({f_log} consecutive failures)",
                    prompt_tokens=0,
                    completion_tokens=0,
                    outcome="skipped-circuit",
                )
            )
            continue

        # 4a. Resolve the tier. An unknown tier id is a CONFIG miss, not a backend
        #     fault: record it (no token charge, no breaker bump, does not consume
        #     the retry cap) and move on — do not misattribute it as an "error".
        try:
            tier = config.tier(tier_id)
        except Exception:  # noqa: BLE001 - unknown / invalid tier id
            attempts.append(
                AttemptRecord(
                    tier_id=tier_id,
                    verifier_passed=False,
                    verify_reason="unknown tier id (not in config)",
                    prompt_tokens=0,
                    completion_tokens=0,
                    outcome="unknown-tier",
                )
            )
            continue

        # 4b. Attempt the tier. A raising backend (eager OR mid-stream) is a
        #     failed attempt, never a propagated exception.
        #     * Eager faults (backend_for() raises, generate() raises before
        #       yielding) are caught by the outer try/except.
        #     * Mid-stream faults and byte-cap overflow are returned by _cap_drain
        #       as (deltas, overflowed, error) and handled afterwards.
        #     Record only the exception TYPE — its message may echo
        #     prompt/response content (R012).
        try:
            backend = backend_for(tier)
            gen = iter(backend.generate(request))
            deltas, overflowed, drain_exc = _cap_drain(gen, max_buffer_bytes)
        except Exception as exc:  # noqa: BLE001 - eager backend fault must not escape
            _record_failure(tier_id)
            total_prompt += prompt_tokens
            running_total += prompt_tokens
            attempts.append(
                AttemptRecord(
                    tier_id=tier_id,
                    verifier_passed=False,
                    verify_reason=f"backend error: {type(exc).__name__}",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=0,
                    outcome="error",
                )
            )
            attempt_count += 1
            continue

        # Mid-stream backend error (e.g. OOM-killed scheduler — repo gotcha #1).
        if drain_exc is not None:
            _record_failure(tier_id)
            total_prompt += prompt_tokens
            running_total += prompt_tokens
            attempts.append(
                AttemptRecord(
                    tier_id=tier_id,
                    verifier_passed=False,
                    verify_reason=f"backend error: {type(drain_exc).__name__}",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=0,
                    outcome="error",
                )
            )
            attempt_count += 1
            continue

        # Drain completed (possibly overflowed). Compute text + tokens for all
        # non-error paths so the audit trail has accurate counts.
        text = "".join(deltas)
        completion_tokens = estimate_tokens([text])
        running_total += prompt_tokens + completion_tokens
        total_prompt += prompt_tokens
        total_completion += completion_tokens
        last_text = text

        # Byte-cap overflow: treat as a verify-failure (we cannot cheaply prove
        # an oversized partial response is correct, and we must not buffer
        # unbounded).
        if overflowed:
            _record_failure(tier_id)
            attempts.append(
                AttemptRecord(
                    tier_id=tier_id,
                    verifier_passed=False,
                    verify_reason=f"response exceeded max_buffer_bytes={max_buffer_bytes}",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    outcome="overflow",
                )
            )
            attempt_count += 1
            continue

        # Assemble a ResponseView.
        # Seam isolation: a raising response_view_factory must not crash the
        # request after the backend already served — fall back to the default
        # text-only view.
        try:
            view = make_view(deltas, request)
        except Exception as exc:  # noqa: BLE001 - factory fault falls back, never crashes
            print(
                f"[anvil-serving] response_view_factory raised "
                f"({type(exc).__name__}); falling back to default build_response_view",
                file=sys.stderr,
                flush=True,
            )
            view = build_response_view(deltas, request)

        # Run verifiers. If verifier_timeout is set, a hung verifier is treated
        # as a verify-failure after the budget (daemon-thread guard).
        if verifier_timeout is not None:
            results = _run_verifiers_timed(view, verifiers, "all", verifier_timeout)
        else:
            results = run_verifiers(view, verifiers, mode="all")
        passed = all(_safe_passed(r) for r in results)

        if passed:
            _record_success(tier_id)  # clean run resets the consecutive-failure count
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
        _record_failure(tier_id)
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
