import json
from pathlib import Path

import pytest

from anvil_serving.media import ComfyUIClient, MediaError, WorkflowRegistry


ROOT = Path(__file__).parents[2] / "configs" / "media" / "workflows"


class Response:
    def __init__(self, value, *, raw=False):
        self.data = value if raw else json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size=-1):
        return self.data[:size]


def test_submit_uses_exact_rendered_graph_and_anvil_client_identity():
    registry = WorkflowRegistry(ROOT / "registry.json")
    rendered = registry.render(
        "image.flux2-dev-fp8mixed-v1",
        "v1",
        {"prompt": "owl", "seed": 1, "width": 512, "height": 512, "steps": 10},
    )
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data)
        return Response({"prompt_id": "prompt_123"})

    prompt_id = ComfyUIClient("http://127.0.0.1:8188", opener=opener).submit(
        rendered, job_id="job_0123456789abcdef"
    )
    assert prompt_id == "prompt_123"
    assert seen["url"].endswith("/prompt")
    assert seen["body"]["prompt"] == rendered.graph
    assert seen["body"]["client_id"] == "anvil-job_0123456789abcdef"


def test_history_normalizes_outputs_without_public_coordinates():
    private = {
        "prompt_123": {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {"8": {"images": [{"filename": "private.png", "subfolder": "secret", "type": "output"}]}},
        }
    }
    status = ComfyUIClient(
        "http://127.0.0.1:8188", opener=lambda *_args, **_kwargs: Response(private)
    ).history("prompt_123")
    assert status.state == "completed"
    assert len(status.outputs) == 1
    assert "private.png" not in repr(status)


def test_fetch_output_is_bounded_and_internal_only():
    from anvil_serving.media import BackendOutput

    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        return Response(b"1234", raw=True)

    client = ComfyUIClient("http://127.0.0.1:8188", opener=opener)
    assert client.fetch_output(BackendOutput("8", "a.png"), max_bytes=4) == b"1234"
    assert "/view?" in seen["url"]
    with pytest.raises(MediaError):
        client.fetch_output(BackendOutput("8", "a.png"), max_bytes=0)


def test_upload_rejects_paths_and_oversize_before_contact():
    contacted = []
    client = ComfyUIClient("http://127.0.0.1:8188", opener=lambda *_a, **_k: contacted.append(True))
    with pytest.raises(MediaError):
        client.upload_input("../private.png", b"x")
    with pytest.raises(MediaError):
        client.upload_input("input.png", b"xx", max_bytes=1)
    assert contacted == []
