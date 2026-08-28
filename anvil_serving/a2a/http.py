"""A2A 1.0 JSON-RPC and SSE framing independent of the gateway server."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from ..control_plane.mcp.errors import ToolError
from ..media.errors import MediaError
from .protocol import A2A_VERSION
from .tasks import A2AMediaTasks


def jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
    *,
    data: list[dict[str, Any]] | None = None,
) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


_A2A_ERRORS = {
    "job_not_found": (-32001, "Task not found", "TASK_NOT_FOUND"),
    "task_not_cancelable": (-32002, "Task is not cancelable", "TASK_NOT_CANCELABLE"),
    "push_notification_not_supported": (
        -32003,
        "Push notification is not supported",
        "PUSH_NOTIFICATION_NOT_SUPPORTED",
    ),
    "unsupported_operation": (-32004, "Operation is not supported", "UNSUPPORTED_OPERATION"),
    "content_type_not_supported": (
        -32005,
        "Content type is not supported",
        "CONTENT_TYPE_NOT_SUPPORTED",
    ),
}


def error_from_exception(request_id: Any, exc: MediaError | ToolError) -> dict:
    code_name = getattr(exc, "code", "invalid_request")
    mapped = _A2A_ERRORS.get(code_name)
    if mapped is not None:
        code, message, reason = mapped
        return jsonrpc_error(
            request_id,
            code,
            message,
            data=[{
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": reason,
                "domain": "a2a-protocol.org",
            }],
        )
    if code_name in {"authentication_required", "scope_denied"}:
        return jsonrpc_error(request_id, -32000, code_name)
    return jsonrpc_error(
        request_id,
        -32602,
        code_name,
        data=[{
            "@type": "type.googleapis.com/google.rpc.BadRequest",
            "fieldViolations": [{"description": "request parameters are invalid"}],
        }],
    )


def version_not_supported(request_id: Any, requested_version: str) -> dict:
    return jsonrpc_error(
        request_id,
        -32009,
        "Protocol version is not supported",
        data=[{
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "reason": "VERSION_NOT_SUPPORTED",
            "domain": "a2a-protocol.org",
            "metadata": {
                "requestedVersion": requested_version,
                "supportedVersions": A2A_VERSION,
            },
        }],
    )


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
            result = tasks.get_task(
                _task_id(params, history_length=True), caller=caller
            )
        elif method == "CancelTask":
            result = tasks.cancel_task(_task_id(params), caller=caller)
        elif method in {"SendStreamingMessage", "SubscribeToTask"}:
            return error_from_exception(
                request_id,
                MediaError(
                    "unsupported_operation",
                    "streaming method requires SSE",
                ),
            )
        else:
            return jsonrpc_error(request_id, -32601, "method not found")
    except (MediaError, ToolError) as exc:
        return error_from_exception(request_id, exc)
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def sse_frames(request_id: Any, updates: Iterable[Mapping[str, Any]]) -> Iterable[bytes]:
    for update in updates:
        payload = {"jsonrpc": "2.0", "id": request_id, "result": dict(update)}
        yield ("data: " + json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n\n").encode("utf-8")


def _task_id(
    params: Mapping[str, Any], *, history_length: bool = False
) -> str:
    allowed = {"id", "metadata"}
    if history_length:
        allowed.add("historyLength")
    if set(params) - allowed:
        raise MediaError("invalid_a2a_request", "A2A task request contains unknown fields")
    metadata = params.get("metadata", {})
    if not isinstance(metadata, Mapping) or len(metadata) > 32:
        raise MediaError("invalid_a2a_request", "A2A task metadata is invalid")
    if history_length:
        requested_history = params.get("historyLength", 0)
        if (
            isinstance(requested_history, bool)
            or not isinstance(requested_history, int)
            or requested_history < 0
            or requested_history > 1000
        ):
            raise MediaError("invalid_a2a_request", "A2A historyLength is invalid")
    task_id = params.get("id")
    if not isinstance(task_id, str) or not task_id or len(task_id) > 128:
        raise MediaError("invalid_a2a_request", "A2A task id is invalid")
    return task_id


__all__ = [
    "error_from_exception",
    "handle_jsonrpc",
    "jsonrpc_error",
    "sse_frames",
    "version_not_supported",
]
