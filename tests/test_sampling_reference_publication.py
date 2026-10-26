"""Bind sampling-reference publication claims to retained native receipts."""

from collections import defaultdict
import json
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
FINDING = ROOT / "docs/findings/2026-10-25-sampling-reference.md"
BUNDLE = FINDING.with_name(FINDING.stem + "-evidence")


def read(name):
    return json.loads((BUNDLE / name).read_text())


def receipts():
    grouped = defaultdict(list)
    for line in (BUNDLE / "kernel-receipts.log").read_text().splitlines():
        kind, payload = line.split(" ", 1)
        assert kind.startswith("ANVIL_SAMPLING_")
        grouped[kind.removeprefix("ANVIL_SAMPLING_")].append(json.loads(payload))
    return grouped


def test_counts_derive_from_complete_case_receipts():
    raw = receipts()
    assert len(raw["INPUTS"]) == len(raw["REGRESSION"]) == 1
    inputs, final = raw["INPUTS"][0], raw["REGRESSION"][0]
    cases = raw["ORDINARY_CASE"]
    expected_cases = {(seed, vocabulary, rows) for seed in inputs["ordinary_seeds"]
                      for vocabulary, _, rows in inputs["shapes"]}
    assert {(case["seed"], case["vocabulary"], case["rows"]) for case in cases} == expected_cases
    assert len(cases) == len(expected_cases) == final["ordinary_cases"] == 8
    forced = raw["FORCED_CASE"]
    assert [case["case"] for case in forced] == list(range(1, 9))
    assert all(case["passed"] for case in forced)
    assert len(forced) == final["forced_cases"] == 8
    rows = sum(case["rows"] for case in cases)
    differing = sum(case["version_difference_rows"] for case in cases)
    failed = sum(case["failed_rows"] for case in cases)
    baseline_exact = sum(case["baseline_exact_reference_rows"] for case in cases)
    patched_exact = sum(case["patched_exact_reference_rows"] for case in cases)
    assert (rows, rows - differing, baseline_exact, patched_exact, failed) == (272, 271, 36, 35, 1)
    assert rows == final["ordinary_rows"] and failed == final["failed_rows"]
    assert all(case["input_unchanged"] for case in cases)

    kernel, summary = read("kernel-summary.json"), read("summary.json")
    assert kernel["ordinary_rows"] == summary["measurement"]["ordinary_rows"] == rows
    assert kernel["ordinary_cases"] == summary["measurement"]["ordinary_cases"] == len(cases)
    assert kernel["forced_cases_passed"] == summary["measurement"]["forced_cases"] == len(forced)
    assert kernel["ordinary_version_identical_rows"] == summary["ordinary"]["version_identical_rows"] == rows - differing
    assert kernel["baseline_exact_reference_rows"] == summary["ordinary"]["baseline_exact_reference_rows"] == baseline_exact
    assert kernel["patched_exact_reference_rows"] == summary["ordinary"]["patched_exact_reference_rows"] == patched_exact
    assert kernel["ordinary_gate_passed_rows"] == rows - failed
    assert summary["ordinary"]["failed_rows"] == failed
    headlines = {item["name"]: item["value"] for item in summary["headline_results"]}
    assert headlines["forced_cases_passed"] == len(forced)
    assert headlines["ordinary_version_identical_rows"] == rows - differing
    assert headlines["ordinary_policy_failed_rows"] == failed


def test_failed_gate_and_operational_only_restoration_remain_separate():
    raw = receipts()
    final = raw["REGRESSION"][0]
    assert final["passed"] is False
    assert len(raw["ORDINARY_ROW"]) == final["diagnostic_rows"] == 1
    difference = raw["ORDINARY_ROW"][0]
    kernel, summary = read("kernel-summary.json"), read("summary.json")
    assert kernel["difference"] == difference
    assert difference["failed_checks"] == ["patched_difference_not_worse"]
    assert difference["patched"]["kept_count"] - difference["reference"]["kept_count"] == 1
    assert difference["patched"]["reference_difference"]["first32"] == [summary["failing_row"]["patched_extra_token"]]
    assert difference["patched"]["extra_mass"] == summary["failing_row"]["patched_extra_probability_mass"]
    assert kernel["gate"] == summary["kernel_gate"] == "failed"
    assert kernel["candidate_weight_load"] is summary["candidate_weights_loaded"] is False
    assert kernel["candidate_inference_requests"] == summary["candidate_inference_requests"] == 0
    assert summary["boundary_failure_resolved"] == "unproven"
    assert kernel["promoted"] is summary["promoted"] is False
    assert kernel["ignis"] == summary["ignis"] == "hold"

    restoration, smoke, closure = read("restoration.json"), read("restoration-smoke.json"), read("closure.json")
    assert restoration["operational_restoration"]["verified"] is True
    assert restoration["verified"] is False and restoration["status"] == "incomplete"
    assert restoration["functional_smoke"]["status"] == "failed"
    assert smoke["passed"] is False and len(smoke["observations"]) == closure["smoke_requests"] == 1
    with pytest.raises(json.JSONDecodeError):
        json.loads(smoke["observations"][0]["content"])
    assert closure["smoke_exact_json"] is False and closure["restoration_acceptance"] == "incomplete"
    assert closure["router_ready"] is closure["router_admitting"] is True
    assert closure["engine_running"] == closure["engine_waiting"] == closure["router_active_requests"] == 0
    gates = {gate["name"]: gate["status"] for gate in summary["gates"]}
    assert gates["end-to-end-boundary"] == "not_run"
    assert gates["operational-restoration"] == "passed"
    assert gates["functional-restoration-smoke"] == "failed"
    assert summary["restoration"] == "operational_verified_functional_failed"


def test_result_card_exposes_counts_and_failed_verdicts():
    text = FINDING.read_text()
    table = {cells[0]: cells[1] for line in text.splitlines() if line.startswith("|")
             if len(cells := [cell.strip() for cell in line.strip("|").split("|")]) >= 2}
    summary = read("summary.json")
    rows = summary["measurement"]["ordinary_rows"]
    def fractions(label):
        return [(int(a), int(b)) for a, b in re.findall(r"(\d+)\s*/\s*(\d+)", table[label])]
    assert fractions("Ordinary rows") == [(summary["ordinary"]["version_identical_rows"], rows)]
    assert fractions("Exact-reference rows") == [(summary["ordinary"]["baseline_exact_reference_rows"], rows),
                                                  (summary["ordinary"]["patched_exact_reference_rows"], rows)]
    assert "failed" in table["Strict ordinary gate"]
    assert int(table["Candidate model inference requests"]) == 0
    assert re.search(r"Operational.*passed.*JSON.*failed", table["Restoration"])
    assert "`no-promotion`" in text
