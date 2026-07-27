"""Characterization tests for the extracted MCP foundation boundary."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys

import pytest

from anvil_serving import mcp
from anvil_serving.benchmarking import artifacts as benchmark_artifacts
from anvil_serving.control_plane.mcp import catalog
from anvil_serving.control_plane.mcp.tools import TOOL_FAMILIES


PUBLIC_CATALOG_SHA256 = (
    "309071cfa38762be00bc01c7ab02a9f72bdf1542467a2efc9ce1e71901d5cf29"
)
HANDLER_MAP_SHA256 = (
    "163b612cc5aeee2f50ee09eb23344e9d39790de3990b410831de37db789a6e18"
)
TOOL_NAMES = [
    "operation_contracts",
    "router_status",
    "router_logs",
    "router_manage",
    "router_transition",
    "decision_summary",
    "serves_status",
    "reservation_status",
    "serves_manage",
    "serves_promote",
    "serves_logs",
    "voice_manage",
    "voice_proxy_manage",
    "doctor_summary",
    "host_summary",
    "gpu_inventory",
    "observability_collect",
    "host_manage",
    "models_inventory",
    "cache_prune_plan",
    "openclaw_sync",
    "openclaw_gateway_restart",
    "openclaw_gateway_status",
    "preflight_probe",
    "benchmark_probe",
    "benchmark_artifact",
    "workflow_packet_validate",
    "external_bench_sources",
    "external_bench_list",
    "external_bench_report",
    "external_bench_compare",
]


def _canonical_sha256(value) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def test_public_catalog_names_order_descriptions_schemas_and_metadata_are_stable():
    assert list(mcp.TOOLS) == TOOL_NAMES
    assert _canonical_sha256(mcp.list_tools()) == PUBLIC_CATALOG_SHA256


def test_direct_handler_map_is_stable_and_uses_dictionary_lookup():
    handlers = [
        (name, specification["handler"].__name__)
        for name, specification in mcp.TOOLS.items()
    ]

    assert type(mcp.TOOLS) is dict
    assert _canonical_sha256(handlers) == HANDLER_MAP_SHA256
    assert all(callable(mcp.TOOLS[name]["handler"]) for name in TOOL_NAMES)


def test_explicit_ordered_families_compose_the_public_catalog():
    assert [family.name for family in TOOL_FAMILIES] == [
        "operations",
        "router",
        "serves",
        "voice",
        "host",
        "models",
        "openclaw",
        "benchmarks",
        "workflow",
        "external_benchmarks",
    ]
    assert [
        tool_name
        for family in TOOL_FAMILIES
        for tool_name in family.tools
    ] == TOOL_NAMES
    assert all(
        specification["handler"].__module__.startswith(
            "anvil_serving.control_plane.mcp.tools."
        )
        for specification in mcp.TOOLS.values()
    )


def test_family_catalog_rejects_duplicate_names():
    def handler(_args):
        return {}

    specification = {
        "description": "Duplicate.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "maxProperties": 0,
            "properties": {},
            "required": [],
        },
        "handler": handler,
    }
    with pytest.raises(RuntimeError, match="duplicate MCP tool name"):
        catalog.build_family_catalog(
            (
                catalog.ToolFamily("first", {"duplicate": specification}),
                catalog.ToolFamily("second", {"duplicate": specification}),
            )
        )


def test_family_catalog_rejects_duplicate_family_names():
    with pytest.raises(RuntimeError, match="duplicate MCP tool family"):
        catalog.build_family_catalog(
            (
                catalog.ToolFamily("duplicate", {}),
                catalog.ToolFamily("duplicate", {}),
            )
        )


def test_static_catalog_validation_fails_closed_at_construction():
    with pytest.raises(RuntimeError, match="unbounded object schema"):
        catalog.build_catalog(
            {
                "unsafe": {
                    "description": "Unsafe.",
                    "inputSchema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {},
                        "required": [],
                    },
                    "handler": lambda _args: {},
                }
            }
        )


def test_facade_preserves_public_surface_and_private_artifact_shim():
    expected = {
        "TOOLS",
        "PROTOCOL_VERSION",
        "SERVER_INFO",
        "ToolError",
        "list_tools",
        "call_tool",
        "validate_tool_arguments",
        "operation_declarations",
        "tool_operation_contracts",
        "handle_request",
        "handle_proxy_request",
        "serve_stdio",
        "main",
        "_resolve_benchmark_artifact_path",
    }

    assert all(hasattr(mcp, name) for name in expected)
    assert mcp.ToolError.__module__.endswith(".control_plane.mcp.errors")
    assert mcp._run_argv.__module__.endswith(".control_plane.mcp.runtime")


def test_public_artifact_policy_uses_domain_errors_and_mcp_translates_them():
    with pytest.raises(
        benchmark_artifacts.BenchmarkArtifactError,
        match="artifact_path must be a file path",
    ) as domain_error:
        benchmark_artifacts.resolve_benchmark_artifact_path("-")
    with pytest.raises(
        mcp.ToolError,
        match="artifact_path must be a file path",
    ) as mcp_error:
        mcp._resolve_benchmark_artifact_path("-")

    assert domain_error.value.code == "bad_artifact_path"
    assert mcp_error.value.code == domain_error.value.code
    assert mcp_error.value.details == domain_error.value.details


@pytest.mark.parametrize(
    "imports",
    [
        (
            "anvil_serving.mcp",
            "anvil_serving.control_plane.mcp.protocol",
            "anvil_serving.controller",
        ),
        (
            "anvil_serving.controller",
            "anvil_serving.control_plane.mcp.catalog",
            "anvil_serving.mcp",
        ),
    ],
)
def test_mcp_foundations_and_facades_import_without_cycles(imports):
    completed = subprocess.run(
        [sys.executable, "-c", "; ".join(f"import {name}" for name in imports)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_jsonrpc_initialize_notification_and_unknown_method_are_unchanged():
    initialized = mcp.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    )
    notification = mcp.handle_request(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    unknown = mcp.handle_request(
        {"jsonrpc": "2.0", "id": 2, "method": "unknown"}
    )

    assert initialized == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": mcp.PROTOCOL_VERSION,
            "serverInfo": mcp.SERVER_INFO,
            "capabilities": {"tools": {}},
        },
    }
    assert notification is None
    assert unknown == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32601, "message": "method not found"},
    }


def test_stdio_parse_errors_and_notifications_remain_bounded():
    stdout = io.StringIO()

    assert mcp.serve_stdio(
        stdin=[
            "{not-json}\n",
            '{"jsonrpc":"2.0","method":"notifications/initialized"}\n',
        ],
        stdout=stdout,
    ) == 0

    responses = stdout.getvalue().splitlines()
    assert len(responses) == 1
    assert json.loads(responses[0])["error"]["code"] == -32700


def test_proxy_uses_explicit_remote_request_seam(monkeypatch):
    request = {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}
    response = {"jsonrpc": "2.0", "id": 7, "result": {"tools": mcp.list_tools()}}
    calls = []

    def remote(controller_url, payload, token):
        calls.append((controller_url, payload, token))
        return response

    monkeypatch.setattr(mcp, "remote_controller_request", remote)

    assert (
        mcp.handle_proxy_request(
            request,
            "http://127.0.0.1:8765",
            "secret",
        )
        == response
    )
    assert calls == [
        ("http://127.0.0.1:8765", request, "secret"),
    ]
