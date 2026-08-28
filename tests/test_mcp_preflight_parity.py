from anvil_serving import mcp
from anvil_serving.control_plane.mcp.tools import benchmarks as benchmark_tools


def test_preflight_probe_explicit_dry_run_executes_the_safe_child_plan(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return {
            "command": argv,
            "returncode": 0,
            "stdout": '{"workload":"preflight"}\n',
            "stderr": "",
        }

    monkeypatch.setattr(benchmark_tools, "_run_argv", run)
    result = mcp.call_tool("preflight_probe", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "checks": "smoke,json",
        "reasoning_effort": "max",
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
    assert argv[argv.index("--reasoning-effort") + 1] == "max"
    assert kwargs["timeout"] == 60


def test_preflight_probe_preview_validates_model_family_controls_without_running(monkeypatch):
    monkeypatch.setattr(
        benchmark_tools,
        "_run_argv",
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
        "none", "minimal", "low", "medium", "high", "max", "xhigh"
    ]
    assert "allowed_finish_reasons" in schema
    assert schema["image_expect"]["maxItems"] == 32
    assert schema["ocr_expect"]["items"]["maxLength"] == 256
    assert schema["video_expect"]["maxItems"] == 32
    assert "video_path" in schema
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


def test_preflight_probe_multimodal_arguments_reach_child(monkeypatch, tmp_path):
    calls = []
    image = tmp_path / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        benchmark_tools,
        "_run_argv",
        lambda argv, **kwargs: (
            calls.append((argv, kwargs))
            or {"command": argv, "returncode": 0, "stdout": "ok\n", "stderr": ""}
        ),
    )

    result = mcp.call_tool("preflight_probe", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "checks": "image,ocr",
        "image_path": str(image),
        "image_expect": ["RTX 5090"],
        "ocr_expect": ["Error 503"],
        "dry_run": True,
    })

    assert result["ok"] is True
    argv = calls[0][0]
    assert argv[argv.index("--image-path") + 1] == str(image)
    assert argv[argv.index("--image-expect") + 1] == "RTX 5090"
    assert argv[argv.index("--ocr-expect") + 1] == "Error 503"


def test_preflight_probe_video_arguments_reach_child(monkeypatch, tmp_path):
    calls = []
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        benchmark_tools,
        "_run_argv",
        lambda argv, **kwargs: (
            calls.append((argv, kwargs))
            or {"command": argv, "returncode": 0, "stdout": "ok\n", "stderr": ""}
        ),
    )

    result = mcp.call_tool("preflight_probe", {
        "base_url": "http://127.0.0.1:30000/v1",
        "model": "local",
        "checks": "video",
        "video_path": str(video),
        "video_expect": ["red", "green"],
        "dry_run": True,
    })

    assert result["ok"] is True
    argv = calls[0][0]
    assert argv[argv.index("--video-path") + 1] == str(video)
    assert argv[argv.index("--video-expect") + 1] == "red"
