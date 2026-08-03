"""Explicit benchmarks MCP tool family."""

from __future__ import annotations

import os
import json
import sys

from ....model_controls import REASONING_EFFORT_CHOICES
from ....benchmarking.jobs import BenchmarkJobError, validate_job_spec
from ....benchmarking.preflight import run_benchmark_preflight
from ....benchmarking.harnesses import (
    cleanup_harness_work,
    harness_asset_status,
    prepare_harness_assets,
)
from ....benchmarking.profiles import load_profile
from ....benchmarking.worker import cancel_benchmark_job, launch_benchmark_job
from ...controller.store import BenchmarkJobStore
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


def _benchmark_job_store() -> BenchmarkJobStore:
    path = os.environ.get("ANVIL_BENCHMARK_JOB_DB")
    run_root = os.environ.get("ANVIL_BENCHMARK_RUN_ROOT")
    kwargs = {"run_root": run_root} if run_root else {}
    return BenchmarkJobStore(path, **kwargs) if path else BenchmarkJobStore(**kwargs)


def _job_error(exc: BenchmarkJobError) -> ToolError:
    return ToolError(exc.code, exc.message, exc.details)


def _job_for_suite(store: BenchmarkJobStore, run_id: str, suite: str) -> dict:
    try:
        record = store.status(run_id)
    except BenchmarkJobError as exc:
        raise _job_error(exc) from None
    if record is None:
        raise ToolError("job_not_found", "benchmark job does not exist", {"run_id": run_id})
    if record["spec"]["suite"] != suite:
        raise ToolError("suite_mismatch", "run belongs to a different suite")
    return record


def tool_benchmark_job_submit(args: dict) -> dict:
    suite = _str_arg(args, "suite", required=True)
    raw = _str_arg(args, "spec_json", required=True)
    follow = _arg_bool(args.get("follow"), False, name="follow")
    detach = _arg_bool(args.get("detach"), False, name="detach")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    if follow and detach:
        raise ToolError("bad_argument", "follow and detach are mutually exclusive")
    if not confirm:
        raise ToolError("confirmation_required", "benchmark submission requires confirmation")
    try:
        value = json.loads(raw)
        spec = validate_job_spec(value)
        if spec["suite"] != suite:
            raise BenchmarkJobError(
                "suite_mismatch", "job specification suite does not match the command"
            )
        store = _benchmark_job_store()
        disposition, job = store.submit(spec)
        launch = (
            launch_benchmark_job(
                path=store.path,
                run_root=store.run_root,
                run_id=job["spec"]["run_id"],
            )
            if job["state"] == "queued"
            else {"launched": False, "reason": f"job is {job['state']}"}
        )
    except (TypeError, json.JSONDecodeError):
        raise ToolError("bad_spec_json", "spec_json must be valid JSON") from None
    except BenchmarkJobError as exc:
        raise _job_error(exc) from None
    return _ok(
        {
            "disposition": disposition,
            "job": job,
            "worker": launch,
            "follow": follow,
            "detached": detach or not follow,
        }
    )


def tool_benchmark_job_preflight(args: dict) -> dict:
    suite = _str_arg(args, "suite", required=True)
    raw_spec = _str_arg(args, "spec_json", required=True)
    raw_requirements = _str_arg(args, "requirements_json") or "{}"
    try:
        spec = validate_job_spec(json.loads(raw_spec))
        requirements = json.loads(raw_requirements)
        if not isinstance(requirements, dict):
            raise BenchmarkJobError("bad_json", "requirements_json must be an object")
        if spec["suite"] != suite:
            raise BenchmarkJobError(
                "suite_mismatch", "job specification suite does not match the command"
            )
        store = _benchmark_job_store()
        artifact = run_benchmark_preflight(
            spec,
            run_root=store.run_root,
            requirements=requirements,
            assets_root=os.environ.get("ANVIL_BENCHMARK_ASSETS_ROOT"),
        )
    except (TypeError, json.JSONDecodeError):
        raise ToolError("bad_json", "benchmark preflight JSON is invalid") from None
    except BenchmarkJobError as exc:
        raise _job_error(exc) from None
    return _ok(artifact)


