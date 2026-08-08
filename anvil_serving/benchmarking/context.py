"""Deterministic long-context case construction, scoring, and curve summaries."""

from __future__ import annotations

import hashlib
import random
import re
import statistics
from typing import Any, Callable, Mapping

from .jobs import BenchmarkJobError


CONTEXT_CASE_SCHEMA = "anvil-serving.context-case/v1"
CONTEXT_OBSERVATION_SCHEMA = "anvil-serving.context-observation/v1"
CONTEXT_CURVE_SCHEMA = "anvil-serving.context-curve/v1"
NATIVE_CONTEXT_CASES = frozenset({"native-needle", "native-order", "native-distractor"})
TokenCounter = Callable[[str], int]
_ANSWER_RE = re.compile(r"\s+")


def _normalize_answer(value: str) -> str:
    return _ANSWER_RE.sub(" ", value.strip()).casefold()


def _token_count(counter: TokenCounter, text: str) -> int:
    value = counter(text)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BenchmarkJobError("bad_tokenizer", "token counter returned an invalid value")
    return value


def _native_payload(case_type: str, seed: int) -> tuple[list[str], str, str]:
    rng = random.Random(seed)
    values = [f"K{rng.randrange(10**11, 10**12 - 1)}" for _ in range(3)]
    if case_type == "native-needle":
        records = [f"The access marker for ORCHID is {values[0]}."]
        question = "Return only the access marker for ORCHID."
        expected = values[0]
    elif case_type == "native-order":
        records = [
            f"Checkpoint ALPHA stores {values[0]}.",
            f"Checkpoint BETA stores {values[1]}.",
            f"Checkpoint GAMMA stores {values[2]}.",
        ]
        question = (
            "Return the ALPHA, BETA, and GAMMA checkpoint values in that order, "
            "separated by ' | '."
        )
        expected = " | ".join(values)
    elif case_type == "native-distractor":
        records = [
            f"Archive ORCHID-{index:03d} stores {value}."
            for index, value in enumerate(values[1:], start=1)
        ] + [f"The canonical ORCHID marker is {values[0]}."]
        question = "Return only the canonical ORCHID marker, ignoring numbered archives."
        expected = values[0]
    else:
        raise BenchmarkJobError("unknown_context_case", f"unknown native case {case_type!r}")
    return records, question, expected


def _render_prompt(filler_units: int, records: list[str], position: float, question: str) -> str:
    filler = [
        f"Reference paragraph {index:07d}: neutral cobalt ledger material for calibration."
        for index in range(filler_units)
    ]
    insertion = round(len(filler) * position)
    document = filler[:insertion] + records + filler[insertion:]
    return (
        "Read the reference document and answer the final question.\n\n"
        + "\n".join(document)
        + "\n\nQuestion: "
        + question
    )


