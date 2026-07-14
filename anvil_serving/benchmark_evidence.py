"""Bounded discovery and comparison for retained local benchmark evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_ROOT = "docs/findings"
DEFAULT_LIMIT = 100
MAX_LIMIT = 1_000
MAX_SCAN_FILES = 10_000
MAX_SCAN_ENTRIES = 50_000
MAX_COMPARE_ARTIFACTS = 20
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_TEXT_CHARS = 1_024
MAX_SUITES = 32
MAX_CHECKS_PER_SUITE = 1_000
MAX_ATTEMPTS_PER_CHECK = 100
BENCHMARK_SCHEMAS = frozenset({
    "anvil-serving.benchmark/v1",
    "anvil-serving.fast-tier-bakeoff/v1",
})
THINKING_MODES = frozenset({"default", "disabled", "enabled", "unsupported"})
THINKING_CONTROL_MECHANISMS = frozenset({"chat_template_kwargs", "reasoning_effort"})


class EvidenceError(ValueError):
    """A benchmark evidence request is invalid or cannot be read safely."""


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            normalized = " ".join(value.split())
            if len(normalized) > MAX_TEXT_CHARS:
                normalized = normalized[: MAX_TEXT_CHARS - 3] + "..."
            return normalized
    return None


def _number(*values: object) -> int | float | None:
    for value in values:
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            return value
    return None


def _bool(*values: object) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
    return None


def _immutable_sha256(value: object) -> str | None:
    text = _text(value)
    if text is None or len(text) != 64:
        return None
    try:
        int(text, 16)
    except ValueError:
        return None
    return text.lower()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            payload = handle.read(MAX_ARTIFACT_BYTES + 1)
    except OSError as exc:
        raise EvidenceError(f"cannot read benchmark artifact {path}: {exc}") from exc
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise EvidenceError(f"artifact exceeds {MAX_ARTIFACT_BYTES} byte read limit: {path}")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise EvidenceError(f"cannot read benchmark artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"benchmark artifact must contain a JSON object: {path}")
    return value


def _suite_summary(name: str, raw: object) -> dict[str, Any]:
    suite = _mapping(raw)
    raw_checks = _list(suite.get("checks"))
    if len(raw_checks) > MAX_CHECKS_PER_SUITE:
        raise EvidenceError(
            f"suite {name!r} exceeds {MAX_CHECKS_PER_SUITE} check limit"
        )
    checks = [_mapping(item) for item in raw_checks]
    validation_errors: list[str] = []
    threshold_passed = sum(item.get("status") == "passed" for item in checks)
    attempt_counts: list[int] = []
    pass_counts: list[int] = []
    for index, item in enumerate(checks):
        raw_attempts = _number(item.get("attempt_count"))
        raw_passes = _number(item.get("pass_count"))
        if "attempt_count" not in item:
            attempt_count = 1
        elif (
            isinstance(raw_attempts, int)
            and 0 <= raw_attempts <= MAX_ATTEMPTS_PER_CHECK
        ):
            attempt_count = raw_attempts
        else:
            validation_errors.append(
                f"check {index} attempt_count must be an integer from 0 to "
                f"{MAX_ATTEMPTS_PER_CHECK}"
            )
            attempt_count = 0
        if "pass_count" not in item:
            pass_count = 1 if item.get("status") == "passed" else 0
        elif (
            isinstance(raw_passes, int)
            and 0 <= raw_passes <= MAX_ATTEMPTS_PER_CHECK
        ):
            pass_count = raw_passes
        else:
            validation_errors.append(f"check {index} pass_count must be a non-negative integer")
            pass_count = 0
        if pass_count > attempt_count:
            validation_errors.append(f"check {index} pass_count exceeds attempt_count")
        attempt_counts.append(attempt_count)
        pass_counts.append(pass_count)
    fully_correct = sum(
        attempts > 0 and passed == attempts
        for attempts, passed in zip(attempt_counts, pass_counts, strict=True)
    )
    attempts = sum(attempt_counts)
    passed_attempts = sum(pass_counts)
    failure_classes: dict[str, int] = {}
    for check in checks:
        raw_attempts = _list(check.get("attempts"))
        if len(raw_attempts) > MAX_ATTEMPTS_PER_CHECK:
            raise EvidenceError(
                f"suite {name!r} check exceeds {MAX_ATTEMPTS_PER_CHECK} attempt limit"
            )
        for attempt in raw_attempts:
            failure_class = _text(_mapping(attempt).get("failure_class"))
            if failure_class:
                failure_classes[failure_class] = failure_classes.get(failure_class, 0) + 1
    return {
        "name": _text(name) or "unnamed-suite",
        "status": _text(suite.get("status")),
        "work_class": _text(suite.get("work_class")),
        "items": len(checks),
        "fully_correct_items": fully_correct,
        "threshold_passed_items": threshold_passed,
        "attempts": attempts,
        "passed_attempts": passed_attempts,
        "failure_classes": failure_classes,
        "source": _text(suite.get("source")),
        "source_sha256": _immutable_sha256(suite.get("source_sha256")),
        "validation_errors": validation_errors,
        "evidence_use": _text(suite.get("evidence_use")) or "diagnostic",
        "validator_strength": _text(suite.get("validator_strength")) or "deterministic_marker",
    }


def _validate_summary(summary: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    capacity = _mapping(summary.get("capacity"))
    protocol = _mapping(summary.get("protocol"))

    for field in ("requests", "concurrency", "context_tokens", "max_tokens"):
        value = capacity.get(field)
        if value is not None and (not isinstance(value, int) or value <= 0):
            errors.append(f"capacity.{field} must be a positive integer")
    completed = capacity.get("completed")
    if completed is not None and (not isinstance(completed, int) or completed < 0):
        errors.append("capacity.completed must be a non-negative integer")
    requests = capacity.get("requests")
    if isinstance(completed, int) and isinstance(requests, int) and completed > requests:
        errors.append("capacity.completed exceeds capacity.requests")
    served_context = capacity.get("served_context_tokens")
    if served_context is not None and (
        not isinstance(served_context, int) or served_context <= 0
    ):
        errors.append("capacity.served_context_tokens must be a positive integer")
    for field in (
        "ttft_p50_ms", "e2e_p50_ms", "output_tokens",
        "aggregate_output_tok_s", "wall_ms",
    ):
        value = capacity.get(field)
        if value is not None and value < 0:
            errors.append(f"capacity.{field} must be non-negative")

    for field in ("version", "repetitions", "visible_answer_tokens"):
        value = protocol.get(field)
        if value is not None and (not isinstance(value, int) or value <= 0):
            errors.append(f"protocol.{field} must be a positive integer")
    headroom = protocol.get("reasoning_headroom_tokens")
    if headroom is not None and (not isinstance(headroom, int) or headroom < 0):
        errors.append("protocol.reasoning_headroom_tokens must be a non-negative integer")
    minimum_pass_rate = protocol.get("minimum_pass_rate")
    if minimum_pass_rate is not None and (
            not isinstance(minimum_pass_rate, (int, float))
            or isinstance(minimum_pass_rate, bool)
            or not 0 < minimum_pass_rate <= 1):
        errors.append("protocol.minimum_pass_rate must be greater than 0 and at most 1")
    if protocol.get("no_thinking") is True and protocol.get("thinking_mode") != "disabled":
        errors.append("protocol.no_thinking=true conflicts with thinking_mode")
    thinking_mode = protocol.get("thinking_mode")
    if thinking_mode is not None and thinking_mode not in THINKING_MODES:
        errors.append(f"protocol.thinking_mode is unsupported: {thinking_mode!r}")
    control = protocol.get("control_mechanism")
    if control is not None and control not in THINKING_CONTROL_MECHANISMS:
        errors.append(f"protocol.control_mechanism is unsupported: {control!r}")

    for suite in _list(_mapping(summary.get("quality")).get("suites")):
        suite_map = _mapping(suite)
        for error in _list(suite_map.get("validation_errors")):
            errors.append(f"suite {suite_map.get('name')}: {error}")
        if suite_map.get("items") == 0:
            errors.append(f"suite {suite_map.get('name')} has no executable checks")
        if suite_map.get("attempts") == 0:
            errors.append(f"suite {suite_map.get('name')} has no executed attempts")
        if suite_map.get("status") not in {"passed", "failed"}:
            errors.append(f"suite {suite_map.get('name')} has an invalid status")
        if suite_map.get("evidence_use") not in {"diagnostic", "ranking"}:
            errors.append(f"suite {suite_map.get('name')} has an invalid evidence_use")
        if suite_map.get("validator_strength") not in {
                "deterministic_marker", "exact_choice", "typed_structure"}:
            errors.append(f"suite {suite_map.get('name')} has an invalid validator_strength")
    return errors


def _numeric_shape_errors(groups: Sequence[tuple[str, Mapping[str, Any], Sequence[str]]]) -> list[str]:
    errors: list[str] = []
    for prefix, values, fields in groups:
        for field in fields:
            if field in values and _number(values.get(field)) is None:
                errors.append(f"{prefix}.{field} must be a finite number")
    return errors


def _artifact_kind(raw: Mapping[str, Any]) -> str | None:
    schema = _text(raw.get("schema")) or ""
    benchmark_schema = schema in BENCHMARK_SCHEMAS
    if benchmark_schema and (
            isinstance(raw.get("suites"), Mapping) and raw.get("suites")
            or any(isinstance(raw.get(name), Mapping) for name in (
                "intelligence", "tool", "session", "voice"
            ))):
        return "quality"
    if benchmark_schema and isinstance(raw.get("metrics"), Mapping):
        return "capacity"
    if isinstance(raw.get("mtp_on"), Mapping) or "mtp-ab" in schema:
        return "speculative"
    if schema.startswith("anvil-serving.benchmark/"):
        return "benchmark"
    return None


def summarize_artifact(path: str | Path) -> dict[str, Any]:
    """Return a prompt-free normalized summary for one benchmark artifact."""
    artifact_path = Path(path)
    raw = _load_json(artifact_path)
    kind = _artifact_kind(raw)
    if kind is None:
        raise EvidenceError(f"JSON file is not recognized benchmark evidence: {artifact_path}")

    identity = _mapping(raw.get("identity"))
    timing = _mapping(raw.get("timing"))
    chat_timing = _mapping(timing.get("chat"))
    metrics = _mapping(raw.get("metrics"))
    context = _mapping(raw.get("context"))
    protocol = _mapping(raw.get("evaluation_protocol"))
    thinking = _mapping(raw.get("thinking"))
    source_recipe = _mapping(raw.get("source_recipe"))
    serve_flags = _mapping(raw.get("serve_flags"))
    mtp_off = _mapping(raw.get("mtp_off"))
    mtp_on = _mapping(raw.get("mtp_on"))
    numeric_shape_errors = _numeric_shape_errors((
        ("artifact", raw, (
            "requests", "completed", "concurrency", "context_tokens",
            "max_context_tokens", "max_tokens", "speedup",
        )),
        ("metrics", metrics, (
            "ttft_p50_ms", "e2e_p50_ms", "output_tokens", "throughput_tok_s",
        )),
        ("timing", timing, ("wall_ms",)),
        ("timing.chat", chat_timing, (
            "ttft_p50_ms", "e2e_p50_ms", "output_tokens",
        )),
        ("context", context, ("max_model_len", "cap_tokens")),
        ("protocol", protocol, (
            "version", "repetitions", "visible_answer_tokens",
            "reasoning_headroom_tokens",
        )),
        ("mtp_off", mtp_off, ("mean_tok_s",)),
        ("mtp_on", mtp_on, ("mean_tok_s",)),
    ))
    raw_suites = _mapping(raw.get("suites"))
    if len(raw_suites) > MAX_SUITES:
        raise EvidenceError(f"artifact exceeds {MAX_SUITES} suite limit: {artifact_path}")
    suites = [
        _suite_summary(str(name), value)
        for name, value in sorted(raw_suites.items())
    ]

    warnings: list[str] = []
    if kind == "quality" and _number(protocol.get("version")) is None:
        warnings.append("quality artifact does not declare an evaluation protocol version")
    if thinking.get("control_status") == "requested_unverified":
        warnings.append("thinking control was requested but not independently verified")
    if any(
        any(name == "budget_exhausted" or name.endswith("_budget_exhausted")
            for name in suite["failure_classes"])
        for suite in suites
    ):
        warnings.append("one or more attempts exhausted the completion budget")
    is_capacity = kind == "capacity"
    method = raw.get("method")
    method_sha256 = (
        hashlib.sha256(method.encode("utf-8")).hexdigest()
        if isinstance(method, str)
        else None
    )

    summary = {
        "path": _text(artifact_path.as_posix()),
        "schema": _text(raw.get("schema")),
        "kind": kind,
        "run_id": _text(raw.get("run_id")),
        "recorded_at": _text(identity.get("started_at"), raw.get("date")),
        "model": _text(identity.get("model"), raw.get("model")),
        "candidate_id": _text(identity.get("candidate_id"), raw.get("candidate_id")),
        "config_id": _text(identity.get("config_id"), raw.get("config_id")),
        "provenance": {
            "engine": _text(raw.get("engine"), identity.get("engine")),
            "gpu": _text(raw.get("gpu"), identity.get("gpu")),
            "recipe_ref": _text(source_recipe.get("ref")),
            "method_sha256": method_sha256,
        },
        "workload": {
            "prompt_set_id": _text(
                raw.get("prompt_set_id"), raw.get("workload_id"), raw.get("suite_id")
            ),
            "shared_prefix_burst": _bool(serve_flags.get("shared_prefix_burst")),
            "cache_policy": _text(
                raw.get("cache_policy"), serve_flags.get("cache_policy")
            ),
        },
        "capacity": {
            "requests": _number(raw.get("requests")),
            "completed": _number(raw.get("completed")),
            "concurrency": _number(raw.get("concurrency")),
            "context_tokens": _number(raw.get("context_tokens")),
            "max_tokens": _number(raw.get("max_tokens")),
            "served_context_tokens": _number(
                raw.get("max_context_tokens"), context.get("max_model_len")
            ),
            "ttft_p50_ms": _number(
                metrics.get("ttft_p50_ms"), chat_timing.get("ttft_p50_ms")
            ) if is_capacity else None,
            "e2e_p50_ms": _number(
                metrics.get("e2e_p50_ms"), chat_timing.get("e2e_p50_ms")
            ) if is_capacity else None,
            "output_tokens": _number(
                metrics.get("output_tokens"), chat_timing.get("output_tokens")
            ) if is_capacity else None,
            "aggregate_output_tok_s": _number(metrics.get("throughput_tok_s")) if is_capacity else None,
            "wall_ms": _number(timing.get("wall_ms")),
        },
        "protocol": {
            "version": _number(protocol.get("version")),
            "repetitions": _number(protocol.get("repetitions")),
            "visible_answer_tokens": _number(protocol.get("visible_answer_tokens")),
            "reasoning_headroom_tokens": _number(protocol.get("reasoning_headroom_tokens")),
            "minimum_pass_rate": _number(protocol.get("minimum_pass_rate")),
            "thinking_mode": _text(thinking.get("mode")),
            "no_thinking": _bool(serve_flags.get("no_thinking")),
            "control_mechanism": _text(thinking.get("control_mechanism")),
            "thinking_control_status": _text(thinking.get("control_status")),
            "thinking_control_evidence": _text(thinking.get("control_evidence")),
            "thinking_control_evidence_sha256": _immutable_sha256(
                thinking.get("control_evidence_sha256")
            ),
        },
        "quality": {"suites": suites},
        "speculative": {
            "off_mean_tok_s": _number(mtp_off.get("mean_tok_s")),
            "on_mean_tok_s": _number(mtp_on.get("mean_tok_s")),
            "speedup": _number(raw.get("speedup")),
        },
        "warnings": warnings,
    }
    if is_capacity:
        summary["protocol"]["thinking_mode"] = _text(
            serve_flags.get("thinking_mode"), summary["protocol"]["thinking_mode"]
        )
    summary["validation_errors"] = [*numeric_shape_errors, *_validate_summary(summary)]
    return summary


def _collect_json(root: Path) -> tuple[list[Path], int, bool]:
    def fail(exc: OSError) -> None:
        raise EvidenceError(f"cannot scan evidence root {root}: {exc}") from exc

    paths: list[Path] = []
    directories = [root]
    entries_seen = 0
    truncated = False
    while directories and not truncated:
        directory = directories.pop()
        try:
            entries = os.scandir(directory)
        except OSError as exc:
            fail(exc)
        with entries:
            for entry in entries:
                if entries_seen >= MAX_SCAN_ENTRIES:
                    truncated = True
                    break
                entries_seen += 1
                try:
                    if entry.is_dir(follow_symlinks=False):
                        directories.append(Path(entry.path))
                    elif (
                        entry.is_file(follow_symlinks=False)
                        and entry.name.lower().endswith(".json")
                    ):
                        paths.append(Path(entry.path))
                        if len(paths) >= MAX_SCAN_FILES:
                            truncated = True
                            break
                except OSError as exc:
                    fail(exc)
    return sorted(paths), entries_seen, truncated


def discover_artifacts(
    root: str | Path = DEFAULT_ROOT,
    *,
    model: str | None = None,
    suite: str | None = None,
    kind: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Discover and summarize recognized JSON benchmark artifacts below ``root``."""
    if not 1 <= limit <= MAX_LIMIT:
        raise EvidenceError(f"limit must be between 1 and {MAX_LIMIT}")
    root_path = Path(root)
    if not root_path.is_dir():
        raise EvidenceError(f"evidence root is not a directory: {root_path}")
    model_filter = (model or "").casefold()
    suite_filter = (suite or "").casefold()
    results: list[dict[str, Any]] = []
    skipped = {"unrecognized": 0, "unreadable": 0, "unsafe": 0}
    scanned = 0
    paths, scanned_entries, truncated = _collect_json(root_path)
    root_resolved = root_path.resolve()
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(root_resolved):
            skipped["unsafe"] += 1
            continue
        scanned += 1
        try:
            summary = summarize_artifact(path)
        except EvidenceError as exc:
            bucket = "unrecognized" if "not recognized benchmark evidence" in str(exc) else "unreadable"
            skipped[bucket] += 1
            continue
        if kind and summary["kind"] != kind:
            continue
        identity_text = " ".join(
            str(summary.get(field) or "") for field in ("model", "candidate_id", "config_id")
        ).casefold()
        if model_filter and model_filter not in identity_text:
            continue
        suite_names = " ".join(
            str(item.get("name") or "") for item in summary["quality"]["suites"]
        ).casefold()
        if suite_filter and suite_filter not in suite_names:
            continue
        results.append(summary)
        if len(results) >= limit:
            truncated = True
            break
    return {
        "schema": "anvil-serving.benchmark-evidence-index/v1",
        "root": root_path.as_posix(),
        "scanned_entries": scanned_entries,
        "scanned_json_files": scanned,
        "matched": len(results),
        "limit": limit,
        "truncated": truncated,
        "skipped": skipped,
        "artifacts": results,
    }


