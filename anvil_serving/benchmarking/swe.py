"""Pinned mini-SWE-agent execution with official SWE-bench grading."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence

from ..model_controls import REASONING_EFFORT_CHOICES
from .artifacts import atomic_write_json, path_is_within, real_path
from .harnesses import HARNESS_ASSETS_SCHEMA, MAX_HARNESS_OUTPUT_BYTES
from .jobs import BenchmarkJobError, canonical_json_bytes, resolve_owned_run_path, utc_now
from .profiles import validate_profile


SWE_RUN_SCHEMA = "anvil-serving.swe-run/v1"
SWE_PLAN_SCHEMA = "anvil-serving.swe-plan/v1"
SWE_DATASET = "princeton-nlp/SWE-bench_Verified"
SWE_SUBSET = "verified"
SWE_SPLIT = "test"
SWE_FAILURE_CLASSES = frozenset({
    "broken_harness",
    "image_failure",
    "test_failure",
    "model_failure",
    "timeout",
    "infrastructure_failure",
})
_INSTANCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*__[A-Za-z0-9][A-Za-z0-9_.-]*$")
_REQUEST_ID_KEYS = frozenset({"request_id", "x-request-id", "x_request_id"})
SWECommandRunner = Callable[[Sequence[str], str, float, Mapping[str, str]], Any]


def _default_runner(
    argv: Sequence[str], cwd: str, timeout: float, env: Mapping[str, str]
):
    return subprocess.run(
        list(argv),
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _bounded_text(value: Any) -> tuple[str, bool]:
    raw = value if isinstance(value, bytes) else str(value or "").encode("utf-8")
    truncated = len(raw) > MAX_HARNESS_OUTPUT_BYTES
    return raw[:MAX_HARNESS_OUTPUT_BYTES].decode("utf-8", "replace"), truncated


def _result_record(argv: Sequence[str], result: Any, duration_s: float) -> dict[str, Any]:
    stdout, stdout_truncated = _bounded_text(getattr(result, "stdout", b""))
    stderr, stderr_truncated = _bounded_text(getattr(result, "stderr", b""))
    return {
        "command": list(argv),
        "returncode": int(getattr(result, "returncode", 1)),
        "duration_s": round(duration_s, 6),
        "stdout": stdout,
        "stderr": stderr,
        "output_truncated": stdout_truncated or stderr_truncated,
    }


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: str, *, code: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkJobError(code, f"required SWE artifact is missing or invalid: {path}") from exc


def validate_swe_selection(profile: Mapping[str, Any], instance_ids: Any) -> list[str]:
    """Require an explicit, ordered, duplicate-free selection for every SWE run."""
    validated = validate_profile(profile)
    limit = validated["suites"]["swe"]["instance_limit"]
    if not isinstance(instance_ids, list) or len(instance_ids) != limit:
        raise BenchmarkJobError(
            "explicit_swe_selection_required",
            f"profile {validated['name']!r} requires exactly {limit} explicit instance IDs",
            {"instance_limit": limit},
        )
    if not all(isinstance(item, str) and _INSTANCE_RE.fullmatch(item) for item in instance_ids):
        raise BenchmarkJobError("bad_swe_selection", "SWE instance IDs are invalid")
    if len(set(instance_ids)) != len(instance_ids):
        raise BenchmarkJobError("bad_swe_selection", "SWE instance IDs must be unique")
    return list(instance_ids)


def _validate_assets(profile: Mapping[str, Any], manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, Mapping) or manifest.get("schema") != HARNESS_ASSETS_SCHEMA:
        raise BenchmarkJobError("bad_harness_manifest", "SWE assets manifest is invalid")
    if manifest.get("profile_sha256") != profile["content_sha256"]:
        raise BenchmarkJobError("harness_profile_mismatch", "SWE assets do not match the profile")
    if manifest.get("suite") != "swe":
        raise BenchmarkJobError("harness_suite_mismatch", "assets were not prepared for SWE")
    assets = manifest.get("assets")
    if not isinstance(assets, Mapping):
        raise BenchmarkJobError("bad_harness_manifest", "SWE assets are absent")
    for name in profile["suites"]["swe"]["adapters"]:
        expected = profile["adapters"][name]
        observed = assets.get(name)
        if not isinstance(observed, Mapping):
            raise BenchmarkJobError("missing_harness_asset", f"SWE asset {name!r} is absent")
        if expected["kind"] in {"git", "dataset"}:
            matched = (
                observed.get("source") == expected["source"]
                and observed.get("revision") == expected["revision"]
                and observed.get("dirty") is False
            )
        else:
            matched = observed.get("image") == expected["image"]
        if not matched:
            raise BenchmarkJobError("harness_identity_mismatch", f"SWE asset {name!r} is not pinned")
    return dict(assets)


def _endpoint(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise BenchmarkJobError("bad_endpoint", "SWE endpoint must be an object")
    if set(value) - {"base_url", "model", "auth_env"}:
        raise BenchmarkJobError("bad_endpoint", "SWE endpoint has unsupported fields")
    base_url = value.get("base_url")
    model = value.get("model")
    auth_env = value.get("auth_env")
    if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
        raise BenchmarkJobError("bad_endpoint", "SWE endpoint base_url must be HTTP(S)")
    if "localhost" in base_url.lower():
        raise BenchmarkJobError("bad_endpoint", "use 127.0.0.1 instead of localhost")
    if not isinstance(model, str) or not model.strip():
        raise BenchmarkJobError("bad_endpoint", "SWE endpoint model alias is required")
    result = {"base_url": base_url.rstrip("/"), "model": model.strip()}
    if auth_env is not None:
        if not isinstance(auth_env, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", auth_env):
            raise BenchmarkJobError("bad_endpoint", "SWE endpoint auth_env is invalid")
        result["auth_env"] = auth_env
    return result


def _validate_request_controls(value: Mapping[str, Any] | None) -> dict[str, Any]:
    controls = dict(value or {})
    if set(controls) - {"thinking_mode", "reasoning_effort"}:
        raise BenchmarkJobError("bad_request_controls", "SWE request controls are invalid")
    thinking_mode = controls.get("thinking_mode", "default")
    reasoning_effort = controls.get("reasoning_effort")
    if thinking_mode not in {"default", "enabled", "disabled"}:
        raise BenchmarkJobError(
            "bad_thinking_mode", "thinking_mode must be default, enabled, or disabled"
        )
    if reasoning_effort is not None and reasoning_effort not in REASONING_EFFORT_CHOICES:
        raise BenchmarkJobError(
            "bad_reasoning_effort", "reasoning_effort is not supported by the harness"
        )
    if reasoning_effort is not None and thinking_mode != "default":
        raise BenchmarkJobError(
            "conflicting_reasoning_controls",
            "reasoning_effort and thinking_mode cannot both be explicit",
        )
    return {"thinking_mode": thinking_mode, "reasoning_effort": reasoning_effort}


def _mini_config(
    endpoint: Mapping[str, str],
    suite: Mapping[str, Any],
    request_controls: Mapping[str, Any],
    container_executable: str,
) -> str:
    # This is intentionally a small YAML overlay. It contains endpoint identity only;
    # the credential is mapped into OPENAI_API_KEY in the child process environment.
    base = json.dumps(endpoint["base_url"], ensure_ascii=True)
    model = json.dumps(f"openai/{endpoint['model']}", ensure_ascii=True)
    executable = json.dumps(container_executable, ensure_ascii=True)
    control_lines = ""
    if request_controls["reasoning_effort"] is not None:
        effort = json.dumps(request_controls["reasoning_effort"], ensure_ascii=True)
        control_lines = f"    reasoning_effort: {effort}\n"
    elif request_controls["thinking_mode"] != "default":
        enabled = "true" if request_controls["thinking_mode"] == "enabled" else "false"
        control_lines = (
            "    extra_body:\n"
            "      chat_template_kwargs:\n"
            f"        enable_thinking: {enabled}\n"
        )
    return (
        "model:\n"
        f"  model_name: {model}\n"
        "  model_class: litellm\n"
        "  cost_tracking: ignore_errors\n"
        "  model_kwargs:\n"
        "    custom_llm_provider: openai\n"
        f"    api_base: {base}\n"
        "    drop_params: true\n"
        "    parallel_tool_calls: true\n"
        f"{control_lines}"
        f"    max_tokens: {suite['max_completion_tokens']}\n"
        "agent:\n"
        f"  step_limit: {suite['max_steps']}\n"
        "environment:\n"
        f"  executable: {executable}\n"
        "  run_args:\n"
        "    - --platform\n"
        "    - linux/amd64\n"
        "    - --rm\n"
    )


def build_swe_run_plan(
    profile: Mapping[str, Any],
    assets_manifest: Mapping[str, Any],
    *,
    endpoint: Mapping[str, str],
    instance_ids: list[str],
    run_root: str,
    cache_root: str,
    ownership_id: str,
    run_id: str,
    python_executable: str = "python",
    request_controls: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic two-stage execution plan without running a model."""
    validated = validate_profile(profile)
    assets = _validate_assets(validated, assets_manifest)
    selected = validate_swe_selection(validated, instance_ids)
    target = _endpoint(endpoint)
    controls = _validate_request_controls(request_controls)
    Path(run_root).mkdir(parents=True, exist_ok=True)
    run_path = resolve_owned_run_path(run_root, ownership_id=ownership_id, run_id=run_id)
    work = resolve_owned_run_path(
        run_root, ownership_id=ownership_id, run_id=run_id, relative="work"
    )
    cache = real_path(cache_root)
    mini_root = real_path(os.path.join(cache, assets["mini-swe-agent"]["cache_key"]))
    grader_root = real_path(os.path.join(cache, assets["swe-bench"]["cache_key"]))
    if not path_is_within(mini_root, cache) or not path_is_within(grader_root, cache):
        raise BenchmarkJobError("unsafe_cache_path", "SWE adapter path escaped the cache")
    output = os.path.join(work, "mini-output")
    grader_work = os.path.join(work, "official-grader")
    config_path = os.path.join(work, "anvil-swe-config.yaml")
    predictions_jsonl = os.path.join(work, "predictions.jsonl")
    exact_filter = "^(?:" + "|".join(re.escape(item) for item in selected) + ")$"
    suite = validated["suites"]["swe"]
    mini_command = [
        python_executable,
        os.path.join(mini_root, "src", "minisweagent", "run", "benchmarks", "swebench.py"),
        "--output", output,
        "--model", f"openai/{target['model']}",
        "--subset", SWE_SUBSET,
        "--split", SWE_SPLIT,
        "--filter", exact_filter,
        "--workers", "1",
        "--environment-class", "docker",
        "--config", os.path.join(
            mini_root, "src", "minisweagent", "config", "benchmarks", "swebench.yaml"
        ),
        "--config", config_path,
    ]
    grader_command = [
        python_executable,
        "-m", "swebench.harness.run_evaluation",
        "--dataset_name", SWE_DATASET,
        "--split", SWE_SPLIT,
        "--predictions_path", predictions_jsonl,
        "--max_workers", "1",
        "--run_id", run_id,
        "--timeout", str(min(1800, suite["timeout_s"])),
        "--report_dir", grader_work,
        "--instance_ids", *selected,
    ]
    plan = {
        "schema": SWE_PLAN_SCHEMA,
        "run_id": run_id,
        "ownership_id": ownership_id,
        "profile": validated["name"],
        "profile_sha256": validated["content_sha256"],
        "dataset": SWE_DATASET,
        "split": SWE_SPLIT,
        "selection": {
            "kind": "explicit_instance_ids",
            "instance_ids": selected,
            "sha256": hashlib.sha256(canonical_json_bytes(selected)).hexdigest(),
        },
        "endpoint": target,
        "request_controls": controls,
        "harnesses": {
            "agent": {"name": "mini-swe-agent", "revision": assets["mini-swe-agent"]["revision"]},
            "grader": {"name": "swe-bench", "revision": assets["swe-bench"]["revision"]},
        },
        "paths": {
            "run": run_path,
            "work": work,
            "output": output,
            "grader_work": grader_work,
            "config": config_path,
            "predictions_jsonl": predictions_jsonl,
            "mini_root": mini_root,
            "grader_root": grader_root,
            "result": os.path.join(run_path, "swe-result.json"),
        },
        "commands": {"agent": mini_command, "grader": grader_command},
        "config_text": _mini_config(
            target, suite, controls, shutil.which("docker") or "docker"
        ),
        "timeout_s": suite["timeout_s"],
    }
    plan["plan_sha256"] = hashlib.sha256(canonical_json_bytes(plan)).hexdigest()
    return plan


