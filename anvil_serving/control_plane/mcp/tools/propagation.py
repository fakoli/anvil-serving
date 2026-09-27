"""Read-only propagation capability declaration; execution arrives in later tasks."""

from __future__ import annotations

from ...propagation import STATUS_SCOPE, capability_declaration
from ..arguments import schema
from ..catalog import ToolFamily
from ..errors import ToolError, ok


def _capabilities(args: dict) -> dict:
    if args:
        raise ToolError("bad_argument", "propagation_capabilities does not accept arguments")
    return ok(capability_declaration())


FAMILY = ToolFamily(
    name="propagation",
    tools={
        "propagation_capabilities": {
            "description": "Read the inert typed Anvil propagation v1 operation declaration.",
            "inputSchema": schema({}),
            "handler": _capabilities,
            "requiredScope": STATUS_SCOPE,
        },
    },
)
