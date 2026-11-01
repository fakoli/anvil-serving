"""Characterization tests for the extracted MCP foundation boundary."""

from __future__ import annotations

import io
import json
from pathlib import Path


from anvil_serving import mcp
from anvil_serving.benchmarking import artifacts as benchmark_artifacts
from anvil_serving.control_plane.mcp.tools import host as host_tools
from anvil_serving.control_plane.mcp.tools import models as model_tools


PUBLIC_CATALOG_SHA256 = (
    "85bc86cc0ff3052fd5df5ef23b43479b20d214fb00d3f6aa77e61a9926dece34"
)
HANDLER_MAP_SHA256 = (
    "79fd026ba4940387e13f4d835de71fea70e167c74a64f6eaac4f15303d428123"
)
TOOL_NAMES = [
    "operation_contracts",
    "controller_inspect",
    "controller_logs",
    "router_status",
    "router_fleet_status",
    "router_config_export",
    "router_logs",
    "router_manage",
    "router_transition",
    "router_configuration",
    "decision_summary",
    "runtime_experiment",
    "serves_status",
    "reservation_status",
    "serves_manage",
    "serves_promote",
    "serves_mode",
    "serves_logs",
    "serves_probe",
    "serves_profile",
    "media_worker_prepare",
    "media_worker_status",
    "media_worker_logs",
    "media_worker_teardown",
    "media_capabilities",
    "media_workflow_list",
    "media_workflow_show",
    "media_workflow_validate",
    "media_workflow_prepare",
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
    "host_services_status",
    "host_services_discover",
    "host_services_capabilities",
    "host_services_logs",
    "host_services_manage",
    "container_exec",
    "models_inventory",
    "model_cache_inventory",
    "recipe_containers",
    "recipe_settings",
    "recipe_manage",
    "cache_prune_plan",
    "openclaw_sync",
    "openclaw_gateway_restart",
    "openclaw_gateway_status",
    "client_catalog_sync",
    "routed_eval",
    "hermes_media_sync",
    "propagation_capabilities",
    "benchmark_harness_prepare",
    "benchmark_harness_status",
    "benchmark_harness_cleanup",
    "benchmark_job_preflight",
    "benchmark_job_submit",
    "benchmark_job_status",
    "benchmark_job_list",
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
