import json
import textwrap

from anvil_serving import harness, mcp


def _router_config(tmp_path):
    path = tmp_path / "router.toml"
    path.write_text(
        textwrap.dedent(
            """
            [router]
            mapping_version = "openclaw-setup-test"

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
            """
        ),
        encoding="utf-8",
    )
    return str(path)


def test_openclaw_sync_schema_exposes_complete_gateway_setup_contract():
    schema = {
        tool["name"]: tool["inputSchema"]
        for tool in mcp.list_tools()
    }["openclaw_sync"]
    properties = schema["properties"]

    assert properties["native_provider"]["type"] == "string"
    assert properties["native_model"]["type"] == "string"
    assert properties["plugin_dir"]["type"] == "string"
    assert properties["tool_profile"]["enum"] == [
        "coding",
        "full",
        "messaging",
        "minimal",
    ]
    assert properties["exec_mode"]["enum"] == [
        "allowlist",
        "ask",
        "auto",
        "deny",
        "full",
    ]
    assert properties["client_side_routing"]["type"] == "boolean"
    assert properties["route_timeout_ms"]["maximum"] == 5000


def test_controller_operation_forwards_complete_gateway_setup_contract():
    declaration = next(
        item
        for item in mcp.operation_declarations()
        if item["path"] == "harness sync openclaw"
    )

    assert {
        "native_provider",
        "native_model",
        "plugin_dir",
        "tool_profile",
        "exec_mode",
        "client_side_routing",
        "route_endpoint",
        "route_auth_env",
        "route_timeout_ms",
    }.issubset(declaration["allowed_arguments"])


def test_openclaw_sync_preview_distinguishes_config_from_runtime_readiness(tmp_path):
    plugin_dir = tmp_path / "openclaw-anvil-intent-router"
    plugin_dir.mkdir()
    (plugin_dir / "openclaw.plugin.json").write_text(
        '{"id":"openclaw-anvil-intent-router"}',
        encoding="utf-8",
    )
    env = mcp.call_tool(
        "openclaw_sync",
        {
            "config": _router_config(tmp_path),
            "base_url": "http://100.87.34.66:8000/v1",
            "native_provider": "openai",
            "native_model": "gpt-5.6-sol",
            "plugin_dir": str(plugin_dir),
            "tool_profile": "full",
            "exec_mode": "auto",
        },
    )

    assert env["ok"] is True, env
    preview = env["data"]["preview"]
    assert preview["fresh_config_ready"] is True
    assert preview["fresh_config_issues"] == []
    assert preview["fresh_setup_ready"] is False
    assert preview["fresh_setup_issues"] == [
        "native model not verified by OpenClaw",
        "plugin runtime not verified by OpenClaw",
    ]
    assert preview["native_primary"] == "openai/gpt-5.6-sol"
    assert preview["native_provider"] == "openai"
    assert preview["native_model"] == "gpt-5.6-sol"
    assert preview["plugin_enabled"] is True
    assert preview["plugin_load_paths"] == [
        str(plugin_dir)
    ]
    assert preview["plugin_manifest_verified"] is True
    assert preview["route_endpoint"] == "http://100.87.34.66:8000/v1/route"
    assert preview["route_auth_env"] == "ANVIL_ROUTER_TOKEN"
    assert preview["route_timeout_ms"] == 500
    assert preview["tool_profile"] == "full"
    assert preview["exec_mode"] == "auto"


def test_fresh_gateway_requires_explicit_tool_and_exec_policy(tmp_path, capsys):
    config_path = _router_config(tmp_path)
    setup = {
        "base_url": "http://100.87.34.66:8000/v1",
        "native_provider": "openai",
        "native_model": "gpt-5.6-sol",
        "plugin_dir": "/opt/anvil/openclaw-anvil-intent-router",
    }

    preview = harness.openclaw_sync_preview(config_path, **setup)
    assert preview["fresh_setup_ready"] is False
    assert preview["fresh_setup_issues"] == [
        "explicit tool profile",
        "explicit exec mode",
    ]

    out = tmp_path / "openclaw.json"
    rc = harness.cmd_sync_openclaw(
        config_path,
        out=str(out),
        api_key_env="ANVIL_ROUTER_TOKEN",
        **setup,
    )
    assert rc == 1
    assert not out.exists()
    error = capsys.readouterr().err
    assert "--tool-profile" in error
    assert "--exec-mode" in error


def test_fresh_gateway_preview_does_not_claim_uninstalled_plugin_ready(tmp_path):
    preview = harness.openclaw_sync_preview(
        _router_config(tmp_path),
        base_url="http://127.0.0.1:8000/v1",
        native_provider="openai",
        native_model="gpt-5.6-sol",
        plugin_dir=str(tmp_path / "missing" / "openclaw-anvil-intent-router"),
        tool_profile="full",
        exec_mode="auto",
    )

    assert preview["fresh_config_ready"] is True
    assert preview["plugin_manifest_verified"] is False
    assert preview["fresh_setup_ready"] is False
    assert preview["fresh_setup_issues"] == [
        "plugin manifest not verified on gateway",
        "native model not verified by OpenClaw",
        "plugin runtime not verified by OpenClaw",
    ]


