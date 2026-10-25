import copy
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


def test_report_uses_raw_metrics_and_keeps_failures_provenance_and_escaping(tmp_path):
    path, _ = fixture(tmp_path)
    report = build_report(path, tmp_path)
    rendered = render_markdown(report, tmp_path / "docs/benchmarks/recipe-results.md", tmp_path)
    assert "123.5 ms" in rendered
    assert "n=99/100; failed=1" in rendered
    assert "diagnostic only" in rendered
    assert "Model &lt;script&gt;" in rendered
    assert "NEVER PUBLISH" not in json.dumps(report) + rendered
    assert hashlib.sha256((tmp_path / "docs/raw.json").read_bytes()).hexdigest() in rendered
    assert '[Raw measurements](../raw.json)' in rendered
    assert rendered == render_markdown(build_report(path, tmp_path), tmp_path / "docs/benchmarks/recipe-results.md", tmp_path)


@pytest.mark.parametrize("change", ["escape", "missing_metric", "bool", "infinite", "duplicate", "scheme", "missing_recipe"])
def test_report_rejects_invalid_evidence_and_paths(tmp_path, change):
    path, catalog = fixture(tmp_path)
    row = catalog["entries"][0]
    if change == "escape":
        row["artifact"] = "../outside.json"
    elif change == "missing_metric":
        row["metrics"][0]["path"] = "metrics.missing"
    elif change in {"bool", "infinite"}:
        artifact = tmp_path / "docs/raw.json"
        raw = json.loads(artifact.read_text())
        raw["metrics"]["ttft_p50_ms"] = True if change == "bool" else float("inf")
        artifact.write_text(json.dumps(raw))
    elif change == "duplicate":
        catalog["entries"].append(copy.deepcopy(row))
    elif change == "scheme":
        catalog["source_base_url"] = "javascript:alert(1)"
    else:
        row["recipe"]["path"] = "missing.toml"
    path.write_text(json.dumps(catalog))
    with pytest.raises(ValueError):
        build_report(path, tmp_path)


def test_report_write_is_explicit_and_check_detects_drift_without_mutation(tmp_path):
    path, _ = fixture(tmp_path)
    output = tmp_path / "docs/benchmarks/recipe-results.md"
    args = [str(path), "--root", str(tmp_path), "--output", str(output)]
    assert main(args) == 1
    assert not output.exists()
    assert main(args + ["--confirm"]) == 0
    assert main(args + ["--check"]) == 0
    original = output.read_bytes()
    artifact = tmp_path / "docs/raw.json"
    raw = json.loads(artifact.read_text())
    raw["metrics"]["ttft_p50_ms"] = 321
    artifact.write_text(json.dumps(raw))
    assert main(args + ["--check"]) == 1
    assert output.read_bytes() == original
    assert main([str(path), "--root", str(tmp_path), "--output", str(path), "--confirm"]) == 1


def test_committed_recipe_report_is_current():
    root = Path(__file__).resolve().parents[1]
    catalog = root / "docs/benchmarks/recipe-catalog.json"
    output = root / "docs/benchmarks/recipe-results.md"
    assert output.read_text(encoding="utf-8") == render_markdown(build_report(catalog, root), output, root)


@pytest.mark.parametrize("change", ["identity", "counts", "unit", "negative_tokens", "boolean_context", "newline", "mutable_recipe", "ambiguous_recipe"])
def test_review_regressions_fail_closed(tmp_path, change):
    path, catalog = fixture(tmp_path)
    artifact = tmp_path / "docs/raw.json"
    raw = json.loads(artifact.read_text())
    entry = catalog["entries"][0]
    if change == "identity":
        raw["model"] = "another-model"
    elif change == "counts":
        raw["completed"] = 101
    elif change == "unit":
        entry["metrics"][0]["unit"] = "tok/s"
    elif change == "negative_tokens":
        raw["metrics"]["prompt_tokens_min"] = -1
    elif change == "boolean_context":
        raw["max_context_tokens"] = True
    elif change == "newline":
        catalog["title"] = "Recipes\n![injected](https://example.com/image)"
    elif change == "mutable_recipe":
        catalog["source_base_url"] = "https://github.com/example/repo/blob/main/"
    else:
        (tmp_path / "recipe.toml").write_text('[[recipe]]\nmodel="a"\n[[recipe]]\nmodel="b"\n')
    path.write_text(json.dumps(catalog))
    artifact.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        build_report(path, tmp_path)


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


