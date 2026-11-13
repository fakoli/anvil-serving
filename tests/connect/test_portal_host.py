from __future__ import annotations

import json

import pytest

from anvil_serving.connect.config import ManifestError, validate_manifest
from anvil_serving.connect.render import render
from tests.connect.test_render import isolated_manifest


def router_keys_manifest():
    value = isolated_manifest()
    gateway = value["gateway"]["gateway"]
    gateway["portal_host"] = "home.example.test"
    gateway["browser_administration"] = {"browser_resource": gateway["resources"][0]["rule"]["id"], "operators": ["human:"+"a"*64]}
    gateway["router_keys"] = {"url":"http://127.0.0.1:8000", "secret_env":"CONNECT_ROUTER_SIGNING", "check_env":"CONNECT_ROUTER_CHECK"}
    return value


def test_router_keys_render_and_separate_permission_boundary():
    value = router_keys_manifest()
    normalized = validate_manifest(value)
    rendered = json.loads(render(value)["files"]["gateway.json"])
    assert rendered["gateway"]["router_keys"] == normalized["gateway"]["gateway"]["router_keys"]
    assert "principals" not in rendered["gateway"]["router_keys"]
    for field in ("portal_host", "browser_administration"):
        changed = router_keys_manifest()
        del changed["gateway"]["gateway"][field]
        with pytest.raises(ManifestError): validate_manifest(changed)


def test_operator_application_policy_preserves_connector_and_requires_authority():
    value = router_keys_manifest()
    gateway = value["gateway"]["gateway"]
    resource = gateway["resources"][0]
    resource["operator_only"] = True
    rendered = render(value)["files"]
    assert json.loads(rendered["gateway.json"])["gateway"]["resources"][0]["operator_only"] is True
    assert "operator_only" not in rendered["connectors/dashboard.json"]
    del gateway["router_keys"]
    del gateway["browser_administration"]
    with pytest.raises(ManifestError, match="operator_only requires"):
        validate_manifest(value)
    resource["operator_only"] = False
    validate_manifest(value)
    for invalid in (None, "true", 1, [], {}):
        resource["operator_only"] = invalid
        with pytest.raises(ManifestError):
            validate_manifest(value)
    del resource["operator_only"]
    gateway["resources"][1]["operator_only"] = False
    with pytest.raises(ManifestError, match="browser resource"):
        validate_manifest(value)