def classify_swe_failure(*, stage: str, returncode: int, text: str, timed_out: bool = False) -> str:
    """Classify a failed stage without conflating model and infrastructure failures."""
    if timed_out:
        return "timeout"
    lower = text.lower()
    if stage == "grader" and any(token in lower for token in ("test timed out", "tests failed", "evaluationerror", "patch apply")):
        return "test_failure"
    if any(token in lower for token in ("no matching manifest", "image", "platform linux/amd64", "exec format error")):
        return "image_failure"
    if stage == "agent" and any(token in lower for token in (
        "authentication", "unauthorized", "forbidden", "api key", "model not found",
        "connection refused", "litellm", "rate limit", "context length",
    )):
        return "model_failure"
    if any(token in lower for token in ("docker daemon", "cannot connect to docker", "no space left", "network is unreachable")):
        return "infrastructure_failure"
    if stage == "grader":
        return "test_failure" if returncode == 0 else "infrastructure_failure"
    return "broken_harness"


def _extract_request_ids(value: Any) -> list[str]:
    found: set[str] = set()

    def walk(item: Any, parent: str = "") -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                lower = str(key).lower()
                if lower in _REQUEST_ID_KEYS and isinstance(child, str) and 0 < len(child) <= 512:
                    found.add(child)
                elif lower == "id" and parent in {"response", "raw_response"} and isinstance(child, str):
                    found.add(child[:512])
                else:
                    walk(child, lower)
        elif isinstance(item, list):
            for child in item:
                walk(child, parent)

    walk(value)
    return sorted(found)


