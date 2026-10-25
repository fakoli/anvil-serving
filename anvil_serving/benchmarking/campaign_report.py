"""Offline, public-safe projections of frozen campaigns and retained native evidence."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import itertools
import json
import math
import os
import stat
from pathlib import Path
import re

from .aider import AIDER_PLAN_SCHEMA, validated_aider_task_grade
from .jobs import BenchmarkJobError, canonical_json_bytes, job_spec_sha256, resolve_owned_run_path
from .swe_agent_adapter import EVENT_SCHEMA, MAX_BYTES, MAX_EVENTS
from .campaign_execution import _read as _read_execution, _validate_release, bind_campaign_child, reconcile_batch, batch_closure

SCHEMA = "anvil-serving.benchmark-campaign-report/v1"
_HASHES = ("workload_sha256", "controls_sha256", "oracle_sha256", "cache_sha256")
_STATES = {"queued", "running", "cancelling", "completed", "failed", "cancelled"}
_METRICS = {
    "time_to_first_token", "time_to_first_output_token", "request_latency",
    "inter_token_latency", "inter_chunk_latency", "output_token_throughput",
    "output_token_throughput_per_user", "request_throughput", "request_count",
    "error_request_count", "completed_request_count", "input_sequence_length",
    "output_sequence_length", "measurement_duration",
}
_UNITS = {"ms", "sec", "s", "us", "ns", "tokens/sec", "tokens/sec/user", "requests/sec", "requests", "tokens"}


def _number(value):
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def _digest(value):
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else None


def _read_bytes(root, relative, reference=None):
    """Read bounded owned evidence; a reference also binds its exact bytes."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("invalid evidence path")
    rel = Path(relative)
    path = root / rel
    if rel.is_absolute() or ".." in rel.parts or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("evidence escapes owned run")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("symlink evidence")
    with open(path, "rb", opener=lambda p, f: os.open(p, f | os.O_NONBLOCK | os.O_NOFOLLOW)) as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("nonregular evidence")
        raw = handle.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("oversized evidence")
    if reference is not None and (reference.get("sha256") != hashlib.sha256(raw).hexdigest()
                                  or reference.get("bytes") != len(raw)):
        raise ValueError("evidence digest mismatch")
    return raw


def _read(root, relative, reference=None):
    value = json.loads(_read_bytes(root, relative, reference))
    if not isinstance(value, dict):
        raise ValueError("invalid evidence object")
    return value


def _native(store, cell, record, root, expected_sha256):
    if record.get("spec_sha256") != expected_sha256:
        return None, "spec_mismatch"
    try:
        artifact = store.artifact(cell["spec"]["run_id"])
        if not artifact:
            return None, "missing_artifact"
        if artifact.get("run", {}).get("spec_sha256") != expected_sha256:
            return None, "spec_mismatch"
        suite = cell["spec"]["suite"]
        stages = artifact.get("results", {}).get("evidence", {}).get("stages", [])
        refs = [ref for stage in stages if stage.get("name") == suite for ref in stage.get("evidence", [])]
        if len(refs) != 1:
            return None, "missing_or_ambiguous_suite_evidence"
        return _read(root, refs[0].get("path"), refs[0]), None
    except (OSError, ValueError, TypeError, KeyError, AttributeError, BenchmarkJobError):
        return None, "invalid_evidence"


def _retained_reference(manifest, path):
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    matches = [item for item in artifacts if isinstance(item, dict) and item.get("path") == path]
    if len(matches) != 1:
        return None
    reference = matches[0]
    if (set(reference) != {"path", "bytes", "sha256"} or type(reference["bytes"]) is not int
            or reference["bytes"] < 0 or not _digest(reference["sha256"])):
        return None
    return reference


def _retained_missing(manifest, reference):
    return reference["path"].removeprefix("aider/") in manifest["missing"]


