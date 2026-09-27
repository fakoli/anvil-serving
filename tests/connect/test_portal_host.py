from __future__ import annotations

import json

import pytest

from anvil_serving.connect.config import ManifestError, validate_manifest
from anvil_serving.connect.edge import published_hosts
from anvil_serving.connect.render import render
from tests.connect.test_render import isolated_manifest, local_tunnel_manifest


def test_portal_host_renders_gateway_owned_routes_and_oidc_callback():
    value = isolated_manifest()
    value["caddy"]["tls"] = {"mode": "acme", "certificate_file": "", "key_file": ""}
    before = render(value)
    value["gateway"]["gateway"]["portal_host"] = "home.example.test"
    result = render(value)
    assert "home.example.test" in published_hosts(validate_manifest(value))
    files = result["files"]
    invitation = files["authelia/notification-templates/IdentityVerificationJWT.html"]
    gateway = json.loads(files["gateway.json"])
    assert gateway["gateway"]["portal_host"] == "home.example.test"
    assert gateway["gateway"]["resources"] == json.loads(before["files"]["gateway.json"])["gateway"]["resources"]
    assert files["connectors/dashboard.json"] == before["files"]["connectors/dashboard.json"]
    assert "https://home.example.test/_anvil-connect/callback" in files["authelia/configuration.yml"]
    assert "default_redirection_url:" in files["authelia/configuration.yml"]
    assert 'href="https://auth.example.test/_anvil-connect/home"' in invitation
    caddy = json.loads(files["caddy.json"])
    routes = caddy["apps"]["http"]["servers"]["anvil_connect"]["routes"]
    home = [r for r in routes if r.get("match", [{}])[0].get("host") == ["home.example.test"]]
    assert len(home) == 1
    assert set(home[0]["match"][0]["path"]) == {
        "/", "/_anvil-connect/home", "/_anvil-connect/home/*",
        "/_anvil-connect/login", "/_anvil-connect/callback", "/_anvil-connect/logout",
    }
    assert home[0]["handle"][-1]["upstreams"][0]["dial"].endswith("/ingress.sock")
    landing = next(r for r in routes if r.get("match", [{}])[0].get("host") == [value["authelia"]["host"]])
    assert landing["handle"][-1]["headers"]["Location"] == ["https://home.example.test/_anvil-connect/home"]
    assert "home.example.test" in caddy["apps"]["tls"]["automation"]["policies"][0]["subjects"]


@pytest.mark.parametrize("host", ["", None, "Home.example.test", "https://home.example.test", "home.example.test:443", "home.example.test/path"])
def test_portal_host_rejects_noncanonical_hostname(host):
    value = isolated_manifest()
    value["gateway"]["gateway"]["portal_host"] = host
    with pytest.raises(ManifestError, match="portal_host|null is not allowed"):
        validate_manifest(value)


def test_portal_host_cannot_reuse_any_service_identity():
    value = local_tunnel_manifest()
    hosts = [value["authelia"]["host"], value["gateway"]["control_host"], value["gateway"]["tunnel_host"],
             value["gateway"]["local_tunnel"]["server_name"], value["gateway"]["local_tunnel"]["http_host"],
             *(r["rule"]["host"] for r in value["gateway"]["gateway"]["resources"])]
    for host in hosts:
        value["gateway"]["gateway"]["portal_host"] = host
        with pytest.raises(ManifestError, match="distinct|existing service identity"):
            validate_manifest(value)


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


@pytest.mark.parametrize("url", ["http://example.test", "http://localhost:8000", "https://user:secret@example.test", "https://example.test/other", "https://example.test?", "https://example.test#", "https://example.test:bad", None])
def test_router_keys_rejects_unsafe_destination(url):
    value = router_keys_manifest()
    value["gateway"]["gateway"]["router_keys"]["url"] = url
    with pytest.raises(ManifestError): validate_manifest(value)
