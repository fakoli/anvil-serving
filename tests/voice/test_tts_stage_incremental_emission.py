"""SF2 (Opus review, `feat/voice-streaming`) -- incremental/streaming emission
regression test for the TTS stage. Mirrors
``tests/voice/test_llm_stage_incremental_emission.py``'s pattern exactly (a
real stage background thread against a real, blocking 127.0.0.1
``http.server``) but for :class:`~anvil_serving.voice.stages.tts.TTSStage`.

Every test in ``tests/voice/test_tts_serve.py`` calls
``list(stage.process(...))`` to exercise :class:`TTSStage`, which
materializes the whole generator up front and proves nothing about
INCREMENTALITY -- a ``process()`` that collected every :class:`AudioOut`
chunk into a list and returned it only once synthesis fully finished would
pass every one of those tests just as well as the real incremental
generator does. This test drives the REAL stack instead: a real
``TTSStage`` background thread, real ``stream_speech``/``urllib``, against a
REAL local ``/v1/audio/speech`` HTTP server that streams one PCM chunk, then
BLOCKS on a ``threading.Event`` before sending the second chunk. It asserts
the first chunk's ``AudioOut`` reaches the stage's output queue WHILE the
mock upstream is still blocked, then releases it and asserts the rest of
the audio (plus a forwarded ``EndOfResponse``) follows.

This MUST genuinely fail under a collect-then-return implementation of
``TTSStage.process`` (verified by hand while writing this test: reverting
``process`` to build and return a list instead of yielding per-chunk makes
the first ``out_q.get(timeout=5.0)`` below time out, because nothing is
put() on the queue until the whole synthesis call -- which is blocked on
the same event this test controls -- returns).

CPU-only, dependency-light: ``http.server``/``threading``/``queue``/
``array`` only -- no torch, no real audio codec, no GPU. 127.0.0.1 only,
never localhost (CLAUDE.md gotcha #1).
"""
from __future__ import annotations

import array
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List

import pytest

from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.messages import AudioOut, EndOfResponse, TTSInput
from anvil_serving.voice.stages.tts import TTSStage, TTSStageConfig


def _int16_bytes(values) -> bytes:
    return array.array("h", values).tobytes()


# Two 8-byte (4-sample) PCM chunks. `chunk_bytes` below is set to exactly 8
# so the first `resp.read(8)` inside `stream_speech` is satisfied entirely by
# chunk 1 -- already fully written+flushed by the time the client reads --
# without ever needing to block waiting on chunk 2's (still-unsent) bytes.
_AUDIO_1 = _int16_bytes([100, 200, 300, 400])
_AUDIO_2 = _int16_bytes([500, 600, 700, 800])


def _make_blocking_speech_handler(block_event: threading.Event, requests: List[bytes]):
    """Build a ``BaseHTTPRequestHandler`` that serves audio chunk 1
    immediately, then blocks on ``block_event`` before serving chunk 2 and
    closing the connection (ending the stream)."""

    class BlockingSpeechHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802 - stdlib-mandated method name
            length = int(self.headers.get("Content-Length", 0))
            requests.append(self.rfile.read(length) if length else b"")

            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            # No Content-Length: this is a streamed response the client reads
            # until the connection closes, exactly like a real streaming TTS
            # serve's `/v1/audio/speech` response.
            self.send_header("Connection", "close")
            self.end_headers()

            self.wfile.write(_AUDIO_1)
            self.wfile.flush()

            # Simulate the TTS engine still synthesizing: block the SERVER
            # thread here. The test asserts chunk 1's AudioOut is already on
            # the TTS stage's output queue while we are stuck right here.
            block_event.wait(timeout=5.0)

            self.wfile.write(_AUDIO_2)
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # silence the test server's stderr access log

    return BlockingSpeechHandler


@pytest.fixture
def blocking_speech_server():
    block_event = threading.Event()
    requests: List[bytes] = []
    handler_cls = _make_blocking_speech_handler(block_event, requests)
    server = HTTPServer(("127.0.0.1", 0), handler_cls)  # 127.0.0.1, never localhost
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        yield server, block_event, requests
    finally:
        block_event.set()  # make sure a stuck handler thread can unwind
        server.server_close()
        thread.join(timeout=2.0)


def _tts_input(turn_id="t1", generation=0, text="hello"):
    return TTSInput(turn_id=turn_id, turn_revision=0, generation=generation, text=text)


def test_first_audio_chunk_reaches_output_queue_before_second_chunk_is_sent(blocking_speech_server):
    """The core SF2 assertion: chunk 1's `AudioOut` is observable on the TTS
    stage's output queue WHILE the mock upstream is still blocked mid-
    synthesis, not only after the whole utterance completes."""
    server, block_event, requests = blocking_speech_server
    base_url = "http://127.0.0.1:%d/v1" % server.server_port

    scope = CancelScope()
    config = TTSStageConfig(
        base_url=base_url,
        timeout=10.0,
        chunk_bytes=8,  # == len(_AUDIO_1) == len(_AUDIO_2): see the constant's comment above
        source_sample_rate=16000,
        target_sample_rate=16000,  # no resampling noise -- exercising incrementality, not resampling
    )
    in_q: "queue.Queue" = queue.Queue()
    out_q: "queue.Queue" = queue.Queue()
    stage = TTSStage(in_q, [out_q], cancel_scope=scope, config=config)

    stage.start()
    try:
        in_q.put(_tts_input())

        first = out_q.get(timeout=5.0)
        assert isinstance(first, AudioOut)
        assert first.pcm == _AUDIO_1

        # Chunk 2 must NOT be on the queue yet -- the server is still parked
        # on block_event.wait() and has not written it to the socket, so the
        # stage's background thread is itself blocked inside resp.read().
        with pytest.raises(queue.Empty):
            out_q.get(timeout=0.3)

        # Queue the turn's terminal EndOfResponse behind the still-in-flight
        # TTSInput, exactly as the real pipeline would once the LLM stage's
        # reply is fully drained -- BaseStage only pulls this off in_queue
        # once the current item's generator is fully consumed.
        in_q.put(EndOfResponse(turn_id="t1", turn_revision=0, generation=0))

        # Release the mock upstream; the remaining audio (and the forwarded
        # EndOfResponse behind it) must follow.
        block_event.set()

        second = out_q.get(timeout=5.0)
        assert isinstance(second, AudioOut)
        assert second.pcm == _AUDIO_2

        end = out_q.get(timeout=5.0)
        assert isinstance(end, EndOfResponse)
        assert end.turn_id == "t1"
    finally:
        stage.stop(join_timeout=2.0)

    assert len(requests) == 1  # exactly one POST for the one utterance we fed in