@pytest.mark.parametrize("change", ["negative", "quantiles", "mean", "eligible_string", "eligible_zero", "aggregate", "recipe_model", "recipe_engine", "recipe_context"])
def test_adversarial_identity_and_distribution_regressions(tmp_path, change):
    path, _ = fixture(tmp_path)
    artifact = tmp_path / "docs/raw.json"
    raw = json.loads(artifact.read_text())
    if change == "negative":
        raw["metrics"]["ttft_p50_ms"] = -1
    elif change == "quantiles":
        raw["metrics"]["ttft_p99_ms"] = 1
    elif change == "mean":
        raw["metrics"].update(ttft_mean_ms=200, ttft_max_ms=150)
    elif change.startswith("eligible"):
        raw["performance_eligible"] = "false" if change == "eligible_string" else 0
    elif change == "aggregate":
        raw["schema"] = "anvil-serving.capacity-aggregate/v1"
    else:
        recipe = tmp_path / "recipe.toml"
        old, new = {"recipe_model": ('"served-model"', '"other"'), "recipe_engine": ('"sglang"', '"other"'), "recipe_context": ("262144", "8192")}[change]
        recipe.write_text(recipe.read_text().replace(old, new))
    artifact.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        build_report(path, tmp_path)


def test_editorial_labels_cannot_replace_native_identity_or_workload(tmp_path):
    path, catalog = fixture(tmp_path)
    catalog["entries"][0].update(model="fake model", hardware="fake GPU", workload="fake workload")
    path.write_text(json.dumps(catalog))
    row = build_report(path, tmp_path)["entries"][0]
    assert row["model"] == "served-model"
    assert row["hardware"] == "test GPU"
    assert "4096 tokens; C8; 100 requests" in row["workload"]


@pytest.mark.parametrize("field", ["response_words", "controlled_output_policy", "shared_prefix_tokens"])
def test_output_workload_controls_are_bound(tmp_path, field):
    path, _ = fixture(tmp_path)
    artifact = tmp_path / "docs/raw.json"
    raw = json.loads(artifact.read_text())
    raw[field] = "strict" if field == "controlled_output_policy" else 100
    artifact.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="identity/workload mismatch"):
        build_report(path, tmp_path)


def test_unique_prefix_namespace_is_validated_and_bound_when_present(tmp_path):
    path, catalog = fixture(tmp_path)
    artifact = tmp_path / "docs/raw.json"
    raw = json.loads(artifact.read_text())
    raw["unique_prefix_namespace"] = "capacity-v1:seed-request-index"
    artifact.write_text(json.dumps(raw))

    with pytest.raises(
        ValueError, match="identity/workload mismatch: unique_prefix_namespace"
    ):
        build_report(path, tmp_path)

    catalog["entries"][0]["expected"]["unique_prefix_namespace"] = raw[
        "unique_prefix_namespace"
    ]
    path.write_text(json.dumps(catalog))
    assert build_report(path, tmp_path)["entries"]


@pytest.mark.parametrize(
    ("mode", "namespace", "error"),
    [
        ("unique", None, "unique prompt_cache_mode requires"),
        ("unique", "seed-7", "unique prompt_cache_mode requires"),
        ("shared", "capacity-v1:seed-request-index", "shared prompt_cache_mode requires"),
        ("other", None, "unique_prefix_namespace requires"),
    ],
)
def test_unique_prefix_namespace_rejects_malformed_or_unknown_values(
    tmp_path, mode, namespace, error
):
    path, _ = fixture(tmp_path)
    artifact = tmp_path / "docs/raw.json"
    raw = json.loads(artifact.read_text())
    raw["prompt_cache_mode"] = mode
    raw["unique_prefix_namespace"] = namespace
    artifact.write_text(json.dumps(raw))

    with pytest.raises(ValueError, match=error):
        build_report(path, tmp_path)


