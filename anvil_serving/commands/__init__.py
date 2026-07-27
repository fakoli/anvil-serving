"""Canonical modular command registry."""

from .family import CommandFamily, command_family
from .registry import COMMAND_TREE, FAMILIES, build_command_tree
from .spec import (
    MANIFEST_PATH,
    MANIFEST_SCHEMA_VERSION,
    CommandNode,
    CommandOption,
    CommandTree,
    CommandTreeError,
    HandlerRef,
    RemoteOperation,
    manifest_data,
    manifest_matches,
    render_manifest,
    validate_command_tree,
    write_manifest,
)

__all__ = [
    "COMMAND_TREE",
    "FAMILIES",
    "MANIFEST_PATH",
    "MANIFEST_SCHEMA_VERSION",
    "CommandFamily",
    "CommandNode",
    "CommandOption",
    "CommandTree",
    "CommandTreeError",
    "HandlerRef",
    "RemoteOperation",
    "build_command_tree",
    "command_family",
    "manifest_data",
    "manifest_matches",
    "render_manifest",
    "validate_command_tree",
    "write_manifest",
]
