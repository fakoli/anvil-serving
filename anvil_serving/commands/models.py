"""Command declarations for the models family."""

from .family import command_family
from .common import CONFIRM_OPTIONS
from .spec import CommandNode, _node, _option, _resource_node


@command_family(category="Local serving tools")
def commands() -> CommandNode:
    return _node(
        "models",
        "Manage model catalog, artifacts, and recipes.",
        children=(
            _resource_node(
                "sync",
                "Sync the model catalog.",
                "anvil_serving.models",
                role="model-catalog",
                options=CONFIRM_OPTIONS,
                mutation="mutate",
                docs_anchor="docs/cli/models.md#catalog-sync",
            ),
            _resource_node(
                "pull",
                "Pull a model artifact.",
                "anvil_serving.models",
                role="model-catalog",
                options=CONFIRM_OPTIONS,
                mutation="mutate",
                docs_anchor="docs/cli/models.md#artifact-pull",
            ),
            _resource_node(
                "score",
                "Rank models from benchmark evidence.",
                "anvil_serving.models",
                role="model-catalog",
                docs_anchor="docs/cli/models.md#model-scoring",
            ),
            _node(
                "recipes",
                "Manage recorded serve recipes.",
                children=(
                    _resource_node(
                        "list",
                        "List recorded serve recipes.",
                        "anvil_serving.models",
                        role="model-catalog",
                        argv_prefix=("recipe", "list"),
                        docs_anchor="docs/cli/models.md#discover-recipes",
                    ),
                    _resource_node(
                        "show",
                        "Show one recorded serve recipe.",
                        "anvil_serving.models",
                        role="model-catalog",
                        argv_prefix=("recipe", "show"),
                        docs_anchor="docs/cli/models.md#discover-recipes",
                    ),
                    _resource_node(
                        "create",
                        "Create one recipe in an operator registry.",
                        "anvil_serving.models",
                        role="model-catalog",
                        options=CONFIRM_OPTIONS,
                        mutation="mutate",
                        argv_prefix=("recipe", "create"),
                        docs_anchor="docs/cli/models.md#create-update-or-delete-a-recipe",
                    ),
                    _resource_node(
                        "update",
                        "Update one selected recipe.",
                        "anvil_serving.models",
                        role="model-catalog",
                        options=CONFIRM_OPTIONS,
                        mutation="mutate",
                        argv_prefix=("recipe", "update"),
                        docs_anchor="docs/cli/models.md#create-update-or-delete-a-recipe",
                    ),
                    _resource_node(
                        "delete",
                        "Delete one selected recipe.",
                        "anvil_serving.models",
                        role="model-catalog",
                        options=CONFIRM_OPTIONS,
                        mutation="mutate",
                        argv_prefix=("recipe", "delete"),
                        docs_anchor="docs/cli/models.md#create-update-or-delete-a-recipe",
                    ),
                    _resource_node(
                        "load",
                        "Load one recipe into a named local container.",
                        "anvil_serving.models",
                        role="model-serve",
                        options=CONFIRM_OPTIONS,
                        mutation="mutate",
                        gpu=True,
                        argv_prefix=("recipe", "load"),
                        docs_anchor="docs/cli/models.md#load-a-recipe",
                    ),
                    _resource_node(
                        "status",
                        "Inspect one recipe-loaded candidate container.",
                        "anvil_serving.models",
                        role="model-serve",
                        argv_prefix=("recipe", "status"),
                        docs_anchor="docs/cli/models.md#operate-a-loaded-recipe",
                    ),
                    _resource_node(
                        "logs",
                        "Read bounded logs from one recipe-loaded candidate container.",
                        "anvil_serving.models",
                        role="model-serve",
                        options=(
                            _option(
                                "--tail",
                                summary="Read this many trailing lines, from 1 through 5000.",
                                value_name="LINES",
                            ),
                            _option(
                                "--since",
                                summary="Read logs since one timestamp or relative duration.",
                                value_name="WHEN",
                            ),
                            _option(
                                "--contains",
                                summary="Emit lines containing a case-insensitive literal; repeat for OR matching.",
                                value_name="TEXT",
                            ),
                        ),
                        argv_prefix=("recipe", "logs"),
                        docs_anchor="docs/cli/models.md#operate-a-loaded-recipe",
                    ),
                    _resource_node(
                        "unload",
                        "Remove one exact recipe-loaded candidate container.",
                        "anvil_serving.models",
                        role="model-serve",
                        options=CONFIRM_OPTIONS,
                        mutation="mutate",
                        argv_prefix=("recipe", "unload"),
                        docs_anchor="docs/cli/models.md#operate-a-loaded-recipe",
                    ),
                ),
                docs_anchor="docs/cli/models.md#recipes",
            ),
            _node(
                "cache",
                "Manage model cache storage.",
                children=(
                    _resource_node(
                        "inventory",
                        "Inspect Docker model-cache storage.",
                        "anvil_serving.models",
                        role="model-catalog",
                        docs_anchor="docs/cli/models.md#cache-inventory",
                    ),
                    _resource_node(
                        "prune",
                        "Plan or prune the model cache.",
                        "anvil_serving.models",
                        role="model-catalog",
                        options=CONFIRM_OPTIONS
                        + (
                            _option(
                                "--execute",
                                summary="Delete the planned cache candidates.",
                                requires_confirmation=True,
                            ),
                        ),
                        mutation="mutate",
                    ),
                    _resource_node(
                        "remove",
                        "Remove one exact cached repository revision.",
                        "anvil_serving.models",
                        role="model-catalog",
                        options=CONFIRM_OPTIONS,
                        mutation="mutate",
                        docs_anchor="docs/cli/models.md#cache-remove",
                    ),
                ),
                docs_anchor="docs/cli/models.md#cache-prune",
            ),
        ),
        docs_anchor="docs/cli/models.md",
    )
