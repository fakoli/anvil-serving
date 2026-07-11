"""Tests for the `anvil-serving mcp` control plane.

No docker, OpenClaw gateway, router, or model serve is required: command and
HTTP seams are faked at the module boundary.
"""
import io
import json
import re
import sqlite3
import sys
import threading
import textwrap
import types
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from anvil_serving import cli, mcp
from anvil_serving.command_tree import manifest_data


def proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


def _manifest(tmp_path, *, up=False):
    p = tmp_path / "serves.toml"
    body = """
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30001
        health = "/health"
        model = "fast-model"
        engine = "vllm"
    """
    if up:
        body += '        up = "docker compose -f {dir}/docker-compose.yml up -d"\n'
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(p)


def _voice_topology(tmp_path):
    p = tmp_path / "voice-topology.toml"
    p.write_text(textwrap.dedent("""
        schema_version = 1
        id = "voice-test"
        command_host = "host:dark"
        command_runtime = "runtime:dark-native"

        [[capacity_policies]]
        id = "model-capable"
        allow_model_workloads = true

        [[hosts]]
        id = "dark"
        roles = ["audio"]
        os = "windows"
        capacity_policy = "model-capable"

        [[runtimes]]
        id = "dark-native"
        host = "dark"
        role = "native"

        [[resources]]
        id = "dark-stt"
        role = "stt-serve"
        host = "dark"
        runtime = "dark-native"
        endpoint = "http://127.0.0.1:30010/v1"
        endpoint_kind = "host-relative-loopback"
        workload = "stt"

        [[resources]]
        id = "dark-tts"
        role = "tts-serve"
        host = "dark"
        runtime = "dark-native"
        endpoint = "http://127.0.0.1:30011/v1"
        endpoint_kind = "host-relative-loopback"
        workload = "tts"
    """), encoding="utf-8")
    return str(p)


def _router_cfg(tmp_path):
    p = tmp_path / "router.toml"
    p.write_text(textwrap.dedent("""
        [router]
        mapping_version = "test"

        [[router.tiers]]
        id = "fast-local"
        base_url = "http://127.0.0.1:30001/v1"
        dialect = "openai"
        context_limit = 32768
        privacy = "local"
        tool_support = true
        auth_env = "ANVIL_FAST_LOCAL_KEY"

        [router.presets]
        chat = ["fast-local"]
        chat-fast = ["fast-local"]
        review = ["fast-local"]
    """), encoding="utf-8")
    return str(p)


def _profile_doc(tmp_path, name="profile.json", decision="allow"):
    p = tmp_path / name
    p.write_text(json.dumps({
        "schema": "anvil-serving.router.profile_bootstrap/v2",
        "mode": "live",
        "eval_max": 25.0,
        "entries": [{
            "tier_id": "fast-local",
            "work_class": "chat",
            "decision": decision,
            "quality_score": 0.9 if decision == "allow" else 0.2,
            "sample_n": 3,
            "last_measured": "2026-07-06T00:00:00Z",
        }],
    }), encoding="utf-8")
    return str(p)


def _catalog(tmp_path):
    root = tmp_path / "model-library"
    cards = root / "cards"
    cards.mkdir(parents=True)
    (root / "INDEX.md").write_text("| human table only |\n", encoding="utf-8")
    (cards / "owner__repo.json").write_text(json.dumps({
        "id": "owner/repo",
        "owner": "owner",
        "repo": "repo",
        "format": "safetensors",
        "sglang_loadable": True,
    }), encoding="utf-8")
    return root


def _external_bench_db(tmp_path):
    from anvil_serving.external_benchmarks import cli as external_cli

    db = tmp_path / "benchmarks.sqlite"
    fixture = Path("tests/fixtures/external_benchmarks/millstone_sample.json")
    assert external_cli.main(["import", "--source", "millstone", "--file", str(fixture), "--db", str(db)]) == 0
    return db


def _sqlite_count(db, table):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]


class Resp:
    status = 200

    def __init__(self, body=b"{}"):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body

    def getcode(self):
        return self.status


class _HandlerServer:
    def __init__(self, handler):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self.httpd.server_address

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def test_tools_list_has_json_schemas():
    tools = {t["name"]: t for t in mcp.list_tools()}
    for name in [
        "router_status",
        "router_logs",
        "router_manage",
        "decision_summary",
        "router_promote",
        "serves_status",
        "serves_manage",
        "serves_logs",
        "voice_manage",
        "doctor_summary",
        "host_summary",
        "host_manage",
        "gpu_inventory",
        "models_inventory",
        "cache_prune_plan",
        "route_decision",
        "openclaw_sync",
        "openclaw_gateway_restart",
        "preflight_probe",
        "benchmark_probe",
        "benchmark_artifact",
        "workflow_packet_validate",
        "external_bench_sources",
        "external_bench_list",
        "external_bench_report",
        "external_bench_compare",
        "operation_contracts",
    ]:
        assert name in tools
        schema = tools[name]["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert "properties" in schema
        assert schema["maxProperties"] == len(schema["properties"])
        assert tools[name]["_meta"]["anvil/targetContextSchema"] == mcp.TARGET_CONTEXT_SCHEMA
    for name in ("preflight_probe", "benchmark_probe"):
        props = tools[name]["inputSchema"]["properties"]
        assert "api_key" not in props
        assert props["api_key_env"]["type"] == "string"
    assert tools["benchmark_probe"]["inputSchema"]["properties"]["requests"]["maximum"] == 200
    assert tools["benchmark_artifact"]["inputSchema"]["required"] == ["base_url", "model", "artifact_path"]
    assert "evidence_dir" not in tools["benchmark_artifact"]["inputSchema"]["properties"]
    assert tools["workflow_packet_validate"]["inputSchema"]["required"] == ["packet"]
    assert tools["openclaw_gateway_restart"]["inputSchema"]["properties"]["timeout_seconds"]["default"] == 120
    assert tools["router_promote"]["inputSchema"]["properties"]["human_approved"]["type"] == "boolean"
    assert tools["openclaw_sync"]["inputSchema"]["properties"]["skills"]["type"] == "boolean"
    assert tools["openclaw_sync"]["inputSchema"]["properties"]["voice"]["type"] == "boolean"
    assert tools["openclaw_sync"]["inputSchema"]["properties"]["voice_api_key_env"]["type"] == "string"
    assert tools["openclaw_sync"]["inputSchema"]["properties"]["voice_consult_model"]["type"] == "string"
    thinking_schema = tools["openclaw_sync"]["inputSchema"]["properties"]["voice_consult_thinking_level"]
    assert thinking_schema["type"] == "string"
    assert thinking_schema["enum"] == ["adaptive", "high", "low", "max", "medium", "minimal", "off", "xhigh"]
    assert (
        tools["openclaw_sync"]["inputSchema"]["properties"][
            "voice_consult_bootstrap_context_mode"
        ]["type"]
        == "string"
    )
    assert tools["openclaw_sync"]["inputSchema"]["properties"][
        "voice_consult_bootstrap_context_mode"
    ]["enum"] == ["full", "lightweight"]
    assert tools["voice_manage"]["inputSchema"]["required"] == ["action"]
    assert tools["external_bench_compare"]["inputSchema"]["required"] == ["local"]
    assert tools["external_bench_report"]["inputSchema"]["properties"]["top"]["default"] == 100
    assert tools["host_summary"]["inputSchema"]["properties"] == {}
    assert tools["host_manage"]["inputSchema"]["required"] == ["action"]
    assert "execute" not in tools["cache_prune_plan"]["inputSchema"]["properties"]


def test_operation_contracts_enumerate_every_remote_capable_command():
    expected = {
        record["path"].replace(" ", "-")
        for record in manifest_data()["commands"]
        if record["visible"]
        and record["tombstone"] is None
        and record["handler"] is not None
        and "controller" in record["transports"]
    }
    declarations = mcp.operation_declarations()

    assert {declaration["name"] for declaration in declarations} == expected
    assert all("controller" in declaration["transports"] for declaration in declarations)
    assert all(declaration["resource_role"] for declaration in declarations)
    assert all(
        declaration["tool"] in mcp.TOOLS
        for declaration in declarations
        if declaration["mode"] == "tool"
    )
    envelope = mcp.call_tool("operation_contracts")
    assert envelope == {"ok": True, "data": {"operations": declarations}}


def test_mcp_rejects_raw_commands_secrets_and_unbounded_arguments(monkeypatch):
    monkeypatch.setenv("ANVIL_CONTROLLER_TOKEN", "controller-secret-token")
    raw_command = mcp.call_tool("router_status", {"command": ["cmd", "/c", "whoami"]})
    raw_secret = mcp.call_tool("router_status", {"token": "controller-secret-token"})
    oversized = mcp.call_tool("router_status", {"container": "x" * (mcp._MAX_SCHEMA_STRING + 1)})

    assert raw_command["error"]["code"] == "raw_command_not_allowed"
    assert raw_secret["error"]["code"] == "bad_argument"
    assert "controller-secret-token" not in json.dumps(raw_secret)
    assert oversized["error"]["code"] == "bad_argument"

    monkeypatch.setitem(
        mcp.TOOLS,
        "operation_contracts",
        {
            **mcp.TOOLS["operation_contracts"],
            "handler": lambda _args: (_ for _ in ()).throw(
                RuntimeError("controller-secret-token")
            ),
        },
    )
    failed = mcp.call_tool("operation_contracts")
    assert "controller-secret-token" not in json.dumps(failed)
    assert "<redacted>" in json.dumps(failed)


def test_stdio_tools_list_and_call(tmp_path):
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "preflight_probe",
                "arguments": {
                    "base_url": "http://127.0.0.1:30000/v1",
                    "model": "local",
                },
            },
        },
    ]
    stdin = [json.dumps(r) + "\n" for r in reqs]
    stdout = io.StringIO()
    assert mcp.serve_stdio(stdin, stdout) == 0
    lines = [json.loads(ln) for ln in stdout.getvalue().splitlines()]
    assert lines[0]["result"]["tools"]
    result = lines[1]["result"]
    envelope = result["structuredContent"]
    assert envelope["ok"] is True
    assert envelope["data"]["would_run"] is True
    assert isinstance(envelope["data"]["command"], list)


def test_stdio_tool_result_carries_fixed_redacted_target_context_metadata():
    context = {
        "command": "Bearer context-secret-token",
        "topology": "fakoli-reference",
        "execution_host": "dark",
        "execution_runtime": "dark-native",
        "resource_host": "dark",
        "transport": "controller",
        "controller_endpoint": "http://100.87.34.66:8765",
    }
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "operation_contracts", "arguments": {}, "context": context},
    }
    stdout = io.StringIO()

    assert mcp.serve_stdio([json.dumps(request) + "\n"], stdout) == 0
    response = json.loads(stdout.getvalue())
    metadata = response["result"]["_meta"]["anvil/context"]
    assert tuple(metadata) == mcp.CONTEXT_FIELDS
    assert metadata["topology"] == "fakoli-reference"
    assert metadata["execution_host"] == "dark"
    assert metadata["command"] == "Bearer <redacted>"
    assert "context-secret-token" not in stdout.getvalue()


def test_stdio_rejects_unbounded_or_unknown_target_context_before_dispatch(monkeypatch):
    monkeypatch.setitem(
        mcp.TOOLS,
        "operation_contracts",
        {
            **mcp.TOOLS["operation_contracts"],
            "handler": lambda _args: pytest.fail("unsafe context reached handler"),
        },
    )
    request = {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {
            "name": "operation_contracts",
            "arguments": {},
            "context": {"authorization": "Bearer context-secret-token"},
        },
    }
    stdout = io.StringIO()

    assert mcp.serve_stdio([json.dumps(request) + "\n"], stdout) == 0
    response = json.loads(stdout.getvalue())
    assert response["error"]["code"] == -32602
    assert response["error"]["data"]["code"] == "bad_context"
    assert "context-secret-token" not in stdout.getvalue()


def test_serves_status_is_structured(tmp_path):
    manifest = _manifest(tmp_path)

    def run(argv, **kw):
        if argv[:2] == ["docker", "inspect"]:
            return proc(0, "running\n")
        if argv and argv[0] == "nvidia-smi":
            return proc(0, "0, 1024, 24576\n")
        return proc(0)

    def open_ok(url, timeout=3):
        assert url == "http://127.0.0.1:30001/health"
        return Resp()

    from anvil_serving import serves

    rows = serves.status_summary(serves.load_manifest(manifest), _run=run, _open=open_ok)
    assert rows["serves"][0]["running"] is True
    assert rows["serves"][0]["health_status"] == 200
    assert rows["gpu_memory_lines"] == ["0, 1024, 24576"]


def test_router_status_is_structured(monkeypatch):
    from anvil_serving import router_manage

    def run(argv, **kw):
        assert argv[:2] == ["docker", "inspect"]
        return proc(0, "running\n")

    def open_ok(url, timeout=3):
        assert url == "http://127.0.0.1:8000/"
        return Resp()

    data = router_manage.status_summary("anvil-router", _run=run, _open=open_ok)
    assert data == {
        "container": "anvil-router",
        "docker_state": "running",
        "running": True,
        "health_status": 200,
        "health_url": "http://127.0.0.1:8000/",
        "ok": True,
    }