def build_native_context_case(
    case_type: str,
    *,
    requested_tokens: int,
    position: float,
    repetition: int,
    token_counter: TokenCounter,
    seed: int = 1,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build one calibrated prompt and a separate private expected-answer record."""
    if case_type not in NATIVE_CONTEXT_CASES:
        raise BenchmarkJobError("unknown_context_case", f"unknown native case {case_type!r}")
    if not isinstance(requested_tokens, int) or not 128 <= requested_tokens <= 1048576:
        raise BenchmarkJobError("bad_context_bucket", "requested_tokens are out of bounds")
    if not isinstance(position, (int, float)) or isinstance(position, bool) or not 0 <= position <= 1:
        raise BenchmarkJobError("bad_context_position", "position must be from 0 through 1")
    if not isinstance(repetition, int) or isinstance(repetition, bool) or repetition < 0:
        raise BenchmarkJobError("bad_repetition", "repetition must be non-negative")
    records, question, expected = _native_payload(case_type, seed + repetition)
    base = _render_prompt(0, records, float(position), question)
    base_tokens = _token_count(token_counter, base)
    if base_tokens > requested_tokens:
        raise BenchmarkJobError(
            "context_bucket_too_small", "case instructions exceed the requested token bucket"
        )
    sample_unit = _render_prompt(1, records, float(position), question)
    tokens_per_unit = max(1, _token_count(token_counter, sample_unit) - base_tokens)
    estimate = max(0, (requested_tokens - base_tokens) // tokens_per_unit)
    low = max(0, estimate - 8)
    high = max(estimate + 8, 1)
    while _token_count(token_counter, _render_prompt(high, records, float(position), question)) <= requested_tokens:
        low = high
        high *= 2
        if high > requested_tokens * 2:
            break
    while low + 1 < high:
        middle = (low + high) // 2
        candidate = _render_prompt(middle, records, float(position), question)
        if _token_count(token_counter, candidate) <= requested_tokens:
            low = middle
        else:
            high = middle
    prompt = _render_prompt(low, records, float(position), question)
    actual_tokens = _token_count(token_counter, prompt)
    case_id = f"{case_type}-{requested_tokens}-{position:.3f}-r{repetition}"
    public = {
        "schema": CONTEXT_CASE_SCHEMA,
        "case_id": case_id,
        "case_type": case_type,
        "requested_tokens": requested_tokens,
        "calibrated_prompt_tokens": actual_tokens,
        "token_measurement": "tokenizer",
        "position": float(position),
        "target_count": len(records) if case_type == "native-order" else 1,
        "distractor_count": len(records) - 1,
        "prompt": prompt,
    }
    expected_record = {
        "case_id": case_id,
        "expected_answer": expected,
        "answer_sha256": hashlib.sha256(expected.encode("utf-8")).hexdigest(),
        "scorer": "normalized_exact/v1",
    }
    return public, expected_record


def score_context_response(
    case: Mapping[str, Any],
    expected_record: Mapping[str, Any],
    response_text: str,
    *,
    observed_prompt_tokens: int | None = None,
    latency_ms: float | None = None,
    throughput_tps: float | None = None,
    finish_reason: str | None = None,
    failure: Mapping[str, Any] | None = None,
    engine_telemetry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if case.get("schema") != CONTEXT_CASE_SCHEMA or case.get("case_id") != expected_record.get(
        "case_id"
    ):
        raise BenchmarkJobError("case_identity_mismatch", "context case identity does not match")
    if not isinstance(response_text, str):
        raise BenchmarkJobError("bad_response", "context response must be text")
    expected = expected_record.get("expected_answer")
    if not isinstance(expected, str):
        raise BenchmarkJobError("bad_expected_answer", "expected answer record is invalid")
    passed = _normalize_answer(response_text) == _normalize_answer(expected)
    completed = failure is None and bool(response_text.strip())
    actual_tokens = (
        observed_prompt_tokens
        if observed_prompt_tokens is not None
        else case["calibrated_prompt_tokens"]
    )
    if not isinstance(actual_tokens, int) or actual_tokens < 0:
        raise BenchmarkJobError("bad_token_usage", "observed prompt tokens are invalid")
    return {
        "schema": CONTEXT_OBSERVATION_SCHEMA,
        "case_id": case["case_id"],
        "case_type": case["case_type"],
        "requested_tokens": case["requested_tokens"],
        "prompt_tokens": actual_tokens,
        "token_measurement": "usage" if observed_prompt_tokens is not None else "tokenizer",
        "position": case["position"],
        "target_count": case["target_count"],
        "distractor_count": case["distractor_count"],
        "passed": passed,
        "completed": completed,
        "visible_answer": response_text,
        "latency_ms": latency_ms,
        "throughput_tps": throughput_tps,
        "finish_reason": finish_reason,
        "failure": dict(failure) if failure is not None else None,
        "engine_telemetry": dict(engine_telemetry) if engine_telemetry is not None else None,
    }


def external_context_case(
    *,
    adapter: str,
    adapter_revision: str,
    case_id: str,
    prompt: str,
    expected_answer: str,
    requested_tokens: int,
    observed_tokens: int,
    position: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize a prepared RULER/MRCR record without importing its dataset."""
    if adapter not in {"ruler", "mrcr"}:
        raise BenchmarkJobError("unknown_context_adapter", "context adapter is unsupported")
    if not re.fullmatch(r"[0-9a-f]{40,64}", adapter_revision):
        raise BenchmarkJobError("mutable_adapter", "context adapter revision is not immutable")
    public = {
        "schema": CONTEXT_CASE_SCHEMA,
        "case_id": case_id,
        "case_type": adapter,
        "adapter_revision": adapter_revision,
        "requested_tokens": requested_tokens,
        "calibrated_prompt_tokens": observed_tokens,
        "token_measurement": "adapter-tokenizer",
        "position": position,
        "target_count": 1,
        "distractor_count": None,
        "prompt": prompt,
    }
    expected = {
        "case_id": case_id,
        "expected_answer": expected_answer,
        "answer_sha256": hashlib.sha256(expected_answer.encode("utf-8")).hexdigest(),
        "scorer": "normalized_exact/v1",
    }
    return public, expected


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def summarize_context_degradation(
    observations: list[Mapping[str, Any]],
    *,
    scoring: Mapping[str, Any],
    advertised_context: int | None = None,
) -> dict[str, Any]:
    """Aggregate attempted buckets and compute the first profile-defined drop."""
    if not observations:
        raise BenchmarkJobError("no_context_observations", "context curve needs observations")
    baseline_bucket = scoring.get("baseline_bucket")
    floor = scoring.get("pass_rate_floor")
    max_drop = scoring.get("max_relative_drop")
    if (
        not isinstance(baseline_bucket, int)
        or not isinstance(floor, (int, float))
        or isinstance(floor, bool)
        or not 0 <= floor <= 1
        or not isinstance(max_drop, (int, float))
        or isinstance(max_drop, bool)
        or not 0 <= max_drop <= 1
    ):
        raise BenchmarkJobError("bad_scoring_policy", "context scoring policy is invalid")
    grouped: dict[int, list[dict[str, Any]]] = {}
    for raw in observations:
        sample = dict(raw)
        if sample.get("schema") != CONTEXT_OBSERVATION_SCHEMA:
            raise BenchmarkJobError("bad_context_observation", "observation schema is invalid")
        bucket = sample.get("requested_tokens")
        if not isinstance(bucket, int) or isinstance(bucket, bool) or bucket < 1:
            raise BenchmarkJobError("bad_context_observation", "observation bucket is invalid")
        if not isinstance(sample.get("passed"), bool) or not isinstance(
            sample.get("completed"), bool
        ):
            raise BenchmarkJobError("bad_context_observation", "observation outcome is invalid")
        grouped.setdefault(bucket, []).append(sample)
    if baseline_bucket not in grouped:
        raise BenchmarkJobError(
            "baseline_not_attempted", "context scoring baseline was not attempted"
        )
    summaries = []
    for bucket in sorted(grouped):
        samples = grouped[bucket]
        latency = [
            float(item["latency_ms"])
            for item in samples
            if isinstance(item.get("latency_ms"), (int, float))
            and not isinstance(item.get("latency_ms"), bool)
        ]
        throughput = [
            float(item["throughput_tps"])
            for item in samples
            if isinstance(item.get("throughput_tps"), (int, float))
            and not isinstance(item.get("throughput_tps"), bool)
        ]
        telemetry = [
            dict(item["engine_telemetry"])
            for item in samples
            if isinstance(item.get("engine_telemetry"), Mapping)
        ]
        failures: dict[str, int] = {}
        for item in samples:
            failure = item.get("failure")
            if isinstance(failure, Mapping):
                code = failure.get("code")
                name = code if isinstance(code, str) and code else "unclassified"
                failures[name] = failures.get(name, 0) + 1
        summaries.append(
            {
                "requested_tokens": bucket,
                "sample_count": len(samples),
                "passed_count": sum(item["passed"] for item in samples),
                "completed_count": sum(item["completed"] for item in samples),
                "pass_rate": sum(item["passed"] for item in samples) / len(samples),
                "completion_rate": sum(item["completed"] for item in samples) / len(samples),
                "latency_ms": {
                    "available": bool(latency),
                    "mean": _mean(latency),
                    "observations": latency,
                },
                "throughput_tps": {
                    "available": bool(throughput),
                    "mean": _mean(throughput),
                    "observations": throughput,
                },
                "engine_telemetry": {
                    "available": bool(telemetry),
                    "observations": telemetry,
                },
                "failures": failures,
                "samples": samples,
            }
        )
    baseline = next(item for item in summaries if item["requested_tokens"] == baseline_bucket)
    baseline_rate = baseline["pass_rate"]
    first_degradation = None
    effective_context = None
    for summary in summaries:
        relative_drop = (
            0.0
            if baseline_rate == 0 and summary["pass_rate"] == 0
            else 1.0
            if baseline_rate == 0
            else max(0.0, (baseline_rate - summary["pass_rate"]) / baseline_rate)
        )
        meets_policy = summary["pass_rate"] >= floor and relative_drop <= max_drop
        summary["relative_drop_from_baseline"] = relative_drop
        summary["meets_policy"] = meets_policy
        if first_degradation is None and not meets_policy:
            first_degradation = {
                "requested_tokens": summary["requested_tokens"],
                "pass_rate": summary["pass_rate"],
                "relative_drop": relative_drop,
                "reasons": [
                    name
                    for condition, name in (
                        (summary["pass_rate"] < floor, "pass_rate_below_floor"),
                        (relative_drop > max_drop, "relative_drop_exceeded"),
                    )
                    if condition
                ],
            }
        if first_degradation is None:
            effective_context = summary["requested_tokens"]
    return {
        "schema": CONTEXT_CURVE_SCHEMA,
        "attempted_buckets": sorted(grouped),
        "advertised_context": advertised_context,
        "effective_context": effective_context,
        "first_material_degradation": first_degradation,
        "threshold_policy": {
            "baseline_bucket": baseline_bucket,
            "baseline_pass_rate": baseline_rate,
            "pass_rate_floor": float(floor),
            "max_relative_drop": float(max_drop),
        },
        "buckets": summaries,
        "notes": [
            "effective_context uses attempted buckets only",
            "missing engine telemetry is unavailable and is not inferred from latency",
        ],
    }
