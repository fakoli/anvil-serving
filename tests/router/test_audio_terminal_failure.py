"""Terminal decision evidence for unexpected audio transport failures."""
from __future__ import annotations

import base64

import pytest

from anvil_serving.router.audio import AudioGateway, AudioGatewayError, _AudioResponse
from anvil_serving.router.config import AudioRoute
from anvil_serving.router.decision_log import DecisionLog


def test_unexpected_audio_transport_failure_is_sanitized_and_recorded_once(capsys):
    calls = []

    def malformed_transport(url, *, data, headers, timeout, max_bytes=None):
        calls.append(dict(headers))
        raise RuntimeError("private malformed HTTP response and credential")

    route = AudioRoute(
        id="tts-local",
        purpose="tts",
        model="tts-model",
        base_url="http://127.0.0.1:30011/v1",
        source_sample_rate=24_000,
    )
    decisions = DecisionLog()
    gateway = AudioGateway(
        (route,),
        max_input_bytes=1024,
        max_output_bytes=4096,
        max_text_chars=256,
        max_concurrency=1,
        transport=malformed_transport,
        decision_log=decisions,
    )
    gateway_id = "req_0123456789abcdef0123456789abcdef"

    with pytest.raises(AudioGatewayError) as raised:
        gateway.dispatch_speech(
            {
                "purpose": "tts",
                "input": "hello",
                "response_format": "pcm16",
            },
            correlation={
                "request_id": "caller-audio-1",
                "gateway_request_id": gateway_id,
            },
        )

    logged = capsys.readouterr().err
    assert raised.value.status == 502
    assert raised.value.etype == "upstream_error"
    assert "private malformed" not in raised.value.message
    assert "private malformed" not in logged
    assert calls == [{"Content-Type": "application/json", "Accept": "audio/pcm",
                      "X-Request-Id": gateway_id}]
    assert len(decisions.records) == 1
    record = decisions.last
    assert record is not None
    assert record.attempts[0].outcome == "error"
    assert record.request_id == "caller-audio-1"
    assert record.gateway_request_id == gateway_id


def test_deep_stt_json_is_sanitized_and_recorded_once(capsys):
    deep_json = b"[" * 2_000 + b"0" + b"]" * 2_000

    def transport(url, *, data, headers, timeout, max_bytes=None):
        return _AudioResponse(deep_json, "application/json")

    route = AudioRoute(
        id="stt-local",
        purpose="stt",
        model="stt-model",
        base_url="http://127.0.0.1:30010/v1",
    )
    decisions = DecisionLog()
    gateway = AudioGateway(
        (route,),
        max_input_bytes=1024,
        max_output_bytes=300_000,
        max_text_chars=256,
        max_concurrency=1,
        transport=transport,
        decision_log=decisions,
    )
    gateway_id = "req_0123456789abcdef0123456789abcdef"

    with pytest.raises(AudioGatewayError) as raised:
        gateway.dispatch_transcription(
            {
                "purpose": "stt",
                "audio_b64": base64.b64encode(b"audio").decode(),
                "format": "wav",
                "is_final": True,
            },
            correlation={"request_id": "caller-stt-1", "gateway_request_id": gateway_id},
        )

    logged = capsys.readouterr().err
    assert raised.value.status == 502
    assert raised.value.etype == "upstream_error"
    assert "RecursionError" not in raised.value.message
    assert "RecursionError" not in logged
    assert len(decisions.records) == 1
    assert decisions.last.gateway_request_id == gateway_id
    assert decisions.last.attempts[0].outcome == "error"


@pytest.mark.parametrize("purpose", ["stt", "tts"])
def test_invalid_audio_response_metadata_is_recorded_once(purpose, capsys):
    def transport(url, *, data, headers, timeout, max_bytes=None):
        return _AudioResponse("private non-bytes body", object())

    route = AudioRoute(
        id=purpose + "-local",
        purpose=purpose,
        model=purpose + "-model",
        base_url="http://127.0.0.1:30010/v1",
        source_sample_rate=24_000 if purpose == "tts" else None,
    )
    decisions = DecisionLog()
    gateway = AudioGateway(
        (route,),
        max_input_bytes=1024,
        max_output_bytes=4096,
        max_text_chars=256,
        max_concurrency=1,
        transport=transport,
        decision_log=decisions,
    )
    gateway_id = "req_0123456789abcdef0123456789abcdef"
    correlation = {
        "request_id": "caller-audio-shape",
        "gateway_request_id": gateway_id,
    }

    with pytest.raises(AudioGatewayError) as raised:
        if purpose == "stt":
            gateway.dispatch_transcription(
                {
                    "purpose": "stt",
                    "audio_b64": base64.b64encode(b"audio").decode(),
                    "format": "wav",
                    "is_final": True,
                },
                correlation=correlation,
            )
        else:
            gateway.dispatch_speech(
                {
                    "purpose": "tts",
                    "input": "hello",
                    "response_format": "pcm16",
                },
                correlation=correlation,
            )

    logged = capsys.readouterr().err
    assert raised.value.status == 502
    assert "private non-bytes body" not in raised.value.message
    assert "private non-bytes body" not in logged
    assert len(decisions.records) == 1
    assert decisions.last.gateway_request_id == gateway_id
    assert decisions.last.attempts[0].outcome == "error"