def test_router_logs_are_bounded_spooled_and_redacted(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["capture_output"] = kwargs.get("capture_output")
        kwargs["stdout"].write(b"Authorization: Bearer router-secret-token\n")
        kwargs["stdout"].write(b'{"x-api-key":"json-secret-token"}\n')
        kwargs["stderr"].write(b"ANVIL_ROUTER_TOKEN=another-secret\n")
        return proc(0)

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    env = mcp.call_tool("router_logs", {
        "container": "anvil-router",
        "tail": 5,
        "max_output_bytes": 1024,
    })
    assert env["ok"] is True
    assert env["data"]["bounded"] is True
    assert seen["argv"][:5] == [sys.executable, "-m", "anvil_serving.cli", "router", "logs"]
    assert "--follow" not in seen["argv"]
    assert seen["capture_output"] is None
    rendered = json.dumps(env)
    assert "router-secret-token" not in rendered
    assert "json-secret-token" not in rendered
    assert "another-secret" not in rendered
    assert "<redacted>" in rendered


def test_router_logs_rejects_follow_and_bad_tail():
    follow = mcp.call_tool("router_logs", {"follow": True})
    assert follow["ok"] is False
    assert follow["error"]["code"] == "follow_not_allowed"

    tail = mcp.call_tool("router_logs", {"tail": 5001})
    assert tail["ok"] is False
    assert tail["error"]["code"] == "bad_argument"


def test_router_manage_preview_and_confirmed_reload(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["timeout"] = kwargs.get("timeout")
        return proc(0, "restarted\n", "")

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    preview = mcp.call_tool("router_manage", {
        "action": "reload",
        "container": "anvil-router",
        "dry_run": False,
    })
    assert preview["ok"] is True
    assert preview["data"]["applied"] is False
    assert "--dry-run" in preview["data"]["command"]
    assert seen == {}

    confirmed = mcp.call_tool("router_manage", {
        "action": "reload",
        "container": "anvil-router",
        "confirm": True,
        "dry_run": False,
        "no_verify": True,
        "timeout_seconds": 9,
    })
    assert confirmed["ok"] is True
    assert confirmed["data"]["applied"] is True
    assert "--dry-run" not in seen["argv"]
    assert "--no-verify" in seen["argv"]
    assert seen["argv"][3:5] == ["router", "reload"]
    assert seen["timeout"] == 9


def test_decision_summary_omits_prompt_and_secret_fields(tmp_path):
    record = {
        "intent": "chat",
        "work_class": "bounded-edit",
        "requested_tiers": ["fast-local", "cloud"],
        "served_tier": "cloud",
        "fell_back": True,
        "total_prompt_tokens": 8,
        "total_completion_tokens": 4,
        "prompt": "secret prompt text",
        "api_key": "sk-proj-secret",
        "attempts": [{
            "tier_id": "fast-local",
            "outcome": "fallback",
            "verifier_passed": False,
            "verify_reason": "x-api-key: local-secret-token",
            "prompt_tokens": 8,
            "completion_tokens": 4,
        }],
    }
    path = tmp_path / "decisions.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    env = mcp.call_tool("decision_summary", {"path": str(path), "limit": 5})
    assert env["ok"] is True
    assert env["data"]["count"] == 1
    rendered = json.dumps(env)
    assert "secret prompt text" not in rendered
    assert "sk-proj-secret" not in rendered
    assert "local-secret-token" not in rendered
    assert "<redacted>" in rendered


def test_decision_summary_tool_calls_router_decisions_endpoint(monkeypatch):
    token = "router-secret-token"
    monkeypatch.setenv("ANVIL_ROUTER_TOKEN", token)
    seen = {}

    def open_decisions(req, timeout=5):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        return Resp(json.dumps({
            "count": 1,
            "records": [{"served_tier": "fast-local"}],
            "echo": token,
        }).encode("utf-8"))

    monkeypatch.setattr(mcp, "_urlopen_no_proxy_no_redirect", open_decisions)
    env = mcp.call_tool("decision_summary", {
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key_env": "ANVIL_ROUTER_TOKEN",
        "limit": 7,
    })
    assert env["ok"] is True
    assert seen["url"] == "http://127.0.0.1:8000/v1/decisions?limit=7"
    assert seen["auth"] == "Bearer " + token
    rendered = json.dumps(env)
    assert token not in rendered
    assert "<redacted>" in rendered


def test_router_promotion_preview_and_human_gate(tmp_path, monkeypatch):
    current = _profile_doc(tmp_path, "current.json", decision="deny")
    candidate = _profile_doc(tmp_path, "candidate.json", decision="allow")
    cfg = tmp_path / "candidate.toml"
    cfg.write_text('[router]\nprofile_path = "/etc/anvil/profile.json"\n', encoding="utf-8")

    preview = mcp.call_tool("router_promote", {
        "profile": candidate,
        "config": str(cfg),
        "current_profile": current,
    })
    assert preview["ok"] is True
    assert preview["data"]["applied"] is False
    assert "--dry-run" in preview["data"]["command"]
    assert preview["data"]["preview"]["diff"]["changed_count"] == 1

    refused = mcp.call_tool("router_promote", {
        "profile": candidate,
        "confirm": True,
        "dry_run": False,
    })
    assert refused["ok"] is False
    assert refused["error"]["code"] == "human_approval_required"

    from anvil_serving import router_manage

    seen = {}

    def fake_promote(profile_path, **kwargs):
        seen["profile_path"] = profile_path
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(router_manage, "cmd_promote", fake_promote)
    applied = mcp.call_tool("router_promote", {
        "profile": candidate,
        "config": str(cfg),
        "confirm": True,
        "dry_run": False,
        "human_approved": True,
    })
    assert applied["ok"] is True
    assert applied["data"]["applied"] is True
    assert seen["profile_path"] == candidate
    assert seen["kwargs"]["config_path"] == str(cfg)


def test_route_decision_posts_to_v1_route(monkeypatch):
    seen = {}

    def open_route(req, timeout=5):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return Resp(b'{"tier":"local","model":"m","provider":"fast"}')

    monkeypatch.setattr(mcp, "_urlopen_no_proxy_no_redirect", open_route)
    env = mcp.call_tool("route_decision", {
        "base_url": "http://127.0.0.1:8000/v1",
        "prompt": "fix this",
    })
    assert env["ok"] is True
    assert seen["url"] == "http://127.0.0.1:8000/v1/route"
    assert seen["body"]["messages"][0]["content"] == "fix this"
    assert env["data"]["response"]["tier"] == "local"


def test_route_decision_redacts_resolved_router_token_from_responses(monkeypatch):
    token = "router-secret-token"
    monkeypatch.setenv("ANVIL_ROUTER_TOKEN", token)

    def open_echo(req, timeout=5):
        assert req.get_header("Authorization") == "Bearer " + token
        return Resp(json.dumps({"echo": token}).encode("utf-8"))

    monkeypatch.setattr(mcp, "_urlopen_no_proxy_no_redirect", open_echo)
    env = mcp.call_tool("route_decision", {
        "base_url": "http://127.0.0.1:8000/v1",
        "prompt": "fix this",
        "api_key_env": "ANVIL_ROUTER_TOKEN",
    })
    rendered = json.dumps(env)
    assert env["ok"] is True
    assert token not in rendered
    assert "<redacted>" in rendered


def test_route_decision_redacts_resolved_router_token_from_http_errors(monkeypatch):
    token = "router-secret-token"
    monkeypatch.setenv("ANVIL_ROUTER_TOKEN", token)

    def open_500(req, timeout=5):
        raise urllib.error.HTTPError(
            req.full_url,
            500,
            "boom",
            {},
            io.BytesIO(json.dumps({"echo": token}).encode("utf-8")),
        )

    monkeypatch.setattr(mcp, "_urlopen_no_proxy_no_redirect", open_500)
    env = mcp.call_tool("route_decision", {
        "base_url": "http://127.0.0.1:8000/v1",
        "prompt": "fix this",
        "api_key_env": "ANVIL_ROUTER_TOKEN",
    })
    rendered = json.dumps(env)
    assert env["ok"] is False
    assert env["error"]["code"] == "route_http_error"
    assert token not in rendered
    assert "<redacted>" in rendered


def test_openclaw_sync_preview_uses_harness_logic_and_env_ref(tmp_path):
    cfg = _router_cfg(tmp_path)
    env = mcp.call_tool("openclaw_sync", {
        "config": cfg,
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key_env": "ANVIL_ROUTER_TOKEN",
    })
    assert env["ok"] is True
    assert env["data"]["applied"] is False
    preview = env["data"]["preview"]
    assert preview["model_ids"] == ["chat", "chat-fast", "review"]
    # Secret hygiene: config references the env var by name; no literal secret is resolved.
    assert preview["api_key"] == "${ANVIL_ROUTER_TOKEN}"


def test_openclaw_gateway_status_is_bounded_and_structured(monkeypatch):
    from anvil_serving import harness

    seen = {}
    monkeypatch.setattr(
        harness,
        "openclaw_gateway_status",
        lambda **kwargs: seen.update(kwargs) or {
            "ok": True,
            "returncode": 0,
            "status": {"status": "running"},
        },
    )
    env = mcp.call_tool("openclaw_gateway_status", {
        "timeout_seconds": 9,
        "max_output_bytes": 4096,
    })
    assert env["ok"] is True
    assert env["data"]["status"] == {"status": "running"}
    assert seen == {"timeout_seconds": 9, "max_output_bytes": 4096}


def test_openclaw_gateway_status_failure_is_classified(monkeypatch):
    from anvil_serving import harness

    monkeypatch.setattr(
        harness,
        "openclaw_gateway_status",
        lambda **_kwargs: {"ok": False, "returncode": 1, "stderr": "not running"},
    )
    env = mcp.call_tool("openclaw_gateway_status", {})
    assert env["ok"] is False
    assert env["error"]["code"] == "command_failed"


def test_openclaw_sync_preview_can_include_skills(tmp_path):
    cfg = _router_cfg(tmp_path)
    env = mcp.call_tool("openclaw_sync", {
        "config": cfg,
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key_env": "ANVIL_ROUTER_TOKEN",
        "skills": True,
        "skill_dir": "/opt/anvil-serving/examples/openclaw/skills",
    })
    assert env["ok"] is True
    preview = env["data"]["preview"]
    assert preview["skills"] is True
    assert preview["skill_name"] == "anvil-serving-workbench"
    assert preview["skill_load_dirs"] == ["/opt/anvil-serving/examples/openclaw/skills"]
    assert preview["agent_models"]["anvil-orchestrator"] == "anvil/review"
    assert preview["agent_models"]["anvil-inventory-scout"] == "anvil/chat-fast"
    assert preview["agent_models"]["anvil-quality-critic"] == "anvil/review"
    assert preview["agent_models"]["anvil-adversarial-reviewer"] == "anvil/review"


def test_openclaw_sync_preview_can_include_voice(tmp_path):
    cfg = _router_cfg(tmp_path)
    env = mcp.call_tool("openclaw_sync", {
        "config": cfg,
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key_env": "ANVIL_ROUTER_TOKEN",
        "voice": True,
        "voice_realtime_url": "ws://127.0.0.1:8765/v1/realtime",
    })
    assert env["ok"] is True
    target = env["data"]["target"]
    preview = env["data"]["preview"]
    assert target["voice"] is True
    assert preview["voice"] is True
    assert preview["voice_provider"] == "anvil"
    assert preview["voice_realtime_url"] == "ws://127.0.0.1:8765/v1/realtime"
    assert preview["voice_model"] == "chat-fast"
    assert preview["voice_consult_model"] == "anvil/chat-fast"
    assert preview["voice_consult_thinking_level"] == "off"
    assert preview["voice_consult_bootstrap_context_mode"] == "lightweight"


def test_openclaw_sync_confirmed_apply_forwards_skills(tmp_path, monkeypatch):
    cfg = _router_cfg(tmp_path)
    out = tmp_path / "openclaw.json"
    seen = {}

    from anvil_serving import harness

    def fake_sync(config_path, **kwargs):
        seen["config_path"] = config_path
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(harness, "cmd_sync_openclaw", fake_sync)
    env = mcp.call_tool("openclaw_sync", {
        "config": cfg,
        "out": str(out),
        "skills": True,
        "skill_dir": "/opt/anvil-serving/examples/openclaw/skills",
        "confirm": True,
        "dry_run": False,
    })
    assert env["ok"] is True
    assert env["data"]["applied"] is True
    assert seen["config_path"] == cfg
    assert seen["kwargs"]["skills"] is True
    assert seen["kwargs"]["skill_dir"] == "/opt/anvil-serving/examples/openclaw/skills"


def test_openclaw_sync_confirmed_apply_forwards_voice(tmp_path, monkeypatch):
    cfg = _router_cfg(tmp_path)
    out = tmp_path / "openclaw.json"
    seen = {}

    from anvil_serving import harness

    def fake_sync(config_path, **kwargs):
        seen["config_path"] = config_path
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(harness, "cmd_sync_openclaw", fake_sync)
    env = mcp.call_tool("openclaw_sync", {
        "config": cfg,
        "out": str(out),
        "voice": True,
        "voice_realtime_url": "ws://127.0.0.1:8765/v1/realtime",
        "voice_model": "fast-local",
        "voice_consult_model": "anvil/chat",
        "voice_consult_thinking_level": "low",
        "voice_consult_bootstrap_context_mode": "full",
        "voice_api_key_env": "ANVIL_VOICE_REALTIME_TOKEN",
        "confirm": True,
        "dry_run": False,
    })
    assert env["ok"] is True
    assert env["data"]["applied"] is True
    assert seen["config_path"] == cfg
    assert seen["kwargs"]["voice"] is True
    assert seen["kwargs"]["voice_realtime_url"] == "ws://127.0.0.1:8765/v1/realtime"
    assert seen["kwargs"]["voice_model"] == "fast-local"
    assert seen["kwargs"]["voice_consult_model"] == "anvil/chat"
    assert seen["kwargs"]["voice_consult_thinking_level"] == "low"
    assert seen["kwargs"]["voice_consult_bootstrap_context_mode"] == "full"
    assert seen["kwargs"]["voice_api_key_env"] == "ANVIL_VOICE_REALTIME_TOKEN"


def test_models_inventory_reads_structured_catalog(tmp_path):
    catalog = _catalog(tmp_path)
    env = mcp.call_tool("models_inventory", {"catalog_dir": str(catalog)})
    assert env["ok"] is True
    data = env["data"]
    assert data["synced"] is False
    assert data["catalog"]["count"] == 1
    assert data["catalog"]["entries"][0]["id"] == "owner/repo"
    assert data["catalog"]["entries"][0]["summary_path"].endswith("owner__repo.json")


def test_models_inventory_missing_catalog_points_to_sync(tmp_path):
    catalog = tmp_path / "missing library"
    env = mcp.call_tool("models_inventory", {"catalog_dir": str(catalog)})
    assert env["ok"] is False
    assert env["error"]["code"] == "catalog_not_found"
    assert "error.details.command" in env["error"]["message"]
    assert env["error"]["details"]["command"][-3:] == ["sync", "--out", str(catalog)]


def test_models_inventory_sync_preview_is_argv(tmp_path):
    catalog = tmp_path / "model-library"
    env = mcp.call_tool("models_inventory", {
        "catalog_dir": str(catalog),
        "hf_roots": "C:/hf-cache",
        "model_dirs": "D:/models",
        "sync": True,
    })
    assert env["ok"] is True
    assert env["data"]["synced"] is False
    assert env["data"]["dry_run"] is True
    cmd = env["data"]["command"]
    assert cmd[:3] == [sys.executable, "-m", "anvil_serving.cli"]
    assert cmd[3:7] == ["models", "sync", "--out", str(catalog)]
    assert "--hf-roots" in cmd and "C:/hf-cache" in cmd
    assert "--model-dirs" in cmd and "D:/models" in cmd


def test_models_inventory_confirmed_sync_returns_catalog_counts(tmp_path, monkeypatch):
    catalog = tmp_path / "model-library"

    def fake_run(argv, **kwargs):
        assert argv[3:7] == ["models", "sync", "--out", str(catalog)]
        cards = catalog / "cards"
        cards.mkdir(parents=True)
        (catalog / "INDEX.md").write_text("# generated\n", encoding="utf-8")
        (cards / "owner__repo.json").write_text(json.dumps({
            "id": "owner/repo",
            "format": "safetensors",
            "model_type": "qwen3",
        }), encoding="utf-8")
        return proc(0, "wrote INDEX.md + 1 summaries\n", "")

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    env = mcp.call_tool("models_inventory", {
        "catalog_dir": str(catalog),
        "sync": True,
        "confirm": True,
        "timeout_seconds": 1,
    })
    assert env["ok"] is True
    assert env["data"]["synced"] is True
    assert env["data"]["catalog"]["count"] == 1
    assert env["data"]["stdout"] == "wrote INDEX.md + 1 summaries\n"


def test_host_summary_tool_is_structured_and_read_only(monkeypatch):
    from anvil_serving import host

    monkeypatch.setattr(host, "_host_total_gb", lambda _run=None: 93.7)
    monkeypatch.setattr(host, "_wsl_vm_memory_gb", lambda _run=None: 62.8)
    monkeypatch.setattr(host, "_gpus", lambda _run=None: [
        ("0", "RTX 5090", 27.7, 31.8),
        ("1", "RTX PRO 6000", 85.0, 95.6),
    ])
    env = mcp.call_tool("host_summary", {})
    assert env["ok"] is True
    data = env["data"]
    assert data["mutates"] is False
    assert data["host_ram_gb"] == 93.7
    assert data["docker"]["memory_cap_gb"] == 62.8
    assert data["recommended_wsl_memory_gb"] == 80
    assert data["gpus"][1]["name"] == "RTX PRO 6000"
    assert {check["name"] for check in data["checks"]} == {
        "host_ram", "docker_wsl_memory", "gpu_inventory",
    }


def test_host_summary_rejects_arguments():
    env = mcp.call_tool("host_summary", {"confirm": True})
    assert env["ok"] is False
    assert env["error"]["code"] == "bad_argument"


def test_host_manage_previews_without_calling_host(monkeypatch):
    from anvil_serving import host

    monkeypatch.setattr(
        host,
        "cmd_wsl_config",
        lambda **_kwargs: pytest.fail("host preview applied a repair"),
    )
    env = mcp.call_tool("host_manage", {
        "action": "wsl-config", "memory": 80, "confirm": False,
    })
    assert env["ok"] is True
    assert env["data"]["applied"] is False
    assert env["data"]["target"]["memory"] == 80


def test_host_manage_confirmed_restart_uses_noninteractive_gate(monkeypatch):
    from anvil_serving import host

    seen = {}
    monkeypatch.setattr(
        host,
        "cmd_restart_docker",
        lambda **kwargs: seen.update(kwargs) or 0,
    )
    env = mcp.call_tool("host_manage", {
        "action": "restart-docker", "confirm": True, "dry_run": False,
    })
    assert env["ok"] is True
    assert env["data"]["applied"] is True
    assert seen == {"force": True}


def test_host_manage_rejects_wsl_arguments_for_restart():
    env = mcp.call_tool("host_manage", {
        "action": "restart-docker", "memory": 80, "confirm": True, "dry_run": False,
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "bad_argument"


def test_cache_prune_plan_returns_json_plan_and_dry_run_report(monkeypatch):
    from anvil_serving import cache_prune

    seen = {}
    plan = {
        "candidates": [{
            "id": "fp8/moe",
            "reason": "incompatible-sm120",
            "format": "safetensors",
            "size_gb": 80.0,
            "reclaimable_bytes": 80_000_000_000,
            "dead_everywhere": True,
            "local_path": "C:/hf/models--fp8--moe",
        }],
        "protected": ["keep/me"],
        "by_reason": {"incompatible-sm120": {"count": 1, "bytes": 80_000_000_000}},
        "total_reclaimable_gb": 80.0,
        "total_reclaimable_bytes": 80_000_000_000,
    }

    def fake_build_plan(mixture):
        seen["mixture"] = set(mixture)
        return plan

    def fake_execute_plan(plan_arg, *, dry_run=True, include_servable=False):
        seen["plan"] = plan_arg
        seen["dry_run"] = dry_run
        seen["include_servable"] = include_servable
        return {
            "dry_run": dry_run,
            "include_servable": include_servable,
            "would_delete": ["fp8/moe"],
            "deleted": [],
            "kept": [],
            "skipped": [],
            "reclaimed_bytes": 0,
            "planned_bytes": 80_000_000_000,
            "reclaimed_gb": 0.0,
        }

    monkeypatch.setattr(cache_prune, "build_plan", fake_build_plan)
    monkeypatch.setattr(cache_prune, "execute_plan", fake_execute_plan)

    env = mcp.call_tool("cache_prune_plan", {
        "mixture": ["keep/me", "keep/me"],
        "include_servable": True,
    })
    assert env["ok"] is True
    data = env["data"]
    assert data["dry_run"] is True
    assert data["deletion_available"] is False
    assert data["human_gate_required"] is True
    assert data["mixture"] == ["keep/me"]
    assert data["plan"] == plan
    assert data["report"]["would_delete"] == ["fp8/moe"]
    assert data["command"][:6] == [
        sys.executable,
        "-m",
        "anvil_serving.cli",
        "models",
        "cache",
        "prune",
    ]
    assert "--json" in data["command"]
    assert data["command"][data["command"].index("--mixture") + 1] == "keep/me"
    assert "--include-servable" in data["command"]
    assert seen == {
        "mixture": {"keep/me"},
        "plan": plan,
        "dry_run": True,
        "include_servable": True,
    }


def test_cache_prune_plan_refuses_deletion_requests(monkeypatch):
    from anvil_serving import cache_prune

    monkeypatch.setattr(cache_prune, "build_plan", lambda mixture: (_ for _ in ()).throw(
        AssertionError("delete requests must be rejected before planning")
    ))
    for args in (
        {"execute": True},
        {"confirm": True},
        {"yes": True},
        {"dry_run": False},
    ):
        env = mcp.call_tool("cache_prune_plan", args)
        assert env["ok"] is False
        assert env["error"]["code"] == "cache_prune_delete_not_available"


def test_cache_prune_plan_rejects_string_confirm_and_does_not_echo_tokens(monkeypatch):
    from anvil_serving import cache_prune

    monkeypatch.setattr(cache_prune, "build_plan", lambda mixture: (_ for _ in ()).throw(
        AssertionError("invalid arguments must be rejected before planning")
    ))
    string_bool = mcp.call_tool("cache_prune_plan", {"confirm": "false"})
    assert string_bool["ok"] is False
    assert string_bool["error"]["code"] == "bad_argument"

    token = "controller-secret-token"
    raw_token = mcp.call_tool("cache_prune_plan", {"api_key": token})
    assert raw_token["ok"] is False
    assert raw_token["error"]["code"] == "bad_argument"
    assert token not in json.dumps(raw_token)


def test_serves_manage_preview_is_dry_run_argv(tmp_path):
    manifest = _manifest(tmp_path)
    env = mcp.call_tool("serves_manage", {
        "action": "down",
        "manifest": manifest,
        "names": ["fast"],
    })
    assert env["ok"] is True
    assert env["data"]["applied"] is False
    cmd = env["data"]["command"]
    assert cmd[:4] == [sys.executable, "-m", "anvil_serving.cli", "serves"]
    assert "down" in cmd
    assert "--dry-run" in cmd
    assert cmd[cmd.index("--manifest") + 1] == manifest


def test_serves_manage_confirmed_action_requires_dry_run_false_to_run(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["timeout"] = kwargs.get("timeout")
        return proc(0, "stopped\n", "")

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    env = mcp.call_tool("serves_manage", {
        "action": "down",
        "manifest": manifest,
        "names": ["fast"],
        "confirm": True,
        "timeout_seconds": 7,
    })
    assert env["ok"] is True
    assert env["data"]["applied"] is False
    assert "--dry-run" in env["data"]["command"]
    assert seen == {}

    env = mcp.call_tool("serves_manage", {
        "action": "down",
        "manifest": manifest,
        "names": ["fast"],
        "confirm": True,
        "dry_run": False,
        "timeout_seconds": 7,
    })
    assert env["ok"] is True
    assert env["data"]["applied"] is True
    assert "--dry-run" not in seen["argv"]
    assert seen["argv"][3:5] == ["serves", "down"]
    assert seen["timeout"] == 7
    assert env["data"]["stdout"] == "stopped\n"


def test_serves_manage_preview_forces_dry_run_without_confirm(tmp_path):
    manifest = _manifest(tmp_path)
    env = mcp.call_tool("serves_manage", {
        "action": "down",
        "manifest": manifest,
        "names": ["fast"],
        "dry_run": False,
    })
    assert env["ok"] is True
    assert env["data"]["applied"] is False
    assert env["data"]["dry_run"] is True
    assert "--dry-run" in env["data"]["command"]


def test_serves_manage_manifest_actions_require_explicit_names(tmp_path):
    manifest = _manifest(tmp_path)
    env = mcp.call_tool("serves_manage", {
        "action": "down",
        "manifest": manifest,
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "missing_argument"


def test_serves_manage_up_preview_includes_manifest_commands(tmp_path):
    manifest = _manifest(tmp_path, up=True)
    env = mcp.call_tool("serves_manage", {
        "action": "up",
        "manifest": manifest,
        "names": ["fast"],
    })
    assert env["ok"] is True
    commands = env["data"]["plan"]["commands"]
    assert commands[0]["kind"] == "manifest_up_when_absent_or_compose_reconcile"
    assert commands[0]["argv"][:3] == ["docker", "compose", "-f"]
    assert commands[0]["argv"][-2:] == ["up", "-d"]
    assert commands[1]["kind"] == "docker_start_when_existing_script_serve_stopped"


def test_serves_manage_rm_literal_requires_allow_literal(tmp_path):
    manifest = _manifest(tmp_path)
    blocked = mcp.call_tool("serves_manage", {
        "action": "rm",
        "manifest": manifest,
        "names": ["port-squatter"],
    })
    assert blocked["ok"] is False
    assert blocked["error"]["code"] == "literal_container_requires_allow"

    allowed = mcp.call_tool("serves_manage", {
        "action": "rm",
        "manifest": manifest,
        "names": ["port-squatter"],
        "allow_literal": True,
    })
    assert allowed["ok"] is True
    assert allowed["data"]["applied"] is False
    assert "--dry-run" in allowed["data"]["command"]
    assert allowed["data"]["plan"]["targets"] == []
    assert allowed["data"]["plan"]["commands"] == [{
        "kind": "docker_rm",
        "target": "port-squatter",
        "argv": ["docker", "rm", "-f", "port-squatter"],
    }]


def test_serves_manage_bad_action_and_missing_manifest(tmp_path):
    bad = mcp.call_tool("serves_manage", {"action": "restart"})
    assert bad["ok"] is False
    assert bad["error"]["code"] == "bad_action"

    missing = mcp.call_tool("serves_manage", {
        "action": "down",
        "manifest": str(tmp_path / "missing.toml"),
    })
    assert missing["ok"] is False
    assert missing["error"]["code"] == "manifest_not_found"


def test_serves_logs_runs_bounded_tail_for_one_serve(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["timeout"] = kwargs.get("timeout")
        seen["capture_output"] = kwargs.get("capture_output")
        seen["text"] = kwargs.get("text")
        kwargs["stdout"].write(b"LOG\n")
        return proc(0)

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    env = mcp.call_tool("serves_logs", {
        "manifest": manifest,
        "names": ["fast"],
        "tail": 5,
        "since": "10m",
        "timeout_seconds": 3,
    })
    assert env["ok"] is True
    assert env["data"]["bounded"] is True
    assert seen["argv"][3:5] == ["serves", "logs"]
    assert seen["argv"][seen["argv"].index("--tail") + 1] == "5"
    assert seen["argv"][seen["argv"].index("--since") + 1] == "10m"
    assert "--follow" not in seen["argv"]
    assert seen["timeout"] == 3
    assert seen["capture_output"] is None
    assert seen["text"] is None
    assert env["data"]["stdout"] == "LOG\n"


def test_serves_logs_rejects_unknown_manifest_serve_before_running(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)

    def fake_run(argv, **kwargs):
        raise AssertionError("serves_logs should reject unknown manifest targets before subprocess")

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    env = mcp.call_tool("serves_logs", {
        "manifest": manifest,
        "names": ["missing"],
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "no_matching_serve"


def test_serves_logs_truncates_output_to_byte_cap(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)

    def fake_run(argv, **kwargs):
        kwargs["stdout"].write(b"A" * 2048)
        kwargs["stderr"].write(b"B" * 2048)
        return proc(0)

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    env = mcp.call_tool("serves_logs", {
        "manifest": manifest,
        "names": ["fast"],
        "max_output_bytes": 1024,
    })
    assert env["ok"] is True
    assert len(env["data"]["stdout"]) == 1024
    assert len(env["data"]["stderr"]) == 1024
    assert env["data"]["stdout_truncated"] is True
    assert env["data"]["stderr_truncated"] is True


def test_serves_logs_rejects_unbounded_follow_and_bad_tail(tmp_path):
    manifest = _manifest(tmp_path)
    follow = mcp.call_tool("serves_logs", {
        "manifest": manifest,
        "names": ["fast"],
        "follow": True,
    })
    assert follow["ok"] is False
    assert follow["error"]["code"] == "follow_not_allowed"

    tail = mcp.call_tool("serves_logs", {
        "manifest": manifest,
        "names": ["fast"],
        "tail": 5001,
    })
    assert tail["ok"] is False
    assert tail["error"]["code"] == "bad_argument"


def test_serves_logs_requires_exactly_one_name(tmp_path):
    manifest = _manifest(tmp_path)
    env = mcp.call_tool("serves_logs", {"manifest": manifest, "names": []})
    assert env["ok"] is False
    assert env["error"]["code"] == "bad_argument"


def test_serves_manage_and_logs_bool_arguments_must_be_real_booleans(tmp_path):
    manifest = _manifest(tmp_path)
    manage = mcp.call_tool("serves_manage", {
        "action": "down",
        "manifest": manifest,
        "confirm": "false",
    })
    assert manage["ok"] is False
    assert manage["error"]["code"] == "bad_argument"

    logs = mcp.call_tool("serves_logs", {
        "manifest": manifest,
        "names": ["fast"],
        "follow": "false",
    })
    assert logs["ok"] is False
    assert logs["error"]["code"] == "bad_argument"


def test_voice_manage_preview_is_dry_run_argv(tmp_path):
    config = tmp_path / "voice.toml"
    config.write_text(textwrap.dedent("""
        [voice]
        name = "mini"
        realtime_host = "127.0.0.1"
        realtime_port = 8765

        [voice.llm]
        base_url = "http://127.0.0.1:8000/v1"
        model = "chat"

        [voice.stt]
        base_url = "http://127.0.0.1:30010/v1"
        model = "mlx-stt"
        lifecycle = "native"
        start_command = "python -m mlx_audio.server --port 30010"
        stop_timeout = 3.0
        pid_file = "/tmp/stt.pid"
        log_file = "/tmp/stt.log"

        [voice.tts]
        base_url = "http://127.0.0.1:30011/v1"
        model = "mlx-tts"
        lifecycle = "native"
        start_command = "python -m mlx_audio.server --port 30011"
        pid_file = "/tmp/tts.pid"
        log_file = "/tmp/tts.log"
    """), encoding="utf-8")

    env = mcp.call_tool("voice_manage", {
        "action": "up",
        "config": str(config),
        "topology": _voice_topology(tmp_path),
    })

    assert env["ok"] is True
    data = env["data"]
    assert data["applied"] is False
    assert data["command"][:6] == [
        sys.executable, "-m", "anvil_serving.cli", "voice", "audio", "up"
    ]
    assert "--dry-run" in data["command"]
    assert data["plan"]["audio_serves"][0]["lifecycle"] == "native"
    assert data["plan"]["audio_serves"][0]["start_command"][:3] == ["python", "-m", "mlx_audio.server"]
    assert data["plan"]["audio_serves"][0]["stop_timeout"] == 3.0


def test_voice_manage_accepts_profile_selection(tmp_path):
    config = tmp_path / "voice.toml"
    config.write_text(textwrap.dedent("""
        [voice]
        name = "openclaw"
        realtime_host = "127.0.0.1"
        realtime_port = 8765

        [voice.llm]
        base_url = "http://127.0.0.1:8000/v1"
        model = "chat"

        [voice.stt]
        base_url = "http://127.0.0.1:30010/v1"
        model = "mlx-stt"
        lifecycle = "native"
        start_command = "python -m mlx_audio.server --port 30010"
        pid_file = "/tmp/stt.pid"
        log_file = "/tmp/stt.log"

        [voice.tts]
        base_url = "http://127.0.0.1:30011/v1"
        model = "mlx-tts"
        lifecycle = "native"
        start_command = "python -m mlx_audio.server --port 30011"
        pid_file = "/tmp/tts.pid"
        log_file = "/tmp/tts.log"

        [voice.profiles.dark-audio.stt]
        base_url = "http://100.87.34.66:30110/v1"
        model = "tdt_ctc-110m"
        lifecycle = "external"

        [voice.profiles.dark-audio.tts]
        base_url = "http://100.87.34.66:30111/v1"
        model = "kokoro"
        lifecycle = "external"
    """), encoding="utf-8")

    env = mcp.call_tool("voice_manage", {
        "action": "up",
        "config": str(config),
        "profile": "dark-audio",
        "topology": _voice_topology(tmp_path),
    })

    assert env["ok"] is True
    data = env["data"]
    assert data["command"][:8] == [
        sys.executable,
        "-m",
        "anvil_serving.cli",
        "voice",
        "audio",
        "up",
        "--config",
        str(config),
    ]
    assert data["command"][8:10] == ["--profile", "dark-audio"]
    assert data["command"][10:12] == ["--topology", _voice_topology(tmp_path)]
    assert data["plan"]["profile"] == "dark-audio"
    assert data["plan"]["available_profiles"] == ["dark-audio"]
    assert data["plan"]["audio_serves"][0]["lifecycle"] == "external"
    assert data["plan"]["audio_serves"][0]["base_url"] == "http://100.87.34.66:30110/v1"


def test_voice_manage_profile_does_not_validate_unprofiled_base_first(tmp_path):
    config = tmp_path / "voice.toml"
    config.write_text(textwrap.dedent("""
        [voice]
        name = "openclaw"
        realtime_host = "127.0.0.1"
        realtime_port = 8765

        [voice.llm]
        base_url = "http://127.0.0.1:8000/v1"
        model = "chat"

        [voice.stt]
        base_url = "http://localhost:30010/v1"
        model = "bad-base-stt"

        [voice.tts]
        base_url = "http://localhost:30011/v1"
        model = "bad-base-tts"

        [voice.profiles.dark-audio.stt]
        base_url = "http://100.87.34.66:30110/v1"
        model = "tdt_ctc-110m"
        lifecycle = "external"

        [voice.profiles.dark-audio.tts]
        base_url = "http://100.87.34.66:30111/v1"
        model = "kokoro"
        lifecycle = "external"
    """), encoding="utf-8")

    env = mcp.call_tool("voice_manage", {
        "action": "up",
        "config": str(config),
        "profile": "dark-audio",
        "topology": _voice_topology(tmp_path),
    })

    assert env["ok"] is True
    assert env["data"]["plan"]["profile"] == "dark-audio"
    assert env["data"]["plan"]["audio_serves"][0]["base_url"] == "http://100.87.34.66:30110/v1"


def test_voice_manage_confirmed_action_requires_dry_run_false_to_run(tmp_path, monkeypatch):
    config = tmp_path / "voice.toml"
    config.write_text(textwrap.dedent("""
        [voice]
        name = "mini"
        realtime_host = "127.0.0.1"
        realtime_port = 8765

        [voice.llm]
        base_url = "http://127.0.0.1:8000/v1"
        model = "chat"

        [voice.stt]
        base_url = "http://127.0.0.1:30010/v1"
        model = "mlx-stt"
        lifecycle = "external"

        [voice.tts]
        base_url = "http://127.0.0.1:30011/v1"
        model = "mlx-tts"
        lifecycle = "external"
    """), encoding="utf-8")
    preview = mcp.call_tool("voice_manage", {
        "action": "down",
        "config": str(config),
        "topology": _voice_topology(tmp_path),
        "confirm": True,
    })
    assert preview["ok"] is True
    assert preview["data"]["applied"] is False

    applied = mcp.call_tool("voice_manage", {
        "action": "down",
        "config": str(config),
        "topology": _voice_topology(tmp_path),
        "confirm": True,
        "dry_run": False,
        "timeout_seconds": 7,
    })
    assert applied["ok"] is True
    assert applied["data"]["applied"] is False
    assert applied["data"]["command"][:6] == [
        sys.executable, "-m", "anvil_serving.cli", "voice", "audio", "down"
    ]
    assert "--dry-run" not in applied["data"]["command"]
    assert applied["data"]["target"]["timeout_seconds"] == 7
    assert all(
        item["state"] == "external"
        for item in applied["data"]["lifecycle"]["serves"]
    )


def test_voice_manage_confirmed_native_action_bridges_to_cli(tmp_path, monkeypatch):
    config = tmp_path / "voice.toml"
    config.write_text(textwrap.dedent("""
        [voice]
        name = "mini"
        realtime_host = "127.0.0.1"
        realtime_port = 8765

        [voice.llm]
        base_url = "http://127.0.0.1:8000/v1"
        model = "chat"

        [voice.stt]
        base_url = "http://127.0.0.1:30010/v1"
        model = "mlx-stt"
        lifecycle = "native"
        start_command = "python -m mlx_audio.server --port 30010"

        [voice.tts]
        base_url = "http://127.0.0.1:30011/v1"
        model = "mlx-tts"
        lifecycle = "external"
    """), encoding="utf-8")
    seen = {}

    from anvil_serving.voice import cli as voice_cli

    def fake_lifecycle(data, action, **kwargs):
        seen["voice"] = data["voice"]["name"]
        seen["action"] = action
        seen["timeout_seconds"] = kwargs["timeout_seconds"]
        return {"action": action, "dry_run": False, "returncode": 0, "serves": []}

    monkeypatch.setattr(voice_cli, "execute_audio_lifecycle", fake_lifecycle)

    applied = mcp.call_tool("voice_manage", {
        "action": "up",
        "config": str(config),
        "topology": _voice_topology(tmp_path),
        "confirm": True,
        "dry_run": False,
        "timeout_seconds": 11,
    })

    assert applied["ok"] is True
    assert applied["data"]["applied"] is True
    assert applied["data"]["command"][:6] == [
        sys.executable, "-m", "anvil_serving.cli", "voice", "audio", "up"
    ]
    assert "--dry-run" not in applied["data"]["command"]
    assert applied["data"]["target"]["timeout_seconds"] == 11
    assert seen == {"voice": "mini", "action": "up", "timeout_seconds": 11.0}


def test_voice_manage_bad_action_and_bad_config(tmp_path):
    bad_action = mcp.call_tool("voice_manage", {"action": "restart"})
    assert bad_action["ok"] is False
    assert bad_action["error"]["code"] == "bad_action"

    missing = mcp.call_tool("voice_manage", {
        "action": "up",
        "config": str(tmp_path / "missing.toml"),
        "topology": _voice_topology(tmp_path),
    })
    assert missing["ok"] is False
    assert missing["error"]["code"] == "bad_config"


def test_voice_manage_requires_topology_before_preview(tmp_path, monkeypatch):
    monkeypatch.delenv("ANVIL_VOICE_TOPOLOGY", raising=False)
    config = tmp_path / "voice.toml"
    config.write_text(textwrap.dedent("""
        [voice]
        [voice.llm]
        base_url = "http://127.0.0.1:8000/v1"
        model = "chat"
        [voice.stt]
        base_url = "http://127.0.0.1:30010/v1"
        model = "stt"
        lifecycle = "external"
        [voice.tts]
        base_url = "http://127.0.0.1:30011/v1"
        model = "tts"
        lifecycle = "external"
    """), encoding="utf-8")

    result = mcp.call_tool("voice_manage", {"action": "up", "config": str(config)})

    assert result["ok"] is False
    assert result["error"]["code"] == "missing_topology"


def test_voice_manage_refuses_local_execution_when_topology_identity_is_mini(tmp_path):
    config = tmp_path / "voice.toml"
    config.write_text(textwrap.dedent("""
        [voice]
        [voice.llm]
        base_url = "http://127.0.0.1:8000/v1"
        model = "chat"
        [voice.stt]
        base_url = "http://127.0.0.1:30010/v1"
        model = "stt"
        lifecycle = "external"
        [voice.tts]
        base_url = "http://127.0.0.1:30011/v1"
        model = "tts"
        lifecycle = "external"
    """), encoding="utf-8")

    result = mcp.call_tool("voice_manage", {
        "action": "up",
        "config": str(config),
        "topology": "examples/fakoli-dark/operator-topology.toml",
    })

    assert result["ok"] is False
    assert result["error"]["code"] == "audio_target_refused"


def test_voice_manage_status_and_logs_are_immediate_and_bounded(tmp_path, monkeypatch):
    from anvil_serving.voice import cli as voice_cli

    config = tmp_path / "voice.toml"
    config.write_text(textwrap.dedent("""
        [voice]
        [voice.llm]
        base_url = "http://127.0.0.1:8000/v1"
        model = "chat"
        [voice.stt]
        base_url = "http://127.0.0.1:30010/v1"
        model = "stt"
        lifecycle = "external"
        [voice.tts]
        base_url = "http://127.0.0.1:30011/v1"
        model = "tts"
        lifecycle = "external"
    """), encoding="utf-8")
    topology = _voice_topology(tmp_path)
    monkeypatch.setattr(
        voice_cli,
        "cmd_audio_status",
        lambda args: (print("audio status ok"), 0)[1],
    )
    monkeypatch.setattr(
        voice_cli,
        "cmd_audio_logs",
        lambda args: (print("audio logs ok"), 0)[1],
    )

    status = mcp.call_tool("voice_manage", {
        "action": "status",
        "config": str(config),
        "topology": topology,
        "ready_timeout": 1.5,
    })
    logs = mcp.call_tool("voice_manage", {
        "action": "logs",
        "config": str(config),
        "topology": topology,
        "tail": 17,
    })

    assert status["ok"] is True
    assert status["data"]["applied"] is False
    assert status["data"]["output"] == "audio status ok\n"
    assert status["data"]["command"][-2:] == ["--ready-timeout", "1.5"]
    assert logs["ok"] is True
    assert logs["data"]["output"] == "audio logs ok\n"
    assert logs["data"]["command"][-2:] == ["--tail", "17"]


def test_voice_manage_rejects_non_finite_ready_timeout(tmp_path):
    result = mcp.call_tool("voice_manage", {
        "action": "status",
        "config": str(tmp_path / "unused.toml"),
        "topology": _voice_topology(tmp_path),
        "ready_timeout": float("nan"),
    })

    assert result["ok"] is False
    assert result["error"]["code"] == "bad_argument"


def test_voice_proxy_manage_is_persistent_typed_and_model_free(tmp_path, monkeypatch):
    from anvil_serving.voice import realtime_service

    config = tmp_path / "voice.toml"
    config.write_text(textwrap.dedent("""
        [voice]
        name = "mini-proxy"
        realtime_host = "127.0.0.1"
        realtime_port = 8765

        [voice.llm]
        base_url = "http://127.0.0.1:8000/v1"
        model = "chat"

        [voice.stt]
        base_url = "http://127.0.0.1:30110/v1"
        model = "remote-stt"
        lifecycle = "external"

        [voice.tts]
        base_url = "http://127.0.0.1:30111/v1"
        model = "remote-tts"
        lifecycle = "external"
    """), encoding="utf-8")
    topology = Path(__file__).parents[1] / "examples" / "fakoli-dark" / "operator-topology.toml"
    calls = []

    class FakeProcess:
        def __init__(self, process_config):
            calls.append(("init", process_config.owner, process_config.topology_path))

        def status(self):
            calls.append(("status",))
            return {"action": "status", "owner": "fakoli-mini", "returncode": 0}

        def up(self, *, dry_run=False):
            calls.append(("up", dry_run))
            return {"action": "up", "owner": "fakoli-mini", "returncode": 0}

    monkeypatch.setattr(realtime_service, "RealtimeProxyProcessService", FakeProcess)
    monkeypatch.setenv("ANVIL_VOICE_TOPOLOGY", str(topology))

    status = mcp.call_tool("voice_proxy_manage", {
        "action": "status", "config": str(config),
    })
    preview = mcp.call_tool("voice_proxy_manage", {
        "action": "up", "config": str(config),
    })
    applied = mcp.call_tool("voice_proxy_manage", {
        "action": "up", "config": str(config), "confirm": True, "dry_run": False,
    })

    assert status["ok"] is True
    assert preview["data"]["dry_run"] is True
    assert applied["data"]["applied"] is True
    assert ("up", True) in calls and ("up", False) in calls
    assert all("audio" not in str(call) for call in calls)


def test_voice_proxy_manage_preserves_successful_noop_as_not_applied(tmp_path, monkeypatch):
    from anvil_serving.voice import realtime_service

    config = tmp_path / "voice.toml"
    config.write_text(textwrap.dedent("""
        [voice]
        realtime_host = "127.0.0.1"
        realtime_port = 8765
        [voice.llm]
        base_url = "http://127.0.0.1:8000/v1"
        model = "chat"
        [voice.stt]
        base_url = "http://127.0.0.1:30110/v1"
        model = "stt"
        lifecycle = "external"
        [voice.tts]
        base_url = "http://127.0.0.1:30111/v1"
        model = "tts"
        lifecycle = "external"
    """), encoding="utf-8")

    class NoopProcess:
        def __init__(self, process_config):
            pass

        def up(self, *, dry_run=False):
            return {
                "action": "up",
                "returncode": 0,
                "applied": False,
                "reason": "already_ready",
            }

    monkeypatch.setattr(realtime_service, "RealtimeProxyProcessService", NoopProcess)

    result = mcp.call_tool("voice_proxy_manage", {
        "action": "up",
        "config": str(config),
        "topology": "examples/fakoli-dark/operator-topology.toml",
        "confirm": True,
        "dry_run": False,
    })

    assert result["ok"] is True
    assert result["data"]["applied"] is False
    assert result["data"]["reason"] == "already_ready"


def test_host_manage_rejects_action_specific_arguments_in_preview():
    env = mcp.call_tool(
        "host_manage",
        {"action": "restart-docker", "memory": 80, "dry_run": True},
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "bad_argument"


def test_capture_is_bounded_without_holding_output_in_memory():
    rc, stdout, stderr = mcp._capture(
        lambda: (print("x" * (mcp._MAX_CAPTURE_CHARS + 100)), 0)[1]
    )
    assert rc == 0
    assert len(stdout) == mcp._MAX_CAPTURE_CHARS
    assert stderr == ""


def test_voice_manage_schema_exposes_action_enum():
    tool = next(item for item in mcp.list_tools() if item["name"] == "voice_manage")
    assert tool["inputSchema"]["properties"]["action"]["enum"] == ["up", "down", "status", "logs"]
    assert tool["inputSchema"]["properties"]["profile"]["type"] == "string"


def test_openclaw_sync_rejects_non_anvil_api_key_env(tmp_path):
    cfg = _router_cfg(tmp_path)
    env = mcp.call_tool("openclaw_sync", {
        "config": cfg,
        "api_key_env": "ANTHROPIC_API_KEY",
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "unsafe_api_key_env"


def test_openclaw_sync_skill_dir_requires_skills(tmp_path):
    cfg = _router_cfg(tmp_path)
    env = mcp.call_tool("openclaw_sync", {
        "config": cfg,
        "skill_dir": "/opt/anvil-serving/examples/openclaw/skills",
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "bad_argument"


def test_openclaw_sync_rejects_bad_voice_api_key_env(tmp_path):
    cfg = _router_cfg(tmp_path)
    env = mcp.call_tool("openclaw_sync", {
        "config": cfg,
        "voice": True,
        "voice_api_key_env": "not a valid env name",
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "bad_voice_api_key_env"


def test_openclaw_sync_rejects_bad_voice_consult_thinking_level(tmp_path):
    cfg = _router_cfg(tmp_path)
    env = mcp.call_tool("openclaw_sync", {
        "config": cfg,
        "voice": True,
        "voice_consult_thinking_level": "turbo",
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "bad_argument"


def test_openclaw_sync_rejects_bad_voice_consult_bootstrap_context_mode(tmp_path):
    cfg = _router_cfg(tmp_path)
    env = mcp.call_tool("openclaw_sync", {
        "config": cfg,
        "voice": True,
        "voice_consult_bootstrap_context_mode": "compact",
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "bad_argument"


def test_openclaw_sync_rejects_unsafe_gateway_target_in_preview(tmp_path):
    cfg = _router_cfg(tmp_path)
    env = mcp.call_tool("openclaw_sync", {
        "config": cfg,
        "gateway_host": "-oProxyCommand=bad",
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "bad_gateway_target"


def test_openclaw_sync_apply_requires_confirmed_target(tmp_path):
    cfg = _router_cfg(tmp_path)
    env = mcp.call_tool("openclaw_sync", {
        "config": cfg,
        "dry_run": False,
        "confirm": True,
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "missing_target"


def test_openclaw_sync_apply_rejects_stdout_out(tmp_path):
    cfg = _router_cfg(tmp_path)
    env = mcp.call_tool("openclaw_sync", {
        "config": cfg,
        "out": "-",
        "dry_run": False,
        "confirm": True,
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "missing_target"
    assert "render-only" in env["error"]["message"]


def test_gateway_restart_is_gated_and_uses_argv_preview():
    env = mcp.call_tool("openclaw_gateway_restart", {
        "gateway_host": "mini",
        "gateway_user": "sd",
    })
    assert env["ok"] is True
    assert env["data"]["restarted"] is False
    assert env["data"]["command"] == [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=60",
        "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=1",
        "--", "sd@mini", 'exec "${SHELL:-sh}" -lc "openclaw gateway restart"',
    ]


def test_gateway_restart_rejects_unsafe_gateway_target_in_preview():
    env = mcp.call_tool("openclaw_gateway_restart", {
        "gateway_host": "mini -oProxyCommand=bad",
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "bad_gateway_target"


def test_bool_arguments_must_be_real_booleans():
    env = mcp.call_tool("openclaw_gateway_restart", {
        "gateway_host": "mini",
        "dry_run": False,
        "confirm": "false",
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "bad_argument"


def test_preflight_and_benchmark_probe_are_argv_not_shell():
    pre = mcp.call_tool("preflight_probe", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "no_thinking": True,
    })
    bench = mcp.call_tool("benchmark_probe", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "requests": 1,
        "concurrency": 1,
    })
    for env in (pre, bench):
        assert env["ok"] is True
        cmd = env["data"]["command"]
        assert isinstance(cmd, list)
        assert cmd[0]  # sys.executable path
        assert any(str(part).startswith("http://127.0.0.1") for part in cmd)


def test_benchmark_artifact_tool_is_separate_from_fast_probe():
    probe = mcp.call_tool("benchmark_probe", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "requests": 1,
        "concurrency": 1,
    })
    artifact = mcp.call_tool("benchmark_artifact", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "artifact_path": ".anvil/benchmarks/local-benchmark.json",
        "requests": 1,
        "concurrency": 1,
    })

    assert probe["ok"] is True
    assert artifact["ok"] is True
    assert "--json-out" not in probe["data"]["command"]
    assert "--json-out" in artifact["data"]["command"]
    assert artifact["data"]["applied"] is False
    assert artifact["data"]["dry_run"] is True


def test_benchmark_artifact_rejects_path_outside_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("ANVIL_BENCHMARK_EVIDENCE_DIR", raising=False)
    monkeypatch.delenv("ANVIL_EVIDENCE_DIR", raising=False)

    env = mcp.call_tool("benchmark_artifact", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "artifact_path": str(tmp_path / "outside.json"),
    })

    assert env["ok"] is False
    assert env["error"]["code"] == "unsafe_artifact_path"


def test_benchmark_artifact_does_not_treat_unmarked_cwd_as_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("ANVIL_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("ANVIL_BENCHMARK_EVIDENCE_DIR", raising=False)
    monkeypatch.delenv("ANVIL_EVIDENCE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    env = mcp.call_tool("benchmark_artifact", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "artifact_path": str(tmp_path / "bench.json"),
    })

    assert env["ok"] is False
    assert env["error"]["code"] == "missing_artifact_root"


def test_benchmark_artifact_does_not_treat_unrelated_git_repo_as_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("ANVIL_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("ANVIL_BENCHMARK_EVIDENCE_DIR", raising=False)
    monkeypatch.delenv("ANVIL_EVIDENCE_DIR", raising=False)
    (tmp_path / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    env = mcp.call_tool("benchmark_artifact", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "artifact_path": str(tmp_path / "bench.json"),
    })

    assert env["ok"] is False
    assert env["error"]["code"] == "missing_artifact_root"


def test_benchmark_artifact_accepts_server_configured_evidence_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(tmp_path))
    artifact_path = tmp_path / "benchmarks" / "preview.json"

    env = mcp.call_tool("benchmark_artifact", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "artifact_path": str(artifact_path),
    })

    assert env["ok"] is True
    assert env["data"]["artifact_path"] == str(artifact_path.resolve())
    assert str(tmp_path.resolve()) in env["data"]["allowed_roots"]


def test_benchmark_artifact_accepts_server_configured_anvil_evidence_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("ANVIL_BENCHMARK_EVIDENCE_DIR", raising=False)
    monkeypatch.setenv("ANVIL_EVIDENCE_DIR", str(tmp_path))

    env = mcp.call_tool("benchmark_artifact", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "artifact_path": str(tmp_path / "benchmarks" / "preview.json"),
    })

    assert env["ok"] is True
    assert str(tmp_path.resolve()) in env["data"]["allowed_roots"]


def test_benchmark_artifact_rejects_base_url_secret_surfaces():
    for base_url in [
        "http://token:secret@127.0.0.1:30000/v1",
        "http://127.0.0.1:30000/v1?token=secret",
        "http://127.0.0.1:30000/v1#secret",
    ]:
        env = mcp.call_tool("benchmark_artifact", {
            "base_url": base_url,
            "model": "local",
            "artifact_path": ".anvil/benchmarks/local.json",
        })

        assert env["ok"] is False
        assert env["error"]["code"] == "bad_base_url"


def test_benchmark_artifact_confirmed_run_writes_json_and_returns_metrics(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(tmp_path))
    artifact_path = tmp_path / "benchmarks" / "local.json"
    seen = {}

    def fake_run(argv, capture_output=True, text=True, timeout=None):
        seen["argv"] = argv
        seen["capture_output"] = capture_output
        seen["text"] = text
        seen["timeout"] = timeout
        out_path = Path(argv[argv.index("--json-out") + 1])
        out_path.write_text(json.dumps({
            "schema": "anvil-serving.benchmark/v1",
            "run_id": "benchmark-20260706T000000Z",
            "base_url": "http://127.0.0.1:30000/v1",
            "model": "local",
            "requests": 2,
            "completed": 2,
            "concurrency": 1,
            "context_tokens": 4096,
            "max_context_tokens": 131072,
            "max_tokens": 64,
            "metrics": {
                "ttft_p50_ms": 120.0,
                "ttft_p95_ms": 180.0,
                "e2e_p50_ms": 240.0,
                "e2e_p95_ms": 320.0,
                "throughput_tok_s": 91.5,
                "output_tokens": 128,
                "prefix_cache_hit_avg": 0.42,
            },
        }), encoding="utf-8")
        return proc(0, "wrote JSON summary\n", "")

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    env = mcp.call_tool("benchmark_artifact", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "artifact_path": str(artifact_path),
        "requests": 2,
        "concurrency": 1,
        "ctx_tokens": 4096,
        "confirm": True,
        "timeout_seconds": 30,
    })

    assert env["ok"] is True
    data = env["data"]
    assert data["applied"] is True
    assert data["dry_run"] is False
    assert Path(data["artifact_path"]) == artifact_path.resolve()
    assert data["summary"]["run_id"] == "benchmark-20260706T000000Z"
    assert data["key_metrics"]["completed"] == 2
    assert data["key_metrics"]["throughput_tok_s"] == 91.5
    assert data["key_metrics"]["prefix_cache_hit_avg"] == 0.42
    assert "--json-out" in seen["argv"]
    assert seen["capture_output"] is True
    assert seen["text"] is True
    assert seen["timeout"] == 30


def test_benchmark_artifact_confirmed_run_rejects_stale_json(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(tmp_path))
    artifact_path = tmp_path / "benchmarks" / "stale.json"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text(json.dumps({
        "schema": "anvil-serving.benchmark/v1",
        "run_id": "old-run",
        "metrics": {"throughput_tok_s": 1.0},
    }), encoding="utf-8")

    monkeypatch.setattr(mcp.subprocess, "run", lambda *a, **k: proc(0, "no write\n", ""))
    env = mcp.call_tool("benchmark_artifact", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "artifact_path": str(artifact_path),
        "confirm": True,
    })

    assert env["ok"] is False
    assert env["error"]["code"] == "artifact_not_written"
    assert not artifact_path.exists()


def test_external_bench_sources_and_report_are_advisory_only(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(tmp_path))
    db = _external_bench_db(tmp_path)

    sources = mcp.call_tool("external_bench_sources", {"db": str(db)})
    assert sources["ok"] is True
    assert sources["data"]["db_exists"] is True
    assert sources["data"]["advisory_only"] is True
    assert sources["data"]["promotion_quality_evidence"] is False
    assert "millstone" in {row["name"] for row in sources["data"]["sources"]}

    report = mcp.call_tool("external_bench_report", {
        "db": str(db),
        "gpu": "RTX PRO 6000",
        "source": "millstone",
        "top": 2,
    })
    assert report["ok"] is True
    data = report["data"]
    assert data["advisory_only"] is True
    assert data["promotion_quality_evidence"] is False
    assert data["filters"]["gpu"] == "RTX PRO 6000"
    assert data["count"] == 2
    assert data["rows"][0]["source_name"] == "millstone"
    assert "throughput_tok_s" in data["columns"]


def test_external_bench_mcp_read_paths_do_not_initialize_missing_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(tmp_path))
    missing = tmp_path / "missing" / "benchmarks.sqlite"

    sources = mcp.call_tool("external_bench_sources", {"db": str(missing)})
    assert sources["ok"] is True
    assert sources["data"]["db_exists"] is False
    assert "millstone" in {row["name"] for row in sources["data"]["sources"]}

    for tool_name, args in [
        ("external_bench_list", {}),
        ("external_bench_report", {}),
        ("external_bench_compare", {"local": "tests/fixtures/external_benchmarks/local_benchmark_sample.json"}),
    ]:
        env = mcp.call_tool(tool_name, {"db": str(missing), **args})
        assert env["ok"] is False
        assert env["error"]["code"] == "external_bench_db_not_found"

    assert not missing.exists()
    assert not missing.parent.exists()


def test_external_bench_compare_returns_structured_advisory_deltas(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(tmp_path))
    db = _external_bench_db(tmp_path)
    local = Path("tests/fixtures/external_benchmarks/local_benchmark_sample.json")

    env = mcp.call_tool("external_bench_compare", {
        "db": str(db),
        "local": str(local),
        "gpu": "RTX PRO 6000",
        "top": 3,
    })

    assert env["ok"] is True
    data = env["data"]
    assert data["advisory_only"] is True
    assert data["promotion_quality_evidence"] is False
    assert data["comparison"]["has_external_prior"] is True
    assert data["comparison"]["match_type"] == "exact"
    assert data["chosen"]["row"]["source_name"] == "millstone"
    throughput = data["chosen"]["deltas"]["throughput_tok_s"]
    assert throughput["local"] == 520.0
    assert throughput["external"] == 410.0
    assert round(throughput["delta_pct"], 1) == 26.8


def test_external_bench_compare_mcp_does_not_record_comparison_history(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(tmp_path))
    db = _external_bench_db(tmp_path)
    local = Path("tests/fixtures/external_benchmarks/local_benchmark_sample.json")
    before = (
        _sqlite_count(db, "serve_fingerprints"),
        _sqlite_count(db, "benchmark_comparisons"),
    )

    env = mcp.call_tool("external_bench_compare", {
        "db": str(db),
        "local": str(local),
    })

    assert env["ok"] is True
    assert (
        _sqlite_count(db, "serve_fingerprints"),
        _sqlite_count(db, "benchmark_comparisons"),
    ) == before


def test_external_bench_compare_rejects_local_artifact_outside_allowed_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(tmp_path))
    db = _external_bench_db(tmp_path)
    outside = tmp_path.parent / "outside-local-benchmark.json"
    outside.write_text(json.dumps({"model": "Qwen3.6-35B-A3B-MTP"}), encoding="utf-8")

    env = mcp.call_tool("external_bench_compare", {
        "db": str(db),
        "local": str(outside),
    })

    assert env["ok"] is False
    assert env["error"]["code"] == "unsafe_artifact_path"


def _workflow_packet(**overrides):
    packet = {
        "schema_version": "operator-workflow/v1",
        "request": "preflight and benchmark fast tier",
        "gate_state": "human_required",
        "targets": {
            "endpoint": "http://127.0.0.1:30001/v1",
            "model": "fast-local",
        },
        "tools_used": [{
            "name": "preflight_probe",
            "source_class": "mcp",
            "ok": True,
            "dry_run": False,
            "confirmed": True,
            "target": "http://127.0.0.1:30001/v1",
            "error": None,
        }],
        "artifacts": [],
        "advisory_priors": [],
        "recommendation": "needs_more_data",
        "human_gate_required": True,
        "promoted": False,
    }
    packet.update(overrides)
    return packet


def test_workflow_packet_docs_example_passes_validator():
    text = Path("docs/OPERATOR-SKILLS-AND-SUBAGENTS.md").read_text(encoding="utf-8")
    result_section = text.split("## Result Contract", 1)[1]
    match = re.search(r"```json\r?\n(\{.*?\})\r?\n```", result_section, re.S)
    assert match is not None
    packet = json.loads(match.group(1))

    env = mcp.call_tool("workflow_packet_validate", {"packet": packet})

    assert env["ok"] is True
    assert env["data"]["valid"] is True
    assert env["data"]["errors"] == []


def test_workflow_packet_promoted_requires_human_approved_router_promote():
    promote_tool = {
        "name": "router_promote",
        "source_class": "mcp",
        "ok": True,
        "dry_run": False,
        "confirmed": True,
        "target": "router-profile",
        "error": None,
    }
    packet = _workflow_packet(
        tools_used=[promote_tool],
        recommendation="promote",
        promoted=True,
    )

    env = mcp.call_tool("workflow_packet_validate", {"packet": packet})

    assert env["ok"] is True
    assert env["data"]["valid"] is False
    assert any(error["field"] == "promoted" for error in env["data"]["errors"])

    failed_apply = dict(promote_tool, data={"human_approved": True, "applied": False, "returncode": 1})
    env = mcp.call_tool("workflow_packet_validate", {"packet": dict(packet, tools_used=[failed_apply])})
    assert env["data"]["valid"] is False
    assert any(error["field"] == "promoted" for error in env["data"]["errors"])

    missing_returncode = dict(promote_tool, data={"human_approved": True, "applied": True})
    env = mcp.call_tool("workflow_packet_validate", {"packet": dict(packet, tools_used=[missing_returncode])})
    assert env["data"]["valid"] is False
    assert any(error["field"] == "promoted" for error in env["data"]["errors"])

    approved = dict(promote_tool, data={"human_approved": True, "applied": True, "returncode": 0})
    env = mcp.call_tool("workflow_packet_validate", {"packet": dict(packet, tools_used=[approved])})
    assert env["data"]["valid"] is True


def test_workflow_packet_promote_recommendation_keeps_human_gate_until_applied():
    packet = _workflow_packet(
        recommendation="promote",
        gate_state="not_required",
        human_gate_required=False,
    )

    env = mcp.call_tool("workflow_packet_validate", {"packet": packet})

    assert env["ok"] is True
    assert env["data"]["valid"] is False
    fields = {error["field"] for error in env["data"]["errors"]}
    assert "gate_state" in fields
    assert "human_gate_required" in fields

    approved_tool = {
        "name": "router_promote",
        "source_class": "mcp",
        "ok": True,
        "dry_run": False,
        "confirmed": True,
        "target": "router-profile",
        "error": None,
        "data": {"human_approved": True, "applied": True, "returncode": 0},
    }
    env = mcp.call_tool("workflow_packet_validate", {"packet": dict(
        packet,
        tools_used=[approved_tool],
        promoted=True,
    )})
    assert env["data"]["valid"] is True


def test_workflow_packet_artifact_paths_are_normalized_and_bounded(tmp_path):
    packet = _workflow_packet(artifacts=[
        ".anvil/benchmarks/local.json",
        {"path": ".anvil/benchmarks/object.json", "kind": "benchmark"},
    ])

    env = mcp.call_tool("workflow_packet_validate", {"packet": packet})

    assert env["ok"] is True
    assert env["data"]["valid"] is True
    artifacts = env["data"]["normalized_packet"]["artifacts"]
    assert Path(artifacts[0]).is_absolute()
    assert Path(artifacts[1]["path"]).is_absolute()
    assert artifacts[1]["kind"] == "benchmark"

    bad = _workflow_packet(artifacts=[{"path": str(tmp_path / "outside.json")}])
    env = mcp.call_tool("workflow_packet_validate", {"packet": bad})
    assert env["ok"] is True
    assert env["data"]["valid"] is False
    assert any(error["field"] == "artifacts[0].path" for error in env["data"]["errors"])


def test_workflow_packet_enums_and_required_tool_fields_are_validated():
    packet = _workflow_packet(
        gate_state="done",
        recommendation="ship_it",
        tools_used=[{"name": "probe", "source_class": "raw-shell", "ok": "yes"}],
    )

    env = mcp.call_tool("workflow_packet_validate", {"packet": packet})

    assert env["ok"] is True
    assert env["data"]["valid"] is False
    fields = {error["field"] for error in env["data"]["errors"]}
    assert "gate_state" in fields
    assert "recommendation" in fields
    assert "tools_used[0].source_class" in fields
    assert "tools_used[0].ok" in fields
    assert "tools_used[0].target" in fields


def test_workflow_packet_rejects_non_object_and_missing_required_fields():
    non_object = mcp.call_tool("workflow_packet_validate", {"packet": []})
    assert non_object["ok"] is True
    assert non_object["data"]["valid"] is False
    assert non_object["data"]["errors"][0]["field"] == "packet"

    missing = mcp.call_tool("workflow_packet_validate", {"packet": {"schema_version": "operator-workflow/v1"}})
    assert missing["ok"] is True
    assert missing["data"]["valid"] is False
    fields = {error["field"] for error in missing["data"]["errors"]}
    assert "request" in fields
    assert "tools_used" in fields
    assert "promoted" in fields


def test_workflow_packet_malformed_tool_entries_stay_structured_errors():
    packet = _workflow_packet(tools_used=["oops"])

    env = mcp.call_tool("workflow_packet_validate", {"packet": packet})

    assert env["ok"] is True
    assert env["data"]["valid"] is False
    assert env["data"]["errors"][0]["field"] == "tools_used[0]"


def test_external_bench_priors_do_not_satisfy_workflow_promotion(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(tmp_path))
    db = _external_bench_db(tmp_path)
    local = Path("tests/fixtures/external_benchmarks/local_benchmark_sample.json")
    prior = mcp.call_tool("external_bench_compare", {"db": str(db), "local": str(local)})["data"]

    packet = _workflow_packet(advisory_priors=[prior])
    env = mcp.call_tool("workflow_packet_validate", {"packet": packet})
    assert env["ok"] is True
    assert env["data"]["valid"] is True

    promote_from_prior = _workflow_packet(
        advisory_priors=[prior],
        recommendation="promote",
        gate_state="not_required",
        human_gate_required=False,
    )
    env = mcp.call_tool("workflow_packet_validate", {"packet": promote_from_prior})
    assert env["ok"] is True
    assert env["data"]["valid"] is False
    fields = {error["field"] for error in env["data"]["errors"]}
    assert {"gate_state", "human_gate_required"}.issubset(fields)

    quality_claim = _workflow_packet(advisory_priors=[dict(prior, promotion_quality_evidence=True)])
    env = mcp.call_tool("workflow_packet_validate", {"packet": quality_claim})
    assert env["data"]["valid"] is False
    assert any(error["field"] == "advisory_priors[0].promotion_quality_evidence" for error in env["data"]["errors"])

    non_advisory = _workflow_packet(advisory_priors=[dict(prior, advisory_only=False)])
    env = mcp.call_tool("workflow_packet_validate", {"packet": non_advisory})
    assert env["data"]["valid"] is False
    assert any(error["field"] == "advisory_priors[0].advisory_only" for error in env["data"]["errors"])

    missing_flags = _workflow_packet(advisory_priors=[{"source": "external"}])
    env = mcp.call_tool("workflow_packet_validate", {"packet": missing_flags})
    assert env["data"]["valid"] is False
    fields = {error["field"] for error in env["data"]["errors"]}
    assert "advisory_priors[0].advisory_only" in fields
    assert "advisory_priors[0].promotion_quality_evidence" in fields

    non_object = _workflow_packet(advisory_priors=["external"])
    env = mcp.call_tool("workflow_packet_validate", {"packet": non_object})
    assert env["data"]["valid"] is False
    assert any(error["field"] == "advisory_priors[0]" for error in env["data"]["errors"])


def test_workflow_packet_voice_artifacts_are_scoped_to_voice_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(tmp_path))
    voice_artifact = tmp_path / "voice-benchmark.json"
    voice_artifact.write_text('{"ok": true}\n', encoding="utf-8")
    artifact = {
        "kind": "voice-benchmark",
        "path": str(voice_artifact),
        "evidence_scope": "voice-pipeline",
        "promotion_quality_evidence": False,
    }

    env = mcp.call_tool("workflow_packet_validate", {"packet": _workflow_packet(artifacts=[artifact])})

    assert env["ok"] is True
    assert env["data"]["valid"] is True
    normalized = env["data"]["normalized_packet"]["artifacts"][0]
    assert normalized["kind"] == "voice-benchmark"
    assert normalized["evidence_scope"] == "voice-pipeline"
    assert normalized["promotion_quality_evidence"] is False

    missing_scope = dict(artifact)
    del missing_scope["evidence_scope"]
    env = mcp.call_tool("workflow_packet_validate", {"packet": _workflow_packet(artifacts=[missing_scope])})
    assert env["data"]["valid"] is False
    assert any(error["field"] == "artifacts[0].evidence_scope" for error in env["data"]["errors"])

    quality_claim = dict(artifact, promotion_quality_evidence=True)
    env = mcp.call_tool("workflow_packet_validate", {"packet": _workflow_packet(artifacts=[quality_claim])})
    assert env["data"]["valid"] is False
    assert any(error["field"] == "artifacts[0].promotion_quality_evidence" for error in env["data"]["errors"])

    bare_path = _workflow_packet(
        request="run voice benchmark",
        artifacts=[str(voice_artifact)],
        tools_used=[{
            "name": "voice_benchmark",
            "source_class": "cli",
            "ok": True,
            "dry_run": False,
            "confirmed": True,
            "target": "voice",
            "error": None,
        }],
    )
    env = mcp.call_tool("workflow_packet_validate", {"packet": bare_path})
    assert env["data"]["valid"] is False
    assert any(error["field"] == "artifacts[0]" for error in env["data"]["errors"])

    missing_kind = _workflow_packet(
        request="run voice benchmark",
        artifacts=[{"path": str(voice_artifact)}],
    )
    env = mcp.call_tool("workflow_packet_validate", {"packet": missing_kind})
    assert env["data"]["valid"] is False
    fields = {error["field"] for error in env["data"]["errors"]}
    assert "artifacts[0].kind" in fields
    assert "artifacts[0].evidence_scope" in fields
    assert "artifacts[0].promotion_quality_evidence" in fields


def _operator_workflow_packet_from_fixture(fixture, evidence_root):
    artifact_path = None
    tools_used = []
    for step in fixture["steps"]:
        tool = json.loads(json.dumps(step["tool"]))
        if tool["name"] == "preflight_probe":
            env = mcp.call_tool("preflight_probe", step["arguments"])
            assert env["ok"] is True
            data = env["data"]
        elif tool["name"] == "benchmark_artifact":
            raw_artifact_path = evidence_root / Path(fixture["benchmark_artifact"]["path"])
            args = dict(step["arguments"], artifact_path=str(raw_artifact_path))
            env = mcp.call_tool("benchmark_artifact", args)
            assert env["ok"] is True
            data = env["data"]
            artifact_path = Path(data["artifact_path"])
            tool["target"] = data["artifact_path"]
        else:
            data = json.loads(json.dumps(step.get("data", {})))
        tool["data"] = data
        tools_used.append(tool)

    critic = fixture["critic"]
    assert artifact_path is not None
    packet = {
        "schema_version": "operator-workflow/v1",
        "request": fixture["request"],
        "gate_state": critic["gate_state"],
        "targets": fixture["targets"],
        "tools_used": tools_used,
        "artifacts": [{
            "kind": "benchmark",
            "path": str(artifact_path),
            "source_tool": "benchmark_artifact",
        }],
        "advisory_priors": fixture["advisory_priors"],
        "recommendation": critic["recommendation"],
        "human_gate_required": critic["human_gate_required"],
        "promoted": critic["promoted"],
    }
    return packet, artifact_path


def test_operator_workflow_fixture_produces_valid_result_packet(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(tmp_path))
    fixture = json.loads(Path(
        "tests/fixtures/operator_workflows/model_swap_promotion_evidence.json",
    ).read_text(encoding="utf-8"))

    def assert_arg(argv, flag, expected):
        assert argv[argv.index(flag) + 1] == str(expected)

    def fake_run(argv, capture_output=True, text=True, timeout=None):
        assert capture_output is True
        assert text is True
        assert timeout == 30
        module = argv[argv.index("-m") + 1]
        if module == "anvil_serving.preflight":
            args = next(step["arguments"] for step in fixture["steps"] if step["tool"]["name"] == "preflight_probe")
            assert_arg(argv, "--base-url", args["base_url"])
            assert_arg(argv, "--model", args["model"])
            assert_arg(argv, "--needle-ctx", args["needle_ctx"])
            assert_arg(argv, "--tool-batch", args["tool_batch"])
            assert "--no-thinking" in argv
            return proc(0, json.dumps(fixture["preflight_result"]) + "\n", "")
        if module == "anvil_serving.benchmark":
            args = next(step["arguments"] for step in fixture["steps"] if step["tool"]["name"] == "benchmark_artifact")
            assert_arg(argv, "--base-url", args["base_url"])
            assert_arg(argv, "--model", args["model"])
            assert_arg(argv, "--requests", args["requests"])
            assert_arg(argv, "--concurrency", args["concurrency"])
            assert_arg(argv, "--ctx-tokens", args["ctx_tokens"])
            assert_arg(argv, "--max-tokens", args["max_tokens"])
            out_path = Path(argv[argv.index("--json-out") + 1])
            assert out_path.is_relative_to(tmp_path)
            out_path.write_text(
                json.dumps(fixture["benchmark_artifact"]["summary"], indent=2) + "\n",
                encoding="utf-8",
            )
            return proc(0, "wrote JSON summary\n", "")
        raise AssertionError("unexpected fixture workflow command: %r" % (argv,))

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    packet, artifact_path = _operator_workflow_packet_from_fixture(fixture, tmp_path)
    env = mcp.call_tool("workflow_packet_validate", {"packet": packet})

    assert env["ok"] is True
    assert env["data"]["valid"] is True
    normalized = env["data"]["normalized_packet"]
    assert normalized["promoted"] is False
    assert normalized["human_gate_required"] is True
    assert normalized["gate_state"] == "human_required"

    tool_names = {tool["name"] for tool in normalized["tools_used"]}
    assert {
        "doctor_summary",
        "models_inventory",
        "serves_status",
        "preflight_probe",
        "benchmark_artifact",
        "quality_critic",
    }.issubset(tool_names)

    preflight = next(tool for tool in normalized["tools_used"] if tool["name"] == "preflight_probe")
    assert preflight["ok"] is True
    assert preflight["confirmed"] is True
    assert preflight["data"]["returncode"] == 0
    preflight_output = json.loads(preflight["data"]["stdout"])
    assert preflight_output["checks"]["chat_completion"] == "pass"

    benchmark = next(tool for tool in normalized["tools_used"] if tool["name"] == "benchmark_artifact")
    assert benchmark["ok"] is True
    assert benchmark["confirmed"] is True
    assert benchmark["dry_run"] is False
    assert Path(benchmark["data"]["artifact_path"]) == artifact_path.resolve()
    assert Path(normalized["artifacts"][0]["path"]) == artifact_path.resolve()

    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["schema"] == "anvil-serving.benchmark/v1"
    assert artifact["completed"] == artifact["requests"]
    assert artifact["metrics"]["throughput_tok_s"] == 91.5

    assert packet["recommendation"] == "needs_more_data"
    assert all(prior["advisory_only"] is True for prior in packet["advisory_priors"])
    assert all(prior["promotion_quality_evidence"] is False for prior in packet["advisory_priors"])


def test_probe_tools_use_api_key_env_and_reject_raw_keys(monkeypatch):
    monkeypatch.setenv("ANVIL_ROUTER_TOKEN", "super-secret-token")
    pre = mcp.call_tool("preflight_probe", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "api_key_env": "ANVIL_ROUTER_TOKEN",
    })
    bench = mcp.call_tool("benchmark_probe", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "api_key_env": "ANVIL_ROUTER_TOKEN",
    })
    for env in (pre, bench):
        assert env["ok"] is True
        rendered = json.dumps(env)
        assert "--api-key-env" in env["data"]["command"]
        assert "ANVIL_ROUTER_TOKEN" in env["data"]["command"]
        assert "super-secret-token" not in rendered

    bad = mcp.call_tool("preflight_probe", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "api_key": "super-secret-token",
    })
    assert bad["ok"] is False
    assert bad["error"]["code"] == "raw_secret_not_allowed"

    unsafe_env = mcp.call_tool("route_decision", {
        "base_url": "http://127.0.0.1:8000/v1",
        "prompt": "hello",
        "api_key_env": "ANTHROPIC_API_KEY",
    })
    assert unsafe_env["ok"] is False
    assert unsafe_env["error"]["code"] == "unsafe_api_key_env"

    controller_token = mcp.call_tool("route_decision", {
        "base_url": "http://127.0.0.1:8000/v1",
        "prompt": "hello",
        "api_key_env": "ANVIL_CONTROLLER_TOKEN",
    })
    assert controller_token["ok"] is False
    assert controller_token["error"]["code"] == "unsafe_api_key_env"

    token_like_env = "TOKEN_123"
    token_like = mcp.call_tool("route_decision", {
        "base_url": "http://127.0.0.1:8000/v1",
        "prompt": "hello",
        "api_key_env": token_like_env,
    })
    assert token_like["ok"] is False
    assert token_like["error"]["code"] == "unsafe_api_key_env"
    assert token_like_env not in json.dumps(token_like)

    unsafe_url = mcp.call_tool("route_decision", {
        "base_url": "http://8.8.8.8/v1",
        "prompt": "hello",
        "api_key_env": "ANVIL_ROUTER_TOKEN",
    })
    assert unsafe_url["ok"] is False
    assert unsafe_url["error"]["code"] == "unsafe_base_url"

    wildcard_url = mcp.call_tool("preflight_probe", {
        "base_url": "http://0.0.0.0:30000/v1",
        "model": "local",
    })
    assert wildcard_url["ok"] is False
    assert wildcard_url["error"]["code"] == "unsafe_base_url"

    metadata_url = mcp.call_tool("route_decision", {
        "base_url": "http://169.254.169.254/v1",
        "prompt": "hello",
    })
    assert metadata_url["ok"] is False
    assert metadata_url["error"]["code"] == "unsafe_base_url"


def test_probe_cli_helpers_resolve_api_key_env(monkeypatch):
    from anvil_serving import benchmark, preflight

    monkeypatch.setenv("ANVIL_ROUTER_TOKEN", "super-secret-token")
    assert preflight.resolve_api_key(api_key_env="ANVIL_ROUTER_TOKEN") == "super-secret-token"
    assert benchmark.resolve_api_key(api_key_env="ANVIL_ROUTER_TOKEN") == "super-secret-token"
    assert preflight.resolve_api_key() is None


def test_remote_controller_request_sends_env_token_headers_and_redacts(monkeypatch):
    seen = {}

    def open_ok(req, timeout=30):
        seen["url"] = req.full_url
        seen["authorization"] = req.get_header("Authorization")
        seen["x_api_key"] = req.get_header("X-api-key")
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return Resp(b'{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}')

    token = "controller-secret-token"
    response = mcp.remote_controller_request(
        "http://127.0.0.1:8765",
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        token,
        opener=open_ok,
    )
    assert response["result"]["tools"] == []
    assert seen == {
        "url": "http://127.0.0.1:8765",
        "authorization": "Bearer " + token,
        "x_api_key": token,
        "body": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    }

    def open_fail(req, timeout=30):
        raise urllib.error.HTTPError(
            req.full_url,
            401,
            "nope",
            {},
            io.BytesIO(("bad " + token).encode("utf-8")),
        )

    try:
        mcp.remote_controller_request(
            "http://127.0.0.1:8765",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            token,
            opener=open_fail,
        )
    except mcp.ToolError as exc:
        rendered = json.dumps({"message": exc.message, "details": exc.details})
        assert exc.code == "controller_http_error"
        assert token not in rendered
        assert "<redacted>" in rendered
    else:  # pragma: no cover - must raise
        raise AssertionError("expected controller_http_error")


def test_remote_controller_request_rejects_unsafe_urls_before_auth_headers():
    called = False

    def opener(req, timeout=30):
        nonlocal called
        called = True
        return Resp(b"{}")

    try:
        mcp.remote_controller_request(
            "http://8.8.8.8:8765",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            "controller-secret-token",
            opener=opener,
        )
    except mcp.ToolError as exc:
        rendered = json.dumps({"message": exc.message, "details": exc.details})
        assert exc.code == "unsafe_base_url"
        assert "controller-secret-token" not in rendered
    else:  # pragma: no cover - must raise
        raise AssertionError("expected unsafe_base_url")
    assert called is False


def test_remote_controller_request_ignores_environment_proxies(monkeypatch):
    proxy_hits = []
    target_hits = []

    class ProxyHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_POST(self):
            proxy_hits.append(self.headers.get("Authorization"))
            self.send_response(502)
            self.end_headers()

    class TargetHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_POST(self):
            target_hits.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}')

    with _HandlerServer(ProxyHandler) as (_, proxy_port), _HandlerServer(TargetHandler) as (_, target_port):
        monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{proxy_port}")
        monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy_port}")
        monkeypatch.setenv("no_proxy", "")
        response = mcp.remote_controller_request(
            f"http://127.0.0.1:{target_port}",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            "controller-secret-token",
        )

    assert response["result"]["ok"] is True
    assert target_hits == ["Bearer controller-secret-token"]
    assert proxy_hits == []


def test_remote_controller_request_does_not_follow_redirects_with_token():
    redirected_hits = []

    class RedirectTargetHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_POST(self):
            redirected_hits.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

    class RedirectHandler(BaseHTTPRequestHandler):
        redirect_location = ""

        def log_message(self, format, *args):
            return

        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", self.redirect_location)
            self.end_headers()

    with _HandlerServer(RedirectTargetHandler) as (_, target_port):
        RedirectHandler.redirect_location = f"http://127.0.0.1:{target_port}/capture"
        with _HandlerServer(RedirectHandler) as (_, redirect_port):
            try:
                mcp.remote_controller_request(
                    f"http://127.0.0.1:{redirect_port}",
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    "controller-secret-token",
                )
            except mcp.ToolError as exc:
                rendered = json.dumps({"message": exc.message, "details": exc.details})
                assert exc.code == "controller_http_error"
                assert exc.details["status"] == 302
                assert exc.details["location"] == RedirectHandler.redirect_location
                assert "body" not in exc.details
                assert "controller-secret-token" not in rendered
            else:  # pragma: no cover - must raise
                raise AssertionError("expected controller_http_error")

    assert redirected_hits == []


def test_remote_controller_request_truncates_http_error_body():
    body = b"x" * (mcp._MAX_ERROR_BODY_BYTES + 8)

    def open_fail(req, timeout=30):
        raise urllib.error.HTTPError(
            req.full_url,
            500,
            "boom",
            {},
            io.BytesIO(body),
        )

    try:
        mcp.remote_controller_request(
            "http://127.0.0.1:8765",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            "controller-secret-token",
            opener=open_fail,
        )
    except mcp.ToolError as exc:
        assert exc.code == "controller_http_error"
        assert exc.details["status"] == 500
        assert len(exc.details["body"]) == mcp._MAX_ERROR_BODY_BYTES
        assert exc.details["body_truncated"] is True
    else:  # pragma: no cover - must raise
        raise AssertionError("expected controller_http_error")


def test_mcp_proxy_main_rejects_unsafe_controller_url(monkeypatch, capsys):
    monkeypatch.setenv("ANVIL_CONTROLLER_TOKEN", "controller-secret-token")
    rc = mcp.main([
        "--controller-url", "http://localhost:8765",
        "--auth-env", "ANVIL_CONTROLLER_TOKEN",
    ])
    assert rc == 2
    captured = capsys.readouterr()
    assert "not localhost" in captured.err
    assert "controller-secret-token" not in captured.err


def test_stdio_proxy_forwards_tool_methods_and_handles_initialize(monkeypatch):
    seen = []

    def fake_remote(controller_url, request, token, **kwargs):
        seen.append((controller_url, token, request))
        if request["method"] == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"tools": mcp.list_tools()},
            }
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "content": [],
                "structuredContent": {"ok": True, "data": {"proxied": True}},
                "isError": False,
            },
        }

    monkeypatch.setattr(mcp, "remote_controller_request", fake_remote)
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "preflight_probe",
                "arguments": {
                    "base_url": "http://127.0.0.1:30000/v1",
                    "model": "local",
                    "confirm": False,
                },
            },
        },
    ]
    stdout = io.StringIO()
    assert mcp.serve_stdio(
        [json.dumps(r) + "\n" for r in reqs],
        stdout,
        controller_url="http://127.0.0.1:8765",
        controller_token="secret",
    ) == 0
    lines = [json.loads(ln) for ln in stdout.getvalue().splitlines()]
    assert lines[0]["result"]["serverInfo"]["name"] == "anvil-serving"
    assert lines[1]["result"]["tools"] == mcp.list_tools()
    assert lines[2]["result"]["structuredContent"]["data"]["proxied"] is True
    assert [item[2]["method"] for item in seen] == ["tools/list", "tools/call"]
    assert all(item[0] == "http://127.0.0.1:8765" and item[1] == "secret" for item in seen)


def test_stdio_proxy_rejects_catalog_drift_and_unsafe_calls_before_forwarding(monkeypatch):
    seen = []

    def fake_remote(controller_url, request, token, **kwargs):
        seen.append(request)
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "tools": [
                    {
                        **mcp.list_tools()[0],
                        "description": "drifted remote contract",
                    }
                ]
            },
        }

    monkeypatch.setattr(mcp, "remote_controller_request", fake_remote)
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "router_status",
                "arguments": {"argv": ["cmd", "/c", "whoami"]},
            },
        },
    ]
    stdout = io.StringIO()

    assert mcp.serve_stdio(
        [json.dumps(request) + "\n" for request in requests],
        stdout,
        controller_url="http://127.0.0.1:8765",
        controller_token="secret",
    ) == 0
    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert responses[0]["error"]["data"]["code"] == "operation_contract_mismatch"
    assert responses[1]["error"]["data"]["code"] == "raw_command_not_allowed"
    assert seen == [requests[0]]


def test_stdio_proxy_accepts_an_allowlisted_catalog_subset(monkeypatch):
    subset = [next(tool for tool in mcp.list_tools() if tool["name"] == "host_summary")]

    def fake_remote(controller_url, request, token, **kwargs):
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"tools": subset},
        }

    monkeypatch.setattr(mcp, "remote_controller_request", fake_remote)
    stdout = io.StringIO()

    assert mcp.serve_stdio(
        [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"],
        stdout,
        controller_url="http://127.0.0.1:8765",
        controller_token="secret",
    ) == 0
    assert json.loads(stdout.getvalue())["result"]["tools"] == subset


def test_stdio_proxy_preserves_safe_target_context_as_result_metadata(monkeypatch):
    def fake_remote(controller_url, request, token, **kwargs):
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "content": [],
                "structuredContent": {"ok": True, "data": {}},
                "isError": False,
            },
        }

    monkeypatch.setattr(mcp, "remote_controller_request", fake_remote)
    request = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "operation_contracts",
            "arguments": {},
            "context": {
                "topology": "fakoli-reference",
                "execution_host": "dark",
                "execution_runtime": "dark-native",
                "transport": "controller",
            },
        },
    }
    stdout = io.StringIO()

    assert mcp.serve_stdio(
        [json.dumps(request) + "\n"],
        stdout,
        controller_url="http://127.0.0.1:8765",
        controller_token="secret",
    ) == 0
    context = json.loads(stdout.getvalue())["result"]["_meta"]["anvil/context"]
    assert context["topology"] == "fakoli-reference"
    assert context["execution_host"] == "dark"
    assert context["transport"] == "controller"