def _distinct(summaries: Sequence[Mapping[str, Any]], path: Sequence[str]) -> list[Any]:
    observed: list[Any] = []
    for summary in summaries:
        value: Any = summary
        for part in path:
            value = _mapping(value).get(part)
        if value not in observed:
            observed.append(value)
    return [] if observed == [None] else observed


def _suite_signature(summary: Mapping[str, Any]) -> tuple[tuple[str, str | None, str, str], ...]:
    suites = _list(_mapping(summary.get("quality")).get("suites"))
    return tuple(
        (
            str(_mapping(item).get("name") or ""),
            _immutable_sha256(_mapping(item).get("source_sha256")),
            str(_mapping(item).get("evidence_use") or "diagnostic"),
            str(_mapping(item).get("validator_strength") or "deterministic_marker"),
        )
        for item in suites
    )


def _value_at(summary: Mapping[str, Any], path: Sequence[str]) -> Any:
    value: Any = summary
    for part in path:
        value = _mapping(value).get(part)
    return value


def compare_artifacts(paths: Iterable[str | Path]) -> dict[str, Any]:
    """Compare normalized evidence and report material workload mismatches."""
    path_list = list(paths)
    if not 2 <= len(path_list) <= MAX_COMPARE_ARTIFACTS:
        raise EvidenceError(
            f"compare requires between 2 and {MAX_COMPARE_ARTIFACTS} artifacts"
        )
    summaries = [summarize_artifact(path) for path in path_list]
    fields = {
        "kind": ("kind",),
        "schema": ("schema",),
        "context_tokens": ("capacity", "context_tokens"),
        "served_context_tokens": ("capacity", "served_context_tokens"),
        "requests": ("capacity", "requests"),
        "concurrency": ("capacity", "concurrency"),
        "max_tokens": ("capacity", "max_tokens"),
        "protocol_version": ("protocol", "version"),
        "repetitions": ("protocol", "repetitions"),
        "visible_answer_tokens": ("protocol", "visible_answer_tokens"),
        "reasoning_headroom_tokens": ("protocol", "reasoning_headroom_tokens"),
        "minimum_pass_rate": ("protocol", "minimum_pass_rate"),
        "shared_prefix_burst": ("workload", "shared_prefix_burst"),
        "cache_policy": ("workload", "cache_policy"),
        "prompt_set_id": ("workload", "prompt_set_id"),
        "gpu": ("provenance", "gpu"),
    }
    differences = {
        name: values
        for name, field_path in fields.items()
        if len(values := _distinct(summaries, field_path)) > 1
    }
    if all(summary.get("kind") == "capacity" for summary in summaries):
        for name, field_path in {
            "thinking_mode": ("protocol", "thinking_mode"),
            "no_thinking": ("protocol", "no_thinking"),
        }.items():
            values = _distinct(summaries, field_path)
            if len(values) > 1:
                differences[name] = values
    suite_signatures: list[tuple[tuple[str, str | None, str, str], ...]] = []
    for summary in summaries:
        signature = _suite_signature(summary)
        if signature not in suite_signatures:
            suite_signatures.append(signature)
    if len(suite_signatures) > 1:
        differences["suites"] = suite_signatures
    if any(summary.get("kind") == "speculative" for summary in summaries):
        method_values = _distinct(summaries, ("provenance", "method_sha256"))
        if len(method_values) > 1:
            differences["method_sha256"] = method_values
    provenance_fields = {
        "engine": ("provenance", "engine"),
        "recipe_ref": ("provenance", "recipe_ref"),
        "thinking_mode": ("protocol", "thinking_mode"),
        "thinking_control": ("protocol", "control_mechanism"),
        "thinking_control_status": ("protocol", "thinking_control_status"),
        "thinking_control_evidence": ("protocol", "thinking_control_evidence"),
        "thinking_control_evidence_sha256": (
            "protocol", "thinking_control_evidence_sha256"
        ),
    }
    provenance_differences = {
        name: values
        for name, field_path in provenance_fields.items()
        if len(values := _distinct(summaries, field_path)) > 1
    }
    required_by_kind = {
        "capacity": (
            "model", "capacity.requests", "capacity.concurrency",
            "capacity.context_tokens", "capacity.max_tokens",
            "protocol.thinking_mode", "protocol.no_thinking",
            "workload.shared_prefix_burst", "workload.cache_policy",
            "workload.prompt_set_id", "provenance.engine", "provenance.gpu",
        ),
        "quality": (
            "model", "protocol.version", "protocol.repetitions",
            "protocol.visible_answer_tokens", "protocol.reasoning_headroom_tokens",
            "protocol.minimum_pass_rate",
            "protocol.thinking_mode", "protocol.control_mechanism",
            "protocol.thinking_control_status",
            "protocol.thinking_control_evidence_sha256",
            "provenance.recipe_ref",
            "provenance.engine", "provenance.gpu",
        ),
        "speculative": (
            "model", "provenance.engine", "provenance.gpu",
            "provenance.method_sha256",
        ),
        "benchmark": ("model", "provenance.engine", "provenance.gpu"),
    }
    unknown_fields: dict[str, list[str]] = {}
    for summary in summaries:
        required = required_by_kind.get(str(summary.get("kind")), ())
        for field in required:
            if _value_at(summary, field.split(".")) is None:
                unknown_fields.setdefault(field, []).append(str(summary.get("path")))
    if any(summary.get("kind") == "quality" and not _suite_signature(summary)
           for summary in summaries):
        unknown_fields["suites"] = [
            str(summary.get("path"))
            for summary in summaries
            if summary.get("kind") == "quality" and not _suite_signature(summary)
        ]
    for summary in summaries:
        if summary.get("kind") != "quality":
            continue
        missing_hashes = [
            str(_mapping(suite).get("name") or "unnamed-suite")
            for suite in _list(_mapping(summary.get("quality")).get("suites"))
            if _immutable_sha256(_mapping(suite).get("source_sha256")) is None
        ]
        if missing_hashes:
            unknown_fields.setdefault("suites.source_sha256", []).append(
                str(summary.get("path"))
            )
        if _mapping(summary.get("protocol")).get("thinking_control_status") not in {
            "verified", "supported"
        }:
            unknown_fields.setdefault("protocol.thinking_control_status", []).append(
                str(summary.get("path"))
            )
        if _mapping(summary.get("protocol")).get("version") != 3:
            unknown_fields.setdefault("protocol.version=3", []).append(
                str(summary.get("path"))
            )
        repetitions = _mapping(summary.get("protocol")).get("repetitions")
        if not isinstance(repetitions, int) or repetitions < 3:
            unknown_fields.setdefault("protocol.repetitions>=3", []).append(
                str(summary.get("path"))
            )
        non_ranking = [
            str(_mapping(suite).get("name") or "unnamed-suite")
            for suite in _list(_mapping(summary.get("quality")).get("suites"))
            if _mapping(suite).get("evidence_use") != "ranking"
        ]
        if non_ranking:
            unknown_fields.setdefault("suites.evidence_use=ranking", []).append(
                str(summary.get("path"))
            )
        weak_validators = [
            str(_mapping(suite).get("name") or "unnamed-suite")
            for suite in _list(_mapping(summary.get("quality")).get("suites"))
            if _mapping(suite).get("validator_strength") not in {
                "exact_choice", "typed_structure"
            }
        ]
        if weak_validators:
            unknown_fields.setdefault("suites.strong_validator", []).append(
                str(summary.get("path"))
            )
    invalid_artifacts = {
        str(summary.get("path")): list(summary.get("validation_errors") or [])
        for summary in summaries
        if summary.get("validation_errors")
    }
    return {
        "schema": "anvil-serving.benchmark-evidence-comparison/v1",
        "comparable": not differences and not unknown_fields and not invalid_artifacts,
        "differences": differences,
        "provenance_differences": provenance_differences,
        "unknown_fields": unknown_fields,
        "invalid_artifacts": invalid_artifacts,
        "artifacts": summaries,
    }


