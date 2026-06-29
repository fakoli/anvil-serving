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

    ``buffered_text`` is the rejected *local* response. It travels only on this
    side channel to the logger; it is **never** delivered to the harness (that is
    the whole point of the commit window). ``overflowed`` is True when the
    fallback was triggered by the ``max_buffer_bytes`` guard rather than a
    verifier verdict (in which case ``verify_results`` is empty, since the buffer
    was abandoned before verification).
    """

    work_class: str
    reason: str
    verify_results: List[VerifyResult] = field(default_factory=list)
    buffered_text: str = ""
    overflowed: bool = False


#: Default view factory: a plain :class:`Backend` only yields text deltas, so the
#: view carries just the joined text. T009 can inject a richer factory to also
#: carry ``finish_reason``/``tool_calls`` when the backend exposes them.
ResponseViewFactory = Callable[[Sequence[str], InternalRequest], ResponseView]


def build_response_view(buffered: Sequence[str], request: InternalRequest) -> ResponseView:
    """Assemble a :class:`ResponseView` from buffered text deltas.

    The :class:`Backend` protocol yields plain text deltas only, so the default
    view's ``text`` is the lossless join of the buffer and the structured fields
    (``finish_reason``/``tool_calls``) are left unset. Callers with a richer
    backend pass a custom ``response_view_factory`` to populate those.
    """
    return ResponseView(text="".join(buffered))


# --------------------------------------------------------------------------- #
# internal: fully materialize a local stream (the safety boundary)
# --------------------------------------------------------------------------- #
def _drain(
    deltas: Iterator[str],
    max_buffer_bytes: Optional[int],
) -> "tuple[List[str], bool]":
    """Drain ``deltas`` into a list, optionally capping total UTF-8 byte size.

    Returns ``(buffered, overflowed)``. ``overflowed`` is True iff the cap was
    exceeded — in which case draining stops early and the underlying generator is
    closed. This function is the safety boundary: it returns a fully realized
    ``list`` (not a lazy iterator), so by the time the caller inspects the buffer
    the entire local response is in memory and nothing has been delivered.
    """
    buffered: List[str] = []
    total = 0
    overflowed = False
    gen = iter(deltas)
    try:
        for delta in gen:
            buffered.append(delta)
            if max_buffer_bytes is not None:
                total += len(delta.encode("utf-8", "surrogatepass"))
                if total > max_buffer_bytes:
                    overflowed = True
                    break
    finally:
        # Close the generator if we stopped early (or even on exhaustion) so a
        # backend holding a resource gets a chance to clean up.
        close = getattr(gen, "close", None)
        if callable(close):
            close()
    return buffered, overflowed


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

    ``on_fallback`` (optional) is invoked with a :class:`FallbackEvent` *before*
    the fallback stream starts — the discard is a profile signal worth logging.
    """
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
    # delta on any path other than the explicit PASS commit.
    buffered, overflowed = _drain(local_backend.generate(request), max_buffer_bytes)

    if overflowed:
        # Oversized local output: we cannot cheaply prove it is good and we will
        # not stream a partial local answer -> treat as a verify-failure.
        passed = False
        results: List[VerifyResult] = []
        reason = f"local response exceeded max_buffer_bytes={max_buffer_bytes}"
    else:
        view = response_view_factory(buffered, request)
        results = list(run_verifiers(view, verifiers or default_verifiers()))
        passed = all_passed(results)
        reason = "verify failed" if not passed else "verify passed"

    if passed:
        # COMMIT. Only here — after a PASS decision — is the buffered local
        # response delivered, replayed in order.
        for delta in buffered:
            yield delta
        return

    # FALLBACK. The buffer is discarded; `buffered` is never iterated again.
    # Log the discard (profile signal), then stream the cloud backend ONLY.
    if on_fallback is not None:
        on_fallback(
            FallbackEvent(
                work_class=work_class,
                reason=reason,
                verify_results=results,
                buffered_text="".join(buffered),
                overflowed=overflowed,
            )
        )
    yield from fallback_backend.generate(request)
