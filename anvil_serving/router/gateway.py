"""Protocol-neutral composition for the media gateway HTTP surfaces."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .. import __version__, mcp
from ..a2a.agent_card import build_agent_card
from ..a2a.http import error_from_exception, handle_jsonrpc, jsonrpc_error, sse_frames
from ..a2a.tasks import A2AMediaTasks
from ..control_plane.mcp.tools.media import service_context
from ..control_plane.mcp.errors import ToolError
from ..control_plane.mcp.security import caller_context, require_scope
from ..media.artifacts import ArtifactPayload, ArtifactStore
from ..media.contracts import TERMINAL_STATES
from ..media.errors import MediaError
from ..media.workflows import WorkflowRegistry


MCP_PATH = "/mcp"
ARTIFACT_PREFIX = "/artifacts/"


@dataclass(frozen=True)
class ProtocolGateway:
    """Bind authenticated protocol adapters to one durable media service."""

    caller: Mapping[str, Any]
    tasks: A2AMediaTasks
    registry: WorkflowRegistry
    artifacts: ArtifactStore
    public_origin: str
    mcp_handler: Callable[..., dict | None] = mcp.handle_request

    def mcp_request(self, request: dict) -> dict | None:
        with service_context(self.tasks.operations, self.tasks.backend):
            return self.mcp_handler(request, caller=self.caller, audience="media")

    def a2a_request(self, request: dict) -> dict:
        return handle_jsonrpc(request, tasks=self.tasks, caller=self.caller)

    def a2a_stream(self, request: dict) -> Iterable[bytes] | dict:
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
            if method == "SendStreamingMessage":
                first = self.tasks.send_message(
                    params, caller=self.caller, force_immediate=True
                )["task"]
            elif method == "SubscribeToTask":
                task_id = _stream_task_request(params)
                first = self.tasks.get_task(task_id, caller=self.caller)
                job = self.tasks.operations.jobs.get(
                    task_id,
                    principal=str(self.caller.get("principal") or ""),
                )
                if job.state in TERMINAL_STATES:
                    raise MediaError(
                        "unsupported_operation",
                        "terminal media tasks cannot be subscribed",
                        status=409,
                    )
            else:
                return jsonrpc_error(request_id, -32601, "method not found")
        except (MediaError, ToolError) as exc:
            return error_from_exception(request_id, exc)

        cursor = first["metadata"]["sequence"]
        updates = itertools.chain(
            ({"task": first},),
            self.tasks.observe(
                first["id"],
                caller=self.caller,
                after_sequence=cursor,
            ),
        )
        return sse_frames(request_id, updates)

    def agent_card(self) -> dict[str, Any]:
        workflows = tuple(
            self.registry.get(item["id"], item["version"])
            for item in self.registry.list()
        )
        return build_agent_card(
            workflows,
            public_origin=self.public_origin,
            server_version=__version__,
        )

    def artifact(
        self,
        artifact_id: str,
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> ArtifactPayload:
        try:
            with caller_context(self.caller):
                identity = require_scope("media:read")
        except ToolError as exc:
            status = 401 if exc.code in {"authentication_required", "invalid_caller_context"} else 403
            raise MediaError(exc.code, exc.message, status=status) from exc
        return self.artifacts.read(
            artifact_id,
            principal=identity.principal,
            start=start,
            end=end,
        )


def _stream_task_request(params: Mapping[str, Any]) -> str:
    if set(params) - {"id", "metadata"}:
        raise MediaError("invalid_a2a_request", "A2A stream request contains unknown fields")
    task_id = params.get("id")
    if not isinstance(task_id, str) or not task_id or len(task_id) > 128:
        raise MediaError("invalid_a2a_request", "A2A task id is invalid")
    metadata = params.get("metadata", {})
    if not isinstance(metadata, Mapping) or len(metadata) > 32:
        raise MediaError("invalid_a2a_request", "A2A stream metadata is invalid")
    return task_id


__all__ = ["ARTIFACT_PREFIX", "MCP_PATH", "ProtocolGateway"]
