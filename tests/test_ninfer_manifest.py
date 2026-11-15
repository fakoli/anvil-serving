"""Native NInfer manifests retain ordinary managed GPU reservations."""
import io
import json
import pytest
from anvil_serving import serves


@pytest.mark.parametrize("with_image", [False, True])
def test_ninfer_probe_uses_bounded_chat_contract(tmp_path, with_image):
    photo = tmp_path / "fixture.png"
    photo.write_bytes(b"fixture")
    def opened(request, timeout):
        payload = json.loads(request.data)
        assert request.full_url == "http://127.0.0.1:8080/v1/chat/completions"
        assert payload["model"] == "candidate"
        assert payload["max_tokens"] == 256
        assert payload["temperature"] == 0
        content = payload["messages"][0]["content"]
        if with_image:
            assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        else:
            assert content == "Reply READY"
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "READY"}, "finish_reason": "stop"}]}).encode())
    result = serves.probe_serve({"name": "candidate", "engine": "ninfer", "model": "candidate", "port": 8080}, text="Reply READY", image_path=str(photo) if with_image else None, _open=opened)
    assert result["recognized_excerpt"] == "READY"
