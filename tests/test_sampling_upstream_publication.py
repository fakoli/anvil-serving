"""Recompute the prospective sampling trial's claims from retained receipts."""

from collections import defaultdict
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FINDING = ROOT / "docs/findings/2026-10-25-sampling-upstream-tolerance.md"
BUNDLE = FINDING.with_name(FINDING.stem + "-evidence")


def read(name):
    return json.loads((BUNDLE / name).read_text())


def test_native_count_policy_and_previous_difference_remain_auditable():
    receipts = defaultdict(list)
    for line in (BUNDLE / "kernel-receipts.log").read_text().splitlines():
        kind, payload = line.split(" ", 1)
        receipts[kind.removeprefix("ANVIL_SAMPLING_")].append(json.loads(payload))
    inputs = receipts["INPUTS"][0]
    cases = receipts["ORDINARY_CASE"]
    expected = {(seed, vocabulary, rows) for seed in inputs["ordinary_seeds"]
                for vocabulary, _, rows in inputs["shapes"]}
    assert {(c["seed"], c["vocabulary"], c["rows"]) for c in cases} == expected
    assert len(cases) == len(expected) == 8
    assert [c["case"] for c in receipts["FORCED_CASE"]] == list(range(1, 9))
    assert all(c["passed"] for c in receipts["FORCED_CASE"])
    for case in cases:
        assert case["input_unchanged"] and case["failed_rows"] == 0
        native = case["native_counts"]
        assert len(native) == case["rows"] and all(n > 0 for n in native)
        for version in ("baseline", "patched"):
            counts = case[version + "_counts"]
            difference = max(abs(a - b) for a, b in zip(native, counts, strict=True))
            maximum = max(native)
            tolerance = case["native_count_tolerance"][version]
            assert tolerance["maximum_count_difference"] == difference
            assert tolerance["maximum_native_count"] == maximum
            assert tolerance["passed"] is (difference <= 3 or 200 * difference < maximum)
            assert tolerance["passed"] and tolerance["policy"] == "upstream-count-v1"
    rows = sum(c["rows"] for c in cases)
    same = rows - sum(c["version_difference_rows"] for c in cases)
    exact = tuple(sum(c[v + "_exact_reference_rows"] for c in cases)
                  for v in ("baseline", "patched"))
    assert (rows, same, exact) == (272, 271, (36, 35))
    final = receipts["REGRESSION"][0]
    assert final["passed"] and final["failed_native_batches"] == final["failed_rows"] == 0
    assert len(receipts["ORDINARY_ROW"]) == final["diagnostic_rows"] == 1
    delta = receipts["ORDINARY_ROW"][0]
    assert delta["passed"] and delta["failed_checks"] == []
    assert delta["patched"]["kept_count"] - delta["baseline"]["kept_count"] == 1
    assert delta["patched"]["reference_difference"]["first32"] == [30317]
    assert delta["strict_diagnostics"]["patched_difference_not_worse"] is False
    kernel, summary = read("kernel-summary.json"), read("summary.json")
    assert kernel["result"] == final
    assert summary["measurement"]["ordinary_rows"] == final["ordinary_rows"] == rows
    assert summary["kernel"]["stock_patch_equal_rows"] == kernel["stock_patch_equal_rows"] == same
    assert summary["kernel"]["baseline_exact_binary64_reference_rows"] == exact[0]
    assert summary["kernel"]["patched_exact_binary64_reference_rows"] == exact[1]


