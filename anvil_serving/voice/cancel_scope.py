"""Generation-counter cancel scope for barge-in (anvil task T003).

A real-time voice turn can be interrupted mid-reply: the user starts talking
again while the assistant is still generating/speaking (a "barge-in"). Every
:mod:`~anvil_serving.voice.messages` inter-stage message carries the
``generation`` it was produced under; a barge-in bumps the shared
:class:`CancelScope`'s generation, and every downstream stage checks
``cancel_scope.is_stale(msg.generation)`` on each item it processes (the LLM
stage checks it once per streamed delta -- see ``stages/llm.py``) to decide
whether to keep working on now-superseded output or drop it.

Concurrency model -- LOCK-FREE SINGLE-WRITER, MANY-READER:

The VAD stage (``stages/vad.py``) is the ONLY writer: it calls
:meth:`cancel`/:meth:`begin_new_generation` from its own single background
thread. Every other stage only READS ``.generation`` via :meth:`is_stale`/
:meth:`current`. Under CPython's GIL, reading or writing a plain ``int``
attribute is a single atomic bytecode-level operation, so a reader can never
observe a torn/partial value -- it either sees the generation before or after
a bump, never a corrupt one. This is exactly the property a lock-free
single-writer/many-reader design needs, and it holds ONLY under the GIL: it
is NOT safe on a free-threaded (``--disable-gil`` / PEP 703) CPython build, a
compiled extension that releases the GIL around this code, or if a second
writer is ever introduced. :meth:`cancel`/:meth:`begin_new_generation` take an
internal lock anyway (cheap, and future-proofs against a second writer, e.g. a
watchdog timer), but the READ path (:meth:`is_stale`/:meth:`current`) is
intentionally lock-free and depends on the GIL assumption above.
"""
from __future__ import annotations

import threading


class CancelScope:
    """Tracks the current "generation" of a conversational turn.

    ``discarding`` is a coarse flag a stage may set while it is actively
    flushing work made stale by a barge-in, so a caller can tell "no barge-in
    yet" apart from "actively unwinding one" (a later realtime-server unit can
    use it to suppress sending partially-superseded audio).
    """

    def __init__(self) -> None:
        self.generation = 0
        self.discarding = False
        self._write_lock = threading.Lock()

    def cancel(self) -> int:
        """Bump the generation for a barge-in. Returns the new generation.

        Marks ``discarding = True``: downstream stages are expected to be
        unwinding in-flight work tagged with the now-stale prior generation.
        """
        with self._write_lock:
            self.generation += 1
            self.discarding = True
            return self.generation

    def begin_new_generation(self) -> int:
        """Advance to a fresh generation for a clean (non-barge-in) new turn.

        Unlike :meth:`cancel`, this does NOT set ``discarding`` -- there is no
        in-flight stale work to unwind, just a new turn starting from idle.
        """
        with self._write_lock:
            self.generation += 1
            self.discarding = False
            return self.generation

    def mark_settled(self) -> None:
        """Clear ``discarding`` once every stage has finished flushing stale work."""
        with self._write_lock:
            self.discarding = False

    def is_stale(self, generation: int) -> bool:
        """``True`` if ``generation`` predates the CURRENT generation."""
        return generation < self.generation

    def current(self) -> int:
        """The current generation counter."""
        return self.generation
