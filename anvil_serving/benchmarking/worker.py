"""Detached benchmark worker entry point for durable unattended jobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Mapping
import uuid

from .artifacts import (
    BenchmarkArtifactError,
    atomic_write_json,
    build_measured_evidence,
    evidence_file_reference,
    normalize_evidence_failure_class,
)
from .harnesses import prepare_harness_assets
from .campaign_execution import campaign_result_reference, child_lock
from .jobs import BenchmarkJobError, resolve_owned_run_path, utc_now
from .preflight import require_benchmark_preflight, run_benchmark_preflight
from .profiles import load_profile, suite_budgets
from .suite_runner import run_agentic_suite, run_context_suite
from .swe import build_swe_run_plan, run_swe_benchmark
from .submission import validate_submission_spec
from .swe_logs import server_log_collector
from .retention import (
    _aider_retention_complete, _swe_retention_complete, read_retained_swe_artifacts, retain_swe_artifacts,
)
from ..control_plane.controller.store import BenchmarkJobStore


def _store(path: str, run_root: str) -> BenchmarkJobStore:
    return BenchmarkJobStore(path, run_root=run_root)


def _write_stage(
    store: BenchmarkJobStore,
    record: Mapping[str, Any],
    name: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    spec = record["spec"]
    relative = f"evidence/{len(value.get('stages', []))}-{name}.json"
    # The index above is only a filename hint; stage ordering is set by the caller.
    path = resolve_owned_run_path(
        store.run_root,
        ownership_id=spec["ownership_id"],
        run_id=spec["run_id"],
        relative=relative,
    )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, value)
    run_path = resolve_owned_run_path(
        store.run_root, ownership_id=spec["ownership_id"], run_id=spec["run_id"]
    )
    return evidence_file_reference(path, root=run_path)


def _identities(
    spec: Mapping[str, Any],
    profile: Mapping[str, Any],
    preflight: Mapping[str, Any] | None,
    assets: Mapping[str, Any] | None,
) -> dict[str, Any]:
    declared = spec.get("parameters", {}).get("identities", {})
    declared = declared if isinstance(declared, Mapping) else {}
    worker = preflight.get("observed", {}).get("worker", {}) if preflight else {}
    endpoint = preflight.get("observed", {}).get("endpoint", {}) if preflight else {}
    adapters = assets.get("assets", {}) if assets else {}
    suite = profile.get("suites", {}).get(spec["suite"], {})
    parameters = spec.get("parameters", {})
    topology_kind = (
        "co-resident-benchmark-client"
        if parameters.get("client_topology") == "co-resident"
        else "isolated-benchmark-worker"
    )

    def declared_or(name: str, fallback: Any) -> Any:
        value = declared.get(name)
        return value if value is not None else fallback

    return {
        "model": declared_or("model", {"alias": spec["endpoint"]["model"]}),
        "served_model": declared_or(
            "served_model",
            {"id": spec["endpoint"]["model"], "configured_context": endpoint.get("configured_context", "unavailable")},
        ),
        "runtime": declared_or("runtime", {"status": "unavailable", "reason": "not exposed by router preflight"}),
        "image": declared_or(
            "image",
            adapters.get("worker-base", {"status": "not-applicable"}),
        ),
        "hardware": declared_or("hardware", dict(worker) or {"worker_id": spec["worker"]["id"]}),
        "topology": declared_or(
            "topology", {"kind": topology_kind, "worker_id": spec["worker"]["id"]}
        ),
        "context": declared_or(
            "context",
            {
                "configured_tokens": endpoint.get("configured_context", "unavailable"),
                "profile_buckets": suite.get("token_buckets", "not-applicable"),
                "selected_buckets": parameters.get(
                    "token_buckets", suite.get("token_buckets", "not-applicable")
                ),
                "output_headroom_tokens": parameters.get(
                    "output_headroom_tokens", suite.get("output_headroom_tokens", "not-applicable")
                ),
            },
        ),
        "concurrency": declared_or("concurrency", {"requests": parameters.get("concurrency", 1), "workers": 1, "status": "configured"} if spec["suite"] == "performance" else {"requests": 1, "workers": 1}),
        "harnesses": declared_or(
            "harnesses",
            {
                name: {
                    key: value
                    for key, value in profile["adapters"][name].items()
                    if key in {"source", "revision", "image"}
                }
                for name in suite.get("adapters", [])
            } or {"native": {"schema": f"anvil-serving.{spec['suite']}/v1"}},
        ),
        "dataset": declared_or(
            "dataset",
            {"name": "SWE-bench_Verified", "split": "test"}
            if spec["suite"] == "swe"
            else {"name": "Aider Polyglot", "revision": profile.get("adapters", {}).get("polyglot-benchmark", {}).get("revision", "unavailable")}
            if spec["suite"] == "aider"
            else {"name": "native-synthetic", "runner": parameters.get("runner"), "requested_workload": parameters.get("workload"), "tokenizer_match": "unverified"}
            if spec["suite"] == "performance"
            else {"name": "deterministic-native", "profile_sha256": profile.get("content_sha256", "unavailable")},
        ),
    }


def _stage(sequence: int, name: str, status: str, reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "name": name,
        "status": status,
        "evidence": [reference],
    }


def _retain_stopped_aider(record: Mapping[str, Any], *, run_path: str) -> dict[str, Any]:
    """Containers outlive worker processes; verify their cleanup before retention."""
    from .aider import retain_aider_artifacts
    from .aider_containment import cleanup_aider_containers

    spec = record["spec"]
    cleanup = cleanup_aider_containers(
        run_path=run_path, ownership_id=spec["ownership_id"], run_id=spec["run_id"]
    )
    if cleanup["status"] != "completed":
        return {"state": "failed", "reason": "aider_container_cleanup_unverified", "containers": cleanup}
    if "campaign" in spec and not os.path.lexists(os.path.join(run_path, "aider")):
        return {"state": "absent", "reason": "not_started", "containers": cleanup}
    return {**retain_aider_artifacts(run_path=run_path), "containers": cleanup}


def _retain_external_artifacts(record: Mapping[str, Any], *, run_path: str) -> dict[str, Any]:
    if record["spec"]["suite"] == "aider":
        return _retain_stopped_aider(record, run_path=run_path)
    from .performance import retain_performance_artifacts
    return retain_performance_artifacts(run_path=run_path)


def _aider_campaign_requires_stop(store, spec):
    from .campaign_execution import _read, _path, _manifest_digest, _requires_aider_stop, bind_campaign_child
    try:
        binding = spec['campaign']
        manifest = _read(_path({'ownership_id': spec['ownership_id'], 'run_id': binding['campaign_id']},
                               store.run_root, 'campaign/manifest.json'))
        if _manifest_digest(manifest) != binding['manifest_sha256']:
            return True
        cells = [cell for cell in manifest['cells'] if cell['cell_id'] == binding['cell_id']]
        if len(cells) != 1:
            return True
        cell = cells[0]
        expected = bind_campaign_child(cell['spec'], manifest_sha256=binding['manifest_sha256'],
            campaign_id=binding['campaign_id'], cell_id=cell['cell_id'], arm_id=cell['arm_id'], batch_id=cell['batch_id'])
        return True if expected != spec else _requires_aider_stop(cell)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, BenchmarkJobError):
        return True  # Unknown history cannot authorize deletion or closure.


def _aider_campaign_stop_reference(store, record, retention, run_path, *, cancelling=False):
    from .aider_containment import aider_custody_snapshot
    spec = record["spec"]
    proof = {"schema": "anvil-serving.aider-campaign-stop/v1", "ownership_id": spec["ownership_id"],
             "run_id": spec["run_id"], "spec_sha256": record["spec_sha256"], "retention": retention,
             "records": aider_custody_snapshot(run_path=run_path, ownership_id=spec["ownership_id"], run_id=spec["run_id"])}
    if cancelling or retention.get("state") == "absent":
        proof["worker_stop"] = evidence_file_reference(os.path.join(run_path, "evidence", "worker-stop-receipt.json"), root=run_path)
    return _write_stage(store, record, "aider-campaign-stop", proof)


def _read_swe_custody_plan(path: str, *, expected_sha256: str | None = None,
                           required_schema: str | None = None, owner: str | None = None,
                           run_id: str | None = None) -> str | None:
    """Read one stable, bounded plan and return its pinned custody-helper hash."""
    expected_hash: str | None = None
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("SWE plan is not regular")
        raw = stream.read(262_145)
        after = os.fstat(stream.fileno())
    current = os.stat(path, follow_symlinks=False)
    def identity(item):
        return (item.st_dev, item.st_ino, item.st_mode, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
    if len(raw) > 262_144 or identity(before) != identity(after) or identity(after) != identity(current):
        raise ValueError("SWE plan changed or exceeds its bound")
    if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("retained SWE plan digest differs from its manifest")
    planned = json.loads(raw)
    if required_schema is not None:
        source = planned.get("source") if isinstance(planned, dict) else None
        if (not isinstance(planned, dict) or planned.get("schema") != required_schema
                or planned.get("ownership_id") != owner
                or planned.get("run_id") != run_id or not isinstance(source, dict)
                or not all(isinstance(source.get(name), str) and re.fullmatch(r"[0-9a-f]{64}", source[name])
                           for name in ("plan_sha256", "predictions_sha256"))):
            raise ValueError("SWE custody binding is invalid")
    harnesses = planned.get("harnesses") if isinstance(planned, dict) else None
    custody = harnesses.get("container_custody") if isinstance(harnesses, dict) else None
    if custody is not None:
        expected_hash = custody.get("adapter_sha256") if isinstance(custody, dict) else None
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValueError("SWE custody source hash is invalid")
    return expected_hash


def _retained_swe_custody_hash(run_path: str, *, owner: str, run_id: str) -> tuple[str | None, str | None]:
    """Prefer work, then only a manifest-verified canonical plan after retention."""
    work_plan = os.path.join(run_path, "work", "swe-plan.json")
    try:
        return _read_swe_custody_plan(work_plan), None
    except FileNotFoundError:
        pass
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None, "plan_unreadable"
    canonical = os.path.join(run_path, "evidence", "swe-canonical")
    if os.path.lexists(canonical):
        try:
            manifest = read_retained_swe_artifacts(run_path=run_path)
            plan_entry = next((entry for entry in manifest["files"] if entry.get("path") == "swe-plan.json"), None)
            if not isinstance(plan_entry, dict) or not isinstance(plan_entry.get("sha256"), str):
                raise ValueError("canonical SWE plan is unavailable")
            retained_plan = os.path.join(canonical, "swe-plan.json")
            return _read_swe_custody_plan(retained_plan, expected_sha256=plan_entry["sha256"]), None
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None, "plan_unreadable"
    try:
        binding = os.path.join(run_path, "evidence", "swe-regrade-custody-binding.json")
        return _read_swe_custody_plan(
            binding, required_schema="anvil-serving.swe-regrade-custody-binding/v1",
            owner=owner, run_id=run_id,
        ), None
    except FileNotFoundError:
        return None, "plan_missing"
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None, "plan_unreadable"


def _swe_container_cleanup(record: Mapping[str, Any], *, run_path: str, phase: str) -> dict[str, Any]:
    """Daemon-owned SWE containers require an owner-scoped absence receipt."""
    spec = record["spec"]
    ledger = os.path.join(run_path, "swe-containers")
    expected_hash, plan_error = _retained_swe_custody_hash(
        run_path, owner=spec["ownership_id"], run_id=spec["run_id"],
    )

    if not os.path.isdir(ledger):
        # Retained plans that predate custody do not acquire it retroactively.
        # Every current custody plan instead requires an on-disk ledger.
        required = expected_hash is not None or plan_error not in {None, "plan_missing"}
        return {"status": "incomplete" if required else "completed",
                "reason": "ledger_missing" if required else "not_started",
                "containers": [], "errors": []}
    if plan_error is not None or expected_hash is None:
        return {"status": "incomplete", "reason": plan_error or "custody_hash_missing",
                "containers": [], "errors": []}

    from . import swe_container_custody
    try:
        swe_container_custody.verify_source_hash(expected_hash)
    except (OSError, ValueError, TypeError):
        return {"status": "incomplete", "reason": "custody_source_hash_mismatch",
                "containers": [], "errors": []}
    receipt = os.path.join(run_path, "evidence", f"swe-container-cleanup-{phase}-{uuid.uuid4().hex}.json")
    Path(receipt).parent.mkdir(parents=True, exist_ok=True)
    cleanup = swe_container_custody.cleanup(
        ledger, spec["ownership_id"], spec["run_id"], receipt_path=receipt,
    )
    return {**cleanup, "receipt": evidence_file_reference(receipt, root=run_path)}


def _retain_stopped_swe(record: Mapping[str, Any], *, run_path: str, work_path: str, phase: str) -> dict[str, Any]:
    cleanup = _swe_container_cleanup(record, run_path=run_path, phase=phase)
    if cleanup["status"] != "completed":
        return {"state": "failed", "reason": "swe_container_cleanup_unverified", "containers": cleanup}
    if ("campaign" in record["spec"] and cleanup.get("reason") == "not_started"
            and not os.path.lexists(os.path.join(run_path, "work"))
            and not os.path.lexists(os.path.join(run_path, "swe-containers"))):
        return {"state": "absent", "reason": "not_started", "containers": cleanup}
    return {**retain_swe_artifacts(run_path=run_path, work_path=work_path), "containers": cleanup}


def _swe_campaign_stop_reference(store, record, retention, run_path, *, cancelling=False):
    proof = {"schema": "anvil-serving.swe-campaign-stop/v1", "ownership_id": record["spec"]["ownership_id"],
             "run_id": record["spec"]["run_id"], "spec_sha256": record["spec_sha256"], "retention": retention}
    if cancelling or retention.get("state") == "absent":
        proof["worker_stop"] = evidence_file_reference(os.path.join(run_path, "evidence", "worker-stop-receipt.json"), root=run_path)
    return _write_stage(store, record, "swe-campaign-stop", proof)


def _run_suite(
    store: BenchmarkJobStore,
    record: Mapping[str, Any],
    profile: Mapping[str, Any],
    assets: Mapping[str, Any],
) -> dict[str, Any]:
    spec = record["spec"]
    if spec["suite"] == "context":
        return run_context_suite(profile, spec)
    if spec["suite"] == "agentic":
        return run_agentic_suite(profile, spec)
    if spec["suite"] not in {"swe", "aider", "performance"}:
        raise BenchmarkJobError("unknown_suite", "benchmark suite is unknown")
    instance_ids = spec.get("parameters", {}).get("instance_ids")
    parameters = spec.get("parameters", {})
    cache_root = os.environ.get(
        "ANVIL_BENCHMARK_CACHE_ROOT",
        os.path.expanduser("~/.anvil-serving/benchmark-harness-cache"),
    )
    if spec["suite"] == "performance":
        from .performance import build_performance_run_plan, run_performance_benchmark
        plan = build_performance_run_plan(
            profile, assets, endpoint=spec["endpoint"],
            parameters={key: parameters[key] for key in (
                "runner", "workload", "concurrency", "request_count", "duration_s",
                "warmup_requests", "cache_condition", "thinking_mode", "reasoning_effort",
                "temperature", "top_p",
            ) if key in parameters},
            run_root=store.run_root, cache_root=cache_root,
            ownership_id=spec["ownership_id"], run_id=spec["run_id"], timeout_s=spec["timeout_s"],
        )
        return run_performance_benchmark(plan)
    if spec["suite"] == "aider":
        from .aider import build_aider_run_plan, run_aider_benchmark

        plan = build_aider_run_plan(
            profile, assets, endpoint=spec["endpoint"], instance_ids=instance_ids,
            run_root=store.run_root, cache_root=cache_root,
            ownership_id=spec["ownership_id"], run_id=spec["run_id"],
            request_controls={key: parameters[key] for key in (
                "thinking_mode", "reasoning_effort", "temperature", "top_p"
            ) if key in parameters}, timeout_s=spec["timeout_s"],
        )
        return run_aider_benchmark(plan)
    plan = build_swe_run_plan(
        profile,
        assets,
        endpoint=spec["endpoint"],
        instance_ids=instance_ids,
        run_root=store.run_root,
        cache_root=cache_root,
        ownership_id=spec["ownership_id"],
        run_id=spec["run_id"],
        request_controls={
            key: parameters[key]
            for key in ("thinking_mode", "reasoning_effort", "temperature", "top_p",
                        "force_nonempty_content")
            if key in parameters
        },
        paired_image_ids=parameters.get("paired_image_ids"),
        server_log=parameters.get("server_log"),
        prompt_contract=parameters.get("prompt_contract", "legacy/v1"),
        **({"timeout_s": spec["timeout_s"]} if suite_budgets(profile, "swe") is not None else {}),
    )
    selector = plan.get("server_log")
    collector = server_log_collector(selector) if selector is not None else None
    return run_swe_benchmark(plan, **({"log_collector": collector} if collector else {}))



def _finish_job(store, spec, target, *, results, failure=None):
    if 'campaign' not in spec:
        finished = store.transition(spec['run_id'], target, results=results, failure=failure)
        if spec["suite"] == "swe":
            finished = {**finished, "container_custody": results.get("container_custody", {"status": "incomplete"})}
        return finished
    # Waiting must never hold the write lock: cancellation must stop all siblings.
    try:
        reference = campaign_result_reference(store, spec)
    except Exception as secondary:
        if target != "failed" or spec["suite"] not in {"swe", "aider"}:
            raise
        results = dict(results)
        results.setdefault("secondary_failures", []).append({"stage": "campaign_result_reference", "type": type(secondary).__name__})
        reference = {}  # An unanchored failed child cannot release its reservation.
    with child_lock(store, spec):
        current = store.status(spec['run_id'])
        if current['state'] != 'running':
            return current
        # Receipt writers use this same lock; cancellation cannot replace it
        # between reference capture and immutable artifact commit.
        path = resolve_owned_run_path(store.run_root, ownership_id=spec['ownership_id'],
                                      run_id=spec['run_id'], relative='campaign-execution.json')
        if reference:
            reference = {'campaign_execution': evidence_file_reference(path, root=str(Path(path).parent))}
        return store.transition(spec['run_id'], target, results={**results, **reference}, failure=failure)

def execute_benchmark_job(store: BenchmarkJobStore, run_id: str) -> dict[str, Any]:
    """Claim and execute one job, retaining evidence at each completed stage."""
    record = store.claim(run_id)
    spec = record["spec"]
    profile: dict[str, Any] = {}
    stages: list[dict[str, Any]] = []
    assets = None
    preflight = None
    swe_container_custody = None
    retention = None
    campaign_context = None
    campaign_closure = None
    try:
        profile = load_profile(spec["profile"])
        admission = validate_submission_spec(spec, profile=profile, require_profile_binding=True)
        if "campaign" in spec and spec["suite"] not in {"context", "agentic", "aider", "performance", "swe"}:
            raise BenchmarkJobError("unsupported_campaign_suite", "campaign cannot execute this suite")
        if "budgets" in admission:
            stages.append(_stage(len(stages), "admission", "completed", _write_stage(store, record, "admission", admission)))
        if spec.get("parameters", {}).get("operation") == "regrade":
            from .swe_regrade import prepare_swe_regrade, regrade_swe_predictions

            store.append_log(run_id, level="info", message="preparing retained SWE regrade")
            plan = prepare_swe_regrade(store, record)
            suite_result = regrade_swe_predictions(plan)
            swe_container_custody = suite_result.get("container_custody")
            suite_ref = _write_stage(store, record, "regrade", suite_result)
            completed = suite_result.get("state") == "completed"
            stages.append(_stage(len(stages), "regrade", "completed" if completed else "failed", suite_ref))
            if not completed:
                failure = suite_result.get("failure") or {"class": "grader_failure", "message": "regrade incomplete"}
                raise BenchmarkJobError(failure.get("class", "grader_failure"), failure.get("message", "regrade incomplete"))
            evidence = build_measured_evidence(
                run={"run_id": spec["run_id"], "ownership_id": spec["ownership_id"], "suite": spec["suite"], "profile": spec["profile"], "spec_sha256": record["spec_sha256"]},
                identities=_identities(spec, profile, None, None), stages=stages,
                completeness="completed", summary=dict(suite_result.get("summary") or {"completed": True}),
                created_at=utc_now(), artifact_root=resolve_owned_run_path(store.run_root, ownership_id=spec["ownership_id"], run_id=spec["run_id"]),
            )
            return _finish_job(
                store, spec, "completed",
                results={"evidence": evidence, "container_custody": swe_container_custody or {"status": "incomplete"}},
            )
        store.append_log(run_id, level="info", message="benchmark worker claimed job")
        if "budgets" in admission:
            run_path = resolve_owned_run_path(store.run_root, ownership_id=spec["ownership_id"], run_id=run_id)
            if shutil.disk_usage(run_path).free < admission["budgets"]["min_free_disk_bytes"]:
                raise BenchmarkJobError("insufficient_disk", "free disk is below the profile reserve before asset preparation")
        if "campaign" in spec:
            from .campaign_execution import enter_campaign
            campaign_context = enter_campaign(store, spec)
            assets = campaign_context["payload"]["assets"]
        else:
            store.append_log(run_id, level="info", message="preparing pinned benchmark assets")
            assets = prepare_harness_assets(
                profile,
                suite=spec["suite"],
                run_root=store.run_root,
                ownership_id=spec["ownership_id"],
                run_id=spec["run_id"],
                cache_root=os.environ.get(
                    "ANVIL_BENCHMARK_CACHE_ROOT",
                    os.path.expanduser("~/.anvil-serving/benchmark-harness-cache"),
                ),
                offline=bool(spec.get("parameters", {}).get("offline", False)),
                **({"aider_instance_ids": admission["selection"]["instance_ids"]}
                   if spec["suite"] == "aider" and admission["selection"]["instance_ids"] is not None else {}),
            )
        stages.append(_stage(len(stages), "asset_preparation", "completed", _write_stage(store, record, "assets", assets)))
        if store.status(run_id)["state"] != "running":
            return store.status(run_id)

        store.append_log(run_id, level="info", message="running independent benchmark preflight")
        requirements = dict(profile["suites"][spec["suite"]]["requirements"])
        model_host_id = spec.get("parameters", {}).get("model_host_id")
        if model_host_id:
            requirements["model_host_id"] = model_host_id
        preflight = run_benchmark_preflight(
            spec,
            run_root=store.run_root,
            requirements=requirements,
        )
        preflight_ref = _write_stage(store, record, "preflight", preflight)
        preflight_status = "completed" if preflight["passed"] else "failed"
        stages.append(_stage(len(stages), "preflight", preflight_status, preflight_ref))
        require_benchmark_preflight(preflight)
        if store.status(run_id)["state"] != "running":
            return store.status(run_id)

        store.append_log(run_id, level="info", message=f"executing {spec['suite']} suite")
        if campaign_context and spec["suite"] in {"aider", "performance", "swe"}:
            from .campaign_execution import verify_child
            _, prepared = verify_child(spec, run_root=store.run_root)
        if campaign_context:
            from .campaign_execution import require_campaign_suite_window
            require_campaign_suite_window(campaign_context)
        if campaign_context and spec["suite"] in {"aider", "performance", "swe"}:
            if spec["suite"] == "swe":
                selector = prepared["plan"].get("server_log")
                collector = server_log_collector(selector) if selector is not None else None
                suite_result = run_swe_benchmark(prepared["plan"], **({"log_collector": collector} if collector else {}))
            elif spec["suite"] == "aider":
                from .aider import run_aider_benchmark
                suite_result = run_aider_benchmark(prepared["plan"])
            else:
                from .performance import run_performance_benchmark
                suite_result = run_performance_benchmark(prepared["plan"])
        else:
            suite_result = _run_suite(store, record, profile, assets)
        if campaign_context and spec["suite"] not in {"swe", "aider"}:
            from .campaign_execution import close_campaign
            campaign_closure = close_campaign(campaign_context)
        if store.status(run_id)["state"] != "running":
            return store.status(run_id)
        if spec["suite"] in {"aider", "performance"}:
            suite = spec["suite"]
            run_path = resolve_owned_run_path(store.run_root, ownership_id=spec["ownership_id"], run_id=run_id)
            retention = _retain_external_artifacts(record, run_path=run_path)
            retention_ref = _write_stage(store, record, f"{suite}-retention", retention)
            stages.append(_stage(len(stages), f"{suite}_retention", "completed" if retention["state"] == "retained" else "incomplete", retention_ref))
            if retention["state"] == "failed":
                code = ("aider_container_cleanup_unverified" if suite == "aider" and retention.get("reason") == "aider_container_cleanup_unverified"
                        else f"{suite}_artifact_retention_failed")
                raise BenchmarkJobError(code, f"{suite} container cleanup or canonical artifacts could not be verified")
            if retention["state"] != "retained" and suite_result.get("state") == "completed":
                raise BenchmarkJobError(f"{suite}_artifact_retention_incomplete", f"{suite} canonical artifacts are incomplete")
        if campaign_context and spec["suite"] == "aider":
            if not _aider_retention_complete(retention, run_path, spec):
                raise BenchmarkJobError("aider_artifact_retention_incomplete", "Aider campaign custody or retention is incomplete")
            from .campaign_execution import close_campaign
            stop_reference = _aider_campaign_stop_reference(store, record, retention, run_path)
            campaign_closure = close_campaign(campaign_context, aider_stop=stop_reference)
        if spec["suite"] == "swe":
            run_path = resolve_owned_run_path(
                store.run_root, ownership_id=spec["ownership_id"], run_id=spec["run_id"]
            )
            work_path = os.path.join(run_path, "work")
            if os.path.isdir(work_path):
                retention = _retain_stopped_swe(record, run_path=run_path, work_path=work_path, phase="terminal")
                swe_container_custody = retention.get("containers")
                if retention["state"] == "failed":
                    code = ("swe_container_cleanup_unverified" if retention.get("reason") == "swe_container_cleanup_unverified"
                            else "swe_artifact_retention_failed")
                    raise BenchmarkJobError(code, "SWE container cleanup or canonical artifact retention could not be verified")
                retention_ref = _write_stage(store, record, "swe-canonical-retention", retention)
                stages.append(_stage(len(stages), "swe_canonical_retention", "completed" if retention["state"] == "retained" else "incomplete", retention_ref))
        if campaign_context and spec["suite"] == "swe":
            if not _swe_retention_complete(retention, run_path):
                raise BenchmarkJobError("swe_artifact_retention_incomplete", "SWE campaign retention is incomplete")
            from .campaign_execution import close_campaign
            stop_reference = _swe_campaign_stop_reference(store, record, retention, run_path)
            campaign_closure = close_campaign(campaign_context, swe_stop=stop_reference)
            if retention["state"] != "retained" and suite_result.get("state", "completed") == "completed":
                raise BenchmarkJobError("swe_artifact_retention_incomplete", "SWE canonical artifacts are incomplete")
        suite_ref = _write_stage(store, record, spec["suite"], suite_result)
        suite_complete = suite_result.get("state", "completed") == "completed"
        stages.append(_stage(len(stages), spec["suite"], "completed" if suite_complete else "failed", suite_ref))
        if not suite_complete:
            failure = suite_result.get("failure") or {"class": "harness", "message": "suite incomplete"}
            raise BenchmarkJobError(
                failure.get("class", "harness"),
                failure.get("message", "benchmark suite did not complete"),
            )
        if campaign_closure and campaign_closure["status"] != "closed":
            raise BenchmarkJobError("campaign_closure_unknown", "owned clients or engine drain could not be verified")
        summary = suite_result.get("summary") or suite_result.get("curve") or {
            "completed": True,
            "passed": suite_result.get("passed"),
        }
        evidence = build_measured_evidence(
            run={
                "run_id": spec["run_id"],
                "ownership_id": spec["ownership_id"],
                "suite": spec["suite"],
                "profile": spec["profile"],
                "spec_sha256": record["spec_sha256"],
            },
            identities=_identities(spec, profile, preflight, assets),
            stages=stages,
            completeness="completed",
            summary=dict(summary),
            created_at=utc_now(),
            artifact_root=resolve_owned_run_path(
                store.run_root, ownership_id=spec["ownership_id"], run_id=spec["run_id"]
            ),
        )
        store.append_log(run_id, level="info", message="benchmark completed; evidence retained")
        results = {"evidence": evidence}
        if spec["suite"] == "swe":
            results["container_custody"] = swe_container_custody or {"status": "incomplete"}
        return _finish_job(store, spec, "completed", results=results)
    except Exception as exc:
        secondary_failures = []
        if campaign_context and spec["suite"] not in {"swe", "aider"} and campaign_context["receipt"]["status"] == "active":
            from .campaign_execution import close_campaign
            try:
                close_campaign(campaign_context)
            except Exception as secondary:
                secondary_failures.append({"stage": "campaign_close", "type": type(secondary).__name__})
        current = store.status(run_id)
        if current is not None and current["state"] in {"cancelled", "cancelling"}:
            return current
        code = exc.code if isinstance(exc, BenchmarkJobError) else type(exc).__name__
        message = exc.message if isinstance(exc, BenchmarkJobError) else str(exc)
        if spec["suite"] in {"aider", "performance"} and not any(stage["name"] == f"{spec['suite']}_retention" for stage in stages):
            run_path = resolve_owned_run_path(store.run_root, ownership_id=spec["ownership_id"], run_id=run_id)
            try:
                retention = _retain_external_artifacts(record, run_path=run_path)
                retention_ref = _write_stage(store, record, f"{spec['suite']}-retention", retention)
                stages.append(_stage(len(stages), f"{spec['suite']}_retention", "incomplete", retention_ref))
            except Exception as secondary:
                retention = None
                secondary_failures.append({"stage": "retention", "type": type(secondary).__name__})
        if spec["suite"] == "swe":
            run_path = resolve_owned_run_path(store.run_root, ownership_id=spec["ownership_id"], run_id=spec["run_id"])
            work_path = os.path.join(run_path, "work")
            if os.path.isdir(work_path) and not any(stage["name"] == "swe_canonical_retention" for stage in stages):
                try:
                    retention = _retain_stopped_swe(record, run_path=run_path, work_path=work_path, phase="failed")
                    swe_container_custody = retention.get("containers")
                    if retention["state"] != "failed":
                        retention_ref = _write_stage(store, record, "swe-canonical-retention", retention)
                        stages.append(_stage(len(stages), "swe_canonical_retention", "completed" if retention["state"] == "retained" else "incomplete", retention_ref))
                except Exception as secondary:
                    retention = None
                    secondary_failures.append({"stage": "retention", "type": type(secondary).__name__})
        if campaign_context and spec["suite"] == "aider" and campaign_context["receipt"]["status"] == "active":
            from .campaign_execution import close_campaign
            try:
                custody_verified = _aider_retention_complete(retention, run_path, spec)
                stop_reference = _aider_campaign_stop_reference(store, record, retention, run_path) if custody_verified else None
                close_campaign(campaign_context, **({"aider_stop": stop_reference} if custody_verified else {"clients_stopped": False, "wait": False}))
            except Exception as secondary:
                secondary_failures.append({"stage": "campaign_stop_proof_or_close", "type": type(secondary).__name__})
        if campaign_context and spec["suite"] == "swe" and campaign_context["receipt"]["status"] == "active":
            from .campaign_execution import close_campaign
            try:
                custody_verified = _swe_retention_complete(retention, run_path)
                stop_reference = _swe_campaign_stop_reference(store, record, retention, run_path) if custody_verified else None
                close_campaign(campaign_context, **({"swe_stop": stop_reference} if custody_verified else {"clients_stopped": False, "wait": False}))
            except Exception as secondary:
                secondary_failures.append({"stage": "campaign_stop_proof_or_close", "type": type(secondary).__name__})
        try:
            failure_class = normalize_evidence_failure_class(code)
        except BenchmarkArtifactError:
            failure_class = "worker_runtime"
        if not stages or stages[-1]["status"] == "completed":
            failure_value = {"code": code, "message": message[:1024]}
            failure_ref = _write_stage(store, record, "failure", failure_value)
            stages.append(_stage(len(stages), "failure", "failed", failure_ref))
        identities = _identities(spec, profile, preflight, assets)
        evidence = build_measured_evidence(
            run={
                "run_id": spec["run_id"],
                "ownership_id": spec["ownership_id"],
                "suite": spec["suite"],
                "profile": spec["profile"],
                "spec_sha256": record["spec_sha256"],
            },
            identities=identities,
            stages=stages,
            completeness="failed",
            summary={"attempted": bool(stages), "completed": False},
            failure={"class": failure_class, "code": code, "message": message[:1024]},
            created_at=utc_now(),
            artifact_root=resolve_owned_run_path(
                store.run_root, ownership_id=spec["ownership_id"], run_id=spec["run_id"]
            ),
        )
        store.append_log(run_id, level="error", message=f"benchmark failed: {code}: {message}")
        return _finish_job(
            store, spec,
            "failed",
            failure={"class": failure_class, "code": code, "message": message[:1024]},
            results={"evidence": evidence, **({"secondary_failures": secondary_failures} if secondary_failures else {}),
                     **({"container_custody": swe_container_custody or {"status": "incomplete"}} if spec["suite"] == "swe" else {})},
        )


def launch_benchmark_job(
    *, path: str, run_root: str, run_id: str, popen: Any = subprocess.Popen
) -> dict[str, Any]:
    """Launch an owned detached worker without embedding credentials in argv."""
    database = os.path.realpath(path)
    root = os.path.realpath(run_root)
    store = BenchmarkJobStore(database, run_root=root)
    record = store.status(run_id)
    if record is None:
        raise BenchmarkJobError("job_not_found", "cannot launch a worker for an absent job")
    if record["state"] != "queued":
        raise BenchmarkJobError("job_already_claimed", "benchmark job is not queued")
    worker_path = resolve_owned_run_path(
        root,
        ownership_id=record["spec"]["ownership_id"],
        run_id=run_id,
        relative="worker.json",
    )
    Path(worker_path).parent.mkdir(parents=True, exist_ok=True)
    argv = [
        sys.executable,
        "-m",
        "anvil_serving.benchmarking.worker",
        "--db", database,
        "--run-root", root,
        "--run-id", run_id,
    ]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "start_new_session": os.name != "nt",
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    try:
        process = popen(argv, **kwargs)
    except OSError as exc:
        raise BenchmarkJobError(
            "worker_launch_failed", "detached benchmark worker could not be launched"
        ) from exc
    try:
        process_start = _process_start_identity(int(process.pid))
        if not isinstance(process_start, str) or not process_start:
            raise BenchmarkJobError("worker_custody_unverified", "worker process start identity is unavailable")
        atomic_write_json(worker_path, {
            "run_id": run_id,
            "pid": int(process.pid),
            "db": database,
            "run_root": root,
            "process_start": process_start,
            "process_group_id": int(process.pid),
            "session_id": int(process.pid),
            "launched_at": utc_now(),
        })
        process.stdin.write(b"1")
        process.stdin.flush()
        process.stdin.close()
    except BaseException as exc:
        # We still own the unreaped child even when no durable receipt exists.
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        finally:
            process.wait(timeout=5)
            try:
                process.stdin.close()
            except OSError:
                pass
        if not isinstance(exc, Exception):
            raise
        raise BenchmarkJobError(
            "worker_launch_failed", "detached benchmark worker custody handoff failed"
        ) from exc
    return {"launched": True, "pid": int(process.pid), "run_id": run_id}


_WORKER_HANDOFF_TIMEOUT_S = 10.0


def _await_worker_custody(store: BenchmarkJobStore, run_id: str) -> None:
    """Require the launching parent's acknowledgement and this child's receipt."""
    acknowledged = threading.Event()
    receipt = []

    def read_acknowledgement() -> None:
        try:
            receipt.append(os.read(sys.stdin.fileno(), 1))
        except (OSError, ValueError):
            pass
        finally:
            acknowledged.set()

    threading.Thread(target=read_acknowledgement, daemon=True).start()
    if not acknowledged.wait(_WORKER_HANDOFF_TIMEOUT_S) or receipt != [b"1"]:
        raise BenchmarkJobError("worker_custody_unverified", "worker custody was not acknowledged")
    record = store.status(run_id)
    if record is None:
        raise BenchmarkJobError("job_not_found", "benchmark job does not exist")
    worker_path = resolve_owned_run_path(
        store.run_root, ownership_id=record["spec"]["ownership_id"],
        run_id=run_id, relative="worker.json",
    )
    pid = os.getpid()
    process_start = _process_start_identity(pid)
    if not isinstance(process_start, str) or not process_start:
        raise BenchmarkJobError("worker_custody_unverified", "worker process start identity is unavailable")
    expected = {
        "run_id": run_id, "pid": pid, "db": os.path.realpath(store.path),
        "run_root": store.run_root, "process_start": process_start,
        "process_group_id": pid, "session_id": pid,
    }
    try:
        with open(worker_path, "rb", opener=lambda path, flags: os.open(
            path, flags | getattr(os, "O_NONBLOCK", 0)
        )) as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("worker receipt is not a regular file")
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise ValueError("worker receipt exceeds 64 KiB")
        worker = json.loads(raw)
        matches = isinstance(worker, dict) and all(worker.get(key) == value for key, value in expected.items())
        if os.name != "nt":
            matches = matches and os.getpgid(pid) == pid and os.getsid(pid) == pid
    except (OSError, ValueError, RecursionError):
        matches = False
    if not matches:
        raise BenchmarkJobError("worker_custody_unverified", "worker custody does not match this process")


def _process_start_identity(pid: int) -> str | None:
    """Return Linux's non-reusable process-start value when it is available."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return raw.rsplit(")", 1)[1].split()[19]
    except (IndexError, OSError):
        return None


