"""Hermetic contract tests for the router's normalized one-shot audio gateway.

The Dark STT/TTS engines are intentionally not started here.  These tests use
the real authenticated front door with an injected transport, so they prove the
router boundary without requiring a GPU, audio models, or a tailnet host.
"""

from __future__ import annotations

import base64
import http.client
import json
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

import pytest

from anvil_serving.router.audio import (
    AudioGateway,
    AudioGatewayError,
    _AudioDeadlineExceeded,
    _AudioResponse,
    _deadline_transport,
)
from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.backends.cloud import CloudBackendError
from anvil_serving.router.config import AudioRoute, ConfigError, load
from anvil_serving.router.decision_log import DecisionLog
from anvil_serving.router.front_door import make_server
from anvil_serving.router.serve import build_server


STT = AudioRoute(
    id="dark-stt",
    purpose="stt",
    model="tdt-0.6b-v3",
    base_url="http://127.0.0.1:30010/v1",
    timeout=3.0,
)
TTS = AudioRoute(
    id="dark-tts",
    purpose="tts",
    model="kokoro",
    base_url="http://127.0.0.1:30011/v1",
    source_sample_rate=24_000,
    timeout=3.0,
)


class FakeAudioTransport:
    """Canned private-engine responses, recording only the router's outbound call."""

    def __init__(self, *, error: Optional[Exception] = None, tts_body: bytes = b"\x01\x00\x02\x00"):
        self.error = error
        self.tts_body = tts_body
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url, *, data, headers, timeout, max_bytes=None):
        self.calls.append({
            "url": url,
            "data": data,
            "headers": dict(headers),
            "timeout": timeout,
            "max_bytes": max_bytes,
        })
        if self.error is not None:
            raise self.error
        if url.endswith("/audio/transcriptions"):
            return _AudioResponse(
                b'{"text":"sensitive transcript must not be logged"}',
                "application/json",
            )
        return _AudioResponse(self.tts_body, "audio/pcm")


def gateway(
    transport: Optional[FakeAudioTransport] = None,
    *,
    log: Optional[DecisionLog] = None,
    max_input_bytes: int = 1024,
    max_output_bytes: int = 4096,
    max_concurrency: int = 2,
) -> AudioGateway:
    return AudioGateway(
        (STT, TTS),
        max_input_bytes=max_input_bytes,
        max_output_bytes=max_output_bytes,
        max_text_chars=256,
        max_concurrency=max_concurrency,
        transport=transport or FakeAudioTransport(),
        decision_log=log,
    )


@contextmanager
def audio_server(audio: Optional[AudioGateway], *, token: Optional[str] = "router-token"):
    httpd = make_server(
        "127.0.0.1", 0, StaticBackend("chat must never receive audio"),
        auth_token=token, audio=audio,
    )
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def post(
    host: str, port: int, path: str, body: Dict[str, Any], *, token: Optional[str] = "router-token",
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Dict[str, str], Dict[str, Any]]:
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        request_headers = {"Content-Type": "application/json"}
        if token is not None:
            request_headers["Authorization"] = "Bearer " + token
        request_headers.update(headers or {})
        connection.request("POST", path, json.dumps(body), request_headers)
        response = connection.getresponse()
        raw = response.read()
        return response.status, {key.lower(): value for key, value in response.getheaders()}, json.loads(raw)
    finally:
        connection.close()


def get(host: str, port: int, path: str, *, token: Optional[str] = "router-token"):
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        headers = {} if token is None else {"Authorization": "Bearer " + token}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_stt_normalizes_json_base64_to_parakeet_multipart_and_logs_metadata_only(capsys):
    transport = FakeAudioTransport()
    decisions = DecisionLog()
    result = gateway(transport, log=decisions).dispatch_transcription({
        "purpose": "stt",
        "audio_b64": base64.b64encode(b"RIFFnot-a-real-wav").decode("ascii"),
        "format": "wav",
        "is_final": True,
    }, correlation={"request_id": "voice-280"})

    assert result["text"] == "sensitive transcript must not be logged"
    assert result["is_final"] is True
    assert result["model"] == "tdt-0.6b-v3"
    assert result["request_id"] == "voice-280"
    (call,) = transport.calls
    assert call["url"] == "http://127.0.0.1:30010/v1/audio/transcriptions"
    assert b'name="model"' in call["data"] and b"tdt-0.6b-v3" in call["data"]
    assert b'filename="audio.wav"' in call["data"]
    assert b"RIFFnot-a-real-wav" in call["data"]

    record = decisions.last
    assert record is not None
    assert record.served_tier == "dark-stt"
    assert record.request_bytes == len(b"RIFFnot-a-real-wav")
    rendered = repr(decisions.summary()) + capsys.readouterr().err
    assert "sensitive transcript" not in rendered
    assert "RIFFnot-a-real-wav" not in rendered
    assert base64.b64encode(b"RIFFnot-a-real-wav").decode("ascii") not in rendered


