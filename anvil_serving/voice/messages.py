"""Typed inter-stage messages for the voice pipeline (anvil task T003).

The orchestrator is a VAD -> STT -> LLM -> TTS pipeline of stdlib-thread
:class:`~anvil_serving.voice.stages.base.BaseStage` workers connected by
``queue.Queue`` pipes (see ``pipeline.py``). These dataclasses are the ONLY
things that ever travel over those queues (plus the
:data:`~anvil_serving.voice.stages.base.PIPELINE_END` sentinel) -- every stage
speaks exactly one of them in, one (or more) out.

Every message carries three turn-tracking fields, shared via the
:class:`StageMessage` base:

* ``turn_id`` -- identifies one logical conversational turn (minted by the VAD
  stage when it detects speech onset).
* ``turn_revision`` -- bumped if the SAME ``turn_id`` is re-used after a
  restart mid-turn (present for forward compatibility with VAD
  implementations that reuse ids; today's VAD stage mints a fresh
  ``turn_id`` per turn instead, so this is usually ``0``).
* ``generation`` -- the :class:`~anvil_serving.voice.cancel_scope.CancelScope`
  generation this message was produced under. A downstream stage compares its
  OWN current ``cancel_scope.generation`` against a message's ``generation``
  via ``cancel_scope.is_stale(msg.generation)`` to recognize and drop work a
  barge-in has since superseded, without needing a lock on the hot path (see
  ``cancel_scope.py`` for the full rationale).

All fields are plain stdlib types (``str``/``int``/``bytes``/``bool``) -- no
third-party audio/ML types leak into this module, keeping it importable with
zero optional dependencies installed.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageMessage:
    """Common turn-tracking fields every inter-stage message carries."""

    turn_id: str
    turn_revision: int
    generation: int


@dataclass(frozen=True)
class VADAudio(StageMessage):
    """One buffered speech segment the VAD stage judged to be user speech.

    Emitted once per completed turn (``is_final=True``): the VAD stage
    accumulates raw PCM frames while speech is detected and flushes the whole
    segment when the silence threshold ends the turn (see ``stages/vad.py``).
    """

    pcm: bytes
    sample_rate: int = 16000
    is_final: bool = True


@dataclass(frozen=True)
class Transcription(StageMessage):
    """STT output text for a user utterance.

    The out-of-process STT serve is called over HTTP by the (later-unit) STT
    stage; this message is its normalized result. ``is_final=False`` is
    reserved for a future streaming-STT partial-hypothesis path -- today's
    stub/real STT stages only ever emit final transcriptions.
    """

    text: str
    is_final: bool = True


@dataclass(frozen=True)
class GenerateRequest(StageMessage):
    """A finalized user turn, ready to feed the LLM stage."""

    text: str


@dataclass(frozen=True)
class LLMChunk(StageMessage):
    """One sentence-batched, TTS-ready chunk of the assistant's streamed reply.

    ``is_final=True`` marks the last chunk of a turn's reply (usually a
    trailing partial sentence flushed once the upstream stream ends).
    """

    text: str
    is_final: bool = False
    joiner: str = ""


@dataclass(frozen=True)
class LLMToolCall(StageMessage):
    """One function call requested by the LLM during a realtime response."""

    item_id: str
    call_id: str
    name: str
    arguments: str
    output_index: int = 0


@dataclass(frozen=True)
class TTSInput(StageMessage):
    """Text handed to the TTS stage to synthesize into audio."""

    text: str
    joiner: str = ""


@dataclass(frozen=True)
class AudioOut(StageMessage):
    """Synthesized audio ready for playback on the realtime connection."""

    pcm: bytes
    sample_rate: int = 24000


@dataclass(frozen=True)
class SpokenText(StageMessage):
    """Exact text selected once TTS produces its first valid audio chunk.

    The candidate cannot change after its first audio is emitted. If the
    remaining stream fails, a later ``TTSSynthesisFailed`` suppresses the
    terminal transcript rather than claiming the whole chunk completed.
    """

    text: str
    joiner: str = ""
    item_id: str = ""


@dataclass(frozen=True)
class TTSSynthesisFailed(StageMessage):
    """Content-free marker that TTS could not complete this text chunk."""


@dataclass(frozen=True)
class EndOfResponse(StageMessage):
    """Sentinel marking the end of one turn's full response.

    Emitted by the LLM stage once its upstream stream completes (and any
    trailing partial sentence has been flushed); the realtime server (a later
    unit) uses this to know a turn's assistant audio is fully drained.
    """