def _process_arguments(pid: int) -> tuple[str, ...] | None:
    try:
        return tuple(
            value.decode(sys.getfilesystemencoding(), "surrogateescape")
            for value in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            if value
        )
    except OSError:
        return None


def _stop_owned_worker(
    worker_path: str, run_id: str, *, database: str, run_root: str
) -> dict[str, Any]:
    """Stop the dedicated worker session only after proving its identity."""
    try:
        worker = json.loads(Path(worker_path).read_text(encoding="utf-8"))
        pid = worker.get("pid")
    except FileNotFoundError:
        return {"stopped": False, "status": "worker_record_missing"}
    except (OSError, ValueError):
        return {"stopped": False, "status": "worker_record_unreadable"}
    if (
        worker.get("run_id") != run_id
        or not isinstance(pid, int)
        or pid <= 1
        or not all(isinstance(worker.get(key), str) and worker[key] for key in ("db", "run_root", "process_start"))
        or worker.get("process_group_id") != pid
        or worker.get("session_id") != pid
        or worker["db"] != database
        or worker["run_root"] != run_root
    ):
        return {"stopped": False, "status": "unverified_identity"}
    if os.name == "nt":
        return {"stopped": False, "status": "unsupported_platform", "pid": pid}
    try:
        arguments = _process_arguments(pid)
        if arguments is None:
            # Another deadline supervisor may already have stopped this group.
            if _worker_group_stopped(worker_path, run_id=run_id, database=database, run_root=run_root):
                return {"stopped": True, "status": "already_stopped", "pid": pid}
            return {"stopped": False, "status": "leader_missing_unverified", "pid": pid}
        expected = (
            sys.executable,
            "-m",
            "anvil_serving.benchmarking.worker",
            "--db",
            worker["db"],
            "--run-root",
            worker["run_root"],
            "--run-id",
            run_id,
        )
        if (
            arguments != expected
            or _process_start_identity(pid) != worker["process_start"]
            or os.getpgid(pid) != pid
            or os.getsid(pid) != pid
        ):
            return {"stopped": False, "status": "unverified_identity", "pid": pid}
        os.killpg(pid, signal.SIGTERM)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not _process_group_running(pid):
                return {"stopped": True, "status": "terminated", "pid": pid}
            time.sleep(0.05)
        os.killpg(pid, signal.SIGKILL)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not _process_group_running(pid):
                return {"stopped": True, "status": "killed", "pid": pid}
            time.sleep(0.05)
    except ProcessLookupError:
        # A missing leader alone does not prove its descendants have stopped.
        try:
            stopped = _worker_group_stopped(worker_path, run_id=run_id, database=database, run_root=run_root)
        except (OSError, subprocess.SubprocessError):
            stopped = False
        return {"stopped": stopped, "status": "already_stopped" if stopped else "leader_missing_unverified", "pid": pid}
    except (OSError, subprocess.SubprocessError):
        return {"stopped": False, "status": "termination_error", "pid": pid}
    return {"stopped": False, "status": "termination_unverified", "pid": pid}