def test_pcm16_stt_is_wrapped_with_sample_rate_before_the_private_engine():
    transport = FakeAudioTransport()
    payload = b"\x00\x00\x01\x00"
    gateway(transport).dispatch_transcription({
        "purpose": "stt",
        "audio_b64": base64.b64encode(payload).decode("ascii"),
        "format": "pcm16",
        "sample_rate": 16_000,
        "is_final": True,
    })
    data = transport.calls[0]["data"]
    assert b'filename="audio.wav"' in data
    assert b"RIFF" in data and (16_000).to_bytes(4, "little") in data


def test_audio_upstream_auth_uses_its_env_var_without_recording_the_secret(capsys):
    transport = FakeAudioTransport()
    protected = replace(STT, auth_env="ANVIL_DARK_STT_TOKEN")
    log = DecisionLog()
    secured = AudioGateway(
        (protected, TTS),
        max_input_bytes=1024,
        max_output_bytes=4096,
        max_text_chars=256,
        max_concurrency=1,
        env={"ANVIL_DARK_STT_TOKEN": "secret-upstream-token"},
        transport=transport,
        decision_log=log,
    )
    secured.dispatch_transcription({
        "purpose": "stt", "audio_b64": base64.b64encode(b"x").decode(),
        "format": "wav", "is_final": True,
    })
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer secret-upstream-token"
    assert "secret-upstream-token" not in repr(log.summary()) + capsys.readouterr().err


def test_tts_returns_live_qualified_pcm16_in_the_canonical_base64_response():
    requested = "pcm16"
    upstream = b"\x01\x00\x02\x00"

    class FormatTransport(FakeAudioTransport):
        def __call__(self, *args, **kwargs):
            super().__call__(*args, **kwargs)
            return _AudioResponse(upstream, "audio/pcm")

    transport = FormatTransport()
    result = gateway(transport).dispatch_speech({
        "purpose": "tts", "input": "private synthesis text", "response_format": requested,
    })
    assert set(result) >= {"audio_b64", "format", "sample_rate", "model", "request_id"}
    assert "audio" not in result
    assert result["format"] == requested
    assert result["sample_rate"] == 24_000
    assert base64.b64decode(result["audio_b64"]) == upstream
    forwarded = json.loads(transport.calls[0]["data"])
    assert forwarded == {
        "model": "kokoro", "input": "private synthesis text",
        "response_format": "pcm",
    }


@pytest.mark.parametrize("unsupported_format", ("wav", "mp3"))
def test_tts_rejects_unqualified_containers_before_calling_the_upstream(unsupported_format):
    transport = FakeAudioTransport()
    with pytest.raises(AudioGatewayError, match="response_format must be pcm16") as rejected:
        gateway(transport).dispatch_speech({
            "purpose": "tts", "input": "x", "response_format": unsupported_format,
        })
    assert rejected.value.status == 400
    assert transport.calls == []


def test_front_door_audio_routes_are_same_token_authenticated_and_health_advertises_them():
    transport = FakeAudioTransport()
    with audio_server(gateway(transport)) as (host, port):
        denied, _, error = post(host, port, "/v1/audio/transcriptions", {
            "purpose": "stt", "audio_b64": base64.b64encode(b"x").decode(), "format": "wav", "is_final": True,
        }, token=None)
        assert denied == 401 and error["error"]["type"] == "authentication_error"

        status, _, answer = post(host, port, "/v1/audio/transcriptions", {
            "purpose": "stt", "audio_b64": base64.b64encode(b"x").decode(), "format": "wav", "is_final": True,
        }, headers={"X-Request-Id": "audio-request-1"})
        assert status == 200 and answer["request_id"] == "audio-request-1"
        health_status, health = get(host, port, "/healthz", token=None)
        assert health_status == 200
        assert "/v1/audio/transcriptions" in health["routes"]
        assert "/v1/audio/speech" in health["routes"]


