"""Caller-scoped MCP tools for bounded named media workflows."""

from __future__ import annotations

import os
import contextvars
from contextlib import contextmanager
from pathlib import Path
from typing import Mapping

from ....media.artifacts import ArtifactStore
from ....media.cli import DEFAULT_REGISTRY
from ....media.comfyui import ComfyUIClient
from ....media.errors import MediaError
from ....media.jobs import MediaJobStore
from ....media.operations import MediaOperations
from ....media.workflows import WorkflowRegistry
from ..arguments import schema as _schema
from ..arguments import str_arg as _str_arg
from ..catalog import ToolFamily
from ..errors import ToolError
from ..errors import ok as _ok
from ..security import CallerContext, require_scope


_SERVICE_OVERRIDE: contextvars.ContextVar[
    tuple[MediaOperations, ComfyUIClient | None] | None
] = contextvars.ContextVar("anvil_media_service_override", default=None)


@contextmanager
def service_context(operations: MediaOperations, backend: ComfyUIClient | None):
    """Bind one request to the gateway's shared durable media service."""
    token = _SERVICE_OVERRIDE.set((operations, backend))
    try:
        yield
    finally:
        _SERVICE_OVERRIDE.reset(token)


def _services() -> tuple[MediaOperations, ComfyUIClient | None]:
    override = _SERVICE_OVERRIDE.get()
    if override is not None:
        return override
    registry = os.environ.get("ANVIL_MEDIA_WORKFLOW_REGISTRY", str(DEFAULT_REGISTRY))
    state = os.environ.get(
        "ANVIL_MEDIA_STATE_DB",
        str(Path.home() / ".anvil-serving" / "media-jobs.sqlite3"),
    )
    artifacts = os.environ.get(
        "ANVIL_MEDIA_ARTIFACT_ROOT",
        str(Path.home() / ".anvil-serving" / "media-artifacts"),
    )
    backend = os.environ.get("ANVIL_MEDIA_BACKEND_URL")
    Path(state).expanduser().resolve(strict=False).parent.mkdir(parents=True, exist_ok=True)
    operations = MediaOperations(
        WorkflowRegistry(registry), MediaJobStore(state), ArtifactStore(artifacts)
    )
    return operations, ComfyUIClient(backend) if backend else None


def _backend(backend: ComfyUIClient | None) -> ComfyUIClient:
    if not isinstance(backend, ComfyUIClient):
        raise ToolError(
            "media_backend_unconfigured",
            "the selected media service has no configured adapter endpoint",
        )
    return backend


def _translate(call):
    try:
        return _ok(call())
    except MediaError as exc:
        raise ToolError(exc.code, exc.message, exc.details) from exc


def _owner(args: dict, caller: CallerContext) -> str:
    requested = _str_arg(args, "owner", "")
    if not requested or requested == caller.principal:
        return caller.principal
    if "media:cross-principal" not in caller.scopes and "operator:media" not in caller.scopes:
        raise ToolError(
            "scope_denied",
            "caller is not authorized to inspect another principal",
            {"requiredScope": "media:cross-principal"},
        )
    return requested


def tool_media_capabilities(args: dict) -> dict:
    require_scope("media:read")
    return _translate(lambda: _services()[0].capabilities())


def tool_media_workflow_list(args: dict) -> dict:
    require_scope("media:read")
    return _translate(lambda: _services()[0].workflow_list())


def tool_media_workflow_show(args: dict) -> dict:
    require_scope("media:read")
    workflow_id = _str_arg(args, "workflow_id", required=True)
    version = _str_arg(args, "version", required=True)
    return _translate(lambda: _services()[0].workflow_show(workflow_id, version))


def tool_media_workflow_validate(args: dict) -> dict:
    require_scope("media:read")
    workflow_id = _str_arg(args, "workflow_id", required=True)
    version = _str_arg(args, "version", required=True)
    operations, backend = _services()
    return _translate(
        lambda: operations.workflow_validate(
            workflow_id, version, backend=_backend(backend)
        )
    )


