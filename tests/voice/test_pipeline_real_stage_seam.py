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
3. the ``Echo*`` stubs remain the default when no config is given (backward
   compatible with ``tests/voice/test_pipeline_spine.py``)
   (:func:`test_default_pipeline_still_uses_echo_stubs`);
4. a message flows end-to-end through the REAL STT/TTS stages (fake
   ``stream_fn`` transports -- no network) with the config genuinely applied
   downstream (:func:`test_real_stt_and_tts_stages_flow_a_message_end_to_end`).

Dependency-light: fake ``stream_fn``s only, no real HTTP/socket, no GPU/torch.
"""
from __future__ import annotations

from anvil_serving.voice.messages import AudioOut, EndOfResponse
from anvil_serving.voice.pipeline import EchoSTTStage, EchoTTSStage, VoicePipeline, real_pipeline_kwargs_from_manifest
from anvil_serving.voice.stages.base import BaseStage
from anvil_serving.voice.stages.llm import LLMStageConfig
from anvil_serving.voice.stages.stt import STTStage, STTStageConfig
from anvil_serving.voice.stages.tts import TTSStage, TTSStageConfig
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


def test_stage_factories_are_wired_to_the_pipelines_own_queues():
    """Every one of the four ``*_stage_factory=`` seams is called with the
    exact queue objects the pipeline itself owns -- proof the queues are
    built BEFORE any stage (the bug this fix resolves)."""
    captured = {}

    def make_factory(key):
        def factory(in_q, out_qs):
            captured[key] = (in_q, list(out_qs))
            return _RecordingStage(in_q, out_qs)
        return factory

    pipeline = VoicePipeline(
        vad_stage_factory=make_factory("vad"),
        stt_stage_factory=make_factory("stt"),
        llm_stage_factory=make_factory("llm"),
        tts_stage_factory=make_factory("tts"),
    )

    # VAD reads the pipeline's own audio_in and feeds the SAME queue STT reads
    # from, PLUS the pipeline's own `vad_events` sideband (PUNCH-LIST #3 --
    # see VoicePipeline's class docstring): a fan-out duplicate a realtime
    # layer drains for SpeechEvent, not a second consumer of the primary path.
    assert captured["vad"][0] is pipeline.audio_in
    assert captured["vad"][1] == [captured["stt"][0], pipeline.vad_events]

    # STT's output queue feeds the stt_bridge, whose output is what LLM reads
    # from -- i.e. STT's out queue and LLM's in queue are connected through
    # the (non-injectable) TranscriptionToGenerate bridge, not directly equal.
    assert pipeline.stt_bridge.in_queue is captured["stt"][1][0]
    assert pipeline.stt_bridge.out_queues == [captured["llm"][0]]

    # Same shape on the LLM -> TTS side, through llm_bridge.
    assert pipeline.llm_bridge.in_queue is captured["llm"][1][0]
    assert pipeline.llm_bridge.out_queues == [captured["tts"][0]]

    # TTS's output queue IS the pipeline's own audio_out.
    assert captured["tts"][1] == [pipeline.audio_out]

    # Every constructed stage is reachable off the pipeline exactly where a
    # caller expects it.
    assert pipeline.vad.in_queue is pipeline.audio_in
    assert pipeline.stt.in_queue is captured["vad"][1][0]
    assert pipeline.llm.in_queue is captured["llm"][0]
    assert pipeline.tts.out_queues == [pipeline.audio_out]

    pipeline.shutdown_gracefully(join_timeout=1.0)


def test_stt_config_switches_in_the_real_stage_with_config_applied():
    config = STTStageConfig(base_url="http://127.0.0.1:9/v1", model="whisper-real")
    pipeline = VoicePipeline(stt_config=config)
    try:
        assert isinstance(pipeline.stt, STTStage)
        assert pipeline.stt.config is config
        # The real stage is wired to the SAME internal queue the VAD stage
        # feeds -- not a disconnected instance built on the wrong queues.
        assert pipeline.stt.in_queue is pipeline.vad.out_queues[0]
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_tts_config_switches_in_the_real_stage_with_config_applied():
    config = TTSStageConfig(base_url="http://127.0.0.1:9/v1", model="kokoro-real")
    pipeline = VoicePipeline(tts_config=config)
    try:
        assert isinstance(pipeline.tts, TTSStage)
        assert pipeline.tts.config is config
        assert pipeline.tts.out_queues == [pipeline.audio_out]
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


def test_manifest_pipeline_kwargs_preserve_stage_runtime_knobs():
    kwargs = real_pipeline_kwargs_from_manifest({
        "voice": {
            "stt": {
                "base_url": "http://127.0.0.1:30010/v1",
                "model": "mlx-community/whisper-tiny-asr-fp16",
                "stream": False,
                "response_format": "json",
                "timeout": 12.5,
                "lifecycle": "external",
            },
            "llm": {
                "base_url": "http://127.0.0.1:8000/v1",
                "model": "fast-local",
                "stream": True,
                "timeout": 33.0,
            },
            "tts": {
                "base_url": "http://127.0.0.1:30011/v1",
                "model": "mlx-community/Kokoro-82M-bf16",
                "response_format": "pcm",
                "source_sample_rate": 24000,
                "target_sample_rate": 16000,
                "chunk_bytes": 2048,
                "timeout": 44.0,
                "lifecycle": "external",
            },
        }
    })

    assert kwargs["stt_config"].stream is False
    assert kwargs["stt_config"].response_format == "json"
    assert kwargs["stt_config"].timeout == 12.5
    assert kwargs["llm_config"].timeout == 33.0
    assert kwargs["tts_config"].response_format == "pcm"
    assert kwargs["tts_config"].source_sample_rate == 24000
    assert kwargs["tts_config"].target_sample_rate == 16000
    assert kwargs["tts_config"].chunk_bytes == 2048
    assert kwargs["tts_config"].timeout == 44.0


def test_default_pipeline_still_uses_echo_stubs():
    """No config given -> the deterministic stubs (backward compatible with
    the existing spine tests)."""
    pipeline = VoicePipeline()
    try:
        assert isinstance(pipeline.stt, EchoSTTStage)
        assert isinstance(pipeline.tts, EchoTTSStage)
    finally:
        pipeline.shutdown_gracefully(join_timeout=1.0)


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
        llm_config=LLMStageConfig(model="chat-fast", base_url="http://127.0.0.1:9/v1"),
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
    assert audio_items, f"expected at least one real-stage AudioOut, got: {items}"
    assert end_items, f"expected an EndOfResponse, got: {items}"
    # Every produced AudioOut chunk carries the REAL TTS stage's resampled bytes.
    assert all(a.pcm for a in audio_items)
