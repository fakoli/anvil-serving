"""Recompute the public Ignis 0.8 tool-choice qualification claims."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FINDING = ROOT / "docs/findings/2026-10-26-ignis-080-preflight.md"
BUNDLE = FINDING.with_name(FINDING.stem + "-evidence")


def read_json(name: str) -> dict:
    return json.loads((BUNDLE / name).read_text())


def test_phase_one_auto_miss_remains_unchanged():
    native = read_json("preflight-native.json")
    summary = read_json("summary.json")
    assert native["passed"] is False
    assert sum(result["passed"] for result in native["results"]) == 4
    assert len(native["results"]) == 5
    assert sum(observation["passed"] for observation in native["observations"]) == 8
    assert len(native["observations"]) == 9
    tools = [row for row in native["observations"] if row["test"] == "tools"]
    assert len(tools) == 4 and sum(row["passed"] for row in tools) == 3
    failed = [row for row in tools if not row["passed"]]
    assert len(failed) == 1
    assert failed[0]["finish_reason"] == "stop"
    assert failed[0]["validation_detail"] == "response did not include tool_calls"
    assert summary["phase_one"]["shared_prefix_tool_calls_passed"] == 3
    assert summary["phase_one"]["shared_prefix_tool_calls_total"] == 4
    assert "not a protocol violation" in summary["phase_one"]["outcome"]


def test_phase_two_required_path_and_request_accounting_are_exact():
    followup = read_json("tool-choice-followup.json")
    summary = read_json("summary.json")
    cells = followup["stage_a"]["cells"]
    assert followup["stage_a"]["passed"] and len(cells) == 3
    assert [cell["id"] for cell in cells] == [
        "auto-default",
        "required-default",
        "required-no-thinking",
    ]
    assert all(cell["passed"] and cell["http_status"] == 200 for cell in cells)
    assert followup["selection_rule"]["selected_tool_choice"] == "required"
    assert followup["selection_rule"]["selected_thinking"] == "default"
    assert followup["stage_b"]["named_checks_passed"] == 5
    assert followup["stage_b"]["named_checks_total"] == 5
    assert followup["stage_b"]["observations_passed"] == 9
    assert followup["stage_b"]["observations_total"] == 9
    assert followup["stage_c"]["observations_passed"] == 4
    assert followup["stage_c"]["observations_total"] == 4
    request_ids = [cell["terminal_engine_request_id"] for cell in cells]
    request_ids += followup["stage_b"]["terminal_engine_request_ids"]
    request_ids += followup["stage_c"]["terminal_engine_request_ids"]
    assert request_ids == list(range(1, 17))
    accounting = followup["accounting"]
    assert accounting["generation_requests"] == 16
    assert accounting["terminal_engine_requests"] == 16
    assert accounting["request_loss_observed"] is False
    assert accounting["captured_tool_request_http_statuses"]["count"] == 12
    assert accounting["captured_tool_request_http_statuses"]["status"] == 200
    assert accounting["all_sixteen_literal_http_200_supported"] is False
    assert summary["phase_two"]["generation_requests"] == 16
    assert summary["phase_two"]["captured_http_200_tool_requests"] == 12
    assert summary["phase_two"]["all_sixteen_literal_http_200_supported"] is False


def test_phase_two_is_request_policy_not_auto_reliability_or_performance():
    followup = read_json("tool-choice-followup.json")
    interpretation = followup["interpretation"]
    assert interpretation["supported_required_path_passed"]
    assert interpretation["normal_thinking_selected"]
    assert interpretation["phase_one_auto_result_changed"] is False
    assert interpretation["phase_two_seeded_auto_result"].startswith("1/1 diagnostic")
    assert interpretation["engine_patch_applied"] is False
    assert interpretation["engine_or_model_bug_established"] is False
    assert interpretation["performance_claim_supported"] is False
    assert interpretation["capacity_claim_supported"] is False
    assert interpretation["coding_or_swe_claim_supported"] is False
    assert interpretation["promotion_supported"] is False
    assert "not a global default" in followup["controls"]["required_policy_scope"]
    finding = " ".join(FINDING.read_text().split())
    assert "does not erase the prior unseeded 3/4 result" in finding
    assert "not a production-wide default" in finding
    assert "No engine patch was needed" in finding
    assert "no engine/model bug is claimed" in finding


def test_resource_and_restoration_boundaries_remain_distinct():
    resources = read_json("resource-summary.json")
    phase_two = resources["phase_two_candidate_cgroup"]
    assert phase_two["configured_ram_limit_bytes"] == 55834574848
    assert phase_two["configured_swap_limit_bytes"] == 0
    assert phase_two["peak_bytes"] == 44944740352
    assert phase_two["oom"] == phase_two["oom_kill"] == phase_two["oom_group_kill"] == 0
    assert resources["phase_two_after_probes"]["candidate_gpu_memory_mib"] == 96027
    assert resources["phase_two_after_probes"]["protected_second_gpu_memory_mib"] == 0
    assert resources["phase_two_minimum_candidate_mem_available_gib"] == 57.27
    assert "high-water mark" in resources["limitations"][0]

    restoration = read_json("restoration.json")
    phase_two_restoration = restoration["phase_two"]
    assert restoration["verified"] and phase_two_restoration["identity_fields_match"]
    assert phase_two_restoration["candidate_removed"]
    assert phase_two_restoration["router_custody_unchanged"]
    assert phase_two_restoration["router_readmitted"]
    assert phase_two_restoration["routed_smoke_passed"]
    assert phase_two_restoration["engine_idle_after_smoke"]
    assert set(phase_two_restoration["container_oom_events"].values()) == {0}
    assert phase_two_restoration["mounted_router_config_bytes"].startswith("not independently")
    assert "no full quality" in phase_two_restoration["quality_scope"]
    smoke = read_json("restoration-smoke-phase-two.json")
    assert smoke["passed"] and len(smoke["observations"]) == 1
    assert smoke["observations"][0]["validation_detail"] == "contains sum("
    review = read_json("independent-restoration-review.json")
    assert review["gate_state"]["operational_restoration_accepted"]
    assert review["gate_state"]["quality_qualification_inferred"] is False
    assert review["recommendation"]["decision"] == "pass"
    assert review["promoted"] is False


def test_public_projection_and_matrix_are_closed_and_public_safe():
    summary = read_json("summary.json")
    assert summary["complete"] and summary["completion_block"] is None
    assert summary["decision"]["evidence_labels"] == ["functional"]
    assert summary["decision"]["decision_labels"] == ["no-promotion"]
    assert summary["decision"]["promoted"] is False
    gates = {gate["gate"]: gate["status"] for gate in summary["gates"]}
    assert gates["phase_one_auto_all_four"] == "failed"
    assert gates["phase_two_required_mechanism"] == "passed"
    assert gates["phase_two_selected_full_preflight"] == "passed"
    assert gates["phase_two_selected_seed_repeat"] == "passed"
    assert gates["phase_two_operational_restoration"] == "passed"
    assert gates["coding_campaign"] == "not-run"

    expected_link = "2026-10-26-ignis-080-preflight"
    for path in (
        ROOT / "docs/findings/README.md",
        ROOT / "docs/benchmarks/runs.md",
        ROOT / "docs/benchmarks/models/qwen38-flash-next.md",
        ROOT / "docs/benchmarks/hardware/rtx-pro-6000.md",
    ):
        assert expected_link in path.read_text()
    methodology = (ROOT / "docs/benchmarks/methodology.md").read_text()
    assert "tool-call/v2-exact-weather" in methodology
    assert "Historical findings keep the validator and policy they actually ran." in methodology

    forbidden = (
        "/data/",
        "/home/",
        "localhost",
        "operator-home",
        "anvil-serving-ops-private",
        "GPU-d0f",
        "GPU-f0c",
        "39168",
    )
    public_text = FINDING.read_text() + "\n" + "\n".join(
        path.read_text() for path in BUNDLE.iterdir() if path.is_file()
    )
    for marker in forbidden:
        assert marker not in public_text
