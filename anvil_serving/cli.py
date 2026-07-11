"""Tree-driven command dispatcher for ``anvil-serving``."""
from __future__ import annotations

import contextlib
import difflib
import io
import sys
import uuid
from importlib import metadata as importlib_metadata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from . import __version__
from . import guard
from .command_tree import COMMAND_TREE, CommandNode, CommandOption, Tombstone
from .operator_output import (
    EXIT_CODES,
    OutputOptions,
    OperatorError,
    PartialResultError,
    SafetyError,
    TransportError,
    UsageError,
    classify_command_policy,
    enforce_command_policy,
    error_envelope,
    render_human,
    render_json,
    success_envelope,
)
from .targets import CommandSpec, ExecutionPlan, TargetResolutionError, resolve_execution_plan
from .topology import TopologyValidationError, load_topology
from .transports import (
    ControllerTransport,
    Operation,
    TransportError as AdapterTransportError,
    TransportResult,
    execute_plan,
)


MIN_PYTHON = (3, 11)
_DOCS_URL = "https://fakoli.github.io/anvil-serving/CLI/"
_GROUP_ORDER = (
    "Data plane",
    "Local serving tools",
    "Quality loop",
    "Control plane & integrations",
    "Voice",
)
_RESOLUTION_VALUE_OPTIONS = {
    "--topology": "topology",
    "--topology-overlay": "topology_overlay",
    "--command-host": "command_host",
    "--command-runtime": "command_runtime",
    "--target": "target",
    "--transport": "transport",
}
_HANDLER_PROGS = {
    "anvil_serving.benchmark": "anvil-serving eval benchmark run",
    "anvil_serving.calibrate": "anvil-serving eval calibrate",
    "anvil_serving.controller": "anvil-serving controller",
    "anvil_serving.doctor": "anvil-serving doctor",
    "anvil_serving.eval": "anvil-serving eval",
    "anvil_serving.external_benchmarks.cli": "anvil-serving eval benchmark external",
    "anvil_serving.gpus": "anvil-serving host gpus",
    "anvil_serving.harness": "anvil-serving harness",
    "anvil_serving.host": "anvil-serving host",
    "anvil_serving.init": "anvil-serving init",
    "anvil_serving.mcp": "anvil-serving mcp serve",
    "anvil_serving.models": "anvil-serving models",
    "anvil_serving.multiplexer": "anvil-serving serves multiplex",
    "anvil_serving.preflight": "anvil-serving eval preflight",
    "anvil_serving.profile": "anvil-serving eval usage",
    "anvil_serving.router.serve": "anvil-serving router run",
    "anvil_serving.router_manage": "anvil-serving router",
    "anvil_serving.serves": "anvil-serving serves",
    "anvil_serving.voice.cli": "anvil-serving voice",
    "anvil_serving.voice_sidecar": "anvil-serving voice-sidecar",
}


@dataclass(frozen=True)
class _ResolutionOptions:
    topology: str | None = None
    topology_overlay: str | None = None
    command_host: str | None = None
    command_runtime: str | None = None
    target: str | None = None
    transport: str = "auto"
    experimental_model_workload: bool = False

    @property
    def requested(self) -> bool:
        return any(
            value is not None
            for value in (
                self.topology,
                self.topology_overlay,
                self.command_host,
                self.command_runtime,
                self.target,
            )
        ) or self.transport != "auto" or self.experimental_model_workload


class _ResolutionOptionError(ValueError):
    """A declarative target-resolution option could not be parsed."""


def _installed_version() -> str:
    """Read the version from the installed distribution metadata."""
    try:
        return importlib_metadata.version("anvil-serving")
    except importlib_metadata.PackageNotFoundError:
        return __version__


def _check_python_version(version_info=None):
    """Return an error message if running under an unsupported interpreter, else None."""
    vi = version_info if version_info is not None else sys.version_info
    if (vi[0], vi[1]) < MIN_PYTHON:
        return "anvil-serving needs Python >=%d.%d; you have %d.%d" % (
            MIN_PYTHON[0], MIN_PYTHON[1], vi[0], vi[1]
        )
    return None