def tool_benchmark_job_status(args: dict) -> dict:
    store = _benchmark_job_store()
    return _ok(_job_for_suite(store, _str_arg(args, "run_id", required=True), _str_arg(args, "suite", required=True)))


def tool_benchmark_job_logs(args: dict) -> dict:
    store = _benchmark_job_store()
    run_id = _str_arg(args, "run_id", required=True)
    _job_for_suite(store, run_id, _str_arg(args, "suite", required=True))
    cursor = _bounded_int_arg(args, "cursor", 0, min_value=0, max_value=2147483647)
    limit = _bounded_int_arg(args, "limit", 100, min_value=1, max_value=1000)
    _arg_bool(args.get("follow"), False, name="follow")
    try:
        return _ok(store.logs(run_id, cursor=cursor, limit=limit))
    except BenchmarkJobError as exc:
        raise _job_error(exc) from None


def tool_benchmark_job_cancel(args: dict) -> dict:
    if not _arg_bool(args.get("confirm"), False, name="confirm"):
        raise ToolError("confirmation_required", "benchmark cancellation requires confirmation")
    store = _benchmark_job_store()
    run_id = _str_arg(args, "run_id", required=True)
    _job_for_suite(store, run_id, _str_arg(args, "suite", required=True))
    try:
        return _ok(cancel_benchmark_job(store, run_id))
    except BenchmarkJobError as exc:
        raise _job_error(exc) from None


def tool_benchmark_job_artifact(args: dict) -> dict:
    store = _benchmark_job_store()
    run_id = _str_arg(args, "run_id", required=True)
    _job_for_suite(store, run_id, _str_arg(args, "suite", required=True))
    try:
        artifact = store.artifact(run_id)
    except BenchmarkJobError as exc:
        raise _job_error(exc) from None
    if artifact is None:
        raise ToolError("artifact_pending", "benchmark artifact is not available")
    return _ok(artifact)


def tool_benchmark_harness_prepare(args: dict) -> dict:
    if not _arg_bool(args.get("confirm"), False, name="confirm"):
        raise ToolError("confirmation_required", "harness preparation requires confirmation")
    profile_name = _str_arg(args, "profile", required=True)
    suite = _str_arg(args, "suite", required=True)
    run_id = _str_arg(args, "run_id", required=True)
    ownership_id = _str_arg(args, "ownership_id", required=True)
    offline = _arg_bool(args.get("offline"), False, name="offline")
    max_download_bytes = _bounded_int_arg(
        args,
        "max_download_bytes",
        20 * 1024**3,
        min_value=1,
        max_value=1024**4,
    )
    store = _benchmark_job_store()
    cache_root = os.environ.get(
        "ANVIL_BENCHMARK_CACHE_ROOT",
        os.path.expanduser("~/.anvil-serving/benchmark-harness-cache"),
    )
    try:
        result = prepare_harness_assets(
            load_profile(profile_name),
            suite=suite,
            run_root=store.run_root,
            ownership_id=ownership_id,
            run_id=run_id,
            cache_root=cache_root,
            offline=offline,
            max_download_bytes=max_download_bytes,
        )
    except BenchmarkJobError as exc:
        raise _job_error(exc) from None
    return _ok(result)


def tool_benchmark_harness_status(args: dict) -> dict:
    store = _benchmark_job_store()
    try:
        result = harness_asset_status(
            run_root=store.run_root,
            ownership_id=_str_arg(args, "ownership_id", required=True),
            run_id=_str_arg(args, "run_id", required=True),
        )
    except BenchmarkJobError as exc:
        raise _job_error(exc) from None
    return _ok(result)


