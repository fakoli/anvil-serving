import json
from pathlib import Path

import pytest

from anvil_serving.media import ComfyUIClient, MediaError, WorkflowRegistry


ROOT = Path(__file__).parents[2] / "configs" / "media" / "workflows"


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size=-1):
        return json.dumps(self.value).encode()[:size]


def opener_for(values, seen):
    def open_request(request, timeout):
        seen.append((request.method, request.full_url, timeout))
        path = request.full_url.split("8188", 1)[1]
        return Response(values[path])

    return open_request


def inventory(workflow):
    return {
        "/system_stats": {"system": {"comfyui_version": "pinned"}},
        "/features": list(workflow.required_features),
        "/object_info": {name: {} for name in workflow.required_nodes},
        "/models/diffusion_models": list(workflow.required_models),
        "/models/text_encoders": [],
        "/models/vae": [],
        "/models/checkpoints": [],
    }


def test_compatibility_checks_all_bounded_surfaces_and_preserves_quality_gate():
    workflow = WorkflowRegistry(ROOT / "registry.json").get("image.flux2-dev-fp8mixed-v1", "v1")
    seen = []
    result = ComfyUIClient(
        "http://127.0.0.1:8188",
        opener=opener_for(inventory(workflow), seen),
    ).compatibility(workflow)
    assert result.ready is True
    assert result.available is False
    assert result.reasons == workflow.unavailable_reasons
    assert [url.rsplit("/", 1)[-1] for _, url, _ in seen[:3]] == ["system_stats", "features", "object_info"]


def test_missing_node_is_normalized_without_private_metadata():
    workflow = WorkflowRegistry(ROOT / "registry.json").get("image.flux2-dev-fp8mixed-v1", "v1")
    values = inventory(workflow)
    values["/object_info"].pop("SaveImage")
    result = ComfyUIClient(
        "http://127.0.0.1:8188",
        opener=opener_for(values, []),
    ).compatibility(workflow)
    assert result.missing_nodes == ("SaveImage",)
    assert result.reasons == ("missing_node",)
    assert "127.0.0.1" not in repr(result.as_public_dict())


def test_oversized_metadata_fails_closed():
    class Oversized:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, size=-1):
            return b"{" + b"x" * size

    workflow = WorkflowRegistry(ROOT / "registry.json").get("image.flux2-dev-fp8mixed-v1", "v1")
    with pytest.raises(MediaError) as error:
        ComfyUIClient("http://127.0.0.1:8188", opener=lambda *_args, **_kwargs: Oversized()).compatibility(workflow)
    assert error.value.code == "backend_response_too_large"
