"""Explicit propagation tools bound to one trusted controller instance."""

from __future__ import annotations

from ...propagation import STATUS_SCOPE, capability_declaration
from ...propagation_jobs import PropagationJobError, PropagationService
from ..arguments import schema
from ..catalog import ToolFamily
from ..errors import ToolError, ok
from ..security import authenticated_caller


OPERATION_NAMES = frozenset({
    "propagation.accept.v1", "propagation.profile.v1", "propagation.status.v1",
    "propagation.resume.v1", "propagation.cancel.v1",
    "propagation.recovery.verify.v1",
    "propagation.dispatch.pending.v1", "propagation.dispatch.record.v1",
    *("fleet.propagation." + verb + ".v1" for verb in (
        "preview", "submit", "status", "verify", "convergence", "cancel",
        "current",
    )),
})


def build_family(service: PropagationService | None = None) -> ToolFamily:
    def _capabilities(args: dict) -> dict:
        if args:
            raise ToolError("bad_argument", "propagation capabilities accepts no arguments")
        declaration = capability_declaration()
        for operation in declaration["operations"]:
            operation["available"] = service is not None and operation["name"] in OPERATION_NAMES
        return ok(declaration)

    tools = {"propagation_capabilities": {
        "description": "Read typed propagation owner capabilities.",
        "inputSchema": schema({}), "handler": _capabilities, "requiredScope": STATUS_SCOPE,
    }}
    if service is not None:
        def handler(name):
            def call(arguments):
                try:
                    return ok(service.handle(name, arguments, caller_id=authenticated_caller().principal))
                except PropagationJobError as exc:
                    raise ToolError(exc.code, "propagation owner refused the operation") from None
            return call

        for operation in capability_declaration()["operations"]:
            name = operation["name"]
            if name in OPERATION_NAMES:
                tools[name] = {
                    "description": "Run a bounded approved propagation owner operation.",
                    "inputSchema": operation["input_schema"],
                    "handler": handler(name), "requiredScope": operation["required_scope"],
                }
    return ToolFamily(name="propagation", tools=tools)


FAMILY = build_family()
