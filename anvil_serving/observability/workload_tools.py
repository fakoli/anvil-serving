"""Closed controller tool contracts for node and fleet workload visibility."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..control_plane.authorization import WORKLOADS_READ
from .workloads import (
    FleetResult,
    NodeResult,
    WorkloadKind,
    WorkloadOwner,
    WorkloadQuery,
    WorkloadState,
    fleet_result_from_dict,
    fleet_result_to_dict,
    node_result_from_dict,
    node_result_to_dict,
    parse_workload_query,
)

NODE_WORKLOADS_TOOL_NAME = "node_workloads"
FLEET_WORKLOADS_TOOL_NAME = "fleet_workloads"

_ERRORS = {
    "invalid_workload_query": "workload query is invalid",
    "idempotency_not_supported": "workload reads do not support idempotency keys",
    "invalid_workload_request": "workload request is invalid",
    "workload_source_unavailable": "workload source is unavailable",
}


def _declaration(name: str, description: str) -> dict[str, Any]:
    """Build a fresh canonical schema for either sealed read-only operation."""

    return {
        "name": name,
        "description": description,
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
            "required": [],
            "maxProperties": 7,
        },
        "_meta": {"anvil/requiredScope": WORKLOADS_READ},
    }


def node_workloads_declaration() -> dict[str, Any]:
    return _declaration(NODE_WORKLOADS_TOOL_NAME, "Read bounded node-local workload metadata.")


def fleet_workloads_declaration() -> dict[str, Any]:
    return _declaration(FLEET_WORKLOADS_TOOL_NAME, "Read bounded declared-fleet workload metadata.")


def parse_node_workload_query(arguments: object) -> WorkloadQuery:
    """Parse only the canonical seven workload query fields."""

    if type(arguments) is not dict:
        raise ValueError("workload query is invalid")
    return parse_workload_query(arguments)


def workload_success(result: NodeResult | FleetResult) -> dict[str, Any]:
    try:
        if type(result) is NodeResult:
            data = node_result_to_dict(result)
            node_result_from_dict(data)
            return {"ok": True, "data": data}
        if type(result) is FleetResult:
            data = fleet_result_to_dict(result)
            fleet_result_from_dict(data)
            return {"ok": True, "data": data}
    except Exception:
        pass
    return workload_failure("workload_source_unavailable")


def workload_failure(code: str) -> dict[str, Any]:
    message = _ERRORS.get(code)
    if message is None:
        code = "workload_source_unavailable"
        message = _ERRORS[code]
    return {"ok": False, "error": {"code": code, "message": message}}


def is_exact_node_workloads_declaration(value: object) -> bool:
    return isinstance(value, Mapping) and dict(value) == node_workloads_declaration()


def is_exact_fleet_workloads_declaration(value: object) -> bool:
    return isinstance(value, Mapping) and dict(value) == fleet_workloads_declaration()


__all__ = [
    "FLEET_WORKLOADS_TOOL_NAME",
    "NODE_WORKLOADS_TOOL_NAME",
    "fleet_workloads_declaration",
    "is_exact_fleet_workloads_declaration",
    "is_exact_node_workloads_declaration",
    "node_workloads_declaration",
    "parse_node_workload_query",
    "workload_failure",
    "workload_success",
]
