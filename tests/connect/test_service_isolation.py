"""Closed numeric service-identity and isolated renderer contract."""
from __future__ import annotations

import json
from pathlib import Path


from anvil_serving.connect.render import render


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
    value["service_limits"] = {
        "gateway": {"memory_max_bytes": 805306368, "tasks_max": 128},
        "edge": {"memory_max_bytes": 536870912, "tasks_max": 64},
        "idp": {"memory_max_bytes": 536870912, "tasks_max": 64},
        "connectors": {"dashboard": {"memory_max_bytes": 402653184, "tasks_max": 64}},
        "clients": {"dashboard-api": {"memory_max_bytes": 268435456, "tasks_max": 32}},
    }
    value["caddy"]["state_directory"] = "/var/lib/anvil-connect/caddy"
    return value


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
        for line in ("UMask=0077", "NoNewPrivileges=true", "ProtectSystem=strict", "ProtectHome=true", "PrivateTmp=true", "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6", "MemoryAccounting=true", "TasksAccounting=true"):
            assert line in unit
    assert "MemoryMax=805306368\nTasksMax=128" in gateway
    assert "MemoryMax=536870912\nTasksMax=64" in caddy
    assert "MemoryMax=536870912\nTasksMax=64" in authelia
    assert "MemoryMax=402653184\nTasksMax=64" in connector
    assert "MemoryMax=268435456\nTasksMax=32" in client
    assert "ReadWritePaths=/run/anvil-connect/ingress /var/lib/anvil-connect/gateway" in gateway
    assert "ReadWritePaths=/var/lib/anvil-connect/caddy" in caddy
    assert "ReadWritePaths=/etc/anvil-connect/users.yml /var/lib/anvil-connect/authelia" in authelia
    assert "ReadWritePaths=/var/lib/anvil-connect/connectors/dashboard" in connector
    assert "ReadWritePaths=\n" in client
    assert "Environment=XDG_CONFIG_HOME=/var/lib/anvil-connect/caddy/config" in caddy
    assert "Environment=XDG_DATA_HOME=/var/lib/anvil-connect/caddy/data" in caddy
    assert "CapabilityBoundingSet=CAP_NET_BIND_SERVICE" in caddy
    assert "CapabilityBoundingSet=\n" in gateway + authelia + connector + client
    caddy_json = json.loads(files["caddy.json"])
    routes = caddy_json["apps"]["http"]["servers"]["anvil_connect"]["routes"]
    assert any(route.get("handle", [{}, {"upstreams": [{"dial": ""}]}])[1].get("upstreams", [{"dial": ""}])[0]["dial"] == "unix//run/anvil-connect/ingress/ingress.sock" for route in routes if len(route.get("handle", [])) > 1)