def _visible(nodes: Sequence[CommandNode]) -> tuple[CommandNode, ...]:
    return tuple(node for node in nodes if node.visible)


def _find(nodes: Sequence[CommandNode], name: str) -> CommandNode | None:
    return next((node for node in nodes if node.name == name), None)


def _command_name(path: Sequence[CommandNode]) -> str:
    return " ".join(node.name for node in path)


def _resolve(argv: Sequence[str]):
    """Resolve path tokens before any command module is imported."""
    nodes: Sequence[CommandNode] = COMMAND_TREE.nodes
    path: list[CommandNode] = []
    for index, token in enumerate(argv):
        if token.startswith("-"):
            return tuple(path), tuple(argv[index:]), None, tuple(nodes)
        node = _find(nodes, token)
        if node is None:
            return tuple(path), tuple(argv[index + 1 :]), token, tuple(nodes)
        path.append(node)
        # A leaf tombstone owns every following positional token. Tombstoned
        # groups can still contain canonical children (for example, ``mcp``).
        if node.tombstone is not None and not node.children:
            return tuple(path), tuple(argv[index + 1 :]), None, tuple(nodes)
        if node.handler is not None and not node.children:
            return tuple(path), tuple(argv[index + 1 :]), None, tuple(nodes)
        nodes = node.children
    return tuple(path), (), None, tuple(nodes)


def _print_help() -> None:
    print("anvil-serving - quality-gated local-model router and serving workbench")
    print()
    print("Usage:")
    print("  anvil-serving <command> [options]")
    print("  anvil-serving <command> --help")
    print("  anvil-serving --version")
    print()
    print("Global options:")
    for option in COMMAND_TREE.global_options:
        print("  %-20s %s" % (", ".join(option.flags), option.summary))
    print()
    root_nodes = _visible(COMMAND_TREE.nodes)
    groups = list(_GROUP_ORDER)
    groups.extend(sorted({node.group for node in root_nodes if node.group and node.group not in groups}))
    for group in groups:
        members = [node for node in root_nodes if node.group == group]
        if not members:
            continue
        print("%s:" % group)
        for node in members:
            print("  %-15s %s" % (node.name, node.summary))
    print()
    print("Examples:")
    print("  anvil-serving router run --config configs/example.toml")
    print("  anvil-serving serves status")
    print("  anvil-serving eval preflight --base-url http://127.0.0.1:30000/v1 --model local")
    print("  anvil-serving eval benchmark external list")
    print("  anvil-serving mcp tools")
    print()
    print("Docs: %s" % _DOCS_URL)


def _print_focused_help(path: Sequence[CommandNode]) -> None:
    node = path[-1]
    command = _command_name(path)
    print("anvil-serving %s - %s" % (command, node.summary))
    print()
    print("Usage:")
    suffix = " <action>" if _visible(node.children) else ""
    print("  anvil-serving %s%s [options]" % (command, suffix))
    children = _visible(node.children)
    if children:
        print()
        print("Actions:")
        for child in children:
            print("  %-15s %s" % (child.name, child.summary))
    options = COMMAND_TREE.global_options + node.options
    if options:
        print()
        print("Options:")
        rendered_options = []
        for option in options:
            label = ", ".join(option.flags)
            if option.value_name is not None:
                label += " " + option.value_name
            rendered_options.append((label, option.summary))
        width = max(20, *(len(label) for label, _summary in rendered_options))
        for label, summary in rendered_options:
            print("  %-*s %s" % (width, label, summary))
    print()
    print("Docs: %s" % node.docs_anchor)


def _handler_argv(path: Sequence[CommandNode]) -> tuple[str, ...]:
    node = path[-1]
    assert node.handler is not None
    if node.handler.argv_prefix is not None:
        return node.handler.argv_prefix
    return tuple(item.name for item in path[1:])


