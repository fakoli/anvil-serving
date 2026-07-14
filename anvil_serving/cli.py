"""Tree-driven command dispatcher for ``anvil-serving``."""
from __future__ import annotations

import contextlib
import difflib
import io
import os
import re
import shutil
import sys
import textwrap
import uuid
from dataclasses import dataclass, replace
from importlib import metadata as importlib_metadata
from collections.abc import Mapping, Sequence

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
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    ControllerTransport,
    Operation,
    SSHRecoveryTransport,
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
    "anvil_serving.benchmark_evidence": "anvil-serving eval benchmark evidence",
    "anvil_serving.calibrate": "anvil-serving eval calibrate",
    "anvil_serving.controller": "anvil-serving controller",
    "anvil_serving.collectors": "anvil-serving collectors",
    "anvil_serving.doctor": "anvil-serving doctor",
    "anvil_serving.edge": "anvil-serving edge",
    "anvil_serving.eval": "anvil-serving eval",
    "anvil_serving.external_benchmarks.cli": "anvil-serving eval benchmark external",
    "anvil_serving.gpus": "anvil-serving host gpus",
    "anvil_serving.gpu_sharing": "anvil-serving host gpu-sharing inspect",
    "anvil_serving.harness": "anvil-serving harness",
    "anvil_serving.host": "anvil-serving host",
    "anvil_serving.init": "anvil-serving init",
    "anvil_serving.mcp": "anvil-serving mcp serve",
    "anvil_serving.models": "anvil-serving models",
    "anvil_serving.multiplexer": "anvil-serving serves multiplex",
    "anvil_serving.preflight": "anvil-serving eval preflight",
    "anvil_serving.profile": "anvil-serving eval usage",
    "anvil_serving.router.serve": "anvil-serving router run",
    "anvil_serving.router_endpoint": "anvil-serving router endpoint",
    "anvil_serving.router_manage": "anvil-serving router",
    "anvil_serving.serves": "anvil-serving serves",
    "anvil_serving.topology_cli": "anvil-serving topology",
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
    allow_ssh_fallback: bool = False
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
        ) or self.transport != "auto" or self.allow_ssh_fallback or self.experimental_model_workload


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


def _walk_nodes(nodes: Sequence[CommandNode]):
    for node in nodes:
        yield node
        yield from _walk_nodes(node.children)


def _find(nodes: Sequence[CommandNode], name: str) -> CommandNode | None:
    return next((node for node in nodes if node.name == name), None)


def _command_name(path: Sequence[CommandNode]) -> str:
    return " ".join(node.name for node in path)


def _help_width() -> int:
    """Return one stable, bounded width for every human help surface."""
    return min(
        100,
        max(60, shutil.get_terminal_size(fallback=(100, 24)).columns),
    )