def _trajectory_metrics(trajectory: Any) -> dict[str, Any]:
    if not isinstance(trajectory, Mapping):
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    info = trajectory.get("info") if isinstance(trajectory.get("info"), Mapping) else {}
    candidates = [
        info.get("model_stats"),
        info.get("usage"),
        trajectory.get("model_stats"),
        trajectory.get("usage"),
    ]
    usage = next((dict(item) for item in candidates if isinstance(item, Mapping)), {})

    def number(*names: str) -> int | None:
        for name in names:
            value = usage.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return None

    prompt = number("prompt_tokens", "input_tokens")
    completion = number("completion_tokens", "output_tokens")
    total = number("total_tokens")
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def _write_predictions_jsonl(path: str, predictions: Mapping[str, Any], selected: list[str]) -> None:
    lines = []
    for instance_id in selected:
        prediction = predictions[instance_id]
        lines.append(json.dumps(prediction, sort_keys=True, ensure_ascii=True, allow_nan=False))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _find_official_report(grader_work: str, run_id: str) -> str | None:
    matches = sorted(Path(grader_work).glob(f"*.{run_id}.json"))
    return str(matches[0]) if len(matches) == 1 else None


def _base_result(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": SWE_RUN_SCHEMA,
        "created_at": utc_now(),
        "run_id": plan["run_id"],
        "ownership_id": plan["ownership_id"],
        "profile": plan["profile"],
        "profile_sha256": plan["profile_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "dataset": plan["dataset"],
        "split": plan["split"],
        "selection": plan["selection"],
        "endpoint": plan["endpoint"],
        "request_controls": plan["request_controls"],
        "harnesses": plan["harnesses"],
        "state": "incomplete",
        "official_grader_complete": False,
        "instances": [],
        "stages": [],
        "summary": {"attempted": 0, "graded": 0, "resolved": 0, "resolve_rate": None},
        "failure": None,
        "promotion": {
            "authorized": False,
            "message": "Benchmark evidence does not authorize model promotion.",
        },
    }


def run_swe_benchmark(
    plan: Mapping[str, Any],
    *,
    runner: SWECommandRunner = _default_runner,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run mini-SWE-agent, normalize predictions, then require the official grader."""
    if not isinstance(plan, Mapping) or plan.get("schema") != SWE_PLAN_SCHEMA:
        raise BenchmarkJobError("bad_swe_plan", "SWE run plan is invalid")
    expected_sha = hashlib.sha256(
        canonical_json_bytes({key: value for key, value in plan.items() if key != "plan_sha256"})
    ).hexdigest()
    if plan.get("plan_sha256") != expected_sha:
        raise BenchmarkJobError("swe_plan_digest_mismatch", "SWE run plan identity changed")
    result = _base_result(plan)
    paths = plan["paths"]
    Path(paths["work"]).mkdir(parents=True, exist_ok=True)
    Path(paths["output"]).mkdir(parents=True, exist_ok=True)
    Path(paths["grader_work"]).mkdir(parents=True, exist_ok=True)
    Path(paths["config"]).write_text(plan["config_text"], encoding="utf-8", newline="\n")
    child_env = dict(os.environ if environ is None else environ)
    auth_env = plan["endpoint"].get("auth_env")
    if auth_env:
        value = child_env.get(auth_env)
        if not value:
            raise BenchmarkJobError("missing_credential", f"required credential environment {auth_env!r} is absent")
        child_env["OPENAI_API_KEY"] = value.strip()
    else:
        child_env.setdefault("OPENAI_API_KEY", "anvil-local")
    child_env["MSWEA_COST_TRACKING"] = "ignore_errors"
    pinned_python_paths = [
        os.path.join(paths["mini_root"], "src"),
        paths["grader_root"],
    ]
    if child_env.get("PYTHONPATH"):
        pinned_python_paths.append(child_env["PYTHONPATH"])
    child_env["PYTHONPATH"] = os.pathsep.join(pinned_python_paths)

    agent_started = time.monotonic()
    try:
        agent_process = runner(plan["commands"]["agent"], paths["work"], plan["timeout_s"], child_env)
    except subprocess.TimeoutExpired as exc:
        stage = _result_record(plan["commands"]["agent"], exc, time.monotonic() - agent_started)
        stage.update({"name": "agent", "status": "failed", "failure_class": "timeout"})
        result["stages"].append(stage)
        result["failure"] = {"class": "timeout", "stage": "agent"}
        atomic_write_json(paths["result"], result)
        return result
    agent_stage = _result_record(
        plan["commands"]["agent"], agent_process, time.monotonic() - agent_started
    )
    agent_stage.update({"name": "agent", "status": "completed"})
    result["stages"].append(agent_stage)
    if agent_stage["returncode"] != 0:
        failure = classify_swe_failure(
            stage="agent",
            returncode=agent_stage["returncode"],
            text=agent_stage["stdout"] + "\n" + agent_stage["stderr"],
        )
        agent_stage.update({"status": "failed", "failure_class": failure})
        result["failure"] = {"class": failure, "stage": "agent"}
        atomic_write_json(paths["result"], result)
        return result

    predictions_path = os.path.join(paths["output"], "preds.json")
    try:
        predictions = _read_json(predictions_path, code="missing_swe_predictions")
        if not isinstance(predictions, Mapping) or set(predictions) != set(plan["selection"]["instance_ids"]):
            raise BenchmarkJobError(
                "swe_selection_mismatch",
                "mini-SWE-agent predictions did not match the explicit selection",
            )
        for instance_id, prediction in predictions.items():
            if not isinstance(prediction, Mapping) or prediction.get("instance_id") != instance_id:
                raise BenchmarkJobError("bad_swe_prediction", "SWE prediction record is invalid")
    except BenchmarkJobError as exc:
        agent_stage.update({"status": "failed", "failure_class": "broken_harness"})
        result["failure"] = {"class": "broken_harness", "stage": "agent", "code": exc.code}
        atomic_write_json(paths["result"], result)
        return result

    selected = plan["selection"]["instance_ids"]
    _write_predictions_jsonl(paths["predictions_jsonl"], predictions, selected)
    for instance_id in selected:
        trajectory_path = os.path.join(
            paths["output"], instance_id, f"{instance_id}.traj.json"
        )
        trajectory = None
        trajectory_sha = None
        try:
            trajectory = _read_json(trajectory_path, code="missing_swe_trajectory")
            trajectory_sha = _sha256_file(trajectory_path)
        except BenchmarkJobError:
            pass
        info = trajectory.get("info", {}) if isinstance(trajectory, Mapping) else {}
        prediction = predictions[instance_id]
        result["instances"].append({
            "instance_id": instance_id,
            "prompt_identity": {"dataset": plan["dataset"], "split": plan["split"]},
            "trajectory": {"path": trajectory_path, "sha256": trajectory_sha},
            "request_ids": _extract_request_ids(trajectory),
            "tokens": _trajectory_metrics(trajectory),
            "duration_s": info.get("duration_s") if isinstance(info.get("duration_s"), (int, float)) else None,
            "exit_status": info.get("exit_status"),
            "prediction_sha256": hashlib.sha256(
                str(prediction.get("model_patch") or "").encode("utf-8")
            ).hexdigest(),
            "grader": {"name": "swe-bench", "revision": plan["harnesses"]["grader"]["revision"], "completed": False, "resolved": None},
            "failure_class": None if trajectory is not None else "broken_harness",
        })
    result["summary"]["attempted"] = len(result["instances"])

    grader_started = time.monotonic()
    try:
        grader_process = runner(
            plan["commands"]["grader"], paths["grader_work"], plan["timeout_s"], child_env
        )
    except subprocess.TimeoutExpired as exc:
        stage = _result_record(plan["commands"]["grader"], exc, time.monotonic() - grader_started)
        stage.update({"name": "official_grader", "status": "failed", "failure_class": "timeout"})
        result["stages"].append(stage)
        result["failure"] = {"class": "timeout", "stage": "official_grader"}
        atomic_write_json(paths["result"], result)
        return result
    grader_stage = _result_record(
        plan["commands"]["grader"], grader_process, time.monotonic() - grader_started
    )
    grader_stage.update({"name": "official_grader", "status": "completed"})
    result["stages"].append(grader_stage)
    report_path = _find_official_report(paths["grader_work"], plan["run_id"])
    if grader_stage["returncode"] != 0 or report_path is None:
        failure = classify_swe_failure(
            stage="grader",
            returncode=grader_stage["returncode"],
            text=grader_stage["stdout"] + "\n" + grader_stage["stderr"],
        )
        grader_stage.update({"status": "failed", "failure_class": failure})
        result["failure"] = {"class": failure, "stage": "official_grader"}
        atomic_write_json(paths["result"], result)
        return result
    report = _read_json(report_path, code="bad_official_grader_report")
    completed_ids = set(report.get("completed_ids", [])) if isinstance(report, Mapping) else set()
    resolved_ids = set(report.get("resolved_ids", [])) if isinstance(report, Mapping) else set()
    error_ids = set(report.get("error_ids", [])) if isinstance(report, Mapping) else set()
    if not set(selected).issubset(completed_ids) or error_ids.intersection(selected):
        grader_stage.update({"status": "failed", "failure_class": "test_failure"})
        result["failure"] = {"class": "test_failure", "stage": "official_grader"}
        atomic_write_json(paths["result"], result)
        return result
    for instance in result["instances"]:
        instance["grader"].update({
            "completed": True,
            "resolved": instance["instance_id"] in resolved_ids,
            "report_sha256": _sha256_file(report_path),
        })
    result["official_grader_complete"] = True
    result["state"] = "completed"
    result["summary"] = {
        "attempted": len(selected),
        "graded": len(selected),
        "resolved": len(resolved_ids.intersection(selected)),
        "resolve_rate": len(resolved_ids.intersection(selected)) / len(selected),
    }
    atomic_write_json(paths["result"], result)
    return result
