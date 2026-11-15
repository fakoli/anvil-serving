"""Real-stage incremental LLM emission and cancellation contracts."""

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.messages import EndOfResponse, GenerateRequest, LLMChunk
from anvil_serving.voice.stages.llm import LLMStage, LLMStageConfig


def _sse_chunk(text: str, finish: str | None = None) -> bytes:
    delta = {"content": text} if text else {}
    return b"data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}).encode() + b"\n\n"


def _make_blocking_handler(block_event, requests):
    class BlockingSSEHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            requests.append(self.rfile.read(length) if length else b"")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(_sse_chunk("First sentence. "))
            self.wfile.flush()
            block_event.wait(timeout=5.0)
            self.wfile.write(_sse_chunk("Second sentence.", finish="stop"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass

    return BlockingSSEHandler


@pytest.fixture
def blocking_sse_server():
    block_event = threading.Event()
    requests = []
    server = HTTPServer(("127.0.0.1", 0), _make_blocking_handler(block_event, requests))
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        yield server, block_event, requests
    finally:
        block_event.set()
        server.server_close()
        thread.join(timeout=2.0)


def _stage(server, scope):
    in_queue = queue.Queue()
    out_queue = queue.Queue()
    stage = LLMStage(in_queue, [out_queue], cancel_scope=scope, config=LLMStageConfig(base_url=f"http://127.0.0.1:{server.server_port}/v1", timeout=10.0))
    return stage, in_queue, out_queue


def test_first_sentence_reaches_output_queue_before_second_sentence_is_sent(blocking_sse_server):
    server, block_event, requests = blocking_sse_server
    stage, in_queue, out_queue = _stage(server, CancelScope())
    stage.start()
    try:
        in_queue.put(GenerateRequest(turn_id="t1", turn_revision=0, generation=0, text="hi"))
        first = out_queue.get(timeout=5.0)
        assert isinstance(first, LLMChunk)
        assert first.text == "First sentence."
        with pytest.raises(queue.Empty):
            out_queue.get(timeout=0.3)
        block_event.set()
        second = out_queue.get(timeout=5.0)
        assert isinstance(second, LLMChunk)
        assert second.text == "Second sentence."
        assert second.is_final is True
        end = out_queue.get(timeout=5.0)
        assert isinstance(end, EndOfResponse)
        assert end.turn_id == "t1"
    finally:
        stage.stop(join_timeout=2.0)
    assert len(requests) == 1


def test_barge_in_mid_stream_drops_subsequent_chunks_through_the_real_stage_thread(blocking_sse_server):
    server, block_event, _requests = blocking_sse_server
    scope = CancelScope()
    stage, in_queue, out_queue = _stage(server, scope)
    stage.start()
    try:
        in_queue.put(GenerateRequest(turn_id="t1", turn_revision=0, generation=0, text="hi"))
        first = out_queue.get(timeout=5.0)
        assert isinstance(first, LLMChunk)
        assert first.text == "First sentence."
        scope.cancel()
        block_event.set()
        with pytest.raises(queue.Empty):
            out_queue.get(timeout=1.0)
    finally:
        stage.stop(join_timeout=2.0)
