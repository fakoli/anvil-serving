"""Command declarations for managed media operations."""

from .common import CONFIRM_OPTIONS
from .family import command_family
from .spec import CommandNode, _node, _option, _resource_node


REGISTRY_OPTION = (_option("--registry", summary="Pinned media workflow registry.", value_name="PATH"),)
STATE_OPTIONS = REGISTRY_OPTION + (
    _option("--state-db", summary="Durable media job-state database.", value_name="PATH"),
    _option("--artifact-root", summary="Opaque media artifact root.", value_name="PATH"),
)
IDENTITY_OPTIONS = STATE_OPTIONS + (
    _option("--principal", summary="Authenticated media principal.", value_name="ID"),
)
BUNDLE_IDENTITY_OPTIONS = (
    _option("--version", summary="Pinned workflow version.", value_name="VERSION"),
    _option("--bundle-lock", summary="Pinned media bundle lock.", value_name="PATH"),
    _option("--models-volume", summary="Protected ComfyUI model volume.", value_name="NAME"),
)


@command_family(category="Control plane & integrations")
def commands() -> CommandNode:
    return _node(
        "media",
        "Inspect and run bounded managed media workflows.",
        children=(
            _resource_node(
                "capabilities",
                "List deterministic media capabilities.",
                "anvil_serving.media.cli",
                role="media-gateway",
                options=STATE_OPTIONS,
                argv_prefix=("capabilities",),
            ),
            _node(
                "bundle",
                "Inventory and stage exact pinned workflow assets.",
                children=(
                    _resource_node(
                        "inventory", "Verify exact model assets for one workflow.",
                        "anvil_serving.media.cli", role="media-worker",
                        options=BUNDLE_IDENTITY_OPTIONS,
                        argv_prefix=("bundle", "inventory"),
                    ),
                    _resource_node(
                        "stage", "Add missing exact model assets without replacing existing files.",
                        "anvil_serving.media.cli", role="media-worker", mutation="mutate",
                        options=CONFIRM_OPTIONS + BUNDLE_IDENTITY_OPTIONS + (
                            _option("--user-volume", summary="Protected ComfyUI user/output volume.", value_name="NAME"),
                            _option("--runtime-uid", summary="Pinned worker numeric user.", value_name="UID"),
                            _option("--runtime-gid", summary="Pinned worker numeric group.", value_name="GID"),
                        ),
                        argv_prefix=("bundle", "stage"),
                    ),
                ),
            ),
            _node(
                "qualify",
                "Qualify an unavailable pinned workflow without promoting it.",
                children=(
                    _resource_node(
                        "run", "Run functional and capacity qualification on the selected worker.",
                        "anvil_serving.media.cli", role="media-worker", mutation="mutate", gpu=True,
                        options=CONFIRM_OPTIONS + IDENTITY_OPTIONS + BUNDLE_IDENTITY_OPTIONS + (
                            _option("--parameters", summary="Bounded JSON parameter object.", value_name="JSON"),
                            _option("--backend-url", summary="Declared adapter endpoint.", value_name="URL"),
                            _option("--gpu-index", summary="Selected local GPU index.", value_name="INDEX"),
                            _option("--poll-seconds", summary="Bounded qualification poll interval.", value_name="SECONDS"),
                            _option("--ffprobe", summary="Video decoder probe executable.", value_name="PATH"),
                        ),
                        argv_prefix=("qualify", "run"),
                    ),
                ),
            ),
            _node(
                "workflow",
                "Inspect, validate, and run named workflows.",
                children=(
                    _resource_node(
                        "list", "List named workflows.", "anvil_serving.media.cli",
                        role="media-gateway", options=STATE_OPTIONS,
                        argv_prefix=("workflow", "list"),
                    ),
                    _resource_node(
                        "show", "Show one named workflow.", "anvil_serving.media.cli",
                        role="media-gateway", options=STATE_OPTIONS + (_option("--version", summary="Pinned workflow version.", value_name="VERSION"),),
                        argv_prefix=("workflow", "show"),
                    ),
                    _resource_node(
                        "validate", "Validate one workflow against its selected worker.", "anvil_serving.media.cli",
                        role="media-worker", gpu=True,
                        options=STATE_OPTIONS + (
                            _option("--version", summary="Pinned workflow version.", value_name="VERSION"),
                            _option("--backend-url", summary="Declared adapter endpoint.", value_name="URL"),
                        ),
                        argv_prefix=("workflow", "validate"),
                    ),
                    _resource_node(
                        "run", "Submit one bounded named media workflow.", "anvil_serving.media.cli",
                        role="media-worker", gpu=True, mutation="mutate",
                        options=CONFIRM_OPTIONS + IDENTITY_OPTIONS + (
                            _option("--version", summary="Pinned workflow version.", value_name="VERSION"),
                            _option("--parameters", summary="Bounded JSON parameter object.", value_name="JSON"),
                            _option("--idempotency-key", summary="Caller retry key.", value_name="KEY"),
                            _option("--backend-url", summary="Declared adapter endpoint.", value_name="URL"),
                        ),
                        argv_prefix=("workflow", "run"),
                    ),
                ),
            ),
            _node(
                "job", "Inspect and cancel durable media jobs.",
                children=(
                    _resource_node(
                        "status", "Inspect one durable media job.", "anvil_serving.media.cli",
                        role="media-gateway", options=IDENTITY_OPTIONS,
                        argv_prefix=("job", "status"),
                    ),
                    _resource_node(
                        "cancel", "Cancel one caller-owned media job.", "anvil_serving.media.cli",
                        role="media-gateway", mutation="mutate", options=CONFIRM_OPTIONS + IDENTITY_OPTIONS + (
                            _option("--backend-url", summary="Declared adapter endpoint.", value_name="URL"),
                        ),
                        argv_prefix=("job", "cancel"),
                    ),
                ),
            ),
            _node(
                "artifact", "Inspect authenticated media artifacts.",
                children=(
                    _resource_node(
                        "inspect", "Inspect opaque artifact metadata.", "anvil_serving.media.cli",
                        role="media-gateway", options=IDENTITY_OPTIONS,
                        argv_prefix=("artifact", "inspect"),
                    ),
                ),
            ),
        ),
        docs_anchor="docs/CLI.md#media",
    )