def _compact_row(summary: Mapping[str, Any]) -> list[str]:
    capacity = _mapping(summary.get("capacity"))
    protocol = _mapping(summary.get("protocol"))
    suites = _list(_mapping(summary.get("quality")).get("suites"))
    stable = ",".join(
        f"{item.get('threshold_passed_items')}/{item.get('items')}"
        for item in suites
        if isinstance(item, Mapping)
    ) or "-"
    perfect = ",".join(
        f"{item.get('fully_correct_items')}/{item.get('items')}"
        for item in suites
        if isinstance(item, Mapping)
    ) or "-"
    return [
        str(summary.get("kind") or "-"),
        str(summary.get("model") or summary.get("candidate_id") or "-"),
        _format_number(capacity.get("concurrency")),
        _format_number(capacity.get("ttft_p50_ms")),
        _format_number(capacity.get("aggregate_output_tok_s")),
        stable,
        perfect,
        _format_number(protocol.get("repetitions")),
        _format_number(protocol.get("reasoning_headroom_tokens")),
        str(summary.get("path") or "-"),
    ]


def _format_number(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value) if value is not None else "-"


def _without_none(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _without_none(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_without_none(item) for item in value]
    return value


def _print_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def _render_list(result: Mapping[str, Any]) -> None:
    artifacts = [_mapping(item) for item in _list(result.get("artifacts"))]
    _print_table(
        ("KIND", "MODEL", "C", "TTFT_MS", "TOK_S", "STABLE", "PERFECT", "REPS", "HEADROOM", "PATH"),
        [_compact_row(item) for item in artifacts],
    )
    print(
        f"matched={result.get('matched')} scanned={result.get('scanned_json_files')} "
        f"truncated={str(bool(result.get('truncated'))).lower()}"
    )


def _render_show(summary: Mapping[str, Any]) -> None:
    _print_table(
        ("KIND", "MODEL", "C", "TTFT_MS", "TOK_S", "STABLE", "PERFECT", "REPS", "HEADROOM", "PATH"),
        [_compact_row(summary)],
    )
    print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=True))


