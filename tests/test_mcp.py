"""Tests for the `anvil-serving mcp` control plane.

No docker, OpenClaw gateway, router, or model serve is required: command and
HTTP seams are faked at the module boundary.
"""
import io
import json
import textwrap
import types

from anvil_serving import cli, mcp


def proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


def _manifest(tmp_path):
    p = tmp_path / "serves.toml"
    p.write_text(textwrap.dedent("""
        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30001
        health = "/health"
        model = "fast-model"
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
    """), encoding="utf-8")
    return str(p)


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


def test_tools_list_has_json_schemas():
    tools = {t["name"]: t for t in mcp.list_tools()}
    for name in [
        "router_status",
        "serves_status",
        "doctor_summary",
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

    monkeypatch.setattr(mcp.urllib.request, "urlopen", open_route)
    env = mcp.call_tool("route_decision", {
        "base_url": "http://127.0.0.1:8000/v1",
        "prompt": "fix this",
    })
    assert env["ok"] is True
    assert seen["url"] == "http://127.0.0.1:8000/v1/route"
    assert seen["body"]["messages"][0]["content"] == "fix this"
    assert env["data"]["response"]["tier"] == "local"


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
    assert env["data"]["command"] == ["ssh", "sd@mini", '$SHELL -lc "openclaw gateway restart"']


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


def test_probe_cli_helpers_resolve_api_key_env(monkeypatch):
    from anvil_serving import benchmark, preflight

    monkeypatch.setenv("ANVIL_ROUTER_TOKEN", "super-secret-token")
    assert preflight.resolve_api_key(api_key_env="ANVIL_ROUTER_TOKEN") == "super-secret-token"
    assert benchmark.resolve_api_key(api_key_env="ANVIL_ROUTER_TOKEN") == "super-secret-token"
    assert preflight.resolve_api_key() is None


def test_cli_dispatches_mcp(monkeypatch):
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(mcp, "main", fake_main)
    assert cli.main(["mcp", "--list-tools"]) == 0
    assert seen["argv"] == ["--list-tools"]
