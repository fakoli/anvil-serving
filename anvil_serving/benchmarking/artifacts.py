"""Validation, integrity checks, and atomic writes for benchmark artifacts."""

import copy
import datetime as dt
import hashlib
import ipaddress
import json
import math
import os
import re
import sys
import tempfile
from typing import Any


class BenchmarkArtifactError(Exception):
    """Typed benchmark artifact validation failure."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


CROSS_SUITE_EVIDENCE_SCHEMA = "anvil-serving.benchmark-evidence/v1"
EVIDENCE_KINDS = frozenset({"measured", "external_prior"})
EVIDENCE_COMPLETENESS = frozenset({"completed", "incomplete", "failed", "cancelled"})
EVIDENCE_FAILURE_CLASSES = frozenset({
    "routing",
    "authentication",
    "serving_runtime",
    "resource_exhaustion",
    "worker_runtime",
    "harness",
    "model_behavior",
    "grading",
    "cancellation",
})
SERVING_COMPARISON_PROFILE = "serving-comparison/v1"
SERVING_METRIC_KEYS = frozenset({
    "effective_prefill_tok_s", "time_to_first_output_ms", "ttft_ms",
    "decode_tok_s", "tpot_ms", "e2e_ms", "throughput_tok_s",
    "prompt_tokens", "completion_tokens", "kv_cache_bytes", "kv_cache_tokens",
    "context_tokens", "concurrency", "gpu_memory", "host_memory", "oom_status",
    "admission_status",
})
SERVING_METRIC_STATUSES = frozenset({
    "measured", "configured", "estimated", "blocked", "not_collected",
})
REQUIRED_MEASURED_IDENTITIES = frozenset({
    "model",
    "served_model",
    "runtime",
    "image",
    "hardware",
    "topology",
    "context",
    "concurrency",
    "harnesses",
    "dataset",
})
PROMOTION_DISCLAIMER = "Benchmark evidence does not authorize model promotion; promotion is a separate human decision."
FAILURE_CLASS_ALIASES = {
    "route_failure": "routing",
    "model_mismatch": "routing",
    "missing_credential": "authentication",
    "authorization_denied": "authentication",
    "model_failure": "model_behavior",
    "reasoning_failure": "model_behavior",
    "protocol_failure": "model_behavior",
    "budget_exhausted": "resource_exhaustion",
    "resource_exhaustion": "resource_exhaustion",
    "timeout": "worker_runtime",
    "infrastructure_failure": "worker_runtime",
    "image_failure": "worker_runtime",
    "broken_harness": "harness",
    "harness_failure": "harness",
    "test_failure": "grading",
    "grader_failure": "grading",
    "cancelled": "cancellation",
}
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEY_RE = re.compile(
    r"(?:authorization|proxy-authorization|api[_-]?key|access[_-]?token|password|secret|credential|cookie)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?:\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{12,}|\bhf_[A-Za-z0-9]{12,}|\bgithub_pat_[A-Za-z0-9_]{12,})",
    re.IGNORECASE,
)
_IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_TAILNET_RE = re.compile(r"(?i)\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net\b")


def atomic_write_json(path, value):
    """Atomically replace a JSON artifact without leaving a truncated target."""
    out = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(out) or os.getcwd()
    if not os.path.isdir(parent):
        raise OSError("output directory does not exist: %s" % parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=parent,
                prefix=".%s." % os.path.basename(out), suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            json.dump(
                value,
                handle,
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, out)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def evidence_file_reference(path: str, *, root: str) -> dict[str, Any]:
    """Return a relative, content-bound reference to retained stage evidence."""
    root_path = real_path(root)
    target = real_path(path)
    if not os.path.isfile(target) or not path_is_within(target, root_path):
        raise BenchmarkArtifactError(
            "unsafe_evidence_reference",
            "evidence reference must be a file within the declared artifact root",
        )
    digest = hashlib.sha256()
    size = 0
    with open(target, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return {
        "path": os.path.relpath(target, root_path).replace("\\", "/"),
        "sha256": digest.hexdigest(),
        "bytes": size,
    }


def normalize_evidence_failure_class(value: str) -> str:
    """Map suite-level failure detail into the stable cross-suite taxonomy."""
    if value in EVIDENCE_FAILURE_CLASSES:
        return value
    normalized = FAILURE_CLASS_ALIASES.get(value)
    if normalized is None:
        raise BenchmarkArtifactError(
            "bad_failure_class", f"unknown benchmark failure class {value!r}"
        )
    return normalized


def _redact_private_networks(text: str) -> str:
    def replace_ip(match: re.Match[str]) -> str:
        value = match.group(0)
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return value
        if value in {"127.0.0.1", "100.64.0.10"}:
            return value
        if address.is_private or address.is_loopback or address in ipaddress.ip_network("100.64.0.0/10"):
            return "100.64.0.10"
        return value

    return _TAILNET_RE.sub("100.64.0.10", _IPV4_RE.sub(replace_ip, text))


def sanitize_publishable_evidence(value: Any) -> Any:
    """Return a deep redacted copy suitable for public evidence validation.

    A collision after redacting dictionary keys would silently discard evidence,
    so reject it rather than selecting one of the colliding values.
    """
    def sanitize_text(text: str, *, parse_serialized: bool = True) -> str:
        if parse_serialized and text.lstrip().startswith(("{", "[")):
            duplicates: list[str] = []

            def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for key, child in pairs:
                    if key in result:
                        duplicates.append(key)
                    result[key] = child
                return result

            try:
                decoded = json.loads(text, object_pairs_hook=object_pairs)
            except json.JSONDecodeError:
                decoded = None
            if duplicates:
                raise BenchmarkArtifactError(
                    "unsafe_serialized_json",
                    "serialized JSON contains duplicate keys",
                )
            if isinstance(decoded, (dict, list)):
                sanitized = sanitize(decoded)
                if sanitized != decoded:
                    return json.dumps(sanitized, separators=(",", ":"), ensure_ascii=True)
                return text
        return _redact_private_networks(_SECRET_VALUE_RE.sub("[REDACTED]", text))

    def sanitize(item: Any, *, parent: str = "") -> Any:
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                key_text = str(key)
                public_key = sanitize_text(key_text, parse_serialized=False)
                if public_key in result:
                    raise BenchmarkArtifactError(
                        "redaction_key_collision",
                        "redaction would merge distinct dictionary keys",
                    )
                if _SECRET_KEY_RE.search(key_text):
                    result[public_key] = "[REDACTED]"
                elif parent.lower() in {"env", "environment_values", "headers"}:
                    result[public_key] = "[REDACTED]"
                else:
                    result[public_key] = sanitize(child, parent=key_text)
            return result
        if isinstance(item, list):
            return [sanitize(child, parent=parent) for child in item]
        if isinstance(item, str):
            return sanitize_text(item)
        return copy.deepcopy(item)

    return sanitize(value)


def _validate_reference(value: Any, *, root: str | None, verify_files: bool) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"path", "sha256", "bytes"}:
        raise BenchmarkArtifactError("bad_evidence_reference", "stage evidence reference is invalid")
    path = value.get("path")
    digest = value.get("sha256")
    size = value.get("bytes")
    if (
        not isinstance(path, str)
        or not path
        or os.path.isabs(path)
        or "\x00" in path
        or ".." in re.split(r"[/\\]+", path)
        or real_path(path, base=root or os.getcwd()) == real_path(root or os.getcwd())
        or not isinstance(digest, str)
        or not _HEX64_RE.fullmatch(digest)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
    ):
        raise BenchmarkArtifactError("bad_evidence_reference", "stage evidence reference is invalid")
    if verify_files:
        if not root:
            raise BenchmarkArtifactError("missing_evidence_root", "file hash validation requires artifact_root")
        target = real_path(path, base=root)
        if not path_is_within(target, real_path(root)) or not os.path.isfile(target):
            raise BenchmarkArtifactError("unsafe_evidence_reference", "referenced evidence file is absent or outside its root")
        observed = evidence_file_reference(target, root=root)
        if observed["sha256"] != digest or observed["bytes"] != size:
            raise BenchmarkArtifactError("evidence_digest_mismatch", "referenced evidence content does not match")
    return copy.deepcopy(value)


def _valid_measurement(value: Any) -> bool:
    """Accept only observed non-negative finite numeric values and distributions."""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int):
        return value >= 0
    return isinstance(value, float) and value >= 0 and math.isfinite(value)


def _valid_distribution(value: Any, *, requests: int | None, complete: bool) -> bool:
    return (isinstance(value, dict) and set(value) == {"samples", "p50", "p95"}
            and isinstance(value["samples"], int) and not isinstance(value["samples"], bool)
            and value["samples"] >= 1 and (requests is None or value["samples"] <= requests)
            and (not complete or requests is None or value["samples"] == requests)
            and _valid_measurement(value["p50"]) and _valid_measurement(value["p95"])
            and value["p95"] >= value["p50"])


def _valid_metric_observation(metric: str, record: dict[str, Any], planned: dict[str, Any] | None, *, complete: bool) -> bool:
    value, observation = record.get("value"), record.get("observation")
    if value is not None and not _valid_measurement(value):
        return False
    if observation is not None and not isinstance(observation, dict):
        return False
    timing = {"effective_prefill_tok_s", "time_to_first_output_ms", "ttft_ms", "decode_tok_s", "tpot_ms", "e2e_ms", "prompt_tokens", "completion_tokens"}
    if metric in timing:
        return _valid_distribution(observation, requests=planned.get("requests") if planned else None, complete=complete)
    if metric == "concurrency":
        return (isinstance(observation, dict) and set(observation) == {"requested", "client_max", "engine_max"}
                and all(isinstance(observation[key], int) and not isinstance(observation[key], bool) and observation[key] >= 1 for key in observation)
                and (planned is None or observation["requested"] == planned["concurrency"]))
    if metric == "gpu_memory":
        required = {"accounting", "peak_used_bytes", "min_free_bytes"}
        return (isinstance(observation, dict) and set(observation) == required
                and isinstance(observation["accounting"], str) and bool(observation["accounting"])
                and all(_valid_measurement(observation[key]) for key in required - {"accounting"}))
    if metric == "host_memory":
        required = {"accounting", "cgroup_current_bytes", "cgroup_peak_bytes", "cgroup_limit_bytes", "host_available_bytes"}
        return (isinstance(observation, dict) and set(observation) == required
                and isinstance(observation["accounting"], str) and bool(observation["accounting"])
                and all(_valid_measurement(observation[key]) for key in required - {"accounting"}))
    if metric == "admission_status":
        return (isinstance(observation, dict) and set(observation) == {"refused_requests", "preemptions", "ooms"}
                and all(isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in observation.values()))
    return _valid_measurement(value)


def _validate_cell_measurement(value: Any, *, root: str | None, verify_files: bool, arm: dict[str, Any], cell: dict[str, Any], complete: bool) -> tuple[dict[str, Any], dict[str, Any] | None]:
    receipt = _validate_reference(value, root=root, verify_files=verify_files)
    if not verify_files:
        return receipt, None
    try:
        with open(real_path(receipt["path"], base=root), encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkArtifactError("bad_metrics_coverage", "cell measurement receipt is not JSON") from exc
    if not isinstance(payload, dict) or payload.get("profile") != SERVING_COMPARISON_PROFILE:
        raise BenchmarkArtifactError("bad_metrics_coverage", "cell measurement receipt profile is invalid")
    if payload.get("arm") != cell["arm"] or payload.get("identity") != arm or payload.get("cell") != cell:
        raise BenchmarkArtifactError("bad_metrics_coverage", "cell measurement receipt does not match frozen plan")
    raw = _validate_reference(payload.get("raw"), root=root, verify_files=True)
    try:
        with open(real_path(raw["path"], base=root), encoding="utf-8") as handle:
            native = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkArtifactError("bad_metrics_coverage", "cell native measurement is not JSON") from exc
    if not isinstance(native, dict):
        raise BenchmarkArtifactError("bad_metrics_coverage", "cell native measurement must be an object")
    completed, failed = native.get("completed"), native.get("failed")
    timings = native.get("request_timings")
    workload = native.get("workload_identity")
    population_valid = (
        all(isinstance(count, int) and not isinstance(count, bool) and count >= 0
            for count in (completed, failed))
        and completed + failed == cell["requests"]
        and isinstance(timings, list) and len(timings) == completed
        and all(isinstance(row, dict) for row in timings)
        and (not complete or failed == 0)
    )
    if (native.get("schema") != "anvil-serving.benchmark/v1" or not population_valid
            or not isinstance(workload, dict) or workload.get("workload_kind") != cell["workload"]
            or any(native.get(key) != expected for key, expected in (
                ("model", arm["model"]), ("context_tokens", cell["context_tokens"]),
                ("concurrency", cell["concurrency"]), ("requests", cell["requests"]),
            ))):
        raise BenchmarkArtifactError("bad_metrics_coverage", "cell native measurement does not match frozen plan")
    return receipt, raw


def _validate_serving_metrics_coverage(
    value: Any, *, root: str | None, verify_files: bool
) -> dict[str, Any]:
    """Validate a frozen serving comparison matrix without inventing measurements."""
    if not isinstance(value, dict) or value.get("profile") != SERVING_COMPARISON_PROFILE:
        raise BenchmarkArtifactError("bad_metrics_coverage", "serving metrics coverage profile is invalid")
    status = value.get("status")
    if status not in {"complete", "partial"}:
        raise BenchmarkArtifactError("bad_metrics_coverage", "serving metrics coverage status is invalid")
    frozen_plan = _validate_reference(value.get("frozen_plan"), root=root, verify_files=verify_files)
    plan_verified = False
    plan_cells: dict[str, Any] | None = None
    arms: dict[str, Any] | None = None
    if verify_files:
        try:
            with open(real_path(frozen_plan["path"], base=root), encoding="utf-8") as handle:
                plan = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise BenchmarkArtifactError("bad_metrics_coverage", "frozen serving comparison plan is not JSON") from exc
        if not isinstance(plan, dict):
            raise BenchmarkArtifactError("bad_metrics_coverage", "frozen serving comparison plan must be an object")
        arms = plan.get("arms")
        plan_cells = plan.get("required_cells") if isinstance(plan, dict) else None
        source_files = plan.get("source_file_hashes") if isinstance(plan, dict) else None
        if (plan.get("profile") != SERVING_COMPARISON_PROFILE or plan.get("frozen_before_requests") is not True
                or not isinstance(plan.get("source_revision"), str) or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", plan["source_revision"])
                or not isinstance(source_files, list) or not source_files
                or not isinstance(arms, dict)
                or len(arms) < 2 or not isinstance(plan_cells, dict) or not plan_cells):
            raise BenchmarkArtifactError("bad_metrics_coverage", "frozen serving comparison plan is incomplete")
        for source_file in source_files:
            _validate_reference(source_file, root=root, verify_files=True)
        for arm in arms.values():
            if not isinstance(arm, dict) or any(not isinstance(arm.get(key), str) or not arm[key]
                                                for key in ("model", "revision", "image", "runtime", "hardware")):
                raise BenchmarkArtifactError("bad_metrics_coverage", "frozen serving comparison arm is incomplete")
        represented_arms: set[str] = set()
        arm_shapes: dict[str, list[tuple[str, int, int, int]]] = {}
        for cell in plan_cells.values():
            if (not isinstance(cell, dict) or set(cell) != {"arm", "workload", "context_tokens", "concurrency", "requests"}
                    or not isinstance(cell.get("arm"), str) or cell["arm"] not in arms or not isinstance(cell["workload"], str) or not cell["workload"]
                    or any(not isinstance(cell[key], int) or isinstance(cell[key], bool) or cell[key] < 1
                           for key in ("context_tokens", "concurrency", "requests"))):
                raise BenchmarkArtifactError("bad_metrics_coverage", "frozen serving comparison cell is invalid")
            represented_arms.add(cell["arm"])
            arm_shapes.setdefault(cell["arm"], []).append((cell["workload"], cell["context_tokens"], cell["concurrency"], cell["requests"]))
        if represented_arms != set(arms):
            raise BenchmarkArtifactError("bad_metrics_coverage", "frozen serving comparison plan omits an arm")
        if len({tuple(sorted(shape)) for shape in arm_shapes.values()}) != 1:
            raise BenchmarkArtifactError("bad_metrics_coverage", "frozen serving comparison matrix differs across arms")
        plan_verified = True
    required_cells = value.get("required_cells")
    if (not isinstance(required_cells, list) or not required_cells
            or any(not isinstance(cell, str) or not cell for cell in required_cells)
            or len(set(required_cells)) != len(required_cells)):
        raise BenchmarkArtifactError("bad_metrics_coverage", "serving metrics coverage requires unique required cells")
    if plan_cells is not None and set(required_cells) != set(plan_cells):
        raise BenchmarkArtifactError("bad_metrics_coverage", "serving metrics coverage does not match the frozen plan")
    cells = value.get("cells")
    if not isinstance(cells, dict) or any(cell not in required_cells for cell in cells):
        raise BenchmarkArtifactError("bad_metrics_coverage", "serving metrics coverage cells are invalid")
    gaps: list[str] = []
    normalized_cells: dict[str, dict[str, Any]] = {}
    for cell_id in required_cells:
        cell = cells.get(cell_id)
        if not isinstance(cell, dict) or set(cell) != {"metrics", "measurement"} or not isinstance(cell["metrics"], dict):
            gaps.extend(f"{cell_id}.{metric}" for metric in sorted(SERVING_METRIC_KEYS))
            continue
        metrics = cell["metrics"]
        has_measured = any(isinstance(record, dict) and record.get("status") == "measured"
                           for record in metrics.values())
        if cell["measurement"] is None and not has_measured:
            measurement, raw_measurement = None, None
        else:
            measurement, raw_measurement = _validate_cell_measurement(
                cell["measurement"], root=root, verify_files=verify_files,
                arm=arms[plan_cells[cell_id]["arm"]] if arms and plan_cells else {},
                cell=plan_cells[cell_id] if plan_cells else {}, complete=status == "complete",
            )
        if any(metric not in SERVING_METRIC_KEYS for metric in metrics):
            raise BenchmarkArtifactError("bad_metrics_coverage", "serving metrics coverage metric is unknown")
        normalized_metrics: dict[str, dict[str, Any]] = {}
        for metric in sorted(SERVING_METRIC_KEYS):
            record = metrics.get(metric)
            if record is None:
                gaps.append(f"{cell_id}.{metric}")
                continue
            if not isinstance(record, dict) or set(record) - {"status", "value", "observation", "reason", "evidence"}:
                raise BenchmarkArtifactError("bad_metrics_coverage", "serving metric record is invalid")
            metric_status = record.get("status")
            if metric_status not in SERVING_METRIC_STATUSES:
                raise BenchmarkArtifactError("bad_metrics_coverage", "serving metric status is invalid")
            has_value = _valid_metric_observation(metric, record, plan_cells.get(cell_id) if plan_cells else None, complete=status == "complete")
            evidence = record.get("evidence")
            if metric_status == "measured":
                if not has_value or not isinstance(evidence, list) or not evidence:
                    raise BenchmarkArtifactError("bad_metrics_coverage", "measured serving metric needs an observation and evidence")
                normalized = copy.deepcopy(record)
                normalized["evidence"] = [
                    _validate_reference(item, root=root, verify_files=verify_files)
                    for item in evidence
                ]
                if raw_measurement is not None and raw_measurement not in normalized["evidence"]:
                    raise BenchmarkArtifactError("bad_metrics_coverage", "measured serving metric must reference its cell measurement")
                normalized_metrics[metric] = normalized
                continue
            if not isinstance(record.get("reason"), str) or not record["reason"]:
                raise BenchmarkArtifactError("bad_metrics_coverage", "unmeasured serving metric needs a reason")
            if metric_status in {"configured", "estimated"}:
                if not isinstance(evidence, list) or not evidence:
                    raise BenchmarkArtifactError("bad_metrics_coverage", "configured or estimated serving metric needs evidence")
                normalized = copy.deepcopy(record)
                normalized["evidence"] = [
                    _validate_reference(item, root=root, verify_files=verify_files)
                    for item in evidence
                ]
                normalized_metrics[metric] = normalized
            else:
                normalized_metrics[metric] = copy.deepcopy(record)
            gaps.append(f"{cell_id}.{metric}")
        normalized_cells[cell_id] = {"measurement": measurement, "metrics": normalized_metrics}
    if status == "complete" and gaps:
        raise BenchmarkArtifactError("false_metrics_completion", "complete serving metrics coverage has missing or unmeasured metrics")
    supplied_gaps = value.get("missing_required_metrics")
    if supplied_gaps is not None and supplied_gaps != gaps:
        raise BenchmarkArtifactError("bad_metrics_coverage", "serving metrics coverage gaps do not match the matrix")
    return {
        "profile": SERVING_COMPARISON_PROFILE,
        "status": status,
        "frozen_plan": frozen_plan,
        "plan_verified": plan_verified,
        "required_cells": list(required_cells),
        "cells": normalized_cells,
        "missing_required_metrics": gaps,
    }


def validate_cross_suite_evidence(
    value: Any,
    *,
    artifact_root: str | None = None,
    verify_files: bool = True,
    publishable: bool = False,
) -> dict[str, Any]:
    """Validate measured/prior separation, completeness, stages, and file hashes."""
    if not isinstance(value, dict) or value.get("schema") != CROSS_SUITE_EVIDENCE_SCHEMA:
        raise BenchmarkArtifactError("bad_evidence_schema", "cross-suite evidence schema is invalid")
    evidence = copy.deepcopy(value)
    kind = evidence.get("evidence_kind")
    if kind not in EVIDENCE_KINDS:
        raise BenchmarkArtifactError("bad_evidence_kind", "evidence kind must be measured or external_prior")
    promotion = evidence.get("promotion")
    if not isinstance(promotion, dict) or promotion.get("authorized") is not False or promotion.get("message") != PROMOTION_DISCLAIMER:
        raise BenchmarkArtifactError("missing_promotion_boundary", "evidence must preserve the human promotion boundary")
    if publishable and sanitize_publishable_evidence(evidence) != evidence:
        raise BenchmarkArtifactError("unsafe_publishable_evidence", "evidence contains secrets or private topology")
    if kind == "external_prior":
        if "run" in evidence or "stages" in evidence or "identities" in evidence or "metrics_coverage" in evidence:
            raise BenchmarkArtifactError("prior_claims_measurement", "external priors cannot carry local run identities or stages")
        source = evidence.get("source")
        if not isinstance(source, dict) or not isinstance(source.get("url"), str) or not source["url"].startswith("https://"):
            raise BenchmarkArtifactError("bad_external_prior", "external prior requires an HTTPS source")
        if evidence.get("locally_measured") is not False:
            raise BenchmarkArtifactError("prior_claims_measurement", "external prior must state locally_measured=false")
        return evidence

    completeness = evidence.get("completeness")
    if completeness not in EVIDENCE_COMPLETENESS:
        raise BenchmarkArtifactError("bad_evidence_completeness", "measured evidence completeness is invalid")
    created_at = evidence.get("created_at")
    try:
        parsed_created_at = dt.datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise BenchmarkArtifactError("bad_evidence_timestamp", "measured evidence created_at is invalid") from exc
    if parsed_created_at.tzinfo is None or parsed_created_at.utcoffset() is None:
        raise BenchmarkArtifactError("bad_evidence_timestamp", "measured evidence created_at requires a UTC offset")
    run = evidence.get("run")
    if not isinstance(run, dict) or not all(isinstance(run.get(key), str) and run[key] for key in (
        "run_id", "ownership_id", "suite", "profile", "spec_sha256"
    )) or not _HEX64_RE.fullmatch(run["spec_sha256"]):
        raise BenchmarkArtifactError("missing_evidence_identity", "measured run identity is incomplete")
    identities = evidence.get("identities")
    if not isinstance(identities, dict) or set(identities) != REQUIRED_MEASURED_IDENTITIES:
        raise BenchmarkArtifactError("missing_evidence_identity", "measured artifact identities are incomplete")
    if any(value is None for value in identities.values()):
        raise BenchmarkArtifactError("missing_evidence_identity", "identity unavailability must be explicit")
    stages = evidence.get("stages")
    if not isinstance(stages, list) or not stages:
        raise BenchmarkArtifactError("missing_evidence_stage", "measured artifact requires ordered stages")
    for sequence, stage in enumerate(stages):
        if not isinstance(stage, dict) or stage.get("sequence") != sequence:
            raise BenchmarkArtifactError("bad_stage_order", "evidence stages must be contiguous and ordered")
        if stage.get("status") not in {"completed", "failed", "cancelled", "incomplete"}:
            raise BenchmarkArtifactError("bad_stage_status", "evidence stage status is invalid")
        if not isinstance(stage.get("name"), str) or not stage["name"]:
            raise BenchmarkArtifactError("bad_stage_status", "evidence stage name is required")
        references = stage.get("evidence")
        if not isinstance(references, list):
            raise BenchmarkArtifactError("bad_evidence_reference", "stage evidence must be a reference list")
        stage["evidence"] = [
            _validate_reference(item, root=artifact_root, verify_files=verify_files)
            for item in references
        ]
    failure = evidence.get("failure")
    if completeness == "completed":
        if failure is not None or any(stage["status"] != "completed" for stage in stages):
            raise BenchmarkArtifactError("false_completion", "completed evidence requires successful stages and no failure")
    else:
        summary = evidence.get("summary")
        if isinstance(summary, dict) and (
            summary.get("completed") is True or summary.get("status") in {"completed", "passed"}
        ):
            raise BenchmarkArtifactError("false_completion", "partial evidence cannot make completed-run assertions")
    if failure is not None:
        if not isinstance(failure, dict) or failure.get("class") not in EVIDENCE_FAILURE_CLASSES:
            raise BenchmarkArtifactError("bad_failure_class", "benchmark failure taxonomy is invalid")
    elif completeness in {"failed", "cancelled"}:
        raise BenchmarkArtifactError("missing_failure", "failed and cancelled evidence require failure identity")
    coverage = evidence.get("metrics_coverage")
    if run["profile"] == SERVING_COMPARISON_PROFILE:
        if coverage is None:
            raise BenchmarkArtifactError("missing_metrics_coverage", "serving comparison evidence requires metrics coverage")
        evidence["metrics_coverage"] = _validate_serving_metrics_coverage(
            coverage, root=artifact_root, verify_files=verify_files
        )
    elif coverage is not None:
        evidence["metrics_coverage"] = _validate_serving_metrics_coverage(
            coverage, root=artifact_root, verify_files=verify_files
        )
    return evidence


def build_measured_evidence(
    *,
    run: dict[str, Any],
    identities: dict[str, Any],
    stages: list[dict[str, Any]],
    completeness: str,
    summary: dict[str, Any],
    failure: dict[str, Any] | None = None,
    created_at: str | None = None,
    artifact_root: str | None = None,
    verify_files: bool = True,
    metrics_coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and validate the common envelope for a locally measured run."""
    normalized_failure = copy.deepcopy(failure)
    if normalized_failure is not None and isinstance(normalized_failure.get("class"), str):
        normalized_failure["class"] = normalize_evidence_failure_class(
            normalized_failure["class"]
        )
    value = {
        "schema": CROSS_SUITE_EVIDENCE_SCHEMA,
        "evidence_kind": "measured",
        "completeness": completeness,
        "created_at": created_at or dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "run": copy.deepcopy(run),
        "identities": copy.deepcopy(identities),
        "stages": copy.deepcopy(stages),
        "summary": copy.deepcopy(summary),
        "failure": normalized_failure,
        "promotion": {"authorized": False, "message": PROMOTION_DISCLAIMER},
    }
    if metrics_coverage is not None:
        value["metrics_coverage"] = copy.deepcopy(metrics_coverage)
    return validate_cross_suite_evidence(
        value,
        artifact_root=artifact_root,
        verify_files=verify_files,
    )


