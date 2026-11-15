"""Pinned mini-SWE-agent execution with official SWE-bench grading."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence

from ..transports import _ProcessOutputOverflow, _ProcessPipeError, _run_bounded_process
from .artifacts import atomic_write_json, path_is_within, real_path
from .harnesses import (
    HARNESS_ASSETS_SCHEMA,
    MAX_HARNESS_OUTPUT_BYTES,
    SWE_PYTHON_ENVIRONMENT_SCHEMA,
    resolve_container_binary,
    require_swe_worker_platform,
)
from .jobs import BenchmarkJobError, canonical_json_bytes, resolve_owned_run_path, utc_now
from .request_controls import resolve_job_request_controls
from .profiles import suite_budgets, validate_profile
from .swe_grader_adapter import GRADER_REVISION, LIMITS, SOURCE_HASHES
from .swe_images import instance_image_tag, observe_paired_images, validate_paired_image_ids
from .swe_logs import validate_server_log_selector
from .swe_agent_adapter import (
    MINI_REVISION, SOURCE_HASHES as AGENT_SOURCE_HASHES, read_attempt_evidence, validate_budgets,
)


SWE_RUN_SCHEMA = "anvil-serving.swe-run/v1"
SWE_PLAN_SCHEMA = "anvil-serving.swe-plan/v1"
SWE_PLAN_SCHEMA_V2 = "anvil-serving.swe-plan/v2"
SWE_PROMPT_CONTRACTS = frozenset({"legacy/v1", "disclosed/v2"})
SWE_DATASET = "princeton-nlp/SWE-bench_Verified"
SWE_DATASET_REVISION = "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
# Git LFS content identity in this exact dataset revision, not a mutable hub head.
SWE_DATA_FILES = {
    "data/test-00000-of-00001.parquet": "a45b1fe4e2f0c8390b2b2938ac83e92ed5979000856808f3679c07812e9e6dcd",
}
SWE_SUBSET = "verified"
SWE_SPLIT = "test"
TASK_CONTAINER_NETWORK = "none"
SWE_FAILURE_CLASSES = frozenset({
    "budget_exhausted",
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


def _prompt_disclosure(suite: Mapping[str, Any]) -> str:
    if "budgets" in suite:
        budget = suite["budgets"]
        return (
            f"The selected suite shares at most {budget['max_model_calls']} model transport attempts, including retries.\n"
            f"Each model completion is capped at {budget['max_completion_tokens']} tokens.\n"
            "Submit your final answer as a patch before the shared call budget is exhausted."
        )
    return (
        f"You have at most {suite['max_steps']} model calls (one call per step).\n"
        f"Each model completion is capped at {suite['max_completion_tokens']} tokens.\n"
        "Submit your final answer as a patch before the call budget is exhausted."
    )


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


def _budget_runner(argv, cwd, timeout, env, *, max_output_bytes=MAX_HARNESS_OUTPUT_BYTES):
    try:
        return _run_bounded_process(argv, timeout=timeout, max_output_bytes=max_output_bytes,
                                    cwd=cwd, env=dict(env), owned_process_group=True)
    except (_ProcessOutputOverflow, _ProcessPipeError) as exc:
        code = "swe_process_output_overflow" if isinstance(exc, _ProcessOutputOverflow) else "swe_process_boundary_failed"
        raise BenchmarkJobError(code, "bounded SWE process did not complete safely") from exc


def _bounded_text(value: Any) -> tuple[str, bool]:
    raw = value if isinstance(value, bytes) else str(value or "").encode("utf-8")
    truncated = len(raw) > MAX_HARNESS_OUTPUT_BYTES
    return raw[:MAX_HARNESS_OUTPUT_BYTES].decode("utf-8", "replace"), truncated


def _result_record(
    argv: Sequence[str], result: Any, duration_s: float, started_at: str,
    ended_at: str | None = None,
) -> dict[str, Any]:
    stdout, stdout_truncated = _bounded_text(getattr(result, "stdout", b""))
    stderr, stderr_truncated = _bounded_text(getattr(result, "stderr", b""))
    return {
        "command": list(argv),
        "returncode": int(getattr(result, "returncode", 1)),
        "duration_s": round(duration_s, 6),
        "started_at": started_at,
        "ended_at": ended_at or utc_now(),
        "clock": {
            "wall": "UTC system clock", "wall_precision_s": 0.000001,
            "wall_resolution_s": time.get_clock_info("time").resolution,
            "duration": "monotonic", "duration_resolution_s": time.get_clock_info("monotonic").resolution,
            "synchronized": None,
        },
        "output_timing": "buffered; individual stdout/stderr event times unknown",
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


def _verify_dataset_snapshot(snapshot):
    try:
        root = Path(snapshot["root"]).resolve(strict=True)
        for relative, expected in snapshot["files"].items():
            path = root / relative
            if (path.is_symlink() or not path.resolve(strict=True).is_relative_to(root)
                    or _sha256_file(str(path)) != expected):
                raise BenchmarkJobError("swe_dataset_mismatch", "pinned SWE dataset content changed or is an LFS pointer")
        # Hugging Face discovers many data formats; permit only the pinned files.
        actual = {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*")
                  if p.is_file() or p.is_symlink()}
    except OSError as exc:
        raise BenchmarkJobError("swe_dataset_unavailable", "pinned SWE dataset files are missing or unreadable") from exc
    if actual != set(snapshot["files"]):
        raise BenchmarkJobError("swe_dataset_mismatch", "unexpected files in pinned SWE dataset")


def _dataset_snapshot(assets, cache_root):
    asset = assets["swe-bench-verified"]
    if asset["revision"] != SWE_DATASET_REVISION:
        raise BenchmarkJobError("unsupported_swe_dataset", "SWE dataset revision has no qualified content identity")
    root = real_path(os.path.join(cache_root, asset["cache_key"], "data"))
    if not path_is_within(root, cache_root):
        raise BenchmarkJobError("unsafe_cache_path", "SWE dataset escaped the harness cache")
    snapshot = {"root": root, "revision": SWE_DATASET_REVISION,
                "files": {path.removeprefix("data/"): digest for path, digest in SWE_DATA_FILES.items()}}
    _verify_dataset_snapshot(snapshot)
    return snapshot


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


def _python_environment(manifest: Mapping[str, Any], *, cache_root: str) -> tuple[str, dict[str, Any]]:
    value = manifest.get("python_environment")
    if (
        not isinstance(value, Mapping)
        or value.get("schema") != SWE_PYTHON_ENVIRONMENT_SCHEMA
        or not isinstance(value.get("cache_key"), str)
        or not isinstance(value.get("executable"), str)
        or not isinstance(value.get("resolved_packages"), list)
        or not isinstance(value.get("resolved_packages_sha256"), str)
    ):
        raise BenchmarkJobError(
            "missing_harness_environment",
            "SWE assets do not include an isolated Python environment",
        )
    packages = value["resolved_packages"]
    if hashlib.sha256(canonical_json_bytes(packages)).hexdigest() != value[
        "resolved_packages_sha256"
    ]:
        raise BenchmarkJobError(
            "harness_environment_mismatch", "SWE Python package inventory is invalid"
        )
    cache = real_path(cache_root)
    environment_root = real_path(os.path.join(cache, value["cache_key"]))
    expected_executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    relative_executable = value["executable"].replace("\\", "/")
    executable = os.path.abspath(os.path.join(environment_root, relative_executable))
    same_interpreter = True
    if os.name != "nt":
        try:
            same_interpreter = os.path.samefile(executable, sys.executable)
        except OSError:
            same_interpreter = False
    if (
        not path_is_within(environment_root, cache)
        or relative_executable != expected_executable
        or os.path.commonpath((environment_root, executable)) != environment_root
        or not os.path.isfile(executable)
        or not same_interpreter
    ):
        raise BenchmarkJobError(
            "unsafe_cache_path", "SWE Python executable escaped the harness cache"
        )
    return executable, dict(value)


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
    result = resolve_job_request_controls(value)
    if "force_nonempty_content" not in dict(value or {}):
        result.pop("chat_template_kwargs")
    return result


def _mini_config(
    endpoint: Mapping[str, str],
    suite: Mapping[str, Any],
    request_controls: Mapping[str, Any],
    container_executable: str,
    paired_images: bool = False,
    prompt_contract: str = "legacy/v1",
) -> str:
    # This is intentionally a small YAML overlay. It contains endpoint identity only;
    # mini-SWE-agent's model client runs in the host process. Its Docker environment
    # receives only task commands, so its network is explicitly disabled here.
    base = json.dumps(endpoint["base_url"], ensure_ascii=True)
    model = json.dumps(f"openai/{endpoint['model']}", ensure_ascii=True)
    executable = json.dumps(container_executable, ensure_ascii=True)
    control_lines = ""
    if request_controls["reasoning_effort"] is not None:
        effort = json.dumps(request_controls["reasoning_effort"], ensure_ascii=True)
        control_lines = (
            "    extra_body:\n"
            f"      reasoning_effort: {effort}\n"
        )
    elif request_controls["thinking_mode"] != "default":
        enabled = "true" if request_controls["thinking_mode"] == "enabled" else "false"
        control_lines = (
            "    extra_body:\n"
            "      chat_template_kwargs:\n"
            f"        enable_thinking: {enabled}\n"
        )
        if "force_nonempty_content" in request_controls:
            force_nonempty = "true" if request_controls["force_nonempty_content"] else "false"
            control_lines += f"        force_nonempty_content: {force_nonempty}\n"
    sampling_lines = ""
    sampling = request_controls.get("sampling")
    if sampling is not None:
        temperature = json.dumps(
            sampling["temperature"]["effective_request"], ensure_ascii=True
        )
        sampling_lines = f"    temperature: {temperature}\n"
        if sampling["top_p"]["sent"]:
            top_p = json.dumps(
                sampling["top_p"]["effective_request"], ensure_ascii=True
            )
            sampling_lines += f"    top_p: {top_p}\n"
    budget_comment = ""
    completion_cap = suite["max_completion_tokens"]
    if "budgets" in suite:
        configured = validate_budgets(suite["budgets"])
        completion_cap = configured["max_completion_tokens"]
        budget_comment = "# suite_budgets: " + json.dumps(configured, sort_keys=True, separators=(",", ":")) + "\n"
    return (
        f"{budget_comment}"
        "model:\n"
        f"  model_name: {model}\n"
        "  model_class: litellm\n"
        "  cost_tracking: ignore_errors\n"
        "  model_kwargs:\n"
        "    custom_llm_provider: openai\n"
        f"    api_base: {base}\n"
        "    drop_params: true\n"
        "    parallel_tool_calls: true\n"
        f"{sampling_lines}"
        f"{control_lines}"
        f"    max_tokens: {completion_cap}\n"
        "agent:\n"
        f"  step_limit: {suite['max_steps']}\n"
        "environment:\n"
        f"  executable: {executable}\n"
        "  run_args:\n"
        "    - --platform\n"
        "    - linux/amd64\n"
        "    - --network\n"
        f"    - {TASK_CONTAINER_NETWORK}\n"
        "    - --rm\n"
        "    - --memory\n"
        "    - 8g\n"
        "    - --memory-swap\n"
        "    - 8g\n"
        "    - --cpus\n"
        "    - '4'\n"
        "    - --pids-limit\n"
        "    - '512'\n"
        + ("    - --pull\n    - never\n" if paired_images else "")
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
    request_controls: Mapping[str, Any] | None = None,
    paired_image_ids: Mapping[str, str] | None = None,
    server_log: Mapping[str, Any] | None = None,
    prompt_contract: str = "legacy/v1",
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Build a deterministic two-stage execution plan without running a model."""
    require_swe_worker_platform()
    validated = validate_profile(profile)
    budgets = suite_budgets(validated, "swe")
    if budgets is not None:
        budgets = validate_budgets(budgets)
        if timeout_s is not None and (type(timeout_s) not in {int, float} or not 0 < timeout_s <= budgets["deadline_s"]):
            raise BenchmarkJobError("bad_swe_budget", "SWE timeout must fit the admitted deadline")
    assets = _validate_assets(validated, assets_manifest)
    selected = validate_swe_selection(validated, instance_ids)
    paired_images = validate_paired_image_ids(paired_image_ids, selected)
    target = _endpoint(endpoint)
    controls = _validate_request_controls(request_controls)
    if prompt_contract not in SWE_PROMPT_CONTRACTS:
        raise BenchmarkJobError("bad_swe_prompt_contract", "SWE prompt contract is unsupported")
    log_target = validate_server_log_selector(server_log) if server_log is not None else None
    Path(run_root).mkdir(parents=True, exist_ok=True)
    run_path = resolve_owned_run_path(run_root, ownership_id=ownership_id, run_id=run_id)
    work = resolve_owned_run_path(
        run_root, ownership_id=ownership_id, run_id=run_id, relative="work"
    )
    cache = real_path(cache_root)
    dataset_snapshot = _dataset_snapshot(assets, cache)
    python_executable, python_environment = _python_environment(
        assets_manifest, cache_root=cache
    )
    mini_root = real_path(os.path.join(cache, assets["mini-swe-agent"]["cache_key"]))
    grader_root = real_path(os.path.join(cache, assets["swe-bench"]["cache_key"]))
    if not path_is_within(mini_root, cache) or not path_is_within(grader_root, cache):
        raise BenchmarkJobError("unsafe_cache_path", "SWE adapter path escaped the cache")
    output = os.path.join(work, "mini-output")
    grader_work = os.path.join(work, "official-grader")
    config_path = os.path.join(work, "anvil-swe-config.yaml")
    mini_config_home = os.path.join(work, "mini-config")
    predictions_jsonl = os.path.join(work, "predictions.jsonl")
    exact_filter = "^(?:" + "|".join(re.escape(item) for item in selected) + ")$"
    suite = validated["suites"]["swe"]
    if assets["swe-bench"]["revision"] != GRADER_REVISION:
        raise BenchmarkJobError("unsupported_grader_containment", "grader containment requires its qualified revision")
    if assets["mini-swe-agent"]["revision"] != MINI_REVISION:
        raise BenchmarkJobError("unsupported_agent_observation", "agent observation requires its qualified revision")
    agent_adapter_path = str(Path(__file__).with_name("swe_agent_adapter.py"))
    agent_adapter_sha256 = _sha256_file(agent_adapter_path)
    # Keep flushed attempt evidence outside disposable work, including cancellation.
    request_events_path = os.path.join(run_path, "swe-request-events.jsonl")
    adapter_path = str(Path(__file__).with_name("swe_grader_adapter.py"))
    adapter_sha256 = _sha256_file(adapter_path)
    containment_receipt = os.path.join(grader_work, "containment.json")
    image_policy_path = os.path.join(work, "paired-image-ids.json")
    image_policy_sha256 = (hashlib.sha256(canonical_json_bytes(
        {instance_image_tag(k): v for k, v in paired_images.items()}
    )).hexdigest() if paired_images is not None else None)
    container_executable = resolve_container_binary() or "docker"
    mini_command = [
        python_executable,
        agent_adapter_path,
        "--mini-root", mini_root,
        "--adapter-sha256", agent_adapter_sha256,
        "--events", request_events_path, "--",
        "--output", output,
        "--model", f"openai/{target['model']}",
        "--subset", dataset_snapshot["root"],
        "--split", SWE_SPLIT,
        "--filter", exact_filter,
        "--workers", "1",
        "--environment-class", "docker",
        "--config", os.path.join(
            mini_root, "src", "minisweagent", "config", "benchmarks", "swebench.yaml"
        ),
        "--config", config_path,
    ]
    if prompt_contract == "disclosed/v2":
        mini_command[8:8] = ["--prompt-disclosure", _prompt_disclosure(suite)]
    if budgets is not None:
        mini_command[8:8] = ["--budgets", json.dumps(budgets, sort_keys=True, separators=(",", ":"))]
    grader_command = [
        python_executable,
        adapter_path, "--grader-root", grader_root,
        "--adapter-sha256", adapter_sha256,
        "--receipt", containment_receipt,
        *(["--image-identities", image_policy_path,
           "--image-identities-sha256", image_policy_sha256] if paired_images is not None else []), "--",
        "--dataset_name", dataset_snapshot["root"],
        "--split", SWE_SPLIT,
        "--predictions_path", predictions_jsonl,
        "--max_workers", "1",
        "--namespace", "swebench",
        "--run_id", run_id,
        "--timeout", str(min(1800, suite["timeout_s"])),
        "--report_dir", grader_work,
        "--instance_ids", *selected,
    ]
    plan = {
        "schema": SWE_PLAN_SCHEMA if budgets is None else SWE_PLAN_SCHEMA_V2,
        "run_id": run_id,
        "ownership_id": ownership_id,
        "profile": validated["name"],
        "profile_sha256": validated["content_sha256"],
        "dataset": SWE_DATASET,
        "dataset_snapshot": dataset_snapshot,
        "split": SWE_SPLIT,
        "selection": {
            "kind": "explicit_instance_ids",
            "instance_ids": selected,
            "sha256": hashlib.sha256(canonical_json_bytes(selected)).hexdigest(),
        },
        "endpoint": target,
        "task_container_network": TASK_CONTAINER_NETWORK,
        "container_limits": dict(LIMITS),
        "paired_image_ids": paired_images,
        "container_executable": container_executable,
        "request_controls": controls,
        "prompt_contract": prompt_contract,
        "server_log": log_target,
        "harnesses": {
            "agent": {"name": "mini-swe-agent", "revision": assets["mini-swe-agent"]["revision"]},
            "agent_observation": {
                "adapter_sha256": agent_adapter_sha256,
                "source_hashes": dict(AGENT_SOURCE_HASHES),
            },
            "grader": {"name": "swe-bench", "revision": assets["swe-bench"]["revision"]},
            "grader_containment": {
                "adapter_sha256": adapter_sha256,
                "source_hashes": dict(SOURCE_HASHES),
                "receipt": containment_receipt,
            },
            "python_environment": {
                "schema": python_environment["schema"],
                "python": python_environment["python"],
                "platform": python_environment["platform"],
                "architecture": python_environment["architecture"],
                "resolved_packages_sha256": python_environment["resolved_packages_sha256"],
            },
        },
        "paths": {
            "run": run_path,
            "work": work,
            "output": output,
            "grader_work": grader_work,
            "config": config_path,
            "mini_config_home": mini_config_home,
            "predictions_jsonl": predictions_jsonl,
            "mini_root": mini_root,
            "grader_root": grader_root,
            "result": os.path.join(run_path, "swe-result.json"),
            "image_policy": image_policy_path,
            "request_events": request_events_path,
            "plan": os.path.join(work, "swe-plan.json"),
        },
        "commands": {"agent": mini_command, "grader": grader_command},
        "config_text": _mini_config(
            target, suite, controls, container_executable, paired_images=paired_images is not None,
            prompt_contract=prompt_contract,
        ),
        "timeout_s": suite["timeout_s"],
    }
    if budgets is not None:
        plan["budgets"] = budgets
        plan["timeout_s"] = min(suite["timeout_s"], budgets["deadline_s"], timeout_s or budgets["deadline_s"])
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


