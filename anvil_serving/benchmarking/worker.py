"""Detached benchmark worker entry point for durable unattended jobs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping

from .artifacts import (
    BenchmarkArtifactError,
    atomic_write_json,
    build_measured_evidence,
    evidence_file_reference,
    normalize_evidence_failure_class,
)
from .harnesses import prepare_harness_assets
from .jobs import BenchmarkJobError, resolve_owned_run_path, utc_now
from .preflight import require_benchmark_preflight, run_benchmark_preflight
from .profiles import load_profile
from .suite_runner import run_agentic_suite, run_context_suite
from .swe import build_swe_run_plan, run_swe_benchmark
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
    suite = profile["suites"][spec["suite"]]
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
        "concurrency": declared_or("concurrency", {"requests": 1, "workers": 1}),
        "harnesses": declared_or(
            "harnesses",
            {
                name: {
                    key: value
                    for key, value in profile["adapters"][name].items()
                    if key in {"source", "revision", "image"}
                }
                for name in suite["adapters"]
            } or {"native": {"schema": f"anvil-serving.{spec['suite']}/v1"}},
        ),
        "dataset": declared_or(
            "dataset",
            {"name": "SWE-bench_Verified", "split": "test"}
            if spec["suite"] == "swe"
            else {"name": "deterministic-native", "profile_sha256": profile["content_sha256"]},
        ),
    }


def _stage(sequence: int, name: str, status: str, reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "name": name,
        "status": status,
        "evidence": [reference],
    }


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
    instance_ids = spec.get("parameters", {}).get("instance_ids")
    parameters = spec.get("parameters", {})
    cache_root = os.environ.get(
        "ANVIL_BENCHMARK_CACHE_ROOT",
        os.path.expanduser("~/.anvil-serving/benchmark-harness-cache"),
    )
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
            for key in ("thinking_mode", "reasoning_effort", "temperature", "top_p")
            if key in parameters
        },
    )
    return run_swe_benchmark(plan)


def execute_benchmark_job(store: BenchmarkJobStore, run_id: str) -> dict[str, Any]:
    """Claim and execute one job, retaining evidence at each completed stage."""
    record = store.claim(run_id)
    spec = record["spec"]
    store.append_log(run_id, level="info", message="benchmark worker claimed job")
    profile = load_profile(spec["profile"])
    stages: list[dict[str, Any]] = []
    assets = None
    preflight = None
    try:
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
        )
        stages.append(_stage(0, "asset_preparation", "completed", _write_stage(store, record, "assets", assets)))
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
        stages.append(_stage(1, "preflight", preflight_status, preflight_ref))
        require_benchmark_preflight(preflight)
        if store.status(run_id)["state"] != "running":
            return store.status(run_id)

        store.append_log(run_id, level="info", message=f"executing {spec['suite']} suite")
        suite_result = _run_suite(store, record, profile, assets)
        if store.status(run_id)["state"] != "running":
            return store.status(run_id)
        suite_ref = _write_stage(store, record, spec["suite"], suite_result)
        suite_complete = suite_result.get("state", "completed") == "completed"
        stages.append(_stage(2, spec["suite"], "completed" if suite_complete else "failed", suite_ref))
        if not suite_complete:
            failure = suite_result.get("failure") or {"class": "harness", "message": "suite incomplete"}
            raise BenchmarkJobError(
                failure.get("class", "harness"),
                failure.get("message", "benchmark suite did not complete"),
            )
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
        return store.transition(run_id, "completed", results={"evidence": evidence})
    except Exception as exc:
        current = store.status(run_id)
        if current is not None and current["state"] in {"cancelled", "cancelling"}:
            return current
        code = exc.code if isinstance(exc, BenchmarkJobError) else type(exc).__name__
        message = exc.message if isinstance(exc, BenchmarkJobError) else str(exc)
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
        return store.transition(
            run_id,
            "failed",
            failure={"class": failure_class, "code": code, "message": message[:1024]},
            results={"evidence": evidence},
        )


def launch_benchmark_job(
    *, path: str, run_root: str, run_id: str, popen: Any = subprocess.Popen
) -> dict[str, Any]:
    """Launch an owned detached worker without embedding credentials in argv."""
    argv = [
        sys.executable,
        "-m",
        "anvil_serving.benchmarking.worker",
        "--db", path,
        "--run-root", run_root,
        "--run-id", run_id,
    ]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
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
    store = BenchmarkJobStore(path, run_root=run_root)
    record = store.status(run_id)
    if record is None:
        raise BenchmarkJobError("job_not_found", "cannot register a worker for an absent job")
    worker_path = resolve_owned_run_path(
        run_root,
        ownership_id=record["spec"]["ownership_id"],
        run_id=run_id,
        relative="worker.json",
    )
    Path(worker_path).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(worker_path, {"run_id": run_id, "pid": int(process.pid), "launched_at": utc_now()})
    return {"launched": True, "pid": int(process.pid), "run_id": run_id}


_MAX_PARTIAL_OUTPUT_BYTES = 64 * 1024 * 1024
_MAX_PARTIAL_OUTPUT_FILES = 512


def _stop_owned_worker(worker_path: str, run_id: str) -> dict[str, Any]:
    """Stop the dedicated worker session only after proving its identity."""
    try:
        worker = json.loads(Path(worker_path).read_text(encoding="utf-8"))
        pid = worker.get("pid")
    except FileNotFoundError:
        return {"stopped": False, "status": "worker_record_missing"}
    except (OSError, ValueError):
        return {"stopped": False, "status": "worker_record_unreadable"}
    if worker.get("run_id") != run_id or not isinstance(pid, int) or pid <= 1:
        return {"stopped": False, "status": "unverified_identity"}
    if os.name == "nt":
        return {"stopped": False, "status": "unsupported_platform", "pid": pid}
    try:
        observed = subprocess.run(
            ("ps", "-p", str(pid), "-o", "command="),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
        command = observed.stdout
        if observed.returncode != 0:
            return {"stopped": True, "status": "already_stopped", "pid": pid}
        if (
            "anvil_serving.benchmarking.worker" not in command
            or run_id not in command
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
        return {"stopped": True, "status": "already_stopped", "pid": pid}
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


def _retain_native_partial_outputs(
    *, run_path: str, work_path: str
) -> dict[str, Any]:
    """Copy only bounded regular mini-SWE output into retained evidence."""
    source = os.path.join(work_path, "mini-output")
    if not os.path.exists(source):
        return {"state": "absent", "source": "work/mini-output", "files": [], "bytes": 0}
    if not os.path.isdir(source) or os.path.islink(source):
        return {"state": "failed", "reason": "native_output_is_not_a_directory"}
    evidence_dir = os.path.join(run_path, "evidence")
    destination = os.path.join(evidence_dir, "native-partial")
    Path(evidence_dir).mkdir(parents=True, exist_ok=True)
    if os.path.exists(destination):
        return {"state": "failed", "reason": "native_output_destination_exists"}
    staging = tempfile.mkdtemp(prefix=".native-partial-", dir=evidence_dir)
    files: list[dict[str, Any]] = []
    total = 0
    try:
        for current, directories, names in os.walk(source, followlinks=False):
            directories.sort()
            names.sort()
            if any(os.path.islink(os.path.join(current, name)) for name in directories):
                return {"state": "failed", "reason": "native_output_contains_symlink"}
            for name in names:
                original = os.path.join(current, name)
                relative = os.path.relpath(original, source)
                if os.path.islink(original) or not os.path.isfile(original):
                    return {"state": "failed", "reason": "native_output_contains_nonregular_file"}
                size = os.path.getsize(original)
                if len(files) >= _MAX_PARTIAL_OUTPUT_FILES or total + size > _MAX_PARTIAL_OUTPUT_BYTES:
                    return {"state": "failed", "reason": "native_output_exceeds_retention_bound"}
                copied = os.path.join(staging, relative)
                Path(copied).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, copied)
                files.append({"path": relative.replace("\\", "/"), **evidence_file_reference(copied, root=staging)})
                total += size
        os.replace(staging, destination)
        return {"state": "retained", "source": "work/mini-output", "files": files, "bytes": total}
    finally:
        if os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)


def _cancellation_evidence(
    record: Mapping[str, Any], *, run_path: str, retention: Mapping[str, Any], termination: Mapping[str, Any]
) -> dict[str, Any]:
    spec = record["spec"]
    manifest_path = os.path.join(run_path, "evidence", "cancellation-native-output.json")
    Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(manifest_path, {"schema": "anvil-serving.native-partial-output/v1", "retention": retention, "termination": termination})
    status = "completed" if retention["state"] == "retained" else "incomplete" if retention["state"] == "absent" else "failed"
    failure_code = "cancelled" if retention["state"] in {"retained", "absent"} else "partial_artifact_retention_failed"
    failure_message = "benchmark cancellation retained native partial output" if failure_code == "cancelled" else "benchmark cancellation retained the work directory because native output retention failed"
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
        stages=[_stage(0, "native_partial_outputs", status, evidence_file_reference(manifest_path, root=run_path))],
        completeness="cancelled",
        summary={"completed": False, "native_partial_output": dict(retention)},
        failure={"class": "cancellation", "code": failure_code, "message": failure_message},
        created_at=utc_now(),
        artifact_root=run_path,
    )


def cancel_benchmark_job(store: BenchmarkJobStore, run_id: str) -> dict[str, Any]:
    """Retain owned partial output, then cancel and clean up a detached worker."""
    record = store.status(run_id)
    if record is None:
        raise BenchmarkJobError("job_not_found", "benchmark job does not exist")
    if record["state"] in {"completed", "failed", "cancelled"}:
        return record
    worker_path = resolve_owned_run_path(
        store.run_root,
        ownership_id=record["spec"]["ownership_id"],
        run_id=run_id,
        relative="worker.json",
    )
    termination = _stop_owned_worker(worker_path, run_id)
    run_path = resolve_owned_run_path(
        store.run_root, ownership_id=record["spec"]["ownership_id"], run_id=run_id
    )
    work_path = resolve_owned_run_path(
        store.run_root, ownership_id=record["spec"]["ownership_id"], run_id=run_id, relative="work"
    )
    retention = (
        _retain_native_partial_outputs(run_path=run_path, work_path=work_path)
        if termination["stopped"]
        else {"state": "failed", "reason": "worker_termination_unverified"}
    )
    evidence = _cancellation_evidence(record, run_path=run_path, retention=retention, termination=termination)
    failure = evidence["failure"]

    def cleanup(work_path: str) -> None:
        if termination["stopped"] and retention["state"] in {"retained", "absent"} and os.path.isdir(work_path):
            shutil.rmtree(work_path)

    cancelled = store.cancel(run_id, cleanup=cleanup, results={"evidence": evidence}, failure=failure)
    cancelled = dict(cancelled)
    cancelled["worker_terminated"] = termination["stopped"]
    cancelled["cleanup_deferred"] = not (termination["stopped"] and retention["state"] in {"retained", "absent"})
    return cancelled


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anvil-benchmark-worker")
    parser.add_argument("--db", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--run-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        execute_benchmark_job(_store(args.db, args.run_root), args.run_id)
    except BenchmarkJobError as exc:
        if exc.code != "job_already_claimed":
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
