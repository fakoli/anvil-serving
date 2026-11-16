
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
        "temperature": 1.0,
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
    assert argv[argv.index("--temperature") + 1] == "1.0"
    assert argv[argv.index("--timeout-seconds") + 1] == "30"
    assert argv[argv.index("--allowed-finish-reasons") + 1] == "stop,length"
    assert argv[argv.index("--reasoning-effort") + 1] == "max"
    assert kwargs["timeout"] == 60


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