def test_audio_discovery_and_method_handling_include_only_bound_purposes():
    stt_only = AudioGateway(
        (STT,), max_input_bytes=1024, max_output_bytes=4096, max_text_chars=256,
        max_concurrency=1, transport=FakeAudioTransport(),
    )
    with audio_server(stt_only) as (host, port):
        health_status, health = get(host, port, "/healthz", token=None)
        speech_status, speech_error = get(host, port, "/v1/audio/speech")
        stt_status, stt_error = get(host, port, "/v1/audio/transcriptions")
    assert stt_only.paths == ("/v1/audio/transcriptions",)
    assert health_status == 200
    assert "/v1/audio/transcriptions" in health["routes"]
    assert "/v1/audio/speech" not in health["routes"]
    assert speech_status == 404
    assert speech_error["error"]["type"] == "not_found"
    assert stt_status == 405
    assert stt_error["error"]["type"] == "method_not_allowed"


def test_audio_routes_are_absent_without_configured_gateway_and_never_fall_through_to_chat():
    with audio_server(None) as (host, port):
        status, _, response = post(host, port, "/v1/audio/speech", {
            "purpose": "tts", "input": "x", "response_format": "pcm16",
        })
    assert status == 404
    assert response["error"]["type"] == "not_found"


def test_audio_error_is_typed_sanitized_and_has_no_provider_fallback():
    transport = FakeAudioTransport(error=CloudBackendError("http://10.1.2.3:30010 failed"))
    with audio_server(gateway(transport)) as (host, port):
        status, _, response = post(host, port, "/v1/audio/transcriptions", {
            "purpose": "stt", "audio_b64": base64.b64encode(b"x").decode(), "format": "wav", "is_final": True,
        })
    assert status == 502
    assert response["error"]["type"] == "upstream_error"
    assert "10.1.2.3" not in json.dumps(response)
    assert len(transport.calls) == 1


def test_purpose_default_route_is_selected_once_and_never_falls_through_to_another_route():
    first = replace(STT, id="stt-primary", default=True)
    second = replace(STT, id="stt-secondary", model="other-stt")
    transport = FakeAudioTransport(error=CloudBackendError("first route failed"))
    no_fallback = AudioGateway(
        (first, second, TTS),
        max_input_bytes=1024,
        max_output_bytes=4096,
        max_text_chars=256,
        max_concurrency=1,
        transport=transport,
    )
    with pytest.raises(AudioGatewayError) as error:
        no_fallback.dispatch_transcription({
            "purpose": "stt", "audio_b64": base64.b64encode(b"x").decode(),
            "format": "wav", "is_final": True,
        })
    assert error.value.status == 502
    assert len(transport.calls) == 1
    assert b"tdt-0.6b-v3" in transport.calls[0]["data"]


def test_audio_route_id_and_purpose_must_match_the_endpoint():
    service = gateway()
    with pytest.raises(AudioGatewayError) as wrong_route:
        service.dispatch_speech({
            "route": "dark-stt", "purpose": "tts", "input": "x", "response_format": "pcm16",
        })
    assert wrong_route.value.status == 404

    with pytest.raises(AudioGatewayError) as wrong_purpose:
        service.dispatch_transcription({
            "route": "dark-stt", "purpose": "tts", "audio_b64": "eA==", "format": "wav",
            "is_final": True,
        })
    assert wrong_purpose.value.status == 400


def test_tts_output_cap_is_enforced_before_the_router_base64_encodes_audio():
    with pytest.raises(AudioGatewayError) as too_large:
        gateway(FakeAudioTransport(tts_body=b"\x00" * 16), max_output_bytes=8).dispatch_speech({
            "purpose": "tts", "input": "x", "response_format": "pcm16",
        })
    assert too_large.value.status == 413
    assert too_large.value.etype == "payload_too_large"