def _process_group_running(group_id: int) -> bool:
    """Report live, rather than merely unreaped, members of one worker group."""
    observed = subprocess.run(
        ("ps", "-eo", "pgid=,stat="),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=5,
        check=False,
    )
    if observed.returncode != 0:
        raise OSError("could not verify worker process group")
    for line in observed.stdout.splitlines():
        fields = line.split(None, 1)
        if len(fields) == 2 and fields[0].isdigit() and int(fields[0]) == group_id:
            if not fields[1].lstrip().startswith("Z"):
                return True
    return False


_MAX_PARTIAL_OUTPUT_BYTES = 64 * 1024 * 1024
_MAX_PARTIAL_OUTPUT_FILES = 512


def _retain_native_partial_outputs(*, run_path: str, work_path: str) -> dict[str, Any]:
    """Preserve the legacy non-SWE cancellation artifact contract."""
    source = os.path.join(work_path, "mini-output")
    if not os.path.exists(source):
        return {"state": "absent", "source": "work/mini-output", "files": [], "bytes": 0}
    if not os.path.isdir(source) or os.path.islink(source):
        return {"state": "failed", "reason": "native_output_is_not_a_directory"}
    evidence_dir, destination = os.path.join(run_path, "evidence"), os.path.join(run_path, "evidence", "native-partial")
    Path(evidence_dir).mkdir(parents=True, exist_ok=True)
    if os.path.exists(destination):
        return {"state": "failed", "reason": "native_output_destination_exists"}
    staging, files, total = tempfile.mkdtemp(prefix=".native-partial-", dir=evidence_dir), [], 0
    try:
        for current, directories, names in os.walk(source, followlinks=False):
            directories.sort(); names.sort()
            if any(os.path.islink(os.path.join(current, name)) for name in directories):
                return {"state": "failed", "reason": "native_output_contains_symlink"}
            for name in names:
                original = os.path.join(current, name)
                if os.path.islink(original) or not os.path.isfile(original):
                    return {"state": "failed", "reason": "native_output_contains_nonregular_file"}
                size = os.path.getsize(original)
                if len(files) >= _MAX_PARTIAL_OUTPUT_FILES or total + size > _MAX_PARTIAL_OUTPUT_BYTES:
                    return {"state": "failed", "reason": "native_output_exceeds_retention_bound"}
                copied = os.path.join(staging, os.path.relpath(original, source))
                Path(copied).parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(original, copied)
                files.append({"path": os.path.relpath(copied, staging).replace("\\", "/"), **evidence_file_reference(copied, root=staging)})
                total += size
        os.replace(staging, destination)
        return {"state": "retained", "source": "work/mini-output", "files": files, "bytes": total}
    finally:
        if os.path.isdir(staging): shutil.rmtree(staging, ignore_errors=True)


