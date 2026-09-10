"""Bounded JSONL transport for an isolated official Pi RPC runner.

This module deliberately knows no browser request data.  Callers supply a
pre-approved argv, checkout cwd, session directory and resolved credential
environment through the Workbench runner policy.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import select
import subprocess
import uuid
from typing import Any, Callable, Mapping, NewType, Protocol, Sequence


PiCommandId = NewType("PiCommandId", str)
PiRunnerId = NewType("PiRunnerId", str)


class PiRpcError(RuntimeError):
    """A Pi runner protocol or lifecycle error."""


class PiProtocolError(PiRpcError):
    """A runner emitted a record outside the LF-delimited JSON contract."""


class PiProcessExited(PiRpcError):
    """The runner ended before the caller could complete its operation."""


@dataclass(frozen=True)
class PiResponse:
    command_id: PiCommandId
    command: str
    ok: bool
    data: Any = None
    error: str | None = None


@dataclass(frozen=True)
class PiEvent:
    """A normalized Pi event with the original JSON retained for audit storage."""

    kind: str
    data: Mapping[str, Any]
    raw: Mapping[str, Any]


class ProcessLike(Protocol):
    stdin: Any
    stdout: Any

    def poll(self) -> int | None: ...


ProcessFactory = Callable[..., ProcessLike]
ReadChunk = Callable[[ProcessLike, int], bytes]


class JsonlDecoder:
    """Strict byte-oriented LF framing.  JSON may contain escaped newlines only."""

    def __init__(self, max_record_bytes: int = 262_144) -> None:
        if max_record_bytes < 1:
            raise ValueError("max_record_bytes must be positive")
        self._max = max_record_bytes
        self._pending = b""

    def feed(self, chunk: bytes) -> list[Mapping[str, Any]]:
        if not isinstance(chunk, bytes):
            raise TypeError("Pi RPC output must be bytes")
        self._pending += chunk
        if len(self._pending) > self._max and b"\n" not in self._pending:
            raise PiProtocolError("Pi RPC record exceeds configured limit")
        records: list[Mapping[str, Any]] = []
        while True:
            position = self._pending.find(b"\n")
            if position < 0:
                break
            line, self._pending = self._pending[:position], self._pending[position + 1 :]
            if line.endswith(b"\r"):
                line = line[:-1]
            if len(line) > self._max:
                raise PiProtocolError("Pi RPC record exceeds configured limit")
            if not line:
                raise PiProtocolError("Pi RPC emitted an empty record")
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PiProtocolError("Pi RPC emitted invalid JSON") from exc
            if not isinstance(value, dict):
                raise PiProtocolError("Pi RPC record must be an object")
            records.append(value)
        return records

    def finish(self) -> None:
        if self._pending:
            raise PiProtocolError("Pi RPC closed with an unterminated record")


def _default_process_factory(argv: Sequence[str], **kwargs: Any) -> ProcessLike:
    return subprocess.Popen(  # noqa: S603 - argv is approved configuration, never browser input.
        list(argv),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
        **kwargs,
    )


def _default_read_chunk(process: ProcessLike, maximum: int) -> bytes:
    stream = process.stdout
    try:
        ready, _, _ = select.select([stream], [], [], 0)
    except (OSError, ValueError, TypeError):
        return b""
    if not ready:
        return b""
    return os.read(stream.fileno(), maximum)


def _event_kind(record: Mapping[str, Any]) -> str:
    event_type = str(record.get("type", ""))
    if event_type in {"message_update", "message"}:
        return "text"
    if event_type.startswith("tool_execution") or event_type in {"tool_call", "tool_result"}:
        return "tool"
    if "extension" in event_type or event_type in {"ui_request", "ui_response"}:
        return "extension"
    if event_type in {"agent_start", "agent_end", "turn_start", "turn_end"}:
        return "lifecycle"
    if event_type == "error":
        return "error"
    return "event"


class PiRpcClient:
    """Non-blocking, bounded command/event bridge to one Pi ``--mode rpc`` process."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        process_factory: ProcessFactory = _default_process_factory,
        read_chunk: ReadChunk = _default_read_chunk,
        max_record_bytes: int = 262_144,
        max_poll_records: int = 128,
        max_pending_commands: int = 64,
    ) -> None:
        if not argv or any(not isinstance(part, str) or not part for part in argv):
            raise ValueError("Pi runner argv must be a non-empty configured sequence")
        if not cwd.is_absolute():
            raise ValueError("Pi runner checkout cwd must be absolute")
        if max_poll_records < 1 or max_pending_commands < 1:
            raise ValueError("max_poll_records must be positive")
        self._argv = tuple(argv)
        self._cwd = cwd
        self._environment = dict(environment)
        self._factory = process_factory
        self._read_chunk = read_chunk
        self._decoder = JsonlDecoder(max_record_bytes)
        self._max_poll_records = max_poll_records
        self._max_pending = max_pending_commands
        self._process: ProcessLike | None = None
        self._responses: dict[PiCommandId, PiResponse] = {}
        self._pending: dict[PiCommandId, str] = {}
        self._queued_events: list[PiEvent] = []

    @property
    def started(self) -> bool:
        return self._process is not None

    def start(self) -> None:
        if self._process is not None:
            return
        self._process = self._factory(self._argv, cwd=str(self._cwd), env=dict(self._environment))

    def send(self, command_type: str, **fields: Any) -> PiCommandId:
        self._require_live()
        if len(self._pending) >= self._max_pending:
            raise PiRpcError("Pi command queue reached its configured limit")
        command_id = PiCommandId(uuid.uuid4().hex)
        record = {"id": command_id, "type": command_type, **fields}
        encoded = json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
        assert self._process is not None
        self._process.stdin.write(encoded)
        self._process.stdin.flush()
        self._pending[command_id] = command_type
        return command_id

    def prompt(self, message: str) -> PiCommandId:
        return self.send("prompt", message=self._message(message))

    def steer(self, message: str) -> PiCommandId:
        return self.send("steer", message=self._message(message))

    def abort(self) -> PiCommandId:
        return self.send("abort")

    def get_state(self) -> PiCommandId:
        return self.send("get_state")

    def set_model(self, provider: str, model_id: str) -> PiCommandId:
        return self.send("set_model", provider=self._identifier(provider), modelId=self._identifier(model_id))

    def set_thinking_level(self, level: str) -> PiCommandId:
        return self.send("set_thinking_level", level=self._identifier(level))

    def fork(self, entry_id: str) -> PiCommandId:
        return self.send("fork", entryId=self._identifier(entry_id))

    def clone(self) -> PiCommandId:
        return self.send("clone")

    def extension_response(self, request_id: str, response: Mapping[str, Any]) -> None:
        """Reply to a Pi extension request.

        Pi reserves ``id`` for the extension request here and deliberately does
        not emit a command acknowledgement, unlike ordinary RPC commands.
        """
        self._require_live()
        if not isinstance(response, Mapping):
            raise ValueError("extension response must be an object")
        allowed = {"value", "confirmed", "cancelled"}
        if len(response) != 1 or not set(response).issubset(allowed):
            raise ValueError("extension response must contain value, confirmed, or cancelled")
        record = {"id": self._identifier(request_id), "type": "extension_ui_response", **dict(response)}
        assert self._process is not None
        self._process.stdin.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n")
        self._process.stdin.flush()

    def close(self) -> None:
        """Terminate the isolated runner when its lease expires or service stops."""
        if self._process is not None and self._process.poll() is None:
            terminate = getattr(self._process, "terminate", None)
            if callable(terminate):
                terminate()
            wait = getattr(self._process, "wait", None)
            if callable(wait):
                try:
                    wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    wait(timeout=2)

    def take_response(self, command_id: PiCommandId) -> PiResponse | None:
        response = self._responses.pop(command_id, None)
        if response is not None:
            self._pending.pop(command_id, None)
        return response

    def responses(self) -> tuple[PiResponse, ...]:
        """Peek at completed responses until durable processing acknowledges each."""
        return tuple(self._responses.values())

    def drain_responses(self) -> tuple[PiResponse, ...]:
        """Consume bounded completed command outcomes after a poll."""
        values = tuple(self._responses.values())
        self._responses.clear()
        return values

    def poll(self) -> list[PiEvent]:
        """Read only currently available records; never hold an HTTP request open."""
        self._require_live()
        assert self._process is not None
        events = self._queued_events[: self._max_poll_records]
        del self._queued_events[: self._max_poll_records]
        while len(events) < self._max_poll_records:
            chunk = self._read_chunk(self._process, 65_536)
            if not chunk:
                break
            for record in self._decoder.feed(chunk):
                if record.get("type") == "response":
                    self._store_response(record)
                    continue
                event = PiEvent(_event_kind(record), dict(record), dict(record))
                if len(events) < self._max_poll_records:
                    events.append(event)
                else:
                    self._queued_events.append(event)
        code = self._process.poll()
        if code is not None:
            self._decoder.finish()
            raise PiProcessExited(f"Pi runner exited with status {code}")
        return events

    def _store_response(self, record: Mapping[str, Any]) -> None:
        raw_id = record.get("id")
        command = record.get("command")
        if not isinstance(raw_id, str) or not isinstance(command, str):
            raise PiProtocolError("Pi RPC response lacks id or command")
        command_id = PiCommandId(raw_id)
        expected = self._pending.pop(command_id, None)
        if expected is None or expected != command:
            raise PiProtocolError("Pi RPC response does not correlate to a pending command")
        self._responses[command_id] = PiResponse(
            command_id=command_id,
            command=command,
            ok=bool(record.get("success")),
            data=record.get("data"),
            error=record.get("error") if isinstance(record.get("error"), str) else None,
        )

    def _require_live(self) -> None:
        if self._process is None:
            raise PiRpcError("Pi runner has not started")
        code = self._process.poll()
        if code is not None:
            raise PiProcessExited(f"Pi runner exited with status {code}")

    @staticmethod
    def _message(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("message must be non-empty")
        return value

    @staticmethod
    def _identifier(value: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 512:
            raise ValueError("identifier must be a bounded non-empty string")
        return value