def _retained_aider_diagnostics(root, manifest):
    reference = _retained_reference(manifest, "aider/requests.jsonl")
    if reference is None or _retained_missing(manifest, reference):
        return None
    try:
        raw = _read_bytes(root, reference["path"], reference)
        lines = raw.splitlines(keepends=True)
        if not 1 <= len(lines) <= MAX_EVENTS or len(raw) > MAX_BYTES + 8192:
            return None
        rows = [json.loads(line) for line in lines]
        if any(not isinstance(row, dict) or row.get("schema") != EVENT_SCHEMA or row.get("seq") != index
               or not isinstance(row.get("event"), str) for index, row in enumerate(rows, 1)):
            return None
        terminal = rows[-1] if rows[-1]["event"] == "adapter_finished" else None
        if terminal is not None and (terminal.get("event_count") != len(rows) - 1
                                     or terminal.get("byte_count") != sum(map(len, lines[:-1]))
                                     or terminal.get("status") not in {"complete", "truncated"}):
            return None
        signals = {"output_truncated": "aider_transport_response",
                   "compilation_failure": "aider_contained_command_finished"}
        observed = {}
        for signal, event in signals.items():
            values = [row[signal] for row in rows if row["event"] == event and signal in row]
            if any(value is not None and type(value) is not bool for value in values):
                return None
            observed[signal] = True if any(value is True for value in values) else None
        return {**observed, "coverage": "complete" if terminal and terminal["status"] == "complete" else "partial"}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, UnicodeDecodeError):
        return None


def _cancelled_aider_raw_evidence(store, cell, record, root, expected_sha256):
    """Project one retained native grade without creating a suite scoring stage."""
    try:
        if record.get("spec_sha256") != expected_sha256:
            return None
        artifact = store.artifact(cell["spec"]["run_id"])
        if not artifact or artifact.get("run", {}).get("spec_sha256") != expected_sha256:
            return None
        stages = artifact.get("results", {}).get("evidence", {}).get("stages", [])
        refs = [ref for stage in stages if stage.get("name") == "aider_retention" for ref in stage.get("evidence", [])]
        if len(refs) != 1:
            return None
        cancellation = _read(root, refs[0].get("path"), refs[0])
        if cancellation.get("schema") != "anvil-serving.aider-cancellation/v1":
            return None
        retention = cancellation.get("retention")
        if (not isinstance(retention, dict) or retention.get("schema") != "anvil-serving.aider-retention/v1"
                or retention.get("state") not in {"retained", "incomplete"}
                or not isinstance(retention.get("manifest_reference"), dict)):
            return None
        manifest_reference = retention["manifest_reference"]
        manifest = _read(root, manifest_reference.get("path"), manifest_reference)
        if (manifest.get("schema") != "anvil-serving.aider-retention/v1" or manifest.get("state") != retention["state"]
                or not isinstance(manifest.get("missing"), list) or manifest != {
                "schema": manifest.get("schema"), "state": manifest.get("state"),
                "artifacts": manifest.get("artifacts"), "missing": manifest.get("missing")}):
            return None
        plan_reference = _retained_reference(manifest, "aider/plan.json")
        selection_reference = _retained_reference(manifest, "aider/selection.json")
        task_id = cell["task_id"]
        native_reference = _retained_reference(manifest, f"aider/results/{task_id}/.aider.results.json")
        if not plan_reference or not selection_reference:
            return None
        if any(not isinstance(path, str) for path in manifest["missing"]) or any(
                _retained_missing(manifest, reference) for reference in (plan_reference, selection_reference)):
            return None
        plan = _read(root, plan_reference["path"], plan_reference)
        selection = _read(root, selection_reference["path"], selection_reference)
        if (plan.get("schema") != AIDER_PLAN_SCHEMA
                or plan.get("plan_sha256") != hashlib.sha256(canonical_json_bytes(
                    {key: value for key, value in plan.items() if key != "plan_sha256"}
                )).hexdigest()
                or not isinstance(cell.get("prepared"), dict)
                or hashlib.sha256(canonical_json_bytes(plan)).hexdigest() != cell["prepared"].get("plan_sha256")
                or plan.get("ownership_id") != cell["spec"]["ownership_id"]
                or plan.get("run_id") != cell["spec"]["run_id"]
                or plan.get("profile") != cell["spec"]["profile"]
                or plan.get("endpoint") != cell["spec"]["endpoint"]
                or not isinstance(plan.get("selection"), dict)
                or selection != plan["selection"]):
            return None
        instance_ids = selection.get("instance_ids")
        if (not isinstance(instance_ids, list) or instance_ids != [task_id]
                or instance_ids != cell["spec"].get("parameters", {}).get("instance_ids")
                or selection.get("sha256") != hashlib.sha256(canonical_json_bytes(instance_ids)).hexdigest()):
            return None
        diagnostics = _retained_aider_diagnostics(root, manifest)
        if (native_reference is None or native_reference["bytes"] > 1024 * 1024
                or _retained_missing(manifest, native_reference)):
            return {"grades": None, "diagnostics": {**diagnostics, "num_exhausted_context_windows": None}
                    if diagnostics is not None else None}
        native = _read(root, native_reference["path"], native_reference)
        grade = validated_aider_task_grade(plan, native)
        if grade is None:
            return {"grades": None, "diagnostics": {**diagnostics, "num_exhausted_context_windows": None}
                    if diagnostics is not None else None}
        if diagnostics is None:
            return {"grades": [grade], "diagnostics": None}
        exhausted = native.get("num_exhausted_context_windows")
        diagnostics = {**diagnostics, "num_exhausted_context_windows": exhausted
                       if type(exhausted) is int and 0 <= exhausted <= 12 else None}
        return {"grades": [grade], "diagnostics": diagnostics}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, BenchmarkJobError):
        return None


