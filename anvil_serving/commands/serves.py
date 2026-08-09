"""Command declarations for the serves family."""

from .family import command_family
from .common import CONFIRM_OPTIONS
from .spec import CommandNode, _node, _option, _remote, _resource_node


@command_family(category="Local serving tools")
def commands() -> CommandNode:
    return _node(
        "serves",
        "Manage local model serve lifecycle.",
        children=(
            _resource_node(
                "render",
                "Render a model serve definition.",
                "anvil_serving.serves",
                role="model-serve",
                mutation="mutate",
                gpu=True,
            ),
            _resource_node(
                "up",
                "Start manifest-owned model serves.",
                "anvil_serving.serves",
                role="model-serve",
                options=CONFIRM_OPTIONS,
                mutation="mutate",
                gpu=True,
                remote_operation=_remote(
                    "serves_manage", fixed=(("action", "up"),), positionals=("names",)
                ),
            ),
            _resource_node(
                "up-for",
                "Resolve a chat alias to its backing serve and start it.",
                "anvil_serving.serves",
                role="model-serve",
                # The default invocation is a read-only resolution report; only
                # --confirm mutates. A conditional gate (the switch --recipe
                # pattern) demands confirmation exactly when --confirm is
                # present, instead of gating the read path behind ceremony.
                options=(
                    _option("--dry-run", summary="Preview without mutating state."),
                    _option(
                        "--confirm",
                        summary="Start the resolved serve (guarded mutation).",
                        requires_confirmation=True,
                    ),
                    _option("--config", summary="Router config TOML.", value_name="PATH"),
                ),
                mutation="mutate",
                gpu=True,
                forward_confirm_flag=True,
                docs_anchor="docs/cli/serves.md#start-by-alias",
            ),
            _resource_node(
                "down",
                "Stop and remove manifest-owned model serves.",
                "anvil_serving.serves",
                role="model-serve",
                options=CONFIRM_OPTIONS
                + (
                    _option(
                        "--keep-container",
                        summary="Stop without removing the container or its logs.",
                    ),
                ),
                mutation="mutate",
                gpu=True,
                remote_operation=_remote(
                    "serves_manage",
                    fixed=(("action", "down"),),
                    allowed=("keep_container", "dry_run"),
                    positionals=("names",),
                ),
            ),
            _resource_node(
                "rm",
                "Remove a model serve.",
                "anvil_serving.serves",
                role="model-serve",
                options=CONFIRM_OPTIONS,
                mutation="mutate",
                gpu=True,
                remote_operation=_remote(
                    "serves_manage", fixed=(("action", "rm"),), positionals=("names",)
                ),
            ),
            _resource_node(
                "adopt",
                "Adopt an existing model serve.",
                "anvil_serving.serves",
                role="model-serve",
                options=CONFIRM_OPTIONS,
                mutation="mutate",
                gpu=True,
                remote_operation=_remote(
                    "serves_manage", fixed=(("action", "adopt"),), positionals=("names",)
                ),
            ),
            _resource_node(
                "switch",
                "Switch a deployment role to an activation-ready recipe.",
                "anvil_serving.serves",
                role="model-serve",
                options=CONFIRM_OPTIONS
                + (
                    _option("--manifest", summary="Serve manifest TOML.", value_name="PATH"),
                    _option("--registry", summary="Serve-recipe registry TOML.", value_name="PATH"),
                    _option(
                        "--recipe",
                        summary="Compatibility spelling for the positional MODEL selector.",
                        value_name="MODEL",
                        requires_confirmation=True,
                    ),
                ),
                mutation="mutate",
                gpu=True,
            ),
            _resource_node(
                "promote",
                "Promote a staged model recipe with preflight and full rollback.",
                "anvil_serving.serves",
                role="model-serve",
                options=CONFIRM_OPTIONS
                + (
                    _option(
                        "--skip-preflight-checks",
                        summary="Skip the implicit lint + rollback-check gate "
                                 "(loudly logged; local invocation only).",
                    ),
                    _option(
                        "--derive",
                        summary="Derive and print a [[promotion]] plan from "
                                 "TARGET and ROLLBACK (read-only).",
                    ),
                    _option(
                        "--router-config",
                        summary="Promoted-state router config for --derive.",
                        value_name="PATH",
                    ),
                    _option(
                        "--rollback-router-config",
                        summary="Rollback-state router config for --derive.",
                        value_name="PATH",
                    ),
                    _option(
                        "--out",
                        summary="Write the derived block here; refuses to overwrite.",
                        value_name="PATH",
                    ),
                ),
                mutation="mutate",
                gpu=True,
                remote_operation=_remote(
                    "serves_promote",
                    confirmed=(("human_approved", True),),
                    allowed=("manifest", "rollback", "resume", "dry_run"),
                    positionals=("plan",),
                ),
            ),
            _node(
                "mode",
                "Preview or transact split and exclusive TP=2 operating modes.",
                children=(
                    _resource_node(
                        "status",
                        "Show the active split or exclusive TP=2 mode.",
                        "anvil_serving.serves",
                        role="model-serve",
                        gpu=True,
                        argv_prefix=("mode", "status"),
                        remote_operation=_remote(
                            "serves_mode", fixed=(("action", "status"),)
                        ),
                    ),
                    _resource_node(
                        "preview",
                        "Preview exclusive entry without mutating GPU workloads.",
                        "anvil_serving.serves",
                        role="model-serve",
                        options=(
                            _option(
                                "--restore-group",
                                summary="Split group restored on entry failure.",
                                value_name="NAME",
                            ),
                        ),
                        gpu=True,
                        argv_prefix=("mode", "preview"),
                        remote_operation=_remote(
                            "serves_mode",
                            fixed=(("action", "preview"),),
                            allowed=("manifest", "restore_group"),
                            positionals=("target",),
                        ),
                    ),
                    *(
                        _resource_node(
                            action,
                            "%s exclusive TP=2 mode transactionally." % summary,
                            "anvil_serving.serves",
                            role="model-serve",
                            options=CONFIRM_OPTIONS
                            + (
                                _option(
                                    "--restore-group",
                                    summary="Split group restored on leave or failure.",
                                    value_name="NAME",
                                ),
                                _option(
                                    "--drain-timeout",
                                    summary="Bounded router drain wait.",
                                    value_name="SECONDS",
                                ),
                                _option(
                                    "--router-url",
                                    summary="Router transition base URL.",
                                    value_name="URL",
                                ),
                            )
                            # Only `enter` runs the preflight gate; `leave` has
                            # no such flag, and the option list is generated by
                            # the same loop for both actions.
                            + (
                                (
                                    _option(
                                        "--skip-preflight-checks",
                                        summary="Skip the implicit lint + "
                                                 "rollback-check gate (loudly "
                                                 "logged; local invocation only).",
                                    ),
                                )
                                if action == "enter"
                                else ()
                            ),
                            mutation="mutate",
                            gpu=True,
                            argv_prefix=("mode", action),
                            # The legacy leaf parser gates the transaction on
                            # its own --confirm option; forward the consumed
                            # dispatcher flag in argv.
                            forward_confirm_flag=True,
                            remote_operation=_remote(
                                "serves_mode",
                                fixed=(("action", action),),
                                confirmed=(("human_approved", True),),
                                allowed=(
                                    "manifest", "restore_group", "drain_timeout",
                                    "dry_run", "timeout_seconds",
                                ),
                                positionals=("target",),
                            ),
                        )
                        for action, summary in (("enter", "Enter"), ("leave", "Leave"))
                    ),
                ),
            ),
            _resource_node(
                "status",
                "Show model serve status.",
                "anvil_serving.serves",
                role="model-serve",
                gpu=True,
                remote_operation=_remote("serves_status", positionals=("names",)),
            ),
            _resource_node(
                "probe",
                "Run an engine-aware functional serve probe.",
                "anvil_serving.serves",
                role="model-serve",
                gpu=True,
                docs_anchor="docs/cli/serves.md#functional-probes",
            ),
            _resource_node(
                "groups",
                "List serve groups across the manifest set and their members.",
                "anvil_serving.serves",
                role="model-serve",
            ),
            _resource_node(
                "lint",
                "Report manifest defects that no other surface makes visible.",
                "anvil_serving.serves",
                role="model-serve",
                docs_anchor="docs/cli/serves.md#lint",
            ),
            _resource_node(
                "rollback-check",
                "Prove every declared rollback is actually usable.",
                "anvil_serving.serves",
                role="model-serve",
                docs_anchor="docs/cli/serves.md#rollback-check",
            ),
            _resource_node(
                "logs",
                "Read bounded model serve logs.",
                "anvil_serving.serves",
                role="model-serve",
                options=(
                    _option("--follow", summary="Follow log output.", output_policy="follow"),
                ),
                gpu=True,
                remote_operation=_remote("serves_logs", positionals=("names",)),
            ),
            _resource_node(
                "multiplex",
                "Run the single-resident model multiplexer.",
                "anvil_serving.multiplexer",
                role="model-serve",
                mutation="process",
                gpu=True,
                argv_prefix=(),
                output_policy="foreground",
            ),
        ),
        docs_anchor="docs/cli/serves.md",
    )
