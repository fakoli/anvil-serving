"""Operator-only managed media-worker controller operations."""

from __future__ import annotations

import os
from pathlib import Path

from ....media.jobs import MediaJobStore
from ....media.lifecycle import MediaWorkerLifecycle
from ..arguments import arg_bool as _arg_bool
from ..arguments import bounded_int_arg as _bounded_int_arg
from ..arguments import bounded_integer_schema as _bounded_integer_schema
from ..arguments import schema as _schema
from ..arguments import str_arg as _str_arg
from ..catalog import ToolFamily
from ..errors import ToolError
from ..errors import ok as _ok
from .serves import tool_serves_logs, tool_serves_manage, tool_serves_status


DEFAULT_MEDIA_STATE_DB = os.path.join(
    os.path.expanduser("~"), ".anvil-serving", "media-jobs.sqlite3"
)


def _lifecycle() -> MediaWorkerLifecycle:
    state_path = os.environ.get("ANVIL_MEDIA_STATE_DB", DEFAULT_MEDIA_STATE_DB)
    parent = Path(state_path).expanduser().resolve(strict=False).parent
    if not parent.is_dir():
        raise ToolError(
            "media_state_unavailable",
            "media state directory is unavailable; initialize operator state first",
        )
    return MediaWorkerLifecycle(
        MediaJobStore(state_path),
        status_operation=tool_serves_status,
        manage_operation=tool_serves_manage,
    )


def _mutation_gate(args: dict) -> tuple[bool, bool]:
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    apply_requested = confirm and not dry_run
    human_approved = _arg_bool(
        args.get("human_approved"), False, name="human_approved"
    )
    if apply_requested and not human_approved:
        raise ToolError(
            "human_approval_required",
            "media-worker lifecycle mutations require confirm=true, dry_run=false, and human_approved=true",
        )
    return apply_requested, human_approved


def _manifest_arg(args: dict) -> str:
    """Resolve the resource-owner manifest without exposing it to the gateway."""
    configured = (os.environ.get("ANVIL_MEDIA_SERVE_MANIFEST") or "").strip()
    return _str_arg(args, "manifest", configured)


def tool_media_worker_prepare(args: dict) -> dict:
    apply_requested, human_approved = _mutation_gate(args)
    receipt = _lifecycle().prepare(
        _str_arg(args, "job_id", required=True),
        principal=_str_arg(args, "principal", required=True),
        service=_str_arg(args, "service", required=True),
        manifest=_manifest_arg(args),
        confirm=apply_requested,
        human_approved=human_approved,
    )
    return _ok(receipt.as_dict())


def tool_media_worker_status(args: dict) -> dict:
    service = _str_arg(args, "service", required=True)
    manifest = _manifest_arg(args)
    result = tool_serves_status({"manifest": manifest, "names": [service]})
    job_id = _str_arg(args, "job_id", "")
    if job_id:
        result["result"]["lifecycle"] = _lifecycle().status(
            job_id, principal=_str_arg(args, "principal", required=True)
        )
    return result


def tool_media_worker_logs(args: dict) -> dict:
    return tool_serves_logs(
        {
            "manifest": _manifest_arg(args),
            "names": [_str_arg(args, "service", required=True)],
            "tail": _bounded_int_arg(args, "tail", 200, min_value=1, max_value=5000),
            "max_output_bytes": _bounded_int_arg(
                args, "max_output_bytes", 65536, min_value=1024, max_value=1048576
            ),
            "since": _str_arg(args, "since", ""),
            "follow": False,
            "timeout_seconds": _bounded_int_arg(
                args, "timeout_seconds", 60, min_value=1, max_value=600
            ),
        }
    )


def tool_media_worker_teardown(args: dict) -> dict:
    apply_requested, human_approved = _mutation_gate(args)
    receipt = _lifecycle().teardown(
        _str_arg(args, "job_id", required=True),
        principal=_str_arg(args, "principal", required=True),
        manifest=_manifest_arg(args),
        confirm=apply_requested,
        human_approved=human_approved,
    )
    return _ok(receipt.as_dict())


_COMMON = {
    "service": {"type": "string", "minLength": 1, "maxLength": 128},
    "manifest": {"type": "string", "maxLength": 4096},
}
_JOB = {
    **_COMMON,
    "job_id": {"type": "string", "minLength": 16, "maxLength": 128},
    "principal": {"type": "string", "minLength": 1, "maxLength": 128},
}
_GATE = {
    "dry_run": {"type": "boolean"},
    "confirm": {"type": "boolean"},
    "human_approved": {"type": "boolean"},
}


FAMILY = ToolFamily(
    name="media-worker",
    tools={
        "media_worker_prepare": {
            "description": "Preview or prepare the declared managed media worker for one durable job.",
            "inputSchema": _schema({**_JOB, **_GATE}, required=["service", "job_id", "principal"]),
            "handler": tool_media_worker_prepare,
        },
        "media_worker_status": {
            "description": "Read bounded managed media-worker and optional job lifecycle status.",
            "inputSchema": _schema({**_JOB}, required=["service"]),
            "handler": tool_media_worker_status,
        },
        "media_worker_logs": {
            "description": "Read bounded logs for the declared managed media worker.",
            "inputSchema": _schema(
                {
                    **_COMMON,
                    "tail": _bounded_integer_schema(1, 5000, 200),
                    "max_output_bytes": _bounded_integer_schema(1024, 1048576, 65536),
                    "since": {"type": "string", "maxLength": 128},
                    "timeout_seconds": _bounded_integer_schema(1, 600, 60),
                },
                required=["service"],
            ),
            "handler": tool_media_worker_logs,
        },
        "media_worker_teardown": {
            "description": "Preview or release a job-owned managed media worker after all owned jobs terminate.",
            "inputSchema": _schema({**_JOB, **_GATE}, required=["service", "job_id", "principal"]),
            "handler": tool_media_worker_teardown,
        },
    },
)


__all__ = ["FAMILY"]
