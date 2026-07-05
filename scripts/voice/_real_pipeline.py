"""Shared helper: a VAD -> STT -> LLM -> TTS pipeline wired from the REAL
out-of-process stages, for the live-hardware harness scripts in this package
(``local_loop_demo.py`` T010, ``realtime_sdk_client_demo.py`` T014,
``mini_validation.py`` T016). Not itself a "RUN ON fakoli-dark" entry point --
just shared wiring; import-safe anywhere (stdlib + the router/voice modules
only, no torch/sounddevice/openai).

FLAGGED FOLLOWUP (fix-it-or-flag-it -- see root CLAUDE.md's golden rule):
:class:`~anvil_serving.voice.pipeline.VoicePipeline` already accepts
``stt_stage=``/``tts_stage=`` constructor parameters that LOOK like the
intended seam for plugging in the real :class:`~anvil_serving.voice.stages.stt.STTStage`/
:class:`~anvil_serving.voice.stages.tts.TTSStage` in place of its
``EchoSTTStage``/``EchoTTSStage`` stubs. In practice that seam cannot be used
correctly: ``VoicePipeline.__init__`` creates its internal
``vad_to_stt``/``stt_to_bridge``/``bridge_to_tts`` queues ITSELF and only
then constructs the default stub with them --

    self.stt = stt_stage or EchoSTTStage(vad_to_stt, [stt_to_bridge])

-- so a caller wanting to pass a real ``STTStage`` would need to already have
those queue objects to build one bound to them, but they are created inside
``__init__`` and never exposed beforehand. There is today no legitimate way to
construct a caller-supplied ``stt_stage``/``tts_stage`` that is correctly
wired -- passing a stage built on the wrong queue objects would silently not
receive/emit anything to the rest of the pipeline. This was left for
"a later unit" per that module's own docstring, and this unit (T010) is
arguably that later unit -- but ``anvil_serving/voice/pipeline.py`` is a
SHARED file outside this unit's assigned file list in a shared worktree,
so rather than editing it here (risking a collision with whatever else is
touching it concurrently), :class:`RealVoicePipeline` below duplicates
``VoicePipeline``'s wiring, substituting the real audio stages for the
Echo stubs. The clean upstream fix would be adding ``stt_config=``/
``tts_config=`` (and ``stt_stream_fn=``/``tts_stream_fn=``) constructor
parameters to ``VoicePipeline`` itself, mirroring the ``llm_config=``/
``llm_stream_fn=`` pattern it already has for the LLM stage -- at which
point this module could shrink to a thin wrapper (or be deleted outright in
favor of ``VoicePipeline`` directly). Left as a followup, not silently
patched around without comment.
"""
from __future__ import annotations

import queue
from array import array
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.pipeline import LLMChunkToTTSInput, TranscriptionToGenerate
from anvil_serving.voice.stages.base import PIPELINE_END, ThreadManager
from anvil_serving.voice.stages.llm import LLMStage, LLMStageConfig
from anvil_serving.voice.stages.stt import STTStage, STTStageConfig
from anvil_serving.voice.stages.tts import TTSStage, TTSStageConfig
from anvil_serving.voice.stages.vad import FakeVADModel, VADConfig, VADStage


class SimpleEnergyVADModel:
    """A crude energy-threshold stand-in for a real acoustic VAD model.

    :class:`~anvil_serving.voice.stages.vad.VADStage`'s own shipped default,
    :class:`~anvil_serving.voice.stages.vad.FakeVADModel`, classifies ANY
    frame containing a single nonzero byte as speech -- fine for a
    deterministic unit test, useless against a real microphone's noise floor
    (every frame would read as "speech"). This computes a plain RMS-over-frame
    energy and thresholds it -- NOT Silero, NOT any ML model, no training, no
    adaptive noise-floor tracking. Good enough to drive turn-taking against
    real audio for a first cut. A real acoustic detector (Silero VAD via
    onnxruntime, per ``stages/vad.py``'s own module-docstring TODO) is a
    follow-up, intentionally not shipped here.
    """

    def __init__(self, threshold: float = 500.0) -> None:
        self.threshold = threshold

    def is_speech(self, frame: bytes) -> bool:
        if not frame:
            return False
        samples = array("h")
        samples.frombytes(frame[: len(frame) - (len(frame) % 2)])
        if not samples:
            return False
        mean_sq = sum(s * s for s in samples) / len(samples)
        return (mean_sq ** 0.5) >= self.threshold