def _samples(cell, native):
    suite, task = cell["spec"]["suite"], cell["task_id"]
    if native is None:
        return None, None
    if suite == "agentic" and native.get("schema") == "anvil-serving.agentic-suite-run/v1":
        emitted = "tool-recovery-" + cell["spec"].get("parameters", {}).get("recovery_result", "error") if task == "tool-recovery" else task
        rows = native.get("observations")
        if not isinstance(rows, list) or any(not isinstance(row, dict) or row.get("scenario_id") != emitted for row in rows):
            return None, "sample_identity_mismatch"
        if not rows:
            return [], "empty_native_population"
        samples = []
        for row in rows:
            passed = row.get("passed") if type(row.get("passed")) is bool else None
            turns = row.get("turns", [])
            latency = sum(t["latency_ms"] for t in turns) if turns and all(_number(t.get("latency_ms")) for t in turns) else None
            samples.append({"attempted": True, "passed": passed, "failed": passed is False or bool(row.get("failure")),
                            "latency_sum_ms": latency})
        return samples, None
    if suite == "aider" and native.get("schema") == "anvil-serving.aider-run/v1":
        rows = native.get("instances")
        if not isinstance(rows, list) or any(not isinstance(row, dict) or row.get("instance_id") != task for row in rows):
            return None, "sample_identity_mismatch"
        if not rows:
            return [], "empty_native_population"
        return [{"attempted": True if type(row.get("resolved")) is bool or isinstance(row.get("native"), dict) else None,
                 "passed": row.get("resolved") if type(row.get("resolved")) is bool else None,
                 "failed": not row["resolved"] if type(row.get("resolved")) is bool else None,
                 "latency_sum_ms": None} for row in rows], None
    if suite == "performance" and native.get("schema") == "anvil-serving.performance-run/v1":
        return None, None  # Native aggregates do not establish per-request IDs or quality scores.
    return None, "unsupported_native_schema"


def _metrics(native):
    result = {}
    source = (native or {}).get("summary", {}).get("metrics", {})
    for name in sorted(_METRICS):
        item = source.get(name, {})
        unit = item.get("unit") if item.get("unit") in _UNITS else None
        measured = item.get("status") == "measured" and _number(item.get("value")) and unit is not None
        result[name] = {"value": item.get("value") if measured else None,
                        "status": "measured" if measured else "unknown", "unit": unit}
    return result