def _render_compare(result: Mapping[str, Any]) -> None:
    artifacts = [_mapping(item) for item in _list(result.get("artifacts"))]
    _print_table(
        ("KIND", "MODEL", "C", "TTFT_MS", "TOK_S", "STABLE", "PERFECT", "REPS", "HEADROOM", "PATH"),
        [_compact_row(item) for item in artifacts],
    )
    differences = _mapping(result.get("differences"))
    provenance_differences = _mapping(result.get("provenance_differences"))
    unknown_fields = _mapping(result.get("unknown_fields"))
    invalid_artifacts = _mapping(result.get("invalid_artifacts"))
    if differences or unknown_fields or invalid_artifacts:
        print("not directly comparable:")
        for name, values in differences.items():
            print(f"  {name}: {json.dumps(values, ensure_ascii=True)}")
        for name, paths in unknown_fields.items():
            print(f"  {name}: unknown in {json.dumps(paths, ensure_ascii=True)}")
        for path, errors in invalid_artifacts.items():
            print(f"  invalid {path}: {json.dumps(errors, ensure_ascii=True)}")
    else:
        print("comparable workload fields match")
    if provenance_differences:
        print("implementation provenance differs:")
        for name, values in provenance_differences.items():
            print(f"  {name}: {json.dumps(values, ensure_ascii=True)}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anvil-serving eval benchmark evidence",
        description="Discover and compare retained local benchmark JSON without exposing prompts.",
    )
    actions = parser.add_subparsers(dest="action", required=True)
    list_parser = actions.add_parser("list", help="List normalized benchmark artifacts below a root.")
    list_parser.add_argument("--root", default=DEFAULT_ROOT)
    list_parser.add_argument("--model")
    list_parser.add_argument("--suite")
    list_parser.add_argument(
        "--kind", choices=("benchmark", "capacity", "quality", "speculative")
    )
    list_parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    list_parser.add_argument("--format", choices=("human", "json"), default="human")

    show_parser = actions.add_parser("show", help="Show one normalized artifact summary.")
    show_parser.add_argument("artifact")
    show_parser.add_argument("--format", choices=("human", "json"), default="human")

    compare_parser = actions.add_parser(
        "compare", help="Compare artifacts and flag material workload differences."
    )
    compare_parser.add_argument("artifacts", nargs="+")
    compare_parser.add_argument(
        "--allow-mismatch",
        action="store_true",
        help="Return success after reporting non-comparable evidence.",
    )
    compare_parser.add_argument("--format", choices=("human", "json"), default="human")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.action == "list":
            result = discover_artifacts(
                args.root,
                model=args.model,
                suite=args.suite,
                kind=args.kind,
                limit=args.limit,
            )
        elif args.action == "show":
            result = summarize_artifact(args.artifact)
        else:
            result = compare_artifacts(args.artifacts)
    except EvidenceError as exc:
        print(f"anvil-serving eval benchmark evidence: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(_without_none(result), indent=2, sort_keys=True, ensure_ascii=True))
    elif args.action == "list":
        _render_list(result)
    elif args.action == "show":
        _render_show(result)
    else:
        _render_compare(result)
    if args.action == "compare" and not result["comparable"] and not args.allow_mismatch:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
