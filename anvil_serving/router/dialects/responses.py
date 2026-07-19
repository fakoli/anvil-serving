"""Supported stateless subset of the OpenAI Responses API.

This adapter intentionally converts only the request/response features a local
Codex harness needs today: input/instructions, function tools and function
results, JSON-schema output, normal responses, and SSE.  Stateful response
chaining, background work, hosted tools, and provider-side storage fail loudly
instead of being silently approximated.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Iterable, Iterator, Mapping, Optional

from ..internal import DialectError, InternalRequest, estimate_tokens, normalize_messages
from . import _new_id
from .openai import OpenAIDialect


_UNSUPPORTED_FIELDS = (
    "previous_response_id", "conversation", "background",
    "metadata",
)


def _event(name: str, payload: Dict[str, Any]) -> bytes:
    payload = {"type": name, **payload}
    return (
        f"event: {name}\n".encode("utf-8")
        + b"data: " + json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n\n"
    )


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                if item.get("type") in {"input_text", "output_text", "text"}:
                    parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return "" if value is None else str(value)


def _tool_definition(tool: Mapping[str, Any]) -> dict[str, Any]:
    tool_type = tool.get("type")
    if tool_type != "function":
        rendered_type = str(tool_type) if tool_type is not None else "missing"
        raise DialectError(
            400,
            "unsupported_feature",
            f"/v1/responses does not support tool type: {rendered_type}; only function tools are supported",
        )
    name = tool.get("name")
    if not isinstance(name, str) or not name:
        raise DialectError(400, "invalid_request_error", "function tools require a non-empty name")
    function: dict[str, Any] = {"name": name}
    if "description" in tool:
        function["description"] = str(tool["description"])
    if "parameters" in tool:
        function["parameters"] = tool["parameters"]
    if "strict" in tool:
        function["strict"] = bool(tool["strict"])
    return {"type": "function", "function": function}


def _tool_definitions(tool: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Translate one Responses tool without widening the local tool boundary.

    Codex currently advertises an internal ``collaboration`` namespace even
    when a bridge disables multi-agent work.  It is not executable by the
    project-local bridge and it cannot be faithfully represented by the Chat
    Completions function schema.  Suppress only that metadata namespace; other
    namespaces fail explicitly so that MCP, remote tools, or provider-hosted
    tools cannot acquire an accidental route through the local model plane.
    """
    if tool.get("type") == "namespace" and tool.get("name") == "collaboration":
        return []
    return [_tool_definition(tool)]