def _nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _retained_response_records(trajectory: Any) -> list[Mapping[str, Any]] | None:
    """Return one complete, unique retained response per assistant turn."""
    if not isinstance(trajectory, Mapping) or not isinstance(trajectory.get("messages"), list):
        return None
    info = trajectory.get("info") if isinstance(trajectory.get("info"), Mapping) else {}
    stats = info.get("model_stats") if isinstance(info.get("model_stats"), Mapping) else {}
    api_calls = _nonnegative_int(stats.get("api_calls"))
    responses: list[Mapping[str, Any]] = []
    response_ids: set[str] = set()
    for message in trajectory["messages"]:
        if not isinstance(message, Mapping):
            return None
        extra = message.get("extra")
        response = extra.get("response") if isinstance(extra, Mapping) else None
        if not isinstance(response, Mapping):
            if message.get("role") == "assistant":
                return None
            continue
        if message.get("role") not in {None, "assistant"}:
            return None
        response_id = response.get("id")
        if isinstance(response_id, str) and response_id:
            if response_id in response_ids:
                return None
            response_ids.add(response_id)
        responses.append(response)
    if api_calls is not None and api_calls != len(responses):
        return None
    return responses


def _trajectory_metrics(trajectory: Any) -> dict[str, Any]:
    if not isinstance(trajectory, Mapping):
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    info = trajectory.get("info") if isinstance(trajectory.get("info"), Mapping) else {}
    def number(value: Mapping[str, Any], *names: str) -> int | None:
        for name in names:
            item = _nonnegative_int(value.get(name))
            if item is not None:
                return item
        return None

    def usage_metrics(value: Mapping[str, Any]) -> dict[str, int | None]:
        prompt = number(value, "prompt_tokens", "input_tokens")
        completion = number(value, "completion_tokens", "output_tokens")
        total = number(value, "total_tokens")
        if total is None and prompt is not None and completion is not None:
            total = prompt + completion
        return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}

    for usage in (
        info.get("model_stats"),
        info.get("usage"),
        trajectory.get("model_stats"),
        trajectory.get("usage"),
    ):
        if not isinstance(usage, Mapping):
            continue
        observed = usage_metrics(usage)
        if any(value is not None for value in observed.values()):
            return observed

    # Per-response usage is an alternative to aggregate model stats, never added to it.
    responses = _retained_response_records(trajectory)
    if not responses or any(not isinstance(response.get("usage"), Mapping) for response in responses):
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    response_usages = [usage_metrics(response["usage"]) for response in responses]

    def summed(field: str) -> int | None:
        values = [item[field] for item in response_usages]
        return sum(values) if all(value is not None for value in values) else None

    prompt = summed("prompt_tokens")
    completion = summed("completion_tokens")
    total = summed("total_tokens")
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def _response_finish_reasons(trajectory: Any) -> list[str | None]:
    responses = _retained_response_records(trajectory)
    if responses is None:
        return []
    reasons = []
    for response in responses:
        choices = response.get("choices")
        if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], Mapping):
            reason = choices[0].get("finish_reason")
            reasons.append(reason if isinstance(reason, str) else None)
        elif "choices" not in response and isinstance(response.get("finish_reason"), str):
            reasons.append(response["finish_reason"])
        else:
            # A multi-choice or malformed response cannot establish one turn's
            # finish reason for a serial format-error diagnosis.
            reasons.append(None)
    return reasons


