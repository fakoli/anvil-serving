"""Shared controller tool-name normalization."""

from __future__ import annotations


def mcp_tool_name(name: str) -> str:
    """Translate declared topology operation names to MCP catalog names."""

    return name.replace("-", "_")
