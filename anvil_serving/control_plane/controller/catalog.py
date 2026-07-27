"""Immutable controller tool-catalog and operation-allowlist validation."""

from __future__ import annotations

from typing import Callable, Optional, Sequence

from ... import mcp
from .errors import ControllerError


ListToolsFunc = Callable[[], list[dict]]
CallToolFunc = Callable[[str, Optional[dict]], dict]


def _mcp_tool_name(name: str) -> str:
    """Translate declared topology operation names to MCP catalog names."""
    return name.replace("-", "_")


def _validated_tool_catalog(
    list_tools_func: ListToolsFunc,
    allowed_operations: Optional[Sequence[str]] = None,
) -> tuple[list[dict], dict[str, str]]:
    """Snapshot a tool catalog and reject ambiguous normalized names."""
    tools = list_tools_func()
    normalized: dict[str, str] = {}
    for tool in tools:
        name = tool.get("name") if isinstance(tool, dict) else None
        if not isinstance(name, str):
            continue
        catalog_name = _mcp_tool_name(name)
        existing = normalized.setdefault(catalog_name, name)
        if existing != name:
            raise ControllerError(
                "ambiguous_tool_catalog",
                "controller tool catalog contains hyphen/underscore normalization collisions",
                status=500,
                details={"tools": sorted((existing, name))},
            )
    if allowed_operations is not None:
        remote_by_name = {
            declaration["name"]: declaration
            for declaration in mcp.operation_declarations()
        }
        allowed: set[str] = set()
        unknown: list[str] = []
        for name in allowed_operations:
            normalized_name = _mcp_tool_name(name)
            declaration = remote_by_name.get(normalized_name.replace("_", "-"))
            if declaration is not None:
                tool = declaration.get("tool")
                if declaration.get("mode") == "tool" and isinstance(tool, str):
                    allowed.add(_mcp_tool_name(tool))
                continue
            if normalized_name in normalized:
                allowed.add(normalized_name)
            else:
                unknown.append(normalized_name)
        unknown.sort()
        if unknown:
            raise ControllerError(
                "unknown_allowed_operation",
                "controller allowlist contains operations absent from the tool catalog",
                status=400,
                details={"operations": unknown},
            )
        tools = [
            tool
            for tool in tools
            if isinstance(tool, dict)
            and isinstance(tool.get("name"), str)
            and _mcp_tool_name(tool["name"]) in allowed
        ]
        normalized = {
            key: value for key, value in normalized.items() if key in allowed
        }
    return tools, normalized