def _cancellation_evidence(
    record: Mapping[str, Any], *, run_path: str, retention: Mapping[str, Any], termination: Mapping[str, Any]
) -> dict[str, Any]:
    spec = record["spec"]
    if spec["suite"] in {"aider", "performance"}:
        suite = spec["suite"]
        manifest_path = os.path.join(run_path, "evidence", f"cancellation-{suite}.json")
        Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(manifest_path, {"schema": f"anvil-serving.{suite}-cancellation/v1", "retention": retention, "termination": termination})
        retained = retention["state"] in {"retained", "incomplete"} or (suite == "aider" and "campaign" in spec and retention["state"] == "absent")
        return build_measured_evidence(
            run={"run_id": spec["run_id"], "ownership_id": spec["ownership_id"], "suite": suite, "profile": spec["profile"], "spec_sha256": record["spec_sha256"]},
            identities=_identities(spec, {}, None, None),
            stages=[_stage(0, f"{suite}_retention", "incomplete" if retained else "failed", evidence_file_reference(manifest_path, root=run_path))],
            completeness="cancelled", summary={"completed": False, f"{suite}_retention": dict(retention)},
            failure={"class": "cancellation", "code": ("aider_container_cleanup_unverified" if suite == "aider" and retention.get("reason") == "aider_container_cleanup_unverified"
                else "cancelled" if retained else "partial_artifact_retention_failed"), "message": f"{suite} cancellation preserves canonical files outside disposable work"},
            created_at=utc_now(), artifact_root=run_path,
        )
    if spec["suite"] != "swe":
        manifest_path = os.path.join(run_path, "evidence", "cancellation-native-output.json")
        Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(manifest_path, {"schema": "anvil-serving.native-partial-output/v1", "retention": retention, "termination": termination})
        status = "completed" if retention["state"] == "retained" else "incomplete" if retention["state"] == "absent" else "failed"
        failure_code = "cancelled" if retention["state"] in {"retained", "absent"} else "partial_artifact_retention_failed"
        return build_measured_evidence(run={"run_id": spec["run_id"], "ownership_id": spec["ownership_id"], "suite": spec["suite"], "profile": spec["profile"], "spec_sha256": record["spec_sha256"]}, identities={key: {"status": "unavailable", "reason": "cancellation before collection"} for key in ("model", "served_model", "runtime", "image", "hardware", "topology", "context", "concurrency", "harnesses", "dataset")}, stages=[_stage(0, "native_partial_outputs", status, evidence_file_reference(manifest_path, root=run_path))], completeness="cancelled", summary={"completed": False, "native_partial_output": dict(retention)}, failure={"class": "cancellation", "code": failure_code, "message": "benchmark cancellation retained native partial output" if failure_code == "cancelled" else "benchmark cancellation retained the work directory because native output retention failed"}, created_at=utc_now(), artifact_root=run_path)
    manifest_path = os.path.join(run_path, "evidence", "cancellation-swe-canonical.json")
    Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(manifest_path, {"schema": "anvil-serving.swe-canonical-cancellation/v1", "retention": retention, "termination": termination})
    status = "completed" if retention["state"] == "retained" else "incomplete" if retention["state"] in {"incomplete", "absent"} else "failed"
    cleanup_failed = retention.get("reason") == "swe_container_cleanup_unverified"
    failure_code = ("swe_container_cleanup_unverified" if cleanup_failed else "cancelled"
                    if retention["state"] in {"retained", "incomplete", "absent"} else "partial_artifact_retention_failed")
    failure_message = ("benchmark cancellation requires owned SWE container cleanup attention" if cleanup_failed else
                       "benchmark cancellation stopped before SWE work began" if retention["state"] == "absent" else
                       "benchmark cancellation retained canonical SWE output" if failure_code == "cancelled" else
                       "benchmark cancellation retained the work directory because canonical SWE retention failed")
    identities = {
        "model": {"alias": spec["endpoint"]["model"]},
        "served_model": {"id": spec["endpoint"]["model"], "status": "unavailable"},
        "runtime": {"status": "unavailable", "reason": "cancellation before collection"},
        "image": {"status": "unavailable", "reason": "cancellation before collection"},
        "hardware": {"worker_id": spec["worker"]["id"]},
        "topology": {"kind": "isolated-benchmark-worker", "worker_id": spec["worker"]["id"]},
        "context": {"status": "unavailable", "reason": "cancellation before collection"},
        "concurrency": {"status": "unavailable", "reason": "cancellation before collection"},
        "harnesses": {"status": "unavailable", "reason": "cancellation before collection"},
        "dataset": {"status": "unavailable", "reason": "cancellation before collection"},
    }
    return build_measured_evidence(
        run={"run_id": spec["run_id"], "ownership_id": spec["ownership_id"], "suite": spec["suite"], "profile": spec["profile"], "spec_sha256": record["spec_sha256"]},
        identities=identities,
        stages=[_stage(0, "swe_canonical_retention", status, evidence_file_reference(manifest_path, root=run_path))],
        completeness="cancelled",
        summary={"completed": False, "swe_canonical_retention": dict(retention)},
        failure={"class": "cancellation", "code": failure_code, "message": failure_message},
        created_at=utc_now(),
        artifact_root=run_path,
    )