def test_openclaw_sync_apply_forwards_complete_gateway_contract(tmp_path, monkeypatch):
    seen = {}

    def fake_sync(config_path, **kwargs):
        seen["config_path"] = config_path
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(harness, "cmd_sync_openclaw", fake_sync)
    config_path = _router_config(tmp_path)
    env = mcp.call_tool(
        "openclaw_sync",
        {
            "config": config_path,
            "out": str(tmp_path / "openclaw.json"),
            "native_provider": "openai",
            "native_model": "gpt-5.6-sol",
            "plugin_dir": "/opt/anvil/openclaw-anvil-intent-router",
            "tool_profile": "full",
            "exec_mode": "auto",
            "route_endpoint": "http://100.87.34.66:8000/v1/route",
            "route_auth_env": "ANVIL_ROUTE_TOKEN",
            "route_timeout_ms": 750,
            "confirm": True,
            "dry_run": False,
        },
    )

    assert env["ok"] is True
    assert seen["config_path"] == config_path
    assert seen["kwargs"]["native_provider"] == "openai"
    assert seen["kwargs"]["native_model"] == "gpt-5.6-sol"
    assert seen["kwargs"]["plugin_dir"] == "/opt/anvil/openclaw-anvil-intent-router"
    assert seen["kwargs"]["tool_profile"] == "full"
    assert seen["kwargs"]["exec_mode"] == "auto"
    assert seen["kwargs"]["authoritative_route"] is True
    assert seen["kwargs"]["route_endpoint"] == "http://100.87.34.66:8000/v1/route"
    assert seen["kwargs"]["route_auth_env"] == "ANVIL_ROUTE_TOKEN"
    assert seen["kwargs"]["route_timeout_ms"] == 750
    assert seen["kwargs"]["_replace_provider_keys"] == ()
    assert isinstance(seen["kwargs"]["_applied_payload"], dict)
    assert isinstance(seen["kwargs"]["_applied_validation"], dict)


def test_openclaw_sync_apply_reports_actual_explicit_merge_values(tmp_path):
    plugin_dir = tmp_path / "openclaw-anvil-intent-router"
    plugin_dir.mkdir()
    (plugin_dir / "openclaw.plugin.json").write_text(
        '{"id":"openclaw-anvil-intent-router"}',
        encoding="utf-8",
    )
    out = tmp_path / "openclaw.json"
    out.write_text(json.dumps({
        "models": {"providers": {"anvil": {
            "baseUrl": "http://old/v1",
            "apiKey": "OLD",
        }}},
        "agents": {"defaults": {"model": {"primary": "openai/old"}}},
        "plugins": {
            "load": {"paths": ["/old/openclaw-anvil-intent-router"]},
            "entries": {"openclaw-anvil-intent-router": {
                "enabled": True,
                "config": {
                    "routeEndpoint": "http://old/v1/route",
                    "routeAuthEnv": "OLD_TOKEN",
                    "routeTimeoutMs": 30,
                },
            }},
        },
        "tools": {"profile": "coding", "exec": {"mode": "ask"}},
    }), encoding="utf-8")

    env = mcp.call_tool("openclaw_sync", {
        "config": _router_config(tmp_path),
        "out": str(out),
        "base_url": "http://127.0.0.1:8001/v1",
        "api_key_env": "ANVIL_ROUTER_TOKEN",
        "native_provider": "openai",
        "native_model": "gpt-5.6-sol",
        "plugin_dir": str(plugin_dir),
        "tool_profile": "full",
        "exec_mode": "auto",
        "route_endpoint": "http://127.0.0.1:8002/v1/route",
        "route_auth_env": "NEW_ROUTE_TOKEN",
        "route_timeout_ms": 750,
        "confirm": True,
        "dry_run": False,
    })

    assert env["ok"] is True, env
    preview = env["data"]["preview"]
    assert preview["base_url"] == "http://127.0.0.1:8001/v1"
    assert preview["api_key"] == "${ANVIL_ROUTER_TOKEN}"
    assert preview["route_endpoint"] == "http://127.0.0.1:8002/v1/route"
    assert preview["route_auth_env"] == "NEW_ROUTE_TOKEN"
    assert preview["route_timeout_ms"] == 750
    assert preview["plugin_manifest_verified"] is True
    assert preview["fresh_config_ready"] is True
    assert preview["fresh_setup_ready"] is False
    assert preview["native_model_verified"] is False
    assert preview["plugin_runtime_verified"] is False

    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["models"]["providers"]["anvil"]["baseUrl"] == "http://127.0.0.1:8001/v1"
    assert written["plugins"]["entries"][
        "openclaw-anvil-intent-router"
    ]["config"]["routeEndpoint"] == "http://127.0.0.1:8002/v1/route"
