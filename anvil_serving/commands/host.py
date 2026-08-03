"""Command declarations for the host family."""

from .family import command_family
from .common import CONFIRM_OPTIONS
from .spec import CommandNode, _node, _handler, _option, _remote, _resource_node


@command_family(category="Local serving tools")
def commands() -> tuple[CommandNode, ...]:
    return (
        _node(
            "init",
            "Scaffold the operational config home (or a single-model bring-up with --single-model).",
            handler=_handler("anvil_serving.init"),
            mutation_class="mutate",
            docs_anchor="docs/cli/host.md#init",
        ),
        _node(
            "host",
            "Inspect and repair declared host operations.",
            children=(
                _node(
                    "config",
                    "Inventory and safely export operator-owned configuration.",
                    children=(
                        _resource_node(
                            "inventory",
                            "Classify operator-home files and verify dependency closure.",
                            "anvil_serving.operator_config",
                            role="host",
                            argv_prefix=("inventory",),
                            options=(
                                _option(
                                    "--home",
                                    summary="Operator config home to inspect.",
                                    value_name="PATH",
                                ),
                                _option(
                                    "--max-bytes",
                                    summary="Maximum accepted candidate size.",
                                    value_name="BYTES",
                                ),
                            ),
                            remote_operation=_remote(
                                "operator_config_inventory",
                                allowed=("max_bytes",),
                                max_response_bytes=1024 * 1024,
                            ),
                            execution_runtime_roles=("native",),
                            docs_anchor=(
                                "docs/cli/host.md#operator-configuration-inventory-and-export"
                            ),
                        ),
                        _resource_node(
                            "export",
                            "Export safe config and sanitized Anvil-owned gateway fragments.",
                            "anvil_serving.operator_config",
                            role="host",
                            argv_prefix=("export",),
                            options=(
                                _option(
                                    "--home",
                                    summary="Operator config home to export.",
                                    value_name="PATH",
                                ),
                                _option(
                                    "--gateway-path",
                                    summary="OpenClaw JSON source for the Anvil-owned fragment.",
                                    value_name="PATH",
                                ),
                                _option(
                                    "--path",
                                    summary="Relative config path to export; repeat for a safe subset.",
                                    value_name="RELATIVE_PATH",
                                ),
                                _option(
                                    "--max-bytes",
                                    summary="Maximum accepted candidate size.",
                                    value_name="BYTES",
                                ),
                            ),
                            remote_operation=_remote(
                                "operator_config_export",
                                allowed=("max_bytes", "paths"),
                                aliases=(("path", "paths"),),
                                max_response_bytes=1024 * 1024,
                            ),
                            execution_runtime_roles=("native",),
                            docs_anchor=(
                                "docs/cli/host.md#operator-configuration-inventory-and-export"
                            ),
                        ),
                    ),
                    docs_anchor=(
                        "docs/cli/host.md#operator-configuration-inventory-and-export"
                    ),
                ),
                _resource_node(
                    "status",
                    "Show structured host status.",
                    "anvil_serving.host",
                    role="host",
                    remote_operation=_remote("host_summary"),
                    execution_runtime_roles=("native",),
                    docs_anchor="docs/cli/host.md#inspect-the-host",
                ),
                _resource_node(
                    "gpus",
                    "Show GPU inventory.",
                    "anvil_serving.gpus",
                    role="host",
                    argv_prefix=(),
                    remote_operation=_remote("gpu_inventory"),
                    execution_runtime_roles=("native", "docker"),
                    docs_anchor="docs/cli/host.md#inspect-the-host",
                ),
                _node(
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
                            execution_runtime_roles=("native",),
                        ),
                        _resource_node(
                            "probe",
                            "Run the guarded Docker CUDA prerequisite probe.",
                            "anvil_serving.gpu_sharing",
                            role="host",
                            options=CONFIRM_OPTIONS,
                            mutation="mutate",
                            argv_prefix=("probe",),
                            execution_runtime_roles=("native",),
                        ),
                    ),
                    docs_anchor="docs/cli/host.md#gpu-sharing",
                ),
                _resource_node(
                    "doctor",
                    "Diagnose host configuration.",
                    "anvil_serving.host",
                    role="host",
                    remote_operation=_remote("host_summary"),
                    execution_runtime_roles=("native",),
                    docs_anchor="docs/cli/host.md#inspect-the-host",
                ),
                _resource_node(
                    "memory",
                    "Show host RAM and WSL VM memory usage.",
                    "anvil_serving.host",
                    role="host",
                    execution_runtime_roles=("native",),
                    execution_host_os=("windows",),
                    docs_anchor="docs/cli/host.md#inspect-the-host",
                ),
                _node(
                    "shared-memory",
                    "Inspect and reclaim vLLM native KV-offload shared memory.",
                    children=(
                        _resource_node(
                            "status",
                            "Inspect ownership of vLLM offload mmap files.",
                            "anvil_serving.host",
                            role="host",
                            argv_prefix=("shared-memory-status",),
                            remote_operation=_remote("host_shared_memory"),
                            execution_runtime_roles=("native",),
                            docs_anchor="docs/cli/host.md#repair-the-host",
                        ),
                        _resource_node(
                            "reclaim",
                            "Remove only twice-verified orphan vLLM offload mmap files.",
                            "anvil_serving.host",
                            role="host",
                            argv_prefix=("shared-memory-reclaim",),
                            options=CONFIRM_OPTIONS,
                            mutation="mutate",
                            remote_operation=_remote(
                                "host_manage",
                                fixed=(("action", "reclaim-shared-memory"),),
                                confirmed=(("confirm", True),),
                                allowed=("dry_run",),
                            ),
                            execution_runtime_roles=("native",),
                            docs_anchor="docs/cli/host.md#repair-the-host",
                        ),
                    ),
                    docs_anchor="docs/cli/host.md#repair-the-host",
                ),
                _resource_node(
                    "wsl-config",
                    "Render or update WSL configuration.",
                    "anvil_serving.host",
                    role="host",
                    options=CONFIRM_OPTIONS,
                    mutation="mutate",
                    remote_operation=_remote(
                        "host_manage",
                        fixed=(("action", "wsl-config"),),
                        confirmed=(("confirm", True),),
                        allowed=("memory", "swap", "revert", "force", "dry_run"),
                    ),
                    execution_runtime_roles=("native",),
                    execution_host_os=("windows",),
                    docs_anchor="docs/cli/host.md#repair-the-host",
                ),
                _resource_node(
                    "restart-docker",
                    "Restart Docker Desktop.",
                    "anvil_serving.host",
                    role="host",
                    options=CONFIRM_OPTIONS,
                    mutation="mutate",
                    recovery=True,
                    remote_operation=_remote(
                        "host_manage",
                        fixed=(("action", "restart-docker"),),
                        confirmed=(("confirm", True),),
                        allowed=("dry_run",),
                    ),
                    execution_runtime_roles=("native",),
                    execution_host_os=("windows", "macos"),
                    docs_anchor="docs/cli/host.md#repair-the-host",
                ),
                _resource_node(
                    "reset-wsl",
                    "Reset WSL.",
                    "anvil_serving.host",
                    role="host",
                    options=CONFIRM_OPTIONS,
                    mutation="mutate",
                    recovery=True,
                    remote_operation=_remote(
                        "host_manage",
                        fixed=(("action", "reset-wsl"),),
                        confirmed=(("confirm", True),),
                        allowed=("dry_run",),
                    ),
                    execution_runtime_roles=("native",),
                    execution_host_os=("windows",),
                    docs_anchor="docs/cli/host.md#repair-the-host",
                ),
                _resource_node(
                    "reclaim",
                    "Drop the WSL VM page cache.",
                    "anvil_serving.host",
                    role="host",
                    options=CONFIRM_OPTIONS
                    + (
                        _option(
                            "--watch",
                            summary="Foreground reclaim watchdog loop.",
                            output_policy="follow",
                        ),
                    ),
                    mutation="mutate",
                    execution_runtime_roles=("native",),
                    execution_host_os=("windows",),
                    docs_anchor="docs/cli/host.md#repair-the-host",
                ),
            ),
            docs_anchor="docs/cli/host.md#command-map",
        ),
        _resource_node(
            "doctor",
            "Check dependencies and configured health.",
            "anvil_serving.doctor",
            role="host",
            argv_prefix=(),
            remote_operation=_remote("doctor_summary"),
            execution_runtime_roles=("native",),
            docs_anchor="docs/cli/host.md#doctor",
        ),
        _node(
            "upgrade",
            "Upgrade this CLI to the newest stable published release.",
            options=CONFIRM_OPTIONS,
            handler=_handler("anvil_serving.upgrade"),
            mutation_class="mutate",
            docs_anchor="docs/cli/host.md#upgrade",
        ),
        _node(
            "dashboard",
            "Serve the read-only system observability dashboard.",
            children=(
                _resource_node(
                    "serve",
                    "Serve the packaged local dashboard.",
                    "anvil_serving.observability.dashboard.app",
                    role="host",
                    mutation="process",
                    argv_prefix=(),
                    output_policy="foreground",
                    execution_runtime_roles=("native",),
                ),
            ),
            docs_anchor="docs/cli/host.md#dashboard",
        ),
    )