def _print_leaf_help(path: Sequence[CommandNode]) -> bool:
    """Render the real leaf parser help under the canonical command path."""
    node = path[-1]
    if node.handler is None:
        return False
    base_prog = _HANDLER_PROGS.get(node.handler.module)
    if base_prog is None:
        return False
    prefix = _handler_argv(path)
    stdout, stderr = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = node.handler.resolve()([*prefix, "--help"])
    except SystemExit as exc:
        if int(exc.code or 0) != 0:
            return False
    else:
        if result not in (None, 0):
            return False
    rendered = stdout.getvalue() or stderr.getvalue()
    if not rendered:
        return False
    old_command = " ".join((base_prog, *prefix))
    canonical = "anvil-serving " + _command_name(path)
    rendered = rendered.replace(old_command, canonical)
    print(rendered, end="" if rendered.endswith("\n") else "\n")
    dispatcher_options = [
        option
        for option in node.options
        if "--confirm" in option.flags
    ]
    global_options = COMMAND_TREE.global_options
    if node.execution_policy != "resource-owner":
        resolution_flags = {*_RESOLUTION_VALUE_OPTIONS, "--experimental-model-workload"}
        global_options = tuple(
            option
            for option in global_options
            if not resolution_flags.intersection(option.flags)
        )
    print("\nDispatcher options:")
    for option in (*global_options, *dispatcher_options):
        label = ", ".join(option.flags)
        if option.value_name:
            label += " " + option.value_name
        print("  %-40s %s" % (label, option.summary))
    print("\nDocs: %s" % node.docs_anchor)
    return True


def _unknown_command(token: str, path: Sequence[CommandNode], siblings: Sequence[CommandNode]) -> int:
    attempted = " ".join([*(node.name for node in path), token])
    print("unknown command: %s" % attempted, file=sys.stderr)
    names = [node.name for node in siblings]
    matches = difflib.get_close_matches(token, names, n=1)
    if matches:
        match = _find(siblings, matches[0])
        assert match is not None
        suggestion = match.tombstone.replacement if match.tombstone else match.name
        print("Did you mean '%s'?" % suggestion, file=sys.stderr)
    print("Run 'anvil-serving --help' to see available commands.", file=sys.stderr)
    return 2


def _tombstone_option(path: Sequence[CommandNode], rest: Sequence[str]) -> CommandOption | None:
    """Return the first declared removed option, without invoking a parser."""
    options = COMMAND_TREE.global_options + tuple(
        option for node in path for option in node.options
    )
    for token in rest:
        if token == "--":
            break
        for option in options:
            if option.tombstone is None:
                continue
            if any(token == flag or token.startswith(f"{flag}=") for flag in option.flags):
                return option
    return None


def _tombstone(path: Sequence[CommandNode], rest: Sequence[str]) -> tuple[str, Tombstone] | None:
    """Find a removed command or option before a handler can be resolved."""
    option = _tombstone_option(path, rest)
    if option is not None:
        assert option.tombstone is not None
        flag = next(flag for flag in option.flags if any(
            token == flag or token.startswith(f"{flag}=") for token in rest
        ))
        return f"{_command_name(path)} {flag}", option.tombstone
    node = path[-1]
    if node.tombstone is not None:
        return _command_name(path), node.tombstone
    return None


def _tombstone_message(removed: str, tombstone: Tombstone) -> str:
    return (
        "anvil-serving: `%s` was removed; use `%s` instead. See %s."
        % (removed, tombstone.replacement, tombstone.docs_anchor)
    )


def _refuse_tombstone(removed: str, tombstone: Tombstone) -> int:
    print(_tombstone_message(removed, tombstone), file=sys.stderr)
    return 2