def _prepare_cancellation(store, run_id):
    """Stop and retain one child without waiting for shared drain or terminalizing."""
    record = store.status(run_id)
    if record is None:
        raise BenchmarkJobError('job_not_found', 'benchmark job does not exist')
    if record['state'] in {'completed', 'failed', 'cancelled'}:
        return None
    worker_path = resolve_owned_run_path(
        store.run_root,
        ownership_id=record["spec"]["ownership_id"],
        run_id=run_id,
        relative="worker.json",
    )
    termination = _stop_owned_worker(
        worker_path,
        run_id,
        database=os.path.realpath(store.path),
        run_root=store.run_root,
    )
    run_path = resolve_owned_run_path(
        store.run_root, ownership_id=record["spec"]["ownership_id"], run_id=run_id
    )
    work_path = resolve_owned_run_path(
        store.run_root, ownership_id=record["spec"]["ownership_id"], run_id=run_id, relative="work"
    )
    try:
        aider_proof = (record["spec"]["suite"] == "aider" and "campaign" in record["spec"]
                       and _aider_campaign_requires_stop(store, record["spec"]))
        if termination["stopped"]:
            if record["spec"]["suite"] in {"aider", "performance"}:
                if aider_proof:
                    receipt_path = os.path.join(run_path, "evidence", "worker-stop-receipt.json")
                    Path(receipt_path).parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_json(receipt_path, {"schema": "anvil-serving.worker-stop-receipt/v1", "run_id": run_id, "termination": termination})
                retention = _retain_external_artifacts(record, run_path=run_path)
            elif record["spec"]["suite"] == "swe":
                receipt_path = os.path.join(run_path, "evidence", "worker-stop-receipt.json")
                Path(receipt_path).parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(receipt_path, {"schema": "anvil-serving.worker-stop-receipt/v1", "run_id": run_id, "termination": termination})
                retention = _retain_stopped_swe(record, run_path=run_path, work_path=work_path, phase="cancel")
            else:
                retention = _retain_native_partial_outputs(run_path=run_path, work_path=work_path)
        else:
            retention = {"state": "failed", "reason": "worker_termination_unverified"}
        if "campaign" in record["spec"]:
            from .campaign_execution import close_cancelled_campaign
            suite = record["spec"]["suite"]
            custody_verified = termination["stopped"] and (_swe_retention_complete(retention, run_path) if suite == "swe" else
                _aider_retention_complete(retention, run_path, record["spec"]) if aider_proof else retention["state"] != "failed")
            stop_reference = (_swe_campaign_stop_reference(store, record, retention, run_path, cancelling=True) if suite == "swe" else
                              _aider_campaign_stop_reference(store, record, retention, run_path, cancelling=True) if aider_proof else None) if custody_verified else None
            close_cancelled_campaign(store, record["spec"], clients_stopped=custody_verified,
                                     **({"swe_stop": stop_reference} if suite == "swe" else {"aider_stop": stop_reference} if aider_proof else {}))
    except (BenchmarkArtifactError, BenchmarkJobError, OSError, ValueError):
        retention = {'state': 'failed', 'reason': 'cancellation_retention_unavailable'}
    return {'record': record, 'termination': termination, 'retention': retention,
            'run_path': run_path, 'work_path': work_path}


