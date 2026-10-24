"""Replay only the pinned official SWE grader against retained predictions."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping

from .artifacts import atomic_write_json, real_path
from .jobs import BenchmarkJobError, canonical_json_bytes, resolve_owned_run_path, utc_now
from .evidence_reader import read_referenced_job_evidence
from .retention import SWE_CANONICAL_RETENTION_SCHEMA, read_retained_swe_artifacts
from .retention import MAX_SWE_RETAINED_BYTES
from .swe import (
    GRADER_REVISION,
    LIMITS,
    SOURCE_HASHES,
    SWE_DATASET,
    SWE_PLAN_SCHEMA,
    _find_official_report,
    _read_json,
    _result_record,
    _sha256_file,
    _python_environment,
    _validate_assets,
    _verify_dataset_snapshot,
)
from .profiles import load_profile
from .swe_images import instance_image_tag
from ..transports import _ProcessOutputOverflow, _ProcessPipeError, _run_bounded_process


RETAINED_SCHEMA = SWE_CANONICAL_RETENTION_SCHEMA
REVIEW_SCHEMA = "anvil-serving.swe-regrade/v1"
MAX_REPLAY_OUTPUT_BYTES = 1024 * 1024
_TERMINAL = frozenset({"completed", "failed", "cancelled"})


def _regrade_runner(argv, cwd, timeout, env):
    return _run_bounded_process(
        argv, timeout=timeout, max_output_bytes=MAX_REPLAY_OUTPUT_BYTES, cwd=cwd, env=env
    )


def _fail(code: str, message: str) -> None:
    raise BenchmarkJobError(code, message)


def _source_plan(path: Path) -> dict[str, Any]:
    value = _read_json(str(path), code="bad_retained_swe_plan")
    if not isinstance(value, Mapping) or value.get("schema") != SWE_PLAN_SCHEMA:
        _fail("bad_retained_swe_plan", "retained SWE plan is invalid")
    expected = hashlib.sha256(canonical_json_bytes({k: v for k, v in value.items() if k != "plan_sha256"})).hexdigest()
    if value.get("plan_sha256") != expected:
        _fail("swe_plan_digest_mismatch", "retained SWE plan identity changed")
    return dict(value)


def _terminal_manifest_reference(store: Any, source: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the canonical-manifest reference bound by terminal job evidence."""
    artifact = store.artifact(source["spec"]["run_id"])
    stages = artifact.get("results", {}).get("evidence", {}).get("stages", []) if isinstance(artifact, Mapping) else []
    if not isinstance(stages, list):
        _fail("missing_swe_canonical_evidence", "source terminal evidence is invalid")
    for stage in stages:
        if not isinstance(stage, Mapping) or stage.get("name") != "swe_canonical_retention":
            continue
        evidence = stage.get("evidence")
        if not isinstance(evidence, list) or len(evidence) != 1 or not isinstance(evidence[0], Mapping):
            continue
        path = evidence[0].get("path")
        if not isinstance(path, str):
            continue
        data = read_referenced_job_evidence(store, source, path).get("data")
        retention = data.get("retention") if isinstance(data, Mapping) and isinstance(data.get("retention"), Mapping) else data
        reference = retention.get("manifest_reference") if isinstance(retention, Mapping) else None
        if isinstance(reference, Mapping):
            return reference
    _fail("missing_swe_canonical_evidence", "source terminal evidence does not reference canonical SWE retention")


def _official_report(path: str, selected: list[str]) -> tuple[set[str], set[str], str]:
    report_path = Path(path)
    if report_path.is_symlink() or not report_path.is_file() or report_path.stat().st_size > MAX_SWE_RETAINED_BYTES:
        _fail("bad_official_grader_report", "official SWE grader report is unsafe or exceeds the retention bound")
    report = _read_json(str(report_path), code="bad_official_grader_report")
    if not isinstance(report, Mapping):
        _fail("bad_official_grader_report", "official SWE grader report is invalid")
    rows: dict[str, set[str]] = {}
    allowed = set(selected)
    for key in ("completed_ids", "resolved_ids", "error_ids"):
        value = report.get(key)
        if (not isinstance(value, list) or any(not isinstance(item, str) for item in value)
                or len(value) != len(set(value)) or not set(value).issubset(allowed)):
            _fail("bad_official_grader_report", "official SWE grader report IDs are invalid")
        rows[key] = set(value)
    if not rows["resolved_ids"].issubset(rows["completed_ids"]):
        _fail("bad_official_grader_report", "official SWE grader resolved IDs contradict completion")
    completed = rows["completed_ids"] - rows["error_ids"]
    resolved = rows["resolved_ids"] & completed
    return completed, resolved, _sha256_file(str(report_path))


