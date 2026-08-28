"""Strict, bounded MCP argument and schema validation."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from typing import Any

from ...operator_output import redact
from .errors import ToolError
from .security import ENV_NAME_RE, PROBE_API_KEY_ENVS
from .security import RAW_COMMAND_KEYS, RAW_SECRET_KEYS


MAX_ARGUMENT_BYTES = 1024 * 1024
MAX_CONTEXT_BYTES = 16 * 1024
MAX_CONTEXT_STRING = 1024
MAX_SCHEMA_STRING = 262144
MAX_SCHEMA_ITEMS = 1000
RAW_SECRET_AWARE_TOOLS = frozenset(
    {
        "benchmark_artifact",
        "benchmark_probe",
        "decision_summary",
        "client_catalog_sync",
        "openclaw_sync",
        "preflight_probe",
    }
)


def probe_api_key_env(args: Mapping[str, Any]) -> str:
    """Validate the credential-reference contract shared by bounded probes."""

    if "api_key" in args:
        raise ToolError(
            "raw_secret_not_allowed",
            "raw api_key is not accepted; set api_key_env to the credential env var name",
        )
    api_key_env = str_arg(args, "api_key_env", "")
    if not api_key_env:
        return ""
    if not ENV_NAME_RE.fullmatch(api_key_env):
        raise ToolError(
            "bad_api_key_env",
            "api_key_env must name an ENV VAR matching ^[A-Z][A-Z0-9_]*$",
        )
    if api_key_env not in PROBE_API_KEY_ENVS:
        raise ToolError(
            "unsafe_api_key_env",
            "api_key_env must be ANVIL_ROUTER_TOKEN for MCP probe tools",
            {"allowed_api_key_envs": sorted(PROBE_API_KEY_ENVS)},
        )
    return api_key_env


def arg_bool(value: Any, default: bool = False, *, name: str = "argument") -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ToolError("bad_argument", "%r must be a boolean" % name)


def str_arg(
    args: dict,
    name: str,
    default: str | None = None,
    required: bool = False,
) -> str:
    value = args.get(name, default)
    if required and (value is None or value == ""):
        raise ToolError(
            "missing_argument",
            "missing required argument %r" % name,
        )
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ToolError("bad_argument", "%r must be a string" % name)
    return value


def int_arg(args: dict, name: str, default: int) -> int:
    value = args.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError("bad_argument", "%r must be an integer" % name)
    return value


def str_list_arg(args: dict, name: str) -> list[str]:
    value = args.get(name, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ToolError(
            "bad_argument",
            "%r must be an array of strings" % name,
        )
    return list(value)


def bounded_int_arg(
    args: dict,
    name: str,
    default: int,
    *,
    min_value: int,
    max_value: int,
) -> int:
    value = int_arg(args, name, default)
    if value < min_value or value > max_value:
        raise ToolError(
            "bad_argument",
            "%r must be between %d and %d" % (name, min_value, max_value),
            {"value": value},
        )
    return value


def bounded_float_arg(
    args: dict,
    name: str,
    default: float,
    *,
    min_value: float,
    max_value: float,
) -> float:
    value = args.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError("bad_argument", "%r must be a number" % name)
    result = float(value)
    if not math.isfinite(result) or result < min_value or result > max_value:
        raise ToolError(
            "bad_argument",
            "%r must be between %s and %s" % (name, min_value, max_value),
            {"value": value},
        )
    return result


def bounded_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return a recursively bounded copy of the supported JSON-schema subset."""

    bounded = dict(schema)
    schema_type = bounded.get("type")
    schema_types = schema_type if isinstance(schema_type, list) else [schema_type]
    if "string" in schema_types:
        bounded.setdefault("maxLength", MAX_SCHEMA_STRING)
    if "array" in schema_types:
        bounded.setdefault("maxItems", MAX_SCHEMA_ITEMS)
        items = bounded.get("items")
        if isinstance(items, Mapping):
            bounded["items"] = bounded_schema(items)
    if "object" in schema_types:
        properties = bounded.get("properties")
        if isinstance(properties, Mapping):
            bounded["properties"] = {
                str(name): bounded_schema(value)
                for name, value in properties.items()
                if isinstance(value, Mapping)
            }
    return bounded


def bounded_tool_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    bounded = bounded_schema(schema)
    bounded["maxProperties"] = len(bounded.get("properties", {}))
    return bounded


def schema(properties: dict, required: list[str] | None = None) -> dict:
    return bounded_tool_schema(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": required or [],
        }
    )


def bounded_integer_schema(minimum: int, maximum: int, default: int) -> dict:
    return {
        "type": "integer",
        "minimum": minimum,
        "maximum": maximum,
        "default": default,
    }


def serialized_size(value: Any, *, code: str, message: str) -> int:
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise ToolError(code, message, {"error": redact(str(exc))}) from exc