def _receipt(cell, root, expected, store):
    try:
        artifact = store.artifact(cell["spec"]["run_id"])
        reference = (artifact or {}).get("results", {}).get("campaign_execution")
        if not isinstance(reference, dict) or reference.get("path") != "campaign-execution.json":
            return None
        value = _read(root, "campaign-execution.json", reference)
        if value.get("schema") != "anvil-serving.campaign-execution/v1" or value.get("spec_sha256") != job_spec_sha256(expected) or value.get("campaign") != expected["campaign"]:
            return None
        return value
    except (OSError, ValueError, TypeError, KeyError, BenchmarkJobError):
        return None


def _coverage(receipt, manifest=None, cell=None, store=None):
    result = dict.fromkeys(('engine', 'gpu', 'host', 'cgroup'), 'unknown')
    if not receipt or not manifest or 'telemetry_collection' not in manifest:
        return result
    try:
        from .campaign_telemetry import verify_terminal, coverage
        release_path = Path(resolve_owned_run_path(store.run_root, ownership_id=manifest['ownership_id'],
            run_id=manifest['campaign_id'], relative='campaign/batches/' + cell['batch_id'] + '.json'))
        release = _read_execution(release_path)
        _validate_release(release, manifest, cell['batch_id'])
        if not isinstance(receipt.get('batch_closure'), dict):
            return result
        shared, _ = batch_closure(manifest, cell['batch_id'], release, run_root=store.run_root,
                                  reference=receipt['batch_closure'])
        if shared['status'] != 'closed' or shared.get('telemetry_terminal') != receipt.get('telemetry_terminal'):
            return result
        reference = receipt['telemetry_terminal']
        terminal, rows, _ = verify_terminal(manifest, cell['batch_id'], release, run_root=store.run_root, reference=reference)
        arm = next(a for a in manifest['arms'] if a['arm_id'] == cell['arm_id'])
        derived = coverage(rows, manifest['telemetry_collection'], arm,
            receipt.get('started_monotonic'), receipt.get('closed_monotonic'), complete=terminal['complete'])
        if derived != receipt.get('telemetry_coverage') or receipt.get('activation') != release['activation'] or receipt.get('boot_id') != release['boot_id']:
            return result
        return {key: 'measured' if value['complete'] else 'unknown' for key, value in derived.items()}
    except (OSError, ValueError, TypeError, KeyError, RecursionError, BenchmarkJobError):
        return result


def _identity_status(manifest, cell, receipt, store):
    """Project only verified attribution, never private activation coordinates."""
    try:
        release_path = Path(resolve_owned_run_path(
            store.run_root, ownership_id=manifest["ownership_id"], run_id=manifest["campaign_id"],
            relative="campaign/batches/" + cell["batch_id"] + ".json",
        ))
        release = _read_execution(release_path)
        _validate_release(release, manifest, cell["batch_id"])
        return "verified" if receipt and receipt.get("activation") == release["activation"] else "unverified"
    except (OSError, ValueError, TypeError, KeyError, BenchmarkJobError):
        return "unverified"


def _timing(receipts):
    intervals, unknown = [], 0
    for receipt in receipts:
        start, end = (receipt or {}).get("started_monotonic"), (receipt or {}).get("closed_monotonic")
        if (not receipt or receipt.get("status") != "closed" or receipt.get("clients_stopped") is not True
                or receipt.get("drain", {}).get("status") != "verified" or not receipt.get("boot_id")
                or not _number(start) or not _number(end) or end < start):
            unknown += 1
        else:
            intervals.append((receipt["boot_id"], start, end))
    # Monotonic intervals on different boots cannot establish shared overlap.
    same_clock = len({item[0] for item in intervals}) <= 1
    union, peak, depth, previous = 0.0, 0, 0, None
    if same_clock:
        events = sorted((t, change) for _, start, end in intervals for t, change in ((start, 1), (end, -1)) if end > start)
        for stamp, change in events:
            if previous is not None and depth:
                union += stamp - previous
            depth += change
            peak = max(peak, depth)
            previous = stamp
    return {"active_wall_seconds": union if intervals and same_clock and not unknown else None,
            "known_active_wall_seconds": union if intervals and same_clock else None,
            "unknown_intervals": unknown, "observed_job_overlap": peak if intervals and same_clock and not unknown else None,
            "observed_request_overlap": None,
            "request_overlap_status": "unknown",
            "scope": "child traffic-capable intervals including drain; not request concurrency"}


