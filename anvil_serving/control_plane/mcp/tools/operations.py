"""Command-tree operation contracts exposed through MCP."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ....commands import COMMAND_TREE
from ..arguments import schema
from ..catalog import ToolFamily
from ..catalog import operation_declarations as catalog_operation_declarations
from ..errors import ToolError
from ..errors import ok


ToolCatalog = Mapping[str, Mapping[str, Any]]
ToolCatalogProvider = Callable[[], ToolCatalog]


def operation_declarations(tools: ToolCatalog) -> list[dict]:
    """Return every command-tree operation declared for controller transport."""

    return catalog_operation_declarations(COMMAND_TREE.nodes, tools)


def _tool_operation_contracts(args: dict, tools: ToolCatalog) -> dict:
    if args:
        raise ToolError(
            "bad_argument",
            "operation_contracts does not accept arguments",
        )
    return ok({"operations": operation_declarations(tools)})


def build_family(tools: ToolCatalogProvider) -> ToolFamily:
    """Build the operation family with an explicit final-catalog dependency."""

    def tool_operation_contracts(args: dict) -> dict:
        return _tool_operation_contracts(args, tools())

    return ToolFamily(
        name="operations",
        tools={
            "operation_contracts": {
                "description": (
                    "List command-tree operations declared for bounded controller transport."
                ),
                "inputSchema": schema({}),
                "handler": tool_operation_contracts,
            },
        },
    )
