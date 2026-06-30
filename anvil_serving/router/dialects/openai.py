"""OpenAI Chat Completions dialect.

Wire shape: ``messages: [{role, content}]`` (``system`` rides as a message with
``role == "system"``), ``max_tokens``, ``temperature``, ``stream``.

Streaming framing (``stream: true``): a sequence of ``data: <json>\\n\\n`` lines
where each ``<json>`` is a ``chat.completion.chunk``, terminated by
``data: [DONE]\\n\\n``. Non-streaming: a single ``chat.completion`` object.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional

from ..internal import (
    InternalRequest,
    estimate_tokens,
    normalize_messages,
)
from . import _new_id


def _sse(obj: Dict[str, Any]) -> bytes:
    """Encode one OpenAI SSE event: ``data: <json>\\n\\n``."""
    return b"data: " + json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\n\n"


def _chunk(cid: str, created: int, model: str,
           delta: Dict[str, Any], finish_reason: Optional[str]) -> Dict[str, Any]:
    return {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {"index": 0, "delta": delta, "finish_reason": finish_reason}
        ],
    }


def _openai_finish_reason(raw: Optional[str]) -> str:
    """Map a raw upstream stop reason to the OpenAI wire ``finish_reason``.

    Passes through OpenAI-native values unchanged; maps Anthropic-style values to
    their OpenAI equivalents; falls back to ``"stop"`` for anything unknown.
    """
    if raw is None:
        return "stop"
    r = str(raw).lower()
    if r in ("stop", "end_turn", "stop_sequence"):
        return "stop"
    if r in ("tool_calls", "tool_use", "function_call"):
        return "tool_calls"
    if r in ("length", "max_tokens", "model_length"):
        return "length"
    return "stop"


class OpenAIDialect:
    """Speak the OpenAI Chat Completions wire protocol."""

    name = "openai"

    def parse_request(self, body: Mapping[str, Any]) -> InternalRequest:
        messages = normalize_messages(body.get("messages"))
        # OpenAI carries the system prompt as a role=system message; surface the
        # first one on `.system` for uniformity while leaving it in `messages`.
        system: Optional[str] = None
        for m in messages:
            if m.role == "system":
                system = m.content
                break
        max_tokens = body.get("max_tokens")
        if max_tokens is None:
            max_tokens = body.get("max_completion_tokens")
        return InternalRequest(
            model=str(body.get("model") or "chat"),
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            temperature=body.get("temperature"),
            stream=bool(body.get("stream", False)),
            dialect=self.name,
            raw=dict(body),
        )

    def stream(
        self,
        request: InternalRequest,
        deltas: Iterable[str],
        *,
        get_structured: Optional[Callable[[], Any]] = None,
    ) -> Iterator[bytes]:
        """Stream the response as OpenAI Chat Completions SSE chunks.

        ``get_structured`` is an optional zero-argument callable invoked **after**
        all ``deltas`` are exhausted.  It should return a
        :class:`~anvil_serving.router.internal.StructuredResult` (or ``None``).
        When provided and non-``None``, the real ``finish_reason`` and any
        ``tool_calls`` chunks are emitted.  The text path is unaffected
        (``get_structured=None`` → ``finish_reason="stop"``).

        Streaming tool calls are rendered as two chunks per call: a header chunk
        that announces the call id/name and an arguments chunk carrying the full
        serialized arguments string (consolidated streaming — avoids partial JSON).
        """
        cid = _new_id("chatcmpl-")
        created = int(time.time())
        model = request.model
        # 1) opening chunk announces the assistant role.
        yield _sse(_chunk(cid, created, model, {"role": "assistant"}, None))
        # 2) one chunk per text delta.
        for piece in deltas:
            yield _sse(_chunk(cid, created, model, {"content": piece}, None))

        # Gather structured fields AFTER deltas are fully consumed (#42 / #52).
        _structured = get_structured() if callable(get_structured) else None
        _tool_calls = _structured.tool_calls if _structured is not None else None
        _finish_reason = _structured.finish_reason if _structured is not None else None

        # 3) Tool-call chunks (if any).  Two chunks per call: header (id/name) then
        #    arguments.  Consolidated streaming — full arguments in one chunk.
        if _tool_calls:
            for _tc_i, _tc in enumerate(_tool_calls):
                _tc_id = _tc.get("id") or _new_id("call_")
                _tc_name = _tc.get("name") or ""
                _tc_args = _tc.get("arguments")
                if isinstance(_tc_args, dict):
                    _tc_args_str = json.dumps(_tc_args)
                else:
                    _tc_args_str = str(_tc_args) if _tc_args is not None else ""
                # Header chunk: id, type, name, empty arguments
                yield _sse(_chunk(cid, created, model, {
                    "tool_calls": [{
                        "index": _tc_i,
                        "id": _tc_id,
                        "type": "function",
                        "function": {"name": _tc_name, "arguments": ""},
                    }]
                }, None))
                # Arguments chunk: full serialized arguments
                yield _sse(_chunk(cid, created, model, {
                    "tool_calls": [{
                        "index": _tc_i,
                        "function": {"arguments": _tc_args_str},
                    }]
                }, None))

        # 4) Final chunk: empty delta + real finish_reason, then [DONE].
        yield _sse(_chunk(cid, created, model, {}, _openai_finish_reason(_finish_reason)))
        yield b"data: [DONE]\n\n"

    def render(
        self,
        request: InternalRequest,
        text: str,
        *,
        structured: Any = None,
    ) -> Dict[str, Any]:
        """Build a non-streaming Chat Completions response.

        ``structured`` is an optional
        :class:`~anvil_serving.router.internal.StructuredResult` from the backend's
        thread-local.  When provided, the real ``finish_reason`` and any
        ``tool_calls`` are included.  ``structured=None`` (the default) produces the
        same output as before this change, preserving the text-path wire shape.
        """
        _tool_calls = structured.tool_calls if structured is not None else None
        _finish_reason = structured.finish_reason if structured is not None else None

        message: Dict[str, Any] = {"role": "assistant", "content": text if text else None}
        if _tool_calls:
            tc_wire = []
            for _tc in _tool_calls:
                _tc_args = _tc.get("arguments")
                if isinstance(_tc_args, dict):
                    _tc_args_str = json.dumps(_tc_args)
                else:
                    _tc_args_str = str(_tc_args) if _tc_args is not None else ""
                tc_wire.append({
                    "id": _tc.get("id") or _new_id("call_"),
                    "type": "function",
                    "function": {
                        "name": _tc.get("name") or "",
                        "arguments": _tc_args_str,
                    },
                })
            message["tool_calls"] = tc_wire

        prompt_texts: List[str] = [m.content for m in request.messages]
        prompt = estimate_tokens(prompt_texts)
        completion = estimate_tokens([text])
        return {
            "id": _new_id("chatcmpl-"),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": _openai_finish_reason(_finish_reason),
                }
            ],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
        }

    def render_error(self, status: int, etype: str, message: str) -> Dict[str, Any]:
        # OpenAI's native error envelope.
        return {"error": {"type": etype, "message": message}}