def test_raw_tokens_and_fresh_dispatch_do_not_turn_strict_failure_into_pass():
    response, result = read("response.json"), read("probe-result.json")
    assert len(result["records"]) == len(response["choices"]) == 1
    choice = response["choices"][0]
    tokens = choice["token_ids"]
    assert len(tokens) == response["usage"]["completion_tokens"] == 137
    assert response["usage"]["prompt_tokens"] + len(tokens) == response["usage"]["total_tokens"] == 185
    closes = [i for i, token in enumerate(tokens) if token == 154842]
    assert closes == [64, 126] and tokens.count(0) == tokens.count(154841) == 0
    assert choice["finish_reason"] == "stop" and choice["stop_reason"] == 154827
    assert response["usage"]["completion_tokens_details"]["reasoning_tokens"] == 64
    content = choice["message"]["content"]
    assert "9567,1085,10652" in content and content.strip() != "9567,1085,10652"
    assert result["records"][0]["oracle"] is False
    assert result["records"][0]["boundary"] == "invalid_repeated_or_missing_close"
    assert result["runtime_verified"] is False and result["engine_drain"] == "unknown"
    assert result["runtime_identity_authority"] == "operator_attested"
    boundary, summary = read("boundary-summary.json"), read("summary.json")
    assert summary["boundary"] == boundary
    assert boundary["closing_token_positions_zero_based"] == closes
    assert boundary["completion_tokens"] == len(tokens) and boundary["token_zero_count"] == 0
    assert boundary["exact_answer_oracle"] is False
    traces = read("serving-worker-traces.json")["receipts"]
    assert len(traces) == boundary["candidate_worker_trace_receipts"] == 2
    for trace in traces:
        row = trace["receipt"]
        assert row["rows"] == 4 and row["vocab"] == 154880 and row["has_k"] == 0
        assert row["forced_rows"] == row["token_154842_finite_rows"] == 2
        assert row["forced_finite_min"] == row["forced_finite_max"] == row["split_mask_completed"] == 1
        assert "pid" not in row
    freshness = read("trace-freshness.json")
    assert freshness["pre_request_warnings_are_exact_prefix"]
    assert freshness["post_request_warning_count"] - freshness["pre_request_warning_count"] == 2
    assert freshness["appended_sampling_trace_count"] == len(traces)
    gates = {gate["name"]: gate["status"] for gate in summary["gates"]}
    assert gates["reasoning-boundary"] == gates["strict-answer"] == "failed"
    assert summary["promoted"] is False and summary["ignis"] == "hold"


def test_restored_identity_and_existing_json_failure_remain_separate():
    before, restored = read("runtime-before.json"), read("runtime-restored.json")
    assert before["capture"] == restored["capture"]
    for field in ("revision", "image_id"):
        assert before["identity"][field] == restored["identity"][field]
    for field in ("command_sha256", "entrypoint_sha256", "mounts_sha256",
                  "reasoning_parser", "speculative_tokens"):
        assert before["launch"][field] == restored["launch"][field]
    assert restored["resources"]["engine"] == {"running": 0.0, "waiting": 0.0}
    router = read("router-restored.json")["tiers"][0]
    assert router["ready"] and router["state"] == "admitting" and router["active_requests"] == 0
    closure, restoration, summary = read("closure.json"), read("restoration.json"), read("summary.json")
    assert all(closure["checks"].values()) and closure["operational_restoration_verified"]
    assert restoration["operational_restoration"]["verified"]
    assert restoration["verified"] is False and restoration["status"] == "incomplete"
    assert closure["quality_regression_established"] is summary["quality_regression_established"] is False
    for file, count in (("baseline-smoke.json", 53), ("restoration-smoke.json", 66)):
        smoke = read(file)
        assert smoke["passed"] is False and len(smoke["observations"]) == 1
        observation = smoke["observations"][0]
        assert observation["usage"]["completion_tokens"] == count
        with pytest.raises(json.JSONDecodeError):
            json.loads(observation["content"])
    gates = {gate["name"]: gate["status"] for gate in summary["gates"]}
    assert gates["operational-restoration"] == "passed"
    assert gates["functional-restoration-smoke"] == gates["baseline-json-smoke"] == "failed"
    assert summary["complete"] and summary["restoration"] == "operational_verified_existing_json_failure_reproduced"
    assert closure["trial_elapsed_seconds"] < closure["target_window_seconds"]
    card = FINDING.read_text().split("## Prospective policy", 1)[0]
    assert "137 completion tokens" in card and "271 / 272" in card
    assert "Operational passed; strict JSON failed before and after" in card
    assert "`no-promotion`" in card
