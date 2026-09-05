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


@pytest.mark.parametrize("value", ['"bad"', '[]'])
def test_malformed_recipe_serve_returns_clean_cli_error(tmp_path, value):
    path, _ = fixture(tmp_path)
    (tmp_path / "recipe.toml").write_text(f'[[recipe]]\nmodel="test-recipe"\nserve={value}\n')
    assert main([str(path), "--root", str(tmp_path)]) == 1
