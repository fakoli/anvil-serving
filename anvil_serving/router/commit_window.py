"""Streaming commit-window for fail-prone work-classes (T008).

The data-plane safety mechanism behind the quality-gated router. For work
classes the router has profiled as *fail-prone* (the ``allow-with-verify``
tier), a local model's answer is not trustworthy enough to stream straight to
the coding harness: a structurally-broken local answer (empty/truncated content,
tool-call JSON that does not validate, code that does not parse — the failures
T007's verifiers catch) must never reach the harness as partial output that then
has to be yanked mid-stream. So for those classes we open a **commit window**:

1. **Buffer** the entire local response into memory *before any byte is
   delivered* (drain ``local_backend.generate(request)`` into a list).
2. **Verify** — assemble a :class:`~anvil_serving.router.verify.ResponseView`
   from the buffer and run the verifier chain (T007).
3. **Commit or fall back:**
   * verify **PASS** → commit: replay the buffered local deltas to the harness.
   * verify **FAIL** → discard the buffer *in its entirety* (emit none of it) and
     stream the injected **fallback** (cloud) backend instead.

The hard guarantee this module exists to provide:

    On a local verify-failure, ZERO partial local tokens reach the harness.

It is structurally impossible to violate that here. In the fail-prone branch the
local stream is fully materialized into a ``list`` (see :func:`_drain`) and the
verify/commit decision is taken **before the generator reaches its first
``yield``**. There is no path that yields a buffered local delta and *then*
decides to fall back: the decision strictly precedes any delivery, and the
fallback branch never references the buffer. See
``tests/router/test_commit_window.py`` (``test_first_yielded_token_*`` and the
no-partial-local cases) for the adversarial pins.

The window also has to shield the harness from seams that *raise*, not only from
a verify-failing answer. In the fail-prone path every seam that can throw — the
local backend (mid-generation: OOM-kill, "scheduler died", reset connection — the
repo's gotcha #1), the ``response_view_factory``, the verifiers (T007 already
backstops these), and the ``on_fallback`` hook — fails SAFE: discard any buffer
and fall back to cloud (or, for the hook, swallow and continue), never propagate
a 500. The no-partial-local-tokens guarantee holds on every error path. A
*hanging* backend stays out of scope (it needs a thread/async to interrupt and
belongs to the serve-path wiring, T009/T012); a synchronously *raising* backend
is caught here.

Work classes NOT flagged fail-prone are streamed straight through — pure
passthrough, no buffer, no verify — preserving low time-to-first-token. The
commit window is applied ONLY to fail-prone classes.

TTFT / latency budget
---------------------
The commit window trades **first-token latency for the safety guarantee**: a
fail-prone response is fully generated *and* verified before its first byte is
delivered, so its TTFT becomes ``(local generation time + verify time)`` instead
of ``(time to first local token)``. Verify is cheap and purely local (T007 does
no I/O), so the dominant added cost is buffering the whole local completion.
That is the intended trade — these are exactly the classes where a wrong-but-fast
partial answer is worse than a correct-but-later one. Non-fail-prone classes keep
their streaming TTFT untouched.

Memory is bounded by the optional ``max_buffer_bytes`` guard: if the local
response exceeds it mid-buffer, that is treated as a verify-failure (we cannot
cheaply prove an oversized output is good, and we will not stream a partial local
answer) → discard the buffer and fall back. A wall-clock ``timeout`` is
deliberately out of scope for this synchronous iterator — interrupting a blocked
backend needs a thread/async layer that belongs to the serve-path wiring
(T009/T012), not to this composable unit; the byte cap is the simple,
stdlib-only guard.

Stdlib-only. Uses T007's ``verify`` and T001's ``Backend`` protocol; the
fallback backend is *injected* (any :class:`Backend`) — this module does NOT
build the full routing/fallback chain (T009) or classify intent (T003).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Container, Iterator, List, Optional, Sequence

from .internal import Backend, InternalRequest
from .verify import ResponseView, VerifyResult, all_passed, default_verifiers, run_verifiers


# --------------------------------------------------------------------------- #
# fallback signal
# --------------------------------------------------------------------------- #
@dataclass
class FallbackEvent:
    """What the commit window discarded and why — handed to the ``on_fallback``
    logging hook so the router can record it as a profile signal.

    ``buffered_text`` is the rejected *local* response (whatever was buffered
    before the discard, possibly partial if the local backend raised). It travels
    only on this side channel to the logger; it is **never** delivered to the
    harness (that is the whole point of the commit window). ``overflowed`` is True
    when the fallback was triggered by the ``max_buffer_bytes`` guard rather than
    a verifier verdict (in which case ``verify_results`` is empty, since the
    buffer was abandoned before verification). ``error`` is set (to a
    ``"<Type>: <msg>"`` string) when the fallback was caused by a *seam fault* —
    the local backend raising mid-generation, or a throwing
    ``response_view_factory`` — rather than an ordinary verify-failure; it is
    ``None`` on a normal verify-fail or overflow.
    """

    work_class: str
    reason: str
    verify_results: List[VerifyResult] = field(default_factory=list)
    buffered_text: str = ""
    overflowed: bool = False
    error: Optional[str] = None


#: Default view factory: a plain :class:`Backend` only yields text deltas, so the
#: view carries just the joined text. T009 can inject a richer factory to also
#: carry ``finish_reason``/``tool_calls`` when the backend exposes them.
ResponseViewFactory = Callable[[Sequence[str], InternalRequest], ResponseView]


def build_response_view(buffered: Sequence[str], request: InternalRequest) -> ResponseView:
    """Assemble a :class:`ResponseView` from buffered text deltas.

    The :class:`Backend` protocol yields plain text deltas only, so the default
    view's ``text`` is the lossless join of the buffer and the structured fields
    (``finish_reason``/``tool_calls``) are left unset.

    **Consequence for the default verifier chain (findings 12/13).** With this
    default factory, only the *text-based* checks are effective:
    ``NonEmptyContent``, ``CodeParses``, ``DiffWellFormed``, and
    ``FormatWellFormed``. ``NotTruncated`` (needs ``finish_reason``) and
    ``ToolCallJSONValid`` (needs ``tool_calls``) see only their unset defaults and
    are therefore **no-ops** here — they cannot fire. A caller that wants those
    two checks to be meaningful (e.g. T009, once it has a richer backend) must
    inject a custom ``response_view_factory`` that carries ``finish_reason`` and
    ``tool_calls`` from the real response.
    """
    return ResponseView(text="".join(buffered))


# --------------------------------------------------------------------------- #
# internal: fully materialize a local stream (the safety boundary)
# --------------------------------------------------------------------------- #
def _drain(
    deltas: Iterator[str],
    max_buffer_bytes: Optional[int],
) -> "tuple[List[str], bool, Optional[BaseException]]":
    """Drain ``deltas`` into a list, optionally capping total UTF-8 byte size.

    Returns ``(buffered, overflowed, error)``. ``overflowed`` is True iff the cap
    was exceeded — in which case draining stops early. ``error`` is the exception
    the backend raised mid-generation, or ``None`` if it streamed cleanly: a local
    backend that yields some deltas and then raises (repo gotcha #1 — an
    OOM-killed scheduler, a reset connection) must NOT propagate to the consumer,
    so we capture the exception here and let the caller fail safe to fallback. The
    partial buffer is returned for the profile signal but is the caller's to
    discard — it is never delivered to the harness.

    This function is the safety boundary: it returns a fully realized ``list``
    (not a lazy iterator), so by the time the caller inspects the buffer the
    entire local response (or the prefix before a raise) is in memory and nothing
    has been delivered.
    """
    buffered: List[str] = []
    total = 0
    overflowed = False
    error: Optional[BaseException] = None
    gen = iter(deltas)
    try:
        for delta in gen:
            buffered.append(delta)
            if max_buffer_bytes is not None:
                total += len(delta.encode("utf-8", "surrogatepass"))
                if total > max_buffer_bytes:
                    overflowed = True
                    break
    except Exception as exc:  # noqa: BLE001 - backend fault must fail safe, not crash
        error = exc
    finally:
        # Close the generator if we stopped early (or even on exhaustion) so a
        # backend holding a resource gets a chance to clean up. A close() that
        # itself raises must not mask the original outcome.
        close = getattr(gen, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
    return buffered, overflowed, error


# --------------------------------------------------------------------------- #
# the commit window
# --------------------------------------------------------------------------- #
def stream_with_commit_window(
    request: InternalRequest,
    *,
    work_class: str,
    local_backend: Backend,
    fallback_backend: Backend,
    fail_prone_classes: Container[str],
    verifiers: Optional[Sequence] = None,
    on_fallback: Optional[Callable[[FallbackEvent], None]] = None,
    max_buffer_bytes: Optional[int] = None,
    response_view_factory: ResponseViewFactory = build_response_view,
) -> Iterator[str]:
    """Stream a response for ``work_class``, applying the commit window when
    (and only when) the class is fail-prone.

    Yields text deltas (dialect-agnostic; a T001 dialect frames them into SSE
    downstream). Behaviour:

    * ``work_class not in fail_prone_classes`` → **passthrough**: lazily
      ``yield from local_backend.generate(request)``. No buffering, no verify —
      TTFT is preserved. (Acceptance criterion 2.)
    * otherwise → **commit window**: buffer the local response fully, build a
      :class:`ResponseView`, run the verifier chain, then either commit (replay
      the buffered local deltas) on PASS or — on FAIL (or a ``max_buffer_bytes``
      overflow) — discard the buffer entirely and stream ``fallback_backend``.
      Not one buffered local delta is yielded on the fallback path. (Acceptance
      criterion 1.)

    **Fail-safe seams.** In the fail-prone path *every seam that can raise fails
    SAFE*, never propagating to the harness: a ``local_backend`` that raises
    mid-generation, a throwing ``response_view_factory``, and a verifier that
    raises (T007 ``run_verifiers`` already backstops the last) all collapse to
    "discard the buffer → fall back to cloud". A throwing ``on_fallback`` hook is
    swallowed so it cannot turn a recoverable fallback into a crash. The
    no-partial-local-tokens guarantee holds on every one of these error paths.

    ``on_fallback`` (optional) is invoked with a :class:`FallbackEvent` *before*
    the fallback stream starts — the discard is a profile signal worth logging.

    **Observability gap (finding 14).** The PASS/commit path forwards the buffered
    deltas but does *not* surface the verifier scores (a low-but-passing score —
    e.g. a ``RefusalMarker`` hit that did not hard-fail — is currently invisible
    on commit; only the fallback path reports via ``on_fallback``). The serve path
    now drives :func:`~anvil_serving.router.fallback.route_with_fallback` (T009,
    wired) which appends to the :class:`~anvil_serving.router.decision_log.DecisionLog`
    on both commit and fallback; richer per-verifier score reporting on the commit
    path remains a future improvement.

    ``fail_prone_classes`` must be a real collection (set/frozenset/list/...). A
    bare ``str`` is rejected (``in`` on a string is substring matching — a silent
    footgun); pass ``{"my-class"}``, not ``"my-class"``.
    """
    # Footgun guard (finding 8): `work_class in "fail-prone"` is *substring*
    # matching, not membership. Reject a bare str up front so a miswired caller
    # fails loud here rather than silently mis-routing every request.
    if isinstance(fail_prone_classes, str):
        raise TypeError(
            "fail_prone_classes must be a set/collection of class names, not a str "
            f"(got {fail_prone_classes!r})"
        )
    # ---- passthrough: NOT fail-prone -> stream local as-is (no commit window).
    # Documented behaviour (acceptance criterion 2): the window is skipped
    # wholesale here. We do not buffer and we never touch the verifiers, so a
    # non-fail-prone class keeps streaming TTFT even if its output would have
    # failed verify. `yield from` keeps it lazy (one delta pulled per next()).
    if work_class not in fail_prone_classes:
        yield from local_backend.generate(request)
        return

    # ---- fail-prone: open the commit window. ------------------------------- #
    # SAFETY BOUNDARY. Materialize the ENTIRE local response before the first
    # `yield` below. After this line `buffered` is a realized list and not one
    # byte of it has reached the harness; the verify/commit decision is taken
    # while still holding everything in memory. No code below yields a local
    # delta on any path other than the explicit PASS commit. Both the call to
    # `.generate()` (an eager backend can raise here) and the drain itself (a
    # generator backend raises mid-iteration) are guarded — a raising local seam
    # must fail SAFE to cloud, never crash the harness.
    try:
        local_stream = local_backend.generate(request)
    except Exception as exc:  # noqa: BLE001 - eager backend fault must fail safe
        buffered, overflowed, local_error = [], False, exc
    else:
        buffered, overflowed, local_error = _drain(local_stream, max_buffer_bytes)

    results: List[VerifyResult] = []
    seam_error: Optional[str] = None
    if local_error is not None:
        # Local backend raised (OOM-kill / "scheduler died" / connection reset —
        # repo gotcha #1). Discard ANY partial buffer and fall back to cloud.
        buffered = []                       # belt-and-suspenders: deliver nothing local
        passed = False
        seam_error = f"{type(local_error).__name__}: {local_error}"
        reason = f"local backend raised ({seam_error})"
    elif overflowed:
        # Oversized local output: we cannot cheaply prove it is good and we will
        # not stream a partial local answer -> treat as a verify-failure.
        passed = False
        reason = f"local response exceeded max_buffer_bytes={max_buffer_bytes}"
    else:
        try:
            view = response_view_factory(buffered, request)
            results = list(run_verifiers(view, verifiers or default_verifiers()))
            passed = all_passed(results)
            reason = "verify failed" if not passed else "verify passed"
        except Exception as exc:  # noqa: BLE001 - a throwing factory must fail safe
            # `run_verifiers` already backstops a verifier that raises; this guard
            # additionally covers a throwing `response_view_factory` (or any other
            # blow-up while deciding). Treat as a verify-failure -> fallback.
            passed = False
            results = []
            seam_error = f"{type(exc).__name__}: {exc}"
            reason = f"response_view_factory raised ({seam_error})"

    if passed:
        # COMMIT. Only here — after a PASS decision — is the buffered local
        # response delivered, replayed in order.
        for delta in buffered:
            yield delta
        return

    # FALLBACK. The buffer is discarded; `buffered` is never iterated again.
    # Log the discard (profile signal), then stream the cloud backend ONLY. A
    # throwing logging hook must not turn a recoverable fallback into a crash, so
    # the hook call is swallowed best-effort.
    if on_fallback is not None:
        try:
            on_fallback(
                FallbackEvent(
                    work_class=work_class,
                    reason=reason,
                    verify_results=results,
                    buffered_text="".join(buffered),
                    overflowed=overflowed,
                    error=seam_error,
                )
            )
        except Exception:  # noqa: BLE001 - hook is best-effort; never block the fallback
            pass
    yield from fallback_backend.generate(request)
