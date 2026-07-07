"""Assembles the VAD -> STT -> LLM -> TTS voice pipeline (anvil task T004;
real-stage injection seam fixed for PUNCH-LIST #2).

Wires :class:`~anvil_serving.voice.stages.base.BaseStage` instances together
over stdlib ``queue.Queue`` pipes, so a message a stage's ``process()``
returns (or yields) flows straight into the next stage's input queue.

This unit ships REAL stages for VAD (:mod:`.stages.vad`), LLM (:mod:`.stages.llm`),
STT (:mod:`.stages.stt`), and TTS (:mod:`.stages.tts`). ``VoicePipeline``
still defaults STT/TTS to small deterministic STUBS (:class:`EchoSTTStage` /
:class:`EchoTTSStage`) when no ``stt_config=``/``tts_config=`` is given --
good enough to prove the pipeline SPINE moves a message end-to-end in a test,
without a GPU, torch, or network -- but passing a config now switches in the
real out-of-process-serve callers correctly wired to the pipeline's OWN
internal queues (see the FIXED SEAM note below).

FIXED SEAM (was a flagged followup in ``scripts/voice/_real_pipeline.py``):
earlier, ``VoicePipeline.__init__`` built its internal
``vad_to_stt``/``stt_to_bridge``/``bridge_to_tts`` queues itself and only
THEN constructed the default stub bound to them, so a caller wanting to
supply a real :class:`~anvil_serving.voice.stages.stt.STTStage` had no way to
get hold of the correctly-wired queue objects before they existed -- there
was no legitimate way to use the ``stt_stage=``/``tts_stage=`` params. This
version builds the queues FIRST, then constructs every stage from either:

* a **factory** (``vad_stage_factory=``/``stt_stage_factory=``/
  ``llm_stage_factory=``/``tts_stage_factory=``, each
  ``(in_queue, out_queues) -> BaseStage``) -- the fully general escape hatch,
  called with the queues it needs to bind to; or
* a **config** (``stt_config=``/``tts_config=``, mirroring the ``llm_config=``
  pattern this module already had for the LLM stage) -- the common case,
  which switches ``EchoSTTStage``/``EchoTTSStage`` for the real
  :class:`~anvil_serving.voice.stages.stt.STTStage`/
  :class:`~anvil_serving.voice.stages.tts.TTSStage` bound correctly to the
  internal queues, with no wiring bug possible since the caller never touches
  a queue object directly.

HONESTY NOTE: nothing in this module is proven against real audio, a live
STT/TTS/LLM serve, or a GPU -- only the stage-wiring/message-flow spine and
the config-driven real-stage construction are exercised by tests, with fake
``stream_fn``s (and the two ``Echo*`` stubs, when no config is given)
standing in for real out-of-process calls.
"""
from __future__ import annotations

import queue
import time
from dataclasses import fields
from typing import Any, Callable, Dict, List, Mapping, Optional

from .cancel_scope import CancelScope
from .messages import AudioOut, EndOfResponse, GenerateRequest, LLMToolCall, TTSInput, Transcription, VADAudio
from .stages.base import PIPELINE_END, BaseStage, ThreadManager
from .stages.llm import LLMStage, LLMStageConfig
from .stages.llm import StreamFn as LLMStreamFn
from .stages.stt import STTStage, STTStageConfig
from .stages.stt import StreamFn as STTStreamFn
from .stages.tts import TTSStage, TTSStageConfig
from .stages.tts import StreamFn as TTSStreamFn
from .stages.vad import VADConfig, VADStage


class EchoSTTStage(BaseStage):
    """Deterministic STT stub: turns a :class:`VADAudio` segment into a fixed
    :class:`Transcription`.

    Real STT calls an out-of-process serve's ``/v1/audio/transcriptions``
    (:class:`~anvil_serving.voice.stages.stt.STTStage`); this stub is
    :class:`VoicePipeline`'s DEFAULT when no ``stt_config=`` is given -- good
    enough to exercise the pipeline spine in tests without a network call.
    """

    name = "stt-stub"

    def __init__(self, in_queue, out_queues=None, *, fixed_text: str = "hello there") -> None:
        super().__init__(in_queue, out_queues)
        self.fixed_text = fixed_text

    def process(self, item: Any) -> Optional[Transcription]:
        if not isinstance(item, VADAudio):
            return None
        return Transcription(
            turn_id=item.turn_id,
            turn_revision=item.turn_revision,
            generation=item.generation,
            text=self.fixed_text,
            is_final=True,
        )


