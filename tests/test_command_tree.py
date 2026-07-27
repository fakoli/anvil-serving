import re
import subprocess
import sys
from pathlib import Path

import pytest

from anvil_serving import cli, mcp
from anvil_serving.commands import (
    COMMAND_TREE,
    FAMILIES,
    CommandNode,
    CommandOption,
    CommandTree,
    CommandTreeError,
    HandlerRef,
    MANIFEST_PATH,
    build_command_tree,
    command_family,
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
    assert manifest_data()["schema_version"] == 4


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


def test_manifest_records_recursive_paths_and_metadata():
    records = {record["path"]: record for record in manifest_data()["commands"]}

    assert "eval benchmark external compare" in records
    assert records["voice audio up"]["resource_role"] == "stt-serve"
    assert records["voice audio up"]["coowned_resource_roles"] == ["tts-serve"]
    assert records["voice audio status"]["remote_operation"]["tool"] == "voice_manage"
    assert records["voice audio logs"]["output_policy"] == "bounded"
    assert records["voice proxy run"]["mutation_class"] == "process"
    assert records["voice proxy run"]["resource_role"] == "realtime-proxy"
    assert records["voice proxy run"]["coowned_resource_roles"] == ["stt-proxy", "tts-proxy"]
    assert records["voice proxy up"]["remote_operation"]["tool"] == "voice_proxy_manage"
    assert records["voice proxy logs"]["output_policy"] == "bounded"
    assert records["serves render"]["gpu_role_required"] is True
    assert "examples" not in records["serves render"]
    assert "configuration_notes" not in records["serves render"]
    assert "behavior_notes" not in records["serves render"]
    assert not any("--dry-run" in option["flags"] for option in records["serves render"]["options"])
    assert records["mcp"]["handler"] is None
    assert records["mcp serve"]["handler"] == "anvil_serving.mcp:main"
    assert records["router status"]["remote_operation"]["tool"] == "router_status"
    assert records["router endpoint"]["handler"] == "anvil_serving.router_endpoint:main"
    assert records["router endpoint"]["execution_runtime_roles"] == ["native"]
    assert records["router endpoint"]["remote_operation"] is None
    assert records["eval preflight"]["mutation_class"] == "mutate"
    assert records["eval preflight"]["remote_operation"]["tool"] == "preflight_probe"
    assert records["eval preflight"]["remote_operation"]["confirmed_arguments"] == {"confirm": True}
    assert {
        "allowed_finish_reasons",
        "dry_run",
        "reasoning_effort",
        "timeout_seconds",
    } <= set(records["eval preflight"]["remote_operation"]["allowed_arguments"])
    assert records["eval benchmark capacity"]["remote_operation"] is None
    assert records["eval benchmark quality"]["remote_operation"] is None
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
    gpu_probe_flags = {
        flag for option in records["host gpu-sharing probe"]["options"] for flag in option["flags"]
    }
    assert {"--confirm", "--dry-run"} <= gpu_probe_flags
    assert "--gpu-uuid" not in gpu_probe_flags
    assert records["doctor"]["remote_operation"]["tool"] == "doctor_summary"
    assert records["upgrade"]["handler"] == "anvil_serving.upgrade:main"
    assert records["upgrade"]["mutation_class"] == "mutate"
    upgrade_flags = {flag for option in records["upgrade"]["options"] for flag in option["flags"]}
    assert {"--confirm", "--dry-run"} <= upgrade_flags
    assert "--manager" not in upgrade_flags
    assert "--allow-editable" not in upgrade_flags
    assert {"topology show", "topology validate", "topology resolve"} <= records.keys()
    assert records["harness status openclaw"]["remote_operation"] == {
        "mode": "tool",
        "tool": "openclaw_gateway_status",
        "fixed_arguments": {},
        "confirmed_arguments": {},
        "allowed_arguments": ["timeout_seconds", "max_output_bytes"],
        "positional_arguments": [],
    }
    assert any(
        "--dry-run" in option["flags"]
        for option in records["eval benchmark external import"]["options"]
    )
    assert records["router run"]["remote_operation"] is None
    assert records["controller status"]["remote_operation"]["mode"] == "controller-status"
    global_flags = {
        flag for option in records["controller status"]["options"] for flag in option["flags"]
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
        nodes=(
            CommandNode("missing", "Missing.", handler=HandlerRef("anvil_serving.no_such_module")),
        ),
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
        if record["remote_operation"] is not None and record["remote_operation"]["mode"] == "tool"
    }

    assert remote_tools <= set(mcp.TOOLS)


def test_remote_command_arguments_exist_in_the_mcp_tool_schemas():
    for record in manifest_data()["commands"]:
        remote = record["remote_operation"]
        if remote is None or remote["mode"] != "tool":
            continue
        properties = set(mcp.TOOLS[remote["tool"]]["inputSchema"]["properties"])
        declared = set(remote["allowed_arguments"])
        declared.update(remote["confirmed_arguments"])
        missing = declared - properties
        assert not missing, (
            f"{record['path']} declares arguments absent from {remote['tool']}: {sorted(missing)}"
        )


@pytest.mark.parametrize(
    "argv",
    (
        [
            "router",
            "up",
            "--compose",
            "deployment.yml",
            "--service",
            "router",
            "--env-file",
            "router.env",
            "--recreate",
            "--dry-run",
        ],
        ["router", "down", "--compose", "deployment.yml", "--service", "router", "--dry-run"],
        ["router", "restart", "--container", "anvil-router", "--no-verify", "--dry-run"],
        ["router", "reload", "--container", "anvil-router", "--no-verify", "--dry-run"],
    ),
)
def test_canonical_router_lifecycle_options_parse_without_mutation(argv):
    assert cli.main(argv) == 0


@pytest.mark.parametrize(
    ("action", "present", "absent"),
    (
        (
            "up",
            {"--compose", "--service", "--env-file", "--recreate"},
            {"--container", "--no-verify"},
        ),
        (
            "down",
            {"--compose", "--service"},
            {"--env-file", "--recreate", "--container", "--no-verify"},
        ),
        (
            "restart",
            {"--container", "--no-verify"},
            {"--compose", "--service", "--env-file", "--recreate"},
        ),
        (
            "reload",
            {"--container", "--no-verify"},
            {"--compose", "--service", "--env-file", "--recreate"},
        ),
    ),
)
def test_router_lifecycle_help_lists_action_specific_options(capsys, action, present, absent):
    assert cli.main(["router", action, "--help"]) == 0
    rendered = capsys.readouterr().out

    assert present <= set(rendered.split())
    assert not (absent & set(rendered.split()))


def test_repo_workbench_surfaces_catalog_current_mcp_tools_and_cli_gaps():
    root = MANIFEST_PATH.parent.parent
    catalog_paths = (
        root / ".agents" / "skills" / "anvil-serving-workbench" / "SKILL.md",
        root / ".claude" / "skills" / "anvil-serving-workbench" / "SKILL.md",
    )

    for path in catalog_paths:
        text = path.read_text(encoding="utf-8")
        missing = {
            name
            for name in mcp.TOOLS
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text) is None
        }
        assert not missing, f"{path.relative_to(root)} omits MCP tools: {sorted(missing)}"

    for path in catalog_paths[:2]:
        text = path.read_text(encoding="utf-8")
        for command in (
            "models recipes list/show",
            "models recipes create/update",
            "models recipes load",
            "models recipes delete",
            "models pull",
            "serves switch ROLE [MODEL]",
            "eval benchmark quality",
            "confirm=true",
            "dry_run=false",
            "human_approved=true",
            "Model Benchmark Source Freshness",
            "does not prove evidence sufficiency",
        ):
            assert command in text, f"{path.relative_to(root)} omits {command!r}"

    voice_text = (root / "skills" / "anvil-serving-voice-ops" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    for token in (
        "voice_manage",
        "voice_proxy_manage",
        "workflow_packet_validate",
        "voice profiles validate",
        "--candidate-overlay",
        "eval benchmark quality",
    ):
        assert token in voice_text


def test_explicit_family_list_rebuilds_the_same_tree():
    rebuilt = build_command_tree(FAMILIES)

    assert rebuilt == COMMAND_TREE
    assert len(FAMILIES) == 8


def test_registry_import_does_not_import_operational_handlers():
    code = """
import json
import sys
from anvil_serving.commands import COMMAND_TREE

def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node.children)

handlers = {node.handler.module for node in walk(COMMAND_TREE.nodes) if node.handler}
print(json.dumps(sorted(handlers & sys.modules.keys())))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "[]"


def test_manifest_contains_dispatcher_policy_not_leaf_parser_duplication():
    records = {record["path"]: record for record in manifest_data()["commands"]}
    preflight_flags = {
        flag for option in records["eval preflight"]["options"] for flag in option["flags"]
    }

    assert {"--dry-run", "--confirm"} <= preflight_flags
    assert "--base-url" not in preflight_flags
    assert "--model" not in preflight_flags


def test_command_family_decorator_returns_a_testable_declaration():
    @command_family(category="Tests")
    def synthetic():
        return CommandNode(
            "synthetic",
            "Synthetic command.",
            handler=HandlerRef("anvil_serving.init"),
        )

    assert synthetic.category == "Tests"
    assert synthetic.build()[0].name == "synthetic"


def test_duplicate_family_roots_fail_during_assembly():
    with pytest.raises(ValueError, match="duplicate command family root"):
        build_command_tree((FAMILIES[0], FAMILIES[0]))
