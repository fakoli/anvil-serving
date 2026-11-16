"""Tests for the out-of-process STT serve: lifecycle
(``anvil_serving.voice.serves.stt``, anvil task T006 serve half) and stage
(``anvil_serving.voice.stages.stt``, anvil task T006 stage half).

Dependency-light and hermetic: docker is never invoked (a fake `_run`
callable stands in for `subprocess.run`) and no socket is opened (a fake
`transport`/`_open` stands in for `urllib`). No GPU, no torch, no real audio,
no network.
"""
from __future__ import annotations

import json

import pytest

from anvil_serving.voice.stages.stt import (
    STTStageConfig,
    transcribe_stream,
)
from tests.voice.conftest import (
    FakeLineResponse,
)


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, *, data, headers, timeout):
        self.calls.append({"url": url, "data": data, "headers": dict(headers), "timeout": timeout})
        return self.response


def _sse_transcript(*events: dict, done_event: dict) -> bytes:
    out = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
    out += b"data: " + json.dumps(done_event).encode() + b"\n\n"
    return out


@pytest.fixture
def manifest_with_stt(tmp_path):
    p = tmp_path / "serves.toml"
    p.write_text(
        '[[serve]]\nname = "stt"\ncontainer = "anvil-stt"\nruntime = "docker"\nport = 8090\n'
        'model = "parakeet"\nengine = "vllm"\n'
        'up = "echo bring-up-stt"\n',
        encoding="utf-8",
    )
    return str(p)


# --------------------------------------------------------------------------- #
# STTServe: lifecycle (delegates to anvil_serving.serves, never raw docker)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# pcm_to_wav / multipart fields
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# STTStreamAssembler
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# transcribe_stream: wire construction + SSE assembly (hermetic)
# --------------------------------------------------------------------------- #
def test_transcribe_stream_posts_multipart_to_transcriptions_path():
    payload = _sse_transcript(
        {"type": "transcript.text.delta", "delta": "hel"},
        done_event={"type": "transcript.text.done", "text": "hello"},
    )
    transport = FakeTransport(FakeLineResponse(payload))
    config = STTStageConfig(base_url="http://127.0.0.1:8090/v1", model="parakeet")
    results = list(transcribe_stream(b"\x00\x01" * 100, 16000, config, transport=transport))
    assert results == [("hel", False), ("hello", True)]
    call = transport.calls[0]
    assert call["url"] == "http://127.0.0.1:8090/v1/audio/transcriptions"
    assert call["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b"Content-Disposition: form-data; name=\"file\"" in call["data"]
    assert b"RIFF" in call["data"]  # the WAV container header


# --------------------------------------------------------------------------- #
# STTStage: partial + final Transcription, cancel_scope integration
# --------------------------------------------------------------------------- #
