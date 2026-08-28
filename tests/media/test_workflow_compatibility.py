import io
import json
import urllib.error
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
    values = {
        "/system_stats": {"system": {"comfyui_version": "pinned"}},
        "/features": list(workflow.required_features),
        "/models/diffusion_models": list(workflow.required_models),
        "/models/text_encoders": [],
        "/models/vae": [],
        "/models/checkpoints": [],
    }
    values.update({f"/object_info/{name}": {name: {}} for name in workflow.required_nodes})
    return values


def test_compatibility_checks_all_bounded_surfaces_and_preserves_quality_gate():
    workflow = WorkflowRegistry(ROOT / "registry.json").get(
        "video.wan2.2-ti2v-5b-v1", "v1"
    )
    seen = []
    result = ComfyUIClient(
        "http://127.0.0.1:8188",
        opener=opener_for(inventory(workflow), seen),
    ).compatibility(workflow)
    assert result.ready is True
    assert result.available is False
    assert result.reasons == workflow.unavailable_reasons
    assert [url.rsplit("/", 1)[-1] for _, url, _ in seen[:3]] == [
        "system_stats",
        "features",
        workflow.required_nodes[0],
    ]


def test_missing_node_is_normalized_without_private_metadata():
    workflow = WorkflowRegistry(ROOT / "registry.json").get("image.flux2-klein-4b-fp8-v1", "v1")
    values = inventory(workflow)
    values["/object_info/SaveImage"] = {}
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

    workflow = WorkflowRegistry(ROOT / "registry.json").get("image.flux2-klein-4b-fp8-v1", "v1")
    with pytest.raises(MediaError) as error:
        ComfyUIClient("http://127.0.0.1:8188", opener=lambda *_args, **_kwargs: Oversized()).compatibility(workflow)
    assert error.value.code == "backend_response_too_large"


@pytest.mark.parametrize("status", [502, 503, 504])
def test_proxy_unavailable_status_at_health_boundary_requests_cold_lifecycle(status):
    workflow = WorkflowRegistry(ROOT / "registry.json").get(
        "image.flux2-klein-4b-fp8-v1", "v1"
    )

    def unavailable(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            status,
            "upstream unavailable",
            {},
            io.BytesIO(b""),
        )

    with pytest.raises(MediaError) as error:
        ComfyUIClient("https://media.example.test/comfyui", opener=unavailable).compatibility(
            workflow
        )
    assert error.value.code == "backend_unavailable"
    assert error.value.status == 503
    assert error.value.details == {}


def test_nonavailability_http_error_at_health_boundary_remains_backend_error():
    workflow = WorkflowRegistry(ROOT / "registry.json").get(
        "image.flux2-klein-4b-fp8-v1", "v1"
    )

    def broken(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            500,
            "backend failure",
            {},
            io.BytesIO(b""),
        )

    with pytest.raises(MediaError) as error:
        ComfyUIClient("https://media.example.test/comfyui", opener=broken).compatibility(
            workflow
        )
    assert error.value.code == "backend_http_error"
    assert error.value.details["status"] == 500
