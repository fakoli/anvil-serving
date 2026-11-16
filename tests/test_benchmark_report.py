import hashlib
import json
from pathlib import Path

import pytest

from anvil_serving.benchmarking.report import build_report, main, render_markdown


def fixture(tmp_path):
    (tmp_path / "docs/benchmarks").mkdir(parents=True)
    raw = {"schema": "anvil-serving.benchmark/v1", "model": "served-model", "engine": "sglang", "gpu": "test GPU", "measurement_protocol": "capacity-v3", "prompt_cache_mode": "unique", "request_canaries": True, "requests": 100, "completed": 99,
           "failed": 1, "concurrency": 8, "context_tokens": 4096,
           "max_context_tokens": 262144, "max_tokens": 512,
           "metrics": {"ttft_p50_ms": 123.45}, "secret_prompt": "NEVER PUBLISH"}
    (tmp_path / "docs/raw.json").write_text(json.dumps(raw))
    (tmp_path / "recipe.toml").write_text('[[recipe]]\nmodel = "test-recipe"\n[recipe.serve]\nserved_model_name="served-model"\nengine="sglang"\ncontext_tokens=262144\n')
    (tmp_path / "docs/finding.md").write_text("# finding")
    entry = {"id": "example", "title": "Model <script>", "model": "owner/model",
             "hardware": "GPU", "topology": "TP1", "workload": "C8 controlled output",
             "decision": "no-promotion", "strengths": ["Useful <bound>"], "limitations": ["Unqualified"],
             "recipe": {"path": "recipe.toml", "label": "Recipe"},
             "finding": {"path": "docs/finding.md", "label": "Finding"},
             "dossier": {"path": "docs/finding.md", "label": "Dossier"},
             "artifact": "docs/raw.json",
             "metrics": [{"label": "TTFT P50", "path": "metrics.ttft_p50_ms", "unit": "ms", "precision": 1}]}
    from anvil_serving.benchmarking.report import IDENTITY_FIELDS
    entry["expected"] = {key: raw.get(key) for key in IDENTITY_FIELDS}
    catalog = {"schema": "anvil-serving.benchmark-recipe-catalog/v1", "source_base_url": "https://github.com/example/repo/blob/" + "a" * 40 + "/", "title": "Recipes", "reviewed_at": "2026-09-05", "entries": [entry]}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    return path, catalog


def test_committed_recipe_report_is_current():
    root = Path(__file__).resolve().parents[1]
    catalog = root / "docs/benchmarks/recipe-catalog.json"
    output = root / "docs/benchmarks/recipe-results.md"
    assert output.read_text(encoding="utf-8") == render_markdown(build_report(catalog, root), output, root)


def test_unrelated_document_cannot_be_clobbered_even_with_confirmation(tmp_path):
    path, _ = fixture(tmp_path)
    target = tmp_path / "README.md"
    target.write_text("User-owned document")
    assert main([str(path), "--root", str(tmp_path), "--output", str(target), "--confirm"]) == 1
    assert target.read_text() == "User-owned document"


def test_actual_cli_forwards_catalog_and_confirmation(tmp_path):
    from anvil_serving.cli import main as cli_main
    path, _ = fixture(tmp_path)
    output = tmp_path / "docs/benchmarks/recipe-results.md"
    args = ["eval", "benchmark", "report", str(path), "--root", str(tmp_path), "--output", str(output)]
    assert cli_main(args + ["--confirm"]) == 0
    assert cli_main(args + ["--check"]) == 0


class CampaignStore:
    """Small durable-artifact fixture; no inference or external processes."""
    def __init__(self, root):
        self.run_root = str(root)
        root.mkdir()
        self.records, self.artifacts = {}, {}

    def status(self, run_id):
        return self.records.get(run_id)

    def artifact(self, run_id):
        return self.artifacts.get(run_id)