class TranscriptionToGenerate(BaseStage):
    """Bridges a final :class:`Transcription` into a :class:`GenerateRequest`."""

    name = "transcription-bridge"

    def process(self, item: Any) -> Optional[GenerateRequest]:
        if not isinstance(item, Transcription) or not item.is_final:
            return None
        return GenerateRequest(
            turn_id=item.turn_id,
            turn_revision=item.turn_revision,
            generation=item.generation,
            text=item.text,
        )


class LLMChunkToTTSInput(BaseStage):
    """Bridges an :class:`~anvil_serving.voice.messages.LLMChunk` into a
    :class:`TTSInput`, passing an :class:`EndOfResponse` straight through."""

    name = "llm-chunk-bridge"

    def process(self, item: Any):
        if isinstance(item, (EndOfResponse, LLMToolCall)):
            return item
        text = getattr(item, "text", None)
        if not text:
            return None
        return TTSInput(
            turn_id=item.turn_id,
            turn_revision=item.turn_revision,
            generation=item.generation,
            text=text,
        )


class EchoTTSStage(BaseStage):
    """Deterministic TTS stub: turns :class:`TTSInput` text into fake PCM bytes
    (the UTF-8 encoding of the text -- never real synthesized audio).

    Real TTS calls an out-of-process serve's ``/v1/audio/speech``
    (:class:`~anvil_serving.voice.stages.tts.TTSStage`); this stub is
    :class:`VoicePipeline`'s DEFAULT when no ``tts_config=`` is given -- good
    enough to exercise the pipeline spine in tests without a network call.
    """

    name = "tts-stub"

    def process(self, item: Any):
        if isinstance(item, (EndOfResponse, LLMToolCall)):
            return item
        if not isinstance(item, TTSInput):
            return None
        return AudioOut(
            turn_id=item.turn_id,
            turn_revision=item.turn_revision,
            generation=item.generation,
            pcm=item.text.encode("utf-8"),
        )


#: A stage factory takes the queue a stage should read from and the list of
#: queues it should fan its output out to, and returns a constructed (but not
#: yet started) :class:`BaseStage`. This is the fully general injection seam
#: every one of ``VoicePipeline``'s four real stages accepts -- called AFTER
#: the pipeline's internal queues already exist, which is what fixes the old
#: wiring bug (see module docstring).
StageFactory = Callable[["queue.Queue[Any]", List["queue.Queue[Any]"]], BaseStage]


