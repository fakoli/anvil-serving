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
import time
import uuid
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional

from ..internal import (
    InternalRequest,
    estimate_tokens,
    flatten_content,
    normalize_messages,
)


def _new_id() -> str:
    return "msg_" + uuid.uuid4().hex[:24]


def _event(etype: str, data: Dict[str, Any]) -> bytes:
    """Encode one Anthropic named SSE event: ``event: T\\ndata: <json>\\n\\n``."""
    body = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return b"event: " + etype.encode("utf-8") + b"\n" + b"data: " + body + b"\n\n"


def _input_tokens(request: InternalRequest) -> int:
    texts: List[str] = [m.content for m in request.messages]
    if request.system:
        texts.append(request.system)
    return estimate_tokens(texts)


class AnthropicDialect:
    """Speak the Anthropic Messages wire protocol."""

    name = "anthropic"

    def parse_request(self, body: Mapping[str, Any]) -> InternalRequest:
        system_raw = body.get("system")
        system = flatten_content(system_raw) if system_raw is not None else None
        return InternalRequest(
            model=str(body.get("model") or "claude"),
            messages=normalize_messages(body.get("messages")),
            system=system,
            max_tokens=body.get("max_tokens"),
            temperature=body.get("temperature"),
            stream=bool(body.get("stream", False)),
            dialect=self.name,
            raw=dict(body),
        )

    def stream(self, request: InternalRequest, deltas: Iterable[str]) -> Iterator[bytes]:
        msg_id = _new_id()
        model = request.model
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

        output_tokens = 0
        for piece in deltas:
            output_tokens += 1
            yield _event("content_block_delta", {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": piece},
            })

        yield _event("content_block_stop", {
            "type": "content_block_stop",
            "index": 0,
        })
        yield _event("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        })
        yield _event("message_stop", {"type": "message_stop"})

    def render(self, request: InternalRequest, text: str) -> Dict[str, Any]:
        # output_tokens here counts the whole reply; the streamed path counts
        # deltas. Both are deterministic estimates, not a real tokenizer.
        return {
            "id": _new_id(),
            "type": "message",
            "role": "assistant",
            "model": request.model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": _input_tokens(request),
                "output_tokens": estimate_tokens([text]),
            },
        }