def _extract_resolution_options(
    argv: Sequence[str],
) -> tuple[_ResolutionOptions, tuple[str, ...]]:
    """Consume dispatcher-owned target options and preserve handler arguments."""
    values: dict[str, str | bool | None] = {
        "topology": None,
        "topology_overlay": None,
        "command_host": None,
        "command_runtime": None,
        "target": None,
        "transport": "auto",
        "experimental_model_workload": False,
    }
    forwarded: list[str] = []
    index = 0
    after_separator = False
    while index < len(argv):
        token = argv[index]
        if token == "--":
            after_separator = True
            forwarded.append(token)
            index += 1
            continue
        if after_separator:
            forwarded.append(token)
            index += 1
            continue
        if token == "--experimental-model-workload":
            values["experimental_model_workload"] = True
            index += 1
            continue
        if token.startswith("--experimental-model-workload="):
            raise _ResolutionOptionError(
                "--experimental-model-workload does not accept a value"
            )

        flag, separator, inline_value = token.partition("=")
        attribute = _RESOLUTION_VALUE_OPTIONS.get(flag)
        if attribute is None:
            forwarded.append(token)
            index += 1
            continue
        if separator:
            value = inline_value
        else:
            index += 1
            if index >= len(argv) or argv[index].startswith("-"):
                raise _ResolutionOptionError(f"{flag} requires a value")
            value = argv[index]
        if not value:
            raise _ResolutionOptionError(f"{flag} requires a value")
        values[attribute] = value
        index += 1
    return _ResolutionOptions(**values), tuple(forwarded)


def _command_spec(path: Sequence[CommandNode]) -> CommandSpec:
    node = path[-1]
    return CommandSpec(
        name="-".join(item.name for item in path),
        resource_role=node.resource_role,
        supported_transports=node.transports,
        execution_runtime_roles=node.execution_runtime_roles,
        mutation_class=node.mutation_class,
        recovery_capable=node.recovery_capable,
        gpu_role_required=node.gpu_role_required,
        execution_policy=node.execution_policy,
    )


def _resolve_dispatch_plan(
    path: Sequence[CommandNode], options: _ResolutionOptions
) -> ExecutionPlan | None:
    """Resolve a topology-aware invocation before importing its handler."""
    if not options.requested:
        return None
    if options.topology is None:
        raise _ResolutionOptionError(
            "target-resolution options require --topology PATH"
        )
    command = _command_spec(path)
    if command.execution_policy != "resource-owner":
        raise _ResolutionOptionError(
            f"{_command_name(path)} does not support target-resolution options"
        )
    topology = load_topology(options.topology)
    return resolve_execution_plan(
        topology,
        command,
        target=options.target,
        transport=options.transport,
        command_host=options.command_host,
        command_runtime=options.command_runtime,
        overlay=options.topology_overlay,
        experimental_model_workload=options.experimental_model_workload,
    )


def _active_option_policies(path: Sequence[CommandNode], rest: Sequence[str]) -> tuple[str, ...]:
    policies = []
    separator = rest.index("--") if "--" in rest else len(rest)
    policy_args = rest[:separator]
    for option in COMMAND_TREE.global_options + tuple(
        option for item in path for option in item.options
    ):
        if option.output_policy is None:
            continue
        if any(token == flag or token.startswith(f"{flag}=") for token in policy_args for flag in option.flags):
            policies.append(option.output_policy)
    return tuple(policies)


def command_policy(path: Sequence[CommandNode], argv: Sequence[str]):
    """Return the reusable output policy for a resolved declarative command."""
    return classify_command_policy(
        path[-1].output_policy,
        option_policies=_active_option_policies(path, argv),
    )


def _remote_scalar(flag: str, value: str, schema: Mapping[str, object]) -> object:
    schema_type = schema.get("type")
    types = schema_type if isinstance(schema_type, list) else [schema_type]
    try:
        if "integer" in types:
            return int(value)
        if "number" in types:
            return float(value)
    except ValueError:
        raise UsageError(f"{flag} requires a numeric value") from None
    return value


