"""Pinned Aider Polyglot planning and retained, independently graded results."""
from __future__ import annotations

from functools import partial
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Mapping
from urllib.parse import urlsplit

from .aider_prepare import AIDER_PLAN_SCHEMA as AIDER_PLAN_SCHEMA, _adapter_command
from .aider_containment import AIDER_REVISION, POLYGLOT_REVISION, SOURCE_HASHES, task_snapshot, validate_task_id
from .artifacts import atomic_write_json, evidence_file_reference, path_is_within, real_path
from .harnesses import HARNESS_ASSETS_SCHEMA, resolve_container_binary
from .jobs import BenchmarkJobError, canonical_json_bytes, resolve_owned_run_path, utc_now
from .profiles import suite_budgets, validate_profile
from .request_controls import resolve_job_request_controls
from .swe import _budget_runner, _endpoint, _result_record
from .swe_agent_adapter import validate_budgets

AIDER_RUN_SCHEMA = "anvil-serving.aider-run/v1"
# Aider prints reasoning and repeated edits; retained stage output stays 64 KiB per stream.
MAX_AIDER_PROCESS_OUTPUT_BYTES = 8 * 1024 * 1024


def _digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def validate_aider_parameters(profile, parameters):
    validated = validate_profile(profile)
    if "aider" not in validated["suites"] or not isinstance(parameters, Mapping):
        raise BenchmarkJobError("bad_aider_parameters", "Aider parameters require an Aider suite")
    if set(parameters) - {"instance_ids", "thinking_mode", "reasoning_effort", "temperature", "top_p"}:
        raise BenchmarkJobError("bad_aider_parameters", "unsupported Aider parameters")
    ids = parameters.get("instance_ids")
    if ids is not None:
        try:
            if not isinstance(ids, list) or not ids or len(ids) > validated["suites"]["aider"]["instance_limit"]:
                raise ValueError
            ids = [validate_task_id(item) for item in ids]
            if len(set(ids)) != len(ids):
                raise ValueError
        except ValueError as exc:
            raise BenchmarkJobError("bad_aider_selection", "invalid Aider task selection") from exc
    return {"selection": {"instance_ids": ids}, "request_controls": resolve_job_request_controls(
        {key: value for key, value in parameters.items() if key != "instance_ids"})}


def _source_root(cache, asset, revision):
    if asset.get("revision") != revision or asset.get("dirty") is not False:
        raise BenchmarkJobError("aider_source_mismatch", "Aider source identity is not pinned")
    root = real_path(os.path.join(cache, asset["cache_key"]))
    if not path_is_within(root, cache) or root == cache:
        raise BenchmarkJobError("unsafe_cache_path", "Aider source escaped its cache")
    return root