def test_stdio_proxy_does_not_forward_notifications_or_null_ids(monkeypatch):
    seen = []

    def fake_remote(controller_url, request, token, **kwargs):
        seen.append(request)
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": {}}

    monkeypatch.setattr(mcp, "remote_controller_request", fake_remote)
    reqs = [
        {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "fake"}},
        {"jsonrpc": "2.0", "id": None, "method": "tools/list"},
    ]
    stdout = io.StringIO()
    assert mcp.serve_stdio(
        [json.dumps(r) + "\n" for r in reqs],
        stdout,
        controller_url="http://127.0.0.1:8765",
        controller_token="secret",
    ) == 0
    assert seen == []
    lines = [json.loads(ln) for ln in stdout.getvalue().splitlines()]
    assert len(lines) == 1
    assert lines[0]["error"]["code"] == -32600


def test_stdio_tools_call_notification_does_not_execute(monkeypatch):
    calls = []

    def fake_call(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": True}

    monkeypatch.setattr(mcp, "call_tool", fake_call)
    stdout = io.StringIO()
    assert mcp.serve_stdio([
        json.dumps({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "fake", "arguments": {"confirm": True}},
        }) + "\n"
    ], stdout) == 0
    assert calls == []
    assert stdout.getvalue() == ""


def test_stdio_tools_call_id_null_is_protocol_error(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp, "call_tool", lambda name, arguments=None: calls.append((name, arguments)) or {"ok": True})
    stdout = io.StringIO()
    assert mcp.serve_stdio([
        json.dumps({
            "jsonrpc": "2.0",
            "id": None,
            "method": "tools/call",
            "params": {"name": "fake", "arguments": {"confirm": True}},
        }) + "\n"
    ], stdout) == 0
    response = json.loads(stdout.getvalue())
    assert calls == []
    assert response["error"]["code"] == -32600