def prepare_swe_regrade(store: Any, record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate immutable source evidence without preparing assets or contacting a model."""
    spec = record.get("spec") if isinstance(record, Mapping) else None
    if not isinstance(spec, Mapping):
        _fail("bad_regrade_record", "regrade record is invalid")
    parameters = spec.get("parameters")
    source_run_id = parameters.get("source_run_id") if isinstance(parameters, Mapping) else None
    if not isinstance(source_run_id, str):
        _fail("missing_source_run_id", "regrade requires a source SWE run")
    source = store.status(source_run_id)
    if not isinstance(source, Mapping):
        _fail("source_run_not_found", "source SWE run does not exist")
    source_spec = source.get("spec")
    if (not isinstance(source_spec, Mapping) or source.get("state") not in _TERMINAL
            or source_spec.get("suite") != "swe"):
        _fail("source_run_not_terminal", "source SWE run must be terminal")
    if source_spec.get("ownership_id") != spec.get("ownership_id"):
        _fail("source_run_owner_mismatch", "source SWE run belongs to another owner")
    if spec.get("suite") != "swe" or spec.get("profile") != source_spec.get("profile"):
        _fail("source_run_profile_mismatch", "regrade must retain the source SWE profile")
    if spec.get("endpoint") != source_spec.get("endpoint"):
        _fail("source_run_endpoint_mismatch", "regrade must retain the source SWE endpoint identity")
    source_root = Path(resolve_owned_run_path(store.run_root, ownership_id=source_spec["ownership_id"], run_id=source_run_id))
    retained = source_root / "evidence" / "swe-canonical"
    try:
        manifest = read_retained_swe_artifacts(run_path=str(source_root))
    except ValueError as exc:
        raise BenchmarkJobError("bad_swe_canonical_manifest", "retained SWE canonical manifest is invalid") from exc
    files = {entry["path"]: entry for entry in manifest["files"]}
    anchored_manifest = _terminal_manifest_reference(store, source)
    expected_path = "evidence/swe-canonical/manifest.json"
    manifest_path = retained / "manifest.json"
    if (anchored_manifest.get("path") != expected_path
            or anchored_manifest.get("sha256") != _sha256_file(str(manifest_path))
            or anchored_manifest.get("bytes") != manifest_path.stat().st_size):
        _fail("swe_canonical_evidence_mismatch", "retained SWE canonical manifest is not bound by terminal evidence")
    if not {"swe-plan.json", "predictions.jsonl"}.issubset(files):
        _fail("missing_swe_regrade_evidence", "retained SWE plan and predictions are required")
    plan = _source_plan(retained / "swe-plan.json")
    if plan.get("run_id") != source_run_id or plan.get("ownership_id") != spec.get("ownership_id") or plan.get("profile") != source_spec.get("profile") or plan.get("dataset") != SWE_DATASET:
        _fail("source_run_plan_mismatch", "retained SWE plan does not match its source job")
    if source_spec.get("parameters", {}).get("instance_ids") is not None and plan.get("selection", {}).get("instance_ids") != source_spec["parameters"]["instance_ids"]:
        _fail("source_run_plan_mismatch", "retained SWE selection does not match its source job")
    grader = plan.get("harnesses", {}).get("grader") if isinstance(plan.get("harnesses"), Mapping) else None
    containment = plan.get("harnesses", {}).get("grader_containment") if isinstance(plan.get("harnesses"), Mapping) else None
    if (not isinstance(grader, Mapping) or grader.get("revision") != GRADER_REVISION
            or not isinstance(containment, Mapping) or containment.get("source_hashes") != SOURCE_HASHES):
        _fail("untrusted_swe_grader", "retained SWE grader identity is not qualified")
    adapter = Path(__file__).with_name("swe_grader_adapter.py")
    adapter_sha256 = _sha256_file(str(adapter))
    if containment.get("adapter_sha256") != adapter_sha256:
        _fail("untrusted_swe_grader", "retained SWE grader adapter identity changed")
    assets_path = source_root / "assets.json"
    assets = _read_json(str(assets_path), code="missing_harness_asset")
    profile = load_profile(source_spec["profile"])
    validated_assets = _validate_assets(profile, assets)
    cache_root = os.environ.get("ANVIL_BENCHMARK_CACHE_ROOT", os.path.expanduser("~/.anvil-serving/benchmark-harness-cache"))
    python, _ = _python_environment(assets, cache_root=cache_root)
    grader_root = real_path(os.path.join(real_path(cache_root), validated_assets["swe-bench"]["cache_key"]))
    if not os.path.isdir(grader_root):
        _fail("unavailable_swe_grader", "pinned SWE grader environment is unavailable")
    paths = plan.get("paths") if isinstance(plan.get("paths"), Mapping) else {}
    if paths.get("grader_root") != grader_root:
        _fail("source_run_plan_mismatch", "retained SWE grader path does not match the pinned assets")
    _verify_dataset_snapshot(plan["dataset_snapshot"])
    target_root = resolve_owned_run_path(store.run_root, ownership_id=spec["ownership_id"], run_id=spec["run_id"])
    source_timeout = plan.get("timeout_s", source_spec.get("timeout_s", 1800))
    target_timeout = spec.get("timeout_s", source_timeout)
    if (type(source_timeout) is not int or type(target_timeout) is not int
            or not 1 <= source_timeout <= 1800 or not 1 <= target_timeout <= 1800):
        _fail("bad_swe_regrade_timeout", "retained SWE grader timeout is invalid")
    return {
        "schema": REVIEW_SCHEMA,
        "run_id": spec["run_id"], "ownership_id": spec["ownership_id"], "source_run_id": source_run_id,
        "source_plan_sha256": plan["plan_sha256"], "source_predictions_sha256": files["predictions.jsonl"]["sha256"],
        "selection": plan["selection"], "dataset": plan["dataset"], "dataset_snapshot": plan["dataset_snapshot"],
        "grader": {"revision": GRADER_REVISION, "source_hashes": dict(SOURCE_HASHES), "adapter_sha256": adapter_sha256,
                   "root": grader_root, "python": python, "limits": dict(LIMITS)},
        "predictions_path": str(retained / "predictions.jsonl"),
        "paired_image_ids": plan.get("paired_image_ids"),
        "paired_image_path": str(retained / "paired-image-ids.json") if plan.get("paired_image_ids") is not None else None,
        "paired_image_sha256": files.get("paired-image-ids.json", {}).get("sha256"),
        "timeout_s": min(source_timeout, target_timeout),
        "paths": {"run": target_root, "work": os.path.join(target_root, "work"),
                  "grader_work": os.path.join(target_root, "work", "official-grader"),
                  "result": os.path.join(target_root, "swe-regrade-result.json"),
                  "artifact": os.path.join(target_root, "evidence", "swe-regrade-v1.json")},
    }


def regrade_swe_predictions(plan: Mapping[str, Any], *, runner: Any = _regrade_runner) -> dict[str, Any]:
    """Run the regenerated official-grader command only; source evidence remains read-only."""
    if not isinstance(plan, Mapping) or plan.get("schema") != REVIEW_SCHEMA:
        _fail("bad_swe_regrade_plan", "SWE regrade plan is invalid")
    paths = plan["paths"]
    for name in ("work", "grader_work"):
        Path(paths[name]).mkdir(parents=True, exist_ok=True)
    result_path, artifact_path = Path(paths["result"]), Path(paths["artifact"])
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    if result_path.exists() or artifact_path.exists():
        _fail("regrade_destination_exists", "regrade destination already contains results")
    predictions_path = Path(plan["predictions_path"])
    if (predictions_path.is_symlink() or not predictions_path.is_file()
            or _sha256_file(str(predictions_path)) != plan.get("source_predictions_sha256")):
        _fail("swe_canonical_hash_mismatch", "retained SWE predictions changed")
    grader = plan["grader"]
    adapter = str(Path(__file__).with_name("swe_grader_adapter.py"))
    receipt = os.path.join(paths["grader_work"], "containment.json")
    selected = plan["selection"]["instance_ids"]
    paired_args = []
    if plan.get("paired_image_ids") is not None:
        paired = plan.get("paired_image_ids")
        if not isinstance(paired, Mapping) or not isinstance(plan.get("paired_image_path"), str):
            _fail("bad_swe_regrade_plan", "paired image evidence is invalid")
        image_path = Path(plan["paired_image_path"])
        expected = hashlib.sha256(canonical_json_bytes({instance_image_tag(key): value for key, value in paired.items()})).hexdigest()
        if image_path.is_symlink() or not image_path.is_file() or _sha256_file(str(image_path)) != plan.get("paired_image_sha256"):
            _fail("swe_canonical_hash_mismatch", "retained paired image evidence changed")
        paired_args = ["--image-identities", str(image_path), "--image-identities-sha256", expected]
    command = [grader["python"], adapter, "--grader-root", grader["root"], "--adapter-sha256", grader["adapter_sha256"],
               "--receipt", receipt, *paired_args, "--", "--dataset_name", plan["dataset_snapshot"]["root"], "--split", plan["dataset_snapshot"].get("split", "test"),
               "--predictions_path", plan["predictions_path"], "--max_workers", "1", "--namespace", "swebench",
               "--run_id", plan["run_id"], "--timeout", str(plan["timeout_s"]), "--report_dir", paths["grader_work"], "--instance_ids", *selected]
    started_at, started = utc_now(), time.monotonic()
    clean_env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": grader["root"]}
    try:
        process = runner(command, paths["grader_work"], plan["timeout_s"], clean_env)
    except subprocess.TimeoutExpired:
        result = {"schema": REVIEW_SCHEMA, "run_id": plan["run_id"], "source_run_id": plan["source_run_id"], "state": "incomplete",
                  "failure": {"class": "timeout", "stage": "official_grader", "code": "grader_timeout"}, "stages": []}
        atomic_write_json(str(result_path), result)
        atomic_write_json(str(artifact_path), result)
        return result
    except _ProcessOutputOverflow:
        result = {"schema": REVIEW_SCHEMA, "run_id": plan["run_id"], "source_run_id": plan["source_run_id"], "state": "incomplete",
                  "failure": {"class": "infrastructure_failure", "stage": "official_grader", "code": "grader_output_overflow"}, "stages": []}
        atomic_write_json(str(result_path), result)
        atomic_write_json(str(artifact_path), result)
        return result
    except (_ProcessPipeError, OSError) as exc:
        result = {"schema": REVIEW_SCHEMA, "run_id": plan["run_id"], "source_run_id": plan["source_run_id"], "state": "incomplete",
                  "failure": {"class": "infrastructure_failure", "stage": "official_grader", "code": type(exc).__name__}, "stages": []}
        atomic_write_json(str(result_path), result)
        atomic_write_json(str(artifact_path), result)
        return result
    stage = _result_record(command, process, time.monotonic() - started, started_at)
    stage.update({"name": "official_grader", "status": "completed" if stage["returncode"] == 0 else "failed"})
    report_path = _find_official_report(paths["grader_work"], plan["run_id"])
    completed: set[str] = set()
    resolved: set[str] = set()
    report_sha256 = None
    if report_path and stage["returncode"] == 0:
        completed, resolved, report_sha256 = _official_report(report_path, selected)
    result = {"schema": REVIEW_SCHEMA, "run_id": plan["run_id"], "source_run_id": plan["source_run_id"], "state": "completed" if len(completed) == len(selected) else "incomplete",
              "source_plan_sha256": plan["source_plan_sha256"], "source_predictions_sha256": plan["source_predictions_sha256"],
              "stages": [stage], "official_grader_complete": len(completed) == len(selected),
              "summary": {"attempted": len(selected), "graded": len(completed), "resolved": len(resolved),
                          "resolve_rate": len(resolved) / len(completed) if completed else None},
              "report_sha256": report_sha256,
              "failure": None if len(completed) == len(selected) else {"class": "test_failure", "stage": "official_grader"}}
    atomic_write_json(str(result_path), result)
    atomic_write_json(str(artifact_path), result)
    return result
