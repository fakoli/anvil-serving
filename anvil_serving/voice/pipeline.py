"""Assembles the VAD -> STT -> LLM -> TTS voice pipeline (anvil task T004).

Wires :class:`~anvil_serving.voice.stages.base.BaseStage` instances together
over stdlib ``queue.Queue`` pipes, so a message a stage's ``process()``
returns flows straight into the next stage's input queue.

This unit ships REAL stages for VAD (:mod:`.stages.vad`) and LLM
(:mod:`.stages.llm`). STT and TTS are represented here by small deterministic
STUBS (:class:`EchoSTTStage` / :class:`EchoTTSStage`) -- good enough to prove
the pipeline SPINE moves a message end-to-end in a test, without a GPU,
torch, or network. A later unit swaps them for real out-of-process-serve
callers (STT: ``/v1/audio/transcriptions``, TTS: ``/v1/audio/speech``), per
the house rule that heavy inference never runs in-process here.

HONESTY NOTE: nothing in this module is proven against real audio, a live
STT/TTS serve, or a GPU -- only the stage-wiring/message-flow spine is
exercised by tests, with the two Echo* stubs standing in for real out-of-
process calls. TODO(a later unit): replace the stubs with HTTP-calling STT/
TTS stages and validate against a live serve.
"""
from __future__ import annotations

import queue
import time
from typing import Any, List, Optional

from .cancel_scope import CancelScope
from .messages import AudioOut, EndOfResponse, GenerateRequest, TTSInput, Transcription, VADAudio
from .stages.base import PIPELINE_END, BaseStage, ThreadManager
from .stages.llm import LLMStage, LLMStageConfig, StreamFn
from .stages.vad import VADConfig, VADStage


class EchoSTTStage(BaseStage):
    """Deterministic STT stub: turns a :class:`VADAudio` segment into a fixed
    :class:`Transcription`.

    Real STT calls an out-of-process serve's ``/v1/audio/transcriptions``;
    this stub exists ONLY to exercise the pipeline spine in tests -- see the
    module docstring's honesty note.
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
        if isinstance(item, EndOfResponse):
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

    Real TTS calls an out-of-process serve's ``/v1/audio/speech``; this stub
    exists ONLY to exercise the pipeline spine in tests -- see the module
    docstring's honesty note.
    """

    name = "tts-stub"

    def process(self, item: Any):
        if isinstance(item, EndOfResponse):
            return item
        if not isinstance(item, TTSInput):
            return None
        return AudioOut(
            turn_id=item.turn_id,
            turn_revision=item.turn_revision,
            generation=item.generation,
            pcm=item.text.encode("utf-8"),
        )


class VoicePipeline:
    """Wires VAD -> STT -> (bridge) -> LLM -> (bridge) -> TTS over queues and
    manages their background threads as one unit.

    ``audio_in`` is where raw PCM frames (or :data:`PIPELINE_END`) are fed in;
    ``audio_out`` is where synthesized :class:`AudioOut` (and forwarded
    :class:`EndOfResponse`) items land. Everything in between is internal
    plumbing a caller does not need to touch.
    """

    def __init__(
        self,
        *,
        cancel_scope: Optional[CancelScope] = None,
        vad_stage: Optional[BaseStage] = None,
        stt_stage: Optional[BaseStage] = None,
        llm_stage: Optional[BaseStage] = None,
        tts_stage: Optional[BaseStage] = None,
        vad_config: Optional[VADConfig] = None,
        llm_config: Optional[LLMStageConfig] = None,
        llm_stream_fn: Optional[StreamFn] = None,
    ) -> None:
        self.cancel_scope = cancel_scope or CancelScope()

        self.audio_in: "queue.Queue[Any]" = queue.Queue()
        vad_to_stt: "queue.Queue[Any]" = queue.Queue()
        stt_to_bridge: "queue.Queue[Any]" = queue.Queue()
        bridge_to_llm: "queue.Queue[Any]" = queue.Queue()
        llm_to_bridge: "queue.Queue[Any]" = queue.Queue()
        bridge_to_tts: "queue.Queue[Any]" = queue.Queue()
        self.audio_out: "queue.Queue[Any]" = queue.Queue()

        self.vad = vad_stage or VADStage(
            self.audio_in, [vad_to_stt], cancel_scope=self.cancel_scope, config=vad_config,
        )
        self.stt = stt_stage or EchoSTTStage(vad_to_stt, [stt_to_bridge])
        self.stt_bridge = TranscriptionToGenerate(stt_to_bridge, [bridge_to_llm])
        self.llm = llm_stage or LLMStage(
            bridge_to_llm, [llm_to_bridge],
            cancel_scope=self.cancel_scope, config=llm_config, stream_fn=llm_stream_fn,
        )
        self.llm_bridge = LLMChunkToTTSInput(llm_to_bridge, [bridge_to_tts])
        self.tts = tts_stage or EchoTTSStage(bridge_to_tts, [self.audio_out])

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
