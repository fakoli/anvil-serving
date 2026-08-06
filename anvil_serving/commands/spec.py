"""Core types and validation for the modular command registry.

This module is deliberately independent of the current root dispatcher.  It
defines the public v2 surface so dispatch and help derive from one validated
declaration.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import importlib
import json
from pathlib import Path
from typing import Callable, Iterable


MANIFEST_SCHEMA_VERSION = 5
MANIFEST_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "CLI-COMMAND-MANIFEST.json"
CLI_DOC = "docs/CLI.md"
ROUTER_DOC = "docs/cli/router.md"
SERVES_DOC = "docs/cli/serves.md"
MODELS_DOC = "docs/cli/models.md"
EVAL_DOC = "docs/cli/eval.md"
HOST_DOC = "docs/cli/host.md"
CONTROL_PLANE_DOC = "docs/cli/control-plane.md"
VOICE_CLI_DOC = "docs/cli/voice.md"
_MUTATION_CLASSES = frozenset({"read", "mutate", "process"})
_TRANSPORTS = frozenset({"local", "controller", "ssh"})
_EXECUTION_POLICIES = frozenset({"offline", "resource-owner"})
_OUTPUT_POLICIES = frozenset({"bounded", "foreground", "protocol", "follow"})
_REMOTE_MODES = frozenset({"tool", "controller-status", "mcp-bridge"})
_HOST_OSES = frozenset({"linux", "macos", "windows"})


class CommandTreeError(ValueError):
    """A command tree declaration is incomplete or internally inconsistent."""


@dataclass(frozen=True)
class HandlerRef:
    """A lazy, importable handler reference used by the future dispatcher."""

    module: str
    attribute: str = "main"
    argv_prefix: tuple[str, ...] | None = None
    forward_resolution_options: bool = False
    forward_confirm_flag: bool = False

    def __post_init__(self) -> None:
        if self.argv_prefix is not None:
            object.__setattr__(self, "argv_prefix", tuple(self.argv_prefix))

    @property
    def name(self) -> str:
        return f"{self.module}:{self.attribute}"

    def resolve(self) -> Callable[..., object]:
        try:
            target: object = importlib.import_module(self.module)
            for part in self.attribute.split("."):
                target = getattr(target, part)
        except (AttributeError, ImportError, ModuleNotFoundError) as exc:
            raise CommandTreeError(f"unresolved handler {self.name!r}") from exc
        if not callable(target):
            raise CommandTreeError(f"handler {self.name!r} is not callable")
        return target


@dataclass(frozen=True)
class CommandOption:
    """A visible CLI option."""

    flags: tuple[str, ...]
    summary: str
    value_name: str | None = None
    output_policy: str | None = None
    requires_confirmation: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "flags", tuple(self.flags))


@dataclass(frozen=True)
class RemoteOperation:
    """Typed controller behavior for one canonical command leaf."""

    mode: str = "tool"
    tool: str | None = None
    fixed_arguments: tuple[tuple[str, object], ...] = field(default_factory=tuple)
    confirmed_arguments: tuple[tuple[str, object], ...] = field(default_factory=tuple)
    allowed_arguments: tuple[str, ...] = field(default_factory=tuple)
    positional_arguments: tuple[str, ...] = field(default_factory=tuple)
    argument_aliases: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    max_response_bytes: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "fixed_arguments", tuple(self.fixed_arguments))
        object.__setattr__(self, "confirmed_arguments", tuple(self.confirmed_arguments))
        object.__setattr__(self, "allowed_arguments", tuple(self.allowed_arguments))
        object.__setattr__(self, "positional_arguments", tuple(self.positional_arguments))
        object.__setattr__(self, "argument_aliases", tuple(self.argument_aliases))


@dataclass(frozen=True)
class CommandNode:
    """One path segment in the public command tree."""

    name: str
    summary: str
    children: tuple["CommandNode", ...] = field(default_factory=tuple)
    options: tuple[CommandOption, ...] = field(default_factory=tuple)
    handler: HandlerRef | None = None
    resource_role: str | None = None
    coowned_resource_roles: tuple[str, ...] = field(default_factory=tuple)
    transports: tuple[str, ...] = field(default_factory=tuple)
    execution_runtime_roles: tuple[str, ...] = field(default_factory=tuple)
    execution_host_os: tuple[str, ...] = field(default_factory=tuple)
    mutation_class: str = "read"
    recovery_capable: bool = False
    gpu_role_required: bool = False
    execution_policy: str = "offline"
    output_policy: str = "bounded"
    docs_anchor: str = "docs/CLI.md"
    visible: bool = True
    group: str | None = None
    remote_operation: RemoteOperation | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "children", tuple(self.children))
        object.__setattr__(self, "options", tuple(self.options))
        object.__setattr__(self, "coowned_resource_roles", tuple(self.coowned_resource_roles))
        object.__setattr__(self, "transports", tuple(self.transports))
        object.__setattr__(self, "execution_runtime_roles", tuple(self.execution_runtime_roles))
        object.__setattr__(self, "execution_host_os", tuple(self.execution_host_os))


@dataclass(frozen=True)
class CommandTree:
    """The sole declarative source for the v2 CLI public surface."""

    nodes: tuple[CommandNode, ...]
    global_options: tuple[CommandOption, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "global_options", tuple(self.global_options))


def _deferred_handler(*_args: object, **_kwargs: object) -> None:
    """Marker handler for v2 paths whose concrete implementation lands later."""
    raise RuntimeError("this v2 command is not wired into the dispatcher yet")


def _option(
    *flags: str,
    summary: str,
    value_name: str | None = None,
    output_policy: str | None = None,
    requires_confirmation: bool = False,
) -> CommandOption:
    return CommandOption(
        flags=flags,
        summary=summary,
        value_name=value_name,
        output_policy=output_policy,
        requires_confirmation=requires_confirmation,
    )


def _handler(
    module: str,
    *,
    attribute: str = "main",
    argv_prefix: Iterable[str] | None = None,
    forward_resolution_options: bool = False,
    forward_confirm_flag: bool = False,
) -> HandlerRef:
    return HandlerRef(
        module,
        attribute=attribute,
        argv_prefix=None if argv_prefix is None else tuple(argv_prefix),
        forward_resolution_options=forward_resolution_options,
        forward_confirm_flag=forward_confirm_flag,
    )


def _future_handler() -> HandlerRef:
    return HandlerRef("anvil_serving.command_tree", "_deferred_handler")


def _remote(
    tool: str | None = None,
    *,
    mode: str = "tool",
    fixed: Iterable[tuple[str, object]] = (),
    confirmed: Iterable[tuple[str, object]] = (),
    allowed: Iterable[str] = (),
    positionals: Iterable[str] = (),
    aliases: Iterable[tuple[str, str]] = (),
    max_response_bytes: int | None = None,
) -> RemoteOperation:
    return RemoteOperation(
        mode=mode,
        tool=tool,
        fixed_arguments=tuple(fixed),
        confirmed_arguments=tuple(confirmed),
        allowed_arguments=tuple(allowed),
        positional_arguments=tuple(positionals),
        argument_aliases=tuple(aliases),
        max_response_bytes=max_response_bytes,
    )


def _node(
    name: str,
    summary: str,
    *,
    children: Iterable[CommandNode] = (),
    options: Iterable[CommandOption] = (),
    handler: HandlerRef | None = None,
    resource_role: str | None = None,
    coowned_resource_roles: Iterable[str] = (),
    transports: tuple[str, ...] = (),
    execution_runtime_roles: tuple[str, ...] = (),
    execution_host_os: tuple[str, ...] = (),
    mutation_class: str = "read",
    recovery_capable: bool = False,
    gpu_role_required: bool = False,
    execution_policy: str = "offline",
    output_policy: str = "bounded",
    docs_anchor: str = CLI_DOC,
    visible: bool = True,
    group: str | None = None,
    remote_operation: RemoteOperation | None = None,
) -> CommandNode:
    return CommandNode(
        name=name,
        summary=summary,
        children=tuple(children),
        options=tuple(options),
        handler=handler,
        resource_role=resource_role,
        coowned_resource_roles=tuple(coowned_resource_roles),
        transports=transports,
        execution_runtime_roles=execution_runtime_roles,
        execution_host_os=execution_host_os,
        mutation_class=mutation_class,
        recovery_capable=recovery_capable,
        gpu_role_required=gpu_role_required,
        execution_policy=execution_policy,
        output_policy=output_policy,
        docs_anchor=docs_anchor,
        visible=visible,
        group=group,
        remote_operation=remote_operation,
    )


def _resource_node(
    name: str,
    summary: str,
    module: str | None,
    *,
    role: str,
    coowned_roles: Iterable[str] = (),
    mutation: str = "read",
    recovery: bool = False,
    gpu: bool = False,
    options: Iterable[CommandOption] = (),
    argv_prefix: Iterable[str] | None = None,
    handler_attribute: str = "main",
    forward_resolution_options: bool = False,
    forward_confirm_flag: bool = False,
    output_policy: str = "bounded",
    docs_anchor: str = CLI_DOC,
    remote_operation: RemoteOperation | None = None,
    execution_runtime_roles: tuple[str, ...] = ("native", "docker"),
    execution_host_os: tuple[str, ...] = (),
    group: str | None = None,
) -> CommandNode:
    return _node(
        name,
        summary,
        handler=_handler(
            module,
            attribute=handler_attribute,
            argv_prefix=argv_prefix,
            forward_resolution_options=forward_resolution_options,
            forward_confirm_flag=forward_confirm_flag,
        )
        if module
        else _future_handler(),
        resource_role=role,
        coowned_resource_roles=coowned_roles,
        transports=(
            ("local", "controller", "ssh")
            if recovery and remote_operation is not None
            else ("local", "ssh")
            if recovery
            else ("local", "controller")
            if remote_operation is not None
            else ("local",)
        ),
        execution_runtime_roles=execution_runtime_roles,
        execution_host_os=execution_host_os,
        mutation_class=mutation,
        recovery_capable=recovery,
        gpu_role_required=gpu,
        execution_policy="resource-owner",
        output_policy=output_policy,
        options=options,
        docs_anchor=docs_anchor,
        remote_operation=remote_operation,
        group=group,
    )


GLOBAL_OPTIONS = (
    _option(
        "--topology",
        summary=(
            "Topology document used for target resolution "
            "(default: operator config home)."
        ),
        value_name="PATH",
    ),
    _option(
        "--topology-overlay",
        summary="Deployment overlay applied to the topology.",
        value_name="PATH",
    ),
    _option("--command-host", summary="Declared command host.", value_name="host:ID"),
    _option("--command-runtime", summary="Declared command runtime.", value_name="runtime:ID"),
    _option(
        "--target", summary="Explicit resource-owner target.", value_name="host:ID|host-role:ROLE"
    ),
    _option("--transport", summary="Execution transport.", value_name="auto|local|controller|ssh"),
    _option(
        "--allow-ssh-fallback",
        summary="Allow verified SSH recovery after a proven pre-dispatch controller failure.",
    ),
    _option(
        "--experimental-model-workload",
        summary="Allow a topology-permitted experimental model workload on a model-free host.",
    ),
    _option("--json", summary="Emit the machine-readable result envelope."),
    _option("--quiet", summary="Suppress nonessential human output."),
    _option("--verbose", summary="Include diagnostic human output."),
    _option("-h", "--help", summary="Show focused help and exit."),
)


def _inherit_docs_anchor(node: CommandNode, parent_anchor: str = CLI_DOC) -> CommandNode:
    """Give descendants without a specific reference the family page of their parent."""
    docs_anchor = parent_anchor if node.docs_anchor == CLI_DOC else node.docs_anchor
    return replace(
        node,
        docs_anchor=docs_anchor,
        children=tuple(_inherit_docs_anchor(child, docs_anchor) for child in node.children),
    )


def _default_tree() -> CommandTree:
    from .registry import COMMAND_TREE

    return COMMAND_TREE


def validate_command_tree(
    tree: CommandTree | None = None, *, resolve_handlers: bool = True
) -> None:
    """Raise ``CommandTreeError`` when a command declaration is invalid."""
    tree = tree or _default_tree()
    _validate_options(tree.global_options, "<global>")
    _validate_nodes(
        tree.nodes,
        (),
        inherited_flags=frozenset(flag for option in tree.global_options for flag in option.flags),
        resolve_handlers=resolve_handlers,
    )


def _validate_nodes(
    nodes: tuple[CommandNode, ...],
    parent: tuple[str, ...],
    *,
    inherited_flags: frozenset[str],
    resolve_handlers: bool,
) -> None:
    names: set[str] = set()
    for node in nodes:
        path = parent + (node.name,)
        label = " ".join(path)
        if not node.name or any(character.isspace() for character in node.name):
            raise CommandTreeError(f"invalid command path segment {node.name!r} at {label!r}")
        if node.name in names:
            raise CommandTreeError(f"duplicate command path {label!r}")
        names.add(node.name)
        if not node.summary:
            raise CommandTreeError(f"command {label!r} requires a summary")
        if not node.docs_anchor:
            raise CommandTreeError(f"command {label!r} requires a documentation anchor")
        if node.mutation_class not in _MUTATION_CLASSES:
            raise CommandTreeError(f"command {label!r} has an invalid mutation class")
        if node.execution_policy not in _EXECUTION_POLICIES:
            raise CommandTreeError(f"command {label!r} has an invalid execution policy")
        if node.output_policy not in _OUTPUT_POLICIES:
            raise CommandTreeError(f"command {label!r} has an invalid output policy")
        _validate_options(node.options, label)
        declared_flags = frozenset(flag for option in node.options for flag in option.flags)
        duplicate_inherited = inherited_flags & declared_flags
        if duplicate_inherited:
            raise CommandTreeError(
                f"duplicate option {sorted(duplicate_inherited)[0]!r} on {label!r}"
            )
        _validate_policy(node, label)
        if not node.children and node.handler is None:
            raise CommandTreeError(f"command {label!r} has no handler")
        if (
            node.handler is not None
            and node.handler.forward_confirm_flag
            and "--confirm" not in declared_flags
        ):
            raise CommandTreeError(
                f"command {label!r} forwards --confirm without declaring the option"
            )
        if node.handler is not None and resolve_handlers:
            node.handler.resolve()
        _validate_nodes(
            node.children,
            path,
            inherited_flags=inherited_flags | declared_flags,
            resolve_handlers=resolve_handlers,
        )


def _validate_options(options: tuple[CommandOption, ...], label: str) -> None:
    flags: set[str] = set()
    for option in options:
        if not option.flags or not option.summary:
            raise CommandTreeError(f"option on {label!r} requires flags and a summary")
        for flag in option.flags:
            if not flag.startswith("-"):
                raise CommandTreeError(f"invalid option {flag!r} on {label!r}")
            if flag in flags:
                raise CommandTreeError(f"duplicate option {flag!r} on {label!r}")
            flags.add(flag)
        if option.output_policy is not None and option.output_policy not in _OUTPUT_POLICIES:
            raise CommandTreeError(f"option on {label!r} has an invalid output policy")


def _validate_policy(node: CommandNode, label: str) -> None:
    transports = set(node.transports)
    if len(transports) != len(node.transports) or not transports <= _TRANSPORTS:
        raise CommandTreeError(f"command {label!r} has invalid transports")
    if node.execution_policy == "offline":
        if (
            node.resource_role
            or node.coowned_resource_roles
            or node.transports
            or node.execution_runtime_roles
            or node.execution_host_os
            or node.recovery_capable
            or node.gpu_role_required
            or node.remote_operation
        ):
            raise CommandTreeError(f"offline command {label!r} must not declare execution metadata")
        return
    if not node.resource_role or not node.transports or not node.execution_runtime_roles:
        raise CommandTreeError(
            f"resource-owner command {label!r} requires resource, transport, and runtime metadata"
        )
    if (
        len(set(node.coowned_resource_roles)) != len(node.coowned_resource_roles)
        or node.resource_role in node.coowned_resource_roles
        or any(not role for role in node.coowned_resource_roles)
    ):
        raise CommandTreeError(f"command {label!r} has invalid co-owned resource roles")
    if (
        len(set(node.execution_host_os)) != len(node.execution_host_os)
        or not set(node.execution_host_os) <= _HOST_OSES
    ):
        raise CommandTreeError(f"command {label!r} has invalid execution host OS metadata")
    if node.recovery_capable and "ssh" not in transports:
        raise CommandTreeError(f"recovery-capable command {label!r} requires ssh transport")
    if ("controller" in transports) != (node.remote_operation is not None):
        raise CommandTreeError(
            f"command {label!r} must pair controller transport with a remote operation"
        )
    remote = node.remote_operation
    if remote is None:
        return
    if remote.mode not in _REMOTE_MODES:
        raise CommandTreeError(f"command {label!r} has an invalid remote operation mode")
    if remote.mode == "tool" and not remote.tool:
        raise CommandTreeError(f"command {label!r} requires a controller tool")
    if remote.mode != "tool" and remote.tool is not None:
        raise CommandTreeError(f"command {label!r} special remote mode cannot declare a tool")
    fixed_names = [name for name, _value in remote.fixed_arguments]
    confirmed_names = [name for name, _value in remote.confirmed_arguments]
    if len(fixed_names) != len(set(fixed_names)) or any(not name for name in fixed_names):
        raise CommandTreeError(f"command {label!r} has invalid fixed remote arguments")
    if (
        len(confirmed_names) != len(set(confirmed_names))
        or any(not name for name in confirmed_names)
        or set(fixed_names) & set(confirmed_names)
    ):
        raise CommandTreeError(f"command {label!r} has invalid confirmed remote arguments")
    if len(remote.allowed_arguments) != len(set(remote.allowed_arguments)):
        raise CommandTreeError(f"command {label!r} has duplicate allowed remote arguments")
    if len(remote.positional_arguments) != len(set(remote.positional_arguments)):
        raise CommandTreeError(f"command {label!r} has duplicate remote positional arguments")
    alias_names = [name for name, _target in remote.argument_aliases]
    alias_targets = [target for _name, target in remote.argument_aliases]
    if (
        len(alias_names) != len(set(alias_names))
        or any(not name or not target for name, target in remote.argument_aliases)
        or any(target not in remote.allowed_arguments for target in alias_targets)
    ):
        raise CommandTreeError(f"command {label!r} has invalid remote argument aliases")
    if remote.max_response_bytes is not None and (
        isinstance(remote.max_response_bytes, bool) or remote.max_response_bytes <= 0
    ):
        raise CommandTreeError(f"command {label!r} has an invalid remote response bound")


def manifest_data(tree: CommandTree | None = None) -> dict[str, object]:
    """Return deterministic, JSON-serializable manifest data for ``tree``."""
    tree = tree or _default_tree()
    validate_command_tree(tree)
    records = list(_manifest_records(tree.nodes, (), tree.global_options))
    return {"schema_version": MANIFEST_SCHEMA_VERSION, "commands": records}


def _manifest_records(
    nodes: tuple[CommandNode, ...], parent: tuple[str, ...], inherited: tuple[CommandOption, ...]
):
    for node in nodes:
        path = parent + (node.name,)
        options = inherited + node.options
        yield {
            "path": " ".join(path),
            "summary": node.summary,
            "visible": node.visible,
            "options": [_option_data(option) for option in options],
            "mutation_class": node.mutation_class,
            "execution_policy": node.execution_policy,
            "output_policy": node.output_policy,
            "resource_role": node.resource_role,
            "coowned_resource_roles": list(node.coowned_resource_roles),
            "transports": list(node.transports),
            "execution_runtime_roles": list(node.execution_runtime_roles),
            "execution_host_os": list(node.execution_host_os),
            "recovery_capable": node.recovery_capable,
            "gpu_role_required": node.gpu_role_required,
            "handler": node.handler.name if node.handler else None,
            "remote_operation": _remote_operation_data(node.remote_operation),
            "docs_anchor": node.docs_anchor,
        }
        yield from _manifest_records(node.children, path, options)


def _option_data(option: CommandOption) -> dict[str, object]:
    return {
        "flags": list(option.flags),
        "summary": option.summary,
        "value_name": option.value_name,
        "output_policy": option.output_policy,
        "requires_confirmation": option.requires_confirmation,
    }


def _remote_operation_data(remote: RemoteOperation | None) -> dict[str, object] | None:
    if remote is None:
        return None
    return {
        "mode": remote.mode,
        "tool": remote.tool,
        "fixed_arguments": dict(remote.fixed_arguments),
        "confirmed_arguments": dict(remote.confirmed_arguments),
        "allowed_arguments": list(remote.allowed_arguments),
        "positional_arguments": list(remote.positional_arguments),
        "argument_aliases": dict(remote.argument_aliases),
        "max_response_bytes": remote.max_response_bytes,
    }


def render_manifest(tree: CommandTree | None = None) -> bytes:
    """Serialize the manifest with stable ordering and a final newline."""
    return (
        json.dumps(manifest_data(tree), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def manifest_matches(path: Path = MANIFEST_PATH, tree: CommandTree | None = None) -> bool:
    """Return whether the checked-in manifest equals in-memory regeneration."""
    try:
        return path.read_bytes() == render_manifest(tree)
    except OSError:
        return False


def write_manifest(path: Path = MANIFEST_PATH, tree: CommandTree | None = None) -> None:
    """Write the deterministic manifest for deliberate regeneration workflows."""
    path.write_bytes(render_manifest(tree))
