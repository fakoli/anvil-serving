"""PUNCH-LIST #1 -- incremental/streaming emission (anvil task: voice
streaming). Proves the LLM stage's first-audio latency tracks first-SENTENCE
latency, not full-reply latency.

Before this fix, ``LLMStage.process`` accumulated every sentence-batched
chunk of the whole assistant reply into a list and returned it only once the
upstream stream finished -- so nothing reached this stage's output queue
until the ENTIRE reply had arrived, no matter how early sentence 1 was ready.
``BaseStage._run`` compounded this: even if ``process`` HAD yielded early, the
old ``_run`` only ever called ``self.process(item)`` once and handed whatever
it returned to ``_emit_result`` -- a returned generator would have been
enqueued as one opaque object, never iterated.

This test drives the REAL stack end-to-end (real ``LLMStage`` thread, real
``stream_chat_completion``/``urllib``, a REAL local HTTP server -- no fake
``stream_fn`` shortcut) against a mock ``/v1/chat/completions`` SSE server
that emits sentence 1's tokens, then BLOCKS on a ``threading.Event`` before
sending sentence 2. It asserts the first ``LLMChunk`` reaches the stage's
output queue while sentence 2 is still blocked upstream, then releases the
event and asserts sentence 2 (and the trailing ``EndOfResponse``) follow.

Dependency-light: ``http.server``/``threading``/``queue`` only -- no GPU, no
torch, no real model. 127.0.0.1 only (never localhost), per CLAUDE.md gotcha
#1.
"""
from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List

import pytest

from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.messages import EndOfResponse, GenerateRequest, LLMChunk
from anvil_serving.voice.stages.llm import LLMStage, LLMStageConfig


def _sse_chunk(text: str, finish: str | None = None) -> bytes:
    delta = {"content": text} if text else {}
    payload = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n"


def _make_blocking_handler(block_event: threading.Event, requests: List[bytes]):
    """Build a ``BaseHTTPRequestHandler`` that serves sentence 1 immediately,
    then blocks on ``block_event`` before serving sentence 2 + [DONE]."""

    class BlockingSSEHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802 - stdlib-mandated method name
            length = int(self.headers.get("Content-Length", 0))
            requests.append(self.rfile.read(length) if length else b"")

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            # No Content-Length: this is a streamed response: the client
            # reads until we close the connection, exactly like a real
            # streaming chat-completions upstream.
            self.send_header("Connection", "close")
            self.end_headers()

            self.wfile.write(_sse_chunk("First sentence. "))
            self.wfile.flush()

            # Simulate the upstream model still generating: block the
            # SERVER thread here. The test asserts sentence 1 is already on
            # the LLM stage's output queue while we are stuck right here.
            block_event.wait(timeout=5.0)

            self.wfile.write(_sse_chunk("Second sentence.", finish="stop"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # silence the test server's stderr access log

    return BlockingSSEHandler


@pytest.fixture
def blocking_sse_server():
    block_event = threading.Event()
    requests: List[bytes] = []
    handler_cls = _make_blocking_handler(block_event, requests)
    server = HTTPServer(("127.0.0.1", 0), handler_cls)  # 127.0.0.1, never localhost
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        yield server, block_event, requests
    finally:
        block_event.set()  # make sure a stuck handler thread can unwind
        server.server_close()
        thread.join(timeout=2.0)


def test_first_sentence_reaches_output_queue_before_second_sentence_is_sent(blocking_sse_server):
    """The core PUNCH-LIST #1 assertion: sentence 1 is observable on the LLM
    stage's output queue WHILE the mock upstream is still blocked mid-reply,
    not only after the whole turn completes."""
    server, block_event, requests = blocking_sse_server
    base_url = "http://127.0.0.1:%d/v1" % server.server_port

    scope = CancelScope()
    config = LLMStageConfig(base_url=base_url, timeout=10.0)
    in_q: "queue.Queue" = queue.Queue()
    out_q: "queue.Queue" = queue.Queue()
    stage = LLMStage(in_q, [out_q], cancel_scope=scope, config=config)

    stage.start()
    try:
        in_q.put(GenerateRequest(turn_id="t1", turn_revision=0, generation=0, text="hi"))

        first = out_q.get(timeout=5.0)
        assert isinstance(first, LLMChunk)
        assert first.text == "First sentence."

        # Sentence 2 must NOT be on the queue yet -- the server is still
        # parked on block_event.wait() and has not written it to the socket.
        with pytest.raises(queue.Empty):
            out_q.get(timeout=0.3)

        # Release the mock upstream; sentence 2 (and EndOfResponse) must
        # follow.
        block_event.set()

        second = out_q.get(timeout=5.0)
        assert isinstance(second, LLMChunk)
        assert second.text == "Second sentence."
        assert second.is_final is True

        end = out_q.get(timeout=5.0)
        assert isinstance(end, EndOfResponse)
        assert end.turn_id == "t1"
    finally:
        stage.stop(join_timeout=2.0)

    assert len(requests) == 1  # exactly one POST for the one turn we fed in


def test_barge_in_mid_stream_drops_subsequent_chunks_through_the_real_stage_thread(
    blocking_sse_server,
):
    """Mirrors ``test_llm_stage.py``'s hermetic barge-in test, but through a
    REAL background thread + REAL HTTP server (rather than calling
    ``process()`` directly) -- proves the incremental-emission fix does not
    regress barge-in: a cancel_scope bump while the server is blocked mid-turn
    must suppress sentence 2 once the server is released."""
    server, block_event, _requests = blocking_sse_server
    base_url = "http://127.0.0.1:%d/v1" % server.server_port

    scope = CancelScope()
    config = LLMStageConfig(base_url=base_url, timeout=10.0)
    in_q: "queue.Queue" = queue.Queue()
    out_q: "queue.Queue" = queue.Queue()
    stage = LLMStage(in_q, [out_q], cancel_scope=scope, config=config)

    stage.start()
    try:
        in_q.put(GenerateRequest(turn_id="t1", turn_revision=0, generation=0, text="hi"))

        first = out_q.get(timeout=5.0)
        assert isinstance(first, LLMChunk)
        assert first.text == "First sentence."

        # Barge-in lands while the server (and thus this stage's stream_fn
        # generator) is still blocked mid-turn.
        scope.cancel()
        block_event.set()  # release the server; its output must now be dropped

        # Nothing else should ever arrive for this now-stale generation --
        # neither the "Second sentence." chunk nor an EndOfResponse.
        with pytest.raises(queue.Empty):
            out_q.get(timeout=1.0)
    finally:
        stage.stop(join_timeout=2.0)
