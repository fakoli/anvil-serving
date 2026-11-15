"""Hermetic contract tests for the router's normalized one-shot audio gateway.

The Dark STT/TTS engines are intentionally not started here.  These tests use
the real authenticated front door with an injected transport, so they prove the
router boundary without requiring a GPU, audio models, or a tailnet host.
"""

from __future__ import annotations

import base64
import http.client
import json
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
from tests.router.helpers import StaticBackend
from anvil_serving.router.backends.relay import RelayBackendError
from anvil_serving.router.config import AudioRoute, ConfigError
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
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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


def test_audio_routes_are_absent_without_configured_gateway_and_never_fall_through_to_chat():
    with audio_server(None) as (host, port):
        status, _, response = post(host, port, "/v1/audio/speech", {
            "purpose": "tts", "input": "x", "response_format": "pcm16",
        })
    assert status == 404
    assert response["error"]["type"] == "not_found"


def test_audio_error_is_typed_sanitized_and_has_no_provider_fallback():
    transport = FakeAudioTransport(error=RelayBackendError("http://10.1.2.3:30010 failed"))
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
    transport = FakeAudioTransport(error=RelayBackendError("first route failed"))
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
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        with pytest.raises(_AudioDeadlineExceeded):
            _deadline_transport(
                f"http://{host}:{port}/slow", data=b"{}", headers={}, timeout=0.02, max_bytes=16,
            )
        with pytest.raises(RelayBackendError):
            _deadline_transport(
                f"http://{host}:{port}/redirect", data=b"{}", headers={}, timeout=1.0, max_bytes=16,
            )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


BASE_TOML = """
[router]

[[router.tiers]]
id = "chat"
base_url = "http://127.0.0.1:30000/v1"
model = "chat"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "ANVIL_CHAT_KEY"

[router.model_routes]
llm.primary = "chat"
"""


def write_config(tmp_path, suffix: str) -> str:
    path = tmp_path / "audio.toml"
    path.write_text(
        BASE_TOML.replace("\n[[router.tiers]]", suffix + "\n[[router.tiers]]"),
        encoding="utf-8",
    )
    return str(path)


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