def build_aider_run_plan(profile, assets_manifest, *, endpoint, run_root, cache_root,
                        ownership_id, run_id, instance_ids=None, request_controls=None, timeout_s=None):
    from .aider_prepare import validate_aider_environment, verify_aider_python_environment

    profile = validate_profile(profile)
    selected = validate_aider_parameters(profile, {"instance_ids": instance_ids})["selection"]["instance_ids"]
    if (assets_manifest.get("schema") != HARNESS_ASSETS_SCHEMA
            or assets_manifest.get("profile_sha256") != profile["content_sha256"]
            or assets_manifest.get("suite") != "aider"):
        raise BenchmarkJobError("bad_harness_manifest", "Aider assets do not match this profile and suite")
    assets = assets_manifest["assets"]
    for name in ("aider", "polyglot-benchmark"):
        if assets[name].get("source") != profile["adapters"][name]["source"]:
            raise BenchmarkJobError("aider_source_mismatch", "Aider asset source changed")
    cache = real_path(cache_root)
    aider_root = _source_root(cache, assets["aider"], AIDER_REVISION)
    polyglot_root = _source_root(cache, assets["polyglot-benchmark"], POLYGLOT_REVISION)
    environment = validate_aider_environment(assets_manifest.get("aider_environment"), cache_root=cache, assets=assets)
    python_env = assets_manifest.get("python_environment", {})
    python = verify_aider_python_environment(python_env, cache_root=cache)
    available = {row["task_id"]: row for row in environment["selection"]}
    selected = selected or sorted(available)[:profile["suites"]["aider"]["instance_limit"]]
    if not selected or set(selected) - set(available):
        raise BenchmarkJobError("unprepared_aider_selection", "Aider selection lacks prepared dependency inventory")
    snapshots = {}
    for task_id in selected:
        snapshots[task_id] = task_snapshot(polyglot_root, task_id)
        if snapshots[task_id] != available[task_id]["files"]:
            raise BenchmarkJobError("aider_task_mismatch", "prepared Aider task source changed")
    budgets = suite_budgets(profile, "aider")
    if budgets is None:
        raise BenchmarkJobError("missing_aider_budgets", "Aider requires explicit v2 suite budgets")
    budgets = validate_budgets(budgets)
    timeout = budgets["deadline_s"] if timeout_s is None else timeout_s
    if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= budgets["deadline_s"]:
        raise BenchmarkJobError("bad_aider_timeout", "Aider timeout must fit its admitted deadline")
    target = _endpoint(endpoint)
    url = urlsplit(target["base_url"])
    if url.username or url.password or url.query or url.fragment or not url.hostname:
        raise BenchmarkJobError("bad_endpoint", "Aider endpoint must not contain credentials or query parameters")
    run_path = resolve_owned_run_path(run_root, ownership_id=ownership_id, run_id=run_id)
    base = str(Path(run_path) / "aider")
    paths = {"aider": base, "original": base + "/original", "results": base + "/results",
             "selection": base + "/selection.json", "plan": base + "/plan.json",
             "result": base + "/result.json", "adapter_result": base + "/adapter-result.json",
             "request_events": base + "/requests.jsonl", "process": base + "/process.json",
             "home": base + "/home", "aider_root": aider_root, "polyglot_root": polyglot_root}
    container = resolve_container_binary()
    if not container:
        raise BenchmarkJobError("container_runtime_missing", "Aider requires a prepared container runtime")
    adapter = Path(__file__).with_name("aider_adapter.py")
    plan = {"schema": AIDER_PLAN_SCHEMA, "ownership_id": ownership_id, "run_id": run_id,
            "run_root": real_path(run_root), "run_path": run_path, "cache_root": cache,
            "profile": profile["name"], "profile_sha256": profile["content_sha256"],
            "endpoint": target, "request_controls": resolve_job_request_controls(request_controls),
            "budgets": budgets, "timeout_s": timeout, "paths": paths,
            "execution_contract": {"edit_format": "whole", "attempts": 2, "test_timeout_s": 180,
                "lint": "pinned upstream flake8_lint in prepared image via python3 -I, not host Python",
                "cpp_header_lint": "pinned basic_lint with C++ grammar for admitted cpp solution .h files; "
                    "other paths unchanged; local correction, not official leaderboard equivalence",
                "prompt": "unchanged pinned upstream instructions and test feedback"},
            "selection": {"instance_ids": selected, "sha256": _digest(selected),
                          "excluded": sorted(set(available) - set(selected)), "files": snapshots},
            "aider_environment": environment, "python_environment": python_env,
            "source_hashes": SOURCE_HASHES, "adapter_sha256": hashlib.sha256(adapter.read_bytes()).hexdigest(),
            "container_executable": container,
            "command": _adapter_command(python, paths["plan"])}
    plan["plan_sha256"] = _digest(plan)
    verify_plan(plan)
    return plan


def verify_plan(plan):
    """Compatibility entrypoint for the shared preparation/launch validator."""
    from .aider_prepare import verify_aider_plan
    return verify_aider_plan(plan)


def _normalize(plan, result):
    selected = plan["selection"]["instance_ids"]
    for task_id in selected:
        native = Path(plan["paths"]["results"]) / task_id / ".aider.results.json"
        row = {"instance_id": task_id, "artifact_id": hashlib.sha256(task_id.encode()).hexdigest(),
               "first_attempt_passed": None, "second_attempt_passed": None, "resolved": None}
        if native.is_file() and not native.is_symlink():
            if (not native.resolve().is_relative_to(Path(plan["paths"]["results"]).resolve())
                    or any(parent.is_symlink() for parent in native.parents)
                    or native.stat().st_size > 1024 * 1024):
                raise ValueError("unsafe native Aider result")
            raw = json.loads(native.read_text())
            row["native"] = {"path": str(native), "sha256": hashlib.sha256(native.read_bytes()).hexdigest()}
            outcomes = raw.get("tests_outcomes")
            if (raw.get("model") == "openai/" + plan["endpoint"]["model"] and raw.get("edit_format") == "whole"
                    and isinstance(outcomes, list) and 1 <= len(outcomes) <= 2
                    and all(type(item) is bool for item in outcomes)
                    and (outcomes == [True] or len(outcomes) == 2 and outcomes[0] is False)):
                row.update(first_attempt_passed=outcomes[0], second_attempt_passed=outcomes[1] if len(outcomes) == 2 else None,
                           resolved=any(outcomes))
        result["instances"].append(row)
    graded = [row for row in result["instances"] if row["resolved"] is not None]
    result["summary"] = {"selected": len(selected), "graded": len(graded),
                         "resolved": sum(row["resolved"] for row in graded),
                         "resolve_rate": sum(row["resolved"] for row in graded) / len(graded) if graded else None}
    if len(graded) != len(selected) and result["failure"] is None:
        result["failure"] = {"class": "broken_harness", "code": "missing_aider_outcomes"}


