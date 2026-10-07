from __future__ import annotations

import io
import json

import pytest

from anvil_serving.voice import config, tts_benchmark
from anvil_serving.voice.pipeline import real_pipeline_factory_from_manifest
from anvil_serving.voice.stages.tts import TTSStageConfig, build_speech_request_body, stream_speech


def _manifest(stream):
    return {"voice": {
        "llm": {"base_url": "http://127.0.0.1:8000/v1", "model": "llm.voice"},
        "stt": {"base_url": "http://127.0.0.1:8090/v1", "model": "stt"},
        "tts": {"base_url": "http://127.0.0.1:8091/v1", "model": "fish", "stream": stream},
    }}


@pytest.mark.parametrize("stream", [True, False])
def test_manifest_stream_setting_reaches_real_pipeline_request(stream):
    resolved = config.resolve_manifest_data(_manifest(stream))
    pipeline = real_pipeline_factory_from_manifest(resolved.data)()
    payload = []

    def transport(url, *, data, headers, timeout):
        payload.append(json.loads(data))
        return io.BytesIO(b"\x01\0" * 120)

    try:
        assert b"".join(stream_speech("Please speak.", pipeline.tts.config, transport=transport))
    finally:
        pipeline.stop()
    assert payload[0]["stream"] is stream


def test_stream_defaults_true_and_manifest_rejects_nonboolean():
    assert build_speech_request_body("hello", TTSStageConfig())["stream"] is True
    with pytest.raises(config.ConfigError, match="true or false"):
        config.resolve_manifest_data(_manifest("false"))


def test_declared_openai_voice_and_language_survive_live_pipeline_configuration():
    data = _manifest(False)
    data["voice"]["tts"].update(voice_id="selected-voice", language="en")
    pipeline = real_pipeline_factory_from_manifest(config.resolve_manifest_data(data).data)()
    try:
        body = build_speech_request_body("Hello.", pipeline.tts.config)
    finally:
        pipeline.stop()
    assert body == {"model": "fish", "input": "Hello.", "response_format": "pcm",
                    "stream": False, "voice": "selected-voice", "language": "en"}


@pytest.mark.parametrize("stream", [True, False])
def test_benchmark_records_exact_stream_request_and_buffering_contract(tmp_path, stream):
    manifest = tmp_path / "corpus.jsonl"
    manifest.write_text(json.dumps({"schema_version": "tts-corpus/v1", "id": "hello",
                                    "text": "Hello.", "category": "short", "language": "en"}) + "\n")
    payload = []

    class Response(io.BytesIO):
        status = 200
        headers = {"Content-Type": "application/octet-stream"}

    def transport(url, *, data, headers, timeout):
        payload.append(json.loads(data))
        return Response(b"\x01\0" * 120)

    evidence = tts_benchmark.run_tts_benchmark(
        manifest, config=TTSStageConfig(stream=stream), audio_out=tmp_path / "audio",
        repetitions=1, transport=transport,
    )
    assert evidence["complete"] is True
    assert all(body["stream"] is stream for body in payload)
    assert evidence["configuration"]["resolved_tts"]["stream"] is stream
    assert evidence["endpoint"]["stream"] is stream
    assert evidence["measurement_scope"]["upstream_stream_requested"] is stream
    if not stream:
        assert evidence["measurement_scope"]["upstream_buffering"] == "buffered-request; first audio may follow full synthesis"


@pytest.mark.parametrize("max_tokens", [None, 1, 750, 8192])
def test_explicit_generation_cap_has_live_benchmark_and_evidence_parity(tmp_path, max_tokens):
    from anvil_serving.voice.cli import _stage_config

    base = _manifest(False)
    overlay = {"tts": {"max_tokens": max_tokens}} if max_tokens is not None else None
    resolved = config.resolve_manifest_data(base, candidate_overlay=overlay)
    assert "max_tokens" not in base["voice"]["tts"]
    pipeline = real_pipeline_factory_from_manifest(resolved.data)()
    try:
        live_body = build_speech_request_body("Hello.", pipeline.tts.config)
    finally:
        pipeline.stop()
    if max_tokens is None:
        assert "max_tokens" not in live_body
    else:
        assert live_body["max_tokens"] == max_tokens

    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(json.dumps({"schema_version": "tts-corpus/v1", "id": "hello",
                                  "text": "Hello.", "category": "short", "language": "en"}) + "\n")
    payload = []

    class Response(io.BytesIO):
        status = 200
        headers = {"Content-Type": "audio/pcm"}

    def transport(url, *, data, headers, timeout):
        payload.append(json.loads(data))
        return Response(b"\x01\0" * 120)

    evidence = tts_benchmark.run_tts_benchmark(
        corpus, config=_stage_config(resolved.data["voice"]["tts"], TTSStageConfig),
        audio_out=tmp_path / "audio", repetitions=1, transport=transport,
    )
    assert evidence["complete"] is True
    assert payload == [live_body, live_body]
    assert evidence["configuration"]["resolved_tts"]["max_tokens"] == max_tokens


@pytest.mark.parametrize("value", [True, False, 0, -1, 8193, 750.0, "750"])
def test_generation_cap_rejects_invalid_manifest_overlay_and_direct_config(value):
    data = _manifest(False)
    data["voice"]["tts"]["max_tokens"] = value
    with pytest.raises(config.ConfigError, match="max_tokens"):
        config.resolve_manifest_data(data)
    with pytest.raises(config.ConfigError, match="max_tokens"):
        config.resolve_manifest_data(_manifest(False), candidate_overlay={"tts": {"max_tokens": value}})
    with pytest.raises(ValueError, match="max_tokens"):
        TTSStageConfig(max_tokens=value)
