"""Closed controller tool contract for node-local workload visibility."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..control_plane.authorization import WORKLOADS_READ
from .workloads import (
    NodeResult,
    WorkloadKind,
    WorkloadOwner,
    WorkloadQuery,
    WorkloadState,
    node_result_to_dict,
    parse_workload_query,
)

NODE_WORKLOADS_TOOL_NAME = "node_workloads"

_ERRORS = {
    "invalid_workload_query": "workload query is invalid",
    "idempotency_not_supported": "workload reads do not support idempotency keys",
    "invalid_workload_request": "workload request is invalid",
    "workload_source_unavailable": "workload source is unavailable",
}


def node_workloads_declaration() -> dict[str, Any]:
    """Return the single canonical declaration reused by controller surfaces."""

    return {
        "name": NODE_WORKLOADS_TOOL_NAME,
        "description": "Read bounded node-local workload metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "enum": [item.value for item in WorkloadOwner]},
                "kind": {"type": "string", "enum": [item.value for item in WorkloadKind]},
                "state": {"type": "string", "enum": [item.value for item in WorkloadState]},
                "host": {
                    "type": "string",
                    "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$",
                    "maxLength": 64,
                },
                "active_only": {"type": "boolean", "default": False},
                "recent_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 86400,
                    "default": 3600,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "default": 200,
                },
            },
            "additionalProperties": False,
        },
        "_meta": {"anvil/requiredScope": WORKLOADS_READ},
    }


def parse_node_workload_query(arguments: object) -> WorkloadQuery:
    """Parse only the canonical seven workload query fields."""

    if type(arguments) is not dict:
        raise ValueError("workload query is invalid")
    return parse_workload_query(arguments)


def workload_success(result: NodeResult) -> dict[str, Any]:
    if type(result) is not NodeResult:
        return workload_failure("workload_source_unavailable")
    return {"ok": True, "data": node_result_to_dict(result)}


def workload_failure(code: str) -> dict[str, Any]:
    message = _ERRORS.get(code)
    if message is None:
        code = "workload_source_unavailable"
        message = _ERRORS[code]
    return {"ok": False, "error": {"code": code, "message": message}}


def is_exact_node_workloads_declaration(value: object) -> bool:
    return isinstance(value, Mapping) and dict(value) == node_workloads_declaration()


__all__ = [
    "NODE_WORKLOADS_TOOL_NAME",
    "is_exact_node_workloads_declaration",
    "node_workloads_declaration",
    "parse_node_workload_query",
    "workload_failure",
    "workload_success",
]
