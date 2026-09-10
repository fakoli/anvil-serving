"""Closed headless-device approval declarations mirror the native gateway grammar."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from anvil_serving.connect.config import ManifestError, canonical_manifest, validate_manifest
from anvil_serving.connect.render import render


def declaration() -> dict:
    return json.loads((Path(__file__).parents[2] / "connect/examples/deployment.json").read_text())


def device_declaration(prefix: str = "/") -> dict:
    value = declaration()
    browser = next(item for item in value["gateway"]["gateway"]["resources"] if item["rule"]["access"] == "browser")
    browser["rule"]["path_prefix"] = prefix
    for connector in value["connectors"]:
        for item in connector["resources"]:
            if item["envelope"]["rule"]["id"] == browser["rule"]["id"]:
                item["envelope"]["rule"]["path_prefix"] = prefix
    human = "human:" + "a" * 64
    value["gateway"]["gateway"]["device_authorizations"] = [{
        "browser_resource": "dashboard",
        "api_resource": "dashboard-api",
        "methods": ["GET"],
        "label": "Mac client",
        "principals": {human: "dashboard-api"},
    }]
    value["clients"][0]["device_authorization"] = {
        "browser_host": "dash.example.test",
        "approval_path": ("/_anvil-connect/device" if prefix == "/" else prefix + "/_anvil-connect/device"),
        "api_resource": "dashboard-api",
        "methods": ["GET"],
    }
    return value


def test_absent_device_fields_preserve_legacy_canonical_manifest_and_render() -> None:
    value = declaration()
    normalized = validate_manifest(value)
    assert "device_authorizations" not in normalized["gateway"]["gateway"]
    assert "device_authorization" not in normalized["clients"][0]
    assert json.loads(render(value)["files"]["gateway.json"]) == normalized["gateway"]
    assert canonical_manifest(value) == canonical_manifest(normalized)


def test_device_authorization_is_copied_from_the_exact_gateway_binding() -> None:
    value = device_declaration("/observatory")
    normalized = validate_manifest(value)
    binding = normalized["gateway"]["gateway"]["device_authorizations"][0]
    client = normalized["clients"][0]["device_authorization"]
    assert binding == {
        "browser_resource": "dashboard",
        "api_resource": "dashboard-api",
        "methods": ["GET"],
        "label": "Mac client",
        "principals": {"human:" + "a" * 64: "dashboard-api"},
    }
    assert client == {
        "browser_host": "dash.example.test",
        "approval_path": "/observatory/_anvil-connect/device",
        "api_resource": "dashboard-api",
        "methods": ["GET"],
    }
    files = render(value)["files"]
    assert json.loads(files["gateway.json"])["gateway"]["device_authorizations"] == [binding]
    assert json.loads(files["clients/dashboard-api.json"])["device_authorization"] == client


@pytest.mark.parametrize(("path", "replacement"), [
    (("gateway", "gateway", "device_authorizations", 0, "browser_resource"), "dashboard-api"),
    (("gateway", "gateway", "device_authorizations", 0, "api_resource"), "dashboard"),
    (("gateway", "gateway", "device_authorizations", 0, "methods"), ["POST"]),
    (("gateway", "gateway", "device_authorizations", 0, "methods"), ["GET", "GET"]),
    (("gateway", "gateway", "device_authorizations", 0, "methods"), []),
    (("gateway", "gateway", "device_authorizations", 0, "label"), "unsafe\nlabel"),
    (("gateway", "gateway", "device_authorizations", 0, "label"), "x" * 129),
    (("gateway", "gateway", "device_authorizations", 0, "principals"), {"human:" + "A" * 64: "dashboard-api"}),
    (("gateway", "gateway", "device_authorizations", 0, "principals"), {"human:" + "a" * 64: "UPPER"}),
    (("clients", 0, "device_authorization", "browser_host"), "other.example.test"),
    (("clients", 0, "device_authorization", "approval_path"), "/_anvil-connect/device/"),
    (("clients", 0, "device_authorization", "approval_path"), "/other/_anvil-connect/device"),
    (("clients", 0, "device_authorization", "api_resource"), "other-api"),
    (("clients", 0, "device_authorization", "methods"), ["POST"]),
])
def test_device_authorization_rejects_widening_or_ambiguous_fields(path, replacement) -> None:
    value = device_declaration()
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    with pytest.raises(ManifestError):
        validate_manifest(value)


def test_device_approval_requires_browser_control_methods_not_api_grant_post() -> None:
    value = device_declaration()
    # The API grant remains GET-only, while the browser rule reserves GET/POST
    # for the approval page and its fixed control posts.
    assert validate_manifest(value)["gateway"]["gateway"]["device_authorizations"][0]["methods"] == ["GET"]
    browser = next(item for item in value["gateway"]["gateway"]["resources"] if item["rule"]["id"] == "dashboard")
    browser["rule"]["methods"] = ["GET"]
    for connector in value["connectors"]:
        for item in connector["resources"]:
            if item["envelope"]["rule"]["id"] == "dashboard":
                item["envelope"]["rule"]["methods"] = ["GET"]
    with pytest.raises(ManifestError, match="allow GET and POST"):
        validate_manifest(value)


def test_device_authorization_shape_and_principal_mappings_are_closed() -> None:
    value = device_declaration()
    value["gateway"]["gateway"]["device_authorizations"] = []
    with pytest.raises(ManifestError):
        validate_manifest(value)

    value = device_declaration()
    value["gateway"]["gateway"]["device_authorizations"][0]["unexpected"] = "value"
    with pytest.raises(ManifestError, match="unknown keys"):
        validate_manifest(value)


@pytest.mark.parametrize(("label", "accepted"), [
    ("é" * 64, True),  # 128 UTF-8 bytes
    ("é" * 65, False),
    ("bad\x01label", False),
    ("bad\x7flabel", False),
    ("\ud800", False),
])
def test_device_label_has_a_bounded_valid_utf8_control_free_grammar(label, accepted) -> None:
    value = device_declaration()
    value["gateway"]["gateway"]["device_authorizations"][0]["label"] = label
    if accepted:
        assert validate_manifest(value)["gateway"]["gateway"]["device_authorizations"][0]["label"] == label
    else:
        with pytest.raises(ManifestError):
            validate_manifest(value)

    value = device_declaration()
    value["gateway"]["gateway"]["device_authorizations"][0]["principals"] = {}
    with pytest.raises(ManifestError, match="opaque human mappings"):
        validate_manifest(value)

    value = device_declaration()
    value["clients"][0]["device_authorization"] = {
        "browser_host": "dash.example.test", "approval_path": "/_anvil-connect/device",
        "api_resource": "dashboard-api", "methods": ["GET"], "scope": "all",
    }
    with pytest.raises(ManifestError, match="unknown keys"):
        validate_manifest(value)


def test_client_device_authorization_requires_gateway_binding() -> None:
    value = device_declaration()
    del value["gateway"]["gateway"]["device_authorizations"]
    with pytest.raises(ManifestError, match="declared gateway authorization"):
        validate_manifest(value)


def test_device_authorization_principal_and_method_order_is_canonical() -> None:
    value = device_declaration()
    api = next(item for item in value["gateway"]["gateway"]["resources"] if item["rule"]["id"] == "dashboard-api")
    api["rule"]["methods"] = ["POST", "GET"]
    value["clients"][0]["rule"]["methods"] = ["POST", "GET"]
    for connector in value["connectors"]:
        for item in connector["resources"]:
            if item["envelope"]["rule"]["id"] == "dashboard-api":
                item["envelope"]["rule"]["methods"] = ["POST", "GET"]
    browser = next(item for item in value["gateway"]["gateway"]["resources"] if item["rule"]["id"] == "dashboard")
    browser["rule"]["methods"] = ["POST", "GET"]
    for connector in value["connectors"]:
        for item in connector["resources"]:
            if item["envelope"]["rule"]["id"] == "dashboard":
                item["envelope"]["rule"]["methods"] = ["POST", "GET"]
    binding = value["gateway"]["gateway"]["device_authorizations"][0]
    binding["methods"] = ["POST", "GET"]
    binding["principals"] = {"human:" + "b" * 64: "other-api", "human:" + "a" * 64: "dashboard-api"}
    value["clients"][0]["device_authorization"]["methods"] = ["POST", "GET"]
    normalized = validate_manifest(copy.deepcopy(value))
    assert normalized["gateway"]["gateway"]["device_authorizations"][0]["methods"] == ["GET", "POST"]
    assert list(normalized["gateway"]["gateway"]["device_authorizations"][0]["principals"]) == ["human:" + "a" * 64, "human:" + "b" * 64]
