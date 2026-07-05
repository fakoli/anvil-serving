"""BaseStage: the common thread-loop skeleton every pipeline stage shares
(anvil task T003).

Every stage in the voice pipeline (VAD, STT, LLM, TTS -- and the small
bridging stages ``pipeline.py`` wires between them) is one background thread
pulling items off an input ``queue.Queue``, running :meth:`BaseStage.process`
on each, and fanning the result out to zero or more output queues. This
module is stdlib-only (``threading``, ``queue``, ``logging``).
"""
from __future__ import annotations

import inspect
import logging
import queue
import threading
from typing import Any, Iterable, List, Optional

logger = logging.getLogger(__name__)

# Sentinel enqueued to tell a stage's thread to drain and stop. Forwarded to
# every downstream queue so a pipeline shuts down stage-by-stage in order,
# rather than every stage racing to notice a shared "stop" flag mid-item.
PIPELINE_END = object()


class BaseStage:
    """One background-thread pipeline stage.

    Subclasses implement :meth:`process`: given one input item, return
    ``None`` (emit nothing), a single output item, or an iterable of output
    items (fan-out). The run loop:

    * ``in_queue.get(timeout=0.1)`` -- a short poll rather than a blocking
      ``get()`` with no timeout, so :meth:`stop` is responsive (checked every
      100ms) without requiring a wake-up sentinel be pushed on every stop.
    * :data:`PIPELINE_END` sentinel -- forwarded to every ``out_queues``
      member, then the loop exits. This is how a pipeline shuts down cleanly:
      pushing one sentinel at the front propagates stage-by-stage.
    * **per-item exception isolation** -- an exception raised by
      :meth:`process` for ONE item is logged and swallowed; the thread keeps
      pulling the next item rather than dying. A single malformed
      transcription or a transient HTTP error on one turn must not silently
      wedge the whole voice session.
    """

    name = "stage"

    def __init__(
        self,
        in_queue: "queue.Queue[Any]",
        out_queues: Optional[List["queue.Queue[Any]"]] = None,
    ) -> None:
        self.in_queue = in_queue
        self.out_queues: List["queue.Queue[Any]"] = list(out_queues or [])
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def process(self, item: Any):
        """Process one input item. Override in every subclass.

        Two calling conventions are supported:

        * **Generator** (preferred for anything that streams from a network
          call, e.g. :class:`~anvil_serving.voice.stages.llm.LLMStage`,
          :class:`~anvil_serving.voice.stages.tts.TTSStage`): ``yield`` each
          output item the moment it is ready. :meth:`_run` iterates the
          generator and ``put()``s each yielded item onto every
          ``out_queues`` member IMMEDIATELY, before asking the generator for
          its next item -- this is what lets a stage emit its first output
          (e.g. the first speakable sentence) while it is still busy
          producing the rest (e.g. still streaming the rest of the reply
          from the LLM), instead of blocking the whole turn until
          :meth:`process` fully returns.
        * **Plain return** (fine for a stage whose one input item always
          becomes its output synchronously, e.g. a bridging/stub stage):
          return ``None`` to emit nothing, a single item to emit it, or a
          list/tuple of items to fan them all out (in order).
        """
        raise NotImplementedError

    def _emit_one(self, item: Any) -> None:
        for q in self.out_queues:
            q.put(item)

    def _emit_result(self, result: Any) -> None:
        if result is None:
            return
        if isinstance(result, (list, tuple)):
            for item in result:
                if item is not None:
                    self._emit_one(item)
        else:
            self._emit_one(result)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self.in_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is PIPELINE_END:
                self._emit_one(PIPELINE_END)
                break
            try:
                result = self.process(item)
                if inspect.isgenerator(result):
                    # Pull one item at a time and put() it right away -- do
                    # NOT collect into a list first. If `process` is blocked
                    # (e.g. waiting on the next network chunk) between two
                    # yields, whatever it already yielded is on the queue
                    # NOW, not after the whole turn finishes.
                    for produced in result:
                        self._emit_result(produced)
                else:
                    self._emit_result(result)
            except Exception:  # noqa: BLE001 - per-item isolation is the point
                logger.exception(
                    "%s: process() raised on item %r; continuing", self.name, item
                )
                continue

    def start(self) -> None:
        """Start the stage's background thread (idempotent)."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def stop(self, *, join_timeout: Optional[float] = 2.0) -> None:
        """Signal the run loop to stop and join the thread (idempotent).

        Resets ``self._thread`` to ``None`` once the join confirms the thread
        has actually exited, so a later :meth:`start` can spin up a fresh
        thread instead of silently no-op'ing (the "restart after stop" bug).
        If the join times out before the thread exits, ``self._thread`` is
        left as-is -- :meth:`start` stays a no-op against a thread that, as
        far as we can tell, might still be running.
        """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
            if not self._thread.is_alive():
                self._thread = None

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


class ThreadManager:
    """Starts/stops a group of :class:`BaseStage` instances together."""

    def __init__(self, stages: Optional[Iterable[BaseStage]] = None) -> None:
        self.stages: List[BaseStage] = list(stages or [])

    def add(self, stage: BaseStage) -> BaseStage:
        self.stages.append(stage)
        return stage

    def start_all(self) -> None:
        for s in self.stages:
            s.start()

    def stop_all(self, *, join_timeout: Optional[float] = 2.0) -> None:
        for s in self.stages:
            s.stop(join_timeout=join_timeout)

    def all_alive(self) -> bool:
        return all(s.is_alive() for s in self.stages)
