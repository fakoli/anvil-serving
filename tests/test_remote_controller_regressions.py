"""Regression coverage for split-host controller CLI contracts."""

import pytest

from anvil_serving.transports import Operation


def test_operation_allows_declared_credential_environment_references():
    operation = Operation(
        "eval-preflight",
        {
            "api_key_env": "ANVIL_ROUTER_TOKEN",
            "voice_api_key_env": "ANVIL_VOICE_TOKEN",
        },
    )

    assert dict(operation.arguments) == {
        "api_key_env": "ANVIL_ROUTER_TOKEN",
        "voice_api_key_env": "ANVIL_VOICE_TOKEN",
    }


def test_operation_still_rejects_raw_credential_payloads():
    with pytest.raises(ValueError, match="credential payloads"):
        Operation("eval-preflight", {"api_key": "not-allowed"})
