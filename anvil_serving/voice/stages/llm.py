"""LLM stage: OpenAI Chat Completions (never Responses) via the anvil router
(anvil task T004).

Streams a user turn's reply from ``{base_url}/chat/completions`` using
``urllib`` + the SAME incremental SSE parsing approach as
``anvil_serving/router/backends/sse.py`` (:class:`OpenAIStreamAssembler`),
sentence-batches the streamed text deltas into speakable
:class:`~anvil_serving.voice.messages.LLMChunk` items, strips characters that
read badly to a TTS engine, and sends a thinking-disable directive so a
thinking-by-default local model doesn't burn its token budget reasoning
before ever emitting speech (CLAUDE.md gotcha #6/#9).

Wire contract (non-negotiable):

* NEVER the Responses API -- always POST ``{base_url}/chat/completions``.
  :func:`stream_chat_completion` hardcodes the path suffix; there is no
  parameter that can select ``/responses``.
* ``base_url`` defaults to the anvil router
  (``http://127.0.0.1:8000/v1`` -- 127.0.0.1, never localhost; CLAUDE.md
  gotcha #1).
* The bearer token, if configured, comes from an ENV VAR NAME
  (``api_key_env``), never a literal -- resolved at call time via
  ``os.environ``, mirroring ``voice/config.py``'s secret-hygiene contract.
* ``model`` defaults to the ``"chat-fast"`` anvil intent preset (see
  ``anvil_serving/router/intent.py`` + ``classify.py``): a voice turn is
  latency-sensitive, so it is routed fast-tier-first under the quality gate,
  the same as every other anvil-fronted harness request -- no special-case
  glue needed on the router side.

Stdlib-only: ``json``, ``os``, ``re``, ``urllib.request``/``urllib.error``.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Set

from ...router.backends.sse import OpenAIStreamAssembler, iter_sse_events
from ..cancel_scope import CancelScope
from ..messages import EndOfResponse, GenerateRequest, LLMChunk, LLMToolCall
from .base import BaseStage

DEFAULT_BASE_URL = "http://127.0.0.1:8000/v1"
DEFAULT_MODEL = "chat-fast"  # anvil intent preset: low-latency, fast-tier-first
DEFAULT_HISTORY_MAX_TURNS = 8
DEFAULT_HISTORY_MAX_MESSAGE_CHARS = 1200
DEFAULT_TOOL_RESULT_TIMEOUT = 60.0
DEFAULT_TOOL_CALL_MAX_ROUNDS = 4
DEFAULT_TOOL_RESULT_MAX_CHARS = 12000

# Markdown/formatting characters a TTS engine would mispronounce if spoken
# verbatim (emphasis/heading markers, code fences/backticks, table pipes,
# strikethrough) plus a leading bullet marker on its own line.
_TTS_HOSTILE_RE = re.compile(r"[`*_#>|~]|^\s*[-+]\s+", re.MULTILINE)

# Sentence-end punctuation the sentence-batcher splits speakable chunks on.
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")

ChatMessage = Mapping[str, Any]


@dataclass(frozen=True)
class LLMStreamTextDelta:
    text: str


@dataclass(frozen=True)
class LLMStreamToolCalls:
    tool_calls: List[Dict[str, str]]


@dataclass(frozen=True)
class _PendingToolResult:
    call_id: str
    output: str
    will_continue: bool = False
    suppress_response: bool = False


def strip_tts_hostile(text: str) -> str:
    """Remove markdown/formatting characters a TTS engine would mispronounce."""
    return _TTS_HOSTILE_RE.sub("", text)


@dataclass
class LLMStageConfig:
    """Endpoint + request-shaping config for :class:`LLMStage`."""

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key_env: Optional[str] = None
    timeout: float = 20.0
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    system_prompt: Optional[str] = None
    # Bounded per-Realtime-session memory. Each completed user/assistant turn
    # is replayed as normal Chat Completions messages before the next user
    # utterance. `0` disables memory; message trimming keeps long voice
    # sessions from silently ballooning local/cloud prompt cost.
    history_max_turns: int = DEFAULT_HISTORY_MAX_TURNS
    history_max_message_chars: int = DEFAULT_HISTORY_MAX_MESSAGE_CHARS
    tools: Optional[List[Mapping[str, Any]]] = None
    tool_choice: Optional[str] = None
    tool_result_timeout: float = DEFAULT_TOOL_RESULT_TIMEOUT
    tool_call_max_rounds: int = DEFAULT_TOOL_CALL_MAX_ROUNDS
    tool_result_max_chars: int = DEFAULT_TOOL_RESULT_MAX_CHARS
    # Sent verbatim so a thinking-by-default local model (Qwen3.5, gpt-oss,
    # ...) doesn't burn its max_tokens budget reasoning and return empty
    # content (CLAUDE.md gotcha #6/#9). Both knobs are harmless no-ops on a
    # model/engine that doesn't recognize them.
    disable_thinking_body: Mapping[str, Any] = field(
        default_factory=lambda: {
            "chat_template_kwargs": {"enable_thinking": False},
            "reasoning_effort": "low",
        }
    )
    # Rides as a harmless top-level extension field; both router dialects
    # pass an unknown top-level key straight through into
    # ``InternalRequest.raw`` verbatim, so classify.py's Tier-0 heuristic can
    # read it as a structural low-latency signal -- defense in depth
    # alongside naming the "chat-fast" preset in ``model`` (see
    # anvil_serving/router/classify.py's ``is_voice`` check).
    modality: Optional[str] = "voice"


class LLMClientError(Exception):
    """Raised when the upstream Chat Completions call fails (transport error)."""


def _normalized_history(history: Optional[Sequence[ChatMessage]]) -> List[Dict[str, str]]:
    """Return prior turns as OpenAI chat messages, dropping malformed entries."""
    out: List[Dict[str, str]] = []
    for message in history or ():
        if not isinstance(message, Mapping):
            continue
        role = message.get("role")
        content = message.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content:
            out.append({"role": role, "content": content})
    return out


def _trim_history_text(text: str, max_chars: int) -> str:
    """Normalize whitespace and trim long remembered messages with context."""
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    marker = " ... "
    if max_chars <= len(marker) + 2:
        return cleaned[:max_chars]
    head = max_chars // 2
    tail = max_chars - head - len(marker)
    return cleaned[:head] + marker + cleaned[-tail:]


def _trim_tool_result_output(text: str, max_chars: int) -> str:
    """Trim large tool outputs without changing whitespace inside the result."""
    if len(text) <= max_chars:
        return text
    marker = "\n...[truncated]...\n"
    if max_chars <= len(marker) + 2:
        return text[:max_chars]
    head = max_chars // 2
    tail = max_chars - head - len(marker)
    return text[:head] + marker + text[-tail:]


def _normalize_tools(tools: Optional[Sequence[Mapping[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """Normalize Realtime-style flat function tools to Chat Completions tools."""
    normalized: List[Dict[str, Any]] = []
    for tool in tools or ():
        if not isinstance(tool, Mapping) or tool.get("type") != "function":
            continue
        fn = tool.get("function")
        if isinstance(fn, Mapping):
            name = fn.get("name")
            if not isinstance(name, str) or not name:
                continue
            payload: Dict[str, Any] = {"name": name}
            description = fn.get("description")
            parameters = fn.get("parameters")
        else:
            name = tool.get("name")
            if not isinstance(name, str) or not name:
                continue
            payload = {"name": name}
            description = tool.get("description")
            parameters = tool.get("parameters")
        if isinstance(description, str) and description:
            payload["description"] = description
        if isinstance(parameters, Mapping):
            payload["parameters"] = dict(parameters)
        normalized.append({"type": "function", "function": payload})
    return normalized or None


def _base_request_body(messages: Sequence[Mapping[str, Any]], config: LLMStageConfig) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "model": config.model,
        "messages": [dict(message) for message in messages],
        "stream": True,
    }
    if config.max_tokens is not None:
        body["max_tokens"] = config.max_tokens
    if config.temperature is not None:
        body["temperature"] = config.temperature
    if config.modality:
        body["modality"] = config.modality
    tools = _normalize_tools(config.tools)
    if tools:
        body["tools"] = tools
        if config.tool_choice:
            body["tool_choice"] = config.tool_choice
    for key, value in config.disable_thinking_body.items():
        body[key] = value
    return body


def build_request_body_from_messages(
    messages: Sequence[Mapping[str, Any]], config: LLMStageConfig,
) -> Dict[str, Any]:
    """Build the ``/v1/chat/completions`` request body from prepared messages."""
    return _base_request_body(messages, config)


def build_request_body(
    text: str, config: LLMStageConfig, *, history: Optional[Sequence[ChatMessage]] = None,
) -> Dict[str, Any]:
    """Build the ``/v1/chat/completions`` request body for one user turn."""
    messages: List[Dict[str, Any]] = []
    if config.system_prompt:
        messages.append({"role": "system", "content": config.system_prompt})
    messages.extend(_normalized_history(history))
    messages.append({"role": "user", "content": text})
    return _base_request_body(messages, config)


#: Signature every transport (the real one and test fakes) implements:
#: ``(url, *, data: bytes, headers: dict, timeout: float) -> a file-like
#: response`` (supports ``for line in resp`` and ``.close()``, matching a live
#: ``urllib`` response -- see ``anvil_serving/router/backends/sse.py``'s
#: ``iter_sse_events``). Mirrors ``CloudBackend``'s ``stream_transport`` DI
#: seam (``anvil_serving/router/backends/cloud.py``) so tests can inject a
#: canned in-memory response instead of opening a real socket.
Transport = Callable[..., Any]


def _default_transport(url: str, *, data: bytes, headers: Mapping[str, str], timeout: float):
    """The real transport: POST via ``urllib.request.urlopen``."""
    req = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - local/router URL only
    except urllib.error.URLError as exc:
        raise LLMClientError("LLM stage: request to %s failed: %s" % (url, exc)) from exc


def _post_stream(
    url: str, body: Mapping[str, Any], *, api_key_env: Optional[str], timeout: float,
    transport: Transport,
):
    """POST ``body`` to ``url`` via ``transport``; return its response for SSE parsing.

    NEVER touches ``/v1/responses`` -- callers always build ``url`` by
    appending ``/chat/completions`` (see :func:`stream_chat_completion`).
    """
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key_env:
        token = (os.environ.get(api_key_env) or "").strip()
        if token:
            headers["Authorization"] = "Bearer %s" % token
    data = json.dumps(body).encode("utf-8")
    return transport(url, data=data, headers=headers, timeout=timeout)


def stream_chat_completion(
    text: str, config: LLMStageConfig, *,
    history: Optional[Sequence[ChatMessage]] = None,
    transport: Optional[Transport] = None,
) -> Iterator[str]:
    """Yield text deltas from a streaming ``/v1/chat/completions`` call.

    NEVER ``/v1/responses`` -- the URL is built by string-appending
    ``/chat/completions`` to ``config.base_url``, with no code path that can
    select the Responses API. ``transport`` defaults to the real
    ``urllib``-backed client (:func:`_default_transport`); pass a fake for
    hermetic tests (see ``tests/voice/test_llm_stage.py``).
    """
    messages: List[Dict[str, Any]] = []
    if config.system_prompt:
        messages.append({"role": "system", "content": config.system_prompt})
    messages.extend(_normalized_history(history))
    messages.append({"role": "user", "content": text})
    for event in stream_chat_completion_events(messages, config, transport=transport):
        if isinstance(event, LLMStreamTextDelta):
            yield event.text


def stream_chat_completion_events(
    messages: Sequence[Mapping[str, Any]], config: LLMStageConfig, *,
    transport: Optional[Transport] = None,
) -> Iterator[Any]:
    """Yield text deltas and final tool-call batches from Chat Completions SSE."""
    url = config.base_url.rstrip("/") + "/chat/completions"
    resp = _post_stream(
        url, build_request_body_from_messages(messages, config),
        api_key_env=config.api_key_env, timeout=config.timeout,
        transport=transport or _default_transport,
    )
    assembler = OpenAIStreamAssembler()
    try:
        for event, data in iter_sse_events(resp):
            delta = assembler.feed(event, data)
            if delta:
                yield LLMStreamTextDelta(delta)
            if assembler.done:
                break
        result = assembler.result()
        if result.tool_calls:
            calls: List[Dict[str, str]] = []
            for raw in result.tool_calls:
                name = raw.get("name")
                call_id = raw.get("id")
                arguments = raw.get("arguments")
                if not isinstance(name, str) or not name:
                    continue
                if not isinstance(call_id, str) or not call_id:
                    call_id = "call_%d" % (len(calls) + 1)
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments if arguments is not None else {})
                calls.append({"id": call_id, "name": name, "arguments": arguments})
            if calls:
                yield LLMStreamToolCalls(calls)
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


class SentenceBatcher:
    """Accumulates streamed text deltas and yields complete, TTS-cleaned
    sentence chunks as soon as sentence-ending punctuation arrives."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> List[str]:
        """Feed one text delta; return zero or more completed sentences."""
        self._buf += delta
        parts = _SENTENCE_END_RE.split(self._buf)
        if len(parts) <= 1:
            return []
        *complete, remainder = parts
        self._buf = remainder
        return [strip_tts_hostile(p).strip() for p in complete if p.strip()]

    def flush(self) -> Optional[str]:
        """Return any trailing partial sentence once the stream has ended."""
        text = strip_tts_hostile(self._buf).strip()
        self._buf = ""
        return text or None


