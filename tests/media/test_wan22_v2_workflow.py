import json
from pathlib import Path

from anvil_serving.media import WorkflowRegistry, canonical_digest


ROOT = Path(__file__).parents[2] / "configs" / "media" / "workflows"


def _json(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_wan22_v2_uses_the_official_5b_sampling_and_latent_wiring():
    descriptor = _json("video.wan2.2-ti2v-5b-v2.json")
    graph = _json(descriptor["graph"])
    registry = WorkflowRegistry(ROOT / "registry.json")
    v1 = registry.get("video.wan2.2-ti2v-5b-v1", "v1")
    v2 = registry.get("video.wan2.2-ti2v-5b-v2", "v2")

    assert v1.available is False
    assert v1.unavailable_reasons == ("quality_failed",)
    assert v2.available is False
    assert v2.unavailable_reasons == ("quality_unverified",)
    assert descriptor["graph_digest"] == canonical_digest(graph)
    assert {"Wan22ImageToVideoLatent", "ModelSamplingSD3"} <= set(v2.required_nodes)

    latent = graph["5"]["inputs"]
    sampler = graph["6"]["inputs"]
    sampling = graph["9"]["inputs"]
    positive = graph["4"]["inputs"]
    negative = graph["7"]["inputs"]
    assert graph["5"]["class_type"] == "Wan22ImageToVideoLatent"
    assert latent["vae"] == ["3", 0]
    assert sampler["latent_image"] == ["5", 0]
    assert sampler["positive"] == ["4", 0]
    assert sampler["negative"] == ["7", 0]
    assert sampler["positive"] != sampler["negative"]
    assert positive["clip"] == negative["clip"] == ["2", 0]
    assert negative["text"] == "blur, artifacts, distorted geometry, oversaturation, text, watermark"
    assert sampling == {"model": ["1", 0], "shift": 8.0}
    assert sampler["model"] == ["9", 0]
    assert (sampler["cfg"], sampler["sampler_name"], sampler["scheduler"], sampler["denoise"]) == (
        5.0, "uni_pc", "simple", 1.0,
    )
    assert graph["8"]["inputs"]["format"] == "video/h264-mp4"
    assert graph["8"]["inputs"]["frame_rate"] == 16

    lock = _json("bundle.lock.json")
    locked = {(item["id"], item["version"]): item for item in lock["workflows"]}
    assert locked[(v2.id, v2.version)]["graph_sha256"] == descriptor["graph_digest"]
    assert locked[(v2.id, v2.version)]["models"] == locked[(v1.id, v1.version)]["models"]
