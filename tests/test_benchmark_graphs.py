import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "skills"
    / "anvil-serving-benchmark-docs"
    / "scripts"
    / "plot_benchmark_matrix.py"
)
SKILL = SCRIPT.parents[1] / "SKILL.md"
ARTIFACT_CONTRACT = SCRIPT.parents[1] / "references" / "artifact-set-contract.md"


def _module():
    spec = importlib.util.spec_from_file_location("benchmark_graphs", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_fixture(
    tmp_path: Path,
    artifact: str = "run.json",
    *,
    artifact_schema: str = "anvil-serving.benchmark/v1",
) -> Path:
    (tmp_path / artifact).write_text(
        json.dumps(
            {
                "schema": artifact_schema,
                "metrics": {"ttft_p50_ms": 123.5},
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "graph-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "anvil-serving.benchmark-graph-manifest/v1",
                "title": "Test matrix",
                "subtitle": "Exact retained evidence",
                "output": "graphs/matrix.svg",
                "data_output": "graphs/data.json",
                "metric_semantics": {"ttft": "request start to first content"},
                "charts": [
                    {
                        "title": "TTFT",
                        "metric": "metrics.ttft_p50_ms",
                        "x_label": "Concurrency",
                        "y_label": "Milliseconds",
                        "series": [
                            {
                                "label": "Control",
                                "points": [{"x": "1", "artifact": artifact}],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _update_manifest(manifest: Path, **updates) -> None:
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document.update(updates)
    manifest.write_text(json.dumps(document), encoding="utf-8")


def test_renderer_is_deterministic_and_retains_provenance(tmp_path):
    module = _module()
    manifest = _write_fixture(tmp_path)

    svg_path, data_path = module.render(manifest)
    first_hash = hashlib.sha256(svg_path.read_bytes()).hexdigest()
    module.render(manifest)

    assert first_hash == hashlib.sha256(svg_path.read_bytes()).hexdigest()
    svg = svg_path.read_text(encoding="utf-8")
    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert 'role="img"' in svg
    assert (
        'data-anvil-schema="anvil-serving.benchmark-graph-data/v1"' in svg
    )
    assert "<title id=\"title\">Test matrix</title>" in svg
    assert data["schema"] == "anvil-serving.benchmark-graph-data/v1"
    point = data["charts"][0]["series"][0]["points"][0]
    assert point["value"] == 123.5
    assert point["artifact"] == "run.json"
    assert len(point["artifact_sha256"]) == 64


def test_renderer_rejects_artifacts_outside_manifest_directory(tmp_path):
    module = _module()
    manifest = _write_fixture(tmp_path, "../outside.json")

    with pytest.raises(ValueError, match="canonical relative path"):
        module.render(manifest)


def test_renderer_rejects_absolute_in_base_artifact_path(tmp_path):
    module = _module()
    manifest = _write_fixture(tmp_path, str(tmp_path / "run.json"))

    with pytest.raises(ValueError, match="canonical relative path"):
        module.render(manifest)


def test_renderer_rejects_arbitrary_json_schema(tmp_path):
    module = _module()
    manifest = _write_fixture(tmp_path, artifact_schema="unrelated/v1")

    with pytest.raises(ValueError, match="unsupported point artifact schema"):
        module.render(manifest)


def test_renderer_accepts_native_capacity_aggregate_schema(tmp_path):
    module = _module()
    manifest = _write_fixture(
        tmp_path, artifact_schema="anvil-serving.capacity-aggregate/v1"
    )

    _, data_path = module.render(manifest)

    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert data["charts"][0]["series"][0]["points"][0]["value"] == 123.5


@pytest.mark.parametrize("output_key", ["output", "data_output"])
def test_renderer_rejects_overwriting_source_artifact(tmp_path, output_key):
    module = _module()
    manifest = _write_fixture(tmp_path)
    _update_manifest(manifest, **{output_key: "run.json"})

    with pytest.raises(ValueError, match="must not overwrite"):
        module.render(manifest)


def test_renderer_rejects_overwriting_graph_manifest(tmp_path):
    module = _module()
    manifest = _write_fixture(tmp_path)
    _update_manifest(manifest, data_output="graph-manifest.json")

    with pytest.raises(ValueError, match="must not overwrite"):
        module.render(manifest)


def test_renderer_rejects_same_svg_and_data_output(tmp_path):
    module = _module()
    manifest = _write_fixture(tmp_path)
    _update_manifest(
        manifest, output="graphs/same.svg", data_output="graphs/same.svg"
    )

    with pytest.raises(ValueError, match="paths must differ"):
        module.render(manifest)


def test_renderer_rejects_absolute_in_base_output_path(tmp_path):
    module = _module()
    manifest = _write_fixture(tmp_path)
    _update_manifest(manifest, output=str(tmp_path / "matrix.svg"))

    with pytest.raises(ValueError, match="canonical relative path"):
        module.render(manifest)


@pytest.mark.parametrize(
    ("output_key", "relative", "payload", "expected"),
    [
        ("output", "graphs/matrix.svg", "<svg></svg>", "unmarked SVG"),
        ("data_output", "graphs/data.json", "{}", "unmarked graph data"),
    ],
)
def test_renderer_rejects_unmarked_existing_output(
    tmp_path, output_key, relative, payload, expected
):
    module = _module()
    manifest = _write_fixture(tmp_path)
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match=expected):
        module.render(manifest)


def test_renderer_accepts_legacy_generated_svg_marker(tmp_path):
    module = _module()
    manifest = _write_fixture(tmp_path)
    target = tmp_path / "graphs" / "matrix.svg"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        '<svg role="img"><text>Values are derived from the retained JSON '
        "artifacts named in the graph data file.</text></svg>",
        encoding="utf-8",
    )

    svg_path, _ = module.render(manifest)

    assert module.SVG_SCHEMA_MARKER in svg_path.read_text(encoding="utf-8")


def test_benchmark_skill_documents_graphing_and_timing_semantics():
    skill = SKILL.read_text(encoding="utf-8")
    contract = ARTIFACT_CONTRACT.read_text(encoding="utf-8")

    assert "plot_benchmark_matrix.py" in skill
    assert "TPOT and mean" in skill
    assert "effective prefill" in skill
    assert "engineering-learning" in skill
    assert "fix-forward disposition" in skill
    assert "manual retry is recovery evidence" in skill
    assert "graph-data JSON" in contract
    assert "source artifact" in contract
