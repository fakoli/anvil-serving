"""Terminal decision evidence for unexpected audio transport failures."""
from __future__ import annotations

import pytest

from anvil_serving.router.audio import AudioGateway, AudioGatewayError
from anvil_serving.router.config import AudioRoute
from anvil_serving.router.decision_log import DecisionLog


def test_unexpected_audio_transport_failure_is_sanitized_and_recorded_once(capsys):
    calls = []

    def malformed_transport(url, *, data, headers, timeout, max_bytes=None):
        calls.append(dict(headers))
        raise RuntimeError("private malformed HTTP response and credential")

    route = AudioRoute(
        id="tts-local", purpose="tts", model="tts-model",
        base_url="http://127.0.0.1:30011/v1", source_sample_rate=24_000,
    )
    decisions = DecisionLog()
    gateway = AudioGateway(
        (route,), max_input_bytes=1024, max_output_bytes=4096,
        max_text_chars=256, max_concurrency=1, transport=malformed_transport,
        decision_log=decisions,
    )
    gateway_id = "req_0123456789abcdef0123456789abcdef"

    with pytest.raises(AudioGatewayError) as raised:
        gateway.dispatch_speech(
            {"purpose": "tts", "input": "hello", "response_format": "pcm16"},
            correlation={"request_id": "caller-audio-1", "gateway_request_id": gateway_id},
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
