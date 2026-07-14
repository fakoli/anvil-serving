"""Declarative Operator CLI v2 command tree and manifest renderer.

This module is deliberately independent of the current root dispatcher.  It
defines the public v2 surface so later migration tasks can drive dispatch,
help, and tombstones from one validated declaration.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import importlib
import json
from pathlib import Path
from typing import Callable, Iterable


MANIFEST_SCHEMA_VERSION = 2
MANIFEST_PATH = Path(__file__).resolve().parent.parent / "docs" / "CLI-COMMAND-MANIFEST.json"
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
class Tombstone:
    """Migration guidance for a removed path or option."""

    replacement: str
    docs_anchor: str


@dataclass(frozen=True)
class CommandOption:
    """A visible CLI option, including declarative option tombstones."""

    flags: tuple[str, ...]
    summary: str
    value_name: str | None = None
    tombstone: Tombstone | None = None
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "fixed_arguments", tuple(self.fixed_arguments))
        object.__setattr__(self, "confirmed_arguments", tuple(self.confirmed_arguments))
        object.__setattr__(self, "allowed_arguments", tuple(self.allowed_arguments))
        object.__setattr__(self, "positional_arguments", tuple(self.positional_arguments))


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
    tombstone: Tombstone | None = None
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


def _removed_option(*flags: str, replacement: str) -> CommandOption:
    return CommandOption(
        flags=flags,
        summary="Removed option.",
        tombstone=Tombstone(replacement, "docs/CLI.md#migration-from-legacy-commands"),
    )


def _handler(
    module: str,
    *,
    attribute: str = "main",
    argv_prefix: Iterable[str] | None = None,
    forward_resolution_options: bool = False,
) -> HandlerRef:
    return HandlerRef(
        module,
        attribute=attribute,
        argv_prefix=None if argv_prefix is None else tuple(argv_prefix),
        forward_resolution_options=forward_resolution_options,
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
) -> RemoteOperation:
    return RemoteOperation(
        mode=mode,
        tool=tool,
        fixed_arguments=tuple(fixed),
        confirmed_arguments=tuple(confirmed),
        allowed_arguments=tuple(allowed),
        positional_arguments=tuple(positionals),
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
    tombstone: Tombstone | None = None,
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
        tombstone=tombstone,
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
        ) if module else _future_handler(),
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
    _option("--topology", summary="Topology document used for target resolution.", value_name="PATH"),
    _option("--topology-overlay", summary="Deployment overlay applied to the topology.", value_name="PATH"),
    _option("--command-host", summary="Declared command host.", value_name="host:ID"),
    _option("--command-runtime", summary="Declared command runtime.", value_name="runtime:ID"),
    _option("--target", summary="Explicit resource-owner target.", value_name="host:ID|host-role:ROLE"),
    _option("--transport", summary="Execution transport.", value_name="auto|local|controller|ssh"),
    _option("--allow-ssh-fallback", summary="Allow verified SSH recovery after a proven pre-dispatch controller failure."),
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


def build_command_tree() -> CommandTree:
    """Build the complete canonical v2 tree without importing command handlers."""
    migration_docs = "docs/CLI.md#migration-from-legacy-commands"

    def removed(replacement: str) -> Tombstone:
        return Tombstone(replacement, migration_docs)

    action_options = (_option("--dry-run", summary="Preview without mutating state."),)
    confirm_options = action_options + (_option("--confirm", summary="Confirm the guarded mutation."),)
    manifest_option = _option("--manifest", summary="Serve manifest TOML.", value_name="PATH")
    recipe_registry_option = _option("--registry", summary="Serve-recipe registry TOML.", value_name="PATH")
    recipe_file_option = _option("--recipe-file", summary="TOML file containing one recipe.", value_name="PATH")
    recipe_container_option = _option("--container", summary="New Docker container name.", value_name="NAME")
    assume_yes_option = _option("--yes", summary="Confirm a direct irreversible leaf operation.")
    group_option = _option(
        "--group",
        summary="Act on every serve tagged NAME across the manifest set (repeatable; 'all' selects every serve).",
        value_name="NAME",
    )

    router = _node(
        "router", "Manage the deployed router and its lifecycle.",
        children=(
            _resource_node("run", "Run the router in the foreground.", "anvil_serving.router.serve", role="router", mutation="process", argv_prefix=(), output_policy="foreground", options=(
                _option("--config", summary="Router TOML; alternatively configure ANVIL_MODE.", value_name="PATH"),
                _option("--mode", summary="Configured router mode; alternatively use ANVIL_MODE.", value_name="agentic|flexibility"),
                _option("--host", summary="Router bind host.", value_name="ADDRESS"),
                _option("--port", summary="Router bind port.", value_name="PORT"),
            )),
            *(
                _resource_node(
                    action,
                    summary,
                    "anvil_serving.router_manage",
                    role="router",
                    mutation="mutate",
                    options=confirm_options,
                    remote_operation=_remote(
                        "router_manage",
                        fixed=(("action", action),),
                        allowed=(
                            ("compose", "service", "env_file", "dry_run")
                            if action == "up"
                            else ("compose", "service", "dry_run")
                            if action == "down"
                            else ("container", "dry_run", "no_verify")
                        ),
                    ),
                )
                for action, summary in (
                    ("up", "Start the deployed router."), ("down", "Stop the deployed router."),
                    ("restart", "Restart the deployed router."), ("reload", "Reload router configuration."),
                )
            ),
            _resource_node(
                "promote",
                "Promote a reviewed router configuration.",
                "anvil_serving.router_manage",
                role="router",
                mutation="mutate",
                options=confirm_options + (
                    _option("--profile", summary="Reviewed profile JSON to promote.", value_name="PATH"),
                    _option("--config", summary="Optional router configuration to promote.", value_name="PATH"),
                    _option("--validate-only", summary="Validate without changing router state."),
                ),
                remote_operation=_remote(
                    "router_promote",
                    confirmed=(("human_approved", True),),
                    allowed=(
                        "container", "dry_run", "profile", "config", "cfg_volume",
                        "image", "profile_dest", "config_dest", "no_reload",
                        "validate_only",
                    ),
                ),
            ),
            _resource_node(
                "endpoint",
                "Show the router listen address and this node's Tailscale DNS name.",
                "anvil_serving.router_endpoint",
                role="router",
                argv_prefix=(),
                options=(
                    _option("--container", summary="Deployed router container.", value_name="NAME"),
                    _option("--host", summary="Explicit listen host override.", value_name="ADDRESS"),
                    _option("--port", summary="Explicit listen port override.", value_name="PORT"),
                    _option("--no-tailscale", summary="Skip Tailscale DNS discovery."),
                ),
                execution_runtime_roles=("native",),
                docs_anchor=ROUTER_DOC,
            ),
            _resource_node("status", "Show router status.", "anvil_serving.router_manage", role="router", remote_operation=_remote("router_status", allowed=("container",))),
            _resource_node(
                "transition-status", "Show router tier transition state.",
                "anvil_serving.router_manage", role="router",
                options=(
                    _option("--tier", summary="Optional tier id.", value_name="ID"),
                    _option("--router-url", summary="Private router base URL.", value_name="URL"),
                ),
                remote_operation=_remote(
                    "router_transition", fixed=(("action", "status"),),
                    allowed=("tier", "router_url"),
                ),
            ),
            *(
                _resource_node(
                    action, summary, "anvil_serving.router_manage", role="router",
                    mutation="mutate" if action in ("quiesce", "readmit") else "read",
                    options=(confirm_options if action in ("quiesce", "readmit") else ()) + (
                        _option("--tier", summary="Tier id.", value_name="ID"),
                        _option("--router-url", summary="Private router base URL.", value_name="URL"),
                    ) + ((_option("--timeout", summary="Positive drain timeout.", value_name="SECONDS"),) if action == "drain" else ()),
                    remote_operation=_remote(
                        "router_transition", fixed=(("action", action),),
                        allowed=("tier", "router_url", "timeout", "dry_run"),
                    ),
                )
                for action, summary in (
                    ("quiesce", "Quiesce one router tier."),
                    ("drain", "Wait for a quiesced tier to drain."),
                    ("readmit", "Safely readmit one router tier."),
                )
            ),
            _resource_node("logs", "Read bounded router logs.", "anvil_serving.router_manage", role="router", options=(_option("--follow", summary="Follow log output.", output_policy="follow"),), remote_operation=_remote("router_logs", allowed=("container", "tail", "since", "follow"))),
            _resource_node("token", "Inspect the router token state.", "anvil_serving.router_manage", role="router", options=(_option("--reveal", summary="Reveal the local token after confirmation.", requires_confirmation=True), _option("--confirm", summary="Confirm token reveal."))),
        ),
        docs_anchor=ROUTER_DOC,
    )
    serves_actions = (
        _resource_node("render", "Render a model serve definition.", "anvil_serving.serves", role="model-serve", mutation="mutate", gpu=True, options=action_options),
        _resource_node("up", "Start manifest-owned model serves.", "anvil_serving.serves", role="model-serve", mutation="mutate", gpu=True, options=confirm_options + (manifest_option, group_option, _option("--compose", summary="Use an ad-hoc compose file.", value_name="PATH"), _option("--recreate", summary="Recreate an existing container."), _option("--evict", summary="Stop evictable reservations via a drained ADR-0018 transition."), _option("--drain-timeout", summary="Bounded drain wait before an evicted serve is stopped.", value_name="SECONDS"), _option("--router-url", summary="Deployed router base URL for eviction quiesce/drain.", value_name="URL")), remote_operation=_remote("serves_manage", fixed=(("action", "up"),), positionals=("names",))),
        _resource_node("down", "Stop manifest-owned model serves.", "anvil_serving.serves", role="model-serve", mutation="mutate", gpu=True, options=confirm_options + (manifest_option, group_option), remote_operation=_remote("serves_manage", fixed=(("action", "down"),), positionals=("names",))),
        _resource_node("rm", "Remove a model serve.", "anvil_serving.serves", role="model-serve", mutation="mutate", gpu=True, options=confirm_options + (manifest_option, assume_yes_option), remote_operation=_remote("serves_manage", fixed=(("action", "rm"),), positionals=("names",))),
        _resource_node("adopt", "Adopt an existing model serve.", "anvil_serving.serves", role="model-serve", mutation="mutate", gpu=True, options=confirm_options + (manifest_option, assume_yes_option), remote_operation=_remote("serves_manage", fixed=(("action", "adopt"),), positionals=("names",))),
        _resource_node("promote", "Promote a staged model recipe with preflight and full rollback.", "anvil_serving.serves", role="model-serve", mutation="mutate", gpu=True, options=confirm_options + (manifest_option, _option("--rollback", summary="Restore the plan's rollback serve and router state."), _option("--resume", summary="Resume an interrupted promotion.")), remote_operation=_remote("serves_promote", positionals=("plan",), allowed=("manifest", "rollback", "resume", "dry_run"), confirmed=(("human_approved", True),))),
        _resource_node("status", "Show model serve status.", "anvil_serving.serves", role="model-serve", gpu=True, options=(manifest_option, group_option), remote_operation=_remote("serves_status", positionals=("names",))),
        _resource_node("groups", "List serve groups across the manifest set and their members.", "anvil_serving.serves", role="model-serve", options=(manifest_option,)),
        _resource_node("logs", "Read bounded model serve logs.", "anvil_serving.serves", role="model-serve", gpu=True, options=(manifest_option, _option("--tail", summary="Number of trailing lines.", value_name="N|all"), _option("--since", summary="Only logs since a timestamp or duration.", value_name="TIME"), _option("--follow", summary="Follow log output.", output_policy="follow")), remote_operation=_remote("serves_logs", positionals=("names",))),
        _resource_node("multiplex", "Run the single-resident model multiplexer.", "anvil_serving.multiplexer", role="model-serve", mutation="process", gpu=True, argv_prefix=(), output_policy="foreground"),
    )
    serves = _node("serves", "Manage local model serve lifecycle.", children=serves_actions, docs_anchor=SERVES_DOC)
    models = _node(
        "models", "Manage model catalog, artifacts, and recipes.",
        children=(
            _resource_node("sync", "Sync the model catalog.", "anvil_serving.models", role="model-catalog", mutation="mutate", options=action_options),
            _resource_node("pull", "Pull a model artifact.", "anvil_serving.models", role="model-catalog", mutation="mutate", options=confirm_options),
            _resource_node("score", "Rank models from benchmark evidence.", "anvil_serving.models", role="model-catalog"),
            _node("recipes", "Manage recorded serve recipes.", children=(
                _resource_node("list", "List recorded serve recipes.", "anvil_serving.models", role="model-catalog", argv_prefix=("recipe", "list"), options=(recipe_registry_option,)),
                _resource_node("show", "Show one recorded serve recipe.", "anvil_serving.models", role="model-catalog", argv_prefix=("recipe", "show"), options=(recipe_registry_option,)),
                _resource_node("create", "Create one recipe in an operator registry.", "anvil_serving.models", role="model-catalog", mutation="mutate", argv_prefix=("recipe", "create"), options=confirm_options + (recipe_registry_option, recipe_file_option)),
                _resource_node("update", "Update one selected recipe.", "anvil_serving.models", role="model-catalog", mutation="mutate", argv_prefix=("recipe", "update"), options=confirm_options + (recipe_registry_option, recipe_file_option)),
                _resource_node("delete", "Delete one selected recipe.", "anvil_serving.models", role="model-catalog", mutation="mutate", argv_prefix=("recipe", "delete"), options=confirm_options + (recipe_registry_option,)),
                _resource_node("load", "Load one recipe into a named local container.", "anvil_serving.models", role="model-serve", mutation="mutate", gpu=True, argv_prefix=("recipe", "load"), options=confirm_options + (recipe_registry_option, recipe_container_option)),
            ), docs_anchor=f"{MODELS_DOC}#recipes"),
            _node("cache", "Manage model cache storage.", children=(
                _resource_node(
                    "prune",
                    "Plan or prune the model cache.",
                    "anvil_serving.models",
                    role="model-catalog",
                    mutation="mutate",
                    options=confirm_options + (
                        _option("--execute", summary="Delete the planned cache candidates.", requires_confirmation=True),
                        _option("--yes", summary="Acknowledge cache deletion."),
                        _option("--mixture", summary="Comma-separated model ids to protect.", value_name="MODELS"),
                        _option("--include-servable", summary="Also delete candidates servable elsewhere."),
                        _option("--allow-empty-mixture", summary="Allow a broad wipe with no protected mixture."),
                        _option("--self-check", summary="Run the non-destructive internal self-check."),
                    ),
                ),
            ), docs_anchor=f"{MODELS_DOC}#cache-prune"),
            _node(
                "recipe",
                "Removed singular recipe spelling.",
                children=(
                    _node("list", "Removed singular recipe list path.", tombstone=removed("models recipes list"), visible=False),
                    _node("show", "Removed singular recipe show path.", tombstone=removed("models recipes show"), visible=False),
                ),
                tombstone=removed("models recipes"),
                visible=False,
            ),
        ),
        docs_anchor=MODELS_DOC,
    )
    external_remote_tools = {
        "sources": "external_bench_sources",
        "list": "external_bench_list",
        "report": "external_bench_report",
        "compare": "external_bench_compare",
    }
    external_actions = tuple(
        _resource_node(action, summary, "anvil_serving.external_benchmarks.cli", role="evaluation", mutation=mutation, argv_prefix=(action,), remote_operation=(_remote(external_remote_tools[action]) if action in external_remote_tools else None))
        for action, summary, mutation in (
            ("init", "Initialize benchmark evidence storage.", "mutate"), ("sources", "List benchmark sources.", "read"),
            ("fetch", "Fetch and import benchmark evidence.", "mutate"), ("import", "Import saved benchmark evidence.", "mutate"),
            ("list", "List normalized benchmark evidence.", "read"), ("report", "Render a benchmark report.", "read"),
            ("export", "Export benchmark evidence.", "mutate"), ("compare", "Compare local benchmark evidence.", "read"),
        )
    )
    notebook = _node(
        "notebook", "Record, list, or render model-bakeoff notebook runs.",
        children=tuple(
            _resource_node(
                action, summary, "anvil_serving.external_benchmarks.cli",
                role="evaluation", mutation="mutate" if action == "add" else "read",
                argv_prefix=("notebook", action),
            )
            for action, summary in (
                ("add", "Record a bakeoff evidence run."),
                ("list", "List recorded bakeoff runs."),
                ("render", "Render the bakeoff comparison."),
            )
        ),
        docs_anchor=f"{EVAL_DOC}#external-benchmarks",
    )
    eval_node = _node(
        "eval", "Run quality evaluation workflows.",
        children=(
            _resource_node("usage", "Analyze recorded usage.", "anvil_serving.profile", role="evaluation", argv_prefix=()),
            _resource_node(
                "preflight", "Preflight an endpoint.", "anvil_serving.preflight",
                role="evaluation", mutation="mutate", argv_prefix=(),
                options=(_option("--confirm", summary="Confirm the live evaluation workload."),),
                remote_operation=_remote(
                    "preflight_probe", confirmed=(("confirm", True),),
                    allowed=("base_url", "model", "api_key_env", "needle_ctx", "tool_batch", "checks", "no_thinking", "thinking_mode", "reasoning_effort", "reasoning_evidence", "visible_answer_tokens", "reasoning_headroom_tokens", "timeout_seconds"),
                ),
            ),
            _resource_node("planning", "Run planning evaluation.", "anvil_serving.eval", role="evaluation"),
            _resource_node("bootstrap", "Bootstrap a quality profile.", "anvil_serving.eval", role="evaluation", mutation="mutate"),
            _resource_node("calibrate", "Calibrate a reviewable quality profile.", "anvil_serving.calibrate", role="evaluation", mutation="mutate", argv_prefix=()),
            _node("benchmark", "Run or import benchmark evidence.", children=(
                _resource_node(
                    "run", "Run an endpoint benchmark.", "anvil_serving.benchmark",
                    role="evaluation", mutation="mutate", argv_prefix=(),
                    options=(_option("--confirm", summary="Confirm the live evaluation workload."),),
                    remote_operation=_remote(
                        "benchmark_probe", confirmed=(("confirm", True),),
                        allowed=("base_url", "model", "api_key_env", "requests", "concurrency", "max_tokens", "ctx_tokens", "no_thinking", "thinking_mode", "reasoning_effort", "visible_answer_tokens", "reasoning_headroom_tokens", "timeout_seconds"),
                    ),
                ),
                _node(
                    "evidence",
                    "Inspect retained local benchmark evidence.",
                    children=tuple(
                        _node(
                            action,
                            summary,
                            handler=_handler(
                                "anvil_serving.benchmark_evidence",
                                argv_prefix=(action,),
                            ),
                            docs_anchor=f"{EVAL_DOC}#benchmark-evidence",
                        )
                        for action, summary in (
                            ("list", "List retained local benchmark artifacts."),
                            ("show", "Show a normalized benchmark artifact summary."),
                            ("compare", "Compare artifacts and flag workload mismatches."),
                        )
                    ),
                    docs_anchor=f"{EVAL_DOC}#benchmark-evidence",
                ),
                _node("external", "Manage external benchmark evidence.", children=(*external_actions, notebook), docs_anchor=f"{EVAL_DOC}#external-benchmarks"),
            ), docs_anchor=f"{EVAL_DOC}#benchmark"),
        ),
        docs_anchor=EVAL_DOC,
    )
    voice = _node(
        "voice", "Manage audio and realtime proxy operations.",
        children=(
            _node("audio", "Manage Dark-owned STT/TTS lifecycle.", children=(
                _resource_node("up", "Start audio serves.", "anvil_serving.voice.cli", role="stt-serve", coowned_roles=("tts-serve",), mutation="mutate", options=confirm_options, argv_prefix=("audio", "up"), forward_resolution_options=True, remote_operation=_remote("voice_manage", fixed=(("action", "up"),))),
                _resource_node("down", "Stop audio serves.", "anvil_serving.voice.cli", role="stt-serve", coowned_roles=("tts-serve",), mutation="mutate", options=confirm_options, argv_prefix=("audio", "down"), forward_resolution_options=True, remote_operation=_remote("voice_manage", fixed=(("action", "down"),))),
                _resource_node("status", "Show bounded audio serve status.", "anvil_serving.voice.cli", role="stt-serve", coowned_roles=("tts-serve",), argv_prefix=("audio", "status"), forward_resolution_options=True, remote_operation=_remote("voice_manage", fixed=(("action", "status"),), allowed=("config", "profile", "ready_timeout", "timeout_seconds"))),
                _resource_node("logs", "Show bounded audio serve logs.", "anvil_serving.voice.cli", role="stt-serve", coowned_roles=("tts-serve",), argv_prefix=("audio", "logs"), forward_resolution_options=True, remote_operation=_remote("voice_manage", fixed=(("action", "logs"),), allowed=("config", "profile", "tail", "timeout_seconds"))),
            ), docs_anchor=f"{VOICE_CLI_DOC}#audio-lifecycle"),
            _node("proxy", "Manage the realtime proxy process.", children=(
                _resource_node("run", "Run the realtime proxy.", "anvil_serving.voice.cli", role="realtime-proxy", coowned_roles=("stt-proxy", "tts-proxy"), mutation="process", argv_prefix=("proxy", "run"), forward_resolution_options=True, output_policy="foreground", execution_runtime_roles=("native",)),
                *(
                    _resource_node(
                        action,
                        summary,
                        "anvil_serving.voice.cli",
                        role="realtime-proxy",
                        coowned_roles=("stt-proxy", "tts-proxy"),
                        mutation="mutate",
                        options=confirm_options,
                        argv_prefix=("proxy", action),
                        forward_resolution_options=True,
                        remote_operation=_remote(
                            "voice_proxy_manage",
                            fixed=(("action", action),),
                            allowed=("config", "profile", "pid_file", "log_file", "dry_run", "timeout_seconds"),
                        ),
                        execution_runtime_roles=("native",),
                    )
                    for action, summary in (
                        ("up", "Start the realtime proxy."),
                        ("down", "Stop the realtime proxy."),
                        ("restart", "Restart the realtime proxy."),
                    )
                ),
                _resource_node("status", "Show realtime proxy status.", "anvil_serving.voice.cli", role="realtime-proxy", coowned_roles=("stt-proxy", "tts-proxy"), argv_prefix=("proxy", "status"), forward_resolution_options=True, remote_operation=_remote("voice_proxy_manage", fixed=(("action", "status"),), allowed=("config", "profile", "pid_file", "log_file", "timeout_seconds")), execution_runtime_roles=("native",)),
                _resource_node("logs", "Show bounded realtime proxy logs.", "anvil_serving.voice.cli", role="realtime-proxy", coowned_roles=("stt-proxy", "tts-proxy"), argv_prefix=("proxy", "logs"), forward_resolution_options=True, remote_operation=_remote("voice_proxy_manage", fixed=(("action", "logs"),), allowed=("config", "profile", "pid_file", "log_file", "tail", "timeout_seconds")), execution_runtime_roles=("native",)),
                _resource_node("bridge", "Run the Mini-to-Dark audio bridge.", "anvil_serving.voice.cli", role="realtime-proxy", coowned_roles=("stt-proxy", "tts-proxy"), mutation="process", argv_prefix=("proxy", "bridge"), forward_resolution_options=True, output_policy="foreground", execution_runtime_roles=("native",)),
            ), docs_anchor=f"{VOICE_CLI_DOC}#realtime-proxy"),
            _resource_node("benchmark", "Benchmark an end-to-end voice session.", "anvil_serving.voice.cli", role="realtime-proxy", coowned_roles=("stt-proxy", "tts-proxy"), argv_prefix=("benchmark",), execution_runtime_roles=("native",)),
            _node("profiles", "Inspect voice profiles.", children=(
                _node("list", "List voice profiles.", handler=_handler("anvil_serving.voice.cli", attribute="main_profiles_list", argv_prefix=())),
                _node("validate", "Validate the profile selected by --profile.", handler=_handler("anvil_serving.voice.cli", attribute="main_profiles_validate", argv_prefix=())),
            ), docs_anchor=f"{VOICE_CLI_DOC}#profiles"),
            _node("sidecar", "Manage the speech-to-speech sidecar.", children=tuple(
                _node(action, summary, handler=_handler("anvil_serving.voice_sidecar", argv_prefix=(action,)))
                for action, summary in (("validate", "Validate a sidecar manifest."), ("command", "Render a sidecar command."), ("compose", "Render sidecar compose configuration."))
            ), docs_anchor=f"{VOICE_CLI_DOC}#speech-to-speech-sidecar"),
            *(_node(name, "Removed voice command.", tombstone=removed(replacement), visible=False) for name, replacement in (
                ("up", "voice audio up"), ("down", "voice audio down"),
                ("run", "voice proxy run"), ("bridge", "voice proxy bridge"),
                ("start", "voice audio up"), ("stop", "voice audio down"),
            )),
        ),
        docs_anchor=VOICE_CLI_DOC,
    )
    harness_operations = (
        ("sync", "Synchronize harness configuration", "mutate", False, _remote(
            "openclaw_sync", confirmed=(("confirm", True),),
            allowed=(
                "config", "base_url", "api_key_env", "out", "overwrite",
                "restart", "skills", "skill_dir", "voice", "voice_realtime_url", "voice_model",
                "voice_consult_model", "voice_consult_thinking_level",
                "voice_consult_bootstrap_context_mode", "voice_api_key_env", "dry_run",
                "timeout_seconds",
            ),
        )),
        ("restart", "Restart the harness", "mutate", True, _remote(
            "openclaw_gateway_restart", confirmed=(("confirm", True),),
            allowed=("dry_run", "timeout_seconds"),
        )),
        ("status", "Show harness status", "read", False, _remote(
            "openclaw_gateway_status", allowed=("timeout_seconds", "max_output_bytes"),
        )),
    )
    harness = _node("harness", "Manage harness integration.", children=tuple(
        _node(action, summary, children=(_resource_node(
            "openclaw", f"{summary} for OpenClaw.", "anvil_serving.harness", role="gateway",
            mutation=mutation, recovery=recovery,
            options=confirm_options if mutation == "mutate" else (),
            remote_operation=remote_operation,
        ),), docs_anchor=f"{CONTROL_PLANE_DOC}#harness")
        for action, summary, mutation, recovery, remote_operation in harness_operations
    ), docs_anchor=f"{CONTROL_PLANE_DOC}#harness")
    mcp = _node("mcp", "Expose bounded MCP management tools.", children=(
        _resource_node("serve", "Run the MCP management server.", "anvil_serving.mcp", role="operator", argv_prefix=(), output_policy="protocol", remote_operation=_remote(mode="mcp-bridge")),
        _resource_node("tools", "List bounded MCP tools.", "anvil_serving.mcp", role="operator", argv_prefix=("list-tools",), remote_operation=_remote(mode="mcp-bridge")),
        _node("list-tools", "Removed MCP tool-listing command.", tombstone=removed("mcp tools"), visible=False),
    ), options=(_removed_option("--list-tools", replacement="mcp tools"),), tombstone=removed("mcp serve"), docs_anchor=f"{CONTROL_PLANE_DOC}#mcp")
    controller = _node("controller", "Manage the private controller service.", children=(
        _resource_node("serve", "Run the private controller.", "anvil_serving.controller", role="controller", mutation="process", options=(_removed_option("--allow-unauthenticated-loopback", replacement="Configure the token named by --auth-token-env"),), output_policy="foreground"),
        _resource_node("status", "Probe controller health.", "anvil_serving.controller", role="controller", remote_operation=_remote(mode="controller-status")),
    ), docs_anchor=f"{CONTROL_PLANE_DOC}#controller")
    gpu_sharing = _node(
        "gpu-sharing",
        "Inspect and probe CUDA GPU-sharing capabilities.",
        children=(
            _resource_node(
                "inspect",
                "Inspect Green Context and MPS capability without mutation.",
                "anvil_serving.gpu_sharing",
                role="host",
                argv_prefix=(),
                forward_resolution_options=True,
                options=(
                    _option(
                        "--timeout",
                        summary="Per-subprocess timeout in seconds.",
                        value_name="SECONDS",
                    ),
                ),
                execution_runtime_roles=("native",),
                docs_anchor=f"{HOST_DOC}#gpu-sharing",
            ),
            _resource_node(
                "probe",
                "Run the guarded Docker CUDA prerequisite probe.",
                "anvil_serving.gpu_sharing",
                role="host",
                argv_prefix=("probe",),
                mutation="mutate",
                options=confirm_options
                + (
                    _option(
                        "--compose-file",
                        summary="Compose file containing the reviewed probe service.",
                        value_name="PATH",
                    ),
                    _option(
                        "--gpu-uuid",
                        summary="Exact NVIDIA GPU UUID to pin and verify.",
                        value_name="GPU-UUID",
                    ),
                    _option(
                        "--timeout",
                        summary="Bounded live probe timeout in seconds.",
                        value_name="SECONDS",
                    ),
                ),
                execution_runtime_roles=("native",),
                docs_anchor=f"{HOST_DOC}#gpu-sharing",
            ),
        ),
        docs_anchor=f"{HOST_DOC}#gpu-sharing",
    )
    host_read_actions = (
        _resource_node("status", "Show structured host status.", "anvil_serving.host", role="host", execution_runtime_roles=("native",), remote_operation=_remote("host_summary")),
        _resource_node("gpus", "Show GPU inventory.", "anvil_serving.gpus", role="host", argv_prefix=(), execution_runtime_roles=("native",), remote_operation=_remote("gpu_inventory")),
        gpu_sharing,
        _resource_node("doctor", "Diagnose host configuration.", "anvil_serving.host", role="host", execution_runtime_roles=("native",), remote_operation=_remote("host_summary")),
        _resource_node("memory", "Show host RAM and WSL VM memory usage.", "anvil_serving.host", role="host", execution_runtime_roles=("native",), execution_host_os=("windows",)),
    )
    host_repairs = tuple(
        _resource_node(
            action, summary, "anvil_serving.host", role="host", mutation="mutate",
            recovery=action in {"restart-docker", "reset-wsl"}, options=confirm_options,
            execution_runtime_roles=("native",), execution_host_os=host_os,
            remote_operation=_remote(
                "host_manage", fixed=(("action", action),), confirmed=(("confirm", True),),
                allowed=allowed,
            ),
        )
        for action, summary, host_os, allowed in (
            ("wsl-config", "Render or update WSL configuration.", ("windows",), ("memory", "swap", "revert", "force", "dry_run")),
            ("restart-docker", "Restart Docker Desktop.", ("windows", "macos"), ("dry_run",)),
            ("reset-wsl", "Reset WSL.", ("windows",), ("dry_run",)),
        )
    )
    # reclaim's --watch is a foreground loop: option-level "follow" policy makes --json refuse
    # it up front instead of buffering an infinite watchdog into the JSON envelope. Local-only
    # (no remote_operation): the watchdog is a foreground session on the host itself.
    host_reclaim = _resource_node(
        "reclaim", "Drop the WSL VM page cache.", "anvil_serving.host", role="host",
        mutation="mutate", execution_runtime_roles=("native",), execution_host_os=("windows",),
        options=confirm_options + (_option("--watch", summary="Foreground reclaim watchdog loop.", output_policy="follow"),),
    )
    host = _node("host", "Inspect and repair declared host operations.", children=(*host_read_actions, *host_repairs, host_reclaim), docs_anchor=f"{HOST_DOC}#host")
    topology = _node("topology", "Inspect and resolve deployment topology.", children=tuple(
        _node(action, summary, handler=_handler("anvil_serving.topology_cli", argv_prefix=(action,)), docs_anchor=f"{CONTROL_PLANE_DOC}#topology")
        for action, summary in (
            ("show", "Show a validated topology summary."),
            ("validate", "Validate a topology offline."),
            ("resolve", "Resolve one canonical command against a topology."),
        )
    ), docs_anchor=f"{CONTROL_PLANE_DOC}#topology")
    collector_input_options = (
        _option("--config", summary="Collector configuration JSON.", value_name="PATH"),
        _option("--name", summary="Inline collector name.", value_name="NAME"),
        _option("--adapter", summary="Collector adapter identifier.", value_name="ADAPTER"),
        _option("--endpoint", summary="Private or loopback collector URL.", value_name="URL"),
        _option("--capability", summary="Required capability; repeatable.", value_name="NAME"),
        _option("--auth-env", summary="Bearer-token environment variable.", value_name="ENV"),
    )
    collectors = _node(
        "collectors",
        "Configure and inspect optional read-only collector adapters.",
        children=(
            _node(
                "configure",
                "Validate and optionally write adapter configuration.",
                handler=_handler("anvil_serving.collectors", argv_prefix=("configure",)),
                mutation_class="mutate",
                options=collector_input_options + (
                    _option("--output", summary="Write validated configuration.", value_name="PATH", requires_confirmation=True),
                    _option("--confirm", summary="Confirm writing collector configuration."),
                ),
            ),
            _node(
                "validate",
                "Validate adapter configuration without network access.",
                handler=_handler("anvil_serving.collectors", argv_prefix=("validate",)),
                options=collector_input_options,
            ),
            _node(
                "capabilities",
                "Report configured adapter capabilities offline.",
                handler=_handler("anvil_serving.collectors", argv_prefix=("capabilities",)),
                options=collector_input_options,
            ),
            _node(
                "inspect",
                "Perform one bounded read-only adapter inspection.",
                handler=_handler("anvil_serving.collectors", argv_prefix=("inspect",)),
                options=collector_input_options + (
                    _option("--timeout", summary="Bounded request timeout.", value_name="SECONDS"),
                ),
            ),
        ),
        docs_anchor=f"{CONTROL_PLANE_DOC}#collectors",
    )
    dashboard = _node(
        "dashboard",
        "Serve the read-only system observability dashboard.",
        children=(
            _resource_node(
                "serve",
                "Serve the packaged local dashboard.",
                "anvil_serving.observability.dashboard.app",
                role="host",
                argv_prefix=(),
                mutation="process",
                options=(
                    _option("--host", summary="Explicit bind IP.", value_name="IP"),
                    _option("--port", summary="Bind port.", value_name="PORT"),
                    _option(
                        "--auth-env",
                        summary="Bearer-token environment variable for authenticated binds.",
                        value_name="ENV",
                    ),
                ),
                output_policy="foreground",
                execution_runtime_roles=("native",),
            ),
        ),
        docs_anchor=f"{HOST_DOC}#dashboard",
    )
    edge_common_options = (
        _option("--config", summary="Edge route TOML ([edge]/[edge.routes]).", value_name="PATH"),
        _option("--https-port", summary="Node HTTPS listener port (default 443).", value_name="PORT"),
        _option("--host", summary="Default target host for port-only routes.", value_name="ADDRESS"),
        _option("--map", summary="Override/add a route (repeatable); MOUNT=off drops one.", value_name="MOUNT=TARGET"),
    )
    edge = _node(
        "edge", "Own the Tailscale tailnet edge in front of the unchanged router.",
        children=(
            _resource_node(
                "render", "Render the tailscale serve invocations without applying.",
                "anvil_serving.edge", role="host", argv_prefix=("render",),
                options=edge_common_options, execution_runtime_roles=("native",),
                docs_anchor=f"{CONTROL_PLANE_DOC}#edge",
            ),
            _resource_node(
                "status", "Show serve mappings, flagging which this tool manages.",
                "anvil_serving.edge", role="host", argv_prefix=("status",),
                options=edge_common_options, execution_runtime_roles=("native",),
                docs_anchor=f"{CONTROL_PLANE_DOC}#edge",
            ),
            _resource_node(
                "up", "Apply the managed route map (additive; idempotent).",
                "anvil_serving.edge", role="host", mutation="mutate", argv_prefix=("up",),
                options=edge_common_options + confirm_options, execution_runtime_roles=("native",),
                docs_anchor=f"{CONTROL_PLANE_DOC}#edge",
            ),
            _resource_node(
                "down", "Remove ONLY the mounts this tool manages.",
                "anvil_serving.edge", role="host", mutation="mutate", argv_prefix=("down",),
                options=edge_common_options + confirm_options, execution_runtime_roles=("native",),
                docs_anchor=f"{CONTROL_PLANE_DOC}#edge",
            ),
        ),
        docs_anchor=f"{CONTROL_PLANE_DOC}#edge",
    )

    tree = CommandTree(
        nodes=tuple(_inherit_docs_anchor(node) for node in (
            _node("init", "Scaffold the operational config home (or a single-model bring-up with --single-model).", handler=_handler("anvil_serving.init"), docs_anchor=f"{HOST_DOC}#init", group="Local serving tools"),
            _node("router", router.summary, children=router.children, docs_anchor=router.docs_anchor, group="Data plane"),
            _node("serves", serves.summary, children=serves.children, docs_anchor=serves.docs_anchor, group="Local serving tools"),
            _node("models", models.summary, children=models.children, docs_anchor=models.docs_anchor, group="Local serving tools"),
            _node("eval", eval_node.summary, children=eval_node.children, docs_anchor=eval_node.docs_anchor, group="Quality loop"),
            _node("voice", voice.summary, children=voice.children, docs_anchor=voice.docs_anchor, group="Voice"),
            _node("harness", harness.summary, children=harness.children, docs_anchor=harness.docs_anchor, group="Control plane & integrations"),
            _node("mcp", mcp.summary, children=mcp.children, options=mcp.options, handler=mcp.handler, docs_anchor=mcp.docs_anchor, tombstone=mcp.tombstone, visible=mcp.visible, group="Control plane & integrations"),
            _node("controller", controller.summary, children=controller.children, docs_anchor=controller.docs_anchor, group="Control plane & integrations"),
            _node("host", host.summary, children=host.children, docs_anchor=host.docs_anchor, group="Local serving tools"),
            _resource_node("doctor", "Check dependencies and configured health.", "anvil_serving.doctor", role="host", argv_prefix=(), execution_runtime_roles=("native",), remote_operation=_remote("doctor_summary"), docs_anchor=f"{HOST_DOC}#doctor", group="Local serving tools"),
            _node(
                "upgrade",
                "Upgrade this CLI to the newest stable published release.",
                handler=_handler("anvil_serving.upgrade"),
                mutation_class="mutate",
                options=confirm_options + (
                    _option("--manager", summary="Package manager override.", value_name="auto|uv|pipx|pip"),
                    _option("--allow-editable", summary="Replace an editable source install."),
                ),
                docs_anchor=f"{HOST_DOC}#upgrade",
                group="Local serving tools",
            ),
            _node("topology", topology.summary, children=topology.children, docs_anchor=topology.docs_anchor, group="Control plane & integrations"),
            _node("collectors", collectors.summary, children=collectors.children, docs_anchor=collectors.docs_anchor, group="Control plane & integrations"),
            _node("dashboard", dashboard.summary, children=dashboard.children, docs_anchor=dashboard.docs_anchor, group="Local serving tools"),
            _node("edge", edge.summary, children=edge.children, docs_anchor=edge.docs_anchor, group="Control plane & integrations"),
            *(_node(name, "Removed command.", tombstone=removed(replacement), visible=False) for name, replacement in (
                ("serve", "router run"), ("deploy", "serves render"), ("multiplexer", "serves multiplex"),
                ("cache-prune", "models cache prune"), ("score", "models score"), ("profile", "eval usage"),
                ("preflight", "eval preflight"), ("benchmark", "eval benchmark run"),
                ("external-bench", "eval benchmark external"), ("calibrate", "eval calibrate"),
                ("gpus", "host gpus"), ("voice-sidecar", "voice sidecar"), ("onboard", "init"),
            )),
        )),
        global_options=GLOBAL_OPTIONS,
    )
    return tree


COMMAND_TREE = build_command_tree()


def validate_command_tree(tree: CommandTree = COMMAND_TREE, *, resolve_handlers: bool = True) -> None:
    """Raise ``CommandTreeError`` when a command declaration is invalid."""
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
        if node.tombstone is not None:
            if node.handler is not None:
                raise CommandTreeError(f"tombstone {label!r} must not declare a handler")
            if not node.tombstone.replacement or not node.tombstone.docs_anchor:
                raise CommandTreeError(f"tombstone {label!r} requires replacement and documentation")
        elif not node.children and node.handler is None:
            raise CommandTreeError(f"command {label!r} has no handler")
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
        if option.tombstone is not None and (not option.tombstone.replacement or not option.tombstone.docs_anchor):
            raise CommandTreeError(f"option tombstone on {label!r} requires replacement and documentation")
        if option.output_policy is not None and option.output_policy not in _OUTPUT_POLICIES:
            raise CommandTreeError(f"option on {label!r} has an invalid output policy")


def _validate_policy(node: CommandNode, label: str) -> None:
    transports = set(node.transports)
    if len(transports) != len(node.transports) or not transports <= _TRANSPORTS:
        raise CommandTreeError(f"command {label!r} has invalid transports")
    if node.execution_policy == "offline":
        if node.resource_role or node.coowned_resource_roles or node.transports or node.execution_runtime_roles or node.execution_host_os or node.recovery_capable or node.gpu_role_required or node.remote_operation:
            raise CommandTreeError(f"offline command {label!r} must not declare execution metadata")
        return
    if not node.resource_role or not node.transports or not node.execution_runtime_roles:
        raise CommandTreeError(f"resource-owner command {label!r} requires resource, transport, and runtime metadata")
    if (
        len(set(node.coowned_resource_roles)) != len(node.coowned_resource_roles)
        or node.resource_role in node.coowned_resource_roles
        or any(not role for role in node.coowned_resource_roles)
    ):
        raise CommandTreeError(f"command {label!r} has invalid co-owned resource roles")
    if len(set(node.execution_host_os)) != len(node.execution_host_os) or not set(node.execution_host_os) <= _HOST_OSES:
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


def manifest_data(tree: CommandTree = COMMAND_TREE) -> dict[str, object]:
    """Return deterministic, JSON-serializable manifest data for ``tree``."""
    validate_command_tree(tree)
    records = list(_manifest_records(tree.nodes, (), tree.global_options))
    return {"schema_version": MANIFEST_SCHEMA_VERSION, "commands": records}


def _manifest_records(nodes: tuple[CommandNode, ...], parent: tuple[str, ...], inherited: tuple[CommandOption, ...]):
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
            "tombstone": _tombstone_data(node.tombstone),
            "docs_anchor": node.docs_anchor,
        }
        yield from _manifest_records(node.children, path, options)


def _option_data(option: CommandOption) -> dict[str, object]:
    return {
        "flags": list(option.flags),
        "summary": option.summary,
        "value_name": option.value_name,
        "tombstone": _tombstone_data(option.tombstone),
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
    }


def _tombstone_data(tombstone: Tombstone | None) -> dict[str, str] | None:
    if tombstone is None:
        return None
    return {"replacement": tombstone.replacement, "docs_anchor": tombstone.docs_anchor}


def render_manifest(tree: CommandTree = COMMAND_TREE) -> bytes:
    """Serialize the manifest with stable ordering and a final newline."""
    return (json.dumps(manifest_data(tree), indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")


def manifest_matches(path: Path = MANIFEST_PATH, tree: CommandTree = COMMAND_TREE) -> bool:
    """Return whether the checked-in manifest equals in-memory regeneration."""
    try:
        return path.read_bytes() == render_manifest(tree)
    except OSError:
        return False


def write_manifest(path: Path = MANIFEST_PATH, tree: CommandTree = COMMAND_TREE) -> None:
    """Write the deterministic manifest for deliberate regeneration workflows."""
    path.write_bytes(render_manifest(tree))