def build_external_prior_record(*, source: dict[str, Any], claims: dict[str, Any]) -> dict[str, Any]:
    """Build an explicitly non-measured record for a dated external benchmark prior."""
    value = {
        "schema": CROSS_SUITE_EVIDENCE_SCHEMA,
        "evidence_kind": "external_prior",
        "locally_measured": False,
        "source": copy.deepcopy(source),
        "claims": copy.deepcopy(claims),
        "promotion": {"authorized": False, "message": PROMOTION_DISCLAIMER},
    }
    return validate_cross_suite_evidence(value, verify_files=False)


def validate_write_target(path, *, label="output"):
    """Fail before live work when a requested artifact cannot be replaced safely."""
    if not path or path == "-":
        return None
    out = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(out) or os.getcwd()
    if not os.path.isdir(parent):
        raise OSError("%s directory does not exist: %s" % (label, parent))
    if os.path.islink(out):
        raise OSError("%s path cannot be a symbolic link: %s" % (label, out))
    if os.path.exists(out) and not os.path.isfile(out):
        raise OSError("%s path is not a regular file: %s" % (label, out))
    if not os.access(parent, os.W_OK):
        raise OSError("%s directory is not writable: %s" % (label, parent))
    return out


def console_safe(value):
    """Render a value without failing on a restricted console encoding."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(value).encode(encoding, errors="backslashreplace").decode(encoding)


WORKSPACE_ROOT_ENVS = ("ANVIL_WORKSPACE_ROOT",)


BENCHMARK_EVIDENCE_DIR_ENVS = (
    "ANVIL_BENCHMARK_EVIDENCE_DIR",
    "ANVIL_EVIDENCE_DIR",
)


def real_path(path: str, *, base: str | None = None) -> str:
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        expanded = os.path.join(base or os.getcwd(), expanded)
    return os.path.realpath(os.path.abspath(expanded))


def path_is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath(
            [os.path.normcase(path), os.path.normcase(root)]
        ) == os.path.normcase(root)
    except ValueError:
        return False


def is_filesystem_root(path: str) -> bool:
    norm = os.path.normpath(path)
    return os.path.dirname(norm) == norm


def has_workspace_marker(path: str) -> bool:
    pyproject = os.path.join(path, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject, encoding="utf-8") as handle:
                text = handle.read(4096)
        except OSError:
            return False
        if "anvil-serving" in text:
            return True
    readme = os.path.join(path, "README.md")
    if os.path.isfile(readme):
        try:
            with open(readme, encoding="utf-8") as handle:
                text = handle.read(4096)
        except OSError:
            return False
        if "# anvil-serving" in text or "local-model serving" in text:
            return True
    return False


def discover_workspace_root(start: str | None = None) -> str:
    for env_name in WORKSPACE_ROOT_ENVS:
        raw = (os.environ.get(env_name) or "").strip()
        if raw:
            root = real_path(raw)
            if (
                is_filesystem_root(root)
                or not os.path.isdir(root)
                or not has_workspace_marker(root)
            ):
                raise BenchmarkArtifactError(
                    "bad_workspace_root",
                    ("%s must point to an anvil-serving workspace, not a broad filesystem root")
                    % env_name,
                    {"env": env_name, "workspace": root},
                )
            return root

    current = real_path(start or os.getcwd())
    while True:
        if has_workspace_marker(current):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    package_root = real_path(os.path.join(os.path.dirname(__file__), "..", ".."))
    return package_root if has_workspace_marker(package_root) else ""


def configured_benchmark_evidence_roots() -> list[str]:
    roots = []
    for env_name in BENCHMARK_EVIDENCE_DIR_ENVS:
        raw = os.environ.get(env_name, "")
        for item in raw.split(os.pathsep):
            item = item.strip()
            if item:
                root = real_path(item)
                if is_filesystem_root(root):
                    raise BenchmarkArtifactError(
                        "bad_evidence_dir",
                        "%s must not point at a broad filesystem root" % env_name,
                        {"env": env_name, "evidence_dir": root},
                    )
                roots.append(root)
    return roots


def resolve_benchmark_artifact_path(path: str) -> tuple[str, list[str]]:
    if not path:
        raise BenchmarkArtifactError(
            "missing_argument",
            "missing required argument 'artifact_path'",
        )
    if path == "-":
        raise BenchmarkArtifactError(
            "bad_artifact_path",
            "artifact_path must be a file path, not '-'",
        )
    if "\x00" in path:
        raise BenchmarkArtifactError(
            "bad_artifact_path",
            "artifact_path must not contain NUL bytes",
        )

    workspace = discover_workspace_root()
    roots = [workspace] if workspace else []
    roots.extend(root for root in configured_benchmark_evidence_roots() if root not in roots)
    if not roots:
        raise BenchmarkArtifactError(
            "missing_artifact_root",
            ("artifact_path requires an anvil-serving workspace or configured evidence directory"),
            {
                "workspace_envs": list(WORKSPACE_ROOT_ENVS),
                "evidence_dir_envs": list(BENCHMARK_EVIDENCE_DIR_ENVS),
            },
        )

    if os.path.isabs(os.path.expanduser(path)):
        artifact_path = real_path(path)
    elif workspace:
        artifact_path = real_path(path, base=workspace)
    elif len(roots) == 1:
        artifact_path = real_path(path, base=roots[0])
    else:
        raise BenchmarkArtifactError(
            "bad_artifact_path",
            (
                "relative artifact_path requires a workspace when multiple "
                "evidence roots are configured"
            ),
        )
    if not any(path_is_within(artifact_path, root) for root in roots):
        raise BenchmarkArtifactError(
            "unsafe_artifact_path",
            ("artifact_path must be inside the workspace or configured evidence directory"),
            {
                "artifact_path": artifact_path,
                "workspace": workspace or None,
                "evidence_dirs": roots[1:] if workspace else roots,
                "workspace_envs": list(WORKSPACE_ROOT_ENVS),
                "evidence_dir_envs": list(BENCHMARK_EVIDENCE_DIR_ENVS),
            },
        )
    if os.path.isdir(artifact_path):
        raise BenchmarkArtifactError(
            "bad_artifact_path",
            "artifact_path points at a directory",
            {"artifact_path": artifact_path},
        )
    return artifact_path, roots


def benchmark_key_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    metrics = summary.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}
    keys = (
        "ttft_p50_ms",
        "ttft_p95_ms",
        "e2e_p50_ms",
        "e2e_p95_ms",
        "throughput_tok_s",
        "output_tokens",
        "prefix_cache_hit_avg",
    )
    return {
        "requests": summary.get("requests"),
        "completed": summary.get("completed"),
        "concurrency": summary.get("concurrency"),
        "context_tokens": summary.get("context_tokens"),
        "max_context_tokens": summary.get("max_context_tokens"),
        "max_tokens": summary.get("max_tokens"),
        **{key: metrics.get(key) for key in keys},
    }


def metric_delta(local_value: Any, external_value: Any) -> dict[str, Any]:
    def as_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    local_num = as_float(local_value)
    external_num = as_float(external_value)
    delta_abs = None
    delta_pct = None
    if local_num is not None and external_num not in (None, 0.0):
        delta_abs = local_num - external_num
        delta_pct = (delta_abs / external_num) * 100.0
    return {
        "local": local_num,
        "external": external_num,
        "delta_abs": delta_abs,
        "delta_pct": delta_pct,
    }


def read_benchmark_artifact(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        raise BenchmarkArtifactError(
            "artifact_not_written",
            "benchmark completed but JSON artifact was not written",
            {"artifact_path": path},
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            summary = json.load(f)
    except OSError as exc:
        raise BenchmarkArtifactError(
            "artifact_read_failed",
            str(exc),
            {"artifact_path": path},
        )
    except ValueError as exc:
        raise BenchmarkArtifactError(
            "bad_benchmark_artifact",
            "benchmark artifact is not valid JSON",
            {"artifact_path": path, "error": str(exc)},
        )
    if not isinstance(summary, dict):
        raise BenchmarkArtifactError(
            "bad_benchmark_artifact",
            "benchmark artifact must be a JSON object",
            {"artifact_path": path},
        )
    return summary