def tool_benchmark_harness_cleanup(args: dict) -> dict:
    if not _arg_bool(args.get("confirm"), False, name="confirm"):
        raise ToolError("confirmation_required", "harness cleanup requires confirmation")
    store = _benchmark_job_store()
    try:
        result = cleanup_harness_work(
            run_root=store.run_root,
            ownership_id=_str_arg(args, "ownership_id", required=True),
            run_id=_str_arg(args, "run_id", required=True),
        )
    except BenchmarkJobError as exc:
        raise _job_error(exc) from None
    return _ok(result)


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
    known_checks = {"smoke", "json", "needle", "tools", "image", "ocr", "video"}
    unknown_checks = sorted(set(selected_checks) - known_checks)
    if not selected_checks or unknown_checks:
        raise ToolError(
            "bad_argument",
            "checks must select smoke,json,needle,tools,image,ocr,video",
            {"unknown": unknown_checks},
        )
    image_path = _str_arg(args, "image_path")
    video_path = _str_arg(args, "video_path")
    image_expect = [item.strip() for item in _str_list_arg(args, "image_expect")]
    ocr_expect = [item.strip() for item in _str_list_arg(args, "ocr_expect")]
    video_expect = [item.strip() for item in _str_list_arg(args, "video_expect")]
    if any(not item for item in image_expect + ocr_expect + video_expect):
        raise ToolError("bad_argument", "multimodal expectations must be non-empty strings")
    allowed_roots = (
        os.path.abspath(os.getcwd()),
        os.path.abspath(os.path.expanduser("~/.anvil-serving")),
    )

    def resolve_media_path(path: str, *, kind: str) -> str:
        resolved = os.path.abspath(os.path.expanduser(path))
        try:
            contained = any(
                os.path.commonpath((resolved, root)) == root
                for root in allowed_roots
            )
        except ValueError:
            contained = False
        if not contained:
            raise ToolError(
                "unsafe_%s_path" % kind,
                "%s_path must be under the controller working directory or ~/.anvil-serving"
                % kind,
            )
        if os.path.islink(resolved) or not os.path.isfile(resolved):
            raise ToolError(
                "bad_argument",
                "%s_path must be a regular non-symlink file" % kind,
            )
        return resolved

    if {"image", "ocr"} & set(selected_checks):
        if not image_path:
            raise ToolError("bad_argument", "multimodal checks require image_path")
        image_path = resolve_media_path(image_path, kind="image")
        if "image" in selected_checks and not image_expect:
            raise ToolError("bad_argument", "image check requires image_expect")
        if "ocr" in selected_checks and not ocr_expect:
            raise ToolError("bad_argument", "ocr check requires ocr_expect")
    if "video" in selected_checks:
        if not video_path:
            raise ToolError("bad_argument", "video check requires video_path")
        video_path = resolve_media_path(video_path, kind="video")
        if not video_expect:
            raise ToolError("bad_argument", "video check requires video_expect")
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
    if video_path:
        argv += ["--video-path", video_path]
    for expectation in image_expect:
        argv += ["--image-expect", expectation]
    for expectation in ocr_expect:
        argv += ["--ocr-expect", expectation]
    for expectation in video_expect:
        argv += ["--video-expect", expectation]
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
        "benchmark_harness_prepare": {
            "description": "Prepare exact external benchmark assets through the managed worker cache.",
            "inputSchema": _schema(
                {
                    "suite": {"type": "string", "enum": ["context", "agentic", "swe"]},
                    "profile": {"type": "string", "enum": ["smoke", "scout", "deep"]},
                    "run_id": {"type": "string", "maxLength": 128},
                    "ownership_id": {"type": "string", "maxLength": 128},
                    "offline": {"type": "boolean"},
                    "max_download_bytes": _bounded_integer_schema(1, 1024**4, 20 * 1024**3),
                    "confirm": {"type": "boolean"},
                },
                required=["suite", "profile", "run_id", "ownership_id"],
            ),
            "handler": tool_benchmark_harness_prepare,
        },
        "benchmark_harness_status": {
            "description": "Inspect one run's prepared external benchmark assets.",
            "inputSchema": _schema(
                {
                    "suite": {"type": "string", "enum": ["context", "agentic", "swe"]},
                    "run_id": {"type": "string", "maxLength": 128},
                    "ownership_id": {"type": "string", "maxLength": 128},
                },
                required=["suite", "run_id", "ownership_id"],
            ),
            "handler": tool_benchmark_harness_status,
        },
        "benchmark_harness_cleanup": {
            "description": "Clean only a benchmark run's owned work directory.",
            "inputSchema": _schema(
                {
                    "suite": {"type": "string", "enum": ["context", "agentic", "swe"]},
                    "run_id": {"type": "string", "maxLength": 128},
                    "ownership_id": {"type": "string", "maxLength": 128},
                    "confirm": {"type": "boolean"},
                },
                required=["suite", "run_id", "ownership_id"],
            ),
            "handler": tool_benchmark_harness_cleanup,
        },
        "benchmark_job_preflight": {
            "description": "Validate a benchmark endpoint and isolated worker without model lifecycle changes.",
            "inputSchema": _schema(
                {
                    "suite": {"type": "string", "enum": ["context", "agentic", "swe"]},
                    "spec_json": {"type": "string", "maxLength": 262144},
                    "requirements_json": {"type": "string", "maxLength": 262144},
                },
                required=["suite", "spec_json"],
            ),
            "handler": tool_benchmark_job_preflight,
        },
        "benchmark_job_submit": {
            "description": "Submit one durable context, agentic, or SWE benchmark job.",
            "inputSchema": _schema(
                {
                    "suite": {"type": "string", "enum": ["context", "agentic", "swe"]},
                    "spec_json": {"type": "string", "maxLength": 262144},
                    "follow": {"type": "boolean"},
                    "detach": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                },
                required=["suite", "spec_json"],
            ),
            "handler": tool_benchmark_job_submit,
        },
        "benchmark_job_status": {
            "description": "Read durable benchmark job status.",
            "inputSchema": _schema(
                {
                    "suite": {"type": "string", "enum": ["context", "agentic", "swe"]},
                    "run_id": {"type": "string", "maxLength": 128},
                },
                required=["suite", "run_id"],
            ),
            "handler": tool_benchmark_job_status,
        },
        "benchmark_job_logs": {
            "description": "Read bounded cursor logs for a durable benchmark job.",
            "inputSchema": _schema(
                {
                    "suite": {"type": "string", "enum": ["context", "agentic", "swe"]},
                    "run_id": {"type": "string", "maxLength": 128},
                    "cursor": _bounded_integer_schema(0, 2147483647, 0),
                    "limit": _bounded_integer_schema(1, 1000, 100),
                    "follow": {"type": "boolean"},
                },
                required=["suite", "run_id"],
            ),
            "handler": tool_benchmark_job_logs,
        },
        "benchmark_job_cancel": {
            "description": "Cancel a durable benchmark job after recording partial evidence.",
            "inputSchema": _schema(
                {
                    "suite": {"type": "string", "enum": ["context", "agentic", "swe"]},
                    "run_id": {"type": "string", "maxLength": 128},
                    "confirm": {"type": "boolean"},
                },
                required=["suite", "run_id"],
            ),
            "handler": tool_benchmark_job_cancel,
        },
        "benchmark_job_artifact": {
            "description": "Read a durable benchmark job artifact.",
            "inputSchema": _schema(
                {
                    "suite": {"type": "string", "enum": ["context", "agentic", "swe"]},
                    "run_id": {"type": "string", "maxLength": 128},
                },
                required=["suite", "run_id"],
            ),
            "handler": tool_benchmark_job_artifact,
        },
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
                    "video_path": {"type": "string"},
                    "video_expect": {
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
                        "enum": list(REASONING_EFFORT_CHOICES),
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
