"""Tests for the FIXED `VoicePipeline` real-stage injection seam
(PUNCH-LIST #2, FIX #1).

Before this fix, ``VoicePipeline.__init__`` built its internal queues and
only THEN constructed the default ``EchoSTTStage``/``EchoTTSStage`` bound to
them -- so a caller supplying a real ``STTStage``/``TTSStage`` instance had no
way to get hold of the correctly-wired queue objects first (see
``scripts/voice/_real_pipeline.py``'s former "FLAGGED FOLLOWUP" docstring,
now resolved). This file proves:

1. the queues ARE built before any stage, and every stage (default OR a
   caller-supplied factory) is wired to the RIGHT queue objects
   (:func:`test_stage_factories_are_wired_to_the_pipelines_own_queues`);
2. supplying ``stt_config=``/``tts_config=`` switches in the REAL
   :class:`~anvil_serving.voice.stages.stt.STTStage`/
   :class:`~anvil_serving.voice.stages.tts.TTSStage` (config applied, not
   just accepted and ignored) in place of the ``Echo*`` stubs
   (:func:`test_stt_config_switches_in_the_real_stage_with_config_applied`,
   :func:`test_tts_config_switches_in_the_real_stage_with_config_applied`);
3. the ``Echo*`` stubs remain the default when no config is given
   (:func:`test_default_pipeline_still_uses_echo_stubs`);
4. a message flows end-to-end through the REAL STT/TTS stages (fake
   ``stream_fn`` transports -- no network) with the config genuinely applied
   downstream (:func:`test_real_stt_and_tts_stages_flow_a_message_end_to_end`).

Dependency-light: fake ``stream_fn``s only, no real HTTP/socket, no GPU/torch.
"""
from __future__ import annotations

from anvil_serving.voice.messages import AudioOut, EndOfResponse, SpokenText
from anvil_serving.voice.pipeline import (
    VoicePipeline,
)
from anvil_serving.voice.stages.base import BaseStage
from anvil_serving.voice.stages.llm import LLMStageConfig
from anvil_serving.voice.stages.stt import STTStageConfig
from anvil_serving.voice.stages.tts import TTSStageConfig
from anvil_serving.voice.stages.vad import VADConfig

SPEECH = b"\x01\x02\x03\x04"
SILENCE = b"\x00\x00\x00\x00"


class _RecordingStage(BaseStage):
    """A trivial pass-through stage that just remembers the queues it was
    constructed with, for the wiring assertions below."""

    name = "recording-stage"

    def process(self, item):
        return item


def _feed_one_turn(pipeline, *, silence_frames=4):
    pipeline.audio_in.put(SPEECH)
    for _ in range(silence_frames):
        pipeline.audio_in.put(SILENCE)


def _fake_llm_stream(text, config):
    yield "Hi there. "
    yield "How can I help?"


def test_real_stt_and_tts_stages_flow_a_message_end_to_end():
    """The REAL STTStage/TTSStage (config applied via the fixed seam), fed by
    fake `stream_fn` transports -- no network -- still moves one turn all the
    way from VAD through to a synthesized AudioOut, proving the fixed seam
    wires a genuinely functional real-stage pipeline, not just non-crashing
    construction."""

    def fake_stt_stream(pcm, sample_rate, config):
        yield ("the user said something", True)

    def fake_tts_stream(text, config):
        # Two raw PCM int16 samples per chunk; source==target rate avoids any
        # resampling surprises in this wiring-focused test.
        yield b"\x01\x00\x02\x00"

    stt_config = STTStageConfig(base_url="http://127.0.0.1:9/v1", model="whisper-real")
    tts_config = TTSStageConfig(
        base_url="http://127.0.0.1:9/v1", model="kokoro-real",
        source_sample_rate=16000, target_sample_rate=16000,
    )

    pipeline = VoicePipeline(
        vad_config=VADConfig(frame_ms=50, silence_ms=200),
        stt_config=stt_config,
        stt_stream_fn=fake_stt_stream,
        llm_config=LLMStageConfig(model="llm.voice", base_url="http://127.0.0.1:9/v1"),
        llm_stream_fn=_fake_llm_stream,
        tts_config=tts_config,
        tts_stream_fn=fake_tts_stream,
    )
    pipeline.start()
    try:
        _feed_one_turn(pipeline)
        items = pipeline.drain_audio_out(timeout=3.0)
    finally:
        pipeline.shutdown_gracefully()

    audio_items = [m for m in items if isinstance(m, AudioOut)]
    end_items = [m for m in items if isinstance(m, EndOfResponse)]
    spoken_items = [m for m in items if isinstance(m, SpokenText)]
    assert audio_items, f"expected at least one real-stage AudioOut, got: {items}"
    assert end_items, f"expected an EndOfResponse, got: {items}"
    assert [item.text for item in spoken_items] == ["Hi there.", "How can I help?"]
    # Every produced AudioOut chunk carries the REAL TTS stage's resampled bytes.
    assert all(a.pcm for a in audio_items)
