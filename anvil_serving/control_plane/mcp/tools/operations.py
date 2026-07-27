"""Command-tree operation contracts exposed through MCP."""

from __future__ import annotations

from ....commands import COMMAND_TREE
from ..arguments import schema
from ..catalog import ToolFamily
from ..catalog import operation_declarations as catalog_operation_declarations
from ..errors import ToolError
from ..errors import ok


def operation_declarations() -> list[dict]:
    """Return every command-tree operation declared for controller transport."""

    from . import TOOLS

    return catalog_operation_declarations(COMMAND_TREE.nodes, TOOLS)


def tool_operation_contracts(args: dict) -> dict:
    if args:
        raise ToolError(
            "bad_argument",
            "operation_contracts does not accept arguments",
        )
    return ok({"operations": operation_declarations()})


FAMILY = ToolFamily(
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
