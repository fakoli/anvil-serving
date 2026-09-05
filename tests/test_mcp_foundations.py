"""Characterization tests for the extracted MCP foundation boundary."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

from anvil_serving import mcp
from anvil_serving.benchmarking import artifacts as benchmark_artifacts
from anvil_serving.control_plane.mcp import catalog
from anvil_serving.control_plane.mcp.tools import TOOL_FAMILIES
from anvil_serving.control_plane.mcp.tools import host as host_tools
from anvil_serving.control_plane.mcp.tools import models as model_tools
from anvil_serving.control_plane.mcp.tools import router as router_tools


PUBLIC_CATALOG_SHA256 = (
    "b637edac3815408da26e7fc2d6f7eb80ee864702252cb661883cf42a49a54b27"
)
HANDLER_MAP_SHA256 = (
    "aed352154d729466b1abbe884e957252e909d627f7e352afbc7a9ef9c1458824"
)
TOOL_NAMES = [
    "operation_contracts",
    "controller_inspect",
    "controller_logs",
    "router_status",
    "router_fleet_status",
    "router_logs",
    "router_manage",
    "router_transition",
    "decision_summary",
    "serves_status",
    "reservation_status",
    "serves_manage",
    "serves_promote",
    "serves_mode",
    "serves_logs",
    "media_worker_prepare",
    "media_worker_status",
    "media_worker_logs",
    "media_worker_teardown",
    "media_capabilities",
    "media_workflow_list",
    "media_workflow_show",
    "media_workflow_validate",
    "media_workflow_run",
    "media_job_status",
    "media_job_cancel",
    "media_artifact_inspect",
    "voice_manage",
    "voice_proxy_manage",
    "doctor_summary",
    "host_summary",
    "gpu_inventory",
    "host_shared_memory",
    "operator_config_inventory",
    "operator_config_export",
    "observability_collect",
    "host_manage",
    "models_inventory",
    "model_cache_inventory",
    "recipe_containers",
    "cache_prune_plan",
    "openclaw_sync",
    "openclaw_gateway_restart",
    "openclaw_gateway_status",
    "client_catalog_sync",
    "routed_eval",
    "hermes_media_sync",
    "benchmark_harness_prepare",
    "benchmark_harness_status",
    "benchmark_harness_cleanup",
    "benchmark_job_preflight",
    "benchmark_job_submit",
    "benchmark_job_status",
    "benchmark_job_logs",
    "benchmark_job_cancel",
    "benchmark_job_artifact",
    "preflight_probe",
    "benchmark_probe",
    "benchmark_artifact",
    "workflow_packet_validate",
    "external_bench_sources",
    "external_bench_list",
    "external_bench_report",
    "external_bench_compare",
]
MCP_META = {
    "io.modelcontextprotocol/protocolVersion": mcp.PROTOCOL_VERSION,
    "io.modelcontextprotocol/clientInfo": {
        "name": "anvil-serving-tests",
        "version": "1.0",
    },
    "io.modelcontextprotocol/clientCapabilities": {},
}


def _request(method: str, request_id=1, **params):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {**params, "_meta": MCP_META},
    }


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


def test_controller_diagnostic_tools_have_exact_bounded_public_contracts():
    assert mcp.TOOLS["controller_inspect"]["description"] == (
        "Read metadata-only diagnostics for one local controller container."
    )
    assert mcp.TOOLS["controller_inspect"]["inputSchema"] == {
        "type": "object",
        "additionalProperties": False,
        "maxProperties": 1,
        "properties": {
            "container": {"type": "string", "minLength": 1, "maxLength": 128},
        },
        "required": ["container"],
    }
    assert mcp.TOOLS["controller_inspect"]["handler"].__name__ == "_controller_inspect"

    assert mcp.TOOLS["controller_logs"]["description"] == (
        "Read bounded metadata-only audit diagnostics for one local controller container."
    )
    assert mcp.TOOLS["controller_logs"]["inputSchema"] == {
        "type": "object",
        "additionalProperties": False,
        "maxProperties": 2,
        "properties": {
            "container": {"type": "string", "minLength": 1, "maxLength": 128},
            "tail": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100},
        },
        "required": ["container"],
    }
    assert mcp.TOOLS["controller_logs"]["handler"].__name__ == "_controller_logs"


def test_media_artifact_discovery_describes_bounded_native_image_content():
    assert mcp.TOOLS["media_artifact_inspect"]["description"] == (
        "Inspect authenticated artifact metadata; eligible bounded images include "
        "native content, while video and oversized images remain resource-only."
    )


def test_media_run_catalog_accepts_explicit_profileless_resume_value():
    quality_profile = mcp.TOOLS["media_workflow_run"]["inputSchema"][
        "properties"
    ]["quality_profile"]
    assert quality_profile == {
        "type": "string",
        "minLength": 0,
        "maxLength": 128,
    }


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
        "media-worker",
        "media",
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


def test_host_shared_memory_tools_preview_and_apply(monkeypatch):
    from anvil_serving import host

    inspection = {
        "available": True,
        "files": [{"path": "/dev/shm/vllm_offload_x.mmap"}],
        "reclaimable_files": ["/dev/shm/vllm_offload_x.mmap"],
    }
    monkeypatch.setattr(
        host, "inspect_vllm_offload_shared_memory", lambda: inspection,
    )
    observed = []
    monkeypatch.setattr(
        host, "cmd_shared_memory_reclaim",
        lambda **kwargs: observed.append(kwargs) or 0,
    )

    status = host_tools.tool_host_shared_memory({})
    assert status["data"] == inspection
    preview = host_tools.tool_host_manage({"action": "reclaim-shared-memory"})
    assert preview["data"]["applied"] is False
    assert preview["data"]["target"]["inspection"] == inspection
    applied = host_tools.tool_host_manage({
        "action": "reclaim-shared-memory", "confirm": True, "dry_run": False,
    })
    assert applied["data"]["applied"] is True
    assert observed == [{"confirm": True}]


def test_router_fleet_status_tool_uses_installed_runtime_probe(monkeypatch):
    seen = {}
    report = {
        "rows": [],
        "checked": 0,
        "unreachable": 0,
        "unreachable_aliases": [],
        "probe_perspective": "router-runtime",
        "evidence_source": "installed-router",
    }
    monkeypatch.setattr(
        "anvil_serving.router_manage.installed_fleet_status",
        lambda **kwargs: seen.update(kwargs) or report,
    )

    result = router_tools.tool_router_fleet_status(
        {"timeout": 7}
    )

    assert result["data"] == report
    assert seen == {"container": "anvil-router", "timeout": 7.0}


def test_operation_contracts_resolve_against_the_composed_public_catalog():
    result = mcp.call_tool("operation_contracts")

    assert result["ok"]
    declared_tools = {
        operation["tool"]
        for operation in result["data"]["operations"]
        if operation["mode"] == "tool"
    }
    assert declared_tools <= set(mcp.TOOLS)

    with pytest.raises(mcp.ToolError) as refused:
        mcp.tool_operation_contracts({"unexpected": True})
    assert refused.value.code == "bad_argument"


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


def test_artifact_policy_falls_back_to_editable_package_workspace(
        tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANVIL_WORKSPACE_ROOT", raising=False)

    workspace = benchmark_artifacts.discover_workspace_root()

    assert workspace == str(Path(__file__).resolve().parents[1])


def test_models_inventory_defaults_to_operator_config_home(tmp_path, monkeypatch):
    from anvil_serving import models, paths

    seen = {}
    monkeypatch.setattr(paths, "config_home", lambda: str(tmp_path))
    monkeypatch.setattr(
        models,
        "load_model_catalog",
        lambda catalog_dir: seen.setdefault("catalog_dir", catalog_dir) or {},
    )

    result = model_tools.tool_models_inventory({})

    assert result["ok"]
    assert seen["catalog_dir"] == str(tmp_path / "model-library")


def test_doctor_summary_uses_same_operator_home_default_as_cli(tmp_path, monkeypatch):
    from anvil_serving import doctor

    operator_config = tmp_path / "operator-home" / "router.toml"
    operator_config.parent.mkdir()
    operator_config.write_text("[router]\n", encoding="utf-8")
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(operator_config.parent))
    seen = {}
    monkeypatch.setattr(
        doctor,
        "checks_summary",
        lambda **kwargs: seen.update(kwargs) or {"ok": True, "checks": []},
    )

    result = host_tools.tool_doctor_summary({})

    assert result["ok"]
    assert seen["config_path"] == str(operator_config)
    assert seen["config_explicit"] is False


def test_model_cache_inventory_is_read_only_and_structured(monkeypatch):
    from anvil_serving import models

    seen = {}
    monkeypatch.setattr(
        models,
        "cache_inventory",
        lambda **kwargs: seen.update(kwargs) or {
            "schema_version": "model-cache-inventory/v1",
            "repositories": [],
        },
    )

    result = model_tools.tool_model_cache_inventory({
        "volume": "vllm-hfcache",
        "image": "example/inspector:1",
    })

    assert result["ok"]
    assert result["data"]["inventory"]["schema_version"] == "model-cache-inventory/v1"
    assert seen == {"volume": "vllm-hfcache", "image": "example/inspector:1"}


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


def test_jsonrpc_2026_discovery_results_and_legacy_initialize_removal():
    discovered = mcp.handle_request(_request("server/discover"))
    initialized = mcp.handle_request(_request("initialize", request_id=2))

    assert discovered == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "resultType": "complete",
            "supportedVersions": [mcp.PROTOCOL_VERSION],
            "capabilities": {"tools": {}},
            "instructions": (
                "Operate Anvil Serving through explicit, bounded tools. "
                "Mutating tools retain their dry-run, confirmation, and human gates."
            ),
            "_meta": {
                "io.modelcontextprotocol/serverInfo": mcp.SERVER_INFO,
            },
            "ttlMs": 30000,
            "cacheScope": "private",
        },
    }
    assert initialized == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32601, "message": "method not found"},
    }


def test_jsonrpc_requires_stateless_2026_metadata():
    missing = mcp.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )
    old = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {
                "_meta": {
                    **MCP_META,
                    "io.modelcontextprotocol/protocolVersion": "2025-11-25",
                }
            },
        }
    )

    assert missing["error"]["code"] == -32602
    assert old["error"]["code"] == -32022
    assert old["error"]["data"] == {
        "requested": "2025-11-25",
        "supported": [mcp.PROTOCOL_VERSION],
    }


def test_stdio_parse_errors_and_notifications_remain_bounded():
    stdout = io.StringIO()

    assert mcp.serve_stdio(
        stdin=[
            "{not-json}\n",
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"_meta": MCP_META},
                }
            )
            + "\n",
        ],
        stdout=stdout,
    ) == 0

    responses = stdout.getvalue().splitlines()
    assert len(responses) == 1
    assert json.loads(responses[0])["error"]["code"] == -32700


def test_proxy_uses_explicit_remote_request_seam(monkeypatch):
    request = _request("tools/list", request_id=7)
    response = {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {
            "resultType": "complete",
            "tools": mcp.list_tools(),
            "ttlMs": 30000,
            "cacheScope": "private",
        },
    }
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


def test_remote_controller_request_uses_2026_http_endpoint_and_headers():
    request = _request(
        "tools/call",
        request_id=9,
        name="weather_世界",
        arguments={},
    )
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            return b'{"jsonrpc":"2.0","id":9,"result":{"resultType":"complete"}}'

    def opener(req, timeout):
        seen["url"] = req.full_url
        seen["headers"] = {name.lower(): value for name, value in req.header_items()}
        seen["timeout"] = timeout
        return Response()

    result = mcp.remote_controller_request(
        "http://127.0.0.1:8765",
        request,
        "secret",
        opener=opener,
    )

    assert result["result"]["resultType"] == "complete"
    assert seen["url"] == "http://127.0.0.1:8765/mcp"
    assert seen["headers"]["accept"] == "application/json, text/event-stream"
    assert seen["headers"]["mcp-protocol-version"] == mcp.PROTOCOL_VERSION
    assert seen["headers"]["mcp-method"] == "tools/call"
    assert seen["headers"]["mcp-name"].startswith("=?base64?")


def test_remote_controller_request_bounds_response_body():
    request = _request("tools/list", request_id=10)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size):
            return b"x" * size

    with pytest.raises(mcp.ToolError) as exc_info:
        mcp.remote_controller_request(
            "http://127.0.0.1:8765",
            request,
            "secret",
            opener=lambda _request, timeout: Response(),
            max_response_bytes=32,
        )

    assert exc_info.value.code == "controller_response_too_large"


def test_remote_controller_request_admits_bounded_native_image_result():
    request = _request("tools/call", request_id=11, name="media_artifact_inspect")
    image_data = "A" * (2 * 1024 * 1024)
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "result": {
                "resultType": "complete",
                "content": [
                    {"type": "image", "data": image_data, "mimeType": "image/png"}
                ],
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size):
            assert len(payload) < size
            return payload

    result = mcp.remote_controller_request(
        "http://127.0.0.1:8765",
        request,
        "secret",
        opener=lambda _request, timeout: Response(),
    )
    assert result["result"]["content"][0]["data"] == image_data
