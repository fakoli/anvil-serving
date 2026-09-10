from __future__ import annotations

import json
from pathlib import Path

import pytest

from anvil_serving.workbench_app.pi_rpc import JsonlDecoder, PiProcessExited, PiProtocolError, PiRpcClient


class _Input:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, value: bytes) -> int:
        self.writes.append(value)
        return len(value)

    def flush(self) -> None:
        pass


class _Process:
    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self.stdin = _Input()
        self.stdout = object()
        self.chunks = chunks or []
        self.exit_code: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True


def _client(process: _Process, tmp_path: Path) -> PiRpcClient:
    return PiRpcClient(
        ("pi", "--mode", "rpc"),
        cwd=tmp_path / "checkout",
        environment={"PI_CODING_AGENT_DIR": str(tmp_path / "agent")},
        process_factory=lambda *_args, **_kwargs: process,
        read_chunk=lambda instance, _size: instance.chunks.pop(0) if instance.chunks else b"",
    )


def test_decoder_requires_lf_json_objects_and_handles_crlf() -> None:
    decoder = JsonlDecoder(64)
    assert decoder.feed(b'{"type":"x"}\r\n') == [{"type": "x"}]
    with pytest.raises(PiProtocolError):
        decoder.feed(b"not json\n")
    with pytest.raises(PiProtocolError):
        JsonlDecoder(5).feed(b"123456")


def test_commands_are_jsonl_and_response_is_distinct_from_events(tmp_path: Path) -> None:
    process = _Process()
    client = _client(process, tmp_path)
    client.start()
    command_id = client.prompt("inspect contract")
    sent = json.loads(process.stdin.writes[-1])
    assert sent == {"id": command_id, "type": "prompt", "message": "inspect contract"}
    process.chunks.append(
        (json.dumps({"type": "response", "id": command_id, "command": "prompt", "success": True}) + "\n"
         + json.dumps({"type": "tool_execution_start", "toolName": "read"}) + "\n").encode()
    )
    events = client.poll()
    assert [event.kind for event in events] == ["tool"]
    assert client.take_response(command_id).ok is True


def test_extension_reply_uses_official_request_id_and_no_fake_command_ack(tmp_path: Path) -> None:
    process = _Process()
    client = _client(process, tmp_path)
    client.start()
    client.extension_response("request-1", {"confirmed": True})
    assert json.loads(process.stdin.writes[-1]) == {
        "id": "request-1",
        "type": "extension_ui_response",
        "confirmed": True,
    }


def test_runner_exit_is_detected_and_close_terminates_live_process(tmp_path: Path) -> None:
    process = _Process()
    client = _client(process, tmp_path)
    client.start()
    process.exit_code = 143
    with pytest.raises(PiProcessExited):
        client.abort()
    process.exit_code = None
    client.close()
    assert process.terminated