def run_aider_benchmark(plan, *, command_runner=partial(_budget_runner, max_output_bytes=MAX_AIDER_PROCESS_OUTPUT_BYTES), environ=None):
    verify_plan(plan)
    paths = plan["paths"]
    for key in ("aider", "original", "home"):
        Path(paths[key]).mkdir(parents=True, exist_ok=True)
    if Path(paths["results"]).exists():
        raise BenchmarkJobError("aider_run_exists", "Aider native results already exist; create a new run")
    atomic_write_json(paths["plan"], dict(plan))
    atomic_write_json(paths["selection"], plan["selection"])
    for task_id in plan["selection"]["instance_ids"]:
        destination = Path(paths["original"]) / task_id
        if destination.exists():
            raise BenchmarkJobError("aider_run_exists", "Aider original task already exists")
        shutil.copytree(Path(paths["polyglot_root"]) / task_id, destination)
    parent = os.environ if environ is None else environ
    child_env = {key: parent[key] for key in ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR") if key in parent}
    child_env.update(HOME=paths["home"], PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
                     OPENAI_API_BASE=plan["endpoint"]["base_url"],
                     AIDER_BENCHMARK_DIR=paths["aider"], LITELLM_LOCAL_MODEL_COST_MAP="True",
                     AIDER_ANALYTICS="false")
    auth = plan["endpoint"].get("auth_env")
    if auth and not parent.get(auth):
        raise BenchmarkJobError("missing_credential", "Aider endpoint credential reference is unavailable")
    child_env["OPENAI_API_KEY"] = parent[auth].strip() if auth else "anvil-local"
    result = {"schema": AIDER_RUN_SCHEMA, "state": "incomplete", "suite": "aider",
              "run_id": plan["run_id"], "profile": plan["profile"], "plan_sha256": plan["plan_sha256"],
              "endpoint": plan["endpoint"], "selection": plan["selection"], "instances": [],
              "request_controls": plan["request_controls"], "execution_contract": plan.get("execution_contract"), "failure": None,
              "quality": {"status": "unknown"}, "promotion": {"authorized": False}}
    atomic_write_json(paths["result"], result)
    started, stamp = time.monotonic(), utc_now()
    verify_plan(plan)
    try:
        process = command_runner(plan["command"], paths["aider_root"], plan["timeout_s"], child_env)
        stage = _result_record(plan["command"], process, time.monotonic() - started, stamp)
        if stage["returncode"]:
            result["failure"] = {"class": "broken_harness", "code": "aider_adapter_failed"}
    except subprocess.TimeoutExpired as exc:
        stage = _result_record(plan["command"], exc, time.monotonic() - started, stamp)
        result["failure"] = {"class": "budget_exhausted", "code": "deadline_s"}
    except BenchmarkJobError as exc:
        stage = {"status": "failed", "error_type": type(exc).__name__, "code": exc.code}
        result["failure"] = {"class": "infrastructure_failure", "code": exc.code}
    except Exception as exc:
        stage = {"status": "failed", "error_type": type(exc).__name__}
        result["failure"] = {"class": "infrastructure_failure", "code": "aider_process_failed"}
    atomic_write_json(paths["process"], stage)
    result["stage"] = stage
    try:
        terminal = json.loads(Path(paths["adapter_result"]).read_text())
        result["budget"] = terminal["budget"]
        events_path = Path(paths["request_events"])
        if events_path.is_symlink() or events_path.stat().st_size > 1048576 + 8192:
            raise ValueError("invalid request evidence")
        raw_events = events_path.read_bytes().splitlines(keepends=True)
        events = [json.loads(line) for line in raw_events]
        if (not events or events[0].get("event") != "aider_adapter_ready"
                or events[0].get("adapter_sha256") != plan["adapter_sha256"]
                or events[0].get("source_hashes") != SOURCE_HASHES
                or events[-1].get("event") != "adapter_finished"
                or events[-1].get("status") != "complete"
                or events[-1].get("event_count") != len(events) - 1
                or events[-1].get("byte_count") != sum(map(len, raw_events[:-1]))
                or [row.get("seq") for row in events] != list(range(1, len(events) + 1))
                or sum(row.get("event") == "aider_transport_started" for row in events) != terminal["budget"]["model_calls"]):
            raise ValueError("incomplete request evidence")
        result["request_observations"] = evidence_file_reference(str(events_path), root=plan["run_path"])
        if terminal["budget"]["exhausted"]:
            result["failure"] = {"class": "budget_exhausted", "code": terminal["budget"]["reason"]}
        elif (terminal.get("outcome") != "completed" or terminal.get("plan_sha256") != plan["plan_sha256"]
              or terminal.get("adapter_sha256") != plan["adapter_sha256"] or not terminal.get("events_complete")
              or terminal["budget"]["configured"] != plan["budgets"]):
            result["failure"] = result["failure"] or {"class": "broken_harness", "code": "incomplete_aider_observations"}
    except (OSError, ValueError, KeyError, TypeError):
        result["failure"] = result["failure"] or {"class": "broken_harness", "code": "missing_aider_observations"}
    try:
        _normalize(plan, result)
    except (OSError, ValueError, TypeError):
        result["failure"] = result["failure"] or {"class": "broken_harness", "code": "invalid_aider_results"}
    if result["failure"] is None:
        result["state"] = "completed"
        result["quality"] = {"status": "measured", "oracle_image_id": plan["aider_environment"]["image_id"]}
    atomic_write_json(paths["result"], result)
    result["retention"] = retain_aider_artifacts(run_path=plan["run_path"])
    return result


def retain_aider_artifacts(*, run_path):
    """Inventory canonical evidence, never copy generated dependency/build trees."""
    root = Path(run_path).resolve()
    base = root / "aider"
    result = {"schema": "anvil-serving.aider-retention/v1", "state": "incomplete", "artifacts": [], "missing": []}
    required = ["plan.json", "selection.json", "result.json", "process.json", "requests.jsonl", "adapter-result.json"]
    try:
        if base.is_symlink():
            raise ValueError("symlink evidence root")
        files = [base / name for name in required]
        total_bytes = 0
        selection_path = base / "selection.json"
        if selection_path.is_file() and not selection_path.is_symlink():
            if selection_path.stat().st_size > 1024 * 1024:
                raise ValueError("oversized selection")
            selected = json.loads(selection_path.read_text())["instance_ids"]
            if not isinstance(selected, list) or not 1 <= len(selected) <= 6 or len(set(selected)) != len(selected):
                raise ValueError("oversized or duplicate selection")
            for task in selected:
                validate_task_id(task)
                files.extend(base / "results" / task / name for name in (".aider.results.json", ".aider.chat.history.md"))
        for path in files:
            if not path.resolve().is_relative_to(base) or any(p.is_symlink() for p in [path, *path.parents] if p != root):
                raise ValueError("symlink evidence path")
            if not path.exists():
                result["missing"].append(path.relative_to(base).as_posix())
                continue
            total_bytes += path.stat().st_size
            if not path.is_file() or total_bytes > 64 * 1024 * 1024:
                raise ValueError("invalid evidence file")
            result["artifacts"].append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
                                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        result["state"] = "incomplete" if result["missing"] else "retained"
        if base.exists():
            manifest = base / "retention.json"
            if manifest.exists():
                if manifest.is_symlink() or manifest.stat().st_size > 1024 * 1024:
                    raise ValueError("invalid retention manifest")
                if json.loads(manifest.read_text()) != result:
                    raise ValueError("previously retained evidence changed")
            else:
                atomic_write_json(str(manifest), result)
            result["manifest_reference"] = evidence_file_reference(str(manifest), root=str(root))
    except (OSError, ValueError, KeyError, TypeError):
        result["state"] = "failed"
    return result
