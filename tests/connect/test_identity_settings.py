"""Managed SSO and passkey settings preserve explicit authority boundaries."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from anvil_serving.connect.config import ManifestError, validate_manifest


def declaration():
    return json.loads((Path(__file__).parents[2] / "connect/examples/deployment.json").read_text())


def isolated_declaration():
    value = declaration()
    del value["service_user"]
    value["service_identities"] = {
        "gateway": {"uid": 1201, "gid": 1201},
        "edge": {"uid": 1202, "gid": 1202},
        "idp": {"uid": 1203, "gid": 1203},
        "connectors": {"dashboard": {"uid": 1204, "gid": 1204}},
        "clients": {"dashboard-api": {"uid": 1205, "gid": 1205}},
        "ingress": {"group_id": 1290, "directory": "/run/anvil-connect/ingress"},
    }
    value["service_limits"] = {
        "gateway": {"memory_max_bytes": 805306368, "tasks_max": 128},
        "edge": {"memory_max_bytes": 536870912, "tasks_max": 64},
        "idp": {"memory_max_bytes": 536870912, "tasks_max": 64},
        "connectors": {"dashboard": {"memory_max_bytes": 402653184, "tasks_max": 64}},
        "clients": {"dashboard-api": {"memory_max_bytes": 268435456, "tasks_max": 32}},
    }
    value["caddy"]["state_directory"] = "/var/lib/anvil-connect/caddy"
    return value


def signed_declaration(*, isolated: bool = False):
    value = isolated_declaration() if isolated else declaration()
    resource = next(r for r in value["gateway"]["gateway"]["resources"] if r["rule"]["access"] == "browser")
    resource["rule"]["native_auth"] = "signed-identity"
    resource.update(identity_key_env="ANVIL_DASHBOARD_IDENTITY_KEY", identity_key_id="dashboard-v1")
    for connector in value["connectors"]:
        for item in connector["resources"]:
            if item["envelope"]["rule"]["id"] == resource["rule"]["id"]:
                item["envelope"]["rule"]["native_auth"] = "signed-identity"
    value["environment_files"]["gateway_identity"] = "/etc/anvil-connect/secrets/gateway-identity.env"
    return value, resource


def test_gateway_identity_environment_file_is_exactly_signed_mode_scoped():
    value, _ = signed_declaration()
    del value["environment_files"]["gateway_identity"]
    with pytest.raises(ManifestError, match="required for signed-identity"):
        validate_manifest(value)
    value = declaration()
    value["environment_files"]["gateway_identity"] = "/etc/anvil-connect/secrets/gateway-identity.env"
    with pytest.raises(ManifestError, match="only allowed for signed-identity"):
        validate_manifest(value)
    value, _ = signed_declaration()
    value["environment_files"]["gateway_identity"] = "relative.env"
    with pytest.raises(ManifestError, match="clean absolute path"):
        validate_manifest(value)


def test_signing_secret_reference_cannot_be_shared_across_resources():
    value, resource = signed_declaration()
    other = copy.deepcopy(resource)
    other["rule"].update(id="other-dashboard", host="other.example.test")
    other["tunnel_address"] = "127.0.0.1:17301"
    value["gateway"]["gateway"]["resources"].append(other)
    with pytest.raises(ManifestError, match="distinct for each signed-identity"):
        validate_manifest(value)


def passkeys():
    return {"enable_passkey_login": True, "experimental_enable_passkey_uv_two_factors": True,
            "discoverability": "required", "user_verification": "required"}
