"""Regression coverage for split-host controller CLI contracts."""

import pytest

from anvil_serving.commands import manifest_data
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


def test_remote_serves_down_declares_preview_argument():
    records = {record["path"]: record for record in manifest_data()["commands"]}

    assert "dry_run" in records["serves down"]["remote_operation"]["allowed_arguments"]


def test_gpu_inventory_can_run_in_the_hardened_controller_runtime():
    records = {record["path"]: record for record in manifest_data()["commands"]}

    assert records["host gpus"]["execution_runtime_roles"] == ["native", "docker"]