def _campaign_timing(manifest, store, receipts, started_runs):
    observed = _timing(receipts)
    intervals = []
    root = Path(resolve_owned_run_path(store.run_root, ownership_id=manifest["ownership_id"], run_id=manifest["campaign_id"]))
    for batch in dict.fromkeys(cell["batch_id"] for cell in manifest["cells"]):
        try:
            release = _read(root, f"campaign/batches/{batch}.json")
        except FileNotFoundError:
            if any(cell["batch_id"] == batch and cell["spec"]["run_id"] in started_runs for cell in manifest["cells"]):
                intervals.append(None)
            continue
        except (OSError, ValueError, TypeError):
            intervals.append(None)
            continue
        try:
            status = reconcile_batch(manifest, batch, run_root=store.run_root, store=store)
            if status.get("status") != "closed" or not _number(status.get("active_seconds")):
                intervals.append(None)
                continue
            intervals.append({"status": "closed", "boot_id": release["boot_id"],
                              "started_monotonic": release["released_monotonic"],
                              "closed_monotonic": release["released_monotonic"] + status["active_seconds"],
                              "clients_stopped": True, "drain": {"status": "verified"}})
        except (OSError, ValueError, KeyError, TypeError, BenchmarkJobError):
            intervals.append(None)
    active = _timing(intervals)
    return {**observed, "child_active_wall_seconds": observed["active_wall_seconds"],
            "active_wall_seconds": active["active_wall_seconds"],
            "known_active_wall_seconds": active["known_active_wall_seconds"],
            "unknown_batch_intervals": active["unknown_intervals"],
            "scope": "active wall includes release, verified drain and bounded telemetry finalization; child overlap is not request concurrency"}


def _latency_range(rows):
    values = [row["outcomes"][0]["latency_sum_ms"] for row in rows
              if row["outcomes"] and len(row["outcomes"]) == 1 and _number(row["outcomes"][0]["latency_sum_ms"])]
    return [min(values), max(values)] if len(values) >= 2 else None


def _population(rows, field):
    values = [row[field] for row in rows]
    unknown = sum(value is None for value in values)
    return {"count": None if unknown else sum(values), "known_count": sum(v for v in values if v is not None),
            "unknown_cells": unknown}


def _key(cell):
    matching = cell.get("matching", {})
    return (cell["spec"]["suite"], cell["task_id"], cell["repeat"], cell["concurrency"],
            *(matching.get(key) for key in _HASHES))


