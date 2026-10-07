"""Audio models retain native platform and exact readiness contracts."""
import io
import json

import pytest

from anvil_serving.service_runtime.contracts import (
    MODEL_ENGINES, ServiceError, capabilities, validate_platform,
)
from anvil_serving.service_runtime.engine import inspect


def test_audio_models_are_admitted_model_workloads_on_native_macos():
    assert "mlx-audio" in MODEL_ENGINES
    assert "mlx-audio" in capabilities("macos")["native_engines"]
    validate_platform({"manager": "launchd", "engine": "mlx-audio"}, "macos")


@pytest.mark.parametrize("host_os", ["macos", "linux", "windows"])
def test_audio_models_cannot_use_docker(host_os):
    with pytest.raises(ServiceError, match="native macOS"):
        validate_platform({"manager": "docker", "engine": "mlx-audio"}, host_os)


@pytest.mark.parametrize("models,expected", [([], False), (["other"], False), (["pinned"], True)])
def test_audio_readiness_requires_the_declared_model(models, expected):
    requests = []
    def read(request, timeout):
        requests.append(request.full_url)
        return io.BytesIO(json.dumps({"data": [{"id": model} for model in models]}).encode())
    result = inspect({"engine": "mlx-audio", "endpoint": "http://127.0.0.1:1234/v1",
                      "model": "pinned"}, open_url=read)
    assert requests == ["http://127.0.0.1:1234/v1/models"]
    assert result["ready"] is expected
    assert result["advertised_models"] == models
