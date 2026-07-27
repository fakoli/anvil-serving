"""Shared command options used across command families."""

from .spec import _option


ACTION_OPTIONS = (_option("--dry-run", summary="Preview without mutating state."),)
CONFIRM_OPTIONS = ACTION_OPTIONS + (_option("--confirm", summary="Confirm the guarded mutation."),)
MANIFEST_OPTION = _option(
    "--manifest",
    summary="Serve manifest TOML.",
    value_name="PATH",
)
RECIPE_REGISTRY_OPTION = _option(
    "--registry",
    summary="Serve-recipe registry TOML.",
    value_name="PATH",
)
RECIPE_FILE_OPTION = _option(
    "--recipe-file",
    summary="TOML file containing one recipe.",
    value_name="PATH",
)
RECIPE_CONTAINER_OPTION = _option(
    "--container",
    summary="New Docker container name.",
    value_name="NAME",
)
GROUP_OPTION = _option(
    "--group",
    summary=(
        "Act on every serve tagged NAME across the manifest set "
        "(repeatable; 'all' selects every serve)."
    ),
    value_name="NAME",
)