def test_declared_shared_namespace_preserves_legacy_catalog_contract(tmp_path):
    path, catalog = fixture(tmp_path)
    artifact = tmp_path / "docs/raw.json"
    raw = json.loads(artifact.read_text())
    raw["prompt_cache_mode"] = "shared"
    raw["unique_prefix_namespace"] = None
    artifact.write_text(json.dumps(raw))
    catalog["entries"][0]["expected"]["prompt_cache_mode"] = "shared"
    path.write_text(json.dumps(catalog))

    report = build_report(path, tmp_path)
    assert report["entries"][0]["conditions"]["unique_prefix_namespace"] is None


def test_sampling_controls_are_bound_validated_and_rendered(tmp_path):
    path, catalog = fixture(tmp_path)
    artifact = tmp_path / "docs/raw.json"
    raw = json.loads(artifact.read_text())
    raw["sampling"] = {
        "temperature": {"requested": 1.0, "effective_request": 1.0, "sent": True},
        "top_p": {"requested": 0.95, "effective_request": 0.95, "sent": True},
    }
    artifact.write_text(json.dumps(raw))
    catalog["entries"][0]["expected"].update({
        "sampling_recorded": True,
        "sampling_temperature_requested": 1.0,
        "sampling_temperature_effective_request": 1.0,
        "sampling_temperature_sent": True,
        "sampling_top_p_requested": 0.95,
        "sampling_top_p_effective_request": 0.95,
        "sampling_top_p_sent": True,
    })
    path.write_text(json.dumps(catalog))

    report = build_report(path, tmp_path)
    assert report["entries"][0]["conditions"]["sampling_top_p_effective_request"] == 0.95
    rendered = render_markdown(report, tmp_path / "result.md", tmp_path)
    assert "sampling temperature=1.0 (requested=1.0); top_p=0.95" in rendered

    raw["sampling"]["top_p"].update(requested=0.9, effective_request=0.9)
    artifact.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="identity/workload mismatch: sampling_top_p_requested"):
        build_report(path, tmp_path)


@pytest.mark.parametrize("value", ['"bad"', '[]'])
def test_malformed_recipe_serve_returns_clean_cli_error(tmp_path, value):
    path, _ = fixture(tmp_path)
    (tmp_path / "recipe.toml").write_text(f'[[recipe]]\nmodel="test-recipe"\nserve={value}\n')
    assert main([str(path), "--root", str(tmp_path)]) == 1


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
    from anvil_serving.benchmarking.jobs import job_spec_sha256

    cell = manifest["cells"][0]
    spec = bind_campaign_child(cell["spec"], manifest_sha256=manifest["manifest_sha256"],
        campaign_id=manifest["campaign_id"], cell_id=cell["cell_id"], arm_id=cell["arm_id"], batch_id=cell["batch_id"])
    root = Path(store.run_root) / spec["ownership_id"] / spec["run_id"]
    native_path = root / "aider/results/tool-sequence/.aider.results.json"
    selection = {"instance_ids": [cell["task_id"]], "sha256": aider._digest([cell["task_id"]])}
    plan = {"schema": aider.AIDER_PLAN_SCHEMA, "ownership_id": spec["ownership_id"], "run_id": spec["run_id"],
            "profile": spec["profile"], "endpoint": {"model": "llm.primary"}, "selection": selection}
    plan["plan_sha256"] = aider._digest(plan)
    paths = {"aider/plan.json": plan, "aider/selection.json": selection}
    if change != "missing":
        paths["aider/results/tool-sequence/.aider.results.json"] = {
            "model": "cloud" if change == "model" else "openai/llm.primary",
            "edit_format": "whole", "tests_outcomes": [True] if change == "pass" else [False, False],
        }
    for relative, value in paths.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    if change == "selection":
        (root / "aider/selection.json").write_text(json.dumps({"instance_ids": [], "sha256": aider._digest([])}))
    artifacts = [evidence_file_reference(str(root / relative), root=str(root)) for relative in paths]
    if change == "forged_hash":
        artifacts[-1]["sha256"] = "0" * 64
    retention_manifest = {"schema": "anvil-serving.aider-retention/v1", "state": "incomplete",
                          "artifacts": artifacts, "missing": []}
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


