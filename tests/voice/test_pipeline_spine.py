"""End-to-end spine test: a message flows VAD -> STT(stub) -> LLM -> TTS(stub).

Dependency-light: the LLM stage is wired with a fake ``stream_fn`` (no real
HTTP/socket); STT and TTS are the deterministic stubs shipped in
``pipeline.py``. No GPU, no torch, no network -- this proves the STAGE WIRING
and MESSAGE FLOW, not any real audio/inference behavior (see the module
docstrings' honesty notes in ``pipeline.py``/``stages/vad.py``).
"""
from __future__ import annotations

from anvil_serving.voice.messages import AudioOut, EndOfResponse
from anvil_serving.voice.pipeline import VoicePipeline
from anvil_serving.voice.stages.llm import LLMStageConfig
from anvil_serving.voice.stages.vad import VADConfig

SPEECH = b"\x01\x02\x03\x04"
SILENCE = b"\x00\x00\x00\x00"


def _fake_stream(text, config):
    yield "Hi there. "
    yield "How can I help?"


def _feed_one_turn(pipeline, *, silence_frames=4):
    pipeline.audio_in.put(SPEECH)
    for _ in range(silence_frames):
        pipeline.audio_in.put(SILENCE)


def test_message_flows_end_to_end_through_every_stage():
    pipeline = VoicePipeline(
        vad_config=VADConfig(frame_ms=50, silence_ms=200),  # 4 silent frames end a turn
        llm_stream_fn=_fake_stream,
    )
    pipeline.start()
    try:
        _feed_one_turn(pipeline)
        items = pipeline.drain_audio_out(timeout=3.0)
    finally:
        pipeline.shutdown_gracefully()

    audio_items = [m for m in items if isinstance(m, AudioOut)]
    end_items = [m for m in items if isinstance(m, EndOfResponse)]

    assert audio_items, f"expected at least one AudioOut, got: {items}"
    assert end_items, f"expected an EndOfResponse, got: {items}"

    # The stub TTS "synthesizes" by UTF-8 encoding the (sentence-batched, TTS-
    # cleaned) text -- decoding it back proves the real sentence text made it
    # all the way from the fake LLM stream through to the pipeline's output.
    texts = [a.pcm.decode("utf-8") for a in audio_items]
    assert texts == ["Hi there.", "How can I help?"]

    # Every message from one turn shares the same turn_id.
    turn_ids = {m.turn_id for m in items}
    assert len(turn_ids) == 1


def test_pipeline_end_sentinel_shuts_down_cleanly():
    pipeline = VoicePipeline(
        vad_config=VADConfig(frame_ms=50, silence_ms=200),
        llm_stream_fn=_fake_stream,
    )
    pipeline.start()
    assert pipeline.manager.all_alive()
    pipeline.shutdown_gracefully(join_timeout=3.0)
    assert not pipeline.manager.all_alive()


def test_stt_stub_is_deterministic_and_ignores_non_vad_audio():
    from anvil_serving.voice.messages import VADAudio
    from anvil_serving.voice.pipeline import EchoSTTStage

    stage = EchoSTTStage(in_queue=None, fixed_text="hello there")
    assert stage.process("not vad audio") is None
    out = stage.process(VADAudio(turn_id="t", turn_revision=0, generation=0, pcm=b"x"))
    assert out.text == "hello there"
    assert out.is_final is True


def test_llm_stage_config_can_be_injected_via_pipeline():
    pipeline = VoicePipeline(
        vad_config=VADConfig(frame_ms=50, silence_ms=200),
        llm_config=LLMStageConfig(model="chat-fast", base_url="http://127.0.0.1:9/v1"),
        llm_stream_fn=_fake_stream,
    )
    assert pipeline.llm.config.model == "chat-fast"
    pipeline.shutdown_gracefully(join_timeout=0.5)
