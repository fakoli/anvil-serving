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
                        role="media-gateway", mutation="mutate", options=CONFIRM_OPTIONS + IDENTITY_OPTIONS,
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