def tool_media_workflow_run(args: dict) -> dict:
    caller = require_scope("media:submit")
    operations, backend = _services()
    parameters = args.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ToolError("bad_argument", "'parameters' must be an object")
    return _translate(
        lambda: operations.workflow_run(
            _str_arg(args, "workflow_id", required=True),
            _str_arg(args, "version", required=True),
            dict(parameters),
            principal=caller.principal,
            idempotency_key=_str_arg(args, "idempotency_key", required=True),
            backend=_backend(backend),
        )
    )


def tool_media_job_status(args: dict) -> dict:
    caller = require_scope("media:read")
    owner = _owner(args, caller)
    return _translate(
        lambda: _services()[0].job_status(
            _str_arg(args, "job_id", required=True), principal=owner
        )
    )


def tool_media_job_cancel(args: dict) -> dict:
    caller = require_scope("media:cancel")
    owner = _owner(args, caller)
    operations, backend = _services()
    return _translate(
        lambda: operations.job_cancel(
            _str_arg(args, "job_id", required=True),
            principal=owner,
            backend=_backend(backend),
        )
    )


def tool_media_artifact_inspect(args: dict) -> dict:
    caller = require_scope("media:read")
    owner = _owner(args, caller)
    return _translate(
        lambda: _services()[0].artifact_inspect(
            _str_arg(args, "artifact_id", required=True), principal=owner
        )
    )


_WORKFLOW = {
    "workflow_id": {"type": "string", "minLength": 1, "maxLength": 128},
    "version": {"type": "string", "minLength": 1, "maxLength": 64},
}
_OWNER = {"owner": {"type": "string", "minLength": 1, "maxLength": 128}}


def _tool(description: str, properties: dict, handler, *, scope: str, required=()):
    return {
        "description": description,
        "inputSchema": _schema(properties, required=list(required)),
        "handler": handler,
        "audience": "media",
        "requiredScope": scope,
    }


FAMILY = ToolFamily(
    name="media",
    tools={
        "media_capabilities": _tool(
            "List configured named media capabilities and availability.", {}, tool_media_capabilities,
            scope="media:read",
        ),
        "media_workflow_list": _tool(
            "List configured named media workflows.", {}, tool_media_workflow_list,
            scope="media:read",
        ),
        "media_workflow_show": _tool(
            "Inspect one exact named media workflow.", _WORKFLOW, tool_media_workflow_show,
            scope="media:read", required=("workflow_id", "version"),
        ),
        "media_workflow_validate": _tool(
            "Validate one exact named workflow against its configured worker.", _WORKFLOW,
            tool_media_workflow_validate, scope="media:read", required=("workflow_id", "version"),
        ),
        "media_workflow_run": _tool(
            "Submit one allowlisted named workflow and return an opaque durable job.",
            {
                **_WORKFLOW,
                "parameters": {
                    "type": "object", "additionalProperties": True, "maxProperties": 32,
                },
                "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            tool_media_workflow_run,
            scope="media:submit",
            required=("workflow_id", "version", "parameters", "idempotency_key"),
        ),
        "media_job_status": _tool(
            "Inspect one caller-owned durable media job.",
            {"job_id": {"type": "string", "minLength": 16, "maxLength": 128}, **_OWNER},
            tool_media_job_status, scope="media:read", required=("job_id",),
        ),
        "media_job_cancel": _tool(
            "Cancel one caller-owned media job within exclusive-slot safety rules.",
            {"job_id": {"type": "string", "minLength": 16, "maxLength": 128}, **_OWNER},
            tool_media_job_cancel, scope="media:cancel", required=("job_id",),
        ),
        "media_artifact_inspect": _tool(
            "Inspect opaque authenticated artifact metadata without media bytes.",
            {"artifact_id": {"type": "string", "minLength": 16, "maxLength": 128}, **_OWNER},
            tool_media_artifact_inspect, scope="media:read", required=("artifact_id",),
        ),
    },
)


__all__ = ["FAMILY", "service_context"]