def campaign_fixture(tmp_path, *, repeats=1, suite="agentic"):
    from anvil_serving.benchmarking.jobs import JOB_SPEC_SCHEMA, job_spec_sha256
    store = CampaignStore(tmp_path / "campaign-runs")
    manifest = {"campaign_id": "private-campaign", "ownership_id": "owner",
                "manifest_sha256": "a" * 64,
                "arms": [{"arm_id": "private-left", "identity": {"model": "private-model", "label": "private-person"}},
                         {"arm_id": "private-right", "identity": {"model": "different-model"}}], "cells": []}
    for arm in manifest["arms"]:
        for repeat in range(1, repeats + 1):
            run_id = f"{arm['arm_id']}-{repeat}"
            spec = {"schema": JOB_SPEC_SCHEMA, "ownership_id": "owner", "run_id": run_id,
                    "suite": suite, "profile": "pilot-v2", "submitted_at": "2026-10-24T12:00:00Z",
                    "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "private-model"},
                    "worker": {"id": "private-worker"}, "timeout_s": 30,
                    "parameters": {"case_ids": ["tool-sequence"]}}
            manifest["cells"].append({"cell_id": run_id, "arm_id": arm["arm_id"], "batch_id": run_id,
                "task_id": "tool-sequence", "repeat": repeat, "concurrency": 4, "cache_condition": "cold",
                "spec": spec, "spec_sha256": job_spec_sha256(spec),
                "matching": {key: "b" * 64 for key in ("workload_sha256", "controls_sha256", "oracle_sha256", "cache_sha256")}})
    return manifest, store


def campaign_result(manifest, store, index, *, passed=True, state="completed", native=None, timing=None,
                    activation=None, telemetry_coverage=None):
    from anvil_serving.benchmarking.artifacts import evidence_file_reference
    from anvil_serving.benchmarking.campaign_execution import bind_campaign_child
    from anvil_serving.benchmarking.jobs import job_spec_sha256
    cell = manifest["cells"][index]
    spec = bind_campaign_child(cell["spec"], manifest_sha256=manifest["manifest_sha256"],
        campaign_id=manifest["campaign_id"], cell_id=cell["cell_id"], arm_id=cell["arm_id"], batch_id=cell["batch_id"])
    run_id = spec["run_id"]
    root = Path(store.run_root) / spec["ownership_id"] / run_id
    root.mkdir(parents=True)
    store.records[run_id] = {"spec": spec, "state": state, "started_at": "2026-10-24T12:00:01Z", "spec_sha256": job_spec_sha256(spec)}
    if native is None:
        native = {"schema": "anvil-serving.agentic-suite-run/v1", "observations": [
            {"scenario_id": cell["task_id"], "passed": passed, "failure_class": None,
             "visible_answer": "PRIVATE PROMPT", "turns": [{"latency_ms": 8}]}]}
    stage = root / "native.json"
    stage.write_text(json.dumps(native))
    store.artifacts[run_id] = {"run": {"spec_sha256": job_spec_sha256(spec)},
        "results": {"evidence": {"stages": [{"name": spec["suite"], "evidence": [evidence_file_reference(str(stage), root=str(root))]}]}}}
    if timing:
        receipt = {"schema": "anvil-serving.campaign-execution/v1", "campaign": spec["campaign"],
                   "spec_sha256": job_spec_sha256(spec), "boot_id": "private-boot", "status": "closed",
                   "started_monotonic": timing[0], "closed_monotonic": timing[1], "clients_stopped": True,
                   "drain": {"status": "verified"}, "telemetry_coverage": telemetry_coverage or {}}
        if activation is not None:
            receipt["activation"] = activation
        (root / "campaign-execution.json").write_text(json.dumps(receipt))
        store.artifacts[run_id]["results"]["campaign_execution"] = evidence_file_reference(str(root / "campaign-execution.json"), root=str(root))
    return root


