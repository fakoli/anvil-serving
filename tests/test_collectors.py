from __future__ import annotations

import json

import pytest

from anvil_serving import collectors
from tests.conftest import Response


def _config(**overrides):
    values = {
        "name": "gpu-gap-adapter",
        "adapter": "anvil-json-v1",
        "endpoint": "http://100.64.0.20:9100/capabilities",
        "capabilities": ("gpu-process-memory", "gpu-container-attribution"),
        "auth_env": "ANVIL_COLLECTOR_TOKEN",
    }
    values.update(overrides)
    return collectors.CollectorConfig(**values)


def test_private_adapter_requires_environment_resolved_authentication() -> None:
    with pytest.raises(ValueError, match="require auth_env"):
        _config(auth_env=None)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://8.8.8.8/capabilities",
        "https://collector.example/capabilities",
        "https://user:password@100.64.0.20/capabilities",
        "https://100.64.0.20/capabilities?token=secret",
    ],
)
def test_unsafe_or_credential_bearing_endpoints_are_rejected(endpoint) -> None:
    with pytest.raises(ValueError):
        _config(endpoint=endpoint)


def test_inspect_sends_bearer_token_and_redacts_all_output() -> None:
    seen = {}

    def opener(request, timeout):
        seen["authorization"] = request.get_header("Authorization")
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return Response(
            json.dumps(
                {
                    "status": "ok",
                    "capabilities": [
                        "gpu-process-memory",
                        "gpu-container-attribution",
                    ],
                    "diagnostic": "collector-secret-token",
                }
            ).encode()
        )

    result = collectors.inspect_adapter(
        _config(),
        environment={"ANVIL_COLLECTOR_TOKEN": "collector-secret-token"},
        opener=opener,
    )

    assert result["ok"] is True
    assert result["available_capabilities"] == [
        "gpu-container-attribution",
        "gpu-process-memory",
    ]
    assert seen["authorization"] == "Bearer collector-secret-token"
    assert seen["url"] == "http://100.64.0.20:9100/capabilities"
    assert "collector-secret-token" not in json.dumps(result)


def test_configure_writes_only_env_name_and_round_trips(tmp_path, capsys) -> None:
    output = tmp_path / "collector.json"
    rc = collectors.main(
        [
            "configure",
            "--name",
            "local-gap",
            "--endpoint",
            "http://127.0.0.1:9100/capabilities",
            "--capability",
            "gpu-gap",
            "--output",
            str(output),
        ]
    )

    assert rc == 0
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["auth_env"] is None
    assert "token" not in output.read_text(encoding="utf-8").lower()
    assert collectors.CollectorConfig.from_mapping(saved).name == "local-gap"
    assert json.loads(capsys.readouterr().out)["status"] == "configured"


def test_library_inspection_enforces_timeout_bounds() -> None:
    with pytest.raises(ValueError, match="timeout"):
        collectors.inspect_adapter(_config(), timeout=0)
