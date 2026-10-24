"""Bounded native performance runs; native timing remains the measurement source."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Mapping
from urllib.parse import urlsplit

from .artifacts import atomic_write_json, evidence_file_reference, path_is_within, real_path
from .harnesses import HARNESS_ASSETS_SCHEMA
from .jobs import BenchmarkJobError, canonical_json_bytes, resolve_owned_run_path, utc_now
from .profiles import suite_budgets, validate_profile
from .request_controls import JOB_REQUEST_CONTROL_KEYS, resolve_job_request_controls
from .retention import (
    MAX_SWE_RETAINED_BYTES, MAX_SWE_RETAINED_FILES, MAX_SWE_RETAINED_DIRECTORIES,
    MAX_SWE_RETAINED_DEPTH, _digest as _file_digest, _safe_file,
)
from .swe import _budget_runner, _endpoint, _result_record
from .swe_agent_adapter import validate_budgets

PERFORMANCE_PLAN_SCHEMA = "anvil-serving.performance-plan/v1"
PERFORMANCE_RUN_SCHEMA = "anvil-serving.performance-run/v1"
MAX_NATIVE_JSON_BYTES = 32 * 1024 * 1024


def _digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise BenchmarkJobError("bad_performance_parameters", f"{name} must be an integer from {low} through {high}")
    return value


def validate_performance_parameters(profile, parameters):
    profile = validate_profile(profile)
    keys = {"runner", "workload", "concurrency", "request_count", "duration_s", "warmup_requests", "cache_condition"}
    if ("performance" not in profile["suites"] or not isinstance(parameters, Mapping)
            or set(parameters) - keys - JOB_REQUEST_CONTROL_KEYS):
        raise BenchmarkJobError("bad_performance_parameters", "unsupported performance parameters or suite")
    runner = parameters.get("runner")
    workload = parameters.get("workload")
    if not isinstance(runner, str) or runner not in {"lil", "aiperf"} or not isinstance(workload, Mapping) or set(workload) != {"input_tokens", "output_tokens"}:
        raise BenchmarkJobError("bad_performance_parameters", "select lil or aiperf and an exact token workload")
    if ("request_count" in parameters) == ("duration_s" in parameters):
        raise BenchmarkJobError("bad_performance_parameters", "select exactly one request_count or duration_s bound")
    condition = parameters.get("cache_condition", "basic")
    if not isinstance(condition, str) or condition not in {"basic", "cold", "warm", "competing-pressure"}:
        raise BenchmarkJobError("bad_performance_parameters", "unsupported cache condition")
    value = {"runner": runner, "workload": {
        "input_tokens": _integer(workload["input_tokens"], "input_tokens", 0, 1048576),
        "output_tokens": _integer(workload["output_tokens"], "output_tokens", 1, 65536)},
        "concurrency": _integer(parameters.get("concurrency", 1), "concurrency", 1, 256),
        "warmup_requests": _integer(parameters.get("warmup_requests", 0), "warmup_requests", 0, 10000),
        "cache_condition": condition,
        "request_controls": resolve_job_request_controls({key: parameters[key] for key in JOB_REQUEST_CONTROL_KEYS if key in parameters})}
    mode = "request_count" if "request_count" in parameters else "duration_s"
    value[mode] = _integer(parameters[mode], mode, 1, 100000 if mode == "request_count" else 3600)
    budgets = suite_budgets(profile, "performance")
    if budgets is None:
        raise BenchmarkJobError("missing_performance_budgets", "performance requires explicit suite budgets")
    if (value["workload"]["output_tokens"] > budgets["max_completion_tokens"]
            or value.get("request_count", 0) + value["warmup_requests"] > budgets["max_model_calls"]
            or value.get("duration_s", 0) >= budgets["deadline_s"]):
        raise BenchmarkJobError("bad_performance_budget", "requested workload exceeds the suite budget")
    _supported(value)
    return value


def _supported(parameters):
    from .performance_adapter import validate_supported_parameters
    try:
        validate_supported_parameters(parameters)
    except (ValueError, TypeError, KeyError) as exc:
        raise BenchmarkJobError("unsupported_performance_parameters", str(exc)) from exc


def _paths(run_path, runner):
    base = Path(run_path) / "performance"
    paths = {key: str(base / name) for key, name in {
        "performance": ".", "work": "work", "home": "home", "output": "native", "plan": "plan.json",
        "result": "result.json", "adapter_result": "adapter-result.json", "request_events": "requests.jsonl",
        "process": "process.json"}.items()}
    paths["work"] = str(Path(run_path) / "work" / "performance")
    paths["home"] = str(Path(run_path) / "work" / "performance-home")
    paths["native_output"] = str(base / "native" / ("lil.json" if runner == "lil" else "profile_export_aiperf.json"))
    return paths


def _adapter_command(python, plan_path):
    # An explicit argv path also works when the checkout contains PYTHONPATH separators.
    bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('anvil_serving.benchmarking.performance_adapter',run_name='__main__')"
    return [python, "-B", "-c", bootstrap, str(Path(__file__).resolve().parents[2]), "--plan", plan_path]


def build_performance_run_plan(profile, assets_manifest, *, endpoint, parameters, run_root,
                               cache_root, ownership_id, run_id, timeout_s=None):
    from .performance_prepare import verify_performance_environment

    profile = validate_profile(profile)
    params = validate_performance_parameters(profile, parameters)
    if (not isinstance(assets_manifest, Mapping) or assets_manifest.get("schema") != HARNESS_ASSETS_SCHEMA
            or assets_manifest.get("profile_sha256") != profile["content_sha256"]
            or assets_manifest.get("suite") != "performance"):
        raise BenchmarkJobError("bad_harness_manifest", "performance assets do not match this profile and suite")
    runner = params["runner"]
    asset = assets_manifest.get("assets", {}).get(runner, {})
    if any(asset.get(key) != profile["adapters"][runner][key] for key in ("source", "revision")) or asset.get("dirty") is not False:
        raise BenchmarkJobError("performance_source_mismatch", "performance source identity is not pinned")
    cache = real_path(cache_root)
    environment = assets_manifest.get("performance_environments", {}).get(runner)
    python = verify_performance_environment(environment, cache_root=cache, runner_name=runner)
    budgets = validate_budgets(suite_budgets(profile, "performance"))
    timeout = budgets["deadline_s"] if timeout_s is None else timeout_s
    if type(timeout) not in {float, int} or not 0 < timeout <= budgets["deadline_s"]:
        raise BenchmarkJobError("bad_performance_timeout", "performance timeout must fit the admitted deadline")
    target = _endpoint(endpoint)
    url = urlsplit(target["base_url"])
    if url.username or url.password or url.query or url.fragment or not url.hostname or url.path != "/v1":
        raise BenchmarkJobError("bad_endpoint", "performance endpoint must end with /v1 and omit credentials and query parameters")
    if runner == "lil" and target["model"] == "Qwen3.5":
        raise BenchmarkJobError("bad_endpoint", "LIL default model sentinel is not an explicit alias")
    run_path = resolve_owned_run_path(run_root, ownership_id=ownership_id, run_id=run_id)
    paths = _paths(run_path, runner)
    adapter = Path(__file__).with_name("performance_adapter.py")
    plan = {"schema": PERFORMANCE_PLAN_SCHEMA, "runner": runner, "parameters": params,
        "endpoint": target, "request_controls": params["request_controls"], "budgets": budgets,
        "timeout_s": timeout, "environment": environment, "assets": assets_manifest["assets"],
        "run_root": real_path(run_root), "cache_root": cache, "run_path": run_path,
        "ownership_id": ownership_id, "run_id": run_id, "paths": paths,
        "profile": profile["name"], "profile_sha256": profile["content_sha256"],
        "adapter_sha256": hashlib.sha256(adapter.read_bytes()).hexdigest(),
        "instrumentation": {"lil_unconditional_warmup_disabled": runner == "lil",
                            "native_stream_parser": True, "http_attempt_guard": True},
        "command": _adapter_command(python, paths["plan"])}
    plan["plan_sha256"] = _digest(plan)
    verify_plan(plan)
    return plan


def verify_plan(plan):
    from .performance_prepare import verify_performance_environment

    if sys.platform != "linux":
        raise BenchmarkJobError("unsupported_performance_platform", "managed performance execution requires Linux")
    if (not isinstance(plan, Mapping) or plan.get("schema") != PERFORMANCE_PLAN_SCHEMA
            or plan.get("plan_sha256") != _digest({k: v for k, v in plan.items() if k != "plan_sha256"})):
        raise BenchmarkJobError("bad_performance_plan", "performance plan identity changed")
    _supported(plan["parameters"])
    python = verify_performance_environment(plan["environment"], cache_root=plan["cache_root"], runner_name=plan["runner"])
    owned = resolve_owned_run_path(plan["run_root"], ownership_id=plan["ownership_id"], run_id=plan["run_id"])
    if owned != plan["run_path"] or plan["paths"] != _paths(owned, plan["runner"]):
        raise BenchmarkJobError("bad_performance_plan", "performance artifact ownership changed")
    if any(not path_is_within(path, owned) for path in plan["paths"].values()):
        raise BenchmarkJobError("bad_performance_plan", "performance artifact escaped owned run")
    if plan["command"] != _adapter_command(python, plan["paths"]["plan"]):
        raise BenchmarkJobError("bad_performance_plan", "command does not match the prepared interpreter")
    if hashlib.sha256(Path(__file__).with_name("performance_adapter.py").read_bytes()).hexdigest() != plan["adapter_sha256"]:
        raise BenchmarkJobError("performance_adapter_mismatch", "performance containment adapter changed")
    budgets = validate_budgets(plan["budgets"])
    if (type(plan["timeout_s"]) not in {int, float}
            or not 0 < plan["timeout_s"] <= budgets["deadline_s"]):
        raise BenchmarkJobError("bad_performance_plan", "performance timeout exceeds its admitted budget")


def _read_json(path, root, *, limit=MAX_NATIVE_JSON_BYTES):
    relative = Path(path).relative_to(root).as_posix()
    safe = _safe_file(str(root), relative)
    if safe is None or not stat.S_ISREG(os.stat(safe).st_mode):
        raise ValueError("unsafe performance evidence")
    with open(safe, "rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("oversized performance evidence")
    def invalid_constant(value):
        raise ValueError("nonfinite native JSON constant")
    return json.loads(raw, parse_constant=invalid_constant)


def _number(value, *, positive=False):
    try:
        return type(value) in {float, int} and math.isfinite(value) and (value > 0 if positive else value >= 0)
    except OverflowError:
        return False


def _metric(value, unit, source, *, reason=None):
    valid = reason is None and _number(value)
    return {"value": value if valid else None, "unit": unit, "source": source,
            "status": "measured" if valid else "unknown",
            "unavailable_reason": None if valid else reason or "native measurement missing or invalid"}


def normalize_performance_metrics(plan, native):
    """Project native metrics without replacing timings or inferring missing observations."""
    metrics = {}
    if not isinstance(native, Mapping):
        raise ValueError("native performance result is not an object")
    if plan["runner"] == "aiperf":
        if native.get("schema_version") != "1.4":
            raise ValueError("unsupported AIPerf export schema")
        fields = {
            "time_to_first_token": {"ms", "sec", "us", "ns"}, "time_to_first_output_token": {"ms", "sec", "us", "ns"},
            "request_latency": {"ms", "sec", "us", "ns"}, "inter_token_latency": {"ms", "sec", "us", "ns"},
            "inter_chunk_latency": {"ms", "sec", "us", "ns"}, "output_token_throughput": {"tokens/sec"},
            "output_token_throughput_per_user": {"tokens/sec/user"}, "request_throughput": {"requests/sec"},
            "request_count": {"requests"}, "error_request_count": {"requests"},
            "input_sequence_length": {"tokens"}, "output_sequence_length": {"tokens"}}
        for name, units in fields.items():
            record = native.get(name)
            record = record if isinstance(record, Mapping) else {}
            unit = record.get("unit")
            reason = None if unit in units else "native unit missing or unsupported"
            distribution = name not in {"request_count", "error_request_count", "request_throughput", "output_token_throughput"}
            if distribution and not _number(record.get("count"), positive=True):
                reason = reason or "native distribution has no measured observations"
            metrics[name] = _metric(record.get("avg"), unit, f"{name}.avg", reason=reason)
            if distribution and reason is None:
                metrics[name]["distribution"] = {
                    key: value for key, value in record.items()
                    if key in {"p50", "p90", "p95", "p99", "min", "max", "std", "count"} and _number(value)}
        # Pinned AIPerf ERROR_ONLY counters are omitted on clean runs. Keep the
        # missing metric unknown; explicit empty error evidence can still prove completion.
        no_errors = (metrics["error_request_count"]["value"] == 0
                     or "error_request_count" not in native and native.get("error_summary") == [])
        complete = (native.get("is_complete") is True and native.get("was_cancelled") is False
                    and not native.get("incomplete_reason") and not native.get("error_summary")
                    and metrics["request_count"]["status"] == "measured"
                    and metrics["request_count"]["value"] > 0
                    and no_errors)
        expected = plan["parameters"].get("request_count")
        if expected is not None:
            complete = complete and metrics["request_count"]["value"] == expected
        completion = {key: native.get(key) for key in ("is_complete", "was_cancelled", "incomplete_reason", "error_summary")}
    else:
        rows = native.get("results")
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
            raise ValueError("expected exactly one LIL measurement cell")
        row, metadata = rows[0], native.get("metadata", {})
        samples = row.get("request_samples")
        if not isinstance(samples, list) or any(not isinstance(sample, Mapping) for sample in samples):
            raise ValueError("invalid LIL request samples")
        completed = [sample for sample in samples if sample.get("completed") is True]
        for name, native_name, sample_name in (
            ("time_to_first_token", "ttft_avg", "ttft"),
            ("request_latency", "request_latency_avg", "latency"),
            ("inter_chunk_latency", "chunk_inter_token_latency_avg", "chunk_inter_token_latency_avg")):
            proven = bool(completed) and all(_number(sample.get(sample_name), positive=True) for sample in completed)
            metrics[name] = _metric(row.get(native_name), "s", f"results[0].{native_name}",
                reason=None if proven and _number(row.get(native_name), positive=True) else "missing positive completed-sample evidence; native zero may be a default")
        for name, unit in (("inter_token_latency", "s"), ("output_token_throughput", "tokens/sec"),
                           ("output_token_throughput_per_user", "tokens/sec/user"),
                           ("input_sequence_length", "tokens"), ("output_sequence_length", "tokens")):
            metrics[name] = _metric(None, unit, "results[0].request_samples",
                reason="native sample does not distinguish server usage from chunk-count fallback")
        metrics["time_to_first_output_token"] = _metric(None, "s", None, reason="LIL does not export a separate nonreasoning first-output timestamp")
        metrics["completed_request_count"] = _metric(len(completed), "requests", "results[0].request_samples[].completed")
        metrics["request_count"] = _metric(row.get("request_count"), "requests", "results[0].request_count",
            reason=None if row.get("request_count") == len(samples) else "sample count mismatch")
        metrics["error_request_count"] = _metric(row.get("num_errors"), "requests", "results[0].num_errors")
        metrics["measurement_duration"] = _metric(row.get("measurement_seconds"), "s", "results[0].measurement_seconds")
        expected = plan["parameters"].get("request_count")
        complete = (bool(completed) and len(completed) == len(samples) and row.get("request_count") == len(samples)
            and row.get("completed_request_count") == len(completed) and row.get("num_errors") == 0
            and not row.get("failure_reason") and not row.get("loop_detected") and not row.get("capacity_limited")
            and metadata.get("model") == plan["endpoint"]["model"]
            and row.get("concurrency") == plan["parameters"]["concurrency"]
            and (expected is None or len(completed) == expected))
        completion = {key: row.get(key) for key in ("failure_reason", "loop_detected", "capacity_limited", "aggregate_source")}
    return {"metrics": metrics, "native_complete": complete, "completion": completion,
        "cache_condition": {"requested": plan["parameters"]["cache_condition"], "status": "unknown",
                            "unavailable_reason": "no independent cache-state measurement"},
        "kernel_timing": {"status": "unknown", "unavailable_reason": "native client timing is not kernel timing"},
        "timing_scope": "native client timings include transport guard overhead; no overhead correction is inferred",
        "workload_semantics": "LIL zero input_tokens means zero padding, not an empty prompt" if plan["runner"] == "lil"
        else "AIPerf input length targets the prepared builtin tokenizer, not a proven target-model tokenizer"}


def _adapter_evidence(plan):
    paths, root = plan["paths"], plan["run_path"]
    terminal = _read_json(paths["adapter_result"], root, limit=1024 * 1024)
    events_path = _safe_file(root, Path(paths["request_events"]).relative_to(root).as_posix())
    if events_path is None:
        raise ValueError("missing adapter events")
    with open(events_path, "rb") as handle:
        raw = handle.read(MAX_NATIVE_JSON_BYTES + 1)
    if len(raw) > MAX_NATIVE_JSON_BYTES:
        raise ValueError("oversized adapter events")
    lines = raw.splitlines(keepends=True)
    events = [json.loads(line) for line in lines]
    budget = terminal["budget"]
    if (not events or events[0].get("event") != "performance_adapter_ready"
            or events[0].get("adapter_sha256") != plan["adapter_sha256"]
            or events[-1].get("event") != "adapter_finished" or events[-1].get("status") != "complete"
            or events[-1].get("event_count") != len(events) - 1
            or events[-1].get("byte_count") != sum(map(len, lines[:-1]))
            or [row.get("seq") for row in events] != list(range(1, len(events) + 1))
            or sum(row.get("event") == "performance_transport_started" for row in events) != budget["model_calls"]
            or not budget.get("exhausted") and budget["model_calls"] != plan["parameters"]["request_count"]
            or terminal.get("plan_sha256") != plan["plan_sha256"]
            or terminal.get("adapter_sha256") != plan["adapter_sha256"]
            or not terminal.get("events_complete") or budget.get("configured") != plan["budgets"]):
        raise ValueError("incomplete adapter observations")
    return terminal


def run_performance_benchmark(plan, *, command_runner=_budget_runner, environ=None):
    verify_plan(plan)
    paths = plan["paths"]
    base = Path(paths["performance"])
    base.mkdir(parents=True, exist_ok=False)
    for key in ("work", "home", "output"):
        Path(paths[key]).mkdir(parents=True)
    atomic_write_json(paths["plan"], dict(plan))
    result = {"schema": PERFORMANCE_RUN_SCHEMA, "state": "incomplete", "suite": "performance",
        "runner": plan["runner"], "run_id": plan["run_id"], "profile": plan["profile"],
        "plan_sha256": plan["plan_sha256"], "endpoint": plan["endpoint"], "parameters": plan["parameters"],
        "failure": None, "summary": {"metrics": {}, "native_complete": False}, "promotion": {"authorized": False}}
    atomic_write_json(paths["result"], result)
    parent = os.environ if environ is None else environ
    env = {key: parent[key] for key in ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR") if key in parent}
    env.update(HOME=paths["home"], PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
        LLM_BENCH_NO_UPDATE_CHECK="1",
        HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    tokenizer = plan["environment"].get("tokenizer")
    if tokenizer:
        env["TIKTOKEN_CACHE_DIR"] = str(Path(plan["cache_root"]) / tokenizer["cache_key"])
    started, stamp = time.monotonic(), utc_now()
    interrupted = None
    try:
        auth = plan["endpoint"].get("auth_env")
        if auth and not parent.get(auth, "").strip():
            raise BenchmarkJobError("missing_credential", "performance endpoint credential reference is unavailable")
        env["OPENAI_API_KEY"] = parent[auth].strip() if auth else ""
        verify_plan(plan)
        if shutil.disk_usage(base).free < plan["budgets"]["min_free_disk_bytes"]:
            raise BenchmarkJobError("min_free_disk_bytes", "performance disk reserve is unavailable")
        process = command_runner(plan["command"], paths["work"], plan["timeout_s"], env)
        stage = _result_record(plan["command"], process, time.monotonic() - started, stamp)
        if stage["returncode"]:
            result["failure"] = {"class": "broken_harness", "code": "performance_adapter_failed"}
    except subprocess.TimeoutExpired as exc:
        stage = _result_record(plan["command"], exc, time.monotonic() - started, stamp)
        result["failure"] = {"class": "budget_exhausted", "code": "deadline_s"}
    except BaseException as exc:
        stage = {"status": "failed", "error_type": type(exc).__name__}
        result["failure"] = {"class": "infrastructure_failure", "code": getattr(exc, "code", "performance_process_failed")}
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            interrupted = exc
            result["failure"] = {"class": "cancelled", "code": "performance_interrupted"}
    atomic_write_json(paths["process"], stage)
    result["stage"] = stage
    try:
        terminal = _adapter_evidence(plan)
        result["budget"] = terminal["budget"]
        result["instrumentation"] = terminal.get("instrumentation")
        result["request_observations"] = evidence_file_reference(paths["request_events"], root=plan["run_path"])
        if terminal["budget"].get("exhausted"):
            result["failure"] = {"class": "budget_exhausted", "code": terminal["budget"].get("reason")}
        elif terminal.get("outcome") != "completed":
            result["failure"] = result["failure"] or {"class": "broken_harness", "code": "incomplete_performance_observations"}
    except (OSError, ValueError, TypeError, KeyError):
        result["failure"] = result["failure"] or {"class": "broken_harness", "code": "missing_performance_observations"}
    try:
        native = _read_json(paths["native_output"], plan["run_path"])
        result["summary"] = normalize_performance_metrics(plan, native)
        result["native_reference"] = evidence_file_reference(paths["native_output"], root=plan["run_path"])
        if not result["summary"]["native_complete"]:
            result["failure"] = result["failure"] or {"class": "broken_harness", "code": "native_performance_incomplete"}
    except (OSError, ValueError, TypeError, KeyError):
        result["failure"] = result["failure"] or {"class": "broken_harness", "code": "missing_native_performance_results"}
    if result["failure"] is None:
        result["state"] = "completed"
    atomic_write_json(paths["result"], result)
    result["retention"] = retain_performance_artifacts(run_path=plan["run_path"])
    if result["retention"]["state"] != "retained":
        result["state"] = "incomplete"
        result["failure"] = result["failure"] or {"class": "infrastructure_failure", "code": "performance_retention_incomplete"}
    if interrupted is not None:
        raise interrupted
    return result


def retain_performance_artifacts(*, run_path):
    """Inventory canonical native outputs in place; reject mutation of a previous snapshot."""
    root = Path(run_path).resolve()
    base = root / "performance"
    result = {"schema": "anvil-serving.performance-retention/v1", "state": "incomplete", "artifacts": [], "missing": []}
    required = {"plan.json", "result.json", "process.json", "requests.jsonl", "adapter-result.json"}
    if not base.exists() and not base.is_symlink():
        result["missing"] = sorted(required) + ["native output"]
        return result
    try:
        if base.is_symlink():
            raise ValueError("symlink evidence root")
        total, directories = 0, 0
        stack = [(base, 0)]
        while stack:
            directory, depth = stack.pop()
            directories += 1
            if directories > MAX_SWE_RETAINED_DIRECTORIES or depth > MAX_SWE_RETAINED_DEPTH:
                raise ValueError("too many evidence directories")
            with os.scandir(directory) as entries:
                for entry in entries:
                    relative = Path(entry.path).relative_to(base).as_posix()
                    if relative == "retention.json":
                        continue
                    if entry.is_symlink():
                        raise ValueError("symlink evidence path")
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((Path(entry.path), depth + 1))
                        if len(stack) + directories > MAX_SWE_RETAINED_DIRECTORIES:
                            raise ValueError("too many evidence directories")
                        continue
                    if not entry.is_file(follow_symlinks=False) or len(result["artifacts"]) >= MAX_SWE_RETAINED_FILES:
                        raise ValueError("invalid or excessive evidence files")
                    size, digest = _file_digest(entry.path, max_bytes=MAX_SWE_RETAINED_BYTES - total)
                    total += size
                    result["artifacts"].append({"path": "performance/" + relative, "bytes": size, "sha256": digest})
        result["artifacts"].sort(key=lambda row: row["path"])
        found = {row["path"].removeprefix("performance/") for row in result["artifacts"]}
        result["missing"] = sorted(required - found)
        if not any(path.startswith("native/") for path in found):
            result["missing"].append("native output")
        result["state"] = "incomplete" if result["missing"] else "retained"
        manifest = base / "retention.json"
        if manifest.exists() or manifest.is_symlink():
            if _read_json(str(manifest), str(root), limit=1024 * 1024) != result:
                raise ValueError("previously retained performance evidence changed")
        else:
            atomic_write_json(str(manifest), result)
        result["manifest_reference"] = evidence_file_reference(str(manifest), root=str(root))
    except (OSError, ValueError, TypeError, KeyError):
        result["state"] = "failed"
    return result
