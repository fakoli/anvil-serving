"""Hermetic tests for the T017 traffic-window metrics + silent-failure gate.

Exercise :mod:`anvil_serving.router.metrics` over the COMMITTED traffic window
(``tests/router/fixtures/traffic.jsonl``). No network, no real tier, no clock
dependence. Coverage:

* **(a)** the report carries accept-rate + silent-failure rate + cloud-tokens-
  saved per work-class (structured summary AND the human table);
* **(b)** the committed window's silent-failure rate is below the threshold, so
  the gate passes (``main`` exits 0);
* **(c)** a second, high-silent-failure window trips the gate (``main`` exits
  non-zero) — proving the threshold guard actually bites;
* **(d)** determinism — two replays are byte-identical.

Plus unit coverage of the load-bearing definitions (the silent-failure rule,
locality, cloud-tokens-saved accounting) and the JSONL parser.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from anvil_serving.router import metrics

# Repo root: tests/router/this_file -> parents[2].
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "router" / "fixtures" / "traffic.jsonl"

# The committed window's composition (mirrors the documented fixture build). Each
# value is independently derivable from the fixture, so these pin the metrics to
# the intended traffic, not to whatever the code happens to emit.
EXPECTED_TOTAL = {
    "chat": 8,
    "bounded-edit": 8,
    "review": 5,
    "planning": 6,
    "long-context": 5,
    "multi-file-refactor": 3,
}
EXPECTED_ACCEPTED_LOCAL = {
    "chat": 8,
    "bounded-edit": 6,
    "review": 4,
    "planning": 0,        # planning correctly routes to cloud (deny gate) -> 0 local
    "long-context": 5,
    "multi-file-refactor": 2,
}
EXPECTED_ACCEPT_RATE = {
    "chat": 1.0,
    "bounded-edit": 0.75,
    "review": 0.8,
    "planning": 0.0,
    "long-context": 1.0,
    "multi-file-refactor": 2 / 3,
}
EXPECTED_SAVED = {
    "chat": 480,          # 8 * (25 + 35)
    "bounded-edit": 1260,  # 6 * (120 + 90)
    "review": 1350,       # 3 * (200 + 150) + 1 * (180 + 120)
    "planning": 0,        # served by cloud -> nothing saved
    "long-context": 32000,  # 5 * (6000 + 400)
    "multi-file-refactor": 2800,  # 2 * (800 + 600)
}
TOTAL_RECORDS = sum(EXPECTED_TOTAL.values())              # 35
TOTAL_SAVED = sum(EXPECTED_SAVED.values())                # 37890
TOTAL_ACCEPTED_LOCAL = sum(EXPECTED_ACCEPTED_LOCAL.values())  # 25


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def summary():
    return metrics.aggregate(metrics.load_records(FIXTURE))


def _served_local_record(work_class="chat", *, ground_truth="pass", tokens=(30, 40),
                         tier="fast-local"):
    p, c = tokens
    return {
        "work_class": work_class,
        "intent": "chat",
        "requested_tiers": [tier, "cloud"],
        "attempts": [
            {"tier_id": tier, "verifier_passed": True, "verify_reason": "verify passed",
             "prompt_tokens": p, "completion_tokens": c, "outcome": "served", "detail": ""}
        ],
        "served_tier": tier,
        "served_tier_privacy": "local",
        "total_prompt_tokens": p,
        "total_completion_tokens": c,
        "fell_back": False,
        "ground_truth": ground_truth,
    }


def _write_jsonl(path: Path, records) -> Path:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


# --------------------------------------------------------------------------- #
# guard: the committed fixture exists and parses
# --------------------------------------------------------------------------- #
def test_committed_fixture_present_and_parses():
    assert FIXTURE.is_file(), f"committed traffic window missing: {FIXTURE}"
    records = metrics.load_records(FIXTURE)
    assert len(records) == TOTAL_RECORDS
    # Every record carries the real decision-log field names (not an invented schema).
    for r in records:
        for field in ("work_class", "requested_tiers", "attempts", "served_tier",
                      "total_prompt_tokens", "total_completion_tokens", "fell_back"):
            assert field in r, f"record missing decision-log field {field!r}: {r}"


# --------------------------------------------------------------------------- #
# (a) the report carries the three metrics per work-class
# --------------------------------------------------------------------------- #
def test_report_has_three_metrics_per_work_class(summary):
    assert set(summary["work_classes"]) == set(EXPECTED_TOTAL)
    for wc, block in summary["work_classes"].items():
        # The three required metrics are present and well-typed for every class.
        assert "accept_rate" in block
        assert "silent_failure_rate" in block
        assert "cloud_tokens_saved" in block
        assert 0.0 <= block["accept_rate"] <= 1.0
        assert 0.0 <= block["silent_failure_rate"] <= 1.0
        assert isinstance(block["cloud_tokens_saved"], int)
        assert block["total"] == EXPECTED_TOTAL[wc]


def test_human_report_renders_metrics_and_classes(summary):
    report = metrics.format_report(summary)
    # The three metric columns are labeled in the table header.
    assert "accept" in report
    assert "sf_rate" in report           # silent-failure rate
    assert "cloud_tok_saved" in report
    # Every work-class has a row, plus an OVERALL row and a gate verdict line.
    for wc in EXPECTED_TOTAL:
        assert wc in report
    assert "OVERALL" in report
    assert "GATE: PASS" in report


def test_accept_rate_and_saved_match_committed_composition(summary):
    for wc in EXPECTED_TOTAL:
        block = summary["work_classes"][wc]
        assert block["accepted_local"] == EXPECTED_ACCEPTED_LOCAL[wc]
        assert block["accept_rate"] == pytest.approx(EXPECTED_ACCEPT_RATE[wc])
        assert block["cloud_tokens_saved"] == EXPECTED_SAVED[wc]
    assert summary["overall"]["accepted_local"] == TOTAL_ACCEPTED_LOCAL
    assert summary["overall"]["cloud_tokens_saved"] == TOTAL_SAVED


def test_cloud_tokens_saved_equals_independent_sum():
    # Independently recompute "tokens served locally" by summing the record
    # totals over the served-local records (a different code path than the
    # served-attempt accounting in metrics) — they must agree.
    records = metrics.load_records(FIXTURE)
    independent = sum(
        r["total_prompt_tokens"] + r["total_completion_tokens"]
        for r in records
        if r.get("served_tier_privacy") == "local" and r.get("served_tier") is not None
    )
    summary = metrics.aggregate(records)
    assert summary["overall"]["cloud_tokens_saved"] == independent == TOTAL_SAVED


# --------------------------------------------------------------------------- #
# (b) the committed window passes the silent-failure gate
# --------------------------------------------------------------------------- #
def test_silent_failure_rate_below_threshold_on_fixture(summary):
    # Overall and every work-class are strictly below the 1% default threshold.
    assert summary["overall"]["silent_failure_rate"] < metrics.DEFAULT_SILENT_FAILURE_THRESHOLD
    for wc, block in summary["work_classes"].items():
        assert block["silent_failure_rate"] < metrics.DEFAULT_SILENT_FAILURE_THRESHOLD, wc
    assert summary["breaches"] == []
    assert summary["gate_passed"] is True


def test_main_exits_zero_on_committed_fixture(capsys):
    rc = metrics.main(["--replay", str(FIXTURE)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "GATE: PASS" in out


def test_main_json_output_is_valid_machine_summary(capsys):
    rc = metrics.main(["--replay", str(FIXTURE), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["schema"] == metrics.SCHEMA
    assert payload["gate_passed"] is True
    assert payload["total_records"] == TOTAL_RECORDS
    assert payload["overall"]["cloud_tokens_saved"] == TOTAL_SAVED


# --------------------------------------------------------------------------- #
# (c) a high-silent-failure window trips the gate (the guard bites)
# --------------------------------------------------------------------------- #
def test_high_silent_failure_window_fails_gate(tmp_path, capsys):
    # 2 of 4 accepted-local responses were actually wrong -> 50% silent-failure
    # rate, far above the 1% threshold. The gate MUST fail (non-zero exit).
    bad = [
        _served_local_record("chat", ground_truth="pass"),
        _served_local_record("chat", ground_truth="fail"),   # silent failure
        _served_local_record("chat", ground_truth="pass"),
        _served_local_record("chat", ground_truth="fail"),   # silent failure
    ]
    bad_fixture = _write_jsonl(tmp_path / "bad_traffic.jsonl", bad)

    rc = metrics.main(["--replay", str(bad_fixture)])
    out = capsys.readouterr().out
    assert rc == 1, "gate must exit non-zero when the silent-failure rate breaches"
    assert "GATE: FAIL" in out


def test_breach_detected_per_class_and_overall(tmp_path):
    bad = [
        _served_local_record("chat", ground_truth="fail"),   # 1/1 chat -> 100%
        _served_local_record("chat", ground_truth="fail"),
        _served_local_record("review", ground_truth="pass"),  # review clean
        _served_local_record("review", ground_truth="pass"),
    ]
    summary = metrics.aggregate(bad)
    assert summary["work_classes"]["chat"]["silent_failure_rate"] == pytest.approx(1.0)
    assert summary["work_classes"]["review"]["silent_failure_rate"] == 0.0
    # The breaching class AND the overall window are flagged; the clean class is not.
    assert "chat" in summary["breaches"]
    assert metrics.OVERALL_KEY in summary["breaches"]
    assert "review" not in summary["breaches"]
    assert summary["gate_passed"] is False


def test_gate_boundary_rate_equal_to_threshold_breaches():
    # A rate exactly AT the threshold is NOT strictly below it -> a breach.
    records = [_served_local_record("chat", ground_truth="fail")] + [
        _served_local_record("chat", ground_truth="pass") for _ in range(99)
    ]  # 1/100 = exactly 0.01
    summary = metrics.aggregate(records, threshold=0.01)
    assert summary["overall"]["silent_failure_rate"] == pytest.approx(0.01)
    assert summary["gate_passed"] is False
    # Loosening the threshold just past the rate lets it pass.
    relaxed = metrics.aggregate(records, threshold=0.02)
    assert relaxed["gate_passed"] is True


# --------------------------------------------------------------------------- #
# (d) determinism
# --------------------------------------------------------------------------- #
def test_two_replays_are_identical(capsys):
    metrics.main(["--replay", str(FIXTURE)])
    first = capsys.readouterr().out
    metrics.main(["--replay", str(FIXTURE)])
    second = capsys.readouterr().out
    assert first == second

    records = metrics.load_records(FIXTURE)
    assert metrics.format_report(metrics.aggregate(records)) == metrics.format_report(
        metrics.aggregate(records)
    )


def test_json_summary_is_byte_stable(capsys):
    metrics.main(["--replay", str(FIXTURE), "--json"])
    first = capsys.readouterr().out
    metrics.main(["--replay", str(FIXTURE), "--json"])
    second = capsys.readouterr().out
    assert first == second


# --------------------------------------------------------------------------- #
# unit: the load-bearing definitions
# --------------------------------------------------------------------------- #
def test_silent_failure_rule_only_counts_accepted_local_failures():
    # served-local + ground_truth fail -> silent failure.
    assert metrics.is_silent_failure(_served_local_record(ground_truth="fail")) is True
    # served-local + ground_truth pass -> not a silent failure.
    assert metrics.is_silent_failure(_served_local_record(ground_truth="pass")) is False
    # served-local + unaudited (null) -> not counted (only PROVEN failures count).
    assert metrics.is_silent_failure(_served_local_record(ground_truth=None)) is False


def test_cloud_served_failure_is_not_a_silent_failure():
    # A failure that went to cloud is not delivered-locally -> never silent.
    cloud_fail = {
        "work_class": "planning",
        "requested_tiers": ["cloud"],
        "attempts": [{"tier_id": "cloud", "verifier_passed": True,
                      "verify_reason": "verify passed", "prompt_tokens": 10,
                      "completion_tokens": 10, "outcome": "served", "detail": ""}],
        "served_tier": "cloud",
        "served_tier_privacy": "cloud",
        "total_prompt_tokens": 10,
        "total_completion_tokens": 10,
        "fell_back": False,
        "ground_truth": "fail",
    }
    assert metrics.served_locally(cloud_fail) is False
    assert metrics.is_silent_failure(cloud_fail) is False


def test_explicit_silent_failure_override_is_honored():
    rec = _served_local_record(ground_truth="pass")
    rec["silent_failure"] = True
    assert metrics.is_silent_failure(rec) is True


def test_exhausted_record_is_not_local_and_saves_nothing():
    exhausted = {
        "work_class": "bounded-edit",
        "requested_tiers": ["fast-local", "cloud"],
        "attempts": [
            {"tier_id": "fast-local", "verifier_passed": False, "verify_reason": "non_empty_content",
             "prompt_tokens": 30, "completion_tokens": 0, "outcome": "fallback", "detail": ""},
            {"tier_id": "cloud", "verifier_passed": False, "verify_reason": "backend error: RuntimeError",
             "prompt_tokens": 30, "completion_tokens": 0, "outcome": "error", "detail": ""},
        ],
        "served_tier": None,
        "served_tier_privacy": None,
        "total_prompt_tokens": 60,
        "total_completion_tokens": 0,
        "fell_back": True,
        "ground_truth": None,
    }
    assert metrics.served_locally(exhausted) is False
    summary = metrics.aggregate([exhausted])
    block = summary["work_classes"]["bounded-edit"]
    assert block["total"] == 1
    assert block["accepted_local"] == 0
    assert block["accept_rate"] == 0.0
    assert block["cloud_tokens_saved"] == 0


def test_served_local_record_missing_privacy_reads_non_local():
    rec = _served_local_record(ground_truth="fail")
    del rec["served_tier_privacy"]
    # Conservative: absent privacy on a served record is treated as non-local,
    # so it cannot inflate accept-rate/savings or become a silent failure.
    assert metrics.served_locally(rec) is False
    assert metrics.is_silent_failure(rec) is False


def test_null_work_class_buckets_as_unclassified():
    rec = _served_local_record(work_class=None)
    rec["work_class"] = None
    summary = metrics.aggregate([rec])
    assert metrics._UNCLASSIFIED in summary["work_classes"]


# --------------------------------------------------------------------------- #
# parser robustness
# --------------------------------------------------------------------------- #
def test_load_records_skips_blank_lines(tmp_path):
    path = tmp_path / "with_blanks.jsonl"
    path.write_text(
        json.dumps(_served_local_record()) + "\n\n   \n" + json.dumps(_served_local_record()) + "\n",
        encoding="utf-8",
    )
    assert len(metrics.load_records(path)) == 2


def test_load_records_rejects_malformed_line_with_lineno(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text(json.dumps(_served_local_record()) + "\n{not json}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r":2:"):
        metrics.load_records(path)


def test_main_reports_io_error_as_exit_2(capsys):
    rc = metrics.main(["--replay", str(FIXTURE.parent / "does-not-exist.jsonl")])
    assert rc == 2
    assert "error:" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# threshold bounds validation (Bug fix: values > 1.0 silently defeated the gate)
# --------------------------------------------------------------------------- #
def test_threshold_above_one_is_rejected(capsys):
    """--silent-failure-threshold 2.0 must be rejected before any replay is run."""
    with pytest.raises(SystemExit) as exc:
        metrics.main(["--replay", str(FIXTURE), "--silent-failure-threshold", "2.0"])
    assert exc.value.code != 0


def test_threshold_negative_is_rejected(capsys):
    """--silent-failure-threshold -0.1 must be rejected (negative rate is not a fraction)."""
    with pytest.raises(SystemExit) as exc:
        metrics.main(["--replay", str(FIXTURE), "--silent-failure-threshold", "-0.1"])
    assert exc.value.code != 0


def test_threshold_zero_point_zero_one_is_accepted(capsys):
    """A normal threshold of 0.01 (the default) must still be accepted and work."""
    rc = metrics.main(["--replay", str(FIXTURE), "--silent-failure-threshold", "0.01"])
    assert rc == 0   # committed fixture is below 1%
    assert "GATE: PASS" in capsys.readouterr().out


def test_threshold_type_raises_argument_type_error_for_out_of_range():
    """_threshold_type raises ArgumentTypeError for values outside [0, 1]."""
    import argparse
    with pytest.raises(argparse.ArgumentTypeError):
        metrics._threshold_type("1.5")
    with pytest.raises(argparse.ArgumentTypeError):
        metrics._threshold_type("-0.01")


def test_gate_still_trips_when_rate_exceeds_valid_threshold(tmp_path, capsys):
    """The gate must trip for a normal in-range threshold when the rate breaches it."""
    bad = [
        _served_local_record("chat", ground_truth="fail"),   # 1/2 = 50% silent-failure
        _served_local_record("chat", ground_truth="pass"),
    ]
    bad_fixture = _write_jsonl(tmp_path / "bad50.jsonl", bad)
    rc = metrics.main(["--replay", str(bad_fixture), "--silent-failure-threshold", "0.10"])
    out = capsys.readouterr().out
    assert rc == 1, "gate must exit 1 when the silent-failure rate breaches a valid threshold"
    assert "GATE: FAIL" in out


# --------------------------------------------------------------------------- #
# T003: cost_usd dimension in the metrics surface
# --------------------------------------------------------------------------- #
def _cloud_record(work_class="planning", cost_usd=0.0105):
    """A cloud-served record carrying a non-zero cost_usd (metered billing)."""
    return {
        "work_class": work_class,
        "intent": work_class,
        "requested_tiers": ["cloud"],
        "attempts": [
            {"tier_id": "cloud", "verifier_passed": True, "verify_reason": "verify passed",
             "prompt_tokens": 1000, "completion_tokens": 500, "outcome": "served", "detail": ""}
        ],
        "served_tier": "cloud",
        "served_tier_privacy": "cloud",
        "total_prompt_tokens": 1000,
        "total_completion_tokens": 500,
        "fell_back": False,
        "ground_truth": "pass",
        "cost_usd": cost_usd,
    }


def test_metrics_summary_includes_cost_usd():
    """The aggregate summary carries a cost_usd key in each per-class and overall block."""
    records = [_cloud_record("planning", cost_usd=0.0105)]
    summary = metrics.aggregate(records)
    assert "cost_usd" in summary["overall"], "overall block missing cost_usd"
    assert "cost_usd" in summary["work_classes"]["planning"], "per-class block missing cost_usd"


def test_metrics_cloud_record_cost_usd_nonzero():
    """A metered cloud-routed record with cost_usd set contributes to the aggregate."""
    # cost_usd = (3.0 * 1000 + 15.0 * 500) / 1e6 = 0.0105
    records = [_cloud_record("planning", cost_usd=0.0105)]
    summary = metrics.aggregate(records)
    assert summary["overall"]["cost_usd"] == pytest.approx(0.0105)
    assert summary["work_classes"]["planning"]["cost_usd"] == pytest.approx(0.0105)


def test_metrics_local_record_cost_usd_zero():
    """Local-only routes carry cost_usd == 0.0 and the aggregate reflects that."""
    records = [_served_local_record("chat")]   # local record, no cost_usd field
    summary = metrics.aggregate(records)
    assert summary["overall"]["cost_usd"] == pytest.approx(0.0)
    assert summary["work_classes"]["chat"]["cost_usd"] == pytest.approx(0.0)


def test_metrics_cost_usd_sums_across_multiple_cloud_requests():
    """cost_usd is summed over all cloud-served records in the window."""
    records = [
        _cloud_record("planning", cost_usd=0.0105),
        _cloud_record("planning", cost_usd=0.0210),
        _cloud_record("chat", cost_usd=0.0050),
    ]
    summary = metrics.aggregate(records)
    assert summary["overall"]["cost_usd"] == pytest.approx(0.0105 + 0.0210 + 0.0050)
    assert summary["work_classes"]["planning"]["cost_usd"] == pytest.approx(0.0105 + 0.0210)
    assert summary["work_classes"]["chat"]["cost_usd"] == pytest.approx(0.0050)


def test_metrics_cost_usd_mixed_local_and_cloud():
    """Local records contribute 0.0; only cloud records add to cost_usd."""
    records = [
        _served_local_record("chat"),                    # no cost_usd -> 0.0
        _cloud_record("planning", cost_usd=0.0105),     # metered
    ]
    summary = metrics.aggregate(records)
    assert summary["overall"]["cost_usd"] == pytest.approx(0.0105)
    assert summary["work_classes"]["chat"]["cost_usd"] == pytest.approx(0.0)
    assert summary["work_classes"]["planning"]["cost_usd"] == pytest.approx(0.0105)


def test_committed_fixture_cost_usd_present_in_summary(summary):
    """The committed fixture's aggregate summary always has cost_usd (may be 0.0
    since the fixture uses local tiers, but the key must be present)."""
    assert "cost_usd" in summary["overall"]
    for _wc, block in summary["work_classes"].items():
        assert "cost_usd" in block


# --------------------------------------------------------------------------- #
# T013: active serving mode label on the metrics surface (ADR-0011)
# --------------------------------------------------------------------------- #
def _mode_record(work_class="chat", *, mode="flexibility"):
    """A served-local record carrying an active serving ``mode`` label."""
    rec = _served_local_record(work_class)
    rec["mode"] = mode
    return rec


def test_metrics_summary_has_modes_label():
    """The aggregate summary always carries a top-level ``modes`` label (additive)."""
    summary = metrics.aggregate([_mode_record("chat", mode="flexibility")])
    assert summary["modes"] == ["flexibility"]


def test_metrics_modes_are_distinct_and_sorted():
    """Distinct non-empty mode labels are surfaced, sorted and de-duplicated."""
    records = [
        _mode_record("chat", mode="flexibility"),
        _mode_record("chat", mode="agentic"),
        _mode_record("review", mode="flexibility"),
    ]
    summary = metrics.aggregate(records)
    assert summary["modes"] == ["agentic", "flexibility"]


def test_metrics_modes_empty_when_no_record_carries_mode():
    """A window with no mode (a --config boot / pre-T013 fixture) yields modes=[]
    and leaves every existing metric unchanged (additive; None = unchanged)."""
    records = [_served_local_record("chat")]  # no mode field
    summary = metrics.aggregate(records)
    assert summary["modes"] == []
    # The existing metrics are untouched.
    assert summary["work_classes"]["chat"]["accept_rate"] == pytest.approx(1.0)


def test_committed_fixture_has_empty_modes(summary):
    """The pre-T013 committed fixture carries no mode -> modes == [] (no churn)."""
    assert summary["modes"] == []


def test_observed_modes_ignores_non_string_and_empty():
    """observed_modes ignores null / empty / non-string mode values."""
    records = [
        _mode_record("chat", mode="flexibility"),
        _mode_record("chat", mode=""),      # empty -> ignored
        _mode_record("chat", mode=None),    # null  -> ignored
        {"work_class": "chat", "mode": 7},  # non-string -> ignored
    ]
    assert metrics.observed_modes(records) == ["flexibility"]


def test_human_report_stamps_mode_when_present():
    """format_report surfaces the observed mode on the header WHEN present, and is
    byte-identical to pre-T013 when absent."""
    with_mode = metrics.format_report(
        metrics.aggregate([_mode_record("chat", mode="flexibility")])
    )
    assert "mode: flexibility" in with_mode

    without_mode = metrics.format_report(metrics.aggregate([_served_local_record("chat")]))
    assert "mode:" not in without_mode


def test_mode_label_survives_json_roundtrip(tmp_path, capsys):
    """A captured window's mode label rides through the --json machine summary."""
    fixture = _write_jsonl(tmp_path / "moded.jsonl", [_mode_record("chat", mode="agentic")])
    rc = metrics.main(["--replay", str(fixture), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["modes"] == ["agentic"]
