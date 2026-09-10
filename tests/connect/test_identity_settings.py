"""Managed SSO and passkey settings preserve explicit authority boundaries."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from anvil_serving.connect.config import ManifestError, validate_manifest
from anvil_serving.connect.render import render, render_for_inspection


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


def test_signing_reference_reaches_only_gateway_and_mode_matches_both_hops():
    value, resource = signed_declaration(isolated=True)
    files = render(value)["files"]
    gateway = json.loads(files["gateway.json"])
    signed = next(r for r in gateway["gateway"]["resources"] if r["rule"]["id"] == resource["rule"]["id"])
    assert signed["identity_key_env"] == resource["identity_key_env"]
    assert signed["identity_key_id"] == "dashboard-v1"
    gateway_unit = files["systemd/anvil-connect-gateway.service"]
    assert "EnvironmentFile=/etc/anvil-connect/secrets/gateway.env" in gateway_unit
    assert "EnvironmentFile=/etc/anvil-connect/secrets/gateway-identity.env" in gateway_unit
    assert gateway_unit.index("gateway.env") < gateway_unit.index("gateway-identity.env")
    for name, text in files.items():
        if name.startswith("connectors/"):
            assert resource["identity_key_env"] not in text
            assert "identity_key_id" not in text
    routes = json.loads(files["caddy.json"])["apps"]["http"]["servers"]["anvil_connect"]["routes"]
    for route in routes[:-1]:
        assert "X-Anvil-Connect-Identity" in route["handle"][0]["request"]["delete"]


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


def test_legacy_gateway_render_remains_single_environment_file():
    data = validate_manifest(declaration())
    assert "gateway_identity" not in data["environment_files"]
    unit = render_for_inspection(declaration())["files"]["systemd/anvil-connect-gateway.service"]
    assert unit.count("EnvironmentFile=") == 1


@pytest.mark.parametrize("field", ["identity_key_env", "identity_key_id"])
def test_signed_mode_requires_both_references(field):
    value, resource = signed_declaration()
    del resource[field]
    with pytest.raises(ManifestError, match="requires identity_key_env and identity_key_id"):
        validate_manifest(value)


@pytest.mark.parametrize(("field", "bad"), [
    ("identity_key_env", ""), ("identity_key_env", "lowercase"),
    ("identity_key_env", True), ("identity_key_id", "UPPER"),
    ("identity_key_id", "bad\nvalue"), ("identity_key_id", None),
])
def test_signing_references_reject_invalid_values(field, bad):
    value, resource = signed_declaration()
    resource[field] = bad
    with pytest.raises(ManifestError):
        validate_manifest(value)


def test_signing_keys_cannot_be_added_to_legacy_or_api_modes():
    for access in ("browser", "api"):
        value = declaration()
        resource = next(r for r in value["gateway"]["gateway"]["resources"] if r["rule"]["access"] == access)
        resource.update(identity_key_env="ANVIL_DASHBOARD_IDENTITY_KEY", identity_key_id="dashboard-v1")
        with pytest.raises(ManifestError, match="require signed-identity"):
            validate_manifest(value)


def test_signed_mode_requires_independent_connector_agreement():
    value, _ = signed_declaration()
    value["connectors"] = declaration()["connectors"]
    with pytest.raises(ManifestError, match="fixed connector binding"):
        validate_manifest(value)


def test_signing_secret_reference_cannot_be_shared_across_resources():
    value, resource = signed_declaration()
    other = copy.deepcopy(resource)
    other["rule"].update(id="other-dashboard", host="other.example.test")
    other["tunnel_address"] = "127.0.0.1:17301"
    value["gateway"]["gateway"]["resources"].append(other)
    with pytest.raises(ManifestError, match="distinct for each signed-identity"):
        validate_manifest(value)


def test_signing_key_id_cannot_be_shared_across_resources():
    value, resource = signed_declaration()
    other = copy.deepcopy(resource)
    other["rule"].update(id="other-dashboard", host="other.example.test")
    other["tunnel_address"] = "127.0.0.1:17301"
    other["identity_key_env"] = "ANVIL_OTHER_DASHBOARD_IDENTITY_KEY"
    value["gateway"]["gateway"]["resources"].append(other)
    with pytest.raises(ManifestError, match="identity_key_id.*distinct"):
        validate_manifest(value)


def passkeys():
    return {"enable_passkey_login": True, "experimental_enable_passkey_uv_two_factors": True,
            "discoverability": "required", "user_verification": "required"}


def test_passkey_policy_requires_explicit_opt_in_and_keeps_two_factor():
    value = isolated_declaration()
    assert "webauthn" not in validate_manifest(value)["authelia"]
    assert "enable_passkey_login" not in render(value)["files"]["authelia/configuration.yml"]
    value["authelia"]["webauthn"] = passkeys()
    text = render(value)["files"]["authelia/configuration.yml"]
    assert "enable_passkey_login: true" in text
    assert "experimental_enable_passkey_uv_two_factors: true" in text
    assert "user_verification: 'required'" in text
    assert "discoverability: 'required'" in text
    assert "default_policy: two_factor" in text
    assert "prohibit_backup_eligibility" not in text  # permits synced passkey providers
    assert "totp:" not in text  # existing recovery credentials are not removed


@pytest.mark.parametrize(("field", "bad"), [
    ("enable_passkey_login", 1), ("enable_passkey_login", "true"),
    ("experimental_enable_passkey_uv_two_factors", "true"),
    ("experimental_enable_passkey_uv_two_factors", None),
    ("discoverability", "preferred"), ("user_verification", "discouraged"),
    ("unknown_setting", True),
])
def test_passkey_policy_rejects_ambiguous_or_weaker_settings(field, bad):
    value = declaration()
    value["authelia"]["webauthn"] = {**passkeys(), field: bad}
    with pytest.raises(ManifestError):
        validate_manifest(value)


def test_two_factor_passkey_override_requires_enabled_login():
    value = declaration()
    value["authelia"]["webauthn"] = {**passkeys(), "enable_passkey_login": False}
    with pytest.raises(ManifestError, match="requires passkey login"):
        validate_manifest(value)