def cancelled_aider_retention(manifest, store, *, change=None):
    from anvil_serving.benchmarking import aider
    from anvil_serving.benchmarking.artifacts import evidence_file_reference
    from anvil_serving.benchmarking.campaign_execution import bind_campaign_child
    from anvil_serving.benchmarking.jobs import canonical_json_bytes, job_spec_sha256
    from anvil_serving.benchmarking.swe_agent_adapter import EVENT_SCHEMA

    cell = manifest["cells"][0]
    cell["spec"]["parameters"] = {"instance_ids": [cell["task_id"]]}
    spec = bind_campaign_child(cell["spec"], manifest_sha256=manifest["manifest_sha256"],
        campaign_id=manifest["campaign_id"], cell_id=cell["cell_id"], arm_id=cell["arm_id"], batch_id=cell["batch_id"])
    root = Path(store.run_root) / spec["ownership_id"] / spec["run_id"]
    native_path = root / "aider/results/tool-sequence/.aider.results.json"
    selected_task = "other-task" if change == "swapped_task" else cell["task_id"]
    selection = {"instance_ids": [selected_task], "sha256": aider._digest([selected_task])}
    plan = {"schema": aider.AIDER_PLAN_SCHEMA, "ownership_id": spec["ownership_id"], "run_id": spec["run_id"],
            "profile": spec["profile"], "endpoint": {**spec["endpoint"], "model": "other-model" if change == "swapped_model" else spec["endpoint"]["model"]},
            "selection": selection}
    plan["plan_sha256"] = aider._digest(plan)
    cell["prepared"] = {"plan_sha256": hashlib.sha256(canonical_json_bytes(plan)).hexdigest()}
    paths = {"aider/plan.json": plan, "aider/selection.json": selection}
    if change != "missing":
        paths["aider/results/tool-sequence/.aider.results.json"] = {
            "model": "cloud" if change == "model" else "openai/" + plan["endpoint"]["model"],
            "edit_format": "whole", "tests_outcomes": [True] if change == "pass" else [False, False],
        }
        if change == "signals":
            paths["aider/results/tool-sequence/.aider.results.json"]["num_exhausted_context_windows"] = 2
    for relative, value in paths.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(("{\"nested\": " * 1100) + "null" + ("}" * 1100) if change == "nested" and path == native_path else json.dumps(value))
    if change == "selection":
        (root / "aider/selection.json").write_text(json.dumps({"instance_ids": [], "sha256": aider._digest([])}))
    artifacts = [evidence_file_reference(str(root / relative), root=str(root)) for relative in paths]
    event_fields = ({"event": "aider_transport_response", "output_truncated": True},
                    {"event": "aider_transport_response", "output_truncated": None},
                    {"event": "aider_contained_command_finished", "compilation_failure": True},
                    {"event": "aider_contained_command_finished", "compilation_failure": None}) if change in {"signals", "missing_requests"} else (
                   ({"event": "aider_transport_response"},) if change == "missing_metadata" else (
                   ({"event": "aider_transport_response", "output_truncated": "yes"},) if change == "invalid_metadata" else ()))
    events = tuple({"schema": EVENT_SCHEMA, "seq": index, "utc_observed_at": "2026-10-24T12:00:00.000000Z", **fields}
                   for index, fields in enumerate(event_fields, 1))
    if events:
        requests_path = root / "aider/requests.jsonl"
        requests_path.write_text("".join(json.dumps(event) + "\n" for event in events))
        artifacts.append(evidence_file_reference(str(requests_path), root=str(root)))
    if change == "accounted_deadline":
        from anvil_serving.benchmarking.swe_agent_adapter import EventRecorder
        requests_path = root / "aider/requests.jsonl"
        recorder = EventRecorder(requests_path)
        recorder.record("aider_adapter_ready", request_accounting_schema=aider.AIDER_REQUEST_ACCOUNTING_SCHEMA)
        recorder.record("aider_transport_started", logical_request_id="logical-00000001", attempt=1,
                        model_call=1)
        recorder.record("aider_transport_error", logical_request_id="logical-00000001", attempt=1,
                        error_type="timeout", http_status=None, finish_reason=None,
                        output_truncated=None, usage=dict.fromkeys(aider._AIDER_USAGE_FIELDS))
        accounting = {"schema": aider.AIDER_REQUEST_ACCOUNTING_SCHEMA, "status": "incomplete",
                      "started": 1, "responses": 0, "errors": 1, "pending": 0}
        recorder.close(outcome="budget_exhausted", request_accounting=accounting)
        adapter_result_path = root / "aider/adapter-result.json"
        adapter_result_path.write_text(json.dumps({
            "schema": "anvil-serving.aider-adapter-result/v1", "outcome": "budget_exhausted",
            "request_accounting": accounting, "events_complete": False}))
        artifacts.extend(evidence_file_reference(str(path), root=str(root))
                         for path in (requests_path, adapter_result_path))
    if change == "forged_hash":
        artifacts[-1]["sha256"] = "0" * 64
    retention_manifest = {"schema": "anvil-serving.aider-retention/v1", "state": "incomplete",
                          "artifacts": artifacts, "missing": (["results/tool-sequence/.aider.results.json"] if change == "missing_native" else
                                      ["requests.jsonl"] if change == "missing_requests" else [])}
    retention_path = root / "aider/retention.json"
    retention_path.write_text(json.dumps(retention_manifest))
    retention = {**retention_manifest, "manifest_reference": evidence_file_reference(str(retention_path), root=str(root))}
    cancellation_path = root / "evidence/cancellation-aider.json"
    cancellation_path.parent.mkdir(exist_ok=True)
    cancellation_path.write_text(json.dumps({"schema": "anvil-serving.aider-cancellation/v1", "retention": retention}))
    if change == "symlink":
        outside = root / "outside.json"
        outside.write_text(native_path.read_text())
        native_path.unlink()
        native_path.symlink_to(outside)
    store.records[spec["run_id"]] = {"spec": spec, "state": "cancelled", "started_at": "2026-10-24T12:00:01Z",
                                      "spec_sha256": job_spec_sha256(spec)}
    store.artifacts[spec["run_id"]] = {"run": {"spec_sha256": job_spec_sha256(spec)}, "results": {"evidence": {
        "stages": [{"name": "aider_retention", "evidence": [evidence_file_reference(str(cancellation_path), root=str(root))]}]}}}
    return plan


