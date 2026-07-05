"""Tests for `anvil_serving.voice.benchmark` -- TTFA / turn latency / STT
WER-sample / TTS RTF (anvil task T015).

Dependency-light and hermetic: every STT/LLM/TTS call is either a fake
`*_stream_fn` (pure measurement-math tests) or a fake `*_transport` (proving
the real wire calls compose end-to-end) -- no socket is ever opened, no
GPU/torch/real audio anywhere. Clock values are injected so timing assertions
are deterministic.
"""
from __future__ import annotations

import io
import json

import pytest

from anvil_serving.voice.benchmark import (
    DEFAULT_REFERENCE_TEXT,
    run_benchmark,
    run_benchmark_from_manifest,
    synth_sample_pcm,
    to_json,
    word_error_rate,
)
from anvil_serving.voice.stages.llm import LLMStageConfig
from anvil_serving.voice.stages.stt import STTStageConfig
from anvil_serving.voice.stages.tts import TTSStageConfig


# --------------------------------------------------------------------------- #
# word_error_rate
# --------------------------------------------------------------------------- #
def test_wer_identical_strings_is_zero():
    assert word_error_rate("a b c", "a b c") == 0.0


def test_wer_completely_different_same_length_is_one():
    assert word_error_rate("a b c", "x y z") == 1.0


def test_wer_one_insertion():
    assert word_error_rate("a b", "a b c") == 0.5


def test_wer_one_deletion():
    assert word_error_rate("a b c", "a b") == pytest.approx(1 / 3)


def test_wer_both_empty_is_zero():
    assert word_error_rate("", "") == 0.0


def test_wer_empty_reference_nonempty_hypothesis_is_one():
    assert word_error_rate("", "a b c") == 1.0


# --------------------------------------------------------------------------- #
# synth_sample_pcm
# --------------------------------------------------------------------------- #
def test_synth_sample_pcm_length_matches_duration_and_rate():
    pcm = synth_sample_pcm(duration_s=0.5, sample_rate=8000)
    assert len(pcm) == int(0.5 * 8000) * 2  # 2 bytes/sample (int16)


def test_synth_sample_pcm_is_deterministic():
    a = synth_sample_pcm(duration_s=0.1, sample_rate=8000)
    b = synth_sample_pcm(duration_s=0.1, sample_rate=8000)
    assert a == b


# --------------------------------------------------------------------------- #
# run_benchmark: measurement math (fake stream_fns, injected clock)
# --------------------------------------------------------------------------- #
def _clock_sequence(values):
    it = iter(values)
    return lambda: next(it)


def test_run_benchmark_computes_all_four_metrics():
    def fake_stt(pcm, sample_rate, config):
        yield ("hello world", True)

    def fake_llm(text, config):
        yield "reply"

    def fake_tts(text, config):
        yield b"\x00\x01" * 4  # 8 bytes = 4 samples
        yield b"\x00\x01" * 4  # 8 bytes = 4 samples

    clock = _clock_sequence([0.0, 1.0, 1.2, 1.5])  # t0, t_tts_start, first_audio, t_end

    result = run_benchmark(
        stt_config=STTStageConfig(),
        llm_config=LLMStageConfig(),
        tts_config=TTSStageConfig(source_sample_rate=16000),
        pcm=b"\x00\x00", sample_rate=16000,
        reference_text="hello world",
        stt_stream_fn=fake_stt, llm_stream_fn=fake_llm, tts_stream_fn=fake_tts,
        clock=clock,
    )

    assert result["ttfa_ms"] == 1200.0
    assert result["turn_latency_ms"] == 1500.0
    assert result["stt_wer"] == 0.0  # hypothesis matches reference exactly
    assert result["tts_rtf"] == pytest.approx(1000.0)
    assert result["stt_hypothesis"] == "hello world"
    assert result["llm_reply"] == "reply"


def test_run_benchmark_ttfa_never_exceeds_turn_latency():
    def fake_stt(pcm, sample_rate, config):
        yield ("a", True)

    def fake_llm(text, config):
        yield "b"

    def fake_tts(text, config):
        yield b"\x00\x00"
        yield b"\x00\x00"
        yield b"\x00\x00"

    clock = _clock_sequence([0.0, 0.1, 0.15, 0.2, 0.3])
    result = run_benchmark(
        stt_config=STTStageConfig(), llm_config=LLMStageConfig(), tts_config=TTSStageConfig(),
        pcm=b"\x00\x00", sample_rate=16000, reference_text="a",
        stt_stream_fn=fake_stt, llm_stream_fn=fake_llm, tts_stream_fn=fake_tts,
        clock=clock,
    )
    assert result["ttfa_ms"] <= result["turn_latency_ms"]