@pytest.mark.parametrize(("change", "expected"), [
    ("pass", [{"first_attempt_passed": True, "second_attempt_passed": None, "resolved": True}]),
    ("fail", [{"first_attempt_passed": False, "second_attempt_passed": False, "resolved": False}]),
    ("missing", None),
    ("forged_hash", None),
    ("model", None),
    ("selection", None),
    ("symlink", None),
])
def test_campaign_cancelled_aider_retention_projects_only_hash_bound_raw_grades(tmp_path, change, expected):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    manifest, store = campaign_fixture(tmp_path, suite="aider")
    cancelled_aider_retention(manifest, store, change=change)
    row = report_campaign(manifest, store)["cells"][0]
    assert row["raw_task_grades"] == expected
    assert row["outcomes"] is None
    assert row["measurement_complete"] is False


def test_campaign_partial_denominators_and_scored_negative_are_separate(tmp_path):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    manifest, store = campaign_fixture(tmp_path, repeats=2)
    campaign_result(manifest, store, 0, passed=False)
    campaign_result(manifest, store, 1)
    run = manifest["cells"][1]["spec"]["run_id"]
    store.artifacts.pop(run)  # Completed durable job, no scoring evidence.
    campaign_result(manifest, store, 2, state="cancelled", native={"schema": "anvil-serving.agentic-suite-run/v1", "observations": []})
    report = report_campaign(manifest, store)
    assert report["jobs"]["planned"]["count"] == 4
    assert report["arms"][0]["jobs"]["planned"]["count"] == 2
    assert report["arms"][0]["samples"]["scored"]["unknown_cells"] == 1
    assert report["jobs"]["completed"]["count"] == 2
    assert report["jobs"]["scored"] == {"count": None, "known_count": 1, "unknown_cells": 1}
    assert report["samples"]["scored"]["known_count"] == 1
    assert report["samples"]["failed"]["known_count"] == 1
    assert report["samples"]["cancelled"]["count"] == 1
    assert report["jobs"]["unfinished"]["count"] == 1
    assert report["comparisons"][0]["complete"] is False
    assert len(report["comparisons"][0]["matched"]) == 2
    assert report["cells"][0]["outcomes"][0]["passed"] is False


def test_campaign_controls_mismatch_excludes_both_arms_without_survivor_selection(tmp_path):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    manifest, store = campaign_fixture(tmp_path)
    manifest["cells"][1]["matching"]["controls_sha256"] = "c" * 64
    campaign_result(manifest, store, 0)
    campaign_result(manifest, store, 1)
    comparison = report_campaign(manifest, store)["comparisons"][0]
    assert comparison["matched"] == []
    assert len(comparison["exclusions"]) == 2
    assert {r["reason"] for r in comparison["exclusions"]} == {"missing_matching_cell"}
    assert comparison["scope"] == "deployment_comparison"
    assert comparison["engine_causal_claim"] is False


def test_campaign_intervals_union_is_not_configured_or_request_concurrency(tmp_path):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    manifest, store = campaign_fixture(tmp_path)
    campaign_result(manifest, store, 0, timing=(10, 20))
    campaign_result(manifest, store, 1, timing=(15, 25))
    report = report_campaign(manifest, store)
    assert report["timing"]["child_active_wall_seconds"] == 15
    assert report["timing"]["active_wall_seconds"] is None
    assert report["timing"]["observed_job_overlap"] == 2
    assert report["timing"]["observed_request_overlap"] is None
    assert report["cells"][0]["requested_concurrency"] == 4
    assert report["cells"][0]["telemetry_coverage"]["gpu"] == "unknown"
    assert report["comparisons"][0]["complete"] is False


def test_campaign_two_repeats_are_descriptive_and_public_export_is_allowlisted(tmp_path):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    manifest, store = campaign_fixture(tmp_path, repeats=2)
    for index in range(4):
        campaign_result(manifest, store, index, passed=index != 1)
    report = report_campaign(manifest, store)
    assert report["repeats"][0]["score_range"] == [0, 1]
    assert report["repeats"][0]["agreement"] is False
    output = json.dumps(report)
    assert all(text not in output for text in ("private-", "PRIVATE PROMPT", "127.0.0.1", "owner", "different-model", "tool-sequence"))
    assert report["promotion_authorized"] is False


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


def test_campaign_performance_preserves_plan_valid_basic_cache_condition(tmp_path):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    manifest, store = campaign_fixture(tmp_path, suite="performance")
    for cell in manifest["cells"]:
        cell["cache_condition"] = "basic"
        cell["spec"]["parameters"] = {"request_count": 2, "cache_condition": "basic"}
    native = {"schema": "anvil-serving.performance-run/v1", "summary": {"metrics": {
        "request_count": {"status": "measured", "value": 2, "unit": "requests"}}}}
    campaign_result(manifest, store, 0, native=native)
    assert report_campaign(manifest, store)["cells"][0]["cache"] == {"requested": "basic", "proven": None}