def test_stdio_tools_call_falsey_non_object_arguments_are_rejected():
    stdout = io.StringIO()
    assert mcp.serve_stdio([
        json.dumps({
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "router_status", "arguments": False},
        }) + "\n"
    ], stdout) == 0
    response = json.loads(stdout.getvalue())
    assert response["error"]["code"] == -32602
    assert response["error"]["data"]["code"] == "bad_arguments"


def test_stdio_tools_call_falsey_non_object_params_are_rejected():
    for value in (False, 0, "", []):
        stdout = io.StringIO()
        assert mcp.serve_stdio([
            json.dumps({
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": value,
            }) + "\n"
        ], stdout) == 0
        response = json.loads(stdout.getvalue())
        assert response["error"]["code"] == -32602
        assert response["error"]["data"]["code"] == "bad_params"


def test_route_decision_503_is_structured_probe_result(monkeypatch):
    def open_503(req, timeout=5):
        raise urllib.error.HTTPError(
            req.full_url,
            503,
            "unavailable",
            {},
            io.BytesIO(b'{"error":{"code":"no_available_tier"}}'),
        )

    monkeypatch.setattr(mcp, "_urlopen_no_proxy_no_redirect", open_503)
    env = mcp.call_tool("route_decision", {
        "base_url": "http://127.0.0.1:8000/v1",
        "prompt": "hello",
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "no_available_tier"
    assert env["error"]["details"]["status"] == 503


def test_confirmed_probe_nonzero_returncode_is_tool_error(monkeypatch):
    monkeypatch.setattr(mcp.subprocess, "run", lambda *a, **k: proc(1, "bad", "worse"))
    env = mcp.call_tool("preflight_probe", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "confirm": True,
        "timeout_seconds": 1,
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "command_failed"
    assert env["error"]["details"]["returncode"] == 1


def test_mcp_proxy_main_requires_env_token(monkeypatch, capsys):
    monkeypatch.delenv("ANVIL_CONTROLLER_TOKEN", raising=False)
    rc = mcp.main([
        "--controller-url", "http://127.0.0.1:8765",
        "--auth-env", "ANVIL_CONTROLLER_TOKEN",
    ])
    assert rc == 2
    assert "auth env var is unset" in capsys.readouterr().err


def test_mcp_help_documents_modes(capsys):
    try:
        mcp.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:  # pragma: no cover - argparse should exit for help
        raise AssertionError("mcp --help did not exit")
    out = capsys.readouterr().out
    assert "anvil-serving mcp serve" in out
    assert "--list-tools" in out
    assert "--controller-url" in out
    assert "--auth-env" in out


def test_mcp_parse_errors_return_usage_code(capsys):
    rc = mcp.main(["--controller-url"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "usage: anvil-serving mcp serve" in err


def test_mcp_list_tools_positional_alias_still_works(capsys):
    rc = mcp.main(["list-tools"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "tools" in payload
    assert any(tool["name"] == "router_status" for tool in payload["tools"])


def test_cli_dispatches_mcp(monkeypatch):
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(mcp, "main", fake_main)
    assert cli.main(["mcp", "tools"]) == 0
    assert seen["argv"] == ["list-tools"]