def _remote_arguments(
    node: CommandNode,
    rest: Sequence[str],
    *,
    confirmed: bool,
) -> dict[str, object]:
    """Parse CLI arguments through the declared MCP schema without dispatch."""
    remote = node.remote_operation
    if remote is None or remote.mode != "tool" or remote.tool is None:
        raise UsageError("command has no typed controller operation")
    from . import mcp

    tool = mcp.TOOLS.get(remote.tool)
    if tool is None:
        raise UsageError(f"declared controller tool {remote.tool!r} is unavailable")
    schema = tool["inputSchema"]
    properties = schema.get("properties", {})
    arguments = dict(remote.fixed_arguments)
    positional: list[str] = []
    index = 0
    after_separator = False
    while index < len(rest):
        token = rest[index]
        if token == "--":
            after_separator = True
            index += 1
            continue
        if after_separator or not token.startswith("-"):
            positional.append(token)
            index += 1
            continue
        flag, separator, inline = token.partition("=")
        field = flag[2:].replace("-", "_") if flag.startswith("--") else ""
        field_schema = properties.get(field)
        if (
            not field
            or not isinstance(field_schema, Mapping)
            or (remote.allowed_arguments and field not in remote.allowed_arguments)
        ):
            raise UsageError(
                f"{flag} is not supported for remote {node.name}; use focused help"
            )
        if field in arguments:
            raise UsageError(f"{flag} is fixed by the canonical command path")
        schema_type = field_schema.get("type")
        types = schema_type if isinstance(schema_type, list) else [schema_type]
        if "boolean" in types:
            if separator:
                raise UsageError(f"{flag} does not accept a value")
            arguments[field] = True
            index += 1
            continue
        if separator:
            raw_value = inline
        else:
            index += 1
            if index >= len(rest) or rest[index].startswith("-"):
                raise UsageError(f"{flag} requires a value")
            raw_value = rest[index]
        if not raw_value:
            raise UsageError(f"{flag} requires a value")
        if "array" in types:
            item_schema = field_schema.get("items", {"type": "string"})
            if not isinstance(item_schema, Mapping):
                raise UsageError(f"{flag} has an invalid remote schema")
            values = arguments.setdefault(field, [])
            assert isinstance(values, list)
            values.append(_remote_scalar(flag, raw_value, item_schema))
        else:
            arguments[field] = _remote_scalar(flag, raw_value, field_schema)
        index += 1

    if positional:
        fields = remote.positional_arguments
        if not fields:
            raise UsageError("remote command does not accept positional arguments")
        if len(fields) == 1:
            field = fields[0]
            field_schema = properties.get(field, {})
            types = field_schema.get("type")
            types = types if isinstance(types, list) else [types]
            if "array" in types:
                arguments[field] = positional
            elif len(positional) == 1:
                arguments[field] = positional[0]
            else:
                raise UsageError(f"remote command accepts one positional {field}")
        elif len(positional) == len(fields):
            arguments.update(zip(fields, positional, strict=True))
        else:
            raise UsageError("remote command positional arguments are incomplete")
    if confirmed:
        arguments.update(remote.confirmed_arguments)
        if "confirm" in properties and "confirm" not in arguments:
            arguments["confirm"] = True
        if "dry_run" in properties and "dry_run" not in arguments:
            arguments["dry_run"] = False
    return mcp.validate_tool_arguments(remote.tool, arguments)


def _reconcile_remote_mutation(
    controller: ControllerTransport,
    key: str,
    original: AdapterTransportError,
) -> TransportResult:
    try:
        status_result = controller.operation_status(key)
    except AdapterTransportError as status_error:
        raise PartialResultError(
            "remote mutation outcome is ambiguous and status reconciliation failed",
            code="remote_mutation_ambiguous",
            details={
                "idempotency_key": key,
                "dispatch_error": original.as_dict(),
                "status_error": status_error.as_dict(),
            },
        ) from None
    status = status_result.data.get("status")
    if status == "succeeded":
        return status_result
    if status == "failed":
        raise OperatorError(
            "remote mutation failed after dispatch",
            code="remote_mutation_failed",
            details={"idempotency_key": key, "status": dict(status_result.data)},
        )
    raise PartialResultError(
        "remote mutation outcome requires operator reconciliation",
        code="remote_mutation_pending",
        details={
            "idempotency_key": key,
            "status": dict(status_result.data),
            "dispatch_error": original.as_dict(),
        },
    )