def test_campaign_aider_ungraded_placeholder_does_not_prove_attempt(tmp_path):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    manifest, store = campaign_fixture(tmp_path, suite="aider")
    native = {"schema": "anvil-serving.aider-run/v1", "instances": [{"instance_id": "tool-sequence", "resolved": None}]}
    campaign_result(manifest, store, 0, native=native, state="failed")
    row = report_campaign(manifest, store)["cells"][0]
    assert row["samples"]["attempted"] is None
    assert row["samples"]["failed"] is None
    assert row["samples"]["scored"] == 0


def test_campaign_native_negative_oracle_score_survives_infrastructure_classification(tmp_path):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    manifest, store = campaign_fixture(tmp_path)
    native = {"schema": "anvil-serving.agentic-suite-run/v1", "observations": [
        {"scenario_id": "tool-sequence", "passed": False, "failure_class": "infrastructure_failure"}]}
    campaign_result(manifest, store, 0, native=native, state="failed")
    row = report_campaign(manifest, store)["cells"][0]
    assert row["samples"]["scored"] == 1
    assert row["samples"]["failed"] == 1


def test_campaign_completed_empty_native_population_is_explicitly_incomplete(tmp_path):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    manifest, store = campaign_fixture(tmp_path)
    campaign_result(manifest, store, 0, native={
        "schema": "anvil-serving.agentic-suite-run/v1", "observations": []})
    row = report_campaign(manifest, store)["cells"][0]
    assert row["evidence_status"] == "empty_native_population"
    assert row["samples"] == {"planned": 1, "submitted": 1, "attempted": 0,
                              "scored": 0, "failed": 0, "cancelled": 0, "unfinished": 1}
    assert row["measurement_complete"] is False


def test_campaign_manual_closure_without_activation_is_unverified(tmp_path):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    manifest, store = campaign_fixture(tmp_path)
    campaign_result(manifest, store, 0, timing=(10, 20))
    row = report_campaign(manifest, store)["cells"][0]
    assert row["identity_status"] == "unverified"
    assert row["measurement_complete"] is False
    assert set(row) & {"identity_status"} == {"identity_status"}


