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