@pytest.mark.parametrize(("change", "expected"), [
    ("pass", [{"first_attempt_passed": True, "second_attempt_passed": None, "resolved": True}]),
    ("fail", [{"first_attempt_passed": False, "second_attempt_passed": False, "resolved": False}]),
    ("missing", None),
    ("forged_hash", None),
    ("model", None),
    ("selection", None),
    ("symlink", None),
    ("swapped_model", None),
    ("swapped_task", None),
    ("nested", None),
    ("missing_native", None),
])
def test_campaign_cancelled_aider_retention_projects_only_hash_bound_raw_grades(tmp_path, change, expected):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    manifest, store = campaign_fixture(tmp_path, suite="aider")
    cancelled_aider_retention(manifest, store, change=change)
    row = report_campaign(manifest, store)["cells"][0]
    assert row["raw_task_grades"] == expected
    assert row["outcomes"] is None
    assert row["samples"] == {"planned": 1, "submitted": 1, "attempted": None,
                              "scored": None, "failed": None, "cancelled": None, "unfinished": 0}
    assert row["measurement_complete"] is False


def test_campaign_retained_plan_binds_full_prepared_digest_not_internal_digest(tmp_path):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    from anvil_serving.benchmarking.jobs import canonical_json_bytes
    manifest, store = campaign_fixture(tmp_path, suite="aider")
    plan = cancelled_aider_retention(manifest, store, change="pass")
    assert plan["plan_sha256"] != hashlib.sha256(canonical_json_bytes(plan)).hexdigest()
    assert report_campaign(manifest, store)["cells"][0]["raw_task_grades"]