def test_run_benchmark_tts_rtf_is_none_when_no_audio_produced():
    def fake_stt(pcm, sample_rate, config):
        yield ("a", True)

    def fake_llm(text, config):
        yield "b"

    def fake_tts(text, config):
        return iter(())  # TTS produced nothing

    result = run_benchmark(
        stt_config=STTStageConfig(), llm_config=LLMStageConfig(), tts_config=TTSStageConfig(),
        pcm=b"\x00\x00", sample_rate=16000, reference_text="a",
        stt_stream_fn=fake_stt, llm_stream_fn=fake_llm, tts_stream_fn=fake_tts,
        clock=lambda: 0.0,
    )
    assert result["tts_rtf"] is None


def test_run_benchmark_defaults_reference_text_when_not_supplied():
    def fake_stt(pcm, sample_rate, config):
        yield ("whatever", True)

    def fake_llm(text, config):
        yield "reply"

    def fake_tts(text, config):
        return iter(())

    result = run_benchmark(
        stt_config=STTStageConfig(), llm_config=LLMStageConfig(), tts_config=TTSStageConfig(),
        pcm=b"\x00\x00", sample_rate=16000,
        stt_stream_fn=fake_stt, llm_stream_fn=fake_llm, tts_stream_fn=fake_tts,
        clock=lambda: 0.0,
    )
    assert result["reference_text"] == DEFAULT_REFERENCE_TEXT


# --------------------------------------------------------------------------- #
# run_benchmark_from_manifest: builds stage configs from manifest tables
# --------------------------------------------------------------------------- #
def test_run_benchmark_from_manifest_builds_stage_configs_from_tables():
    data = {
        "voice": {
            "stt": {"base_url": "http://127.0.0.1:8090/v1", "model": "parakeet"},
            "llm": {"base_url": "http://127.0.0.1:8000/v1", "model": "chat-fast"},
            "tts": {"base_url": "http://127.0.0.1:8091/v1", "model": "kokoro-82m"},
        }
    }
    seen = {}

    def fake_stt(pcm, sample_rate, config):
        seen["stt_config"] = config
        yield ("hi", True)

    def fake_llm(text, config):
        seen["llm_config"] = config
        yield "ok"

    def fake_tts(text, config):
        seen["tts_config"] = config
        return iter(())

    result = run_benchmark_from_manifest(
        data, pcm=b"\x00\x00", sample_rate=16000, reference_text="hi",
        stt_stream_fn=fake_stt, llm_stream_fn=fake_llm, tts_stream_fn=fake_tts,
    )

    assert seen["stt_config"].base_url == "http://127.0.0.1:8090/v1"
    assert seen["stt_config"].model == "parakeet"
    assert seen["llm_config"].base_url == "http://127.0.0.1:8000/v1"
    assert seen["tts_config"].model == "kokoro-82m"
    assert result["stt_hypothesis"] == "hi"


def test_run_benchmark_from_manifest_generates_sample_pcm_when_none_given():
    data = {"voice": {
        "stt": {"base_url": "http://127.0.0.1:8090/v1", "model": "m"},
        "llm": {"base_url": "http://127.0.0.1:8000/v1", "model": "m"},
        "tts": {"base_url": "http://127.0.0.1:8091/v1", "model": "m"},
    }}
    seen_pcm = {}

    def fake_stt(pcm, sample_rate, config):
        seen_pcm["pcm"] = pcm
        yield ("x", True)

    result = run_benchmark_from_manifest(
        data, sample_rate=8000,
        stt_stream_fn=fake_stt,
        llm_stream_fn=lambda t, c: iter(["y"]),
        tts_stream_fn=lambda t, c: iter(()),
    )
    assert len(seen_pcm["pcm"]) > 0
    assert result["stt_hypothesis"] == "x"


# --------------------------------------------------------------------------- #
# to_json
# --------------------------------------------------------------------------- #
def test_to_json_round_trips():
    result = {"ttfa_ms": 12.3, "turn_latency_ms": 45.6, "stt_wer": 0.1, "tts_rtf": 0.5}
    parsed = json.loads(to_json(result))
    assert parsed == result


# --------------------------------------------------------------------------- #
# End-to-end wire composition: real transcribe_stream/stream_chat_completion/
# stream_speech via fake transports (no fake stream_fns) -- proves the three
# modules actually compose through run_benchmark, not just the measurement math.
# --------------------------------------------------------------------------- #
class _FakeReadResponse:
    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)
        self.closed = False

    def read(self, *a, **kw):
        return self._buf.read(*a, **kw)

    def close(self):
        self.closed = True


class _FakeLineResponse:
    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)
        self.closed = False

    def __iter__(self):
        return iter(self._buf)

    def close(self):
        self.closed = True


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
    assert result["ttfa_ms"] >= 0
    assert result["turn_latency_ms"] >= result["ttfa_ms"]
    assert stt_transport.response.closed
    assert llm_transport.response.closed
    assert tts_transport.response.closed