def test_audio_front_door_rejects_oversized_encoded_body_before_transport():
    transport = FakeAudioTransport()
    small_gateway = gateway(transport, max_input_bytes=16)
    with audio_server(small_gateway) as (host, port):
        status, _, response = post(host, port, "/v1/audio/transcriptions", {
            "purpose": "stt", "audio_b64": "A" * 5000, "format": "wav", "is_final": True,
        })
    assert status == 413
    assert response["error"]["type"] == "payload_too_large"
    assert transport.calls == []


def test_audio_concurrency_has_its_own_nonblocking_admission_cap():
    started = threading.Event()
    release = threading.Event()

    class BlockingTransport(FakeAudioTransport):
        def __call__(self, *args, **kwargs):
            started.set()
            assert release.wait(3)
            return super().__call__(*args, **kwargs)

    transport = BlockingTransport()
    limited = gateway(transport, max_concurrency=1)
    body = {"purpose": "stt", "audio_b64": base64.b64encode(b"x").decode(), "format": "wav", "is_final": True}
    with audio_server(limited) as (host, port):
        first = threading.Thread(target=post, args=(host, port, "/v1/audio/transcriptions", body))
        first.start()
        assert started.wait(2)
        status, _, response = post(host, port, "/v1/audio/transcriptions", body)
        release.set()
        first.join(timeout=4)
    assert status == 503
    assert response["error"]["type"] == "server_busy"
    assert len(transport.calls) == 1


def test_partial_audio_upload_does_not_consume_an_upstream_audio_slot():
    transport = FakeAudioTransport()
    body = {
        "purpose": "stt", "audio_b64": base64.b64encode(b"x").decode(),
        "format": "wav", "is_final": True,
    }
    encoded = json.dumps(body).encode("utf-8")
    limited = gateway(transport, max_concurrency=1)
    with audio_server(limited) as (host, port):
        slow_client = socket.create_connection((host, port), timeout=3)
        try:
            slow_client.sendall(
                b"POST /v1/audio/transcriptions HTTP/1.1\r\n"
                + ("Host: %s:%s\r\n" % (host, port)).encode("ascii")
                + b"Authorization: Bearer router-token\r\n"
                + b"Content-Type: application/json\r\n"
                + ("Content-Length: %d\r\n\r\n" % len(encoded)).encode("ascii")
                + b"{"
            )
            status, _, response = post(host, port, "/v1/audio/transcriptions", body)
        finally:
            slow_client.close()
    assert status == 200
    assert response["text"] == "sensitive transcript must not be logged"
    assert len(transport.calls) == 1


def test_gateway_rejects_interim_transcripts_bad_content_type_and_deadline():
    with pytest.raises(AudioGatewayError, match="final audio") as interim:
        gateway().dispatch_transcription({
            "purpose": "stt", "audio_b64": base64.b64encode(b"x").decode(), "format": "wav", "is_final": False,
        })
    assert interim.value.status == 422

    class BadTypeTransport(FakeAudioTransport):
        def __call__(self, *args, **kwargs):
            super().__call__(*args, **kwargs)
            return _AudioResponse(b"not audio", "application/json")

    with pytest.raises(AudioGatewayError) as bad_type:
        gateway(BadTypeTransport()).dispatch_speech({
            "purpose": "tts", "input": "x", "response_format": "pcm16",
        })
    assert bad_type.value.status == 502

    with pytest.raises(AudioGatewayError) as timeout:
        gateway(FakeAudioTransport(error=_AudioDeadlineExceeded())).dispatch_speech({
            "purpose": "tts", "input": "x", "response_format": "pcm16",
        })
    assert timeout.value.status == 504


def test_tts_rejects_invalid_pcm16_output():
    class InvalidPCMTransport(FakeAudioTransport):
        def __call__(self, *args, **kwargs):
            super().__call__(*args, **kwargs)
            return _AudioResponse(b"\x00", "audio/pcm")

    with pytest.raises(AudioGatewayError) as mismatch:
        gateway(InvalidPCMTransport()).dispatch_speech({
            "purpose": "tts", "input": "x", "response_format": "pcm16",
        })
    assert mismatch.value.status == 502