def _dispatch_remote_tool(
    path: Sequence[CommandNode],
    rest: Sequence[str],
    plan: ExecutionPlan,
    *,
    confirmed: bool,
    output_options: OutputOptions,
    execution_meta: dict[str, object] | None,
) -> int:
    node = path[-1]
    remote = node.remote_operation
    assert remote is not None and remote.mode == "tool" and remote.tool is not None
    if not plan.transport_endpoint or not plan.transport_auth_env:
        raise TransportError(
            "resolved controller transport is missing endpoint or token configuration",
            code="controller_transport_incomplete",
        )
    arguments = _remote_arguments(node, rest, confirmed=confirmed)
    controller = ControllerTransport(
        plan.transport_endpoint,
        auth_env=plan.transport_auth_env,
        allowed_operations=plan.transport_allowed_operations,
    )
    operation = Operation(plan.command.name, arguments, tool_name=remote.tool)
    idempotency_key = (
        "cli-" + uuid.uuid4().hex
        if confirmed and node.mutation_class == "mutate"
        else None
    )
    try:
        result = execute_plan(
            plan,
            operation,
            controller=controller,
            idempotency_key=idempotency_key,
        )
    except AdapterTransportError as exc:
        if idempotency_key is not None and exc.may_have_executed:
            result = _reconcile_remote_mutation(controller, idempotency_key, exc)
        else:
            raise TransportError(str(exc), code=exc.code, details=exc.as_dict()) from None
    data = result.as_dict()
    if execution_meta is not None:
        execution_meta["data"] = data
    if not output_options.json_mode:
        rendered = render_human(
            success_envelope(_command_name(path), plan, data, warnings=plan.warnings),
            options=output_options,
        )
        if rendered.stdout:
            print(rendered.stdout, end="")
        if rendered.stderr:
            print(rendered.stderr, end="", file=sys.stderr)
    return 0


def _requires_confirmation(node: CommandNode, policy_args: Sequence[str]) -> bool:
    has_conditional_gate = any(option.requires_confirmation for option in node.options)
    mutation_gate = node.mutation_class == "mutate" and not has_conditional_gate and any(
        "--confirm" in option.flags for option in node.options
    )
    option_gate = any(
        option.requires_confirmation
        and any(
            token == flag or token.startswith(f"{flag}=")
            for token in policy_args
            for flag in option.flags
        )
        for option in node.options
    )
    return mutation_gate or option_gate


def _confirm(
    path: Sequence[CommandNode], rest: Sequence[str], *, json_mode: bool
) -> tuple[tuple[str, ...], bool]:
    node = path[-1]
    declares_confirmation = any("--confirm" in option.flags for option in node.options)
    separator = rest.index("--") if "--" in rest else len(rest)
    policy_args = rest[:separator]
    leaf_args = rest[separator:]
    malformed = next((token for token in policy_args if token.startswith("--confirm=")), None)
    if malformed is not None and declares_confirmation:
        raise UsageError("--confirm does not accept a value")
    explicit = declares_confirmation and "--confirm" in policy_args
    forwarded = tuple(
        token for token in policy_args if not (declares_confirmation and token == "--confirm")
    ) + tuple(leaf_args)
    if "--help" in leaf_args or "-h" in leaf_args:
        return forwarded, False
    if explicit:
        return forwarded, True
    if "--dry-run" in policy_args:
        return forwarded, False
    if not _requires_confirmation(node, policy_args):
        return forwarded, False
    command = _command_name(path)
    next_action = f"rerun with --confirm: anvil-serving {command} --confirm"
    if json_mode or not sys.stdin.isatty():
        raise SafetyError(
            f"confirmation required; {next_action}",
            code="confirmation_required",
            details={"next_action": next_action},
        )
    try:
        answer = input(f"Confirm `anvil-serving {command}`? [y/N] ").strip().lower()
    except EOFError:
        raise SafetyError(
            f"confirmation input unavailable; {next_action}",
            code="confirmation_unavailable",
            details={"next_action": next_action},
        ) from None
    if answer not in {"y", "yes"}:
        raise SafetyError(
            f"confirmation declined; {next_action}",
            code="confirmation_declined",
            details={"next_action": next_action},
        )
    return forwarded, True