def _finish_cancellation(store, prepared):
    record, termination, retention, run_path = (prepared[k] for k in ('record', 'termination', 'retention', 'run_path'))
    run_id = record['spec']['run_id']
    try:
        evidence = _cancellation_evidence(
            record, run_path=run_path, retention=retention, termination=termination
        )
        failure = evidence["failure"]
        results = {'evidence': evidence}
        if record['spec']['suite'] == 'swe':
            results['container_custody'] = retention.get('containers', {'status': 'incomplete'})
        if 'campaign' in record['spec']:
            path = Path(run_path) / 'campaign-execution.json'
            if path.exists():
                results['campaign_execution'] = evidence_file_reference(str(path), root=run_path)
    except (BenchmarkArtifactError, BenchmarkJobError, OSError, ValueError):
        retention = {"state": "failed", "reason": "cancellation_artifact_unavailable"}
        failure = {
            "class": "cancellation",
            "code": "cancellation_artifact_unavailable",
            "message": "benchmark cancellation could not retain its artifact",
        }
        results = {"cancellation": {"retention": retention, "termination": termination}}

    def cleanup(work_path: str) -> None:
        allowed = {"retained", "incomplete"} if record["spec"]["suite"] in {"swe", "aider", "performance"} else {"retained", "absent"}
        if (termination["stopped"] and retention["state"] in allowed and os.path.isdir(work_path)
                and (record["spec"]["suite"] != "swe" or "campaign" not in record["spec"]
                     or _swe_retention_complete(retention, run_path))
                and (record["spec"]["suite"] != "aider" or "campaign" not in record["spec"]
                     or not _aider_campaign_requires_stop(store, record["spec"])
                     or _aider_retention_complete(retention, run_path, record["spec"]))):
            shutil.rmtree(work_path)

    cancelled = store.cancel(run_id, cleanup=cleanup, results=results, failure=failure)
    cancelled = dict(cancelled)
    if record["spec"]["suite"] == "swe":
        cancelled["container_custody"] = retention.get("containers", {"status": "incomplete"})
    artifact_retention_failed = (
        cancelled.get("failure", {}).get("code") == "cancellation_artifact_unavailable"
    )
    cancelled["worker_terminated"] = termination["stopped"]
    cancelled["cleanup_required"] = record["spec"]["suite"] == "swe" and (
        retention.get("containers", {}).get("status") != "completed"
    )
    cancelled["cleanup_deferred"] = artifact_retention_failed or not (
        termination["stopped"] and (retention["state"] in ({"retained", "incomplete"} if record["spec"]["suite"] in {"swe", "aider", "performance"} else {"retained", "absent"}) or
                                    (record["spec"]["suite"] == "swe" and retention["state"] == "absent"))
    )
    if record["spec"]["suite"] == "swe" and "campaign" in record["spec"]:
        cancelled["cleanup_deferred"] = (artifact_retention_failed or not termination["stopped"]
                                           or not _swe_retention_complete(retention, run_path))
    if (record["spec"]["suite"] == "aider" and "campaign" in record["spec"]
            and _aider_campaign_requires_stop(store, record["spec"])):
        verified = _aider_retention_complete(retention, run_path, record["spec"])
        cancelled["cleanup_required"] = not verified
        cancelled["cleanup_deferred"] = artifact_retention_failed or not termination["stopped"] or not verified
    return cancelled



