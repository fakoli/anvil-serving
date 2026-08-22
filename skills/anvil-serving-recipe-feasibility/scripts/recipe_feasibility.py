#!/usr/bin/env python3
"""Deterministic interval-math pruning for Anvil Serving recipe campaigns."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys
from typing import Any


INPUT_SCHEMA = "anvil-serving.recipe-feasibility-input/v1"
OUTPUT_SCHEMA = "anvil-serving.recipe-feasibility-result/v1"
STATUSES = {"measured", "confirmed", "estimated", "assumed", "unknown"}


class FeasibilityError(ValueError):
    """The campaign input is invalid."""


@dataclass(frozen=True)
class Interval:
    low: float | None
    high: float | None

    def add(self, other: "Interval") -> "Interval":
        return Interval(
            None if self.low is None or other.low is None else self.low + other.low,
            None if self.high is None or other.high is None else self.high + other.high,
        )

    def subtract(self, other: "Interval") -> "Interval":
        return Interval(
            None if self.low is None or other.high is None else self.low - other.high,
            None if self.high is None or other.low is None else self.high - other.low,
        )

    def multiply(self, other: "Interval") -> "Interval":
        if _negative(self) or _negative(other):
            raise FeasibilityError("interval multiplication requires nonnegative bounds")
        return Interval(
            None if self.low is None or other.low is None else self.low * other.low,
            None if self.high is None or other.high is None else self.high * other.high,
        )

    def divide(self, other: "Interval") -> "Interval":
        if _negative(self) or _negative(other):
            raise FeasibilityError("interval division requires nonnegative bounds")
        if other.low is not None and other.low <= 0:
            raise FeasibilityError("interval divisor lower bound must be greater than zero")
        return Interval(
            None if self.low is None or other.high is None else self.low / other.high,
            None if self.high is None or other.low is None else self.high / other.low,
        )

    def floor(self) -> "Interval":
        return Interval(
            None if self.low is None else math.floor(self.low),
            None if self.high is None else math.floor(self.high),
        )

    def as_json(self) -> dict[str, int | float | None]:
        return {"min": _clean_number(self.low), "max": _clean_number(self.high)}


ZERO = Interval(0.0, 0.0)
ONE = Interval(1.0, 1.0)


def _negative(interval: Interval) -> bool:
    return (interval.low is not None and interval.low < 0) or (
        interval.high is not None and interval.high < 0
    )


def _clean_number(value: int | float | None) -> int | float | None:
    if value is None:
        return None
    if math.isfinite(value) and float(value).is_integer():
        return int(value)
    return value


def _sum(intervals: list[Interval]) -> Interval:
    total = ZERO
    for interval in intervals:
        total = total.add(interval)
    return total


def _variable(
    raw: Any,
    path: str,
    ledger: list[dict[str, Any]],
) -> Interval:
    if not isinstance(raw, dict):
        raise FeasibilityError(f"{path} must be a variable object")
    status = raw.get("status")
    if status not in STATUSES:
        raise FeasibilityError(f"{path}.status must be one of {sorted(STATUSES)}")
    if "value" in raw and ("min" in raw or "max" in raw):
        raise FeasibilityError(f"{path} cannot combine value with min/max")
    if status == "estimated":
        if (
            "value" in raw
            or raw.get("min") is None
            or raw.get("max") is None
            or raw.get("min") == raw.get("max")
        ):
            raise FeasibilityError(
                f"{path} is estimated and must use distinct numeric min/max bounds, not a point value"
            )
    if "value" in raw:
        low = high = raw["value"]
    else:
        low = raw.get("min")
        high = raw.get("max")
    for label, value in (("min", low), ("max", high)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise FeasibilityError(f"{path}.{label} must be numeric or absent")
        if value is not None and value < 0:
            raise FeasibilityError(f"{path}.{label} must be nonnegative")
    if low is not None and high is not None and low > high:
        raise FeasibilityError(f"{path}.min cannot exceed max")
    source = raw.get("source")
    notes = raw.get("notes")
    if status == "unknown":
        if not isinstance(notes, str) or not notes.strip():
            raise FeasibilityError(f"{path}.notes must explain an unknown variable")
    elif not isinstance(source, str) or not source.strip():
        raise FeasibilityError(f"{path}.source is required for status {status}")
    ledger.append(
        {
            "path": path,
            "status": status,
            "unit": raw.get("unit"),
            "min": low,
            "max": high,
            "source": source,
            "observed_at": raw.get("observed_at"),
            "notes": notes,
        }
    )
    return Interval(
        None if low is None else float(low),
        None if high is None else float(high),
    )


def _variable_map(
    raw: Any,
    path: str,
    ledger: list[dict[str, Any]],
) -> Interval:
    if raw is None:
        return ZERO
    if not isinstance(raw, dict):
        raise FeasibilityError(f"{path} must be an object")
    return _sum([_variable(value, f"{path}.{name}", ledger) for name, value in raw.items()])


def _track_variables(raw: Any, path: str, ledger: list[dict[str, Any]]) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise FeasibilityError(f"{path} must be an object")
    for name, value in raw.items():
        _variable(value, f"{path}.{name}", ledger)


def _comparison_at_least(actual: Interval, required: Interval) -> str:
    if actual.high is not None and required.low is not None and actual.high < required.low:
        return "fail"
    if actual.low is not None and required.high is not None and actual.low >= required.high:
        return "pass"
    return "unknown"


def _comparison_at_most(actual: Interval, required: Interval) -> str:
    if actual.low is not None and required.high is not None and actual.low > required.high:
        return "fail"
    if actual.high is not None and required.low is not None and actual.high <= required.low:
        return "pass"
    return "unknown"


def _one_minus(interval: Interval) -> Interval:
    return Interval(
        None if interval.high is None else 1.0 - interval.high,
        None if interval.low is None else 1.0 - interval.low,
    )


def _metric(
    metrics: dict[str, Any],
    name: str,
    candidate_path: str,
    ledger: list[dict[str, Any]],
) -> tuple[Interval | None, str | None]:
    raw = metrics.get(name)
    if raw is None:
        return None, None
    interval = _variable(raw, f"{candidate_path}.metrics.{name}", ledger)
    return interval, raw["status"]


def _threshold(
    thresholds: dict[str, Any],
    name: str,
    ledger: list[dict[str, Any]],
) -> Interval | None:
    raw = thresholds.get(name)
    if raw is None:
        return None
    return _variable(raw, f"requirements.thresholds.{name}", ledger)


def _behavior_axes(
    metrics: dict[str, Any],
    thresholds: dict[str, Any],
    candidate_path: str,
    ledger: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    axes: dict[str, dict[str, Any]] = {}

    def evidence_result(
        projected_status: str,
        evidence_statuses: dict[str, str | None],
        **details: Any,
    ) -> dict[str, Any]:
        unmeasured = sorted(
            name
            for name, status in evidence_statuses.items()
            if status not in {"measured", "confirmed"}
        )
        result = {
            "status": projected_status if not unmeasured else "unknown",
            **details,
        }
        if unmeasured:
            result["projected_status"] = projected_status
            result["evidence_statuses"] = evidence_statuses
            result["missing_variables"] = unmeasured
        return result

    deterministic, deterministic_status = _metric(
        metrics, "deterministic_pass_rate", candidate_path, ledger
    )
    deterministic_min = _threshold(thresholds, "min_deterministic_pass_rate", ledger)
    if deterministic is None or deterministic_min is None:
        axes["correctness"] = {
            "status": "unknown",
            "missing_variables": ["deterministic_pass_rate"],
        }
    else:
        axes["correctness"] = evidence_result(
            _comparison_at_least(deterministic, deterministic_min),
            {"deterministic_pass_rate": deterministic_status},
            actual=deterministic.as_json(),
            required_min=deterministic_min.as_json(),
        )

    quality, quality_status = _metric(metrics, "quality_score", candidate_path, ledger)
    reference_quality, reference_quality_status = _metric(
        metrics, "reference_quality_score", candidate_path, ledger
    )
    quality_loss_max = _threshold(thresholds, "max_relative_quality_loss", ledger)
    if quality is None or reference_quality is None or quality_loss_max is None:
        axes["quality"] = {
            "status": "unknown",
            "missing_variables": ["quality_score", "reference_quality_score"],
        }
    else:
        quality_loss = _one_minus(quality.divide(reference_quality))
        axes["quality"] = evidence_result(
            _comparison_at_most(quality_loss, quality_loss_max),
            {
                "quality_score": quality_status,
                "reference_quality_score": reference_quality_status,
            },
            relative_loss=quality_loss.as_json(),
            required_max=quality_loss_max.as_json(),
        )

    warm, warm_status = _metric(metrics, "warm_e2e_seconds", candidate_path, ledger)
    control_warm, control_warm_status = _metric(
        metrics, "no_spec_warm_e2e_seconds", candidate_path, ledger
    )
    gain_min = _threshold(thresholds, "min_warm_e2e_gain", ledger)
    if warm is None or control_warm is None or gain_min is None:
        axes["warm_e2e_speed"] = {
            "status": "unknown",
            "missing_variables": ["warm_e2e_seconds", "no_spec_warm_e2e_seconds"],
        }
    else:
        gain = _one_minus(warm.divide(control_warm))
        axes["warm_e2e_speed"] = evidence_result(
            _comparison_at_least(gain, gain_min),
            {
                "warm_e2e_seconds": warm_status,
                "no_spec_warm_e2e_seconds": control_warm_status,
            },
            gain=gain.as_json(),
            required_min=gain_min.as_json(),
        )

    tasks, tasks_status = _metric(
        metrics, "successful_tasks_per_hour", candidate_path, ledger
    )
    reference_tasks, reference_tasks_status = _metric(
        metrics, "reference_tasks_per_hour", candidate_path, ledger
    )
    tasks_ratio_min = _threshold(thresholds, "min_tasks_per_hour_ratio", ledger)
    if tasks is None or reference_tasks is None or tasks_ratio_min is None:
        axes["tasks_per_hour"] = {
            "status": "unknown",
            "missing_variables": [
                "successful_tasks_per_hour",
                "reference_tasks_per_hour",
            ],
        }
    else:
        ratio = tasks.divide(reference_tasks)
        axes["tasks_per_hour"] = evidence_result(
            _comparison_at_least(ratio, tasks_ratio_min),
            {
                "successful_tasks_per_hour": tasks_status,
                "reference_tasks_per_hour": reference_tasks_status,
            },
            ratio=ratio.as_json(),
            required_min=tasks_ratio_min.as_json(),
        )
    return axes


def _max_tokens(
    available: Interval,
    fixed_demand: Interval,
    per_token: Interval,
    multiplier: Interval,
) -> Interval:
    numerator = available.subtract(fixed_demand)
    denominator = per_token.multiply(multiplier)
    if numerator.high is not None and numerator.high < 0:
        return Interval(0.0, 0.0)
    if numerator.low is not None:
        numerator = Interval(max(0.0, numerator.low), numerator.high)
    return numerator.divide(denominator).floor()


def _classify_candidate(
    candidate: dict[str, Any],
    requirements: dict[str, Any],
    required_tokens: Interval,
    concurrency: Interval,
    physical_available: Interval,
    policy_available: Interval,
    thresholds: dict[str, Any],
    ledger: list[dict[str, Any]],
    index: int,
) -> dict[str, Any]:
    path = f"candidates[{index}]"
    candidate_ledger_start = len(ledger)
    candidate_id = candidate.get("id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise FeasibilityError(f"{path}.id must be a non-empty string")

    context_limit = _variable(
        candidate.get("runtime_context_limit_tokens", {"status": "unknown", "notes": "runtime context limit not supplied"}),
        f"{path}.runtime_context_limit_tokens",
        ledger,
    )
    measured_context_raw = candidate.get("measured_max_stable_context_tokens")
    measured_context = (
        None
        if measured_context_raw is None
        else _variable(measured_context_raw, f"{path}.measured_max_stable_context_tokens", ledger)
    )
    measured_context_status = (
        None if measured_context_raw is None else measured_context_raw.get("status")
    )
    memory_ledger_start = len(ledger)
    resident = _variable_map(candidate.get("resident_components"), f"{path}.resident_components", ledger)
    per_sequence = _variable_map(
        candidate.get("per_sequence_components"), f"{path}.per_sequence_components", ledger
    )
    per_token = _variable_map(
        candidate.get("per_token_components"), f"{path}.per_token_components", ledger
    )
    multiplier = _variable(
        candidate.get("kv_token_multiplier", {"status": "unknown", "notes": "KV residency multiplier not supplied"}),
        f"{path}.kv_token_multiplier",
        ledger,
    )
    memory_ledger_end = len(ledger)
    global_load_bearing_prefixes = (
        "requirements.tokens.",
        "requirements.concurrency",
        "requirements.physical_vram_bytes",
        "requirements.vram_reserves.",
    )
    global_load_bearing_records = [
        item
        for item in ledger[:candidate_ledger_start]
        if item["path"].startswith(global_load_bearing_prefixes)
    ]
    load_bearing_records = (
        global_load_bearing_records + ledger[candidate_ledger_start:memory_ledger_end]
    )
    memory_bound_records = (
        global_load_bearing_records + ledger[memory_ledger_start:memory_ledger_end]
    )
    load_bearing_unknowns = [
        item["path"]
        for item in load_bearing_records
        if item["status"] == "unknown" or item["min"] is None or item["max"] is None
    ]
    planning_bound_inputs = [
        item["path"]
        for item in load_bearing_records
        if item["status"] in {"estimated", "assumed"}
    ]
    untrusted_memory_bound_inputs = [
        item["path"]
        for item in memory_bound_records
        if item["status"] not in {"measured", "confirmed"}
    ]
    memory_bound_unknowns = [
        item["path"]
        for item in memory_bound_records
        if item["status"] == "unknown" or item["min"] is None or item["max"] is None
    ]
    sequence_demand = per_sequence.multiply(concurrency)
    fixed_demand = resident.add(sequence_demand)
    token_demand = per_token.multiply(required_tokens).multiply(multiplier)
    total_demand = fixed_demand.add(token_demand)

    context_axis = _comparison_at_least(context_limit, required_tokens)
    measured_context_axis = None
    if measured_context is not None:
        measured_context_axis = _comparison_at_least(measured_context, required_tokens)

    if total_demand.low is not None and physical_available.high is not None and total_demand.low > physical_available.high:
        memory_axis = "physical-fail"
    elif total_demand.low is not None and policy_available.high is not None and total_demand.low > policy_available.high:
        memory_axis = "policy-fail"
    elif total_demand.high is not None and policy_available.low is not None and total_demand.high <= policy_available.low:
        memory_axis = "pass"
    else:
        memory_axis = "unknown"

    metrics = candidate.get("metrics") or {}
    if not isinstance(metrics, dict):
        raise FeasibilityError(f"{path}.metrics must be an object")
    _track_variables(candidate.get("tracked_variables"), f"{path}.tracked_variables", ledger)
    behavior = _behavior_axes(metrics, thresholds, path, ledger)
    hard_failures = candidate.get("hard_failures") or []
    if not isinstance(hard_failures, list) or not all(isinstance(item, str) and item.strip() for item in hard_failures):
        raise FeasibilityError(f"{path}.hard_failures must be an array of non-empty strings")

    behavior_statuses = [axis["status"] for axis in behavior.values()]
    missing_behavioral_variables = sorted(
        {
            name
            for axis in behavior.values()
            for name in axis.get("missing_variables", [])
        }
    )
    reasons: list[str] = []
    if hard_failures:
        classification = "empirically-disqualified"
        reasons.extend(hard_failures)
    elif measured_context_axis == "fail" and measured_context_status in {"measured", "confirmed"}:
        classification = "empirically-disqualified"
        reasons.append("measured stable context is below the required token budget")
    elif measured_context_axis == "fail" and measured_context_status in {"estimated", "assumed"}:
        classification = "modeled-infeasible"
        reasons.append("modeled stable context is below the required token budget")
    elif measured_context_axis == "fail":
        classification = "unresolved"
        reasons.append("unverified stable-context bounds are below the required token budget")
    elif context_axis == "fail":
        context_status = (candidate.get("runtime_context_limit_tokens") or {}).get("status")
        if context_status in {"measured", "confirmed"}:
            classification = "proven-infeasible"
        elif context_status in {"estimated", "assumed"}:
            classification = "modeled-infeasible"
        else:
            classification = "unresolved"
        reasons.append("runtime context limit is below the required token budget")
    elif memory_axis == "physical-fail":
        if memory_bound_unknowns:
            classification = "unresolved"
        elif untrusted_memory_bound_inputs:
            classification = "modeled-infeasible"
        else:
            classification = "proven-infeasible"
        reasons.append("optimistic demand exceeds optimistic physical VRAM under encoded bounds")
    elif memory_axis == "policy-fail":
        if memory_bound_unknowns:
            classification = "unresolved"
            reasons.append("policy failure depends on an unbounded or unknown input")
        else:
            classification = "policy-infeasible"
            reasons.append("optimistic demand exceeds the declared policy VRAM envelope under encoded bounds")
    elif "fail" in behavior_statuses:
        classification = "requirements-disqualified"
        reasons.append("one or more measured behavior thresholds fail")
    elif context_axis == "unknown" or memory_axis == "unknown":
        classification = "unresolved"
        reasons.append("context or VRAM bounds overlap or remain unbounded")
    elif all(status == "pass" for status in behavior_statuses):
        classification = "math-qualified"
        reasons.append("all encoded resource bounds and measured thresholds pass")
    else:
        classification = "benchmark-survivor"
        reasons.append("resource bounds pass; behavioral evidence remains incomplete")

    max_tokens_physical = _max_tokens(physical_available, fixed_demand, per_token, multiplier)
    max_tokens_policy = _max_tokens(policy_available, fixed_demand, per_token, multiplier)
    physical_margin = physical_available.subtract(total_demand)
    policy_margin = policy_available.subtract(total_demand)

    return {
        "id": candidate_id,
        "classification": classification,
        "reasons": reasons,
        "axes": {
            "context_limit": context_axis,
            "measured_context": measured_context_axis or "not-measured",
            "measured_context_evidence_status": measured_context_status or "not-supplied",
            "memory": memory_axis,
            **behavior,
        },
        "required_tokens": required_tokens.as_json(),
        "demand_bytes": total_demand.as_json(),
        "physical_available_bytes": physical_available.as_json(),
        "policy_available_bytes": policy_available.as_json(),
        "physical_margin_bytes": physical_margin.as_json(),
        "policy_margin_bytes": policy_margin.as_json(),
        "estimated_max_tokens_physical": max_tokens_physical.as_json(),
        "estimated_max_tokens_policy": max_tokens_policy.as_json(),
        "load_bearing_unknowns": load_bearing_unknowns,
        "planning_bound_inputs": planning_bound_inputs,
        "missing_evidence": sorted(
            (["measured_max_stable_context_tokens"] if measured_context_status not in {"measured", "confirmed"} else [])
            + missing_behavioral_variables
        ),
    }


def evaluate(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema") != INPUT_SCHEMA:
        raise FeasibilityError(f"schema must be {INPUT_SCHEMA!r}")
    campaign = data.get("campaign")
    if not isinstance(campaign, str) or not campaign.strip():
        raise FeasibilityError("campaign must be a non-empty string")
    requirements = data.get("requirements")
    if not isinstance(requirements, dict):
        raise FeasibilityError("requirements must be an object")
    ledger: list[dict[str, Any]] = []
    _track_variables(data.get("tracked_variables"), "tracked_variables", ledger)
    required_tokens = _variable_map(requirements.get("tokens"), "requirements.tokens", ledger)
    concurrency = _variable(requirements.get("concurrency"), "requirements.concurrency", ledger)
    physical_vram = _variable(
        requirements.get("physical_vram_bytes"), "requirements.physical_vram_bytes", ledger
    )

    physical_reserves: list[Interval] = []
    policy_reserves: list[Interval] = []
    reserves = requirements.get("vram_reserves") or {}
    if not isinstance(reserves, dict):
        raise FeasibilityError("requirements.vram_reserves must be an object")
    for name, reserve in reserves.items():
        if not isinstance(reserve, dict):
            raise FeasibilityError(f"requirements.vram_reserves.{name} must be an object")
        kind = reserve.get("kind")
        if kind not in {"physical", "policy"}:
            raise FeasibilityError(
                f"requirements.vram_reserves.{name}.kind must be physical or policy"
            )
        interval = _variable(
            reserve.get("variable"), f"requirements.vram_reserves.{name}.variable", ledger
        )
        policy_reserves.append(interval)
        if kind == "physical":
            physical_reserves.append(interval)

    physical_available = physical_vram.subtract(_sum(physical_reserves))
    policy_available = physical_vram.subtract(_sum(policy_reserves))
    thresholds = requirements.get("thresholds") or {}
    if not isinstance(thresholds, dict):
        raise FeasibilityError("requirements.thresholds must be an object")
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise FeasibilityError("candidates must be a non-empty array")
    results = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise FeasibilityError(f"candidates[{index}] must be an object")
        results.append(
            _classify_candidate(
                candidate,
                requirements,
                required_tokens,
                concurrency,
                physical_available,
                policy_available,
                thresholds,
                ledger,
                index,
            )
        )
    deduped_ledger = list({item["path"]: item for item in ledger}.values())
    unresolved_variables = [
        item["path"]
        for item in deduped_ledger
        if item["status"] == "unknown" or item["min"] is None or item["max"] is None
    ]
    return {
        "schema": OUTPUT_SCHEMA,
        "campaign": campaign,
        "equations": {
            "required_tokens": "sum(requirements.tokens)",
            "policy_available_vram": "physical_vram - sum(all vram_reserves)",
            "demand": "resident + concurrency*per_sequence + required_tokens*kv_token_multiplier*per_token",
            "relative_quality_loss": "1 - quality/reference_quality",
            "warm_e2e_gain": "1 - warm_e2e/no_spec_warm_e2e",
            "tasks_per_hour_ratio": "tasks_per_hour/reference_tasks_per_hour",
        },
        "required_tokens": required_tokens.as_json(),
        "physical_available_bytes": physical_available.as_json(),
        "policy_available_bytes": policy_available.as_json(),
        "candidates": results,
        "variables": deduped_ledger,
        "unresolved_variables": unresolved_variables,
        "promotion_authority": False,
    }


def _format_interval(raw: dict[str, Any], *, gib: bool = False) -> str:
    low = raw.get("min")
    high = raw.get("max")
    if low is None and high is None:
        return "unknown"
    divisor = 2**30 if gib else 1
    suffix = " GiB" if gib else ""

    def render(value: int | float | None) -> str:
        if value is None:
            return "?"
        scaled = value / divisor
        return f"{scaled:,.3f}" if gib else f"{scaled:,.0f}"

    if low == high:
        return render(low) + suffix
    return f"[{render(low)}, {render(high)}]{suffix}"


def to_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# Recipe feasibility: {result['campaign']}",
        "",
        f"Required tokens: {_format_interval(result['required_tokens'])}",
        "",
        "| Candidate | Classification | Estimated policy T_max | Policy VRAM margin | Reason |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for candidate in result["candidates"]:
        reason = "; ".join(candidate["reasons"]).replace("|", "\\|")
        lines.append(
            "| {id} | {classification} | {tokens} | {margin} | {reason} |".format(
                id=candidate["id"].replace("|", "\\|"),
                classification=candidate["classification"],
                tokens=_format_interval(candidate["estimated_max_tokens_policy"]),
                margin=_format_interval(candidate["policy_margin_bytes"], gib=True),
                reason=reason,
            )
        )
    lines.extend(
        [
            "",
            "## Missing evidence",
            "",
        ]
    )
    for candidate in result["candidates"]:
        missing = candidate.get("missing_evidence") or []
        if missing:
            rendered = ", ".join(f"`{name}`" for name in missing)
            lines.append(f"- `{candidate['id']}`: {rendered}")
    load_bearing = [
        candidate
        for candidate in result["candidates"]
        if candidate.get("classification") == "unresolved"
        and candidate.get("load_bearing_unknowns")
    ]
    if load_bearing:
        lines.extend(
            [
                "",
                "## Load-bearing unknowns",
                "",
            ]
        )
        for candidate in load_bearing:
            rendered = ", ".join(
                f"`{name}`" for name in candidate["load_bearing_unknowns"]
            )
            lines.append(f"- `{candidate['id']}`: {rendered}")
    unbounded = result.get("unresolved_variables") or []
    if unbounded:
        lines.extend(
            [
                "",
                "## Unbounded variables",
                "",
                *[f"- `{name}`" for name in unbounded],
            ]
        )
    lines.extend(
        [
            "",
            "`benchmark-survivor` and `math-qualified` do not authorize production promotion.",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="feasibility input JSON")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        result = evaluate(data)
    except (OSError, json.JSONDecodeError, FeasibilityError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    output = (
        json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        if args.format == "json"
        else to_markdown(result)
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