class VoicePipeline:
    """Wires VAD -> STT -> (bridge) -> LLM -> (bridge) -> TTS over queues and
    manages their background threads as one unit.

    ``audio_in`` is where raw PCM frames (or :data:`PIPELINE_END`) are fed in;
    ``audio_out`` is where synthesized :class:`AudioOut` (and forwarded
    :class:`EndOfResponse`) items land. Everything in between is internal
    plumbing a caller does not need to touch.

    ``vad_events``/``transcript_events`` (PUNCH-LIST #3) are read-only SIDEBAND
    queues a realtime-server layer (:mod:`~anvil_serving.voice.realtime.service`)
    drains to surface the input-side lifecycle -- VAD's
    :class:`~anvil_serving.voice.stages.vad.SpeechEvent`
    (started/stopped) and the STT stage's :class:`Transcription` -- onto the
    wire. These are FAN-OUT DUPLICATES, not the primary path: the VAD stage's
    ``VADAudio``/``SpeechEvent`` output still goes to the STT stage exactly as
    before (via the internal ``vad_to_stt`` queue), and the STT stage's
    ``Transcription`` output still goes to the ``TranscriptionToGenerate``
    bridge exactly as before. A caller draining ``vad_events``/
    ``transcript_events`` observes copies of what already flows through the
    pipeline; it never consumes the primary queue's items or perturbs
    downstream processing (each is `queue.Queue.put` onto its OWN queue, per
    `BaseStage._emit_one`'s already-existing multi-out_queues fan-out -- see
    `stages/base.py`).

    Every stage can be supplied one of two ways (see the module docstring's
    FIXED SEAM note):

    * ``<stage>_stage_factory=`` -- full control: a callable
      ``(in_queue, out_queues) -> BaseStage``, invoked with THIS pipeline's
      own internal queue objects once they exist.
    * ``<stage>_config=`` (STT/TTS) / ``llm_config=`` (LLM) /
      ``vad_config=``+``vad_model=`` (VAD) -- the common case: build the
      REAL stage from a config object. STT/TTS default to the ``Echo*``
      stubs when no config is given (so existing spine tests keep working
      unchanged); VAD and LLM were already real stages by default.
    """

    def __init__(
        self,
        *,
        cancel_scope: Optional[CancelScope] = None,
        vad_config: Optional[VADConfig] = None,
        vad_model: Optional[Any] = None,
        vad_stage_factory: Optional[StageFactory] = None,
        stt_config: Optional[STTStageConfig] = None,
        stt_stream_fn: Optional[STTStreamFn] = None,
        stt_stage_factory: Optional[StageFactory] = None,
        llm_config: Optional[LLMStageConfig] = None,
        llm_stream_fn: Optional[LLMStreamFn] = None,
        llm_stage_factory: Optional[StageFactory] = None,
        tts_config: Optional[TTSStageConfig] = None,
        tts_stream_fn: Optional[TTSStreamFn] = None,
        tts_stage_factory: Optional[StageFactory] = None,
    ) -> None:
        self.cancel_scope = cancel_scope or CancelScope()

        # Build every internal queue FIRST -- this is the fix: no stage
        # (default or caller-supplied factory) is constructed until the queue
        # objects it needs to bind to already exist.
        self.audio_in: "queue.Queue[Any]" = queue.Queue()
        vad_to_stt: "queue.Queue[Any]" = queue.Queue()
        stt_to_bridge: "queue.Queue[Any]" = queue.Queue()
        bridge_to_llm: "queue.Queue[Any]" = queue.Queue()
        llm_to_bridge: "queue.Queue[Any]" = queue.Queue()
        bridge_to_tts: "queue.Queue[Any]" = queue.Queue()
        self.audio_out: "queue.Queue[Any]" = queue.Queue()
        # PUNCH-LIST #3 sideband queues -- see the class docstring's note.
        self.vad_events: "queue.Queue[Any]" = queue.Queue()
        self.transcript_events: "queue.Queue[Any]" = queue.Queue()

        def _default_vad_factory(in_q, out_qs):
            return VADStage(in_q, out_qs, cancel_scope=self.cancel_scope, config=vad_config, model=vad_model)

        def _default_stt_factory(in_q, out_qs):
            if stt_config is None:
                return EchoSTTStage(in_q, out_qs)
            return STTStage(in_q, out_qs, cancel_scope=self.cancel_scope, config=stt_config, stream_fn=stt_stream_fn)

        def _default_llm_factory(in_q, out_qs):
            return LLMStage(in_q, out_qs, cancel_scope=self.cancel_scope, config=llm_config, stream_fn=llm_stream_fn)

        def _default_tts_factory(in_q, out_qs):
            if tts_config is None:
                return EchoTTSStage(in_q, out_qs)
            return TTSStage(in_q, out_qs, cancel_scope=self.cancel_scope, config=tts_config, stream_fn=tts_stream_fn)

        self.vad = (vad_stage_factory or _default_vad_factory)(self.audio_in, [vad_to_stt, self.vad_events])
        self.stt = (stt_stage_factory or _default_stt_factory)(vad_to_stt, [stt_to_bridge, self.transcript_events])
        self.stt_bridge = TranscriptionToGenerate(stt_to_bridge, [bridge_to_llm])
        self.llm = (llm_stage_factory or _default_llm_factory)(bridge_to_llm, [llm_to_bridge])
        self.llm_bridge = LLMChunkToTTSInput(llm_to_bridge, [bridge_to_tts])
        self.tts = (tts_stage_factory or _default_tts_factory)(bridge_to_tts, [self.audio_out])

        self.manager = ThreadManager(
            [self.vad, self.stt, self.stt_bridge, self.llm, self.llm_bridge, self.tts]
        )

    def start(self) -> None:
        self.manager.start_all()

    def stop(self, *, join_timeout: Optional[float] = 2.0) -> None:
        self.manager.stop_all(join_timeout=join_timeout)

    def shutdown_gracefully(self, *, join_timeout: Optional[float] = 2.0) -> None:
        """Push :data:`PIPELINE_END` and wait (up to ``join_timeout``) for the
        LAST stage in the chain (:attr:`tts`) to exit on its own -- proof it
        (and, transitively, every stage upstream of it, since the sentinel
        only reaches it after every earlier stage forwarded it in FIFO order)
        forwarded the sentinel downstream and broke out of its run loop
        *because it saw the sentinel*, rather than being cut off by
        :meth:`stop`'s stop-event mid-item -- then call :meth:`stop` on every
        stage as a bounded safety net.

        That safety-net call is a near-instant no-op for a stage that already
        exited on the sentinel (its thread is already dead, so ``join``
        returns immediately); it only does real work if some stage never
        drained the sentinel before ``join_timeout`` elapsed (e.g. it was
        blocked inside a slow ``process()`` call), in which case it falls
        back to the old best-effort "signal stop, join with a timeout"
        behavior. If ``join_timeout`` is ``None``, waits indefinitely for the
        drain (matching :meth:`stop`'s own ``None`` == "wait forever").

        Deliberately does NOT read from ``audio_out`` to detect the drain
        (that would consume -- and so discard -- whatever real output the
        caller still wants): callers that need the outgoing session's output
        (e.g. ``tests/voice/test_pipeline_spine.py``, ``realtime/pool.py``'s
        drain-before-release) may call :meth:`drain_audio_out` either before
        OR after ``shutdown_gracefully`` and still see everything that was
        produced.
        """
        self.audio_in.put(PIPELINE_END)
        self._wait_for_last_stage_to_drain(timeout=join_timeout)
        self.manager.stop_all(join_timeout=join_timeout)

    def _wait_for_last_stage_to_drain(self, *, timeout: Optional[float]) -> None:
        stage = self.manager.stages[-1] if self.manager.stages else None
        if stage is None:
            return
        deadline = None if timeout is None else time.monotonic() + timeout
        while stage.is_alive():
            if deadline is not None and time.monotonic() >= deadline:
                return
            time.sleep(0.02)

    def drain_audio_out(self, *, timeout: float = 2.0) -> List[Any]:
        """Collect every item currently available on ``audio_out`` (test helper)."""
        items: List[Any] = []
        try:
            while True:
                items.append(self.audio_out.get(timeout=timeout))
                timeout = 0.2  # only wait the full timeout for the FIRST item
        except queue.Empty:
            return items