def cancel_campaign_jobs(manifest, store):
    """Single owner: stop every sibling, close batches, then commit artifacts."""
    from . import campaign_execution as execution
    directory = execution._directory(manifest, store.run_root)
    with execution.campaign_lock(directory):
        state = execution._state(directory, manifest=manifest)
        state['cancel_requested'] = True
        atomic_write_json(directory / 'state.json', state)
    try:
        with execution.campaign_lock(directory / 'cancellation-lock', timeout_s=0):
            phase_path = directory / 'cancellation.json'
            phase = {'manifest_sha256': manifest['manifest_sha256'], 'phase': 'stopping'}
            atomic_write_json(phase_path, phase)
            prepared = []
            for cell in manifest['cells']:
                spec = execution.bind_campaign_child(cell['spec'], manifest_sha256=manifest['manifest_sha256'],
                    campaign_id=manifest['campaign_id'], cell_id=cell['cell_id'], arm_id=cell['arm_id'], batch_id=cell['batch_id'])
                with child_lock(store, spec):
                    record = store.status(spec['run_id'])
                    if record is None:
                        continue
                    if record['spec'] != spec:
                        raise BenchmarkJobError('campaign_child_mismatch', 'cancellation child differs from frozen manifest')
                    if record['state'] in execution.TERMINAL:
                        continue
                    if record['state'] == 'running':
                        store.transition(spec['run_id'], 'cancelling')
                # The state gate prevents worker commits, but do not hold its
                # write lock while waiting for process termination.
                item = _prepare_cancellation(store, spec['run_id'])
                if item is not None:
                    prepared.append(item)
            phase['phase'] = 'draining'
            atomic_write_json(phase_path, phase)
            for batch_id in dict.fromkeys(i['record']['spec']['campaign']['batch_id'] for i in prepared):
                # Another coordinator may already own the bounded drain. Its
                # closure is awaited by stopped children below, never duplicated.
                execution.coordinate_batch(manifest, batch_id, store=store, force=True)
            children = []
            for item in prepared:
                spec = item['record']['spec']
                if not item['termination']['stopped']:
                    children.append({**store.status(spec['run_id']), 'worker_terminated': False,
                                     'cleanup_deferred': True})
                    continue  # Mutable writer custody is unknown; do not anchor it.
                campaign_result_reference(store, spec, cancelling=True)
                with child_lock(store, spec):
                    if store.status(spec['run_id'])['state'] in execution.TERMINAL:
                        children.append(store.status(spec['run_id']))
                    else:
                        # Receipt has already awaited closure; this sequence is
                        # single-owned through retention, evidence and artifact.
                        children.append(_finish_cancellation(store, item))
            phase['phase'] = 'completed' if all(c['state'] in execution.TERMINAL for c in children) else 'stopping_unverified'
            atomic_write_json(phase_path, phase)
            return {'campaign_id': manifest['campaign_id'], 'cancel_requested': True, 'phase': phase['phase'], 'children': children}
    except BenchmarkJobError as exc:
        if exc.code != 'campaign_lock_timeout':
            raise
        return {'campaign_id': manifest['campaign_id'], 'cancel_requested': True,
                'phase': 'in_progress', 'children': []}


