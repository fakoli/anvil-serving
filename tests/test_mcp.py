"""Tests for the `anvil-serving mcp` control plane.

No docker, OpenClaw gateway, router, or model serve is required: command and
HTTP seams are faked at the module boundary.
"""
import io
import json
import sys
import threading
import textwrap
import types
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from anvil_serving import cli, mcp


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
    """
    if up:
        body += '        up = "docker compose -f {dir}/docker-compose.yml up -d"\n'
    p.write_text(textwrap.dedent(body), encoding="utf-8")
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
    """), encoding="utf-8")
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
        "serves_status",
        "serves_manage",
        "serves_logs",
        "doctor_summary",
        "models_inventory",
        "route_decision",
        "openclaw_sync",
        "openclaw_gateway_restart",
        "preflight_probe",
        "benchmark_probe",
    ]:
        assert name in tools
        schema = tools[name]["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert "properties" in schema
    for name in ("preflight_probe", "benchmark_probe"):
        props = tools[name]["inputSchema"]["properties"]
        assert "api_key" not in props
        assert props["api_key_env"]["type"] == "string"
    assert tools["benchmark_probe"]["inputSchema"]["properties"]["requests"]["maximum"] == 200
    assert tools["openclaw_gateway_restart"]["inputSchema"]["properties"]["timeout_seconds"]["default"] == 120


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
    assert preview["model_ids"] == ["chat"]
    # Secret hygiene: config references the env var by name; no literal secret is resolved.
    assert preview["api_key"] == "${ANVIL_ROUTER_TOKEN}"


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


def test_openclaw_sync_rejects_non_anvil_api_key_env(tmp_path):
    cfg = _router_cfg(tmp_path)
    env = mcp.call_tool("openclaw_sync", {
        "config": cfg,
        "api_key_env": "ANTHROPIC_API_KEY",
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "unsafe_api_key_env"


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


def test_gateway_restart_is_gated_and_uses_argv_preview():
    env = mcp.call_tool("openclaw_gateway_restart", {
        "gateway_host": "mini",
        "gateway_user": "sd",
    })
    assert env["ok"] is True
    assert env["data"]["restarted"] is False
    assert env["data"]["command"] == [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=60",
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
            return {"jsonrpc": "2.0", "id": request["id"], "result": {"tools": []}}
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
            "params": {"name": "preflight_probe", "arguments": {"confirm": False}},
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
    assert lines[1]["result"]["tools"] == []
    assert lines[2]["result"]["structuredContent"]["data"]["proxied"] is True
    assert [item[2]["method"] for item in seen] == ["tools/list", "tools/call"]
    assert all(item[0] == "http://127.0.0.1:8765" and item[1] == "secret" for item in seen)


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


def test_cli_dispatches_mcp(monkeypatch):
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(mcp, "main", fake_main)
    assert cli.main(["mcp", "--list-tools"]) == 0
    assert seen["argv"] == ["--list-tools"]