def report_campaign(manifest, store, *, state=None):
    """Project evidence without inference calls, mutation, or publishing private labels.

    Arm/task/cell labels are synthetic and stable in manifest order. Digests bind
    the private manifest; arbitrary operator labels and native text never export.
    """
    arms = {arm["arm_id"]: f"arm-{index + 1}" for index, arm in enumerate(manifest["arms"])}
    tasks, cells, receipts, internal, job_rows = {}, [], [], [], []
    started_runs = set()
    for index, cell in enumerate(manifest["cells"]):
        spec = cell["spec"]
        task_key = (spec["suite"], cell["task_id"])
        tasks.setdefault(task_key, f"task-{len(tasks) + 1}")
        record = store.status(spec["run_id"])
        root = Path(resolve_owned_run_path(store.run_root, ownership_id=spec["ownership_id"], run_id=spec["run_id"]))
        expected = bind_campaign_child(spec, manifest_sha256=manifest["manifest_sha256"], campaign_id=manifest["campaign_id"],
                                       cell_id=cell["cell_id"], arm_id=cell["arm_id"], batch_id=cell["batch_id"])
        native, reason = _native(store, cell, record, root, job_spec_sha256(expected)) if record else (None, "not_submitted")
        try:
            samples, sample_reason = _samples(cell, native)
        except (TypeError, ValueError, KeyError, AttributeError):
            samples, sample_reason = None, "invalid_sample_evidence"
        reason = sample_reason or reason
        receipt = _receipt(cell, root, expected, store) if record else None
        if record and record.get("started_at"):
            started_runs.add(spec["run_id"])
            receipts.append(receipt)
        status = record.get("state") if record and record.get("state") in _STATES else "planned"
        raw_task_evidence = (_cancelled_aider_raw_evidence(store, cell, record, root, job_spec_sha256(expected))
                             if status == "cancelled" and spec["suite"] == "aider" else None)
        performance = spec["suite"] == "performance"
        planned = spec.get("parameters", {}).get("request_count") if performance else 1
        planned = planned if type(planned) is int and planned > 0 else None
        if samples is not None and len(samples) > (planned or 0):
            samples, reason = None, "sample_population_mismatch"
        try:
            metrics = _metrics(native) if performance else {}
        except (TypeError, ValueError, AttributeError):
            metrics, reason = _metrics(None), "invalid_metric_evidence"
        attempted = metrics.get("request_count", {}).get("value") if performance else sum(s["attempted"] for s in samples) if samples is not None and all(s["attempted"] is not None for s in samples) else None
        scored = sum(s["passed"] is not None for s in samples) if samples is not None else None
        failed = metrics.get("error_request_count", {}).get("value") if performance else sum(s["failed"] for s in samples) if samples is not None and all(s["failed"] is not None for s in samples) else None
        if not record:
            attempted, scored, failed = 0, 0, 0
        remaining = max(0, planned - scored) if planned is not None and scored is not None else None
        counts = {"planned": planned, "submitted": planned if record else 0, "attempted": attempted,
                  "scored": scored, "failed": failed, "cancelled": remaining if status == "cancelled" else 0,
                  "unfinished": remaining if status != "cancelled" else 0}
        coverage = _coverage(receipt, manifest, cell, store)
        identity_status = _identity_status(manifest, cell, receipt, store)
        requested_cache = cell.get("cache_condition", spec.get("parameters", {}).get("cache_condition"))
        if requested_cache not in {"basic", "cold", "warm", "unknown", "unique", "shared", "uncontrolled"}:
            requested_cache = "unspecified"
        row = {"cell": f"cell-{index + 1}", "arm": arms[cell["arm_id"]], "task": tasks[task_key],
               "suite": spec["suite"] if spec["suite"] in {"agentic", "aider", "performance"} else "unsupported",
               "repeat": cell["repeat"], "requested_concurrency": cell["concurrency"], "state": status,
               "spec_sha256": job_spec_sha256(expected),
               "matching": {key: _digest(cell.get("matching", {}).get(key)) for key in _HASHES},
               "evidence_status": reason or "retained", "samples": counts,
               "outcomes": samples, "native_aggregate_metrics": metrics,
               "raw_task_grades": raw_task_evidence["grades"] if raw_task_evidence else None,
               "raw_task_grade_diagnostics": raw_task_evidence["diagnostics"] if raw_task_evidence else None,
               "telemetry_coverage": coverage, "cache": {"requested": requested_cache, "proven": None},
               "identity_status": identity_status,
               "measurement_complete": status == "completed" and bool(native) and not reason and planned is not None
                   and attempted == planned and scored == planned and identity_status == "verified" and all(v == "measured" for v in coverage.values())
                   and bool(receipt) and receipt.get("status") == "closed" and receipt.get("clients_stopped") is True
                   and receipt.get("drain", {}).get("status") == "verified"
                   and _timing([receipt])["active_wall_seconds"] is not None
                   and requested_cache == "uncontrolled"}
        cells.append(row)
        internal.append((cell, row, receipt))
        job_rows.append({"planned": 1, "submitted": int(record is not None),
                         "attempted": int(bool(record and record.get("started_at"))),
                         "scored": int(scored > 0) if scored is not None else None,
                         "failed": int(status == "failed"), "cancelled": int(status == "cancelled"),
                         "unfinished": int(status not in {"completed", "failed", "cancelled"}),
                         "completed": int(status == "completed")})
    comparisons = []
    for left, right in itertools.combinations(arms, 2):
        groups = {arm: defaultdict(list) for arm in (left, right)}
        for cell, row, _ in internal:
            if cell["arm_id"] in groups:
                groups[cell["arm_id"]][_key(cell)].append(row)
        matched, excluded = [], []
        for key in sorted(set(groups[left]) | set(groups[right]), key=repr):
            a, b = groups[left].get(key, []), groups[right].get(key, [])
            reason = "missing_matching_cell" if not a or not b else "duplicate_matching_cell" if len(a) != 1 or len(b) != 1 else "missing_matching_hash" if not all(_digest(v) for v in key[4:]) else None
            if reason:
                excluded.extend({"cell": row["cell"], "reason": reason} for row in a + b)
                continue
            a, b = a[0], b[0]
            fully_scored = all(row["samples"]["scored"] == row["samples"]["planned"] and row["samples"]["planned"] is not None for row in (a, b))
            matched.append({"cells": [a["cell"], b["cell"]], "scored": fully_scored,
                            "passed": [sum(s["passed"] is True for s in row["outcomes"]) if row["outcomes"] is not None else None for row in (a, b)]})
            if not fully_scored:
                excluded.extend({"cell": row["cell"], "reason": "unscored_matched_population"} for row in (a, b))
        arm_rows = [row for cell, row, _ in internal if cell["arm_id"] in (left, right)]
        comparisons.append({"arms": [arms[left], arms[right]], "scope": "deployment_comparison",
                            "engine_causal_claim": False, "matched": matched, "exclusions": excluded,
                            "complete": bool(matched) and not excluded and all(row["measurement_complete"] for row in arm_rows),
                            "performance_comparison": "native_aggregates_only; no matched per-request evidence",
                            "performance_ranking_eligible": False})
    repeats = defaultdict(list)
    for cell, row, _ in internal:
        repeats[(row["arm"], row["task"], row["requested_concurrency"], *_key(cell)[4:])].append(row)
    repeat_summary = []
    for group in repeats.values():
        scores = [int(row["outcomes"][0]["passed"]) for row in group if row["outcomes"] and len(row["outcomes"]) == 1 and row["outcomes"][0]["passed"] is not None]
        repeat_summary.append({"arm": group[0]["arm"], "task": group[0]["task"], "requested_concurrency": group[0]["requested_concurrency"],
                               "planned_repeats": len(group), "scored_repeats": len(scores),
                               "score_range": [min(scores), max(scores)] if len(scores) >= 2 else None,
                               "agreement": len(set(scores)) == 1 if len(scores) >= 2 else None,
                               "latency_sum_ms_range": _latency_range(group),
                               "inference": "descriptive_only; no confidence interval or significance claim"})
    arm_summaries = []
    for arm in arms.values():
        selected = [(row, job) for row, job in zip(cells, job_rows) if row["arm"] == arm]
        arm_summaries.append({"arm": arm,
            "jobs": {key: _population([job for _, job in selected], key) for key in job_rows[0]},
            "samples": {key: _population([row["samples"] for row, _ in selected], key) for key in cells[0]["samples"]}})
    return {"schema": SCHEMA, "manifest_sha256": _digest(manifest.get("manifest_sha256")),
            "labels": "synthetic labels in manifest order; private identities omitted",
            "jobs": {key: _population(job_rows, key) for key in ("planned", "submitted", "attempted", "scored", "failed", "cancelled", "unfinished", "completed")},
            "samples": {key: _population([row["samples"] for row in cells], key) for key in ("planned", "submitted", "attempted", "scored", "failed", "cancelled", "unfinished")},
            "arms": arm_summaries, "cells": cells, "comparisons": comparisons, "repeats": repeat_summary,
            "timing": _campaign_timing(manifest, store, receipts, started_runs), "promotion_authorized": False}