def private_input_kind(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in RAW_COMMAND_KEYS:
                return "command"
            if normalized in RAW_SECRET_KEYS or normalized in {
                "env",
                "environment",
                "environ",
            }:
                return "secret"
            found = private_input_kind(item)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = private_input_kind(item)
            if found:
                return found
    return None


def validate_schema_value(
    value: Any,
    schema_value: Mapping[str, Any],
    field: str,
) -> None:
    schema_type = schema_value.get("type")
    allowed_types = schema_type if isinstance(schema_type, list) else [schema_type]
    valid = False
    for allowed in allowed_types:
        if allowed == "null" and value is None:
            valid = True
        elif allowed == "boolean" and isinstance(value, bool):
            valid = True
        elif (
            allowed == "integer"
            and isinstance(value, int)
            and not isinstance(value, bool)
        ):
            valid = True
        elif (
            allowed == "number"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            valid = True
        elif allowed == "string" and isinstance(value, str):
            valid = True
        elif allowed == "array" and isinstance(value, list):
            valid = True
        elif allowed == "object" and isinstance(value, Mapping):
            valid = True
    if not valid:
        expected = ", ".join(str(item) for item in allowed_types)
        raise ToolError(
            "bad_argument",
            f"{field!r} must have type {expected}",
        )
    if isinstance(value, str):
        if len(value) < int(schema_value.get("minLength", 0)):
            raise ToolError(
                "bad_argument",
                f"{field!r} is shorter than its length limit",
            )
        if len(value) > int(schema_value.get("maxLength", MAX_SCHEMA_STRING)):
            raise ToolError(
                "bad_argument",
                f"{field!r} exceeds its length limit",
            )
        if "enum" in schema_value and value not in schema_value["enum"]:
            code = "bad_action" if field == "action" else "bad_argument"
            raise ToolError(
                code,
                f"{field!r} must be one of {schema_value['enum']!r}",
            )
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema_value and value < schema_value["minimum"]:
            raise ToolError(
                "bad_argument",
                f"{field!r} must be at least {schema_value['minimum']}",
            )
        if "maximum" in schema_value and value > schema_value["maximum"]:
            raise ToolError(
                "bad_argument",
                f"{field!r} must be at most {schema_value['maximum']}",
            )
    if isinstance(value, list):
        if len(value) > int(schema_value.get("maxItems", MAX_SCHEMA_ITEMS)):
            raise ToolError(
                "bad_argument",
                f"{field!r} contains too many items",
            )
        item_schema = schema_value.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                validate_schema_value(item, item_schema, f"{field}[{index}]")
    if isinstance(value, Mapping):
        if len(value) > int(schema_value.get("maxProperties", MAX_SCHEMA_ITEMS)):
            raise ToolError(
                "bad_argument",
                f"{field!r} contains too many properties",
            )
        properties = schema_value.get("properties", {})
        if isinstance(properties, Mapping):
            unknown = sorted(set(value) - set(properties))
            if unknown and schema_value.get("additionalProperties") is False:
                raise ToolError(
                    "bad_argument",
                    f"{field!r} contains unknown properties",
                    {"fields": unknown},
                )
            missing = [
                name for name in schema_value.get("required", []) if name not in value
            ]
            if missing:
                raise ToolError(
                    "missing_argument",
                    f"{field!r} is missing required properties",
                    {"fields": missing},
                )
            for name, item in value.items():
                child = properties.get(name)
                if isinstance(child, Mapping):
                    validate_schema_value(item, child, f"{field}.{name}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ToolError(
            "bad_argument",
            f"{field!r} must be a finite number",
        )


def validate_tool_arguments(
    name: str,
    arguments: Mapping[str, Any],
    tools: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if name not in tools:
        raise ToolError("unknown_tool", "unknown tool %r" % name)
    if not isinstance(arguments, Mapping):
        raise ToolError("bad_arguments", "tool arguments must be an object")

    private_kind = None
    for field, value in arguments.items():
        normalized = str(field).lower().replace("-", "_")
        if normalized in RAW_COMMAND_KEYS:
            private_kind = "command"
            break
        if normalized in RAW_SECRET_KEYS or normalized in {
            "env",
            "environment",
            "environ",
        }:
            if name in RAW_SECRET_AWARE_TOOLS:
                private_kind = "secret"
                break
            continue
        if field not in {"packet", "records"}:
            private_kind = private_input_kind(value)
            if private_kind:
                break
    if private_kind == "command":
        raise ToolError(
            "raw_command_not_allowed",
            "raw command payloads are not accepted; use a declared MCP operation",
        )
    if private_kind == "secret":
        raise ToolError(
            "raw_secret_not_allowed",
            (
                "raw secrets are not accepted; pass an approved credential "
                "environment variable name"
            ),
        )
    if (
        serialized_size(
            arguments,
            code="bad_arguments",
            message="tool arguments must contain JSON values",
        )
        > MAX_ARGUMENT_BYTES
    ):
        raise ToolError(
            "arguments_too_large",
            "tool arguments exceed the configured size limit",
        )
    tool_schema = tools[name]["inputSchema"]
    properties = tool_schema.get("properties", {})
    unknown = sorted(set(arguments) - set(properties))
    guarded_unknown = {"confirm", "dry_run", "execute", "yes"}
    if unknown and not (
        name == "cache_prune_plan" and set(unknown) <= guarded_unknown
    ):
        raise ToolError(
            "bad_argument",
            "unknown tool argument",
            {"fields": unknown},
        )
    missing = [
        field
        for field in tool_schema.get("required", [])
        if field not in arguments
    ]
    if missing:
        raise ToolError(
            "missing_argument",
            "missing required tool argument",
            {"fields": missing},
        )
    for field, value in arguments.items():
        if field in properties and not (
            name == "workflow_packet_validate" and field == "packet"
        ):
            validate_schema_value(value, properties[field], field)
    return dict(arguments)


def target_context(
    value: Any,
    *,
    context_fields: tuple[str, ...],
    context_schema: Mapping[str, Any],
    context_builder: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ToolError("bad_context", "target context must be an object")
    unknown = sorted(set(value) - set(context_fields))
    if unknown:
        raise ToolError(
            "bad_context",
            "target context contains unknown fields",
            {"fields": unknown},
        )
    if (
        serialized_size(
            value,
            code="bad_context",
            message="target context must contain JSON values",
        )
        > MAX_CONTEXT_BYTES
    ):
        raise ToolError(
            "context_too_large",
            "target context exceeds the configured size limit",
        )
    for field, item in value.items():
        validate_schema_value(
            item,
            context_schema["properties"][field],
            field,
        )
    return context_builder(value)