def _swe_diagnostics(trajectory: Any, prediction: Mapping[str, Any]) -> dict[str, Any]:
    """Additive observations; they neither alter patches nor grading decisions."""
    info = trajectory.get("info", {}) if isinstance(trajectory, Mapping) else {}
    exit_status = info.get("exit_status") if isinstance(info.get("exit_status"), str) else None
    patch = prediction.get("model_patch")
    patch_bytes = patch.encode("utf-8") if isinstance(patch, str) else b""
    exit_kind = {
        "Submitted": "submitted",
        "LimitsExceeded": "limits_exceeded",
        "RepeatedFormatError": "repeated_format_error",
    }.get(exit_status, "missing" if trajectory is None else "other")
    observation = {
        "exit": {"status": exit_status, "kind": exit_kind},
        "prediction": {
            "present": isinstance(patch, str),
            "nonempty": bool(patch_bytes),
            "sha256": hashlib.sha256(patch_bytes).hexdigest(),
            "bytes": len(patch_bytes),
        },
        "grading": {"status": "ungraded"},
    }
    if exit_kind == "limits_exceeded":
        # LimitsExceeded can mean call, cost, or wall-clock budget.  The stored
        # status alone does not identify which configured budget was reached.
        observation["exit"]["limit_kind"] = "unknown"
    elif exit_kind == "repeated_format_error":
        reasons = _response_finish_reasons(trajectory)
        if len(reasons) >= 3 and reasons[-3:] == ["length", "length", "length"]:
            observation["exit"]["format_cause"] = "output_cap"
        else:
            observation["exit"]["format_cause"] = "unknown"
    return observation


