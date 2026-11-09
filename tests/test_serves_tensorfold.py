"""TensorFold manifests retain their engine and use the OpenAI probe contract."""
import json
from io import BytesIO

import pytest

from anvil_serving import serves


@pytest.mark.parametrize("tokens, expected", [
    (["--name", "native-model"], "native-model"),
    (["--name=native-model"], "native-model"),
    (["--name", "container-label", "--served-model-name", "api-model"], "api-model"),
    (["--model-path", "/weights", "--name", "api-model"], "api-model"),
    (["--name"], None),
])
def test_native_model_name_flag(tokens, expected):
    assert serves._model_from_argv(tokens) == expected


@pytest.mark.parametrize("with_image", [False, True])
def test_tensorfold_manifest_and_probe(tmp_path, with_image):
    manifest = tmp_path / "serves.toml"
    manifest.write_text('''[[serve]]
name = "native-chat"
container = "native-chat"
runtime = "docker"
port = 30000
model = "example-model"
engine = "tensorfold"
''')
    (serve,) = serves.load_manifest(manifest)
    assert serve["engine"] == "tensorfold"
    image_path = tmp_path / "image.png"
    if with_image:
        image_path.write_bytes(b"synthetic-image")
    requests = []

    def respond(request, **_kwargs):
        requests.append(request)
        return BytesIO(json.dumps({"choices": [{"message": {"content": "READY"}, "finish_reason": "stop"}]}).encode())

    result = serves.probe_serve(serve, text="Reply READY", image_path=str(image_path) if with_image else None, _open=respond)
    assert result["engine"] == "tensorfold"
    assert result["recognized_excerpt"] == "READY"
    assert requests[0].full_url == "http://127.0.0.1:30000/v1/chat/completions"
    content = json.loads(requests[0].data)["messages"][0]["content"]
    assert isinstance(content, list) == with_image
