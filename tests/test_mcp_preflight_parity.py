from types import SimpleNamespace

from anvil_serving import mcp


def test_preflight_probe_explicit_dry_run_executes_the_safe_child_plan(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"workload":"preflight"}\n', stderr="")

    monkeypatch.setattr(mcp.subprocess, "run", run)
    result = mcp.call_tool("preflight_probe", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "checks": "smoke,json",
        "allowed_finish_reasons": "stop,length",
        "timeout_seconds": 30,
        "dry_run": True,
    })

    assert result["ok"] is True
    assert result["data"]["applied"] is False
    assert result["data"]["dry_run"] is True
    argv, kwargs = calls[0]
    assert "--dry-run" in argv
    assert argv[argv.index("--timeout-seconds") + 1] == "30"
    assert argv[argv.index("--allowed-finish-reasons") + 1] == "stop,length"
    assert kwargs["timeout"] == 60


def test_preflight_probe_preview_validates_model_family_controls_without_running(monkeypatch):
    monkeypatch.setattr(
        mcp.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("ran child")),
    )

    gpt_oss = mcp.call_tool("preflight_probe", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "gpt-oss-120b",
        "thinking_mode": "disabled",
    })
    qwen = mcp.call_tool("preflight_probe", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "Qwen3.6-27B",
        "reasoning_effort": "high",
    })

    assert gpt_oss["ok"] is False
    assert gpt_oss["error"]["code"] == "bad_argument"
    assert qwen["ok"] is False
    assert qwen["error"]["code"] == "bad_argument"


def test_preflight_probe_schema_matches_local_bounds_and_controls():
    schema = mcp.TOOLS["preflight_probe"]["inputSchema"]["properties"]

    assert schema["needle_ctx"]["maximum"] == 1000000
    assert schema["tool_batch"]["maximum"] == 128
    assert schema["timeout_seconds"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 3600,
        "default": 900,
    }
    assert schema["reasoning_effort"]["enum"] == [
        "none", "minimal", "low", "medium", "high"
    ]
    assert "allowed_finish_reasons" in schema
    assert "dry_run" in schema


def test_preflight_probe_rejects_invalid_ports_and_unknown_checks():
    invalid_port = mcp.call_tool("preflight_probe", {
        "base_url": "http://127.0.0.1:99999/v1",
        "model": "local",
    })
    unknown_check = mcp.call_tool("preflight_probe", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "checks": "smoke,magic",
    })

    assert invalid_port["ok"] is False
    assert invalid_port["error"]["code"] == "bad_base_url"
    assert unknown_check["ok"] is False
    assert unknown_check["error"]["code"] == "bad_argument"