def test_tts_rejects_a_production_response_without_a_matching_content_type():
    class MissingMimeTransport(FakeAudioTransport):
        def __call__(self, *args, **kwargs):
            super().__call__(*args, **kwargs)
            return _AudioResponse(b"\x00\x00", "")

    with pytest.raises(AudioGatewayError) as missing_mime:
        gateway(MissingMimeTransport()).dispatch_speech({
            "purpose": "tts", "input": "x", "response_format": "pcm16",
        })
    assert missing_mime.value.status == 502


def test_rejected_audio_responses_preserve_only_safe_observed_byte_counts(capsys):
    malformed_stt = b'{"unexpected":"shape"}'

    class MalformedSTTTransport(FakeAudioTransport):
        def __call__(self, *args, **kwargs):
            super().__call__(*args, **kwargs)
            return _AudioResponse(malformed_stt, "application/json")

    stt_log = DecisionLog()
    with pytest.raises(AudioGatewayError):
        gateway(MalformedSTTTransport(), log=stt_log).dispatch_transcription({
            "purpose": "stt", "audio_b64": base64.b64encode(b"x").decode(),
            "format": "wav", "is_final": True,
        })
    assert stt_log.last is not None
    assert stt_log.last.response_bytes == len(malformed_stt)
    assert malformed_stt.decode() not in repr(stt_log.summary()) + capsys.readouterr().err

    invalid_pcm = b"\x00"

    class InvalidPCMTransport(FakeAudioTransport):
        def __call__(self, *args, **kwargs):
            super().__call__(*args, **kwargs)
            return _AudioResponse(invalid_pcm, "audio/pcm")

    tts_log = DecisionLog()
    with pytest.raises(AudioGatewayError):
        gateway(InvalidPCMTransport(), log=tts_log).dispatch_speech({
            "purpose": "tts", "input": "private", "response_format": "pcm16",
        })
    assert tts_log.last is not None
    assert tts_log.last.response_bytes == len(invalid_pcm)
    assert "private" not in repr(tts_log.summary()) + capsys.readouterr().err


def test_stt_rejects_a_non_json_upstream_response_even_with_a_transcript_shape():
    class WrongSTTMimeTransport(FakeAudioTransport):
        def __call__(self, *args, **kwargs):
            super().__call__(*args, **kwargs)
            return _AudioResponse(b'{"text":"must not be trusted"}', "text/plain")

    with pytest.raises(AudioGatewayError) as rejected:
        gateway(WrongSTTMimeTransport()).dispatch_transcription({
            "purpose": "stt", "audio_b64": base64.b64encode(b"x").decode(),
            "format": "wav", "is_final": True,
        })
    assert rejected.value.status == 502


def test_generated_audio_request_id_is_the_same_id_written_to_the_decision_log():
    decisions = DecisionLog()
    response = gateway(log=decisions).dispatch_speech({
        "purpose": "tts", "input": "private", "response_format": "pcm16",
    })
    assert response["request_id"].startswith("aud_")
    assert decisions.last is not None
    assert decisions.last.request_id == response["request_id"]


def test_default_audio_transport_enforces_a_total_deadline_and_never_follows_redirects():
    class RawAudioHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/pcm")
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b"\x00")
            self.wfile.flush()
            time.sleep(0.08)
            try:
                self.wfile.write(b"\x00\x00\x00")
            except OSError:
                pass

        def log_message(self, format, *args):
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), RawAudioHandler)
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(_AudioDeadlineExceeded):
            _deadline_transport(
                f"http://{host}:{port}/slow", data=b"{}", headers={}, timeout=0.02, max_bytes=16,
            )
        with pytest.raises(CloudBackendError):
            _deadline_transport(
                f"http://{host}:{port}/redirect", data=b"{}", headers={}, timeout=1.0, max_bytes=16,
            )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def test_default_audio_transport_records_the_bounded_count_for_an_oversized_response():
    class OversizedAudioHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "audio/pcm")
            self.send_header("Content-Length", "6")
            self.end_headers()
            self.wfile.write(b"\x00\x00\x00\x00\x00\x00")

        def log_message(self, format, *args):
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), OversizedAudioHandler)
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        decisions = DecisionLog()
        route = replace(TTS, base_url=f"http://{host}:{port}/v1")
        service = AudioGateway(
            (route,), max_input_bytes=1024, max_output_bytes=4, max_text_chars=256,
            max_concurrency=1, decision_log=decisions,
        )
        with pytest.raises(AudioGatewayError) as rejected:
            service.dispatch_speech({
                "purpose": "tts", "input": "private", "response_format": "pcm16",
            })
        assert rejected.value.status == 413
        # The bounded transport reads one byte beyond the four-byte cap, never
        # the full content; only that safe observed count reaches the decision.
        assert rejected.value.response_bytes == 5
        assert decisions.last is not None
        assert decisions.last.response_bytes == 5
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