def _set_grading_observation(instance: dict[str, Any]) -> None:
    grader = instance["grader"]
    instance["diagnostics"]["grading"]["status"] = (
        "ungraded" if not grader["completed"] else "resolved" if grader["resolved"] else "unresolved"
    )


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
        **({"budgets": plan["budgets"]} if "budgets" in plan else {}),
        "schema": SWE_RUN_SCHEMA,
        "created_at": utc_now(),
        "run_id": plan["run_id"],
        "ownership_id": plan["ownership_id"],
        "profile": plan["profile"],
        "profile_sha256": plan["profile_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "dataset": plan["dataset"],
        "dataset_snapshot": plan["dataset_snapshot"],
        "split": plan["split"],
        "selection": plan["selection"],
        "endpoint": plan["endpoint"],
        "task_container_network": plan["task_container_network"],
        "container_limits": plan["container_limits"],
        "paired_image_ids": plan.get("paired_image_ids"),
        "image_observations": [],
        "request_controls": plan["request_controls"],
        "prompt_contract": plan.get("prompt_contract", "legacy/v1"),
        "harnesses": plan["harnesses"],
        "state": "incomplete",
        "official_grader_complete": False,
        "instances": [],
        "stages": [],
        "summary": {"attempted": 0, "graded": 0, "resolved": 0, "resolve_rate": None},
        "failure": None,
        "server_log_evidence": {"status": "not_requested" if plan.get("server_log") is None else "not_collected"},
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
    log_collector: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run mini-SWE-agent, normalize predictions, then require the official grader."""
    require_swe_worker_platform()
    if not isinstance(plan, Mapping) or plan.get("schema") not in {SWE_PLAN_SCHEMA, SWE_PLAN_SCHEMA_V2}:
        raise BenchmarkJobError("bad_swe_plan", "SWE run plan is invalid")
    expected_sha = hashlib.sha256(
        canonical_json_bytes({key: value for key, value in plan.items() if key != "plan_sha256"})
    ).hexdigest()
    if plan.get("plan_sha256") != expected_sha:
        raise BenchmarkJobError("swe_plan_digest_mismatch", "SWE run plan identity changed")
    budgets = validate_budgets(plan.get("budgets")) if plan["schema"] == SWE_PLAN_SCHEMA_V2 else None
    if budgets is not None and runner is _default_runner:
        runner = _budget_runner
    if budgets is not None and (type(plan["timeout_s"]) not in {int, float} or not 0 < plan["timeout_s"] <= budgets["deadline_s"]):
        raise BenchmarkJobError("bad_swe_budget", "SWE timeout must fit the admitted deadline")
    _verify_dataset_snapshot(plan["dataset_snapshot"])
    paired_images = validate_paired_image_ids(plan.get("paired_image_ids"), plan["selection"]["instance_ids"])
    result = _base_result(plan)
    paths = plan["paths"]
    Path(paths["work"]).mkdir(parents=True, exist_ok=True)
    Path(paths["output"]).mkdir(parents=True, exist_ok=True)
    Path(paths["grader_work"]).mkdir(parents=True, exist_ok=True)
    Path(paths["mini_config_home"]).mkdir(parents=True, exist_ok=True)
    suite_started = time.monotonic()

    def budget_boundary(stage):
        if budgets is None:
            return plan["timeout_s"]
        remaining = plan["timeout_s"] - (time.monotonic() - suite_started)
        reason = ("deadline_s" if remaining <= 0 else "min_free_disk_bytes"
                  if shutil.disk_usage(paths["work"]).free < budgets["min_free_disk_bytes"] else None)
        if reason is not None:
            result["failure"] = {"class": "budget_exhausted", "stage": stage, "code": reason}
            atomic_write_json(paths["result"], result)
            return None
        return remaining
    atomic_write_json(paths["plan"], dict(plan))
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
    child_env["MSWEA_GLOBAL_CONFIG_DIR"] = paths["mini_config_home"]
    child_env["MSWEA_SILENT_STARTUP"] = "1"
    pinned_python_paths = [
        os.path.join(paths["mini_root"], "src"),
        paths["grader_root"],
    ]
    if child_env.get("PYTHONPATH"):
        pinned_python_paths.append(child_env["PYTHONPATH"])
    child_env["PYTHONPATH"] = os.pathsep.join(pinned_python_paths)

    def image_boundary(stage):
        if paired_images is None:
            return True
        try:
            rows = observe_paired_images(paired_images, executable=plan["container_executable"],
                                         runner=runner, cwd=paths["work"], env=child_env)
        except BenchmarkJobError as exc:
            result["failure"] = {"class": "image_failure", "stage": stage, "code": exc.code}
            atomic_write_json(paths["result"], result)
            return False
        result["image_observations"].append({"stage": stage, "images": rows})
        atomic_write_json(paths["result"], result)
        return True

    if not image_boundary("before_agent"):
        return result
    if paired_images is not None:
        atomic_write_json(paths["image_policy"], {instance_image_tag(k): v for k, v in paired_images.items()})

    agent_started_at = utc_now()
    agent_started = time.monotonic()
    def collect_logs(ended_at):
        if log_collector is None:
            return
        try:
            result["server_log_evidence"] = log_collector(agent_started_at, ended_at)
        except Exception as exc:
            # Evidence failures must not mask the agent's result or copy error bodies.
            result["server_log_evidence"] = {
                "status": "unavailable", "error": type(exc).__name__,
                "requested_interval": {"started_at": agent_started_at, "ended_at": ended_at},
            }
    try:
        try:
            allowance = budget_boundary("agent")
            if allowance is None:
                return result
            agent_process = runner(plan["commands"]["agent"], paths["work"], allowance, child_env)
        finally:
            agent_duration = time.monotonic() - agent_started
            agent_ended_at = utc_now()
            result["agent_interval"] = {
                "started_at": agent_started_at, "ended_at": agent_ended_at,
                "duration_s": round(agent_duration, 6),
            }
            collect_logs(agent_ended_at)
            if paths.get("request_events"):
                try:
                    result["request_observations"] = read_attempt_evidence(
                        paths["request_events"],
                        expected_adapter_sha256=plan["harnesses"]["agent_observation"]["adapter_sha256"],
                    )
                except Exception as exc:
                    result["request_observations"] = {
                        "status": "unavailable", "error": type(exc).__name__, "events": [],
                    }
            atomic_write_json(paths["result"], result)
    except subprocess.TimeoutExpired as exc:
        stage = _result_record(plan["commands"]["agent"], exc, agent_duration, agent_started_at, agent_ended_at)
        failure = "timeout" if budgets is None else "budget_exhausted"
        stage.update({"name": "agent", "status": "failed", "failure_class": failure})
        result["stages"].append(stage)
        result["failure"] = {"class": failure, "stage": "agent", **({"code": "deadline_s"} if budgets is not None else {})}
        atomic_write_json(paths["result"], result)
        return result
    agent_stage = _result_record(
        plan["commands"]["agent"], agent_process, agent_duration, agent_started_at, agent_ended_at
    )
    agent_stage.update({"name": "agent", "status": "completed"})
    result["stages"].append(agent_stage)
    if budgets is not None:
        observations = result.get("request_observations", {})
        exhausted = next((event for event in observations.get("events", []) if event["event"] == "budget_exhausted"), None)
        budget_evidence = observations.get("budget", {})
        if exhausted is not None or budget_evidence.get("exhausted"):
            agent_stage.update({"status": "failed", "failure_class": "budget_exhausted"})
            result["failure"] = {"class": "budget_exhausted", "stage": "agent",
                                 "code": exhausted["reason"] if exhausted else budget_evidence["reason"]}
            atomic_write_json(paths["result"], result)
            return result
        if budget_boundary("agent") is None:
            return result
        if observations.get("status") != "complete" or budget_evidence.get("configured") != budgets:
            agent_stage.update({"status": "failed", "failure_class": "broken_harness"})
            result["failure"] = {"class": "broken_harness", "stage": "agent", "code": "missing_swe_budget_evidence"}
            atomic_write_json(paths["result"], result)
            return result
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
            "request_observation_sequences": [
                event["seq"] for event in result.get("request_observations", {}).get("events", [])
                if event.get("task_instance_id") == instance_id
            ],
            "tokens": _trajectory_metrics(trajectory),
            "duration_s": info.get("duration_s") if isinstance(info.get("duration_s"), (int, float)) else None,
            "exit_status": info.get("exit_status"),
            "prediction_sha256": hashlib.sha256(
                str(prediction.get("model_patch") or "").encode("utf-8")
            ).hexdigest(),
            "diagnostics": _swe_diagnostics(trajectory, prediction),
            "grader": {"name": "swe-bench", "revision": plan["harnesses"]["grader"]["revision"], "completed": False, "resolved": None},
            "failure_class": (
                "broken_harness" if trajectory is None else
                "budget_exhausted" if info.get("exit_status") == "LimitsExceeded" else None
            ),
        })
    result["summary"]["attempted"] = len(result["instances"])
    agent_failure = None
    if any(instance["failure_class"] == "broken_harness" for instance in result["instances"]):
        failure = classify_swe_failure(
            stage="agent",
            returncode=1,
            text=agent_stage["stdout"] + "\n" + agent_stage["stderr"],
        )
        agent_stage.update({"status": "failed", "failure_class": failure})
        agent_failure = {
            "class": failure,
            "stage": "agent",
            "code": "missing_swe_trajectory",
        }
    elif any(instance["failure_class"] == "budget_exhausted" for instance in result["instances"]):
        agent_stage.update({"status": "failed", "failure_class": "budget_exhausted"})
        agent_failure = {
            "class": "budget_exhausted",
            "stage": "agent",
            "code": "swe_limits_exceeded",
        }

    if not image_boundary("before_grader"):
        return result
    try:
        _verify_dataset_snapshot(plan["dataset_snapshot"])
    except BenchmarkJobError as exc:
        result["failure"] = {"class": "broken_harness", "stage": "official_grader", "code": exc.code}
        atomic_write_json(paths["result"], result)
        return result
    grader_started_at = utc_now()
    grader_started = time.monotonic()
    try:
        allowance = budget_boundary("official_grader")
        if allowance is None:
            return result
        grader_process = runner(
            plan["commands"]["grader"], paths["grader_work"], allowance, child_env
        )
    except subprocess.TimeoutExpired as exc:
        stage = _result_record(plan["commands"]["grader"], exc, time.monotonic() - grader_started, grader_started_at)
        failure = "timeout" if budgets is None else "budget_exhausted"
        stage.update({"name": "official_grader", "status": "failed", "failure_class": failure})
        result["stages"].append(stage)
        result["failure"] = agent_failure or {
            "class": failure,
            "stage": "official_grader",
            **({"code": "deadline_s"} if budgets is not None else {}),
        }
        atomic_write_json(paths["result"], result)
        return result
    grader_stage = _result_record(
        plan["commands"]["grader"], grader_process, time.monotonic() - grader_started, grader_started_at
    )
    grader_stage.update({"name": "official_grader", "status": "completed"})
    result["stages"].append(grader_stage)
    if budget_boundary("official_grader") is None:
        return result
    if not image_boundary("after_grader"):
        return result
    report_path = _find_official_report(paths["grader_work"], plan["run_id"])
    if grader_stage["returncode"] != 0 or report_path is None:
        failure = classify_swe_failure(
            stage="grader",
            returncode=grader_stage["returncode"],
            text=grader_stage["stdout"] + "\n" + grader_stage["stderr"],
        )
        grader_stage.update({"status": "failed", "failure_class": failure})
        result["failure"] = agent_failure or {
            "class": failure,
            "stage": "official_grader",
        }
        atomic_write_json(paths["result"], result)
        return result
    report = _read_json(report_path, code="bad_official_grader_report")
    completed_ids = set(report.get("completed_ids", [])) if isinstance(report, Mapping) else set()
    resolved_ids = set(report.get("resolved_ids", [])) if isinstance(report, Mapping) else set()
    error_ids = set(report.get("error_ids", [])) if isinstance(report, Mapping) else set()
    valid_completed_ids = completed_ids.intersection(selected) - error_ids
    selected_resolved_ids = resolved_ids.intersection(valid_completed_ids)
    report_sha256 = _sha256_file(report_path)
    for instance in result["instances"]:
        instance_id = instance["instance_id"]
        instance["grader"].update({
            "completed": instance_id in valid_completed_ids,
            "resolved": instance_id in selected_resolved_ids
            if instance_id in valid_completed_ids
            else None,
            "report_sha256": report_sha256,
        })
        _set_grading_observation(instance)
    result["official_grader_complete"] = len(valid_completed_ids) == len(selected)
    result["summary"] = {
        "attempted": len(selected),
        "graded": len(valid_completed_ids),
        "resolved": len(selected_resolved_ids),
        "resolve_rate": (
            len(selected_resolved_ids) / len(valid_completed_ids)
            if valid_completed_ids
            else None
        ),
    }
    if not result["official_grader_complete"]:
        grader_stage.update({"status": "failed", "failure_class": "test_failure"})
        result["failure"] = agent_failure or {
            "class": "test_failure",
            "stage": "official_grader",
        }
        atomic_write_json(paths["result"], result)
        return result
    if agent_failure:
        result["failure"] = agent_failure
        atomic_write_json(paths["result"], result)
        return result
    result["state"] = "completed"
    atomic_write_json(paths["result"], result)
    return result
