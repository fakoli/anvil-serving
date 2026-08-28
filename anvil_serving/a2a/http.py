"""A2A 1.0 JSON-RPC and SSE framing independent of the gateway server."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from ..control_plane.mcp.errors import ToolError
from ..media.errors import MediaError
from .tasks import A2AMediaTasks


def jsonrpc_error(request_id: Any, code: int, message: str, *, detail: str = "") -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if detail:
        error["data"] = {"detail": detail}
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def handle_jsonrpc(
    request: Mapping[str, Any], *, tasks: A2AMediaTasks, caller: Mapping[str, Any]
) -> dict:
    request_id = request.get("id") if isinstance(request, Mapping) else None
    if (
        not isinstance(request, Mapping)
        or request.get("jsonrpc") != "2.0"
        or request_id is None
        or not isinstance(request.get("params"), Mapping)
    ):
        return jsonrpc_error(request_id, -32600, "invalid request")
    method = request.get("method")
    params = request["params"]
    try:
        if method == "SendMessage":
            result = tasks.send_message(params, caller=caller)
        elif method == "GetTask":
            result = tasks.get_task(_task_id(params), caller=caller)
        elif method == "CancelTask":
            result = tasks.cancel_task(_task_id(params), caller=caller)
        elif method in {"SendStreamingMessage", "SubscribeToTask"}:
            return jsonrpc_error(request_id, -32004, "streaming method requires SSE")
        else:
            return jsonrpc_error(request_id, -32601, "method not found")
    except (MediaError, ToolError) as exc:
        code = -32001 if getattr(exc, "code", "") in {"authentication_required", "scope_denied"} else -32602
        return jsonrpc_error(request_id, code, getattr(exc, "code", "invalid request"), detail=str(exc))
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def sse_frames(request_id: Any, updates: Iterable[Mapping[str, Any]]) -> Iterable[bytes]:
    for update in updates:
        payload = {"jsonrpc": "2.0", "id": request_id, "result": dict(update)}
        yield ("data: " + json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n\n").encode("utf-8")


def _task_id(params: Mapping[str, Any]) -> str:
    if set(params) - {"id", "historyLength", "afterSequence"}:
        raise MediaError("invalid_a2a_request", "A2A task request contains unknown fields")
    task_id = params.get("id")
    if not isinstance(task_id, str) or not task_id or len(task_id) > 128:
        raise MediaError("invalid_a2a_request", "A2A task id is invalid")
    return task_id


__all__ = ["handle_jsonrpc", "jsonrpc_error", "sse_frames"]
