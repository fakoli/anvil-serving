"""Closed numeric service-identity and isolated renderer contract."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from anvil_serving.connect.config import ManifestError, require_isolated, role_identity, validate_manifest
from anvil_serving.connect.render import render, render_for_inspection


ROOT = Path(__file__).parents[2]


def legacy_manifest() -> dict:
    return json.loads((ROOT / "connect/examples/deployment.json").read_text(encoding="utf-8"))


def isolated_manifest() -> dict:
    value = legacy_manifest()
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


def test_legacy_is_readable_but_not_an_isolated_role() -> None:
    legacy = validate_manifest(legacy_manifest())
    assert legacy["service_user"] == "anvil-connect"
    assert "service_identities" not in legacy
    with pytest.raises(ManifestError, match="isolated service identities"):
        require_isolated(legacy)
    with pytest.raises(ManifestError, match="isolated service identities"):
        role_identity(legacy, "gateway")
    assert render_for_inspection(legacy_manifest())["generation"]
    with pytest.raises(ManifestError, match="isolated service identities"):
        render(legacy_manifest())


def test_isolated_identity_shape_is_exact_sorted_and_revalidatable() -> None:
    value = isolated_manifest()
    normalized = validate_manifest(value)
    assert role_identity(normalized, "gateway") == (1201, 1201)
    assert role_identity(normalized, "edge") == (1202, 1202)
    assert role_identity(normalized, "idp") == (1203, 1203)
    assert role_identity(normalized, "connector", "dashboard") == (1204, 1204)
    assert role_identity(normalized, "client", "dashboard-api") == (1205, 1205)
    assert normalized["gateway"]["ingress"] == {
        "directory": "/run/anvil-connect/ingress",
        "gateway_uid": 1201,
        "edge_uid": 1202,
        "group_id": 1290,
    }
    assert validate_manifest(normalized) == normalized


@pytest.mark.parametrize("mutate", [
    lambda value: value.__setitem__("service_user", "anvil-connect"),
    lambda value: value["service_identities"].__setitem__("unexpected", {}),
    lambda value: value["service_identities"]["gateway"].__setitem__("uid", True),
    lambda value: value["service_identities"]["edge"].__setitem__("uid", 1201),
    lambda value: value["service_identities"]["idp"].__setitem__("gid", 1202),
    lambda value: value["service_identities"]["ingress"].__setitem__("group_id", 1201),
    lambda value: value["service_identities"]["connectors"].__setitem__("unknown", {"uid": 1206, "gid": 1206}),
    lambda value: value["service_identities"]["clients"].clear(),
])
def test_isolated_identity_rejects_mixed_partial_or_nonunique_roles(mutate) -> None:
    value = isolated_manifest()
    mutate(value)
    with pytest.raises(ManifestError):
        validate_manifest(value)



@pytest.mark.parametrize("section", ["gateway", "connectors", "clients"])
def test_environment_files_must_not_share_canonical_paths(section: str) -> None:
    value = isolated_manifest()
    shared = value["environment_files"]["gateway"]
    if section == "connectors":
        value["environment_files"][section]["dashboard"] = shared
    elif section == "clients":
        value["environment_files"][section]["dashboard-api"] = shared
    else:
        value["environment_files"]["connectors"]["dashboard"] = shared
    with pytest.raises(ManifestError, match="must not reuse canonical EnvironmentFile paths"):
        validate_manifest(value)


def test_gateway_identity_environment_file_must_be_unique() -> None:
    value = isolated_manifest()
    resource = next(item for item in value["gateway"]["gateway"]["resources"] if item["rule"]["access"] == "browser")
    resource["rule"]["native_auth"] = "signed-identity"
    resource.update(identity_key_env="ANVIL_DASHBOARD_IDENTITY_KEY", identity_key_id="dashboard-v1")
    for connector in value["connectors"]:
        for item in connector["resources"]:
            if item["envelope"]["rule"]["id"] == resource["rule"]["id"]:
                item["envelope"]["rule"]["native_auth"] = "signed-identity"
    value["environment_files"]["gateway_identity"] = value["environment_files"]["clients"]["dashboard-api"]
    with pytest.raises(ManifestError, match="must not reuse canonical EnvironmentFile paths"):
        validate_manifest(value)

def test_manifest_requires_exactly_one_identity_mode() -> None:
    value = legacy_manifest()
    del value["service_user"]
    with pytest.raises(ManifestError, match="exactly one"):
        validate_manifest(value)


def test_ingress_is_derived_or_must_match_exactly() -> None:
    value = isolated_manifest()
    normalized = validate_manifest(value)
    value["gateway"]["ingress"] = copy.deepcopy(normalized["gateway"]["ingress"])
    assert validate_manifest(value)["gateway"]["ingress"] == normalized["gateway"]["ingress"]
    value["gateway"]["ingress"]["edge_uid"] = 1900
    with pytest.raises(ManifestError, match="derived isolated ingress"):
        validate_manifest(value)


@pytest.mark.parametrize(("path", "replacement"), [
    (("gateway", "state_directory"), "/var/lib/anvil-connect/caddy/child"),
    (("authelia", "state_directory"), "/var/lib/anvil-connect/gateway"),
    (("caddy", "state_directory"), "/var/lib/anvil-connect/gateway"),
    (("connector", "state_directory"), "/var/lib/anvil-connect/gateway/child"),
    (("ingress", "directory"), "/var/lib/anvil-connect"),
])
def test_isolated_state_and_ingress_paths_must_be_disjoint(path: tuple[str, str], replacement: str) -> None:
    value = isolated_manifest()
    section, key = path
    if section == "connector":
        value["connectors"][0][key] = replacement
    elif section == "ingress":
        value["service_identities"][section][key] = replacement
    else:
        value[section][key] = replacement
    with pytest.raises(ManifestError, match="disjoint paths"):
        validate_manifest(value)


def test_isolated_caddy_state_is_required() -> None:
    value = isolated_manifest()
    del value["caddy"]["state_directory"]
    with pytest.raises(ManifestError, match="state_directory"):
        validate_manifest(value)


def test_isolated_units_are_numeric_and_role_scoped() -> None:
    files = render(isolated_manifest())["files"]
    gateway = files["systemd/anvil-connect-gateway.service"]
    caddy = files["systemd/anvil-connect-caddy.service"]
    authelia = files["systemd/anvil-connect-authelia.service"]
    connector = files["systemd/anvil-connect-connector-dashboard.service"]
    client = files["systemd/anvil-connect-client-dashboard-api.service"]
    assert "User=1201" in gateway and "Group=1201" in gateway
    assert "User=1202" in caddy and "Group=1202" in caddy and "SupplementaryGroups=1290" in caddy
    assert "User=1203" in authelia and "User=1204" in connector and "User=1205" in client
    assert "SupplementaryGroups=" not in gateway + authelia + connector + client
    for unit in (gateway, caddy, authelia, connector, client):
        for line in ("UMask=0077", "NoNewPrivileges=true", "ProtectSystem=strict", "ProtectHome=true", "PrivateTmp=true", "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6"):
            assert line in unit
    assert "ReadWritePaths=/run/anvil-connect/ingress /var/lib/anvil-connect/gateway" in gateway
    assert "ReadWritePaths=/var/lib/anvil-connect/caddy" in caddy
    assert "ReadWritePaths=/var/lib/anvil-connect/authelia" in authelia
    assert "ReadWritePaths=/var/lib/anvil-connect/connectors/dashboard" in connector
    assert "ReadWritePaths=\n" in client
    assert "Environment=XDG_CONFIG_HOME=/var/lib/anvil-connect/caddy/config" in caddy
    assert "Environment=XDG_DATA_HOME=/var/lib/anvil-connect/caddy/data" in caddy
    assert "CapabilityBoundingSet=CAP_NET_BIND_SERVICE" in caddy
    assert "CapabilityBoundingSet=\n" in gateway + authelia + connector + client
    caddy_json = json.loads(files["caddy.json"])
    routes = caddy_json["apps"]["http"]["servers"]["anvil_connect"]["routes"]
    assert any(route.get("handle", [{}, {"upstreams": [{"dial": ""}]}])[1].get("upstreams", [{"dial": ""}])[0]["dial"] == "unix//run/anvil-connect/ingress/ingress.sock" for route in routes if len(route.get("handle", [])) > 1)