StreamFn = Callable[..., Iterator[Any]]


class LLMStage(BaseStage):
    """Streams an LLM reply for each :class:`GenerateRequest`, emitting
    sentence-batched :class:`LLMChunk` items followed by an
    :class:`EndOfResponse`.

    ``process`` is a GENERATOR: each :class:`LLMChunk` is yielded the instant
    its sentence-batch is complete, while :func:`stream_chat_completion` is
    still blocked reading the REST of the reply off the wire.
    :class:`~anvil_serving.voice.stages.base.BaseStage` pulls one yielded item
    at a time and puts it on the downstream queue immediately (see
    ``base.py``'s ``_run``) -- so first-audio latency downstream tracks
    first-SENTENCE latency, not full-reply latency. Do NOT collect chunks
    into a list and return it at the end; that would silently defeat the
    incremental contract this stage exists to provide.

    Checks ``cancel_scope.is_stale`` on the incoming request AND on every
    streamed delta: a barge-in landing mid-stream stops emitting further
    chunks for that now-superseded generation (the in-flight HTTP request
    itself is not aborted -- only its OUTPUT stops being forwarded downstream;
    aborting the socket is a possible future refinement, not required for
    THIS stage's own correctness).

    HONESTY NOTE: whatever this stage already emitted for a since-superseded
    generation is still forwarded -- it is a LATER-unit OBLIGATION for
    whatever real stage(s) sit downstream (real TTS / the realtime unit) to
    also check ``cancel_scope.is_stale`` on what they receive and drop it
    there. Today's default downstream wiring (``pipeline.py``'s
    ``EchoTTSStage``/bridge stubs) does NOT do this -- it holds no
    ``cancel_scope`` at all and forwards everything -- so nothing downstream
    of this stage currently drops a stale item on its own. (The real
    ``stages/tts.py::TTSStage`` DOES already check ``is_stale``, but it is not
    yet the stage ``VoicePipeline`` wires by default.)

    Q1 fix (Opus gate): a mid-turn EXCEPTION (e.g. ``self._stream_fn`` raising
    because the upstream connection dropped) still guarantees a terminal
    :class:`EndOfResponse` is emitted before the exception propagates -- a
    client can no longer hang on an unterminated turn just because the
    upstream call failed partway through. This is distinct from the
    barge-in/stale ``return`` path above, which stays deliberately silent (no
    terminal from this stage) because a superseded turn's own terminal is the
    realtime service's job (``RealtimeService._on_response_cancel`` emits a
    ``cancelled`` terminal on the wire for that case) -- only a genuine
    exception reaches the new error-path terminal.
    """

    name = "llm"

    def __init__(
        self,
        in_queue,
        out_queues=None,
        *,
        cancel_scope: Optional[CancelScope] = None,
        config: Optional[LLMStageConfig] = None,
        stream_fn: Optional[StreamFn] = None,
    ) -> None:
        super().__init__(in_queue, out_queues)
        self.cancel_scope = cancel_scope or CancelScope()
        self.config = config or LLMStageConfig()
        self._manifest_system_prompt = self.config.system_prompt
        # Injectable for tests. When omitted, the stage uses the real streaming
        # HTTP client that can surface assembled tool calls.
        self._stream_fn = stream_fn
        self._stream_accepts_history = (
            self._callable_accepts_history(stream_fn) if stream_fn is not None else False
        )
        self._history_max_turns = self._validate_nonnegative_int(
            self.config.history_max_turns, "history_max_turns"
        )
        self._history_max_message_chars = self._validate_positive_int(
            self.config.history_max_message_chars, "history_max_message_chars"
        )
        self._tool_call_max_rounds = self._validate_nonnegative_int(
            self.config.tool_call_max_rounds, "tool_call_max_rounds"
        )
        self._tool_result_timeout = self._validate_positive_number(
            self.config.tool_result_timeout, "tool_result_timeout"
        )
        self._tool_result_max_chars = self._validate_positive_int(
            self.config.tool_result_max_chars, "tool_result_max_chars"
        )
        self._history: List[Dict[str, str]] = []
        self._tool_results: "queue.Queue[_PendingToolResult]" = queue.Queue()
        self._tool_result_lock = threading.Lock()
        self._pending_tool_call_ids: Set[str] = set()

    @staticmethod
    def _validate_nonnegative_int(value: Any, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("%s must be a nonnegative integer" % name)
        return value

    @staticmethod
    def _validate_positive_int(value: Any, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("%s must be a positive integer" % name)
        return value

    @staticmethod
    def _validate_positive_number(value: Any, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError("%s must be a positive number" % name)
        return float(value)

    @staticmethod
    def _callable_accepts_history(stream_fn: StreamFn) -> bool:
        try:
            import inspect

            params = inspect.signature(stream_fn).parameters
        except (TypeError, ValueError):
            return False
        return "history" in params or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )

    def configure_realtime_session(self, session: Mapping[str, Any]) -> None:
        """Apply Realtime session options that affect subsequent LLM turns."""
        instructions = session.get("instructions")
        tools = session.get("tools")
        tool_choice = session.get("tool_choice")
        prompts = [
            self._manifest_system_prompt.strip() if self._manifest_system_prompt else "",
            instructions.strip() if isinstance(instructions, str) else "",
        ]
        system_prompt = "\n\n".join(part for part in prompts if part) or None
        self.config = replace(
            self.config,
            system_prompt=system_prompt,
            tools=_normalize_tools(tools if isinstance(tools, list) else None),
            tool_choice=tool_choice.strip() if isinstance(tool_choice, str) and tool_choice.strip() else None,
        )

    def configure_realtime_response(self, session: Mapping[str, Any], response: Mapping[str, Any]) -> None:
        """Apply one response's LLM-shaping overrides without trusting client model ids."""
        merged = dict(session)
        for key in ("instructions", "tools", "tool_choice"):
            if key in response:
                merged[key] = response[key]
        self.configure_realtime_session(merged)

    def submit_tool_result(
        self,
        call_id: str,
        output: str,
        *,
        will_continue: bool = False,
        suppress_response: bool = False,
    ) -> bool:
        """Submit a Realtime function-call output to the active turn, if any."""
        if not call_id:
            return False
        with self._tool_result_lock:
            if call_id not in self._pending_tool_call_ids:
                return False
        self._tool_results.put(
            _PendingToolResult(
                call_id=call_id,
                output=_trim_tool_result_output(output, self._tool_result_max_chars),
                will_continue=will_continue,
                suppress_response=suppress_response,
            )
        )
        return True

    def _stream_turn(
        self, text: str, history: Sequence[ChatMessage], messages: Sequence[Mapping[str, Any]],
    ) -> Iterator[Any]:
        if self._stream_fn is None:
            return stream_chat_completion_events(messages, self.config)
        if self._stream_accepts_history:
            return self._stream_fn(text, self.config, history=history)
        return self._stream_fn(text, self.config)

    def _stream_events(
        self, text: str, history: Sequence[ChatMessage], messages: Sequence[Mapping[str, Any]],
    ) -> Iterator[Any]:
        for event in self._stream_turn(text, history, messages):
            if isinstance(event, (LLMStreamTextDelta, LLMStreamToolCalls)):
                yield event
            elif isinstance(event, str):
                yield LLMStreamTextDelta(event)

    def _history_snapshot(self) -> List[Dict[str, str]]:
        return [dict(message) for message in self._history]

    def reset_history(self) -> None:
        """Clear remembered turns for this pipeline/session."""
        self._history = []

    def _remember_completed_turn(self, user_text: str, assistant_text: str) -> None:
        if self._history_max_turns <= 0:
            return
        user = _trim_history_text(user_text, self._history_max_message_chars)
        assistant = _trim_history_text(assistant_text, self._history_max_message_chars)
        if not user or not assistant:
            return
        self._history.extend([
            {"role": "user", "content": user},
            {"role": "assistant", "content": assistant},
        ])
        max_messages = self._history_max_turns * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]

    def _request_messages(
        self, history: Sequence[ChatMessage], turn_messages: Sequence[Mapping[str, Any]],
    ) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        if self.config.system_prompt:
            messages.append({"role": "system", "content": self.config.system_prompt})
        messages.extend(_normalized_history(history))
        messages.extend(dict(message) for message in turn_messages)
        return messages

    @staticmethod
    def _assistant_tool_message(
        tool_calls: Sequence[Dict[str, str]], content: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": call["arguments"]},
                }
                for call in tool_calls
            ],
        }

    @staticmethod
    def _tool_result_message(result: _PendingToolResult) -> Dict[str, Any]:
        return {"role": "tool", "tool_call_id": result.call_id, "content": result.output}

    def _set_pending_tool_calls(self, call_ids: Set[str]) -> None:
        with self._tool_result_lock:
            self._pending_tool_call_ids = set(call_ids)

    def _clear_pending_tool_calls(self, call_ids: Set[str]) -> None:
        with self._tool_result_lock:
            self._pending_tool_call_ids.difference_update(call_ids)

    def _wait_for_tool_results(
        self, call_ids: Set[str], generation: int,
    ) -> tuple[List[_PendingToolResult], bool, bool]:
        results: List[_PendingToolResult] = []
        pending = set(call_ids)
        deadline = time.monotonic() + self._tool_result_timeout
        self._set_pending_tool_calls(pending)
        try:
            while pending:
                if self.cancel_scope.is_stale(generation):
                    return results, True, False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return results, False, True
                try:
                    result = self._tool_results.get(timeout=min(0.1, remaining))
                except queue.Empty:
                    continue
                if result.call_id not in pending:
                    continue
                if result.will_continue:
                    continue
                if result.suppress_response:
                    return results, True, False
                results.append(result)
                pending.remove(result.call_id)
            return results, False, False
        finally:
            self._clear_pending_tool_calls(call_ids)

    def _tool_timeout_chunk(self, item: GenerateRequest) -> LLMChunk:
        return LLMChunk(
            turn_id=item.turn_id,
            turn_revision=item.turn_revision,
            generation=item.generation,
            text="I could not get the tool result in time.",
            is_final=True,
        )

    def process(self, item: Any):
        if not isinstance(item, GenerateRequest):
            return  # empty generator: emits nothing
        if self.cancel_scope.is_stale(item.generation):
            return  # superseded by a barge-in before we even started

        batcher = SentenceBatcher()
        history = self._history_snapshot()
        assistant_parts: List[str] = []
        turn_messages: List[Dict[str, Any]] = [{"role": "user", "content": item.text}]
        tool_rounds = 0
        try:
            while True:
                tool_calls: List[Dict[str, str]] = []
                messages = self._request_messages(history, turn_messages)
                custom_history: Sequence[ChatMessage] = history if len(turn_messages) == 1 else turn_messages
                for event in self._stream_events(item.text, custom_history, messages):
                    if self.cancel_scope.is_stale(item.generation):
                        # A barge-in landed mid-stream: stop emitting further
                        # chunks for this now-stale generation. Whatever was
                        # already YIELDED (and, per BaseStage._run, therefore
                        # already put() on the downstream queue) stays forwarded
                        # -- there's no reason to try to withhold it here.
                        # Dropping it instead of forwarding it is a LATER-unit
                        # obligation: the real downstream stage(s) (real TTS /
                        # the realtime unit) must check cancel_scope.is_stale
                        # themselves and drop it there (see the class docstring's
                        # HONESTY NOTE -- today's default stub wiring does not).
                        #
                        # Deliberately NOT a terminal EndOfResponse here: a
                        # superseded (barge-in'd) turn must stay silent on the
                        # wire -- the realtime service's own cancel path
                        # (`RealtimeService._on_response_cancel`) emits the
                        # cancelled turn's terminal instead (see Q1's docstring
                        # note below). This `return` is UNCHANGED by the Q1 fix.
                        return
                    if isinstance(event, LLMStreamToolCalls):
                        tool_calls.extend(event.tool_calls)
                        continue
                    if not isinstance(event, LLMStreamTextDelta):
                        continue
                    assistant_parts.append(event.text)
                    for sentence in batcher.feed(event.text):
                        yield LLMChunk(
                            turn_id=item.turn_id,
                            turn_revision=item.turn_revision,
                            generation=item.generation,
                            text=sentence,
                        )
                if not tool_calls:
                    break
                if tool_rounds >= self._tool_call_max_rounds:
                    timeout = self._tool_timeout_chunk(item)
                    assistant_parts.append(timeout.text)
                    yield timeout
                    break
                tool_rounds += 1
                pre_tool_text = batcher.flush()
                if pre_tool_text:
                    yield LLMChunk(
                        turn_id=item.turn_id,
                        turn_revision=item.turn_revision,
                        generation=item.generation,
                        text=pre_tool_text,
                    )
                assistant_content = "".join(assistant_parts).strip() or None
                turn_messages.append(self._assistant_tool_message(tool_calls, assistant_content))
                call_ids = {call["id"] for call in tool_calls if call.get("id")}
                self._set_pending_tool_calls(call_ids)
                for output_index, call in enumerate(tool_calls):
                    yield LLMToolCall(
                        turn_id=item.turn_id,
                        turn_revision=item.turn_revision,
                        generation=item.generation,
                        item_id=call["id"],
                        call_id=call["id"],
                        name=call["name"],
                        arguments=call["arguments"],
                        output_index=output_index,
                    )
                results, suppressed, timed_out = self._wait_for_tool_results(call_ids, item.generation)
                if suppressed:
                    yield EndOfResponse(
                        turn_id=item.turn_id,
                        turn_revision=item.turn_revision,
                        generation=item.generation,
                    )
                    return
                if timed_out:
                    timeout = self._tool_timeout_chunk(item)
                    assistant_parts.append(timeout.text)
                    yield timeout
                    break
                turn_messages.extend(self._tool_result_message(result) for result in results)
        except Exception:
            # Q1 fix: a mid-turn failure (the streaming HTTP call raising --
            # e.g. the upstream connection dropping mid-reply) used to leave
            # this turn dangling with no terminal EndOfResponse ever reaching
            # the client, since the code path that yields it below is never
            # reached when the loop above raises instead of completing. Emit
            # the terminal here on the way out so a client can never hang on
            # an unterminated turn, then re-raise so BaseStage._run's own
            # per-item exception isolation still logs the failure (this does
            # NOT apply to the barge-in `return` above -- only a genuine
            # exception reaches this branch).
            yield EndOfResponse(
                turn_id=item.turn_id,
                turn_revision=item.turn_revision,
                generation=item.generation,
            )
            raise

        if self.cancel_scope.is_stale(item.generation):
            return

        trailing = batcher.flush()
        if trailing:
            yield LLMChunk(
                turn_id=item.turn_id,
                turn_revision=item.turn_revision,
                generation=item.generation,
                text=trailing,
                is_final=True,
            )
        self._remember_completed_turn(item.text, "".join(assistant_parts))
        yield EndOfResponse(
            turn_id=item.turn_id,
            turn_revision=item.turn_revision,
            generation=item.generation,
        )