@dataclass
class RealPipelineConfig:
    """Stage configs for one :class:`RealVoicePipeline` instance."""

    vad: VADConfig
    stt: STTStageConfig
    llm: LLMStageConfig
    tts: TTSStageConfig
    vad_model: Optional[Any] = None  # None -> FakeVADModel (see stages/vad.py); pass SimpleEnergyVADModel for real mic input


def stage_config_from_manifest_table(table: Mapping[str, Any], cls: type) -> Any:
    """Build an ``STTStageConfig``/``LLMStageConfig``/``TTSStageConfig`` from a
    validated voice-manifest ``[voice.<stt|llm|tts>]`` table (see
    ``anvil_serving/voice/config.py``)."""
    kwargs: Dict[str, Any] = {"base_url": table["base_url"], "model": table["model"]}
    if table.get("api_key_env"):
        kwargs["api_key_env"] = table["api_key_env"]
    if "stream" in table and hasattr(cls, "stream"):
        kwargs["stream"] = table["stream"]
    return cls(**kwargs)


def real_pipeline_config_from_manifest(
    data: Mapping[str, Any], *, vad_config: Optional[VADConfig] = None, vad_model: Optional[Any] = None,
) -> RealPipelineConfig:
    """Build a :class:`RealPipelineConfig` from a loaded+validated voice manifest."""
    voice = data["voice"]
    return RealPipelineConfig(
        vad=vad_config or VADConfig(),
        stt=stage_config_from_manifest_table(voice["stt"], STTStageConfig),
        llm=stage_config_from_manifest_table(voice["llm"], LLMStageConfig),
        tts=stage_config_from_manifest_table(voice["tts"], TTSStageConfig),
        vad_model=vad_model,
    )


class RealVoicePipeline:
    """VAD -> STT -> (bridge) -> LLM -> (bridge) -> TTS wired from the REAL
    out-of-process stages, duck-type-compatible with
    :class:`anvil_serving.voice.pipeline.VoicePipeline` (same ``audio_in``/
    ``audio_out``/``cancel_scope``/``llm.in_queue``/``start``/``stop``/
    ``shutdown_gracefully`` surface, so it drops into
    :class:`anvil_serving.voice.realtime.pool.SessionPool`'s
    ``pipeline_factory`` seam unchanged) -- see the module docstring for why
    this duplicates ``VoicePipeline`` instead of using its
    ``stt_stage=``/``tts_stage=`` params.
    """

    def __init__(self, config: RealPipelineConfig, *, cancel_scope: Optional[CancelScope] = None) -> None:
        self.cancel_scope = cancel_scope or CancelScope()

        self.audio_in: "queue.Queue[Any]" = queue.Queue()
        vad_to_stt: "queue.Queue[Any]" = queue.Queue()
        stt_to_bridge: "queue.Queue[Any]" = queue.Queue()
        bridge_to_llm: "queue.Queue[Any]" = queue.Queue()
        llm_to_bridge: "queue.Queue[Any]" = queue.Queue()
        bridge_to_tts: "queue.Queue[Any]" = queue.Queue()
        self.audio_out: "queue.Queue[Any]" = queue.Queue()

        self.vad = VADStage(
            self.audio_in, [vad_to_stt],
            cancel_scope=self.cancel_scope, config=config.vad,
            model=config.vad_model or FakeVADModel(),
        )
        self.stt = STTStage(vad_to_stt, [stt_to_bridge], cancel_scope=self.cancel_scope, config=config.stt)
        self.stt_bridge = TranscriptionToGenerate(stt_to_bridge, [bridge_to_llm])
        self.llm = LLMStage(bridge_to_llm, [llm_to_bridge], cancel_scope=self.cancel_scope, config=config.llm)
        self.llm_bridge = LLMChunkToTTSInput(llm_to_bridge, [bridge_to_tts])
        self.tts = TTSStage(bridge_to_tts, [self.audio_out], cancel_scope=self.cancel_scope, config=config.tts)

        self.manager = ThreadManager(
            [self.vad, self.stt, self.stt_bridge, self.llm, self.llm_bridge, self.tts]
        )

    def start(self) -> None:
        self.manager.start_all()

    def stop(self, *, join_timeout: Optional[float] = 2.0) -> None:
        self.manager.stop_all(join_timeout=join_timeout)

    def shutdown_gracefully(self, *, join_timeout: Optional[float] = 2.0) -> None:
        self.audio_in.put(PIPELINE_END)
        self.manager.stop_all(join_timeout=join_timeout)

    def drain_audio_out(self, *, timeout: float = 2.0):
        items = []
        try:
            while True:
                items.append(self.audio_out.get(timeout=timeout))
                timeout = 0.2
        except queue.Empty:
            return items
