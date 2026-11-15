"""Real-stage incremental TTS emission contract."""

import array
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.messages import AudioOut, EndOfResponse, SpokenText, TTSInput
from anvil_serving.voice.stages.tts import TTSStage, TTSStageConfig


def _int16_bytes(values) -> bytes:
    return array.array("h", values).tobytes()


_AUDIO_1 = _int16_bytes([100, 200, 300, 400])
_AUDIO_2 = _int16_bytes([500, 600, 700, 800])


def _make_blocking_speech_handler(block_event, requests):
    class BlockingSpeechHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            requests.append(self.rfile.read(length) if length else b"")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(_AUDIO_1)
            self.wfile.flush()
            block_event.wait(timeout=5.0)
            self.wfile.write(_AUDIO_2)
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass

    return BlockingSpeechHandler


@pytest.fixture
def blocking_speech_server():
    block_event = threading.Event()
    requests = []
    server = HTTPServer(("127.0.0.1", 0), _make_blocking_speech_handler(block_event, requests))
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        yield server, block_event, requests
    finally:
        block_event.set()
        server.server_close()
        thread.join(timeout=2.0)


def test_first_audio_chunk_reaches_output_queue_before_second_chunk_is_sent(blocking_speech_server):
    server, block_event, requests = blocking_speech_server
    in_queue = queue.Queue()
    out_queue = queue.Queue()
    stage = TTSStage(in_queue, [out_queue], cancel_scope=CancelScope(), config=TTSStageConfig(base_url=f"http://127.0.0.1:{server.server_port}/v1", timeout=10.0, chunk_bytes=8, source_sample_rate=16000, target_sample_rate=16000))
    stage.start()
    try:
        in_queue.put(TTSInput(turn_id="t1", turn_revision=0, generation=0, text="hello"))
        first = out_queue.get(timeout=5.0)
        assert isinstance(first, AudioOut)
        assert first.pcm == _AUDIO_1
        spoken = out_queue.get(timeout=5.0)
        assert isinstance(spoken, SpokenText)
        assert spoken.text == "hello"
        with pytest.raises(queue.Empty):
            out_queue.get(timeout=0.3)
        in_queue.put(EndOfResponse(turn_id="t1", turn_revision=0, generation=0))
        block_event.set()
        second = out_queue.get(timeout=5.0)
        assert isinstance(second, AudioOut)
        assert second.pcm == _AUDIO_2
        end = out_queue.get(timeout=5.0)
        assert isinstance(end, EndOfResponse)
        assert end.turn_id == "t1"
    finally:
        stage.stop(join_timeout=2.0)
    assert len(requests) == 1
