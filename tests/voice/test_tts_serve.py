"""Tests for the out-of-process TTS serve: lifecycle
(``anvil_serving.voice.serves.tts``, anvil task T008 serve half) and stage
(``anvil_serving.voice.stages.tts``, anvil task T008 stage half).

Dependency-light and hermetic: docker is never invoked (a fake `_run`
callable stands in for `subprocess.run`) and no socket is opened (a fake
`transport`/`_open` stands in for `urllib`). No GPU, no torch, no real audio,
no network.
"""
from __future__ import annotations

import array
import base64
import json

import pytest

from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.messages import (
    AudioOut,
    SpokenText,
    TTSInput,
)
from anvil_serving.voice.realtime.ws import make_ws_server, serve_forever_in_background
from anvil_serving.voice.stages.tts import (
    TTSStage,
    TTSStageConfig,
    stream_speech,
)


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class FakeReadResponse:
    """Fake of an open urllib response supporting `.read(n)` in a loop --
    what `stream_speech` needs for incremental reads (no socket).

    `status` defaults to 200 so every pre-existing test using this fake
    (constructed before F3's status check existed) keeps behaving exactly as
    before -- only tests that explicitly pass a non-2xx `status` exercise the
    new rejection path.
    """

    def __init__(self, chunks, status: int = 200):
        self._chunks = list(chunks)
        self.status = status
        self.closed = False

    def read(self, n=-1):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def close(self):
        self.closed = True


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, *, data, headers, timeout):
        self.calls.append({"url": url, "body": json.loads(data), "headers": dict(headers), "timeout": timeout})
        return self.response


def _int16_bytes(values) -> bytes:
    return array.array("h", values).tobytes()


@pytest.fixture
def manifest_with_tts(tmp_path):
    p = tmp_path / "serves.toml"
    p.write_text(
        '[[serve]]\nname = "tts"\ncontainer = "anvil-tts"\nruntime = "docker"\nport = 8091\n'
        'model = "kokoro-82m"\nengine = "vllm"\n'
        'up = "echo bring-up-tts"\n',
        encoding="utf-8",
    )
    return str(p)


# --------------------------------------------------------------------------- #
# TTSServe: lifecycle (delegates to anvil_serving.serves, never raw docker)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# build_speech_request_body / resample_int16
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# stream_speech: wire construction + incremental read (hermetic)
# --------------------------------------------------------------------------- #


def test_stream_speech_cartesia_websocket_streams_base64_pcm(monkeypatch):
    monkeypatch.setenv("ANVIL_TEST_CARTESIA_TOKEN", "cartesia-token")
    audio = _int16_bytes([1, 2, 3, 4])
    payloads = []

    def on_connect(conn, path):
        first = conn.recv_text()
        payloads.append(json.loads(first))
        conn.send_json({"type": "chunk", "data": base64.b64encode(audio[:4]).decode("ascii")})
        conn.send_json({"type": "chunk", "data": base64.b64encode(audio[4:]).decode("ascii")})
        conn.send_json({"type": "done"})

    server = make_ws_server(
        "127.0.0.1",
        0,
        on_connect,
        ws_path="/tts/websocket",
        token_env="ANVIL_TEST_CARTESIA_TOKEN",
    )
    thread = serve_forever_in_background(server)
    try:
        host, port = server.server_address[:2]
        config = TTSStageConfig(
            base_url="http://%s:%d" % (host, port),
            model="gepard-1.0",
            protocol="gepard",
            api_key_env="ANVIL_TEST_CARTESIA_TOKEN",
            source_sample_rate=22050,
        )

        chunks = list(stream_speech("hi there", config))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert b"".join(chunks) == audio
    assert payloads[0]["transcript"] == "hi there"
    assert payloads[0]["model_id"] == "gepard-1.0"
    assert payloads[0]["output_format"]["sample_rate"] == 22050
    assert payloads[0]["continue"] is False


# --------------------------------------------------------------------------- #
# F3 -- a non-2xx response must raise, never be streamed as if it were audio
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# TTSStage: incremental AudioOut, resampling, barge-in abort, EndOfResponse
# --------------------------------------------------------------------------- #
def _tts_input(turn_id="t1", generation=0, text="hello"):
    return TTSInput(turn_id=turn_id, turn_revision=0, generation=generation, text=text)


def test_tts_stage_emits_resampled_audio_chunks():
    scope = CancelScope()
    audio = _int16_bytes(range(0, 480))  # 240 samples-worth chunked below

    def fake_stream(text, config):
        yield audio[:200]
        yield audio[200:]

    config = TTSStageConfig(source_sample_rate=24000, target_sample_rate=16000)
    stage = TTSStage(in_queue=None, cancel_scope=scope, config=config, stream_fn=fake_stream)
    # process() is a generator now (PUNCH-LIST #1) -- materialize ONCE so
    # both checks below see every item.
    out = list(stage.process(_tts_input()))

    audio_out = [m for m in out if isinstance(m, AudioOut)]
    assert all(m.sample_rate == 16000 for m in audio_out)
    assert [m.text for m in out if isinstance(m, SpokenText)] == ["hello"]
    total_out_samples = sum(len(m.pcm) // 2 for m in audio_out)
    total_in_samples = len(audio) // 2
    assert total_out_samples == round(total_in_samples * (16000 / 24000))
