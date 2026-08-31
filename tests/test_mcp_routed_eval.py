"""Controller transport contract for routed real-client acceptance."""

from pathlib import Path

import pytest

from anvil_serving import mcp, routed_eval
from anvil_serving.commands import COMMAND_TREE
from anvil_serving.control_plane.mcp.tools import openclaw as openclaw_tools


def _command(path):
    nodes = COMMAND_TREE.nodes
    node = None
    for name in path:
        node = next(item for item in nodes if item.name == name)
        nodes = node.children
    return node


def _arguments(**overrides):
    arguments = {
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "llm.primary",
        "api_key_env": "ANVIL_ROUTER_TOKEN",
        "expected_served_model": "candidate-exact",
        "expected_config_fingerprint": "candidate-config",
        "expected_router_config_sha256": "a" * 64,
        "min_context_tokens": 250_000,
        "clients": "openclaw,hermes",
        "run_id": "candidate-routed",
        "dry_run": True,
    }
    arguments.update(overrides)
    return arguments


def test_eval_routed_declares_typed_controller_operation():
    node = _command(("eval", "routed"))

    assert node.transports == ("local", "controller")
    assert node.remote_operation.tool == "routed_eval"
    assert node.remote_operation.max_response_bytes == 1024 * 1024


def test_routed_eval_schema_is_secret_reference_only():
    properties = mcp.TOOLS["routed_eval"]["inputSchema"]["properties"]

    assert "api_key" not in properties
    assert {
        "api_key_env",
        "confirm",
        "dry_run",
        "expected_router_config_sha256",
        "no_harness_sync",
        "output",
    } <= set(properties)
    with pytest.raises(mcp.ToolError, match="raw api_key"):
        mcp.tool_routed_eval({**_arguments(), "api_key": "never"})


def test_routed_eval_dry_run_forwards_bounded_contract(monkeypatch):
    seen = {}

    def run(**kwargs):
        seen.update(kwargs)
        return {"dry_run": True, "passed": None, "output": kwargs["output"]}

    monkeypatch.setattr(routed_eval, "run_routed_eval", run)

    result = mcp.tool_routed_eval(_arguments(timeout_seconds=321.5))

    assert result["ok"] is True
    assert seen["alias"] == "llm.primary"
    assert seen["timeout_seconds"] == 321.5
    assert seen["dry_run"] is True
    assert seen["sync_harnesses"] is True
    assert seen["environment"] == {}
    assert seen["output"].endswith(
        str(Path("routed-eval") / "candidate-routed.json")
    )


def test_routed_eval_live_run_requires_confirmation():
    with pytest.raises(mcp.ToolError) as refused:
        mcp.tool_routed_eval(_arguments(dry_run=False, confirm=False))

    assert refused.value.code == "human_approval_required"


def test_routed_eval_live_run_resolves_only_named_credential(monkeypatch):
    seen = {}

    def run(**kwargs):
        seen.update(kwargs)
        return {"dry_run": False, "passed": True, "output": kwargs["output"]}

    monkeypatch.setattr(routed_eval, "run_routed_eval", run)
    monkeypatch.setattr(
        openclaw_tools,
        "resolve_env_value",
        lambda name: ("private-test-token", "/private/.env"),
    )

    result = mcp.tool_routed_eval(
        _arguments(dry_run=False, confirm=True, run_id="credential-resolution")
    )

    assert result["ok"] is True
    assert seen["environment"] == {"ANVIL_ROUTER_TOKEN": "private-test-token"}
    assert "private-test-token" not in repr(result)


def test_routed_eval_remote_output_cannot_escape_private_evidence_root(tmp_path):
    with pytest.raises(mcp.ToolError) as refused:
        openclaw_tools.tool_routed_eval(
            _arguments(output=str(tmp_path / "outside.json"))
        )

    assert refused.value.code == "unsafe_output_path"
