import re
from pathlib import Path

import pytest

from anvil_serving import mcp
from anvil_serving.command_tree import (
    COMMAND_TREE,
    CommandExample,
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
    assert manifest_data()["schema_version"] == 3


def test_visible_commands_link_to_existing_reference_pages_and_headings():
    root = MANIFEST_PATH.parent.parent
    headings_by_path: dict[Path, set[str]] = {}

    for record in manifest_data()["commands"]:
        if not record["visible"]:
            continue
        relative, _, fragment = record["docs_anchor"].partition("#")
        path = root / relative
        assert path.is_file(), f"{record['path']} links to missing docs page {relative}"
        if not fragment:
            continue
        if path not in headings_by_path:
            slugs = set()
            for line in path.read_text(encoding="utf-8").splitlines():
                match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
                if not match:
                    continue
                slug = re.sub(r"[^\w -]", "", match.group(1).casefold())
                slugs.add(re.sub(r"[\s]+", "-", slug).strip("-"))
            headings_by_path[path] = slugs
        assert fragment in headings_by_path[path], (
            f"{record['path']} links to missing heading #{fragment} in {relative}"
        )


def test_manifest_records_recursive_paths_metadata_and_tombstones():
    records = {record["path"]: record for record in manifest_data()["commands"]}

    assert "eval benchmark external compare" in records
    assert records["voice audio up"]["resource_role"] == "stt-serve"
    assert records["voice audio up"]["coowned_resource_roles"] == ["tts-serve"]
    assert records["voice audio status"]["remote_operation"]["tool"] == "voice_manage"
    assert records["voice audio logs"]["output_policy"] == "bounded"
    assert records["voice proxy run"]["mutation_class"] == "process"
    assert records["voice proxy run"]["resource_role"] == "realtime-proxy"
    assert records["voice proxy run"]["coowned_resource_roles"] == [
        "stt-proxy", "tts-proxy"
    ]
    assert records["voice proxy up"]["remote_operation"]["tool"] == "voice_proxy_manage"
    assert records["voice proxy logs"]["output_policy"] == "bounded"
    assert records["serves render"]["gpu_role_required"] is True
    assert records["serves render"]["examples"]
    assert records["serves render"]["configuration_notes"]
    assert records["serves render"]["behavior_notes"]
    assert not any(
        "--dry-run" in option["flags"]
        for option in records["serves render"]["options"]
    )
    for path in ("serves rm", "serves adopt"):
        removed_yes = next(
            option for option in records[path]["options"] if "--yes" in option["flags"]
        )
        assert removed_yes["tombstone"]["replacement"] == "--confirm"
    assert records["serve"]["tombstone"]["replacement"] == "router run"
    assert records["mcp"]["handler"] is None
    assert records["mcp"]["tombstone"]["replacement"] == "mcp serve"
    assert records["mcp serve"]["handler"] == "anvil_serving.mcp:main"
    assert records["router status"]["remote_operation"]["tool"] == "router_status"
    assert records["router endpoint"]["handler"] == "anvil_serving.router_endpoint:main"
    assert records["router endpoint"]["execution_runtime_roles"] == ["native"]
    assert records["router endpoint"]["remote_operation"] is None
    assert records["eval preflight"]["mutation_class"] == "mutate"
    assert records["eval preflight"]["remote_operation"]["tool"] == "preflight_probe"
    assert records["eval preflight"]["remote_operation"]["confirmed_arguments"] == {
        "confirm": True
    }
    assert {
        "allowed_finish_reasons",
        "dry_run",
        "reasoning_effort",
        "timeout_seconds",
    } <= set(records["eval preflight"]["remote_operation"]["allowed_arguments"])
    assert records["eval benchmark capacity"]["remote_operation"] is None
    assert records["eval benchmark quality"]["remote_operation"] is None
    assert records["eval benchmark run"]["tombstone"]["replacement"] == (
        "eval benchmark capacity or eval benchmark quality"
    )
    assert records["eval benchmark external export"]["mutation_class"] == "mutate"
    assert records["harness sync openclaw"]["remote_operation"]["tool"] == "openclaw_sync"
    assert records["harness restart openclaw"]["recovery_capable"] is True
    assert records["host wsl-config"]["execution_host_os"] == ["windows"]
    assert records["host restart-docker"]["execution_host_os"] == ["windows", "macos"]
    assert records["host reset-wsl"]["execution_host_os"] == ["windows"]
    assert records["host status"]["remote_operation"]["tool"] == "host_summary"
    assert records["host gpu-sharing inspect"]["mutation_class"] == "read"
    assert records["host gpu-sharing inspect"]["handler"] == "anvil_serving.gpu_sharing:main"
    assert records["host gpu-sharing inspect"]["execution_runtime_roles"] == ["native"]
    assert records["host gpu-sharing probe"]["mutation_class"] == "mutate"
    assert records["host gpu-sharing probe"]["handler"] == "anvil_serving.gpu_sharing:main"
    assert {flag for option in records["host gpu-sharing probe"]["options"] for flag in option["flags"]} >= {
        "--confirm",
        "--dry-run",
        "--gpu-uuid",
    }
    assert records["doctor"]["remote_operation"]["tool"] == "doctor_summary"
    assert records["upgrade"]["handler"] == "anvil_serving.upgrade:main"
    assert records["upgrade"]["mutation_class"] == "mutate"
    assert {flag for option in records["upgrade"]["options"] for flag in option["flags"]} >= {
        "--allow-editable",
        "--confirm",
        "--dry-run",
        "--manager",
    }
    assert {"topology show", "topology validate", "topology resolve"} <= records.keys()
    assert records["harness status openclaw"]["remote_operation"] == {
        "mode": "tool",
        "tool": "openclaw_gateway_status",
        "fixed_arguments": {},
        "confirmed_arguments": {},
        "allowed_arguments": ["timeout_seconds", "max_output_bytes"],
        "positional_arguments": [],
    }
    for path in ("eval bootstrap", "eval calibrate"):
        assert any(
            "--dry-run" in option["flags"] for option in records[path]["options"]
        )
    assert any(
        "--dry-run" in option["flags"]
        for option in records["eval benchmark external import"]["options"]
    )
    assert records["router run"]["remote_operation"] is None
    assert records["controller status"]["remote_operation"]["mode"] == "controller-status"
    global_flags = {
        flag
        for option in records["controller status"]["options"]
        for flag in option["flags"]
    }
    assert "--experimental-model-workload" in global_flags
    assert "--allow-ssh-fallback" in global_flags


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


def test_remote_command_tools_exist_in_the_mcp_catalog():
    remote_tools = {
        record["remote_operation"]["tool"]
        for record in manifest_data()["commands"]
        if record["remote_operation"] is not None
        and record["remote_operation"]["mode"] == "tool"
    }

    assert remote_tools <= set(mcp.TOOLS)


def test_repo_workbench_surfaces_catalog_current_mcp_tools_and_cli_gaps():
    root = MANIFEST_PATH.parent.parent
    catalog_paths = (
        root / ".agents" / "skills" / "anvil-serving-workbench" / "SKILL.md",
        root / ".claude" / "skills" / "anvil-serving-workbench" / "SKILL.md",
        root / "examples" / "openclaw" / "skills" / "anvil-serving-workbench" / "SKILL.md",
        root / "docs" / "OPERATOR-SKILLS-AND-SUBAGENTS.md",
        root / "CLAUDE.md",
    )

    for path in catalog_paths:
        text = path.read_text(encoding="utf-8")
        missing = {
            name
            for name in mcp.TOOLS
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text)
            is None
        }
        assert not missing, f"{path.relative_to(root)} omits MCP tools: {sorted(missing)}"

    for path in catalog_paths[:3]:
        text = path.read_text(encoding="utf-8")
        for command in (
            "models recipes list/show",
            "models pull",
            "serves switch ROLE [MODEL]",
            "eval benchmark quality",
        ):
            assert command in text, f"{path.relative_to(root)} omits {command!r}"

    voice_text = (
        root / "skills" / "anvil-serving-voice-ops" / "SKILL.md"
    ).read_text(encoding="utf-8")
    for token in (
        "voice_manage",
        "voice_proxy_manage",
        "workflow_packet_validate",
        "voice profiles validate",
        "--candidate-overlay",
        "eval benchmark quality",
    ):
        assert token in voice_text


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        (
            "examples",
            ("not-an-example",),
            "must be a CommandExample",
        ),
        (
            "examples",
            (CommandExample("anvil-serving init", "broken\nsummary"),),
            "summary must be one line",
        ),
        (
            "configuration_notes",
            (None,),
            "configuration notes must be non-empty one-line text",
        ),
        (
            "configuration_notes",
            ("broken\nconfiguration",),
            "configuration notes must be non-empty one-line text",
        ),
        (
            "behavior_notes",
            ("broken\nbehavior",),
            "behavior notes must be non-empty one-line text",
        ),
    ),
)
def test_reviewed_help_metadata_must_be_one_line(field, value, message):
    node = CommandNode(
        "init",
        "Initialize.",
        handler=HandlerRef("anvil_serving.init"),
        **{field: value},
    )

    with pytest.raises(CommandTreeError, match=message):
        validate_command_tree(CommandTree(nodes=(node,), global_options=()))
