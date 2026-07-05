"""Acoustic VAD stage: turn lifecycle + barge-in (anvil task T004).

Real acoustic VAD (e.g. Silero VAD) is a small ONNX/torch model. Per the house
rule this module NEVER imports torch, onnxruntime, or any ML runtime -- it
defines a tiny :class:`VADModel` seam any real detector implements
(``is_speech(frame) -> bool``) and ships exactly one deterministic
:class:`FakeVADModel` good enough to drive the turn-taking state machine in
tests. A later unit swaps in a real detector behind this same interface.

HONESTY NOTE: nothing in this module is proven against real audio hardware or
a live VAD model -- only the turn-lifecycle state machine (silence-threshold
end-of-turn, barge-in generation bump) is exercised, with :class:`FakeVADModel`
standing in for acoustic speech/silence detection. TODO(a later unit): wire a
real detector (e.g. an onnxruntime-based Silero wrapper) behind
:class:`VADModel` and validate against recorded audio.

Turn lifecycle: raw PCM is fed to :meth:`VADStage.process` one fixed-size
frame at a time. The stage tracks whether it is currently INSIDE a speech
segment; a run of ``silence_frames`` consecutive non-speech frames ends the
segment. On end-of-turn it emits a ``SpeechEvent("stopped", ...)`` followed by
the buffered :class:`~anvil_serving.voice.messages.VADAudio` (``is_final``).
On speech onset it emits ``SpeechEvent("started", ...)`` -- and if the
PREVIOUS turn's response was still being generated/spoken (``self.responding``
is True), that onset is a BARGE-IN: the stage bumps ``cancel_scope`` (marking
the outgoing generation stale) BEFORE starting the new turn, so every
downstream LLMChunk/TTSInput/AudioOut tagged with the old generation is
recognized stale by ``is_stale()`` and dropped.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, List, Optional, Protocol, runtime_checkable

from ..cancel_scope import CancelScope
from ..messages import VADAudio
from .base import BaseStage


@runtime_checkable
class VADModel(Protocol):
    """The acoustic-VAD seam: is this frame speech?

    Real implementations wrap an actual detector (Silero, WebRTC VAD, ...).
    NEVER imported or instantiated by this module -- only the protocol shape
    is defined here.
    """

    def is_speech(self, frame: bytes) -> bool: ...


class FakeVADModel:
    """Deterministic VAD stand-in for tests: a frame is "speech" iff it is
    non-empty and contains at least one non-zero byte (silence is
    conventionally represented as all-zero PCM)."""

    def is_speech(self, frame: bytes) -> bool:
        return bool(frame) and any(b != 0 for b in frame)


@dataclass(frozen=True)
class SpeechEvent:
    """Turn-lifecycle notification the VAD stage fans out alongside
    :class:`~anvil_serving.voice.messages.VADAudio`.

    Not one of the fixed inter-stage message types in ``messages.py`` (those
    are the DATA that flows stage-to-stage); this is a lifecycle signal a
    later realtime-server unit (or a test) can observe. Downstream data stages
    (STT, LLM, TTS) simply ignore it via their own ``isinstance`` filtering.

    ``audio_ms`` is this event's position, in milliseconds, along this
    ``VADStage`` instance's own running frame clock (``frame_index *
    config.frame_ms`` -- see :attr:`VADStage._frame_index`) -- i.e. the
    ``audio_start_ms``/``audio_end_ms`` the OpenAI Realtime wire protocol's
    ``input_audio_buffer.speech_started``/``speech_stopped`` events carry
    (PUNCH-LIST #3, ``realtime/events.py``'s ``_dispatch_speech_event``).
    Deterministic and testable (frame-count * configured frame duration), but
    NOT a real acoustic timestamp -- honest about what it is, same spirit as
    this module's other honesty notes.
    """

    kind: str  # "started" | "stopped"
    turn_id: str
    turn_revision: int
    generation: int
    audio_ms: int = 0


@dataclass(frozen=True)
class VADConfig:
    """Tunables for the turn-taking state machine.

    ``silence_ms`` is the end-of-turn silence threshold. Kept in the 150-250ms
    band: long enough to not clip a mid-sentence breath, short enough to keep
    turn-taking snappy (the whole point of the "chat-fast" work class this
    pipeline routes through -- see anvil_serving/router/classify.py). Bounds
    are enforced at construction so a misconfigured manifest fails fast rather
    than silently producing sluggish or hair-trigger turn-taking.

    ``frame_ms`` is the duration one ``process()`` frame represents, used to
    convert the silence-run length (in frames) to milliseconds via
    :attr:`silence_frames`.
    """

    frame_ms: int = 20
    silence_ms: int = 200

    def __post_init__(self) -> None:
        if not (150 <= self.silence_ms <= 250):
            raise ValueError(
                f"silence_ms must be in [150, 250] (got {self.silence_ms}); "
                f"see docs/findings/2026-07-04-hf-speech-to-speech-review.md "
                f"for the turn-taking latency rationale"
            )
        if self.frame_ms <= 0:
            raise ValueError(f"frame_ms must be positive (got {self.frame_ms})")

    @property
    def silence_frames(self) -> int:
        """Consecutive silent frames that constitute end-of-turn."""
        return max(1, round(self.silence_ms / self.frame_ms))


class VADStage(BaseStage):
    """Turns a stream of raw PCM frames into :class:`VADAudio` turn segments.

    ``responding`` is a plain attribute the pipeline (or a test) sets ``True``
    once a turn's response has started generating/playing and back to
    ``False`` once it is fully drained; a speech onset detected while it is
    ``True`` is treated as a barge-in. The stage itself also sets it ``True``
    the moment it emits an end-of-turn ``VADAudio`` (the natural point a
    response is about to start) -- a caller only needs to clear it once
    playback of the FULL response has finished.
    """

    name = "vad"

    def __init__(
        self,
        in_queue,
        out_queues=None,
        *,
        cancel_scope: Optional[CancelScope] = None,
        config: Optional[VADConfig] = None,
        model: Optional[VADModel] = None,
    ) -> None:
        super().__init__(in_queue, out_queues)
        self.cancel_scope = cancel_scope or CancelScope()
        self.config = config or VADConfig()
        self.model = model or FakeVADModel()

        self._turn_counter = itertools.count(1)
        self._in_speech = False
        self._buffer: List[bytes] = []
        self._silence_run = 0
        self._turn_id: Optional[str] = None
        self._turn_revision = 0
        # True while a response for the CURRENT (or just-ended) turn is still
        # being generated/played; a new speech onset while True is a barge-in.
        self.responding = False
        # Running count of every frame this instance has ever processed
        # (speech or silence) -- this stage's own audio clock, used only to
        # stamp `SpeechEvent.audio_ms` (see that dataclass's docstring).
        self._frame_index = 0

    def _start_new_turn(self, *, barge_in: bool) -> int:
        if barge_in:
            gen = self.cancel_scope.cancel()
        else:
            gen = self.cancel_scope.begin_new_generation()
        self._turn_id = f"turn-{next(self._turn_counter)}"
        self._turn_revision = 0
        self._buffer = []
        self._silence_run = 0
        self.responding = False
        return gen

    def process(self, frame: bytes) -> Optional[List[Any]]:
        # This frame's own start-of-frame position on this instance's running
        # audio clock, BEFORE incrementing -- see `SpeechEvent.audio_ms`.
        frame_start_ms = self._frame_index * self.config.frame_ms
        self._frame_index += 1

        is_speech = self.model.is_speech(frame)
        events: List[Any] = []

        if is_speech:
            if not self._in_speech:
                barge_in = self.responding
                gen = self._start_new_turn(barge_in=barge_in)
                self._in_speech = True
                events.append(
                    SpeechEvent("started", self._turn_id, self._turn_revision, gen, audio_ms=frame_start_ms)
                )
            self._buffer.append(frame)
            self._silence_run = 0
            return events or None

        # A silent frame.
        if not self._in_speech:
            return None  # silence before any speech onset: nothing to do

        self._silence_run += 1
        if self._silence_run >= self.config.silence_frames:
            pcm = b"".join(self._buffer)
            gen = self.cancel_scope.current()
            turn_id = self._turn_id or "turn-0"
            turn_revision = self._turn_revision
            end_ms = self._frame_index * self.config.frame_ms
            events.append(SpeechEvent("stopped", turn_id, turn_revision, gen, audio_ms=end_ms))
            events.append(
                VADAudio(
                    turn_id=turn_id,
                    turn_revision=turn_revision,
                    generation=gen,
                    pcm=pcm,
                    is_final=True,
                )
            )
            self._in_speech = False
            self._buffer = []
            self._silence_run = 0
            self.responding = True  # a response is about to be generated
            return events
        return None