def test_campaign_report_projects_verified_release_activation_without_private_binding(tmp_path):
    from anvil_serving.benchmarking.campaign_execution import _manifest_digest
    from anvil_serving.benchmarking.jobs import canonical_json_bytes
    from anvil_serving.benchmarking.campaign_report import report_campaign
    manifest, store = campaign_fixture(tmp_path)
    manifest["budgets"] = {"drain_reserve_s": 10, "per_arm_s": 40, "total_s": 80}
    for arm in manifest["arms"]:
        arm["budget_s"] = 40
    binding = {"generation": "a" * 64, "metrics_url": "http://127.0.0.1:9999/engine/" + "a" * 64 + "/metrics",
               "container_id": "private-container", "started_at": "private-start", "served_model": "private-served"}
    activation = {"status": "verified", **binding,
                  "binding_sha256": hashlib.sha256(canonical_json_bytes(binding)).hexdigest()}
    coverage = {key: {"status": "measured", "complete": True, "sample_count": 1,
                      "started_monotonic": 9, "closed_monotonic": 21}
                for key in ("engine", "gpu", "host", "cgroup")}
    campaign_result(manifest, store, 0, timing=(10, 20), activation=activation, telemetry_coverage=coverage)
    release = {"schema": "anvil-serving.campaign-release/v1", "manifest_sha256": _manifest_digest(manifest),
               "batch_id": manifest["cells"][0]["batch_id"], "arm_id": manifest["cells"][0]["arm_id"],
               "run_ids": [manifest["cells"][0]["spec"]["run_id"]], "boot_id": "private-boot",
               "released_monotonic": 9, "traffic_deadline_monotonic": 39, "deadline_monotonic": 49,
               "allowance_s": 40, "drain_reserve_s": 10, "activation": activation}
    path = Path(store.run_root) / "owner" / manifest["campaign_id"] / "campaign/batches" / (release["batch_id"] + ".json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(release))
    row = report_campaign(manifest, store)["cells"][0]
    assert row["identity_status"] == "verified"
    assert set(row["telemetry_coverage"].values()) == {"unknown"}  # Summary-only legacy claims are not series evidence.
    public = json.dumps(row)
    assert all(value not in public for value in binding.values())


def test_campaign_batch_wall_starts_at_release_and_missing_drain_invalidates_total(tmp_path):
    from anvil_serving.benchmarking.campaign_report import report_campaign
    from anvil_serving.benchmarking.jobs import canonical_json_bytes
    manifest, store = campaign_fixture(tmp_path)
    manifest["budgets"] = {"drain_reserve_s": 10, "per_arm_s": 40, "total_s": 80}
    for arm in manifest["arms"]:
        arm["budget_s"] = 40
    manifest["manifest_sha256"] = hashlib.sha256(canonical_json_bytes({k: v for k, v in manifest.items() if k != "manifest_sha256"})).hexdigest()
    activation_binding = {"generation": "a" * 64, "metrics_url": "http://127.0.0.1:9999/engine/" + "a" * 64 + "/metrics",
                          "container_id": "private-container", "started_at": "private-start", "served_model": "private-served"}
    activation = {"status": "verified", **activation_binding,
                  "binding_sha256": hashlib.sha256(canonical_json_bytes(activation_binding)).hexdigest()}
    paths = [campaign_result(manifest, store, 0, timing=(10, 20)),
             campaign_result(manifest, store, 1, timing=(15, 25))]
    batches = Path(store.run_root) / "owner" / manifest["campaign_id"] / "campaign/batches"
    batches.mkdir(parents=True)
    for index, cell in enumerate(manifest["cells"]):
        (batches / (cell["batch_id"] + ".json")).write_text(json.dumps({
            "schema": "anvil-serving.campaign-release/v1", "arm_id": cell["arm_id"],
            "manifest_sha256": manifest["manifest_sha256"], "batch_id": cell["batch_id"],
            "run_ids": [cell["spec"]["run_id"]], "boot_id": "private-boot", "released_monotonic": 9 + index * 5,
            "deadline_monotonic": 49 + index * 5, "traffic_deadline_monotonic": 39 + index * 5,
                "drain_reserve_s": 10, "allowance_s": 40, "activation": activation}))
    # Build the explicit shared closure chain before querying this fake store.
    from anvil_serving.benchmarking import campaign_execution as execution
    from anvil_serving.benchmarking.artifacts import atomic_write_json, evidence_file_reference
    for index, cell in enumerate(manifest['cells']):
        release = json.loads((batches / (cell['batch_id'] + '.json')).read_text())
        receipt_path = paths[index] / 'campaign-execution.json'
        receipt = json.loads(receipt_path.read_text())
        receipt.update(activation=activation, clients_stopped_wall=100 + index)
        shared = {'schema': 'anvil-serving.campaign-batch-closure/v1',
            'manifest_sha256': manifest['manifest_sha256'], 'release_sha256': execution._digest(release),
            'batch_id': cell['batch_id'], 'status': 'closed', 'stops': [execution._stop_fact(receipt)],
            'drain': {'status': 'verified'}, 'closed_monotonic': receipt['closed_monotonic'],
            'finalized_monotonic': receipt['closed_monotonic']}
        closure = batches.parent / 'closures' / (cell['batch_id'] + '.json')
        closure.parent.mkdir(exist_ok=True)
        atomic_write_json(closure, shared)
        receipt['batch_closure'] = evidence_file_reference(str(closure), root=str(batches.parent))
        atomic_write_json(receipt_path, receipt)
        store.artifacts[cell['spec']['run_id']]['results']['campaign_execution'] = evidence_file_reference(str(receipt_path), root=str(paths[index]))
    report = report_campaign(manifest, store)
    assert report["timing"]["active_wall_seconds"] == 16
    assert report["timing"]["child_active_wall_seconds"] == 15
    receipt_path = paths[1] / "campaign-execution.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["drain"]["status"] = "unknown"
    receipt_path.write_text(json.dumps(receipt))
    timing = report_campaign(manifest, store)["timing"]
    assert timing["active_wall_seconds"] is None
    assert timing["known_active_wall_seconds"] == 11
    assert timing["unknown_batch_intervals"] == 1