def _tool_choice(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    if value.get("type") == "function" and isinstance(value.get("name"), str):
        return {"type": "function", "function": {"name": value["name"]}}
    return dict(value)


class ResponsesDialect:
    """Translate the stateless Responses subset onto the existing OpenAI relay."""

    name = "responses"

    def parse_request(self, body: Mapping[str, Any]) -> InternalRequest:
        # Codex sends the explicit stateless form.  Accepting `store: false`
        # retains the Responses request shape without claiming provider-side
        # response storage or stateful continuation support.
        if "store" in body and body["store"] is not False:
            raise DialectError(400, "unsupported_feature", "/v1/responses supports only store: false")
        # The current Codex client sends this legacy stateless compatibility
        # request.  It is safe to accept because the local route never
        # persists Responses state and does not claim to return encrypted
        # provider reasoning.  Any other response enrichment remains outside
        # the supported subset.
        if "include" in body:
            include = body["include"]
            if not isinstance(include, list) or any(item != "reasoning.encrypted_content" for item in include):
                raise DialectError(
                    400,
                    "unsupported_feature",
                    "/v1/responses supports only include: [\"reasoning.encrypted_content\"] in stateless mode",
                )
        # A Workbench/Codex run may describe a reasoning preference, but the
        # selected Serving profile owns the actual upstream thinking policy.
        # Validate the stateless client shape, deliberately do not relay it as
        # a caller override of the profile's reasoning configuration.
        if "reasoning" in body:
            reasoning = body["reasoning"]
            if reasoning is not None:
                if not isinstance(reasoning, Mapping) or set(reasoning) - {"effort", "summary"}:
                    raise DialectError(400, "unsupported_feature", "/v1/responses supports only reasoning.effort and reasoning.summary")
                effort = reasoning.get("effort")
                if effort is not None and effort not in {"minimal", "low", "medium", "high", "xhigh"}:
                    raise DialectError(400, "invalid_request_error", "reasoning.effort is invalid")
                summary = reasoning.get("summary")
                if summary is not None and not isinstance(summary, str):
                    raise DialectError(400, "invalid_request_error", "reasoning.summary must be a string")
        if "parallel_tool_calls" in body and body["parallel_tool_calls"] is not False:
            raise DialectError(400, "unsupported_feature", "/v1/responses supports only parallel_tool_calls: false")
        if "truncation" in body and body["truncation"] != "disabled":
            raise DialectError(400, "unsupported_feature", "/v1/responses supports only truncation: disabled")
        for field in _UNSUPPORTED_FIELDS:
            if field in body:
                raise DialectError(400, "unsupported_feature", f"/v1/responses does not support {field}")
        if "input" not in body:
            raise DialectError(400, "invalid_request_error", "input is required")
        input_value = body.get("input")
        messages: list[dict[str, Any]] = []
        instructions = body.get("instructions")
        if instructions is not None:
            if not isinstance(instructions, str):
                raise DialectError(400, "invalid_request_error", "instructions must be a string")
            messages.append({"role": "system", "content": instructions})
        if isinstance(input_value, str):
            messages.append({"role": "user", "content": input_value})
        elif isinstance(input_value, list):
            for item in input_value:
                if not isinstance(item, Mapping):
                    raise DialectError(400, "invalid_request_error", "input items must be objects")
                item_type = item.get("type", "message")
                if item_type == "message":
                    role = str(item.get("role") or "user")
                    if role not in {"system", "developer", "user", "assistant"}:
                        raise DialectError(400, "invalid_request_error", f"unsupported input message role: {role}")
                    messages.append({"role": "system" if role == "developer" else role, "content": _text(item.get("content"))})
                elif item_type == "function_call":
                    call_id = str(item.get("call_id") or item.get("id") or _new_id("call_"))
                    name = item.get("name")
                    if not isinstance(name, str) or not name:
                        raise DialectError(400, "invalid_request_error", "function_call input requires name")
                    arguments = item.get("arguments", "")
                    if isinstance(arguments, Mapping):
                        arguments = json.dumps(arguments, separators=(",", ":"))
                    messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": str(arguments)}}]})
                elif item_type == "function_call_output":
                    call_id = item.get("call_id")
                    if not isinstance(call_id, str) or not call_id:
                        raise DialectError(400, "invalid_request_error", "function_call_output requires call_id")
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": _text(item.get("output"))})
                else:
                    raise DialectError(400, "unsupported_feature", f"unsupported input item type: {item_type}")
        else:
            raise DialectError(400, "invalid_request_error", "input must be a string or an array")

        converted: dict[str, Any] = {
            "model": str(body.get("model") or "chat"),
            "messages": messages,
            "stream": bool(body.get("stream", False)),
        }
        for field in ("temperature", "top_p"):
            if field in body:
                converted[field] = body[field]
        if "max_output_tokens" in body:
            converted["max_completion_tokens"] = body["max_output_tokens"]
        tools = body.get("tools")
        if tools is not None:
            if not isinstance(tools, list):
                raise DialectError(400, "invalid_request_error", "tools must be an array")
            converted["tools"] = [
                definition
                for tool in tools
                for definition in _tool_definitions(tool if isinstance(tool, Mapping) else {})
            ]
        if "tool_choice" in body:
            converted["tool_choice"] = _tool_choice(body["tool_choice"])
        text = body.get("text")
        if text is not None:
            if not isinstance(text, Mapping) or set(text) - {"format"}:
                raise DialectError(400, "unsupported_feature", "only text.format is supported")
            format_value = text.get("format")
            if not isinstance(format_value, Mapping) or format_value.get("type") != "json_schema":
                raise DialectError(400, "unsupported_feature", "only text.format.type=json_schema is supported")
            schema = format_value.get("schema")
            if not isinstance(schema, Mapping):
                raise DialectError(400, "invalid_request_error", "text.format.schema must be an object")
            converted["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": str(format_value.get("name") or "response"), "schema": dict(schema), "strict": bool(format_value.get("strict", False))},
            }
        request = OpenAIDialect().parse_request(converted)
        # The relay emits Chat Completions upstream, but response rendering remains
        # Responses-native at the front door.
        request.dialect = "openai"
        request.raw["_anvil_responses"] = True
        return request

    @staticmethod
    def _usage(request: InternalRequest, text: str, structured: Any) -> dict[str, int]:
        usage = getattr(structured, "usage", None) if structured is not None else None
        if usage is not None:
            input_tokens = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
        else:
            input_tokens = estimate_tokens([message.content for message in request.messages])
            output_tokens = estimate_tokens([text])
        return {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens}

    @staticmethod
    def _output(response_id: str, text: str, structured: Any) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        if text:
            output.append({"id": _new_id("msg_"), "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": text, "annotations": []}]})
        for call in getattr(structured, "tool_calls", None) or ():
            arguments = call.get("arguments", "")
            if isinstance(arguments, Mapping):
                arguments = json.dumps(arguments, separators=(",", ":"))
            call_id = str(call.get("id") or _new_id("call_"))
            output.append({"id": _new_id("fc_"), "type": "function_call", "status": "completed", "call_id": call_id, "name": str(call.get("name") or ""), "arguments": str(arguments)})
        return output

    def render(self, request: InternalRequest, text: str, *, structured: Any = None) -> Dict[str, Any]:
        response_id = _new_id("resp_")
        return {"id": response_id, "object": "response", "created_at": int(time.time()), "status": "completed", "model": request.model, "output": self._output(response_id, text, structured), "usage": self._usage(request, text, structured)}

    def stream(self, request: InternalRequest, deltas: Iterable[str], *, get_structured: Optional[Callable[[], Any]] = None) -> Iterator[bytes]:
        response_id = _new_id("resp_")
        created_at = int(time.time())
        base = {"response": {"id": response_id, "object": "response", "created_at": created_at, "status": "in_progress", "model": request.model, "output": []}}
        yield _event("response.created", base)
        yield _event("response.in_progress", base)
        message_id: str | None = None
        pieces: list[str] = []
        for piece in deltas:
            pieces.append(piece)
            if message_id is None:
                message_id = _new_id("msg_")
                item = {"id": message_id, "type": "message", "role": "assistant", "status": "in_progress", "content": []}
                yield _event("response.output_item.added", {"output_index": 0, "item": item})
                yield _event("response.content_part.added", {"item_id": message_id, "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}})
            yield _event("response.output_text.delta", {"item_id": message_id, "output_index": 0, "content_index": 0, "delta": piece})
        text = "".join(pieces)
        structured = get_structured() if callable(get_structured) else None
        output = self._output(response_id, text, structured)
        if message_id is not None and output and output[0]["type"] == "message":
            output[0]["id"] = message_id
        if message_id is not None:
            yield _event("response.output_text.done", {"item_id": message_id, "output_index": 0, "content_index": 0, "text": ""})
            yield _event("response.content_part.done", {"item_id": message_id, "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}})
            yield _event("response.output_item.done", {"output_index": 0, "item": {"id": message_id, "type": "message", "role": "assistant", "status": "completed", "content": []}})
        output_index = 1 if message_id is not None else 0
        for call in (item for item in output if item["type"] == "function_call"):
            yield _event("response.output_item.added", {"output_index": output_index, "item": {**call, "status": "in_progress", "arguments": ""}})
            yield _event("response.function_call_arguments.delta", {"item_id": call["id"], "output_index": output_index, "delta": call["arguments"]})
            yield _event("response.function_call_arguments.done", {"item_id": call["id"], "output_index": output_index, "arguments": call["arguments"]})
            yield _event("response.output_item.done", {"output_index": output_index, "item": call})
            output_index += 1
        completed = {"id": response_id, "object": "response", "created_at": created_at, "status": "completed", "model": request.model, "output": output, "usage": self._usage(request, text, structured)}
        yield _event("response.completed", {"response": completed})

    def render_error(self, status: int, etype: str, message: str) -> Dict[str, Any]:
        return {"error": {"type": etype, "message": message, "code": None}}
