import importlib.util
import json
from pathlib import Path


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
