"""Command-tree operation contracts exposed through MCP."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .... import controller_diagnostics
from ....commands import COMMAND_TREE
from ..arguments import bounded_integer_schema, schema
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


def _diagnostic_arguments(args: dict, *, logs: bool) -> tuple[str, int]:
    """Validate the fixed diagnostic inputs before the shared library is called."""

    allowed = {"container", "tail"} if logs else {"container"}
    if type(args) is not dict or set(args) - allowed:
        raise ToolError("bad_argument", "invalid controller diagnostic arguments")
    container = args.get("container")
    if type(container) is not str:
        raise ToolError("bad_argument", "invalid controller diagnostic arguments")
    tail = args.get("tail", controller_diagnostics.DEFAULT_LOG_TAIL)
    if type(tail) is not int:
        raise ToolError("bad_argument", "invalid controller diagnostic arguments")
    try:
        return (
            controller_diagnostics.validate_container_name(container),
            controller_diagnostics.validate_log_tail(tail),
        )
    except ValueError:
        raise ToolError("bad_argument", "invalid controller diagnostic arguments") from None


def _controller_inspect(args: dict) -> dict:
    container, _tail = _diagnostic_arguments(args, logs=False)
    return ok(controller_diagnostics.inspect_controller(container))


def _controller_logs(args: dict) -> dict:
    container, tail = _diagnostic_arguments(args, logs=True)
    return ok(controller_diagnostics.controller_logs(container, tail))


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
            "controller_inspect": {
                "description": "Read metadata-only diagnostics for one local controller container.",
                "inputSchema": schema(
                    {"container": {"type": "string", "minLength": 1, "maxLength": 128}},
                    required=["container"],
                ),
                "handler": _controller_inspect,
            },
            "controller_logs": {
                "description": "Read bounded metadata-only audit diagnostics for one local controller container.",
                "inputSchema": schema(
                    {
                        "container": {"type": "string", "minLength": 1, "maxLength": 128},
                        "tail": bounded_integer_schema(1, 200, 100),
                    },
                    required=["container"],
                ),
                "handler": _controller_logs,
            },
        },
    )