BASE_TOML = """
[router]
mapping_version = "audio-test"

[[router.tiers]]
id = "chat"
base_url = "http://127.0.0.1:30000/v1"
model = "chat"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "ANVIL_CHAT_KEY"

[router.presets]
chat = ["chat"]
"""


def write_config(tmp_path, suffix: str) -> str:
    path = tmp_path / "audio.toml"
    path.write_text(
        BASE_TOML.replace("\n[[router.tiers]]", suffix + "\n[[router.tiers]]"),
        encoding="utf-8",
    )
    return str(path)


def test_audio_config_is_opt_in_validates_private_routes_and_requires_default(tmp_path):
    config = load(write_config(tmp_path, """
audio_max_input_bytes = 8192
audio_max_concurrency = 2

[[router.audio_routes]]
id = "dark-stt"
purpose = "stt"
model = "tdt"
base_url = "http://host.docker.internal:30010/v1"

[[router.audio_routes]]
id = "dark-tts"
purpose = "tts"
model = "kokoro"
base_url = "http://127.0.0.1:30011/v1"
source_sample_rate = 24000
"""))
    assert [route.id for route in config.audio_routes] == ["dark-stt", "dark-tts"]
    assert config.audio_max_input_bytes == 8192

    for base_url, fragment in [
        ("http://8.8.8.8:30010/v1", "RFC1918"),
        ("file:///tmp/audio", "http://"),
        ("http://localhost:30010/v1", "never localhost"),
    ]:
        with pytest.raises(ConfigError, match=fragment):
            load(write_config(tmp_path, f"""
[[router.audio_routes]]
id = "bad"
purpose = "stt"
model = "stt"
base_url = "{base_url}"
"""))

    with pytest.raises(ConfigError, match="source_sample_rate"):
        load(write_config(tmp_path, """
[[router.audio_routes]]
id = "bad-tts"
purpose = "tts"
model = "tts"
base_url = "http://127.0.0.1:30011/v1"
"""))

    with pytest.raises(ConfigError, match="exactly one must set default"):
        load(write_config(tmp_path, """
[[router.audio_routes]]
id = "stt-a"
purpose = "stt"
model = "stt-a"
base_url = "http://127.0.0.1:30010/v1"

[[router.audio_routes]]
id = "stt-b"
purpose = "stt"
model = "stt-b"
base_url = "http://127.0.0.1:30012/v1"
"""))


def test_build_server_does_not_reuse_the_generic_chat_transport_for_audio(tmp_path):
    config_path = write_config(tmp_path, """
[server]
auth_env = "ANVIL_ROUTER_TOKEN"

[[router.audio_routes]]
id = "dark-stt"
purpose = "stt"
model = "tdt"
base_url = "http://127.0.0.1:30010/v1"
""")

    def generic_chat_transport(*args, **kwargs):
        raise AssertionError("audio must not use the generic chat transport seam")

    httpd = build_server(
        config_path,
        port=0,
        backends={"chat": StaticBackend("chat")},
        env={"ANVIL_ROUTER_TOKEN": "router-token"},
        transport=generic_chat_transport,
    )
    try:
        assert httpd.anvil_audio is not None
        assert httpd.anvil_audio._transport is not generic_chat_transport
    finally:
        httpd.server_close()


def test_build_server_rejects_audio_routes_without_resolved_front_door_auth(tmp_path):
    config_path = write_config(tmp_path, """
[[router.audio_routes]]
id = "dark-stt"
purpose = "stt"
model = "tdt"
base_url = "http://127.0.0.1:30010/v1"
""")
    with pytest.raises(ConfigError, match="audio_routes.*auth_env"):
        build_server(
            config_path,
            port=0,
            backends={"chat": StaticBackend("chat")},
        )
