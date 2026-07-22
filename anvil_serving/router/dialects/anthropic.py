"""Anthropic Messages API dialect.

Wire shape: ``messages: [{role, content}]`` plus a top-level optional ``system``;
``max_tokens`` is required by the real API. ``stream`` toggles SSE.

Streaming framing (``stream: true``): named events, each two lines
``event: <type>\\ndata: <json>\\n\\n``, in this order::

    message_start -> content_block_start -> [ping]
      -> content_block_delta (x N) -> content_block_stop
      -> message_delta -> message_stop

Non-streaming: a single ``message`` object with a text content block.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional

from ..internal import (
    DialectError,
    InternalRequest,
    estimate_tokens,
    flatten_content,
    normalize_messages,
    normalize_stop,
)
from . import _new_id


def _event(etype: str, data: Dict[str, Any]) -> bytes:
    """Encode one Anthropic named SSE event: ``event: T\\ndata: <json>\\n\\n``."""
    body = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return b"event: " + etype.encode("utf-8") + b"\n" + b"data: " + body + b"\n\n"


def _input_tokens(request: InternalRequest) -> int:
    texts: List[str] = [m.content for m in request.messages]
    if request.system:
        texts.append(request.system)
    return estimate_tokens(texts)


def _anthropic_stop_reason(raw: Optional[str]) -> str:
    """Map a raw upstream ``finish_reason`` to the Anthropic wire ``stop_reason``.

    Passes through Anthropic-native values unchanged; maps OpenAI-style values to
    their Anthropic equivalents; falls back to ``"end_turn"`` for anything unknown.
    """
    if raw is None:
        return "end_turn"
    r = str(raw).lower()
    if r in ("end_turn", "stop_sequence"):
        return r
    if r == "stop":
        return "end_turn"
    if r in ("tool_use", "tool_calls", "function_call"):
        return "tool_use"
    if r in ("max_tokens", "length", "model_length"):
        return "max_tokens"
    return "end_turn"


class AnthropicDialect:
    """Speak the Anthropic Messages wire protocol."""

    name = "anthropic"

    def parse_request(self, body: Mapping[str, Any]) -> InternalRequest:
        system_raw = body.get("system")
        system = flatten_content(system_raw) if system_raw is not None else None
        # max_tokens is REQUIRED by the Anthropic Messages API; reject its
        # absence with the API's own error type (OpenAI keeps it optional).
        max_tokens = body.get("max_tokens")
        if max_tokens is None:
            raise DialectError(400, "invalid_request_error",
                               "max_tokens: field required")
        return InternalRequest(
            model=str(body.get("model") or "claude"),
            messages=normalize_messages(body.get("messages")),
            system=system,
            max_tokens=max_tokens,
            temperature=body.get("temperature"),
            top_p=body.get("top_p"),
            stop=normalize_stop(body.get("stop_sequences")),
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
        response_model: Optional[str] = None,
    ) -> Iterator[bytes]:
        """Stream the response as Anthropic named SSE events.

        ``get_structured`` is an optional zero-argument callable invoked **after**
        all ``deltas`` are exhausted.  It should return a
        :class:`~anvil_serving.router.internal.StructuredResult` (or ``None``).
        When provided and non-``None``, the real ``stop_reason`` and any
        ``tool_use`` content blocks are emitted instead of the hardcoded defaults.
        The text path is unaffected (``get_structured=None`` → ``"end_turn"``).

        Streaming tool calls are rendered as consolidated blocks: each tool_use
        block is emitted with a single ``input_json_delta`` chunk carrying the
        full serialized input, rather than split partial-json chunks.  This is
        fully wire-compatible with the Anthropic protocol and avoids the risk of
        delivering partial tool-call JSON to the harness.
        """
        msg_id = _new_id("msg_")
        model = response_model or request.model
        input_tokens = _input_tokens(request)

        yield _event("message_start", {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": input_tokens, "output_tokens": 0},
            },
        })
        yield _event("content_block_start", {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        })
        # Optional keep-alive ping (real API emits these); exercises the path.
        yield _event("ping", {"type": "ping"})

        pieces: List[str] = []
        for piece in deltas:
            pieces.append(piece)
            yield _event("content_block_delta", {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": piece},
            })
        # Gather structured fields AFTER deltas are fully consumed.  At this
        # point the backend's thread-local is populated (#42 / #52).
        _structured = get_structured() if callable(get_structured) else None
        _tool_calls = _structured.tool_calls if _structured is not None else None
        _finish_reason = _structured.finish_reason if _structured is not None else None
        _usage = _structured.usage if _structured is not None else None

        # Prefer the upstream's REAL output count when the backend surfaced one;
        # otherwise estimate over the ASSEMBLED text, not the delta count: the
        # verify path commits a fully-buffered response as ONE delta, which a
        # raw count would report as output_tokens=1 regardless of length.
        output_tokens = (
            _usage["output_tokens"] if _usage is not None
            else estimate_tokens(["".join(pieces)])
        )

        yield _event("content_block_stop", {"type": "content_block_stop", "index": 0})

        # Emit tool_use content blocks.  Each tool call is a separate content
        # block with a single input_json_delta carrying the full serialized input
        # (consolidated streaming — fully wire-compatible with the Anthropic
        # protocol and safe against partial-JSON delivery).
        if _tool_calls:
            for _tc_idx, _tc in enumerate(_tool_calls):
                _block_idx = _tc_idx + 1
                _tc_id = _tc.get("id") or _new_id("toolu_")
                _tc_name = _tc.get("name") or ""
                _tc_args = _tc.get("arguments")
                if isinstance(_tc_args, dict):
                    _tc_input_dict = _tc_args
                elif isinstance(_tc_args, str) and _tc_args.strip():
                    try:
                        _tc_input_dict = json.loads(_tc_args)
                    except (ValueError, TypeError):
                        _tc_input_dict = {}
                else:
                    _tc_input_dict = {}
                yield _event("content_block_start", {
                    "type": "content_block_start",
                    "index": _block_idx,
                    "content_block": {
                        "type": "tool_use",
                        "id": _tc_id,
                        "name": _tc_name,
                        "input": {},
                    },
                })
                yield _event("content_block_delta", {
                    "type": "content_block_delta",
                    "index": _block_idx,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(_tc_input_dict),
                    },
                })
                yield _event("content_block_stop", {
                    "type": "content_block_stop",
                    "index": _block_idx,
                })

        yield _event("message_delta", {
            "type": "message_delta",
            "delta": {
                "stop_reason": _anthropic_stop_reason(_finish_reason),
                "stop_sequence": None,
            },
            "usage": {"output_tokens": output_tokens},
        })
        yield _event("message_stop", {"type": "message_stop"})

    def render(
        self,
        request: InternalRequest,
        text: str,
        *,
        structured: Any = None,
        response_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build a non-streaming Anthropic message response.

        ``structured`` is an optional
        :class:`~anvil_serving.router.internal.StructuredResult` from the backend's
        thread-local.  When provided, the real ``stop_reason`` and any ``tool_use``
        content blocks are included.  ``structured=None`` (the default) produces the
        same output as before this change, preserving the text-path wire shape.
        """
        _tool_calls = structured.tool_calls if structured is not None else None
        _finish_reason = structured.finish_reason if structured is not None else None
        _usage = getattr(structured, "usage", None) if structured is not None else None

        content: List[Any] = []
        if text:
            content.append({"type": "text", "text": text})
        if _tool_calls:
            for _tc in _tool_calls:
                _tc_args = _tc.get("arguments")
                if isinstance(_tc_args, dict):
                    _tc_input = _tc_args
                elif isinstance(_tc_args, str) and _tc_args.strip():
                    try:
                        _tc_input = json.loads(_tc_args)
                    except (ValueError, TypeError):
                        _tc_input = {}
                else:
                    _tc_input = {}
                content.append({
                    "type": "tool_use",
                    "id": _tc.get("id") or _new_id("toolu_"),
                    "name": _tc.get("name") or "",
                    "input": _tc_input,
                })
        if not content:
            # Ensure at least one content block (e.g. tool-only response with empty text)
            content = [{"type": "text", "text": ""}]

        return {
            "id": _new_id("msg_"),
            "type": "message",
            "role": "assistant",
            "model": response_model or request.model,
            "content": content,
            "stop_reason": _anthropic_stop_reason(_finish_reason),
            "stop_sequence": None,
            # Real upstream counts when the backend surfaced them; else estimates.
            "usage": (
                dict(_usage) if _usage is not None else {
                    "input_tokens": _input_tokens(request),
                    "output_tokens": estimate_tokens([text]),
                }
            ),
        }

    def render_error(self, status: int, etype: str, message: str) -> Dict[str, Any]:
        # Anthropic's native error envelope (top-level "type":"error").
        return {"type": "error", "error": {"type": etype, "message": message}}
