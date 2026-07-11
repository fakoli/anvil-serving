from pathlib import Path

import pytest

from anvil_serving.command_tree import (
    COMMAND_TREE,
    CommandNode,
    CommandOption,
    CommandTree,
    CommandTreeError,
    HandlerRef,
    MANIFEST_PATH,
    manifest_data,
    manifest_matches,
    render_manifest,
    validate_command_tree,
)


def test_manifest_is_checked_in_and_matches_deterministic_regeneration():
    assert manifest_matches()
    assert MANIFEST_PATH.read_bytes() == render_manifest()


def test_manifest_is_byte_stable():
    assert render_manifest() == render_manifest()
    assert manifest_data()["schema_version"] == 1


def test_manifest_records_recursive_paths_metadata_and_tombstones():
    records = {record["path"]: record for record in manifest_data()["commands"]}

    assert "eval benchmark external compare" in records
    assert records["voice audio up"]["resource_role"] == "audio"
    assert records["voice proxy run"]["mutation_class"] == "process"
    assert records["serves render"]["gpu_role_required"] is True
    assert records["serve"]["tombstone"]["replacement"] == "router run"
    assert records["mcp"]["handler"] is None
    assert records["mcp"]["tombstone"]["replacement"] == "mcp serve"
    assert records["mcp serve"]["handler"] == "anvil_serving.mcp:main"
    global_flags = {
        flag
        for option in records["controller status"]["options"]
        for flag in option["flags"]
    }
    assert "--experimental-model-workload" in global_flags


def test_duplicate_paths_fail_validation():
    duplicate = CommandTree(
        nodes=(
            CommandNode("init", "One.", handler=HandlerRef("anvil_serving.init")),
            CommandNode("init", "Two.", handler=HandlerRef("anvil_serving.init")),
        ),
        global_options=(),
    )

    with pytest.raises(CommandTreeError, match="duplicate command path"):
        validate_command_tree(duplicate)


def test_duplicate_options_fail_validation():
    duplicate = CommandTree(
        nodes=(CommandNode("init", "Initialize.", handler=HandlerRef("anvil_serving.init")),),
        global_options=(
            CommandOption(("--json",), "JSON."),
            CommandOption(("--json",), "Duplicate JSON."),
        ),
    )

    with pytest.raises(CommandTreeError, match="duplicate option"):
        validate_command_tree(duplicate)


def test_duplicate_inherited_option_fails_validation():
    duplicate = CommandTree(
        nodes=(
            CommandNode(
                "group",
                "Group.",
                children=(
                    CommandNode(
                        "child",
                        "Child.",
                        handler=HandlerRef("anvil_serving.init"),
                        options=(CommandOption(("--json",), "Duplicate JSON."),),
                    ),
                ),
            ),
        ),
        global_options=(CommandOption(("--json",), "JSON."),),
    )

    with pytest.raises(CommandTreeError, match="duplicate option"):
        validate_command_tree(duplicate)


def test_unresolved_handler_fails_validation():
    invalid = CommandTree(
        nodes=(CommandNode("missing", "Missing.", handler=HandlerRef("anvil_serving.no_such_module")),),
        global_options=(),
    )

    with pytest.raises(CommandTreeError, match="unresolved handler"):
        validate_command_tree(invalid)


def test_manifest_drift_is_detected(tmp_path: Path):
    path = tmp_path / "manifest.json"
    path.write_bytes(render_manifest() + b"drift")

    assert not manifest_matches(path)


def test_declared_tree_is_valid():
    validate_command_tree(COMMAND_TREE)
