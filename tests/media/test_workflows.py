import json
from pathlib import Path

import pytest

from anvil_serving.media import MediaError, WorkflowRegistry, canonical_digest


ROOT = Path(__file__).parents[2] / "configs" / "media" / "workflows"


def test_packaged_registry_is_deterministic_and_explicit():
    registry = WorkflowRegistry(ROOT / "registry.json")
    first = registry.list()
    assert first == registry.list()
    assert [item["id"] for item in first] == [
        "image.flux2-klein-4b-fp8-v1",
        "video.wan2.2-ti2v-5b-v1",
    ]
    assert all(item["available"] is False for item in first)
    assert all("service_target" not in item for item in first)


def test_render_binds_only_declared_inputs_without_mutating_registry():
    registry = WorkflowRegistry(ROOT / "registry.json")
    rendered = registry.render(
        "image.flux2-klein-4b-fp8-v1",
        "v1",
        {"prompt": "a brass owl", "seed": 7, "width": 768, "height": 512, "steps": 12},
    )
    assert rendered.graph["4"]["inputs"]["text"] == "a brass owl"
    assert rendered.graph["6"]["inputs"]["width"] == 768
    assert rendered.graph["10"]["inputs"]["width"] == 768
    again = registry.render(
        "image.flux2-klein-4b-fp8-v1",
        "v1",
        {"prompt": "second", "seed": 8, "width": 512, "height": 512, "steps": 10},
    )
    assert again.graph["4"]["inputs"]["text"] == "second"


def test_unknown_parameter_and_workflow_fail_before_render():
    registry = WorkflowRegistry(ROOT / "registry.json")
    with pytest.raises(MediaError, match="unknown fields"):
        registry.render(
            "image.flux2-klein-4b-fp8-v1",
            "v1",
            {"prompt": "x", "seed": 1, "width": 512, "height": 512, "steps": 10, "raw_graph": {}},
        )
    with pytest.raises(MediaError) as error:
        registry.get("image.unknown", "v1")
    assert error.value.status == 404


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