@pytest.mark.parametrize(("change", "expected"), [
    ("signals", {"output_truncated": True, "compilation_failure": True, "coverage": "partial", "num_exhausted_context_windows": 2}),
    ("missing_metadata", {"output_truncated": None, "compilation_failure": None, "coverage": "partial", "num_exhausted_context_windows": None}),
    ("invalid_metadata", None),
    ("missing_requests", None),
])
def test_campaign_cancelled_aider_diagnostics_are_allowlisted_and_independent(tmp_path, change, expected):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    manifest, store = campaign_fixture(tmp_path, suite="aider")
    cancelled_aider_retention(manifest, store, change=change)
    row = report_campaign(manifest, store)["cells"][0]
    assert row["raw_task_grade_diagnostics"] == expected
    assert row["raw_task_grades"] == [{"first_attempt_passed": False, "second_attempt_passed": False, "resolved": False}]
    assert row["state"] == "cancelled"


def test_campaign_retains_producer_shaped_deadline_diagnostics_and_independent_grade(tmp_path):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    manifest, store = campaign_fixture(tmp_path, suite="aider")
    cancelled_aider_retention(manifest, store, change="accounted_deadline")
    row = report_campaign(manifest, store)["cells"][0]
    assert row["raw_task_grade_diagnostics"] == {
        "output_truncated": None, "compilation_failure": None,
        "coverage": "partial", "num_exhausted_context_windows": None}
    assert row["raw_task_grades"] == [
        {"first_attempt_passed": False, "second_attempt_passed": False, "resolved": False}]
    assert row["state"] == "cancelled"


@pytest.mark.parametrize("change", ["hash", "wrong_sample", "duplicate_sample", "spec", "escape"])
def test_campaign_damaged_or_mismatched_artifact_never_scores(tmp_path, change):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    manifest, store = campaign_fixture(tmp_path)
    native = {"schema": "anvil-serving.agentic-suite-run/v1", "observations": [{"scenario_id": "tool-sequence", "passed": True}]}
    if change == "wrong_sample":
        native["observations"][0]["scenario_id"] = "other-task"
    if change == "duplicate_sample":
        native["observations"] *= 2
    root = campaign_result(manifest, store, 0, native=native)
    run = manifest["cells"][0]["spec"]["run_id"]
    if change == "hash":
        (root / "native.json").write_text("{}")
    if change == "spec":
        store.records[run]["spec_sha256"] = "f" * 64
    if change == "escape":
        store.artifacts[run]["results"]["evidence"]["stages"][0]["evidence"][0]["path"] = "../outside.json"
    row = report_campaign(manifest, store)["cells"][0]
    assert row["samples"]["scored"] is None
    assert row["measurement_complete"] is False


def test_campaign_performance_preserves_native_unknowns_without_synthetic_requests(tmp_path):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    manifest, store = campaign_fixture(tmp_path, suite="performance")
    for cell in manifest["cells"]:
        cell["spec"]["parameters"] = {"request_count": 3, "cache_condition": "cold"}
    native = {"schema": "anvil-serving.performance-run/v1", "summary": {"native_complete": True,
        "metrics": {"request_count": {"status": "measured", "value": 3, "unit": "requests"},
                    "time_to_first_token": {"status": "unknown", "value": 0, "unit": "ms"}}}}
    campaign_result(manifest, store, 0, native=native)
    row = report_campaign(manifest, store)["cells"][0]
    assert row["samples"]["planned"] == 3
    assert row["samples"]["attempted"] == 3
    assert row["samples"]["scored"] is None
    assert row["native_aggregate_metrics"]["time_to_first_token"]["value"] is None
    assert row["native_aggregate_metrics"]["error_request_count"]["value"] is None
    assert row["outcomes"] is None
    assert row["cache"] == {"requested": "cold", "proven": None}
