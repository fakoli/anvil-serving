"""Protocol-neutral composition for the media gateway HTTP surfaces."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .. import __version__, mcp
from ..a2a.agent_card import build_agent_card
from ..a2a.http import handle_jsonrpc, jsonrpc_error, sse_frames
from ..a2a.tasks import A2AMediaTasks
from ..control_plane.mcp.tools.media import service_context
from ..media.artifacts import ArtifactPayload, ArtifactStore
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
                first = self.tasks.send_message(params, caller=self.caller)["task"]
            elif method == "SubscribeToTask":
                task_id, after_sequence = _stream_task_request(params)
                first = self.tasks.get_task(task_id, caller=self.caller)
                if after_sequence > first["metadata"]["sequence"]:
                    raise MediaError("invalid_stream_cursor", "A2A stream cursor is invalid")
            else:
                return jsonrpc_error(request_id, -32601, "method not found")
        except MediaError as exc:
            return jsonrpc_error(request_id, -32602, exc.code, detail=exc.message)

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
        principal = self.caller.get("principal")
        if not isinstance(principal, str) or not principal:
            raise MediaError("authentication_required", "authenticated media caller is required", status=401)
        return self.artifacts.read(
            artifact_id,
            principal=principal,
            start=start,
            end=end,
        )


def _stream_task_request(params: Mapping[str, Any]) -> tuple[str, int]:
    if set(params) - {"id", "afterSequence"}:
        raise MediaError("invalid_a2a_request", "A2A stream request contains unknown fields")
    task_id = params.get("id")
    after = params.get("afterSequence", 0)
    if not isinstance(task_id, str) or not task_id or len(task_id) > 128:
        raise MediaError("invalid_a2a_request", "A2A task id is invalid")
    if isinstance(after, bool) or not isinstance(after, int) or after < 0:
        raise MediaError("invalid_stream_cursor", "A2A stream cursor is invalid")
    return task_id, after


__all__ = ["ARTIFACT_PREFIX", "MCP_PATH", "ProtocolGateway"]
