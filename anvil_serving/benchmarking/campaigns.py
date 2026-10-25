"""Frozen campaign manifests and a foreground coordinator over durable jobs."""
from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path
import socket
import re
import time

from .artifacts import atomic_write_json
from .jobs import (BenchmarkJobError, TERMINAL_JOB_STATES, canonical_json_bytes,
                   job_spec_sha256, resolve_owned_run_path, validate_job_id, _validate_json_value)
from .submission import bind_submission_spec, validate_submission_spec
from .campaign_execution import (
    source_sha256 as source_sha256,
    read_campaign_spec as read_campaign_spec,
    campaign_lock as campaign_lock,
    _new_state as _new_state,
    _state as _state,
    STATE_SCHEMA as STATE_SCHEMA,
    MAX_CAMPAIGN_BYTES as MAX_CAMPAIGN_BYTES,
    CAMPAIGN_LOCK_TIMEOUT_S as CAMPAIGN_LOCK_TIMEOUT_S,
)

SCHEMA = "anvil-serving.benchmark-campaign/v1"


def _digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _object(value, allowed, field):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise BenchmarkJobError("bad_campaign", f"{field} must be an object with supported fields")
    return copy.deepcopy(value)


def _seconds(value, field, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
        raise BenchmarkJobError("bad_campaign_budget", f"{field} must be positive and at most {maximum}")
    return value


def _bind_drain_model(policy, identity):
    if not policy:
        return
    labels = policy.get("metric_labels")
    if not isinstance(labels, dict) or len(labels) != 1 or set(labels) - {"model", "model_name"} or next(iter(labels.values()), None) != identity["served_model"]:
        raise BenchmarkJobError("bad_campaign_drain", "drain metric labels must bind exactly the served model")


def plan_campaign(value):
    """Validate explicit cells without preparing assets, writing, or sending traffic."""
    result = _object(value, {"schema", "campaign_id", "ownership_id", "submitted_at", "source_sha256",
                            "arms", "cells", "budgets", "drain_policy", "telemetry", "telemetry_collection"}, "campaign")
    if result.get("schema") != SCHEMA:
        raise BenchmarkJobError("bad_campaign", "unsupported campaign schema")
    for field in ("campaign_id", "ownership_id"):
        validate_job_id(result.get(field), field=field)
    current_source = source_sha256()
    if result.get("source_sha256", current_source) != current_source:
        raise BenchmarkJobError("campaign_drift", "campaign product source changed")
    result["source_sha256"] = current_source
    budgets = _object(result.get("budgets"), {"total_s", "per_arm_s", "drain_reserve_s"}, "budgets")
    _seconds(budgets.get("total_s"), "total_s", 7200)
    _seconds(budgets.get("per_arm_s"), "per_arm_s", min(3600, budgets["total_s"]))
    _seconds(budgets.get("drain_reserve_s"), "drain_reserve_s", budgets["per_arm_s"])
    if budgets["drain_reserve_s"] < 10:
        raise BenchmarkJobError("bad_campaign_budget", "drain reserve must be at least ten seconds")
    result["budgets"] = budgets
    telemetry = result.get("telemetry")
    if not isinstance(telemetry, dict) or set(telemetry) != {"engine", "gpu", "host", "cgroup"}:
        raise BenchmarkJobError("bad_campaign", "declare engine, gpu, host and cgroup telemetry requirements")
    if any(value not in ("required", "not_available") for value in telemetry.values()):
        raise BenchmarkJobError("bad_campaign", "telemetry requirements must be required or not_available")
    result["drain_policy"] = _object(result.get("drain_policy", {}),
        {"running_metric", "waiting_metric", "timeout_s", "poll_s", "metric_labels"}, "drain_policy")
    from .campaign_execution import validate_drain_policy
    validate_drain_policy(result["drain_policy"], reserve_s=budgets["drain_reserve_s"])
    arms = result.get("arms")
    if not isinstance(arms, list) or not 1 <= len(arms) <= 16:
        raise BenchmarkJobError("bad_campaign", "campaign requires one to sixteen explicit arms")
    arm_map = {}
    for arm in arms:
        _object(arm, {"arm_id", "identity", "activation", "endpoint", "worker", "budget_s", "drain_policy"}, "arm")
        arm_id = validate_job_id(arm.get("arm_id"), field="arm_id")
        if arm_id in arm_map:
            raise BenchmarkJobError("bad_campaign", "duplicate arm")
        identity = arm.get("identity")
        required = {"model", "revision", "image", "runtime", "hardware", "recipe_selector", "recipe_sha256", "served_model"}
        text_fields = required - {"hardware"}
        if (not isinstance(identity, dict) or set(identity) != required
                or any(not isinstance(identity[field], str) or not identity[field] for field in text_fields)
                or not isinstance(identity["hardware"], list) or not identity["hardware"]
                or any(not isinstance(item, str) or not re.fullmatch(r"GPU-[A-Za-z0-9-]{1,127}", item)
                       for item in identity["hardware"])):
            raise BenchmarkJobError("bad_campaign", "arm identity requires explicit model, revision, image, runtime, hardware and recipe pins")
        arm["identity"] = _validate_json_value(identity, path="arm.identity")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", identity["image"]):
            raise BenchmarkJobError("bad_campaign", "image pin must be a Docker SHA-256 ID")
        if not isinstance(identity["recipe_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", identity["recipe_sha256"]):
            raise BenchmarkJobError("bad_campaign", "recipe pin must be SHA-256")
        from .campaign_identity import validate_activation
        validate_activation(arm)
        if "drain_policy" in arm:
            arm["drain_policy"] = _object(arm["drain_policy"],
                {"running_metric", "waiting_metric", "timeout_s", "poll_s", "metric_labels"}, "arm drain_policy")
        arm["budget_s"] = _seconds(arm.get("budget_s", budgets["per_arm_s"]), "arm budget", budgets["per_arm_s"])
        if "drain_policy" in arm:
            validate_drain_policy(arm["drain_policy"], reserve_s=budgets["drain_reserve_s"])
        _bind_drain_model(arm.get("drain_policy", result["drain_policy"]), identity)
        arm_map[arm_id] = arm
    if 'telemetry_collection' in result:
        from .campaign_telemetry import validate_collection
        validate_collection(result['telemetry_collection'], arms=[{**arm, 'drain_policy': arm.get('drain_policy', result['drain_policy'])} for arm in arms], reserve_s=budgets['drain_reserve_s'])
    cells = result.get("cells")
    if not isinstance(cells, list) or not 1 <= len(cells) <= 4096:
        raise BenchmarkJobError("bad_campaign", "campaign requires one to 4096 explicit cells")
    ids, runs, batches = set(), set(), {}
    previous_batch = None
    for cell in cells:
        _object(cell, {"cell_id", "arm_id", "batch_id", "task_id", "repeat", "concurrency", "spec", "cache_condition"}, "cell")
        for field in ("cell_id", "batch_id", "arm_id"):
            validate_job_id(cell.get(field), field=field)
        if cell["cell_id"] in ids or cell["arm_id"] not in arm_map:
            raise BenchmarkJobError("bad_campaign", "duplicate cell or undeclared arm")
        ids.add(cell["cell_id"])
        if type(cell.get("concurrency")) is not int or cell["concurrency"] not in (1, 4):
            raise BenchmarkJobError("bad_campaign", "cell concurrency must be C1 or C4")
        if type(cell.get("repeat")) is not int or not 1 <= cell["repeat"] <= 100:
            raise BenchmarkJobError("bad_campaign", "repeat must be a positive bounded integer")
        if not isinstance(cell.get("task_id"), str) or not 1 <= len(cell["task_id"]) <= 512:
            raise BenchmarkJobError("bad_campaign", "cell requires an explicit task ID")
        if cell.get("cache_condition") not in {"basic", "cold", "warm", "uncontrolled"}:
            raise BenchmarkJobError("bad_campaign", "declare cell cache condition: basic, cold, warm or uncontrolled")
        spec = bind_submission_spec(cell.get("spec"))
        if "profile_sha256" not in spec:
            raise BenchmarkJobError("campaign_profile_unbound", "campaign children require a digest-bound v2 profile")
        arm = arm_map[cell["arm_id"]]
        if (spec["ownership_id"] != result["ownership_id"] or spec["endpoint"] != arm.get("endpoint")
                or spec["worker"] != arm.get("worker") or spec["submitted_at"] != result.get("submitted_at")):
            raise BenchmarkJobError("campaign_binding_mismatch", "child owner, endpoint, worker or timestamp differs from campaign")
        if spec["run_id"] in runs or spec["run_id"] == result["campaign_id"]:
            raise BenchmarkJobError("bad_campaign", "child run IDs must be unique and distinct from the campaign")
        runs.add(spec["run_id"])
        parameters = spec["parameters"]
        if spec["suite"] not in {"agentic", "aider", "performance"}:
            raise BenchmarkJobError("campaign_suite_unsupported", "campaigns currently support agentic, Aider and finite performance cells")
        selection = parameters.get("case_ids") if spec["suite"] == "agentic" else parameters.get("instance_ids")
        if spec["suite"] != "performance" and selection != [cell["task_id"]]:
            raise BenchmarkJobError("bad_campaign", "each child must explicitly select exactly its one task")
        if cell["task_id"] == "tool-recovery" and parameters.get("recovery_result") != "error":
            raise BenchmarkJobError("bad_campaign", "tool-recovery must freeze recovery_result=error")
        if spec["suite"] == "performance" and (parameters.get("duration_s") is not None
                or parameters.get("warmup_requests", 0) != 0 or not parameters.get("request_count")
                or parameters.get("concurrency", 1) != 1 or cell["cache_condition"] != "basic"
                or parameters.get("cache_condition", "basic") != "basic"):
            raise BenchmarkJobError("bad_campaign", "performance children require finite basic requests, no warmup and serial execution")
        cell["spec"], cell["spec_sha256"] = spec, job_spec_sha256(spec)
        admission = validate_submission_spec(spec)
        cell["matching"] = {
            "controls_sha256": _digest(admission.get("request_controls", {})),
            "workload_sha256": _digest({"suite": spec["suite"], "task_id": cell["task_id"],
                                        "selection": admission.get("selection"), "profile_sha256": spec.get("profile_sha256")}),
            "oracle_sha256": _digest({"source": current_source, "suite": spec["suite"]}),
            "cache_sha256": _digest(cell["cache_condition"]),
        }
        key = cell["batch_id"]
        if key in batches and previous_batch != key:
            raise BenchmarkJobError("bad_campaign", "batch membership must be contiguous and frozen")
        batches.setdefault(key, []).append(cell)
        previous_batch = key
    for batch in batches.values():
        first = batch[0]
        if len(batch) != first["concurrency"] or any(
            (c["arm_id"], c["concurrency"], c["repeat"]) != (first["arm_id"], first["concurrency"], first["repeat"])
            for c in batch):
            raise BenchmarkJobError("bad_campaign", "each frozen batch must contain exactly C1 or C4 cells in one arm and repeat")
    return result


def campaign_directory(run_root, ownership_id, campaign_id):
    return Path(resolve_owned_run_path(run_root, ownership_id=ownership_id,
                                      run_id=campaign_id, relative="campaign"))


def _check_source(manifest):
    if manifest["source_sha256"] != source_sha256():
        raise BenchmarkJobError("campaign_drift", "frozen campaign product source changed")
    for cell in manifest["cells"]:
        if job_spec_sha256(cell["spec"]) != cell["spec_sha256"]:
            raise BenchmarkJobError("campaign_drift", "frozen child specification changed")
        validate_submission_spec(cell["spec"], require_profile_binding=True)


def load_campaign(run_root, ownership_id, campaign_id):
    directory = campaign_directory(run_root, ownership_id, campaign_id)
    manifest = read_campaign_spec(directory / "manifest.json")
    if (manifest.get("schema") != SCHEMA or manifest.get("ownership_id") != ownership_id
            or manifest.get("campaign_id") != campaign_id or manifest.get("manifest_sha256") !=
            _digest({key: value for key, value in manifest.items() if key != "manifest_sha256"})):
        raise BenchmarkJobError("campaign_drift", "frozen campaign identity or digest changed")
    return manifest


def _local_worker(manifest):
    hostname = socket.gethostname().casefold()
    if any(arm["worker"]["id"].casefold() not in {hostname, hostname.split(".")[0]} for arm in manifest["arms"]):
        raise BenchmarkJobError("campaign_worker_mismatch", "campaign preparation and execution must run on its declared local worker")


def prepare_campaign(value, store, *, cache_root=None, offline=False):
    from .campaign_execution import prepare_child, verify_prepared_child
    manifest = plan_campaign(value)
    _local_worker(manifest)
    directory = campaign_directory(store.run_root, manifest["ownership_id"], manifest["campaign_id"])
    with campaign_lock(directory):
        if (directory / "manifest.json").exists():
            existing = load_campaign(store.run_root, manifest["ownership_id"], manifest["campaign_id"])
            if existing["input_sha256"] != _digest(manifest):
                raise BenchmarkJobError("campaign_drift", "campaign was already frozen with different input")
            _check_source(existing)
            _state(directory, manifest=existing)
            for cell in existing["cells"]:
                verify_prepared_child(cell["spec"], cell["prepared"], run_root=store.run_root)
            return existing
        # No jobs or traffic exist yet; serialize preparation to avoid replacing shared child plans.
        manifest["input_sha256"] = _digest(manifest)
        for cell in manifest["cells"]:
            if store.status(cell["spec"]["run_id"]) is not None:
                raise BenchmarkJobError("campaign_child_exists", "preparation cannot adopt an existing durable child")
            cell["prepared"] = prepare_child(cell["spec"], run_root=store.run_root,
                                              cache_root=cache_root, offline=offline)
            cell["matching"].update(cell["prepared"].get("matching", {}))
        _check_source(manifest)
        manifest["manifest_sha256"] = _digest(manifest)
        atomic_write_json(directory / "manifest.json", manifest)
        atomic_write_json(directory / "state.json", _new_state(manifest))
    return manifest


def _owned_record(store, manifest, cell):
    from .campaign_execution import bind_campaign_child
    record = store.status(cell["spec"]["run_id"])
    if record is not None:
        expected = bind_campaign_child(cell["spec"], manifest_sha256=manifest["manifest_sha256"],
            campaign_id=manifest["campaign_id"], cell_id=cell["cell_id"], arm_id=cell["arm_id"], batch_id=cell["batch_id"])
        if record["spec"] != expected:
            raise BenchmarkJobError("campaign_child_mismatch", "durable child is not owned by this frozen campaign")
    return record


def cancel_campaign(manifest, store):
    from .worker import cancel_campaign_jobs
    return cancel_campaign_jobs(manifest, store)


def _run_campaign(manifest, store, *, arm_id, poll_s=0.1):
    """Run one explicit arm; existing dispositions are never relaunched or regrouped."""
    from . import campaign_execution as execution
    from .worker import launch_benchmark_job
    _check_source(manifest)
    _local_worker(manifest)
    if arm_id not in {arm["arm_id"] for arm in manifest["arms"]}:
        raise BenchmarkJobError("bad_campaign_arm", "arm is not in the frozen campaign")
    directory = campaign_directory(store.run_root, manifest["ownership_id"], manifest["campaign_id"])
    batches = {}
    for cell in manifest["cells"]:
        batches.setdefault(cell["batch_id"], []).append(cell)
    sampler = None
    try:
        for batch_id, cells in batches.items():
            if cells[0]["arm_id"] != arm_id:
                continue
            with campaign_lock(directory):
                state = _state(directory, manifest=manifest)
                if state["cancel_requested"]:
                    return {"state": "cancelled", "campaign_id": manifest["campaign_id"]}
                _check_source(manifest)
                # Reconcile every arm before spending another batch's allowance.
                for prior_id, prior in batches.items():
                    records = [_owned_record(store, manifest, cell) for cell in prior]
                    if not any(records):
                        continue
                    if prior_id == batch_id:
                        continue
                    receipt = execution.reconcile_batch(manifest, prior_id, run_root=store.run_root, store=store)
                    if receipt.get("status") != "closed":
                        raise BenchmarkJobError("campaign_unclosed_batch", "a prior batch lacks verified closure")
                existing = [_owned_record(store, manifest, cell) for cell in cells]
                if any(existing):
                    if not all(existing):
                        raise BenchmarkJobError("campaign_partial_submission", "partially submitted frozen batch requires cancellation; it cannot be regrouped")
                    if all(record["state"] in TERMINAL_JOB_STATES for record in existing):
                        receipt = execution.reconcile_batch(manifest, batch_id, run_root=store.run_root, store=store)
                        if receipt.get("status") != "closed":
                            raise BenchmarkJobError("campaign_unclosed_batch", "terminal jobs lack verified batch closure")
                        continue
                else:
                    for cell in cells:
                        execution.verify_prepared_child(cell["spec"], cell["prepared"], run_root=store.run_root)
                    state["batches"][batch_id] = {"phase": "submitting", "run_ids": [cell["spec"]["run_id"] for cell in cells]}
                    atomic_write_json(directory / "state.json", state)
                    for cell in cells:
                        spec = execution.bind_campaign_child(cell["spec"], manifest_sha256=manifest["manifest_sha256"],
                            campaign_id=manifest["campaign_id"], cell_id=cell["cell_id"], arm_id=cell["arm_id"], batch_id=batch_id)
                        disposition, record = store.submit(spec)
                        if disposition == "submitted":
                            launch_benchmark_job(path=store.path, run_root=store.run_root, run_id=record["spec"]["run_id"])
                    state["batches"][batch_id]["phase"] = "waiting"
                    atomic_write_json(directory / "state.json", state)
            ready_deadline = time.monotonic() + 60
            released = False
            while True:
                execution.coordinate_batch(manifest, batch_id, store=store, sampler=sampler)
                with campaign_lock(directory):
                    state = _state(directory, manifest=manifest)
                    if state["cancel_requested"]:
                        return {"state": "cancelled", "campaign_id": manifest["campaign_id"]}
                    records = [_owned_record(store, manifest, cell) for cell in cells]
                    receipt = execution.reconcile_batch(manifest, batch_id, run_root=store.run_root, store=store)
                    if receipt.get("status") == "closed":
                        break
                    if receipt.get("status") == "blocked":
                        raise BenchmarkJobError("campaign_unclosed_batch", "batch deadline or closure is unverified; further traffic is blocked")
                    released = released or receipt.get("status") == "active"
                    if not released and execution.batch_ready(manifest, batch_id, run_root=store.run_root):
                        _check_source(manifest)
                        allowance = max(cell["spec"]["timeout_s"] for cell in cells) + manifest["budgets"]["drain_reserve_s"]
                        options = {}
                        if 'telemetry_collection' in manifest:
                            from .campaign_telemetry import BatchSampler
                            sampler = BatchSampler(manifest, batch_id, run_root=store.run_root)
                            options['sampler'] = sampler
                        execution.release_batch(manifest, batch_id, run_root=store.run_root, allowance_s=allowance, store=store, **options)
                        released = True
                    elif not released and (time.monotonic() >= ready_deadline or any(
                            record["state"] in TERMINAL_JOB_STATES for record in records)):
                        raise BenchmarkJobError("campaign_batch_not_ready", "frozen batch could not reach readiness; cancel it before proceeding")
                    elif released and all(record["state"] in TERMINAL_JOB_STATES for record in records):
                        raise BenchmarkJobError("campaign_unclosed_batch", "terminal batch lacks verified drain; further traffic is blocked")
                time.sleep(poll_s)
    finally:
        # Cancellation owns remaining stop/drain/finalization phases on error.
        # Never finalize partial telemetry while siblings may still be active.
        pass
    return {"state": "completed", "campaign_id": manifest["campaign_id"], "arm_id": arm_id}


def run_campaign(manifest, store, *, arm_id, poll_s=0.1):
    try:
        return _run_campaign(manifest, store, arm_id=arm_id, poll_s=poll_s)
    except (BenchmarkJobError, KeyboardInterrupt, OSError, ValueError):
        # Retain cancellation intent even when the foreground coordinator cannot continue.
        cancel_campaign(manifest, store)
        raise
