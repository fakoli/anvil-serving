#!/usr/bin/env python3
"""Combine capacity artifacts from concurrent independent model replicas."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import statistics
import tempfile


_MATCHED_FIELDS = (
    "measurement_protocol", "engine", "context_tokens", "max_context_tokens",
    "max_tokens", "response_words", "prompt_cache_mode", "request_canaries",
    "controlled_output_policy", "context_seed", "serve_flags",
)

_CONFIGURATION_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CLOCK_RECONCILIATION_MIN_TOLERANCE_MS = 5.0
_CLOCK_RECONCILIATION_REL_TOLERANCE = 0.0001
_DERIVED_TIMING_FIELDS = (
    "generation_ms", "visible_generation_ms", "effective_prefill_tok_s",
    "decode_tok_s", "mean_inter_token_latency_ms",
    "mean_time_per_output_token_ms", "tpot_ms",
)


def _percentiles(values: list[float], quantiles: tuple[int, ...]) -> list[float]:
    ordered = sorted(values)
    return [
        ordered[max(0, math.ceil(len(ordered) * quantile / 100) - 1)]
        for quantile in quantiles
    ]


def _timing_distribution(rows: list[dict], key: str) -> dict:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    names = (
        "samples", "mean", "stddev", "mean_ci95_low", "mean_ci95_high",
        "min", "p25", "p50", "p75", "p90", "p95", "p99", "max",
    )
    if not values:
        return {name: 0 if name == "samples" else None for name in names}
    p25, p50, p75, p90, p95, p99 = _percentiles(
        values, (25, 50, 75, 90, 95, 99)
    )
    mean = statistics.fmean(values)
    stddev = statistics.stdev(values) if len(values) > 1 else 0.0
    half_width = 1.96 * stddev / math.sqrt(len(values))
    return {
        "samples": len(values), "mean": mean, "stddev": stddev,
        "mean_ci95_low": mean - half_width,
        "mean_ci95_high": mean + half_width, "min": min(values),
        "p25": p25, "p50": p50, "p75": p75, "p90": p90,
        "p95": p95, "p99": p99, "max": max(values),
    }


def _distribution_metrics(
    prefix: str, distribution: dict, *, unit_suffix: str = ""
) -> dict:
    return {
        (f"{prefix}_samples" if statistic == "samples"
         else f"{prefix}_{statistic}{unit_suffix}"): value
        for statistic, value in distribution.items()
    }


def _finite_nonnegative(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    resolved = float(value)
    if not math.isfinite(resolved) or resolved < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return resolved


def _integer(value: object, *, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer of at least {minimum}")
    return value


def _validated_timing_row(row: object, *, path: Path, position: int) -> dict:
    label = f"{path}: request_timings[{position}]"
    if not isinstance(row, dict):
        raise ValueError(f"{label} must be an object")
    request_index = _integer(row.get("request_index"), label=f"{label}.request_index")
    prompt_tokens = _integer(row.get("prompt_tokens"), label=f"{label}.prompt_tokens")
    output_tokens = _integer(
        row.get("output_tokens"), label=f"{label}.output_tokens", minimum=1
    )
    if row.get("output_token_source") not in (None, "usage"):
        raise ValueError(f"{label}.output_token_source must be usage")
    first_output = _finite_nonnegative(
        row.get("time_to_first_output_ms"),
        label=f"{label}.time_to_first_output_ms",
    )
    ttft = _finite_nonnegative(row.get("ttft_ms"), label=f"{label}.ttft_ms")
    e2e = _finite_nonnegative(row.get("e2e_ms"), label=f"{label}.e2e_ms")
    if first_output > ttft or ttft > e2e:
        raise ValueError(
            f"{label} requires time_to_first_output_ms <= ttft_ms <= e2e_ms"
        )
    for key in _DERIVED_TIMING_FIELDS:
        if row.get(key) is not None:
            _finite_nonnegative(row[key], label=f"{label}.{key}")
    for key in ("content_chunks", "reasoning_chunks", "planned_context_tokens"):
        if row.get(key) is not None:
            _integer(row[key], label=f"{label}.{key}")

    generation = e2e - first_output
    visible_generation = e2e - ttft
    decode_tokens = output_tokens - 1
    tpot = (
        generation / decode_tokens
        if decode_tokens > 0 and generation > 0 else None
    )
    normalized = {
        **row,
        "request_index": request_index,
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "generation_ms": generation,
        "visible_generation_ms": visible_generation,
        "effective_prefill_tok_s": (
            prompt_tokens / (first_output / 1000.0) if first_output > 0 else None
        ),
        "decode_tok_s": (
            decode_tokens / (generation / 1000.0)
            if decode_tokens > 0 and generation > 0 else None
        ),
        "mean_inter_token_latency_ms": tpot,
        "mean_time_per_output_token_ms": tpot,
        "tpot_ms": tpot,
    }
    return normalized


def _validate_controlled_output(
    row: dict, *, path: Path, response_words: int, policy: str | None
) -> None:
    controlled = row.get("controlled_output")
    if not response_words or policy is None:
        if controlled is not None:
            raise ValueError(f"{path}: unexpected controlled-output observation")
        return
    if not isinstance(controlled, dict):
        raise ValueError(f"{path}: controlled-output observation is missing")
    if controlled.get("policy") != policy:
        raise ValueError(f"{path}: controlled-output policy differs from the artifact")
    requested_words = _integer(
        controlled.get("requested_words"),
        label=f"{path}: controlled-output requested_words",
        minimum=1,
    )
    if requested_words != response_words:
        raise ValueError(
            f"{path}: controlled-output requested_words differs from the artifact"
        )
    observed_code_words = _integer(
        controlled.get("observed_code_words"),
        label=f"{path}: controlled-output observed_code_words",
    )
    observed_extra_words = _integer(
        controlled.get("observed_extra_words"),
        label=f"{path}: controlled-output observed_extra_words",
    )
    capture_complete = controlled.get("capture_complete")
    if not isinstance(capture_complete, bool):
        raise ValueError(f"{path}: controlled-output capture status must be boolean")
    exact_adherence = controlled.get("exact_adherence")
    if not (
        exact_adherence is True
        or exact_adherence is False
        or exact_adherence is None
    ):
        raise ValueError(f"{path}: controlled-output adherence must be boolean or null")
    expected_adherence = (
        observed_code_words == requested_words and observed_extra_words == 0
        if capture_complete else None
    )
    if exact_adherence is not expected_adherence:
        raise ValueError(
            f"{path}: controlled-output adherence is inconsistent with observations"
        )
    passed = controlled.get("passed")
    if not isinstance(passed, bool):
        raise ValueError(f"{path}: controlled-output passed must be boolean")
    expected_passed = exact_adherence is True if policy == "strict" else True
    if passed is not expected_passed:
        raise ValueError(
            f"{path}: controlled-output passed is inconsistent with its policy"
        )
    if policy == "strict" and not capture_complete:
        raise ValueError(f"{path}: strict controlled-output capture is incomplete")
    if policy == "strict" and exact_adherence is not True:
        raise ValueError(f"{path}: strict controlled output did not adhere")


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: replica artifact must be an object")
    if payload.get("schema") != "anvil-serving.benchmark/v1":
        raise ValueError(f"{path}: expected anvil-serving.benchmark/v1")
    requests = _integer(payload.get("requests"), label=f"{path}: requests", minimum=1)
    completed = _integer(payload.get("completed"), label=f"{path}: completed")
    failed = _integer(payload.get("failed"), label=f"{path}: failed")
    _integer(payload.get("concurrency"), label=f"{path}: concurrency", minimum=1)
    if failed or completed != requests:
        raise ValueError(f"{path}: only complete, zero-failure artifacts can combine")
    performance_eligible = payload.get("performance_eligible")
    if performance_eligible is not None and not isinstance(performance_eligible, bool):
        raise ValueError(f"{path}: performance_eligible must be boolean or null")
    if performance_eligible is False:
        raise ValueError(f"{path}: artifact is not performance eligible")
    _finite_nonnegative(payload.get("wall_clock_ms"), label=f"{path}: wall_clock_ms")
    for key, minimum in (
        ("context_tokens", 0), ("max_context_tokens", 1),
        ("max_tokens", 1), ("response_words", 0),
    ):
        if payload.get(key) is not None:
            _integer(payload[key], label=f"{path}: {key}", minimum=minimum)
    if payload.get("context_seed") is not None and (
        isinstance(payload["context_seed"], bool)
        or not isinstance(payload["context_seed"], int)
    ):
        raise ValueError(f"{path}: context_seed must be an integer or null")
    if payload.get("prompt_cache_mode") not in ("shared", "unique"):
        raise ValueError(f"{path}: prompt_cache_mode must be shared or unique")
    if not isinstance(payload.get("request_canaries"), bool):
        raise ValueError(f"{path}: request_canaries must be boolean")
    if payload.get("controlled_output_policy") not in (None, "observe", "strict"):
        raise ValueError(f"{path}: controlled_output_policy is invalid")
    if not isinstance(payload.get("engine"), str) or not payload["engine"]:
        raise ValueError(f"{path}: engine must be a nonempty string")
    if not isinstance(payload.get("model"), str) or not payload["model"]:
        raise ValueError(f"{path}: model must be a nonempty string")
    if payload.get("source_recipe") is not None and not isinstance(
        payload["source_recipe"], str
    ):
        raise ValueError(f"{path}: source_recipe must be a string or null")
    if not isinstance(payload.get("serve_flags"), dict):
        raise ValueError(f"{path}: serve_flags must be an object")
    rows = payload.get("request_timings")
    if not isinstance(rows, list) or len(rows) != completed:
        raise ValueError(f"{path}: request_timings must cover every completed request")
    rows = [
        _validated_timing_row(row, path=path, position=position)
        for position, row in enumerate(rows)
    ]
    indexes = [row["request_index"] for row in rows]
    if sorted(indexes) != list(range(completed)):
        raise ValueError(
            f"{path}: request_index values must be exactly 0 through completed - 1"
        )
    for row in rows:
        canary = row.get("request_canary")
        if canary is not None and not isinstance(canary, dict):
            raise ValueError(f"{path}: request canary must be an object or null")
        if not payload.get("request_canaries") and canary is not None:
            raise ValueError(f"{path}: unexpected request canary observation")
        if payload.get("request_canaries"):
            if not isinstance(canary, dict):
                raise ValueError(f"{path}: required request canary must be an object")
            if canary.get("passed") is not True:
                raise ValueError(f"{path}: every required request canary must pass")
            if payload.get("controlled_output_policy") is not None and not (
                canary.get("marker_at_start") is True
                and canary.get("foreign_marker_count") == 0
                and canary.get("capture_complete") is True
            ):
                raise ValueError(f"{path}: modern request canary evidence is incomplete")
        _validate_controlled_output(
            row,
            path=path,
            response_words=payload.get("response_words") or 0,
            policy=payload.get("controlled_output_policy"),
        )
    if not isinstance(payload.get("metrics"), dict):
        raise ValueError(f"{path}: metrics must be an object")
    recorded_tokens = payload["metrics"].get("output_tokens")
    timing_tokens = sum(row["output_tokens"] for row in rows)
    if recorded_tokens is not None:
        _integer(recorded_tokens, label=f"{path}: metrics.output_tokens")
    if recorded_tokens is not None and recorded_tokens != timing_tokens:
        raise ValueError(f"{path}: output token total differs from request timings")
    throughput = payload["metrics"].get("throughput_tok_s")
    if throughput is not None:
        _finite_nonnegative(throughput, label=f"{path}: metrics.throughput_tok_s")
    payload["request_timings"] = rows
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iso_second(value: object, *, label: str) -> float:
    if not isinstance(value, str):
        raise ValueError(f"missing {label}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {label}: {value}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    if parsed.microsecond:
        raise ValueError(f"{label} must be a whole-second legacy timestamp")
    return parsed.timestamp()


def _timeline(artifacts: list[dict]) -> dict:
    clock_domains = [artifact.get("clock_domain_id") for artifact in artifacts]
    has_any_ns = any(
        artifact.get("started_at_unix_ns") is not None
        or artifact.get("finished_at_unix_ns") is not None
        for artifact in artifacts
    )
    exact_ns = all(
        isinstance(artifact.get("started_at_unix_ns"), int)
        and not isinstance(artifact.get("started_at_unix_ns"), bool)
        and artifact["started_at_unix_ns"] >= 0
        and isinstance(artifact.get("finished_at_unix_ns"), int)
        and not isinstance(artifact.get("finished_at_unix_ns"), bool)
        and artifact["finished_at_unix_ns"] >= artifact["started_at_unix_ns"]
        for artifact in artifacts
    )
    if has_any_ns and not exact_ns:
        raise ValueError("replica nanosecond timestamps must be complete, ordered integers")
    common_clock = (
        bool(clock_domains[0])
        and all(domain == clock_domains[0] for domain in clock_domains)
    )
    if common_clock and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", clock_domains[0]):
        raise ValueError("clock_domain_id must use the public-safe CLI identifier syntax")
    reconciliations = []
    if exact_ns:
        for artifact in artifacts:
            start_floor = _iso_second(artifact.get("started_at"), label="started_at")
            finish_floor = _iso_second(artifact.get("finished_at"), label="finished_at")
            if artifact["started_at_unix_ns"] // 1_000_000_000 != int(start_floor):
                raise ValueError("started_at_unix_ns is inconsistent with started_at")
            if artifact["finished_at_unix_ns"] // 1_000_000_000 != int(finish_floor):
                raise ValueError("finished_at_unix_ns is inconsistent with finished_at")
            epoch_wall_ms = (
                artifact["finished_at_unix_ns"] - artifact["started_at_unix_ns"]
            ) / 1_000_000.0
            monotonic_wall_ms = float(artifact["wall_clock_ms"])
            tolerance_ms = max(
                _CLOCK_RECONCILIATION_MIN_TOLERANCE_MS,
                monotonic_wall_ms * _CLOCK_RECONCILIATION_REL_TOLERANCE,
            )
            delta_ms = abs(epoch_wall_ms - monotonic_wall_ms)
            if delta_ms > tolerance_ms:
                raise ValueError(
                    "replica nanosecond interval does not reconcile with wall_clock_ms"
                )
            reconciliations.append({
                "epoch_interval_ms": epoch_wall_ms,
                "monotonic_wall_ms": monotonic_wall_ms,
                "absolute_delta_ms": delta_ms,
                "tolerance_ms": tolerance_ms,
            })
    if exact_ns and common_clock:
        if max(a["started_at_unix_ns"] for a in artifacts) >= min(
            a["finished_at_unix_ns"] for a in artifacts
        ):
            raise ValueError("replica intervals do not overlap on the explicit common clock")
        wall_ms = (
            max(artifact["finished_at_unix_ns"] for artifact in artifacts)
            - min(artifact["started_at_unix_ns"] for artifact in artifacts)
        ) / 1_000_000.0
        return {
            "alignment": "exact-explicit-common-clock-domain",
            "clock_domain_id": clock_domains[0],
            "timestamp_precision": "nanosecond",
            "wall_clock_ms_lower_bound": wall_ms,
            "wall_clock_ms_upper_bound": wall_ms,
            "wall_clock_value": "exact",
            "replica_clock_reconciliation": reconciliations,
        }

    starts = [_iso_second(a.get("started_at"), label="started_at") for a in artifacts]
    finishes = [_iso_second(a.get("finished_at"), label="finished_at") for a in artifacts]
    if len(set(starts)) != 1 or len(set(finishes)) != 1:
        raise ValueError(
            "legacy replica artifacts must record the same UTC start and finish second"
        )
    feasible_starts = []
    walls = []
    for artifact, start_floor, finish_floor in zip(artifacts, starts, finishes):
        wall = float(artifact["wall_clock_ms"]) / 1000.0
        if not math.isfinite(wall) or wall <= 0:
            raise ValueError("replica wall_clock_ms must be a positive finite number")
        recorded_span = finish_floor - start_floor
        # Work in phase offsets rather than subtracting epoch-sized floats; this
        # preserves the sub-millisecond precision of the local monotonic wall.
        low = max(0.0, recorded_span - wall)
        high = min(1.0, recorded_span + 1.0 - wall)
        if high < low:
            raise ValueError("replica wall clock is inconsistent with recorded UTC seconds")
        feasible_starts.append((low, high))
        walls.append(wall)
    wall_lower_s = max(walls)
    wall_upper_s = max(
        wall_i if index_i == index_j else high_i + wall_i - low_j
        for index_i, ((_low_i, high_i), wall_i) in enumerate(
            zip(feasible_starts, walls)
        )
        for index_j, (low_j, _high_j) in enumerate(feasible_starts)
    )
    wall_upper_s = max(wall_lower_s, wall_upper_s)
    return {
        "alignment": "legacy-second-precision-bounded",
        "clock_domain_id": None,
        "timestamp_precision": "second",
        "wall_clock_ms_lower_bound": wall_lower_s * 1000.0,
        "wall_clock_ms_upper_bound": wall_upper_s * 1000.0,
        "wall_clock_value": "conservative-upper-bound",
        "limitation": (
            "ISO timestamps identify only whole-second buckets; no common clock "
            "domain was recorded, so replica phase alignment is bounded, not exact"
        ),
    }


def _combined_metrics(rows: list[dict], timeline: dict) -> dict:
    distributions = {
        "time_to_first_output": ("time_to_first_output_ms", "_ms"),
        "ttft": ("ttft_ms", "_ms"), "generation": ("generation_ms", "_ms"),
        "e2e": ("e2e_ms", "_ms"),
        "effective_prefill_tok_s": ("effective_prefill_tok_s", ""),
        "decode_tok_s": ("decode_tok_s", ""),
        "mean_inter_token_latency_ms": ("mean_inter_token_latency_ms", ""),
        "mean_time_per_output_token": ("mean_inter_token_latency_ms", "_ms"),
        "tpot": ("mean_inter_token_latency_ms", "_ms"),
        "prompt_tokens": ("prompt_tokens", ""),
        "completion_tokens": ("output_tokens", ""),
    }
    metrics = {}
    for prefix, (key, suffix) in distributions.items():
        metrics.update(_distribution_metrics(
            prefix, _timing_distribution(rows, key), unit_suffix=suffix
        ))
    prompt_values = [row["prompt_tokens"] for row in rows if row.get("prompt_tokens") is not None]
    output_tokens = sum(int(row["output_tokens"]) for row in rows)
    wall_lower_s = timeline["wall_clock_ms_lower_bound"] / 1000.0
    wall_upper_s = timeline["wall_clock_ms_upper_bound"] / 1000.0
    throughput_lower = output_tokens / wall_upper_s
    throughput_upper = output_tokens / wall_lower_s
    controlled = [
        row["controlled_output"] for row in rows
        if isinstance(row.get("controlled_output"), dict)
    ]
    controlled_observable = [
        item for item in controlled if item.get("exact_adherence") is not None
    ]
    controlled_exact = sum(
        item.get("exact_adherence") is True for item in controlled_observable
    )
    metrics.update({
        "prompt_tokens": sum(prompt_values) if prompt_values else None,
        "prompt_token_samples": len(prompt_values),
        "request_canary_samples": sum(row.get("request_canary") is not None for row in rows),
        "request_canary_passed": sum(bool((row.get("request_canary") or {}).get("passed")) for row in rows),
        "controlled_output_samples": len(controlled),
        "controlled_output_exact_adherence": controlled_exact,
        "controlled_output_unobservable": len(controlled) - len(controlled_observable),
        "controlled_output_exact_adherence_rate": (
            controlled_exact / len(controlled_observable)
            if controlled_observable else None
        ),
        "output_tokens": output_tokens,
        # Keep the historical headline key, but make it conservative.
        "throughput_tok_s": throughput_lower,
        "throughput_tok_s_lower_bound": throughput_lower,
        "throughput_tok_s_upper_bound": throughput_upper,
        "throughput_estimate_kind": (
            "exact" if throughput_lower == throughput_upper
            else "conservative-lower-bound"
        ),
        "throughput_tok_s_legacy_max_replica_wall_upper_bound": throughput_upper,
    })
    return metrics


def _identity_status(artifacts: list[dict]) -> dict:
    fingerprints = [artifact.get("configuration_fingerprint") for artifact in artifacts]
    if any(fingerprints):
        if not all(fingerprints) or len(set(fingerprints)) != 1:
            raise ValueError("replica configuration_fingerprint values must all match")
        if not all(
            isinstance(value, str) and _CONFIGURATION_FINGERPRINT.fullmatch(value)
            for value in fingerprints
        ):
            raise ValueError(
                "configuration_fingerprint must be sha256 plus 64 lowercase hex digits"
            )
        for field in ("model", "source_recipe"):
            if len({artifact.get(field) for artifact in artifacts}) != 1:
                raise ValueError(
                    f"replica {field} values must match with a configuration fingerprint"
                )
        return {
            "status": "declared-matching-fingerprint",
            "configuration_fingerprint": fingerprints[0],
            "attestation": "operator-declared; not independently verified",
        }
    model_values = sorted({str(artifact.get("model")) for artifact in artifacts})
    source_values = sorted({str(artifact.get("source_recipe")) for artifact in artifacts})
    return {
        "status": "legacy-unverified", "configuration_fingerprint": None,
        "model_values": model_values,
        "source_recipe_values": source_values,
        "limitation": (
            "legacy inputs do not record a configuration fingerprint; differing served-model "
            "or source-recipe labels are retained, and matched workload fields do not prove "
            "identical weights or launch"
        ),
    }


def _effective_shared_prefix(artifact: dict) -> int | None:
    if artifact.get("prompt_cache_mode") == "unique":
        return 0
    return artifact.get("shared_prefix_tokens")


def _shared_prefix_status(artifacts: list[dict]) -> dict:
    values = [_effective_shared_prefix(artifact) for artifact in artifacts]
    if len(set(values)) != 1:
        raise ValueError("replica shared_prefix_tokens values differ")
    if artifacts[0].get("prompt_cache_mode") == "unique":
        return {"effective_tokens": 0, "status": "ignored-for-unique-cache"}
    if values[0] is None:
        return {
            "effective_tokens": None,
            "status": "legacy-unknown",
            "limitation": "legacy shared-cache inputs omit shared_prefix_tokens",
        }
    _integer(values[0], label="shared_prefix_tokens")
    return {"effective_tokens": values[0], "status": "declared"}


def _validation_provenance(artifacts: list[dict]) -> dict:
    rows = [row for artifact in artifacts for row in artifact["request_timings"]]
    canary_rows = [row for row in rows if row.get("request_canary") is not None]
    modern_canaries = all(
        row["request_canary"].get("marker_at_start") is True
        and row["request_canary"].get("capture_complete") is True
        for row in canary_rows
    ) if canary_rows else None
    controlled_rows = [
        row for row in rows if isinstance(row.get("controlled_output"), dict)
    ]
    requested_controlled = any(artifact.get("response_words") for artifact in artifacts)
    return {
        "request_canary_semantics": (
            "beginning-and-any-foreign-marker-verified"
            if modern_canaries else "legacy-substring-only"
            if canary_rows else "not-requested"
        ),
        "controlled_output_adherence": (
            "observed" if controlled_rows else "legacy-unobserved"
            if requested_controlled else "not-requested"
        ),
        "limitation": (
            "legacy validation rows are retained as recorded; absent beginning-marker "
            "and controlled-output observations are not reconstructed"
            if (canary_rows and not modern_canaries)
            or (requested_controlled and not controlled_rows)
            else None
        ),
    }


def _artifact_base(paths: list[Path], requested: Path | None) -> Path:
    if requested is not None:
        return requested.resolve()
    return Path(os.path.commonpath([str(path.resolve().parent) for path in paths]))


def _portable_reference(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base).as_posix()
    except ValueError as exc:
        raise ValueError(f"{path}: input must be inside the artifact output directory") from exc


def _validated_input_paths(paths: list[Path]) -> tuple[list[Path], list[str]]:
    canonical = []
    file_identities = []
    for path in paths:
        if path.suffix.lower() != ".json":
            raise ValueError(f"{path}: replica input must use a .json suffix")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"{path}: replica input is not readable") from exc
        if not resolved.is_file():
            raise ValueError(f"{path}: replica input must be a regular file")
        stat = resolved.stat()
        identity = (stat.st_dev, stat.st_ino)
        if resolved in canonical or identity in file_identities:
            raise ValueError(f"{path}: duplicate replica input or filesystem alias")
        canonical.append(resolved)
        file_identities.append(identity)
    digests = [_sha256(path) for path in canonical]
    if len(set(digests)) != len(digests):
        raise ValueError("replica inputs must have distinct SHA-256 digests")
    return canonical, digests


def _run_identity_status(artifacts: list[dict]) -> dict:
    identities = []
    missing = 0
    for artifact in artifacts:
        run_id = artifact.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            missing += 1
            continue
        identity = (
            run_id, artifact.get("model"), artifact.get("base_url"),
            artifact.get("gpu"),
        )
        if identity in identities:
            raise ValueError("replica composite run identity is duplicated")
        identities.append(identity)
    return {
        "status": "distinct-composite-identities" if not missing else "legacy-missing-run-id",
        "fields": ["run_id", "model", "base_url", "gpu"],
        "missing_run_ids": missing,
        "limitation": (
            "whole-second run_id values may match across concurrent legacy processes; "
            "distinct endpoint/model/GPU fields and source digests prevent replay"
            if len({artifact.get("run_id") for artifact in artifacts}) < len(artifacts)
            else None
        ),
    }


def combine(paths: list[Path], *, artifact_base: Path | None = None) -> dict:
    if len(paths) < 2:
        raise ValueError("at least two replica artifacts are required")
    paths = [Path(path) for path in paths]
    canonical_paths, input_digests = _validated_input_paths(paths)
    artifacts = [_load(path) for path in canonical_paths]
    first = artifacts[0]
    for path, artifact in zip(paths[1:], artifacts[1:]):
        for field in _MATCHED_FIELDS:
            if artifact.get(field) != first.get(field):
                raise ValueError(f"{path}: {field} differs from the first artifact")
    shared_prefix = _shared_prefix_status(artifacts)
    run_identity = _run_identity_status(artifacts)
    timeline = _timeline(artifacts)
    identity = _identity_status(artifacts)
    validation = _validation_provenance(artifacts)
    reference_base = _artifact_base(paths, artifact_base)
    rows = []
    replicas = []
    offset = 0
    for index, (path, artifact, digest) in enumerate(
        zip(paths, artifacts, input_digests), start=1
    ):
        replica_rows = artifact["request_timings"]
        for row in replica_rows:
            rows.append({
                **row, "replica": index,
                "replica_request_index": row.get("request_index"),
                "request_index": offset + int(row.get("request_index", 0)),
            })
        offset += len(replica_rows)
        replicas.append({
            "replica": index, "artifact": _portable_reference(path, reference_base),
            "sha256": digest, "base_url": artifact.get("base_url"),
            "run_id": artifact.get("run_id"),
            "model": artifact.get("model"), "gpu": artifact.get("gpu"),
            "source_recipe": artifact.get("source_recipe"),
            "requests": artifact.get("requests"),
            "concurrency": artifact.get("concurrency"),
            "wall_clock_ms": artifact.get("wall_clock_ms"),
            "throughput_tok_s": artifact["metrics"].get("throughput_tok_s"),
        })
    metrics = _combined_metrics(rows, timeline)
    return {
        "schema": "anvil-serving.capacity-aggregate/v1",
        "measurement_protocol": "capacity-v3-synchronized-independent-replicas",
        "topology": "data-parallel-independent-replicas",
        "started_at": first["started_at"], "finished_at": first["finished_at"],
        "wall_clock_ms": timeline["wall_clock_ms_upper_bound"],
        "wall_clock_ms_lower_bound": timeline["wall_clock_ms_lower_bound"],
        "wall_clock_ms_upper_bound": timeline["wall_clock_ms_upper_bound"],
        "timeline": timeline, "replica_identity": identity,
        "run_identity": run_identity,
        "validation_provenance": validation,
        "replica_count": len(artifacts),
        "requests": sum(int(a["requests"]) for a in artifacts),
        "completed": sum(int(a["completed"]) for a in artifacts),
        "failed": 0, "performance_eligible": True,
        "concurrency": sum(int(a["concurrency"]) for a in artifacts),
        "context_tokens": first.get("context_tokens"),
        "context_seed": first.get("context_seed"),
        "max_context_tokens": first.get("max_context_tokens"),
        "max_tokens": first.get("max_tokens"),
        "response_words": first.get("response_words"),
        "prompt_cache_mode": first.get("prompt_cache_mode"),
        "shared_prefix_tokens": shared_prefix["effective_tokens"],
        "shared_prefix_identity": shared_prefix,
        "request_canaries": first.get("request_canaries"),
        "controlled_output_policy": first.get("controlled_output_policy"),
        "synchronization": timeline["alignment"],
        "metric_population": {
            "successful_requests": len(rows), "excluded_requests": 0,
            "p99_minimum_recommended_samples": 100,
            "p99_interpretation": (
                "tail-estimate" if len(rows) >= 100
                else "descriptive-nearest-rank-only"
            ),
        },
        "replicas": replicas, "request_timings": rows, "metrics": metrics,
    }


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            newline="\n", prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            os.chmod(temporary, existing_mode)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _validate_output_target(path: Path, inputs: list[Path], payload: dict) -> None:
    if path.suffix.lower() != ".json":
        raise ValueError("aggregate output must use a .json suffix")
    if path.is_symlink():
        raise ValueError("aggregate output must not be a symbolic link")
    resolved_output = path.resolve(strict=False)
    for input_path in inputs:
        resolved_input = input_path.resolve(strict=True)
        if resolved_output == resolved_input:
            raise ValueError("aggregate output must not overwrite a replica input")
        if path.exists() and os.path.samefile(path, resolved_input):
            raise ValueError("aggregate output must not alias a replica input")
    if not path.exists():
        return
    if not path.is_file():
        raise ValueError("aggregate output must be a regular file")
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("refusing to overwrite a non-aggregate output") from exc
    if not isinstance(existing, dict) or (
        existing.get("schema") != "anvil-serving.capacity-aggregate/v1"
    ):
        raise ValueError("refusing to overwrite a non-aggregate output")
    existing_digests = sorted(
        replica.get("sha256")
        for replica in existing.get("replicas", [])
        if isinstance(replica, dict) and isinstance(replica.get("sha256"), str)
    )
    replacement_digests = sorted(replica["sha256"] for replica in payload["replicas"])
    if existing_digests != replacement_digests:
        raise ValueError("refusing to overwrite an aggregate derived from different inputs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = combine(args.inputs, artifact_base=args.output.parent)
    _validate_output_target(args.output, args.inputs, payload)
    _atomic_write_json(args.output, payload)
    print(
        f"wrote {args.output} "
        f"({payload['metrics']['throughput_tok_s']:.1f} output tok/s conservative)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
