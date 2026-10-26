import json
from pathlib import Path

import pytest

from anvil_serving.media import MediaError, WorkflowRegistry, canonical_digest


ROOT = Path(__file__).parents[2] / "configs/media/workflows"


def test_h3_generation_preserves_the_tested_non_turbo_audio_video_recipe():
    registry = WorkflowRegistry(ROOT / "registry.h3.json")
    workflow_id = "video.h3-cobijada-t2v-v1"
    rendered = registry.render(
        workflow_id, "v1", {"prompt": "Two hosts talk in a studio.", "seed": 42},
        quality_profile="standard",
    )
    graph = rendered.graph
    assert graph["131"]["inputs"]["prompt"] == "Two hosts talk in a studio."
    assert graph["129"]["inputs"]["noise_seed"] == 42
    assert tuple(graph["131"]["inputs"][key] for key in ("width", "height", "length")) == (864, 480, 124)
    assert graph["123"]["inputs"]["sampler_name"] == "res_multistep"
    assert graph["124"]["inputs"]["steps"] == 20
    assert graph["124"]["inputs"]["scheduler"] == "simple"
    assert graph["130"]["inputs"]["fps"] == 24
    assert graph["130"]["inputs"]["audio"] == ["121", 0]
    assert graph["130"]["inputs"]["images"] == ["122", 0]
    assert graph["92"]["inputs"]["video"] == ["130", 0]
    assert len(rendered.descriptor.required_models) == 4
    assert rendered.descriptor.available is False
    with pytest.raises(MediaError, match="cannot be overridden"):
        registry.render(workflow_id, "v1", {"prompt": "x", "seed": 42, "width": 4096}, quality_profile="standard")


def test_h3_upscale_uses_owned_mp4_and_original_fps_and_waveform():
    registry = WorkflowRegistry(ROOT / "registry.h3.json")
    workflow_id = "video.h3-cobijada-upscale-v1"
    rendered = registry.render(
        workflow_id, "v1", {"source_video": "art_0123456789abcdef"}, quality_profile="standard"
    )
    graph = rendered.graph
    assert graph["6"]["inputs"]["file"] == "art_0123456789abcdef"
    assert graph["8"]["inputs"]["megapixels"] == 1.58203125
    assert graph["8"]["inputs"]["denoise"] == 0.1
    assert graph["8"]["inputs"]["tile_tokens"] == 25000
    assert graph["8"]["inputs"]["sampler_name"] == "euler"
    assert graph["8"]["inputs"]["lora_strength"] == 1.0
    assert graph["8"]["inputs"]["audio"] == ["7", 1]
    assert graph["10"]["inputs"]["audio"] == ["8", 1]
    assert graph["10"]["inputs"]["fps"] == ["7", 2]
    assert graph["11"]["inputs"]["video"] == ["10", 0]
    assert rendered.descriptor.parameters["source_video"].media_types == ("video/mp4",)
    assert len(rendered.descriptor.required_models) == 6
    assert rendered.descriptor.available is False
    with pytest.raises(MediaError, match="opaque artifact"):
        registry.render(workflow_id, "v1", {"source_video": "../local.mp4"}, quality_profile="standard")


def test_h3_bundle_binds_both_graphs_and_all_pinned_assets():
    lock = json.loads((ROOT / "bundle.h3.lock.json").read_text())
    assert lock["runtime"]["comfyui_release"] == "v0.42.0"
    assert lock["runtime"]["image_id"] == "sha256:2745761f803e2c0ac93c5c852e7dfbf0b5b8568ac386aae14277a748f4571bdc"
    assert lock["custom_nodes"][0]["revision"] == "3403ef7a81742fb76eb1633bdd5bb748ad85fd53"
    for entry in lock["workflows"]:
        descriptor = json.loads((ROOT / (entry["id"] + ".json")).read_text())
        graph = json.loads((ROOT / descriptor["graph"]).read_text())
        assert canonical_digest(graph) == descriptor["graph_digest"] == entry["graph_sha256"]
        assert {Path(item["target"]).name for item in entry["models"]} == set(descriptor["required_models"])
