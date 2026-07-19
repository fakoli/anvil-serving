"""Incremental SSE parsing + per-dialect stream assembly for the streaming relay.

The relay backends historically issued NON-streaming upstream calls and
fake-chunked the buffered reply, so a trusted ("allow") tier's TTFT was the full
generation time. This module is the read side of the real streaming path:

* :func:`iter_sse_events` — parse a binary file-like (a live ``urllib``
  response) into ``(event_name, data)`` SSE events, incrementally.
* :class:`OpenAIStreamAssembler` / :class:`AnthropicStreamAssembler` — feed
  events in, get text deltas out as they arrive, and read the assembled
  :class:`~anvil_serving.router.internal.StructuredResult` (finish_reason,
  tool_calls, usage) once the stream ends. The assembled shapes mirror the
  buffered path's ``CloudBackend._extract_structured`` exactly (OpenAI
  ``arguments`` stays a JSON string; Anthropic ``arguments`` is a parsed dict),
  so the verify chain and the dialect renderers see identical data either way.

Stdlib-only; pure parsing — no sockets are opened here. Malformed events are
skipped, never raised: a garbled chunk degrades to lost deltas, and the
downstream verify gate (NonEmptyContent et al.) owns judging the result.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

from ..internal import StructuredResult

#: OpenAI's stream terminator payload.
DONE_SENTINEL = "[DONE]"


def iter_sse_events(fp) -> Iterator[Tuple[Optional[str], str]]:
    """Yield ``(event_name, data)`` per SSE event from binary file-like ``fp``.

    Follows the SSE framing both providers use: events are separated by a blank
    line; ``event:`` names the event (Anthropic), ``data:`` lines carry the
    payload (joined with newlines when split); comment lines (``:``) are
    skipped. Iterating ``fp`` line-by-line keeps this incremental — each event
    is yielded as soon as its blank-line terminator arrives on the wire.
    """
    event: Optional[str] = None
    data_lines: List[str] = []
    for raw in fp:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data_lines:
                yield event, "\n".join(data_lines)
            event, data_lines = None, []
            continue
        if line.startswith(":"):
            continue  # SSE comment / keep-alive
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip(" "))
    if data_lines:  # stream ended without a trailing blank line
        yield event, "\n".join(data_lines)


def _loads(data: str) -> Optional[Mapping[str, Any]]:
    """Parse one event payload; ``None`` for non-JSON / non-object payloads."""
    try:
        obj = json.loads(data)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, Mapping) else None


class OpenAIStreamAssembler:
    """Assemble an OpenAI ``chat.completion.chunk`` stream.

    ``feed`` returns the chunk's text delta (or ``None``); tool-call fragments
    are merged by index (``id``/``name`` announced once, ``arguments`` appended
    across chunks); the final chunk's ``finish_reason`` and — when the server
    honours ``stream_options.include_usage`` — the trailing ``usage`` block are
    captured for :meth:`result`.
    """

    def __init__(self) -> None:
        self.done = False
        self._finish_reason: Optional[str] = None
        self._usage: Optional[Dict[str, int]] = None
        self._tool_calls: Dict[int, Dict[str, str]] = {}

    def feed(self, event: Optional[str], data: str) -> Optional[str]:
        if data == DONE_SENTINEL:
            self.done = True
            return None
        obj = _loads(data)
        if obj is None:
            return None
        usage = obj.get("usage")
        if isinstance(usage, Mapping):
            i, o = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if (isinstance(i, int) and not isinstance(i, bool) and i >= 0
                    and isinstance(o, int) and not isinstance(o, bool) and o >= 0):
                self._usage = {"input_tokens": i, "output_tokens": o}
        choices = obj.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0]
        if not isinstance(first, Mapping):
            return None
        if first.get("finish_reason"):
            self._finish_reason = str(first["finish_reason"])
        delta = first.get("delta")
        if not isinstance(delta, Mapping):
            return None
        raw_tcs = delta.get("tool_calls")
        if isinstance(raw_tcs, list):
            for tc in raw_tcs:
                if not isinstance(tc, Mapping):
                    continue
                idx = tc.get("index")
                idx = idx if isinstance(idx, int) else 0
                slot = self._tool_calls.setdefault(
                    idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = str(tc["id"])
                fn = tc.get("function")
                if isinstance(fn, Mapping):
                    if fn.get("name"):
                        slot["name"] = str(fn["name"])
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        slot["arguments"] += args
        content = delta.get("content")
        return content if isinstance(content, str) and content else None

    def result(self) -> StructuredResult:
        tool_calls: Optional[List[Dict[str, Any]]] = None
        if self._tool_calls:
            tool_calls = [
                {"name": slot["name"], "id": slot["id"],
                 "arguments": slot["arguments"]}
                for _, slot in sorted(self._tool_calls.items())
            ]
        return StructuredResult(
            finish_reason=self._finish_reason,
            tool_calls=tool_calls,
            usage=self._usage,
        )


class AnthropicStreamAssembler:
    """Assemble an Anthropic Messages named-event stream.

    ``text_delta`` chunks are the yielded deltas; ``tool_use`` blocks are
    registered at ``content_block_start`` and their ``input_json_delta``
    fragments accumulated per block index (parsed to a dict at :meth:`result`,
    matching the buffered path's already-parsed ``input``); ``message_start``
    carries ``input_tokens`` and ``message_delta`` the ``stop_reason`` +
    ``output_tokens``.
    """

    def __init__(self) -> None:
        self.done = False
        self._finish_reason: Optional[str] = None
        self._input_tokens: Optional[int] = None
        self._output_tokens: Optional[int] = None
        self._tools: Dict[int, Dict[str, Any]] = {}  # index -> {id,name,parts}

    @staticmethod
    def _int(v: Any) -> Optional[int]:
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None

    def feed(self, event: Optional[str], data: str) -> Optional[str]:
        obj = _loads(data)
        if obj is None:
            return None
        etype = obj.get("type") or event
        if etype == "message_start":
            msg = obj.get("message")
            usage = msg.get("usage") if isinstance(msg, Mapping) else None
            if isinstance(usage, Mapping):
                self._input_tokens = self._int(usage.get("input_tokens"))
            return None
        if etype == "content_block_start":
            block = obj.get("content_block")
            idx = obj.get("index")
            if (isinstance(block, Mapping) and block.get("type") == "tool_use"
                    and isinstance(idx, int)):
                self._tools[idx] = {
                    "id": str(block.get("id") or ""),
                    "name": str(block.get("name") or ""),
                    "parts": [],
                }
            return None
        if etype == "content_block_delta":
            delta = obj.get("delta")
            if not isinstance(delta, Mapping):
                return None
            dtype = delta.get("type")
            if dtype == "text_delta":
                text = delta.get("text")
                return text if isinstance(text, str) and text else None
            if dtype == "input_json_delta":
                idx = obj.get("index")
                part = delta.get("partial_json")
                if isinstance(idx, int) and idx in self._tools and isinstance(part, str):
                    self._tools[idx]["parts"].append(part)
            return None
        if etype == "message_delta":
            delta = obj.get("delta")
            if isinstance(delta, Mapping) and delta.get("stop_reason"):
                self._finish_reason = str(delta["stop_reason"])
            usage = obj.get("usage")
            if isinstance(usage, Mapping):
                out = self._int(usage.get("output_tokens"))
                if out is not None:
                    self._output_tokens = out
            return None
        if etype == "message_stop":
            self.done = True
        return None

    def result(self) -> StructuredResult:
        tool_calls: Optional[List[Dict[str, Any]]] = None
        if self._tools:
            calls: List[Dict[str, Any]] = []
            for _, slot in sorted(self._tools.items()):
                joined = "".join(slot["parts"])
                try:
                    args = json.loads(joined) if joined.strip() else {}
                    if not isinstance(args, dict):
                        # Preserve the raw wire value so ToolCallJSONValid can
                        # reject a non-object instead of laundering it into an
                        # apparently valid no-argument call.
                        args = joined
                except (ValueError, TypeError):
                    # Verification owns malformed tool arguments.  Keeping the
                    # raw fragment makes the parse failure observable there;
                    # coercing it to {} can falsely pass a no-required-args
                    # schema and execute the wrong call.
                    args = joined
                calls.append({"name": slot["name"], "id": slot["id"],
                              "arguments": args})
            tool_calls = calls
        usage: Optional[Dict[str, int]] = None
        if self._input_tokens is not None and self._output_tokens is not None:
            usage = {"input_tokens": self._input_tokens,
                     "output_tokens": self._output_tokens}
        return StructuredResult(
            finish_reason=self._finish_reason,
            tool_calls=tool_calls,
            usage=usage,
        )
