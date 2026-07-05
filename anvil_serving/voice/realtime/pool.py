"""Bounded pool of isolated single-session pipeline units (anvil task T013).

Mirrors the reference design's ``PipelineUnit`` pool (``docs/findings/2026-07-
04-hf-speech-to-speech-review.md`` s5): each concurrent Realtime connection
gets its OWN :class:`~anvil_serving.voice.pipeline.VoicePipeline` instance out
of a fixed-size pool -- real multi-tenancy via N independent instances, not
one shared async pipeline multiplexing sessions. Pool exhaustion is a clean,
typed rejection (:class:`SessionPoolExhausted`) rather than an unbounded
queue of waiters.

HONESTY NOTE -- drain-before-release, adapted (flagged, not silently
dropped): the reference design's ``SESSION_END`` is a SOFT reset (clears
per-session state, keeps the stage threads alive for the NEXT session).
Today's ``BaseStage``/``ThreadManager`` (T003/T004, ``stages/base.py``) only
expose a HARD stop (:data:`~anvil_serving.voice.stages.base.PIPELINE_END`):
once a stage's thread has been ``stop()``-ed, ``BaseStage.start()`` no-ops on
that same instance (its ``_thread`` handle is never cleared), so there is no
in-place restart path to build a true soft reset on without changing T003/T004.
Given that, this pool's drain-before-release does two things instead:

1. **Drain, verified.** :meth:`SessionPool.release` calls
   ``unit.pipeline.shutdown_gracefully()``, which pushes the existing
   :data:`~anvil_serving.voice.stages.base.PIPELINE_END` sentinel and BLOCKS
   until every stage thread has drained and joined -- i.e. every item the
   outgoing session produced has either reached ``audio_out`` or been
   discarded by a stage's own shutdown, and no thread from that session is
   still running. This is the real "no cross-session leakage" guarantee.
2. **Reconstruct, not reuse.** The unit's ``pipeline`` attribute is then
   replaced with a FRESH instance from ``pipeline_factory`` so the next
   ``claim()`` gets a cleanly-started pipeline. This trades a bit of
   thread-restart overhead per claim/release cycle for a simpler, provably
   correct isolation story than patching a soft-reset sentinel through every
   stage's ``process()`` (VAD/STT/LLM/TTS today only forward types they
   explicitly recognize -- see each stage module -- so an arbitrary new
   sentinel would silently NOT propagate without also touching those
   modules, which is out of this unit's assigned scope).

Followup flagged: adding a real ``SESSION_END`` soft-reset sentinel to
``BaseStage`` (recognized like ``PIPELINE_END`` but resetting per-session
state instead of exiting the thread) would let pool units reuse warm threads
across claims if claim/release latency becomes a bottleneck.

**A second, separate issue surfaced while building this and is flagged here
rather than silently worked around:** ``BaseStage.stop()`` (``stages/base.py``)
sets its ``_stop_event`` SYNCHRONOUSLY, and ``ThreadManager.stop_all()`` calls
``stop()`` stage-by-stage in pipeline order. A stage's run loop only checks
``_stop_event`` at the TOP of its poll loop (between items), so if
``stop()`` lands while a stage still has unprocessed backlog in its own
``in_queue`` (including a not-yet-reached ``PIPELINE_END``), that stage can
exit having silently DROPPED the remaining queued items instead of forwarding
them -- ``shutdown_gracefully()`` is only reliably a full drain if the caller
already knows every stage has finished its work (which is exactly how
``tests/voice/test_pipeline_spine.py`` uses it: a blocking
``drain_audio_out()`` BEFORE ever calling ``shutdown_gracefully()``). This
pool's :meth:`SessionPool.release` calls ``shutdown_gracefully()`` right
after freeing the unit -- to avoid inheriting that race, :meth:`release`
gives the pipeline a short real-time grace window first (see
``_DRAIN_GRACE_SECONDS``) so already-queued work has a real chance to finish
before the stop cascade runs. This is a best-effort MITIGATION, not a
correctness guarantee -- ``stages/base.py`` is a T003/T004 module outside
this unit's assigned files, so the real fix (e.g. having
``ThreadManager.stop_all()`` wait for the sentinel to reach the LAST stage
before stopping any of them, or having ``BaseStage.stop()`` only take effect
once its own queue has actually drained to ``PIPELINE_END``) is left as a
flagged followup rather than patched here.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..pipeline import VoicePipeline

#: Best-effort real-time grace window :meth:`SessionPool.release` sleeps
#: before triggering ``shutdown_gracefully()`` -- see the module docstring's
#: second honesty note. Comfortably larger than the wall-clock cost of the
#: fast in-process stub/real stages this pipeline ships with today; NOT a
#: correctness guarantee against the underlying ``BaseStage.stop()`` race.
_DRAIN_GRACE_SECONDS = 0.3


class SessionPoolExhausted(Exception):
    """Raised by :meth:`SessionPool.claim` when every unit is in use."""


@dataclass
class PoolUnit:
    """One pool slot: a pipeline instance plus its current occupancy."""

    unit_id: int
    pipeline: VoicePipeline
    in_use: bool = False
    session_id: Optional[str] = None
    #: The :class:`~anvil_serving.voice.realtime.service.RealtimeService`
    #: bound to this unit for the current session (``None`` when idle).
    service: Any = None


PipelineFactory = Callable[[], VoicePipeline]


class SessionPool:
    """Fixed-size pool of :class:`PoolUnit`\\ s.

    ``size`` units are constructed up front (from ``pipeline_factory``, which
    defaults to ``VoicePipeline`` with no arguments -- pass a factory closing
    over shared config, e.g. an ``LLMStageConfig``, to give every session unit
    the same endpoint config). Thread-safe: :meth:`claim`/:meth:`release` may
    be called from any connection's own thread concurrently.
    """

    def __init__(self, size: int, *, pipeline_factory: Optional[PipelineFactory] = None) -> None:
        if size < 1:
            raise ValueError("pool size must be >= 1 (got %r)" % size)
        self.size = size
        self._pipeline_factory: PipelineFactory = pipeline_factory or VoicePipeline
        self._lock = threading.Lock()
        self._units: List[PoolUnit] = [
            PoolUnit(unit_id=i, pipeline=self._pipeline_factory()) for i in range(size)
        ]
        self._claims_total = 0
        self._rejections_total = 0
        self._releases_total = 0

    # -- claim / release --------------------------------------------------------
    def claim(self, session_id: str) -> PoolUnit:
        """Reserve an idle unit for ``session_id`` and start its pipeline.

        Raises :class:`SessionPoolExhausted` if every unit is currently
        ``in_use`` -- callers (the WS connection handler) should answer the
        Realtime API's own ``session_limit_reached``-style rejection (a
        clean close, not an exception surfacing to the caller's socket).
        """
        with self._lock:
            for unit in self._units:
                if not unit.in_use:
                    unit.in_use = True
                    unit.session_id = session_id
                    unit.pipeline.start()
                    self._claims_total += 1
                    return unit
            self._rejections_total += 1
            raise SessionPoolExhausted(
                "session pool exhausted: all %d unit(s) are in use" % self.size
            )

    def release(self, unit: PoolUnit, *, drain_timeout: Optional[float] = 2.0) -> None:
        """Drain ``unit``'s outgoing session, then free it for reuse.

        Idempotent: releasing an already-idle unit is a no-op (guards against
        a connection handler's ``finally`` block double-releasing after an
        error path already released it).
        """
        with self._lock:
            if not unit.in_use:
                return
            unit.in_use = False
            unit.session_id = None
            unit.service = None
            self._releases_total += 1

        # Drain OUTSIDE the lock -- shutdown_gracefully blocks on thread joins
        # and must never hold the pool lock while doing so (would stall every
        # other connection's claim/release).
        #
        # The grace sleep works around the BaseStage.stop() race documented
        # in this module's docstring (second honesty note): give already-
        # queued work a real chance to reach audio_out before the stop
        # cascade can race ahead of it and drop it silently.
        if drain_timeout:
            time.sleep(min(_DRAIN_GRACE_SECONDS, drain_timeout))
        unit.pipeline.shutdown_gracefully(join_timeout=drain_timeout)
        unit.pipeline = self._pipeline_factory()

    def cancel_active_response(self, session_id: str) -> bool:
        """Barge-in helper: bump the cancel-scope generation for the unit
        currently bound to ``session_id``. Returns ``False`` if no unit is
        bound to that session (already released, or never claimed)."""
        with self._lock:
            for unit in self._units:
                if unit.in_use and unit.session_id == session_id:
                    unit.pipeline.cancel_scope.cancel()
                    return True
        return False

    def unit_for_session(self, session_id: str) -> Optional[PoolUnit]:
        with self._lock:
            for unit in self._units:
                if unit.in_use and unit.session_id == session_id:
                    return unit
        return None

    # -- introspection (/pool, /usage) -------------------------------------------
    def pool_status(self) -> Dict[str, Any]:
        """``GET /pool``-shaped snapshot: per-unit occupancy."""
        with self._lock:
            return {
                "size": self.size,
                "in_use": sum(1 for u in self._units if u.in_use),
                "idle": sum(1 for u in self._units if not u.in_use),
                "units": [
                    {"unit_id": u.unit_id, "in_use": u.in_use, "session_id": u.session_id}
                    for u in self._units
                ],
            }

    def usage_stats(self) -> Dict[str, Any]:
        """``GET /usage``-shaped snapshot: lifetime claim/release/rejection counters."""
        with self._lock:
            return {
                "claims_total": self._claims_total,
                "releases_total": self._releases_total,
                "rejections_total": self._rejections_total,
            }
