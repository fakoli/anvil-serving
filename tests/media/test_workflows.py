import json
from pathlib import Path

import pytest

from anvil_serving.media import MediaError, WorkflowRegistry, canonical_digest


ROOT = Path(__file__).parents[2] / "configs" / "media" / "workflows"


def test_digest_mismatch_fails_closed(tmp_path):
    source = json.loads((ROOT / "image.flux2-klein-4b-fp8-v1.json").read_text())
    graph = json.loads((ROOT / source["graph"]).read_text())
    graph["4"]["inputs"]["text"] = "tampered"
    (tmp_path / "graph.json").write_text(json.dumps(graph))
    source["graph"] = "graph.json"
    (tmp_path / "workflow.json").write_text(json.dumps(source))
    (tmp_path / "registry.json").write_text(
        json.dumps({"schema": "anvil-serving.media-workflow-registry/v1", "workflows": ["workflow.json"]})
    )
    with pytest.raises(MediaError) as error:
        WorkflowRegistry(tmp_path / "registry.json")
    assert error.value.code == "workflow_digest_mismatch"


def test_declared_graph_digests_are_canonical():
    for descriptor_path in sorted(ROOT.glob("*.json")):
        raw = json.loads(descriptor_path.read_text())
        if raw.get("schema") != "anvil-serving.media-workflow/v1":
            continue
        graph = json.loads((ROOT / raw["graph"]).read_text())
        assert raw["graph_digest"] == canonical_digest(graph)
