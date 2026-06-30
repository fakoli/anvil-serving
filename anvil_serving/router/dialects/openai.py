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
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional

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

    def stream(self, request: InternalRequest, deltas: Iterable[str]) -> Iterator[bytes]:
        cid = _new_id("chatcmpl-")
        created = int(time.time())
        model = request.model
        # 1) opening chunk announces the assistant role.
        yield _sse(_chunk(cid, created, model, {"role": "assistant"}, None))
        # 2) one chunk per text delta.
        for piece in deltas:
            yield _sse(_chunk(cid, created, model, {"content": piece}, None))
        # 3) final chunk: empty delta + finish_reason, then the [DONE] sentinel.
        yield _sse(_chunk(cid, created, model, {}, "stop"))
        yield b"data: [DONE]\n\n"

    def render(self, request: InternalRequest, text: str) -> Dict[str, Any]:
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
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
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
