"""Static MCP catalog validation, declaration mapping, and direct lookup."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ...commands import CommandNode
from .errors import ToolError


def build_catalog(specifications: Mapping[str, Mapping[str, Any]]) -> dict[str, dict]:
    """Validate static tool specifications once while preserving declaration order."""

    catalog: dict[str, dict] = {}
    for name, raw_spec in specifications.items():
        if not isinstance(name, str) or not name:
            raise RuntimeError("MCP tool names must be non-empty strings")
        description = raw_spec.get("description")
        schema = raw_spec.get("inputSchema")
        handler = raw_spec.get("handler")
        if not isinstance(description, str) or not description:
            raise RuntimeError("MCP tool %r has no description" % name)
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            raise RuntimeError("MCP tool %r has an invalid input schema" % name)
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, Mapping) or not isinstance(required, list):
            raise RuntimeError("MCP tool %r has an incomplete input schema" % name)
        if schema.get("additionalProperties") is not False:
            raise RuntimeError(
                "MCP tool %r must reject additional properties" % name
            )
        if schema.get("maxProperties") != len(properties):
            raise RuntimeError("MCP tool %r has an unbounded object schema" % name)
        if not set(required) <= set(properties):
            raise RuntimeError(
                "MCP tool %r requires undeclared properties" % name
            )
        if not callable(handler):
            raise RuntimeError("MCP tool %r has no callable handler" % name)
        catalog[name] = dict(raw_spec)
    return catalog


def operation_records(
    nodes: tuple[CommandNode, ...],
    parent: tuple[str, ...] = (),
) -> Iterable[dict[str, Any]]:
    for node in nodes:
        path = parent + (node.name,)
        if node.visible and node.remote_operation is not None:
            remote = node.remote_operation
            yield {
                "name": "-".join(path),
                "path": " ".join(path),
                "mode": remote.mode,
                "tool": remote.tool,
                "fixed_arguments": dict(remote.fixed_arguments),
                "confirmed_arguments": dict(remote.confirmed_arguments),
                "allowed_arguments": list(remote.allowed_arguments),
                "positional_arguments": list(remote.positional_arguments),
                "resource_role": node.resource_role,
                "transports": list(node.transports),
                "execution_runtime_roles": list(node.execution_runtime_roles),
                "mutation_class": node.mutation_class,
                "recovery_capable": node.recovery_capable,
                "gpu_role_required": node.gpu_role_required,
                "execution_policy": node.execution_policy,
                "output_policy": node.output_policy,
            }
        yield from operation_records(node.children, path)


def operation_declarations(
    nodes: tuple[CommandNode, ...],
    tools: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    declarations = list(operation_records(nodes))
    missing_tools = sorted(
        {
            declaration["tool"]
            for declaration in declarations
            if declaration["mode"] == "tool"
            and declaration["tool"] not in tools
        }
    )
    if missing_tools:
        raise RuntimeError(
            "remote command declarations reference missing MCP tools: %s"
            % ", ".join(missing_tools)
        )
    return declarations


def list_tools(
    tools: Mapping[str, Mapping[str, Any]],
    target_context_schema: Mapping[str, Any],
) -> list[dict]:
    return [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": spec["inputSchema"],
            "_meta": {
                "anvil/targetContextSchema": target_context_schema,
                "anvil/operationContractTool": "operation_contracts",
            },
        }
        for name, spec in tools.items()
    ]


def call_tool(
    tools: Mapping[str, Mapping[str, Any]],
    name: str,
    arguments: dict | None,
    *,
    validate_arguments: Callable[[str, Mapping[str, Any]], dict[str, Any]],
    fail: Callable[[str, str, Mapping[str, Any] | None], dict],
    redact_text: Callable[[str], str],
) -> dict:
    """Dispatch by one direct dictionary lookup."""

    spec = tools.get(name)
    if spec is None:
        return fail("unknown_tool", "unknown tool %r" % name, None)
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return fail(
            "bad_arguments",
            "tool arguments must be an object",
            None,
        )
    try:
        validated = validate_arguments(name, arguments)
        return spec["handler"](validated)
    except ToolError as exc:
        return fail(exc.code, exc.message, exc.details)
    except Exception as exc:
        return fail("internal_error", redact_text(str(exc)), None)