def _print_help_table(
    rows: Sequence[tuple[str, str]], *, minimum_label_width: int = 15
) -> None:
    """Render aligned help rows while wrapping descriptions for the terminal."""
    if not rows:
        return
    help_width = _help_width()
    label_width = max(minimum_label_width, *(len(label) for label, _summary in rows))
    label_width = min(label_width, max(minimum_label_width, help_width // 2))
    for label, summary in rows:
        prefix = "  %-*s " % (label_width, label)
        print(
            textwrap.fill(
                summary,
                width=help_width,
                initial_indent=prefix,
                subsequent_indent=" " * len(prefix),
                break_long_words=False,
                break_on_hyphens=False,
            )
        )


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
    print(
        textwrap.fill(
            "anvil-serving - quality-gated local-model router and serving workbench",
            width=_help_width(),
            break_long_words=False,
            break_on_hyphens=False,
        )
    )
    print()
    print("Usage:")
    print("  anvil-serving <command> [options]")
    print("  anvil-serving <command> --help")
    print("  anvil-serving --version")
    print()
    print("Global options:")
    global_rows = [
        ("--command-manifest", "Print the machine-readable command manifest and exit."),
        ("-V, --version", "Print the installed version and exit."),
    ]
    global_rows.extend(
        (", ".join(option.flags), option.summary)
        for option in COMMAND_TREE.global_options
    )
    _print_help_table(global_rows, minimum_label_width=20)
    print()
    root_nodes = _visible(COMMAND_TREE.nodes)
    groups = list(_GROUP_ORDER)
    groups.extend(sorted({node.group for node in root_nodes if node.group and node.group not in groups}))
    for group in groups:
        members = [node for node in root_nodes if node.group == group]
        if not members:
            continue
        print("%s:" % group)
        _print_help_table(
            [(node.name, node.summary) for node in members],
            minimum_label_width=15,
        )
    print()
    print("Examples:")
    print("  anvil-serving router run --config configs/example.toml")
    print("  anvil-serving serves status")
    print("  anvil-serving eval preflight --tier heavy --dry-run")
    print("  anvil-serving eval benchmark external list")
    print("  anvil-serving mcp tools")
    print()
    print("Docs: %s" % _DOCS_URL)


def _print_focused_help(path: Sequence[CommandNode]) -> None:
    node = path[-1]
    command = _command_name(path)
    print(
        textwrap.fill(
            "anvil-serving %s - %s" % (command, node.summary),
            width=_help_width(),
            subsequent_indent="  ",
            break_long_words=False,
            break_on_hyphens=False,
        )
    )
    print()
    print("Usage:")
    suffix = " <action>" if _visible(node.children) else ""
    print(
        textwrap.fill(
            "anvil-serving %s%s [options]" % (command, suffix),
            width=_help_width(),
            initial_indent="  ",
            subsequent_indent="    ",
            break_long_words=False,
            break_on_hyphens=False,
        )
    )
    children = _visible(node.children)
    if children:
        print()
        print("Actions:")
        _print_help_table(
            [(child.name, child.summary) for child in children],
            minimum_label_width=15,
        )
    supports_resolution = node.execution_policy == "resource-owner" or any(
        descendant.execution_policy == "resource-owner"
        for descendant in _walk_nodes(node.children)
    )
    global_options = COMMAND_TREE.global_options
    if not supports_resolution:
        resolution_flags = {
            *_RESOLUTION_VALUE_OPTIONS,
            "--experimental-model-workload",
            "--allow-ssh-fallback",
        }
        global_options = tuple(
            option
            for option in global_options
            if not resolution_flags.intersection(option.flags)
        )
    options = global_options + node.options
    if options:
        print()
        print("Options:")
        rendered_options = []
        for option in options:
            label = ", ".join(option.flags)
            if option.value_name is not None:
                label += " " + option.value_name
            rendered_options.append((label, option.summary))
        _print_help_table(rendered_options, minimum_label_width=20)
    print()
    print("Docs: %s" % node.docs_anchor)


def _handler_argv(path: Sequence[CommandNode]) -> tuple[str, ...]:
    node = path[-1]
    assert node.handler is not None
    if node.handler.argv_prefix is not None:
        return node.handler.argv_prefix
    return tuple(item.name for item in path[1:])


def _hidden_leaf_help_flags(path: Sequence[CommandNode]) -> frozenset[str]:
    """Options owned by the dispatcher or removed from the public interface."""
    flags = {
        flag
        for option in COMMAND_TREE.global_options
        for flag in option.flags
    }
    flags.update(
        flag
        for item in path
        for option in item.options
        if option.tombstone is not None
        for flag in option.flags
    )
    return frozenset(flags)


def _strip_optional_usage_flag(line: str, flag: str) -> str:
    """Remove one dispatcher-owned optional from an argparse usage line."""
    optional_group = re.compile(r"\[([^\[\]]*)\]")

    def strip_alternative(match: re.Match[str]) -> str:
        alternatives = re.split(r"\s+\|\s+", match.group(1))
        retained = [
            alternative
            for alternative in alternatives
            if not re.match(
                r"^" + re.escape(flag) + r"(?:\s|$)", alternative.strip()
            )
        ]
        if len(retained) == len(alternatives):
            return match.group(0)
        if not retained:
            return ""
        return "[" + " | ".join(retained) + "]"

    return re.sub(r"\s+", " ", optional_group.sub(strip_alternative, line)).rstrip()


def _normalized_leaf_sections(
    rendered: str, *, hidden_flags: frozenset[str]
) -> tuple[list[str], list[str]]:
    """Split argparse help into canonical usage and local argument sections."""
    lines = rendered.splitlines()
    if not lines or not lines[0].casefold().startswith("usage:"):
        return [], lines

    usage = [lines[0].split(":", 1)[1].strip()]
    index = 1
    while index < len(lines) and lines[index].strip():
        usage.append(lines[index].strip())
        index += 1
    for flag in sorted(hidden_flags, key=len, reverse=True):
        usage = [_strip_optional_usage_flag(line, flag).strip() for line in usage]
    usage = [line for line in usage if line]
    if usage:
        usage[-1] = usage[-1] + " [global options]"

    body = lines[index:]
    while body and not body[0].strip():
        body.pop(0)

    section_names = {
        "options:",
        "optional arguments:",
        "positional arguments:",
    }
    section_start = next(
        (
            offset
            for offset, line in enumerate(body)
            if line.strip().casefold() in section_names
        ),
        len(body),
    )
    body = body[section_start:]
    examples_start = next(
        (
            offset
            for offset, line in enumerate(body)
            if line.strip().casefold() == "examples:"
        ),
        len(body),
    )
    body = body[:examples_start]

    filtered: list[str] = []
    skipping = False
    for line in body:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        option_line = indent <= 2 and stripped.startswith("-")
        if option_line:
            skipping = any(
                re.match(r"^" + re.escape(flag) + r"(?:[ ,]|$)", stripped)
                for flag in hidden_flags
            )
        elif skipping and (not stripped or indent <= 2):
            skipping = False
        if not skipping:
            normalized = {
                "positional arguments:": "Arguments:",
                "optional arguments:": "Options:",
                "options:": "Options:",
            }.get(stripped.casefold())
            filtered.append(normalized if normalized is not None else line)
    while filtered and not filtered[-1].strip():
        filtered.pop()
    return usage, filtered


def _print_reviewed_leaf_help(path: Sequence[CommandNode], rendered: str) -> None:
    """Render a reviewed leaf with stable navigation and human-first sections."""
    node = path[-1]
    command = _command_name(path)
    help_width = _help_width()
    usage, local_sections = _normalized_leaf_sections(
        rendered,
        hidden_flags=_hidden_leaf_help_flags(path),
    )

    print("anvil-serving %s" % command)
    print(
        textwrap.fill(
            node.summary,
            width=help_width,
            break_long_words=False,
            break_on_hyphens=False,
        )
    )
    if usage:
        print("\nUsage:")
        for line in usage:
            print(
                textwrap.fill(
                    line,
                    width=help_width,
                    initial_indent="  ",
                    subsequent_indent="    ",
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )
    print("\nExamples:")
    for example in node.examples:
        print(
            textwrap.fill(
                example.invocation,
                width=help_width,
                initial_indent="  ",
                subsequent_indent="    ",
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
        print(
            textwrap.fill(
                example.summary,
                width=help_width,
                initial_indent="    ",
                subsequent_indent="    ",
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    if node.configuration_notes:
        print("\nConfiguration:")
        for note in node.configuration_notes:
            print(
                textwrap.fill(
                    note,
                    width=help_width,
                    initial_indent="  ",
                    subsequent_indent="  ",
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )
    if node.behavior_notes:
        print("\nBehavior:")
        for note in node.behavior_notes:
            print(
                textwrap.fill(
                    note,
                    width=help_width,
                    initial_indent="  ",
                    subsequent_indent="  ",
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )
    if local_sections:
        print()
        for line in local_sections:
            stripped = line.strip()
            if not stripped or len(line) <= help_width:
                print(line)
                continue
            indent = line[: len(line) - len(line.lstrip())]
            print(
                textwrap.fill(
                    stripped,
                    width=help_width,
                    initial_indent=indent,
                    subsequent_indent=indent + "  ",
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )

    global_options = COMMAND_TREE.global_options
    if node.execution_policy != "resource-owner":
        resolution_flags = {
            *_RESOLUTION_VALUE_OPTIONS,
            "--experimental-model-workload",
            "--allow-ssh-fallback",
        }
        global_options = tuple(
            option
            for option in global_options
            if not resolution_flags.intersection(option.flags)
        )
    dispatcher_options = tuple(
        option
        for option in node.options
        if "--confirm" in option.flags and option.tombstone is None
    )
    print("\nGlobal options:")
    rendered_global_options = []
    for option in (*global_options, *dispatcher_options):
        label = ", ".join(option.flags)
        if option.value_name:
            label += " " + option.value_name
        rendered_global_options.append((label, option.summary))
    _print_help_table(rendered_global_options, minimum_label_width=20)
    print("\nDocs: %s" % node.docs_anchor)


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
    if node.examples:
        _print_reviewed_leaf_help(path, rendered)
        return True
    print(rendered, end="" if rendered.endswith("\n") else "\n")
    dispatcher_options = [
        option
        for option in node.options
        if "--confirm" in option.flags
    ]
    global_options = COMMAND_TREE.global_options
    if node.execution_policy != "resource-owner":
        resolution_flags = {
            *_RESOLUTION_VALUE_OPTIONS,
            "--experimental-model-workload",
            "--allow-ssh-fallback",
        }
        global_options = tuple(
            option
            for option in global_options
            if not resolution_flags.intersection(option.flags)
        )
    print("\nDispatcher options:")
    rendered_dispatcher_options = []
    for option in (*global_options, *dispatcher_options):
        label = ", ".join(option.flags)
        if option.value_name:
            label += " " + option.value_name
        rendered_dispatcher_options.append((label, option.summary))
    _print_help_table(rendered_dispatcher_options, minimum_label_width=20)
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
    help_path = " ".join(node.name for node in path)
    help_command = "anvil-serving%s --help" % ((" " + help_path) if help_path else "")
    print("Run '%s' to see available %s." % (
        help_command,
        "actions" if path else "commands",
    ), file=sys.stderr)
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
        "allow_ssh_fallback": False,
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
        if token == "--allow-ssh-fallback":
            values["allow_ssh_fallback"] = True
            index += 1
            continue
        if token.startswith("--allow-ssh-fallback="):
            raise _ResolutionOptionError("--allow-ssh-fallback does not accept a value")

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
        execution_host_os=node.execution_host_os,
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
    if options.allow_ssh_fallback and not command.recovery_capable:
        raise _ResolutionOptionError(
            f"{_command_name(path)} is not recovery-capable; drop --allow-ssh-fallback"
        )
    topology = load_topology(options.topology, options.topology_overlay)
    plan = resolve_execution_plan(
        topology,
        command,
        target=options.target,
        transport=options.transport,
        command_host=options.command_host,
        command_runtime=options.command_runtime,
        overlay=options.topology_overlay,
        experimental_model_workload=options.experimental_model_workload,
    )
    for role in path[-1].coowned_resource_roles:
        related = resolve_execution_plan(
            topology,
            replace(command, resource_role=role),
            target=options.target,
            transport=options.transport,
            command_host=options.command_host,
            command_runtime=options.command_runtime,
            overlay=options.topology_overlay,
            experimental_model_workload=options.experimental_model_workload,
        )
        primary_identity = (
            plan.resource_host.id,
            plan.resource_runtime.id,
            plan.execution_host.id,
            plan.execution_runtime.id,
        )
        related_identity = (
            related.resource_host.id,
            related.resource_runtime.id,
            related.execution_host.id,
            related.execution_runtime.id,
        )
        if related_identity != primary_identity:
            raise SafetyError(
                "operation requires co-owned resources on one execution host",
                code="split_resource_ownership",
                details={
                    "primary_role": command.resource_role,
                    "primary_host": plan.resource_host.id,
                    "related_role": role,
                    "related_host": related.resource_host.id,
                },
            )
    return plan


def _resolution_options_argv(options: _ResolutionOptions) -> tuple[str, ...]:
    """Reconstruct dispatcher-owned options for an explicitly opted-in handler."""
    if not options.requested:
        return ()
    values = (
        ("--topology", options.topology),
        ("--topology-overlay", options.topology_overlay),
        ("--command-host", options.command_host),
        ("--command-runtime", options.command_runtime),
        ("--target", options.target),
        ("--transport", options.transport),
    )
    argv = [token for flag, value in values if value is not None for token in (flag, value)]
    if options.experimental_model_workload:
        argv.append("--experimental-model-workload")
    return tuple(argv)


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
    allow_ssh_fallback: bool = False,
) -> int:
    node = path[-1]
    remote = node.remote_operation
    assert remote is not None and remote.mode == "tool" and remote.tool is not None
    arguments = _remote_arguments(node, rest, confirmed=confirmed)
    if plan.transport == "ssh" and arguments.get("dry_run") is True:
        data = {
            "operation": plan.command.name,
            "transport": "ssh",
            "data": {"dry_run": True, "adapter": plan.command.name},
            "response_bytes": 0,
        }
        if execution_meta is not None:
            execution_meta["data"] = data
        if not output_options.json_mode:
            rendered = render_human(
                success_envelope(_command_name(path), plan, data, warnings=plan.warnings),
                options=output_options,
            )
            if rendered.stdout:
                print(rendered.stdout, end="")
        return 0
    controller = None
    if plan.transport == "controller":
        if not plan.transport_endpoint or not plan.transport_auth_env:
            raise TransportError(
                "resolved controller transport is missing endpoint or token configuration",
                code="controller_transport_incomplete",
            )
        controller = ControllerTransport(
            plan.transport_endpoint,
            auth_env=plan.transport_auth_env,
            allowed_operations=plan.transport_allowed_operations,
            timeout_seconds=_remote_transport_timeout(arguments, tool_name=remote.tool),
        )
    ssh = _ssh_recovery_transport(plan) if plan.transport == "ssh" or allow_ssh_fallback else None
    operation = Operation(plan.command.name, arguments, tool_name=remote.tool)
    ssh_operation = Operation(plan.command.name, {})
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
            ssh=ssh,
            ssh_operation=ssh_operation,
            allow_ssh_fallback=allow_ssh_fallback,
            idempotency_key=idempotency_key,
        )
    except AdapterTransportError as exc:
        if execution_meta is not None and exc.code.startswith("ssh_"):
            context = plan.as_dict()
            context["transport"] = "ssh"
            execution_meta["plan"] = context
        if (
            plan.transport == "controller"
            and controller is not None
            and idempotency_key is not None
            and exc.may_have_executed
            and not exc.code.startswith("ssh_")
        ):
            result = _reconcile_remote_mutation(controller, idempotency_key, exc)
        else:
            raise TransportError(str(exc), code=exc.code, details=exc.as_dict()) from None
    data = result.as_dict()
    result_context: ExecutionPlan | Mapping[str, object] = plan
    if result.transport != plan.transport:
        context = plan.as_dict()
        context["transport"] = result.transport
        result_context = context
    if execution_meta is not None:
        execution_meta["data"] = data
        execution_meta["plan"] = result_context
    if not output_options.json_mode:
        rendered = render_human(
            success_envelope(_command_name(path), result_context, data, warnings=plan.warnings),
            options=output_options,
        )
        if rendered.stdout:
            print(rendered.stdout, end="")
        if rendered.stderr:
            print(rendered.stderr, end="", file=sys.stderr)
    return 0


def _remote_transport_timeout(
    arguments: Mapping[str, object], *, tool_name: str | None = None
) -> float:
    """Keep the HTTP deadline outside the bounded remote workload deadline."""
    workload_timeout = arguments.get("timeout_seconds")
    if isinstance(workload_timeout, (int, float)) and not isinstance(workload_timeout, bool):
        if arguments.get("dry_run") is True:
            return 60.0
        multiplier = 1
        if tool_name == "preflight_probe":
            checks = arguments.get("checks", "smoke,json,needle,tools")
            if isinstance(checks, str):
                multiplier = max(1, len([item for item in checks.split(",") if item.strip()]))
        required = float(workload_timeout) * multiplier + 5.0
        if required > MAX_TIMEOUT_SECONDS:
            raise TransportError(
                "remote workload deadline exceeds %.0f seconds; reduce checks or "
                "timeout_seconds" % MAX_TIMEOUT_SECONDS,
                code="remote_timeout_exceeded",
            )
        return max(60.0, required)
    return max(60.0, DEFAULT_TIMEOUT_SECONDS)


def _ssh_recovery_transport(plan: ExecutionPlan) -> SSHRecoveryTransport:
    adapter = {
        "harness-restart-openclaw": (
            "anvil-serving", "harness", "restart", "openclaw", "--confirm",
        ),
        "host-restart-docker": (
            "anvil-serving", "host", "restart-docker", "--confirm",
        ),
        "host-reset-wsl": (
            "anvil-serving", "host", "reset-wsl", "--confirm",
        ),
    }.get(plan.command.name)
    if adapter is None:
        raise TransportError(
            "no fixed SSH recovery adapter is declared for this operation",
            code="ssh_operation_not_allowed",
        )
    selected = plan.transport == "ssh"
    endpoint = plan.transport_endpoint if selected else plan.recovery_transport_endpoint
    transport_id = plan.transport_id if selected else plan.recovery_transport_id
    fingerprint = (
        plan.transport_host_key_fingerprint if selected else plan.recovery_host_key_fingerprint
    )
    known_hosts = (
        plan.transport_known_hosts_path if selected else plan.recovery_known_hosts_path
    )
    identity_file = (os.environ.get("ANVIL_SSH_IDENTITY_FILE") or "").strip()
    if not endpoint or not fingerprint or not known_hosts:
        raise TransportError(
            "resolved SSH recovery transport is missing endpoint or host verification metadata",
            code="ssh_transport_incomplete",
        )
    if not identity_file:
        raise TransportError(
            "ANVIL_SSH_IDENTITY_FILE must name the private key for SSH recovery",
            code="ssh_identity_missing",
        )
    try:
        return SSHRecoveryTransport(
            endpoint,
            adapters={plan.command.name: adapter},
            known_hosts_path=os.path.expanduser(known_hosts),
            host_key_fingerprint=fingerprint,
            identity_file=os.path.expanduser(identity_file),
            transport_id=transport_id,
            timeout_seconds=60,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise TransportError(str(exc), code="ssh_transport_invalid") from None


def _policy_positionals(node: CommandNode, policy_args: Sequence[str]) -> tuple[str, ...]:
    """Return positional tokens after accounting for declared option values."""
    value_flags = {
        flag
        for option in (*COMMAND_TREE.global_options, *node.options)
        if option.value_name is not None
        for flag in option.flags
    }
    positionals = []
    consume_value = False
    for token in policy_args:
        if consume_value:
            consume_value = False
            continue
        if token == "--":
            break
        if token.startswith("-"):
            flag, separator, _value = token.partition("=")
            consume_value = not separator and flag in value_flags
            continue
        positionals.append(token)
    return tuple(positionals)


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
    positional_switch_gate = (
        node.name == "switch"
        and node.handler is not None
        and node.handler.module == "anvil_serving.serves"
        and len(_policy_positionals(node, policy_args)) >= 2
    )
    return mutation_gate or option_gate or positional_switch_gate


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
    next_action = "rerun the same command with --confirm"
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
    if node.children and node.handler is None:
        command = _command_name(path)
        actions = ", ".join(child.name for child in _visible(node.children))
        error = UsageError(
            f"action required before options; usage: anvil-serving {command} <action> [options]",
            code="missing_action",
            details={"command": command, "actions": actions, "unexpected": list(rest)},
        )
        if execution_meta is not None:
            execution_meta["error"] = error
        print(f"anvil-serving {command}: {error}", file=sys.stderr)
        return error.exit_code
    if node.handler is None:
        _print_focused_help(path)
        return 0
    resolution_options = _ResolutionOptions()
    try:
        policy = command_policy(path, rest)
        enforce_command_policy(policy, json_mode=output_options.json_mode)
        rest, confirmed = _confirm(path, rest, json_mode=output_options.json_mode)
        if path[0].name == "topology":
            from . import topology_cli

            data = topology_cli.run([*_handler_argv(path), *rest])
            if node.name == "resolve":
                context: Mapping[str, object] = data
            else:
                context = {
                    "command": f"topology-{node.name}",
                    "topology": data.get("topology"),
                    "overlay": data.get("overlay"),
                }
            if data.get("valid") is False:
                error = UsageError(
                    "topology validation failed",
                    code="invalid_topology",
                    details={"errors": data.get("errors", [])},
                )
                if execution_meta is not None:
                    execution_meta["plan"] = context
                    execution_meta["error"] = error
                if not output_options.json_mode:
                    rendered = render_human(
                        error_envelope(_command_name(path), context, error),
                        options=output_options,
                    )
                    if rendered.stderr:
                        print(rendered.stderr, end="", file=sys.stderr)
                return error.exit_code
            if execution_meta is not None:
                execution_meta["plan"] = context
                execution_meta["data"] = data
            if not output_options.json_mode:
                rendered = render_human(
                    success_envelope(_command_name(path), context, data),
                    options=output_options,
                )
                if rendered.stdout:
                    print(rendered.stdout, end="")
            return 0
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
            and plan.transport in {"controller", "ssh"}
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
                allow_ssh_fallback=resolution_options.allow_ssh_fallback,
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
    if node.handler.forward_resolution_options:
        rest = (*rest, *_resolution_options_argv(resolution_options))
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
        if len(argv) != 1:
            print("anvil-serving: --version does not accept command arguments", file=sys.stderr)
            return 2
        print("anvil-serving %s" % _installed_version())
        return 0
    if argv[0] == "--command-manifest":
        if len(argv) != 1:
            print("anvil-serving: --command-manifest does not accept command arguments", file=sys.stderr)
            return 2
        from .command_tree import render_manifest

        sys.stdout.write(render_manifest().decode("utf-8"))
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
        if (
            token == "--experimental-model-workload"
            or token.startswith("--experimental-model-workload=")
            or token == "--allow-ssh-fallback"
            or token.startswith("--allow-ssh-fallback=")
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