def _terminal_swe_container_cleanup(store, record):
    spec = record["spec"]
    run_path = resolve_owned_run_path(store.run_root, ownership_id=spec["ownership_id"], run_id=spec["run_id"])
    worker_path = os.path.join(run_path, "worker.json")
    stopped = _worker_group_stopped(
        worker_path, run_id=spec["run_id"], database=os.path.realpath(store.path), run_root=store.run_root,
    )
    if not stopped:
        return {"status": "incomplete", "reason": "worker_stop_unverified", "containers": [], "errors": []}
    return _swe_container_cleanup(
        record, run_path=run_path, phase=f"repeat-cancel-r{record['revision']}",
    )


def _persist_terminal_swe_custody(store, record, custody: Mapping[str, Any]) -> dict[str, Any]:
    """Make the latest terminal custody receipt authoritative without losing prior receipts."""
    artifact = store.artifact(record["spec"]["run_id"])
    if not isinstance(artifact, Mapping) or not isinstance(artifact.get("results"), Mapping):
        raise BenchmarkJobError("artifact_unavailable", "terminal SWE custody result is unavailable")
    results = dict(artifact["results"])
    prior = results.get("container_custody")
    history = results.get("container_custody_history", [])
    if not isinstance(history, list) or not all(isinstance(entry, Mapping) for entry in history):
        raise BenchmarkJobError("bad_job_artifact", "terminal SWE custody history is invalid")
    if isinstance(prior, Mapping):
        history = [*history[-15:], {
            "status": prior.get("status", "incomplete"),
            "receipt": prior.get("receipt"),
        }]
    return store.replace_terminal_results(
        record["spec"]["run_id"],
        results={**results, "container_custody": dict(custody), "container_custody_history": history},
    )


def cancel_benchmark_job(store: BenchmarkJobStore, run_id: str) -> dict[str, Any]:
    record = store.status(run_id)
    if record is None:
        raise BenchmarkJobError('job_not_found', 'benchmark job does not exist')
    if record['state'] in {'completed', 'failed', 'cancelled'}:
        if record['spec']['suite'] != 'swe':
            return record
        result = dict(record)
        custody = _terminal_swe_container_cleanup(store, record)
        try:
            result = dict(_persist_terminal_swe_custody(store, record, custody))
        except (BenchmarkArtifactError, BenchmarkJobError, OSError, ValueError):
            custody = {
                "status": "incomplete", "reason": "custody_result_persistence_unverified",
                "containers": [], "errors": [],
            }
        result['container_custody'] = custody
        result['cleanup_required'] = custody.get('status') != 'completed'
        result['cleanup_deferred'] = result['cleanup_required']
        return result
    if 'campaign' in record['spec']:
        from .campaign_execution import _path, _read
        spec = record['spec']
        manifest = _read(_path({'ownership_id': spec['ownership_id'], 'run_id': spec['campaign']['campaign_id']},
                              store.run_root, 'campaign/manifest.json'))
        result = cancel_campaign_jobs(manifest, store)
        return next((c for c in result['children'] if c['spec']['run_id'] == run_id), store.status(run_id))
    return _finish_cancellation(store, _prepare_cancellation(store, run_id))

def _worker_group_stopped(worker_path: str, *, run_id: str, database: str, run_root: str) -> bool:
    """Verify a stored worker identity has no surviving process group."""
    try:
        worker = json.loads(Path(worker_path).read_text(encoding="utf-8"))
        pid = worker["pid"]
    except (KeyError, OSError, ValueError, TypeError):
        return False
    if (not isinstance(pid, int) or pid <= 1 or worker.get("run_id") != run_id
            or worker.get("db") != database or worker.get("run_root") != run_root
            or not isinstance(worker.get("process_start"), str) or not worker["process_start"]
            or worker.get("process_group_id") != pid or worker.get("session_id") != pid):
        return False
    arguments = _process_arguments(pid)
    if arguments is not None:
        return False  # A live or reused PID is never cleanup proof.
    try:
        os.getpgid(pid)
        return False
    except ProcessLookupError:
        try:
            return not _process_group_running(pid)
        except (OSError, subprocess.SubprocessError):
            return False


def cleanup_benchmark_job(store: BenchmarkJobStore, run_id: str) -> dict[str, Any]:
    """Remove disposable work only after worker and retained-evidence proof."""
    record = store.status(run_id)
    if record is None:
        raise BenchmarkJobError("job_not_found", "benchmark job does not exist")
    if record["state"] not in {"completed", "failed", "cancelled"}:
        raise BenchmarkJobError("cleanup_not_terminal", "benchmark cleanup requires a terminal job")
    spec = record["spec"]
    run_path = resolve_owned_run_path(store.run_root, ownership_id=spec["ownership_id"], run_id=run_id)
    worker_path = os.path.join(run_path, "worker.json")
    if record["state"] == "cancelled" and spec["suite"] == "swe":
        try:
            receipt = json.loads(Path(os.path.join(run_path, "evidence", "worker-stop-receipt.json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise BenchmarkJobError("cleanup_stop_unverified", "cancelled job has no verified worker stop receipt") from None
        if receipt.get("run_id") != run_id or not receipt.get("termination", {}).get("stopped"):
            raise BenchmarkJobError("cleanup_stop_unverified", "cancelled job worker stop receipt is invalid")
    if not _worker_group_stopped(worker_path, run_id=run_id, database=os.path.realpath(store.path), run_root=store.run_root):
        raise BenchmarkJobError("cleanup_worker_unverified", "owned benchmark worker group is not proven stopped")
    work_path = os.path.join(run_path, "work")
    if spec["suite"] in {"aider", "performance"}:
        retention = _retain_external_artifacts(record, run_path=run_path)
        if retention["state"] == "failed":
            raise BenchmarkJobError(f"{spec['suite']}_artifact_retention_failed", f"{spec['suite']} canonical artifacts could not be verified")
    elif spec["suite"] == "swe":
        cleanup = _swe_container_cleanup(record, run_path=run_path, phase="cleanup")
        if cleanup["status"] != "completed":
            raise BenchmarkJobError("swe_container_cleanup_unverified", "SWE container cleanup could not be verified")
        from .swe_regrade import _terminal_manifest_reference
        anchored = _terminal_manifest_reference(store, record)
        try:
            manifest = read_retained_swe_artifacts(run_path=run_path)
            current = evidence_file_reference(os.path.join(run_path, "evidence", "swe-canonical", "manifest.json"), root=run_path)
        except (OSError, ValueError) as exc:
            raise BenchmarkJobError("swe_artifact_retention_failed", "SWE canonical artifacts could not be verified") from exc
        if dict(anchored) != current:
            raise BenchmarkJobError("swe_canonical_evidence_mismatch", "SWE canonical retention is not bound by terminal evidence")
        retention = {"state": manifest["state"], "manifest": manifest, "manifest_reference": current, "reused": True}
    else:
        retention = _retain_native_partial_outputs(run_path=run_path, work_path=work_path)
        if retention["state"] == "failed":
            raise BenchmarkJobError("native_artifact_retention_failed", "native partial output could not be retained")
    existed = os.path.isdir(work_path)
    if existed:
        shutil.rmtree(work_path)
    return {"cleaned": existed, "scope": "owned-work", "retention": retention}


def _campaign_watchdog(database: str, run_root: str, run_id: str) -> None:
    """Run the campaign supervisor with the worker's exact custody operations."""
    from .campaign_execution import _watchdog
    _watchdog(database, run_root, run_id, cancel_job=cancel_benchmark_job,
              stop_worker=_stop_owned_worker)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anvil-benchmark-worker")
    parser.add_argument("--campaign-watchdog", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--db", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--run-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.campaign_watchdog:
            _campaign_watchdog(args.db, args.run_root, args.run_id)
            return 0
        store = _store(args.db, args.run_root)
        _await_worker_custody(store, args.run_id)
        execute_benchmark_job(store, args.run_id)
    except BenchmarkJobError as exc:
        if exc.code != "job_already_claimed":
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