def _dispatch(
    path: Sequence[CommandNode],
    rest: Sequence[str],
    *,
    output_options: OutputOptions,
    execution_meta: dict[str, object] | None = None,
) -> int:
    node = path[-1]
    separator = rest.index("--") if "--" in rest else len(rest)
    help_requested = any(token in {"-h", "--help"} for token in rest[:separator])
    if help_requested and node.children:
        _print_focused_help(path)
        return 0
    removed = _tombstone(path, rest)
    if removed is not None:
        return _refuse_tombstone(*removed)
    if help_requested and node.handler is not None and not node.children:
        if _print_leaf_help(path):
            return 0
    if help_requested or (
        node.children and not rest and node.handler is None
    ):
        _print_focused_help(path)
        return 0
    if node.handler is None:
        _print_focused_help(path)
        return 0
    try:
        policy = command_policy(path, rest)
        enforce_command_policy(policy, json_mode=output_options.json_mode)
        rest, confirmed = _confirm(path, rest, json_mode=output_options.json_mode)
        resolution_options, rest = _extract_resolution_options(rest)
        plan = _resolve_dispatch_plan(path, resolution_options)
        if plan is not None and execution_meta is not None:
            execution_meta["plan"] = plan
            execution_meta["warnings"] = tuple(plan.warnings)
        controller_probe = (
            plan is not None
            and plan.command.name == "controller-status"
            and plan.transport in {"local", "controller"}
        )
        if controller_probe:
            assert plan is not None
            if any(
                token == "--url" or token.startswith("--url=")
                for token in rest
            ):
                raise UsageError(
                    "controller status --url cannot be combined with topology resolution"
                )
            endpoint = plan.transport_endpoint or plan.resource_endpoint
            if not endpoint:
                raise UsageError("resolved controller status has no controller endpoint")
            rest = (*rest, "--url", endpoint)
            if plan.transport_auth_env:
                rest = (*rest, "--auth-token-env", plan.transport_auth_env)
        elif (
            plan is not None
            and plan.transport == "controller"
            and node.remote_operation is not None
            and node.remote_operation.mode == "tool"
        ):
            return _dispatch_remote_tool(
                path,
                rest,
                plan,
                confirmed=confirmed,
                output_options=output_options,
                execution_meta=execution_meta,
            )
        elif plan is not None and plan.transport != "local":
            raise TransportError(
                "remote CLI dispatch is not implemented; use the MCP/controller "
                "operation surface for non-local execution",
                code="remote_cli_dispatch_unavailable",
                details={"transport": plan.transport},
            )
    except OperatorError as exc:
        if execution_meta is not None:
            execution_meta["error"] = exc
        print(f"anvil-serving: {exc}", file=sys.stderr)
        return exc.exit_code
    except _ResolutionOptionError as exc:
        print(f"anvil-serving: {exc}", file=sys.stderr)
        return 2
    except TopologyValidationError as exc:
        print(f"anvil-serving: invalid topology: {exc}", file=sys.stderr)
        return 2
    except TargetResolutionError as exc:
        print(f"anvil-serving: {exc}", file=sys.stderr)
        return exc.exit_code
    if plan is not None:
        if not output_options.json_mode:
            for warning in plan.warnings:
                print(warning, file=sys.stderr)
            if controller_probe:
                rendered = render_human(
                    success_envelope(_command_name(path), plan, None),
                    options=output_options,
                )
                if rendered.stdout:
                    print(rendered.stdout, end="")
    handler = node.handler.resolve()
    prefix = _handler_argv(path)
    with guard.confirmation_scope(confirmed):
        result = handler([*prefix, *rest])
    return 0 if result is None else int(result)


def _main(
    argv: Sequence[str],
    *,
    output_options: OutputOptions | None = None,
    execution_meta: dict[str, object] | None = None,
) -> int:
    output_options = output_options or OutputOptions()
    version_error = _check_python_version()
    if version_error:
        print(version_error, file=sys.stderr)
        return 1
    if not argv or argv[0] in ("-h", "--help"):
        _print_help()
        return 0
    if argv[0] in ("-V", "--version"):
        print("anvil-serving %s" % _installed_version())
        return 0
    path, rest, unknown, siblings = _resolve(argv)
    if unknown is not None:
        return _unknown_command(unknown, path, siblings)
    if not path:
        _print_help()
        return 0
    return _dispatch(
        path,
        rest,
        output_options=output_options,
        execution_meta=execution_meta,
    )


