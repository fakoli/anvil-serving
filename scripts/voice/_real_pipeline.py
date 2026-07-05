"""Shared helper: a VAD -> STT -> LLM -> TTS pipeline wired from the REAL
out-of-process stages, for the live-hardware harness scripts in this package
(``local_loop_demo.py`` T010, ``realtime_sdk_client_demo.py`` T014,
``mini_validation.py`` T016). Not itself a "RUN ON fakoli-dark" entry point --
just shared wiring; import-safe anywhere (stdlib + the router/voice modules
only, no torch/sounddevice/openai).

RESOLVED FOLLOWUP (was flagged here as fix-it-or-flag-it -- see root
CLAUDE.md's golden rule): this module used to duplicate ALL of
``anvil_serving.voice.pipeline.VoicePipeline``'s queue-wiring in a standalone
``RealVoicePipeline`` class, because ``VoicePipeline``'s old ``stt_stage=``/
``tts_stage=`` constructor params could not be used correctly (they were
constructed from queue objects the caller never had access to -- see the old
version of this module's docstring / git history for the full writeup).
``anvil_serving/voice/pipeline.py`` has since gained the fix that followup
called for: ``stt_config=``/``tts_config=``/``vad_model=`` constructor
parameters (mirroring the ``llm_config=`` pattern it already had), with the
internal queues built BEFORE any stage -- so :class:`RealVoicePipeline` below
is now just a thin, import-compatible wrapper around
:class:`anvil_serving.voice.pipeline.VoicePipeline` instead of a second copy
of its wiring.
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.pipeline import VoicePipeline, stage_config_from_table
from anvil_serving.voice.stages.llm import LLMStageConfig
from anvil_serving.voice.stages.stt import STTStageConfig
from anvil_serving.voice.stages.tts import TTSStageConfig
from anvil_serving.voice.stages.vad import FakeVADModel, VADConfig


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
    ``anvil_serving/voice/config.py``).

    Kept here as a thin re-export (the real implementation is
    ``anvil_serving.voice.pipeline.stage_config_from_table`` now) so existing
    imports of this name keep working.
    """
    return stage_config_from_table(table, cls)


def real_pipeline_config_from_manifest(
    data: Mapping[str, Any], *, vad_config: Optional[VADConfig] = None, vad_model: Optional[Any] = None,
) -> RealPipelineConfig:
    """Build a :class:`RealPipelineConfig` from a loaded+validated voice manifest."""
    voice = data["voice"]
    return RealPipelineConfig(
        vad=vad_config or VADConfig(),
        stt=stage_config_from_table(voice["stt"], STTStageConfig),
        llm=stage_config_from_table(voice["llm"], LLMStageConfig),
        tts=stage_config_from_table(voice["tts"], TTSStageConfig),
        vad_model=vad_model,
    )


class RealVoicePipeline(VoicePipeline):
    """VAD -> STT -> (bridge) -> LLM -> (bridge) -> TTS wired from the REAL
    out-of-process stages, duck-type-compatible with
    :class:`anvil_serving.voice.pipeline.VoicePipeline` (it now literally IS
    one -- see the module docstring) -- so it drops into
    :class:`anvil_serving.voice.realtime.pool.SessionPool`'s
    ``pipeline_factory`` seam unchanged.

    Kept as a thin, import-compatible wrapper for the harness scripts in this
    package (``local_loop_demo.py``, ``realtime_sdk_client_demo.py``) that
    already construct it from a :class:`RealPipelineConfig`; new callers
    should prefer ``anvil_serving.voice.pipeline.real_pipeline_factory_from_manifest``
    directly.
    """

    def __init__(self, config: RealPipelineConfig, *, cancel_scope: Optional[CancelScope] = None) -> None:
        super().__init__(
            cancel_scope=cancel_scope,
            vad_config=config.vad,
            vad_model=config.vad_model or FakeVADModel(),
            stt_config=config.stt,
            llm_config=config.llm,
            tts_config=config.tts,
        )
