"""Explicit propagation tools bound to one trusted controller instance."""

from __future__ import annotations

from datetime import datetime, timezone

from ...propagation import STATUS_SCOPE, capability_declaration
from ...propagation_jobs import PropagationJobError, PropagationService
from ...controller.propagation_active_identity import ObservedActiveIdentity
from ..arguments import schema
from ..catalog import ToolFamily
from ..errors import ToolError, ok
from ..security import authenticated_caller


OPERATION_NAMES = frozenset({
    "propagation.activation.observe.v1",
    "propagation.accept.v1", "propagation.profile.v1", "propagation.preview.v1", "propagation.status.v1",
    "propagation.resume.v1", "propagation.cancel.v1",
    "propagation.recovery.verify.v1", "propagation.recovery.cancel.v1",
    "propagation.dispatch.pending.v1", "propagation.dispatch.record.v1",
    *("fleet.propagation." + verb + ".v1" for verb in (
        "preview", "submit", "status", "verify", "convergence", "cancel",
        "current", "revoked",
    )),
})


def build_family(service: PropagationService | None = None,
                 observer: ObservedActiveIdentity | None = None,
                 observer_profile_sha256: str = "") -> ToolFamily:
    def _capabilities(args: dict) -> dict:
        if args:
            raise ToolError("bad_argument", "propagation capabilities accepts no arguments")
        declaration = capability_declaration()
        for operation in declaration["operations"]:
            operation["available"] = (service is not None
                and operation["name"] in OPERATION_NAMES
                and service.operation_available(operation["name"]))
        return ok(declaration)

    tools = {"propagation_capabilities": {
        "description": "Read typed propagation owner capabilities.",
        "inputSchema": schema({}), "handler": _capabilities, "requiredScope": STATUS_SCOPE,
    }}
    if observer is not None:
        def observe(args: dict) -> dict:
            if args:
                raise ToolError("bad_argument", "activation observation accepts no arguments")
            try:
                identity = observer()
            except PropagationJobError as exc:
                raise ToolError(exc.code, "active identity could not be verified") from None
            return ok({"activation_ref": identity.activation_ref,
                       "activation_digest": identity.activation_digest,
                       "observer_profile_sha256": observer_profile_sha256,
                       "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")})
        tools["propagation.activation.observe.v1"] = {
            "description": "Verify the installed, pinned activation without exposing Docker or router credentials.",
            "inputSchema": schema({}), "handler": observe, "requiredScope": STATUS_SCOPE,
        }
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
            if name in OPERATION_NAMES and service.operation_available(name):
                tools[name] = {
                    "description": "Run a bounded approved propagation owner operation.",
                    "inputSchema": operation["input_schema"],
                    "handler": handler(name), "requiredScope": operation["required_scope"],
                }
    return ToolFamily(name="propagation", tools=tools)


FAMILY = build_family()
