"""Tests for `anvil_serving.voice.benchmark` -- TTFA / turn latency / STT
WER-sample / TTS RTF (anvil task T015).

Dependency-light and hermetic: every STT/LLM/TTS call is either a fake
`*_stream_fn` (pure measurement-math tests) or a fake `*_transport` (proving
the real wire calls compose end-to-end) -- no socket is ever opened, no
GPU/torch/real audio anywhere. Clock values are injected so timing assertions
are deterministic.
"""
from __future__ import annotations

import hashlib
import json
import struct

import pytest

from anvil_serving.voice.benchmark import (
    DEFAULT_REFERENCE_TEXT,
    MAX_INPUT_WAV_BYTES,
    MEASUREMENT_SCOPE,
    load_benchmark_input_wav,
    run_audio_benchmark,
    run_benchmark,
    run_benchmark_from_manifest,
)
from anvil_serving.voice.stages.llm import LLMStageConfig
from anvil_serving.voice.stages.stt import STTStageConfig
from anvil_serving.voice.stages.tts import TTSStageConfig
from tests.voice.conftest import FakeLineResponse as _FakeLineResponse
from tests.voice.conftest import FakeReadResponse as _FakeReadResponse


def _pcm16_wav(pcm: bytes, *, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Build a minimal PCM WAV fixture without relying on a codec library."""
    block_align = channels * 2
    fmt = struct.pack(
        "<HHIIHH", 1, channels, sample_rate, sample_rate * block_align, block_align, 16
    )
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(pcm)) + pcm
    if len(pcm) % 2:
        body += b"\x00"
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


def test_audio_benchmark_measures_tts_to_stt_without_llm():
    clock_values = iter([1.0, 1.2, 1.5])

    result = run_audio_benchmark(
        stt_config=STTStageConfig(base_url="http://127.0.0.1:30010/v1", model="stt"),
        tts_config=TTSStageConfig(
            base_url="http://127.0.0.1:30011/v1",
            model="tts",
            source_sample_rate=16000,
        ),
        tts_stream_fn=lambda text, config: iter([b"\x00\x00" * 1600]),
        stt_stream_fn=lambda pcm, sample_rate, config: iter([
            (DEFAULT_REFERENCE_TEXT, True)
        ]),
        clock=lambda: next(clock_values),
        profile="dark-audio",
    )

    assert result["scope"] == "audio"
    assert result["audio_roundtrip_ms"] == 500.0
    assert result["tts_ms"] == 200.0
    assert result["stt_ms"] == 300.0
    assert result["tts_output_bytes"] == 3200
    assert result["stt_wer"] == 0.0
    assert result["evidence"]["evidence_scope"] == "voice-pipeline"
    assert result["evidence"]["promotion_quality_evidence"] is False


# --------------------------------------------------------------------------- #
# word_error_rate
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# synth_sample_pcm
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# run_benchmark: measurement math (fake stream_fns, injected clock)
# --------------------------------------------------------------------------- #
def _clock_sequence(values):
    it = iter(values)
    return lambda: next(it)


# --------------------------------------------------------------------------- #
# run_benchmark_from_manifest: builds stage configs from manifest tables
# --------------------------------------------------------------------------- #


def test_load_benchmark_input_wav_and_retain_identity_from_pcm_bytes(tmp_path):
    pcm = b"\x01\x00" * 160
    source = tmp_path / "speech.wav"
    source.write_bytes(_pcm16_wav(pcm))

    benchmark_input = load_benchmark_input_wav(str(source))

    assert benchmark_input.pcm == pcm
    assert benchmark_input.sample_rate == 16000
    assert benchmark_input.identity["input_kind"] == "provided-wav"
    assert benchmark_input.identity["qualification"] == "supplied-content-unverified"
    assert benchmark_input.identity["sample_sha256"] == hashlib.sha256(pcm).hexdigest()
    assert benchmark_input.identity["sample_bytes"] == len(pcm)
    assert benchmark_input.identity["source_wav_sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    assert benchmark_input.identity["source_wav_bytes"] == len(source.read_bytes())
    assert benchmark_input.identity["audio_format"] == "wav-pcm-s16le-mono-16000hz"
    assert benchmark_input.identity["duration_seconds"] == 0.01

    result = run_benchmark_from_manifest(
        {"voice": {"stt": {}, "llm": {}, "tts": {}}},
        pcm=benchmark_input.pcm,
        sample_rate=benchmark_input.sample_rate,
        reference_text="spoken words",
        input_identity=benchmark_input.identity,
        stt_stream_fn=lambda pcm, sample_rate, config: iter([("spoken words", True)]),
        llm_stream_fn=lambda text, config: iter(["reply"]),
        tts_stream_fn=lambda text, config: iter(()),
    )

    assert result["input"]["sample_sha256"] == hashlib.sha256(pcm).hexdigest()
    assert result["input"]["reference_text_sha256"] == hashlib.sha256(
        b"spoken words"
    ).hexdigest()
    assert result["evidence"]["runs"][0]["input"] == result["input"]
    assert result["measurement_scope"] == MEASUREMENT_SCOPE
    assert result["evidence"]["measurement_scope"] == MEASUREMENT_SCOPE


def test_load_benchmark_input_wav_rejects_symlink_nonregular_and_oversized_files(tmp_path):
    target = tmp_path / "target.wav"
    target.write_bytes(_pcm16_wav(b"\x00\x00"))
    link = tmp_path / "link.wav"
    link.symlink_to(target)
    directory = tmp_path / "directory.wav"
    directory.mkdir()
    oversized = tmp_path / "oversized.wav"
    oversized.write_bytes(b"x" * (MAX_INPUT_WAV_BYTES + 1))

    for path, error in ((link, "symbolic link"), (directory, "regular file"), (oversized, "exceeds")):
        with pytest.raises(ValueError, match=error):
            load_benchmark_input_wav(str(path))


# --------------------------------------------------------------------------- #
# to_json
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# End-to-end wire composition: real transcribe_stream/stream_chat_completion/
# stream_speech via fake transports (no fake stream_fns) -- proves the three
# modules actually compose through run_benchmark, not just the measurement math.
# --------------------------------------------------------------------------- #
class _FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, *, data, headers, timeout):
        self.calls.append(url)
        return self.response


def test_run_benchmark_composes_real_stt_llm_tts_wire_calls_via_fake_transports():
    stt_transport = _FakeTransport(_FakeReadResponse(json.dumps({"text": "hello there"}).encode()))
    llm_chunk = {"choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": "stop"}]}
    llm_payload = (b"data: " + json.dumps(llm_chunk).encode() + b"\n\n" + b"data: [DONE]\n\n")
    llm_transport = _FakeTransport(_FakeLineResponse(llm_payload))
    tts_transport = _FakeTransport(_FakeReadResponse(b"\x00\x01\x02\x03"))

    result = run_benchmark(
        stt_config=STTStageConfig(stream=False),
        llm_config=LLMStageConfig(),
        tts_config=TTSStageConfig(source_sample_rate=16000, chunk_bytes=2),
        pcm=b"\x00\x00" * 50, sample_rate=16000,
        reference_text="hello there",
        stt_transport=stt_transport, llm_transport=llm_transport, tts_transport=tts_transport,
    )

    assert result["stt_hypothesis"] == "hello there"
    assert result["llm_reply"] == "hi"
    assert result["stt_wer"] == 0.0
    assert result["tts_rtf"] is not None
    assert result["tts_first_audio_observed"] is True
    assert result["tts_output_bytes"] == 4
    assert result["ttfa_ms"] >= 0
    assert result["turn_latency_ms"] >= result["ttfa_ms"]
    assert stt_transport.response.closed
    assert llm_transport.response.closed
    assert tts_transport.response.closed