# --------------------------------------------------------------------------- #
# Manifest -> real-stage config plumbing (anvil task: PUNCH-LIST #2)
# --------------------------------------------------------------------------- #


def stage_config_from_table(table: Mapping[str, Any], cls: type) -> Any:
    """Build an ``STTStageConfig``/``LLMStageConfig``/``TTSStageConfig`` from a
    validated voice-manifest ``[voice.<stt|llm|tts>]`` table (see
    ``anvil_serving/voice/config.py``)."""
    allowed = {field.name for field in fields(cls)}
    kwargs: Dict[str, Any] = {
        key: value for key, value in table.items() if key in allowed
    }
    return cls(**kwargs)


def real_pipeline_kwargs_from_manifest(
    data: Mapping[str, Any], *, vad_config: Optional[VADConfig] = None, vad_model: Optional[Any] = None,
) -> Dict[str, Any]:
    """Build the ``VoicePipeline(**kwargs)`` real-stage configuration from a
    loaded+validated voice manifest (see ``anvil_serving/voice/config.py``).

    Every field comes from the manifest's own ``[voice.stt]``/``[voice.llm]``/
    ``[voice.tts]`` tables -- nothing here invents an endpoint or reads the
    network; it just shapes the dataclasses ``VoicePipeline`` needs to
    construct the REAL out-of-process stages instead of its ``Echo*`` stubs.
    """
    voice = data["voice"]
    return {
        "vad_config": vad_config,
        "vad_model": vad_model,
        "stt_config": stage_config_from_table(voice["stt"], STTStageConfig),
        "llm_config": stage_config_from_table(voice["llm"], LLMStageConfig),
        "tts_config": stage_config_from_table(voice["tts"], TTSStageConfig),
    }


def real_pipeline_factory_from_manifest(
    data: Mapping[str, Any], *, vad_config: Optional[VADConfig] = None, vad_model: Optional[Any] = None,
) -> Callable[[], VoicePipeline]:
    """Return a zero-arg factory building a REAL ``VoicePipeline`` (real STT/
    LLM/TTS stages, wired via the fixed seam above) from a validated voice
    manifest -- the shape :class:`~anvil_serving.voice.realtime.pool.SessionPool`
    wants for its own ``pipeline_factory=``.
    """
    kwargs = real_pipeline_kwargs_from_manifest(data, vad_config=vad_config, vad_model=vad_model)

    def factory() -> VoicePipeline:
        return VoicePipeline(**kwargs)

    return factory