def _error_for_exit(rc: int, message: str) -> OperatorError:
    error_type = {
        2: UsageError,
        3: SafetyError,
        4: TransportError,
        5: PartialResultError,
    }.get(rc, OperatorError)
    return error_type(message.strip() or "command failed")


def _json_envelope(argv: Sequence[str], options: OutputOptions) -> int:
    """Run the dispatcher and emit its text output as one JSON result envelope."""
    path, rest, unknown, _siblings = _resolve(argv)
    if unknown is None and path:
        removed = _tombstone(path, rest)
        if removed is not None:
            label, tombstone = removed
            print(render_json(error_envelope(
                " ".join(argv),
                None,
                UsageError(
                    _tombstone_message(label, tombstone),
                    code="removed_command",
                    details={
                        "replacement": tombstone.replacement,
                        "docs_anchor": tombstone.docs_anchor,
                    },
                ),
            )))
            return 2
    stdout, stderr = io.StringIO(), io.StringIO()
    execution_meta: dict[str, object] = {}
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = _main(argv, output_options=options, execution_meta=execution_meta)
    except SystemExit as exc:
        rc = int(exc.code or 0)
    command = " ".join(argv)
    context = execution_meta.get("plan")
    warnings = list(execution_meta.get("warnings", ()))
    warnings.extend(line for line in stderr.getvalue().splitlines() if line)
    if rc == 0:
        data = execution_meta.get("data", stdout.getvalue())
        envelope = success_envelope(command, context, data, warnings=warnings)
    else:
        error = execution_meta.get("error")
        if not isinstance(error, OperatorError):
            error = _error_for_exit(rc, stderr.getvalue())
        envelope = error_envelope(
            command,
            context,
            error,
            warnings=execution_meta.get("warnings", ()),
        )
    print(render_json(envelope))
    return rc


def _extract_global_options(argv: Sequence[str]) -> tuple[OutputOptions, tuple[str, ...]]:
    json_mode = quiet = verbose = False
    forwarded = []
    after_separator = False
    for token in argv:
        if token == "--":
            after_separator = True
        if not after_separator and token in {"--json", "--quiet", "--verbose"}:
            json_mode = json_mode or token == "--json"
            quiet = quiet or token == "--quiet"
            verbose = verbose or token == "--verbose"
            continue
        forwarded.append(token)
    return OutputOptions(json_mode=json_mode, quiet=quiet, verbose=verbose), tuple(forwarded)


def _move_leading_resolution_options(argv: Sequence[str]) -> tuple[str, ...]:
    """Allow dispatcher-owned value options before the command path."""
    leading = []
    index = 0
    while index < len(argv):
        token = argv[index]
        flag = token.partition("=")[0]
        if flag in _RESOLUTION_VALUE_OPTIONS:
            leading.append(token)
            if "=" not in token and index + 1 < len(argv):
                index += 1
                leading.append(argv[index])
            index += 1
            continue
        if token == "--experimental-model-workload" or token.startswith(
            "--experimental-model-workload="
        ):
            leading.append(token)
            index += 1
            continue
        break
    return (*argv[index:], *leading)


def main(argv=None):
    argv = tuple(sys.argv[1:] if argv is None else argv)
    json_requested = "--json" in argv
    try:
        options, forwarded = _extract_global_options(argv)
        forwarded = _move_leading_resolution_options(forwarded)
    except UsageError as exc:
        if json_requested:
            print(render_json(error_envelope(" ".join(argv), None, exc)))
        else:
            print(f"anvil-serving: {exc}", file=sys.stderr)
        return EXIT_CODES["usage"]
    if options.json_mode:
        return _json_envelope(forwarded, options)
    if options.quiet:
        with contextlib.redirect_stdout(io.StringIO()):
            return _main(forwarded, output_options=options)
    return _main(forwarded, output_options=options)


if __name__ == "__main__":
    raise SystemExit(main())
