"""Explicit benchmarks MCP tool family."""

from __future__ import annotations

import os
import sys

from ....benchmarking.artifacts import (
    benchmark_key_metrics as _benchmark_key_metrics,
)
from ..arguments import (
    arg_bool as _arg_bool,
    bounded_int_arg as _bounded_int_arg,
    bounded_integer_schema as _bounded_integer_schema,
    probe_api_key_env as _probe_api_key_env,
    schema as _schema,
    str_arg as _str_arg,
    str_list_arg as _str_list_arg,
)
from ..catalog import ToolFamily
from ..errors import ToolError
from ..errors import ok as _ok
from ..evidence import (
    read_benchmark_artifact as _read_benchmark_artifact,
    resolve_benchmark_artifact_path as _resolve_benchmark_artifact_path,
)
from ..runtime import (
    run_argv as _run_argv,
)
from ..security import (
    safe_probe_url as _safe_probe_url,
)


def tool_preflight_probe(args: dict) -> dict:
    from ....model_controls import validate_reasoning_control

    base_url = _safe_probe_url(_str_arg(args, "base_url", required=True))
    model = _str_arg(args, "model", required=True)
    api_key_env = _probe_api_key_env(args)
    needle_ctx = _bounded_int_arg(args, "needle_ctx", 128000, min_value=1, max_value=1000000)
    tool_batch = _bounded_int_arg(args, "tool_batch", 20, min_value=1, max_value=128)
    no_thinking = _arg_bool(args.get("no_thinking"), False, name="no_thinking")
    checks = _str_arg(args, "checks") or "smoke,json,needle,tools"
    selected_checks = [item.strip() for item in checks.split(",") if item.strip()]
    known_checks = {"smoke", "json", "needle", "tools", "image", "ocr"}
    unknown_checks = sorted(set(selected_checks) - known_checks)
    if not selected_checks or unknown_checks:
        raise ToolError(
            "bad_argument",
            "checks must select smoke,json,needle,tools,image,ocr",
            {"unknown": unknown_checks},
        )
    image_path = _str_arg(args, "image_path")
    image_expect = [item.strip() for item in _str_list_arg(args, "image_expect")]
    ocr_expect = [item.strip() for item in _str_list_arg(args, "ocr_expect")]
    if any(not item for item in image_expect + ocr_expect):
        raise ToolError("bad_argument", "multimodal expectations must be non-empty strings")
    if {"image", "ocr"} & set(selected_checks):
        if not image_path:
            raise ToolError("bad_argument", "multimodal checks require image_path")
        resolved_image = os.path.abspath(os.path.expanduser(image_path))
        allowed_roots = (
            os.path.abspath(os.getcwd()),
            os.path.abspath(os.path.expanduser("~/.anvil-serving")),
        )
        if not any(
            os.path.commonpath((resolved_image, root)) == root
            for root in allowed_roots
        ):
            raise ToolError(
                "unsafe_image_path",
                "image_path must be under the controller working directory or ~/.anvil-serving",
            )
        if os.path.islink(resolved_image) or not os.path.isfile(resolved_image):
            raise ToolError("bad_argument", "image_path must be a regular non-symlink file")
        image_path = resolved_image
        if "image" in selected_checks and not image_expect:
            raise ToolError("bad_argument", "image check requires image_expect")
        if "ocr" in selected_checks and not ocr_expect:
            raise ToolError("bad_argument", "ocr check requires ocr_expect")
    thinking_mode = _str_arg(args, "thinking_mode") or "default"
    if thinking_mode not in {"default", "enabled", "disabled", "unsupported"}:
        raise ToolError("bad_argument", "thinking_mode has an unsupported value")
    reasoning_effort = _str_arg(args, "reasoning_effort")
    if no_thinking:
        if thinking_mode not in {"default", "disabled"} or reasoning_effort:
            raise ToolError("bad_argument", "no_thinking conflicts with explicit thinking controls")
        thinking_mode = "disabled"
    if reasoning_effort and thinking_mode != "default":
        raise ToolError("bad_argument", "reasoning_effort cannot be combined with thinking_mode")
    try:
        validate_reasoning_control(
            model,
            thinking_mode=thinking_mode,
            no_thinking=no_thinking,
            reasoning_effort=reasoning_effort or None,
        )
    except ValueError as exc:
        raise ToolError("bad_argument", str(exc)) from None
    reasoning_evidence = _str_arg(args, "reasoning_evidence") or "any"
    if reasoning_evidence not in {"any", "required", "forbidden"}:
        raise ToolError("bad_argument", "reasoning_evidence has an unsupported value")
    visible_tokens = _bounded_int_arg(
        args, "visible_answer_tokens", 256, min_value=1, max_value=65536
    )
    reasoning_tokens = _bounded_int_arg(
        args, "reasoning_headroom_tokens", 0, min_value=0, max_value=65536
    )
    if visible_tokens + reasoning_tokens > 65536:
        raise ToolError("bad_argument", "combined completion allocation exceeds 65536")
    allowed_finish_reasons = _str_arg(args, "allowed_finish_reasons") or "stop,tool_calls"
    if not any(item.strip() for item in allowed_finish_reasons.split(",")):
        raise ToolError("bad_argument", "allowed_finish_reasons cannot be empty")
    dry_run = _arg_bool(args.get("dry_run"), False, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 900, min_value=1, max_value=3600)
    operation_timeout = timeout_seconds * len(selected_checks) + 5
    if operation_timeout > 7200:
        raise ToolError(
            "bad_argument",
            "preflight workload deadline exceeds 7200 seconds; reduce checks or timeout_seconds",
        )
    argv = [
        sys.executable,
        "-m",
        "anvil_serving.preflight",
        "--base-url",
        base_url,
        "--model",
        model,
        "--needle-ctx",
        str(needle_ctx),
        "--tool-batch",
        str(tool_batch),
        "--checks",
        checks,
        "--thinking-mode",
        thinking_mode,
        "--visible-answer-tokens",
        str(visible_tokens),
        "--reasoning-headroom-tokens",
        str(reasoning_tokens),
        "--reasoning-evidence",
        reasoning_evidence,
        "--allowed-finish-reasons",
        allowed_finish_reasons,
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    if api_key_env:
        argv += ["--api-key-env", api_key_env]
    if no_thinking:
        argv.append("--no-thinking")
    if reasoning_effort:
        argv += ["--reasoning-effort", reasoning_effort]
    if image_path:
        argv += ["--image-path", image_path]
    for expectation in image_expect:
        argv += ["--image-expect", expectation]
    for expectation in ocr_expect:
        argv += ["--ocr-expect", expectation]
    if dry_run:
        argv.append("--dry-run")
        result = _run_argv(argv, confirm=True, timeout=min(operation_timeout, 60))
        return _ok({"applied": False, "dry_run": True, **result})
    result = _run_argv(argv, confirm=confirm, timeout=operation_timeout)
    return _ok({"applied": bool(confirm), "dry_run": not confirm, **result})


def tool_benchmark_probe(args: dict) -> dict:
    base_url = _safe_probe_url(_str_arg(args, "base_url", required=True))
    model = _str_arg(args, "model", required=True)
    api_key_env = _probe_api_key_env(args)
    requests = _bounded_int_arg(args, "requests", 60, min_value=1, max_value=200)
    concurrency = _bounded_int_arg(args, "concurrency", 20, min_value=1, max_value=100)
    max_tokens = _bounded_int_arg(args, "max_tokens", 64, min_value=1, max_value=4096)
    ctx_tokens = _bounded_int_arg(args, "ctx_tokens", 0, min_value=0, max_value=262144)
    no_thinking = _arg_bool(args.get("no_thinking"), False, name="no_thinking")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 900, min_value=1, max_value=1800)
    waves = (requests + concurrency - 1) // concurrency
    operation_timeout = timeout_seconds * waves + 30
    if operation_timeout > 7200:
        raise ToolError(
            "bad_argument",
            "benchmark workload deadline exceeds 7200 seconds; reduce requests or timeout_seconds",
        )
    argv = [
        sys.executable,
        "-m",
        "anvil_serving.benchmark",
        "capacity",
        "--base-url",
        base_url,
        "--model",
        model,
        "--requests",
        str(requests),
        "--concurrency",
        str(concurrency),
        "--max-tokens",
        str(max_tokens),
        "--ctx-tokens",
        str(ctx_tokens),
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    if api_key_env:
        argv += ["--api-key-env", api_key_env]
    if no_thinking:
        argv.append("--no-thinking")
    return _ok(_run_argv(argv, confirm=confirm, timeout=operation_timeout))


def tool_benchmark_artifact(args: dict) -> dict:
    base_url = _safe_probe_url(_str_arg(args, "base_url", required=True))
    model = _str_arg(args, "model", required=True)
    api_key_env = _probe_api_key_env(args)
    artifact_path, allowed_roots = _resolve_benchmark_artifact_path(
        _str_arg(args, "artifact_path", required=True)
    )
    requests = _bounded_int_arg(args, "requests", 60, min_value=1, max_value=200)
    concurrency = _bounded_int_arg(args, "concurrency", 20, min_value=1, max_value=100)
    burst = _bounded_int_arg(args, "burst", 0, min_value=0, max_value=200)
    max_tokens = _bounded_int_arg(args, "max_tokens", 64, min_value=1, max_value=4096)
    ctx_tokens = _bounded_int_arg(args, "ctx_tokens", 0, min_value=0, max_value=262144)
    max_model_len = _bounded_int_arg(args, "max_model_len", 0, min_value=0, max_value=1048576)
    no_thinking = _arg_bool(args.get("no_thinking"), False, name="no_thinking")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 900, min_value=1, max_value=1800)
    effective_requests = burst or requests
    effective_concurrency = burst or concurrency
    waves = (effective_requests + effective_concurrency - 1) // effective_concurrency
    operation_timeout = timeout_seconds * waves + 30
    if operation_timeout > 7200:
        raise ToolError(
            "bad_argument",
            "benchmark workload deadline exceeds 7200 seconds; reduce requests or timeout_seconds",
        )
    argv = [
        sys.executable,
        "-m",
        "anvil_serving.benchmark",
        "capacity",
        "--base-url",
        base_url,
        "--model",
        model,
        "--requests",
        str(requests),
        "--concurrency",
        str(concurrency),
        "--max-tokens",
        str(max_tokens),
        "--ctx-tokens",
        str(ctx_tokens),
        "--timeout-seconds",
        str(timeout_seconds),
        "--json-out",
        artifact_path,
    ]
    if burst:
        argv += ["--burst", str(burst)]
    if max_model_len:
        argv += ["--max-model-len", str(max_model_len)]
    if api_key_env:
        argv += ["--api-key-env", api_key_env]
    if no_thinking:
        argv.append("--no-thinking")

    if not confirm:
        return _ok(
            {
                "applied": False,
                "dry_run": True,
                "artifact_path": artifact_path,
                "allowed_roots": allowed_roots,
                "command": argv,
            }
        )

    parent = os.path.dirname(artifact_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    before = None
    if os.path.isfile(artifact_path):
        stat = os.stat(artifact_path)
        before = (stat.st_mtime_ns, stat.st_size)
    result = _run_argv(argv, confirm=True, timeout=operation_timeout)
    if before is not None and os.path.isfile(artifact_path):
        stat = os.stat(artifact_path)
        if (stat.st_mtime_ns, stat.st_size) == before:
            raise ToolError(
                "artifact_not_written",
                "benchmark completed but did not replace the existing JSON artifact",
                {"artifact_path": artifact_path},
            )
    summary = _read_benchmark_artifact(artifact_path)
    return _ok(
        {
            "applied": True,
            "dry_run": False,
            "artifact_path": artifact_path,
            "allowed_roots": allowed_roots,
            "key_metrics": _benchmark_key_metrics(summary),
            "summary": summary,
            **result,
        }
    )


FAMILY = ToolFamily(
    name="benchmarks",
    tools={
        "preflight_probe": {
            "description": "Preview or run an anvil-serving eval preflight command for a model endpoint.",
            "inputSchema": _schema(
                {
                    "base_url": {"type": "string"},
                    "model": {"type": "string"},
                    "api_key_env": {"type": "string"},
                    "needle_ctx": _bounded_integer_schema(1, 1000000, 128000),
                    "tool_batch": _bounded_integer_schema(1, 128, 20),
                    "no_thinking": {"type": "boolean"},
                    "checks": {"type": "string"},
                    "image_path": {"type": "string"},
                    "image_expect": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 256},
                        "maxItems": 32,
                    },
                    "ocr_expect": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 256},
                        "maxItems": 32,
                    },
                    "thinking_mode": {
                        "type": "string",
                        "enum": ["default", "enabled", "disabled", "unsupported"],
                    },
                    "reasoning_effort": {
                        "type": "string",
                        "enum": ["none", "minimal", "low", "medium", "high"],
                    },
                    "reasoning_evidence": {
                        "type": "string",
                        "enum": ["any", "required", "forbidden"],
                    },
                    "visible_answer_tokens": _bounded_integer_schema(1, 65536, 256),
                    "reasoning_headroom_tokens": _bounded_integer_schema(0, 65536, 0),
                    "allowed_finish_reasons": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 3600, 900),
                },
                required=["base_url", "model"],
            ),
            "handler": tool_preflight_probe,
        },
        "benchmark_probe": {
            "description": "Preview or run a bounded eval benchmark capacity command for a model endpoint.",
            "inputSchema": _schema(
                {
                    "base_url": {"type": "string"},
                    "model": {"type": "string"},
                    "api_key_env": {"type": "string"},
                    "requests": _bounded_integer_schema(1, 200, 60),
                    "concurrency": _bounded_integer_schema(1, 100, 20),
                    "max_tokens": _bounded_integer_schema(1, 4096, 64),
                    "ctx_tokens": _bounded_integer_schema(0, 262144, 0),
                    "no_thinking": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 1800, 900),
                },
                required=["base_url", "model"],
            ),
            "handler": tool_benchmark_probe,
        },
        "benchmark_artifact": {
            "description": "Preview or run a bounded eval benchmark capacity command and atomically replace a validated local JSON artifact.",
            "inputSchema": _schema(
                {
                    "base_url": {"type": "string"},
                    "model": {"type": "string"},
                    "artifact_path": {"type": "string"},
                    "api_key_env": {"type": "string"},
                    "requests": _bounded_integer_schema(1, 200, 60),
                    "concurrency": _bounded_integer_schema(1, 100, 20),
                    "burst": _bounded_integer_schema(0, 200, 0),
                    "max_tokens": _bounded_integer_schema(1, 4096, 64),
                    "ctx_tokens": _bounded_integer_schema(0, 262144, 0),
                    "max_model_len": _bounded_integer_schema(0, 1048576, 0),
                    "no_thinking": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 1800, 900),
                },
                required=["base_url", "model", "artifact_path"],
            ),
            "handler": tool_benchmark_artifact,
        },
    },
)
