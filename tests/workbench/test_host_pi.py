"""The host UI is a Connect-admin entry, separate from task authority."""

import json
from dataclasses import replace

import pytest

from test_service import site  # noqa: F401


def host_config():
    return {"id": "host-pi", "resource_id": "serve-a", "origin": "https://pi.example.test",
            "version": "0.9.0", "runtime_sha256": "a" * 64}


def host_owner_config(*names):
    return {"host_pi": host_config() | {"token_ref": "BRIDGE_TOKEN"}, "projects": [
        {"id": name, "resource_id": "serve-a"} for name in (names or ("product",))
    ]}


def test_host_catalog_requires_an_explicit_grant_even_for_wildcard_owner(site):  # noqa: F811
    console, _, mode = site
    if mode != "connect":
        pytest.skip("Connect administrator grant boundary")
    console.workbench.config["host_pi"] = host_config()
    session = next(iter(console.access._sessions.values()))
    for grants, available in (({"*"}, False), ({"unrelated"}, False), ({"*", "serve-a"}, True)):
        changed = replace(session, principal=replace(session.principal, resources=frozenset(grants)))
        host = console.workbench.catalog(changed)["host_pi"]
        assert host["available"] is available and ("origin" in host) is available


def test_host_client_accepts_512_native_rows_and_rejects_513():
    from anvil_serving.observability.dashboard.contracts import ObservatoryError
    from anvil_serving.workbench_app.host_pi import HostPiClient

    rows = [{"native_id": f"native-{index}", "title": "Pi", "running": False} for index in range(512)]
    response = [json.dumps({"v": 1, "items": rows}).encode()]

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self, limit): return response[0][:limit]

    def open_url(_request, *, timeout):
        assert timeout == 1.5
        return Response()

    client = HostPiClient("https://pi.example.test", "private", open_url=open_url)
    assert len(client.list_native()) == 512
    response[0] = json.dumps({"v": 1, "items": rows + [rows[0] | {"native_id": "native-513"}]}).encode()
    with pytest.raises(ObservatoryError, match="unavailable"):
        client.list_native()
    response[0] = json.dumps({"v": 1, "items": [
        {"native_id": f"native-{index:03d}-" + "x" * 180, "title": "x" * 192, "running": False}
        for index in range(512)
    ]}).encode()
    assert len(response[0]) > 128 * 1024
    with pytest.raises(ObservatoryError, match="unavailable"):
        client.list_native()


def test_host_owner_association_verifies_before_persisting_and_lists_safe_native_metadata(tmp_path):
    from anvil_serving.observability.dashboard.access import ConnectBinding, Principal, Session
    from anvil_serving.observability.dashboard.contracts import ObservatoryError
    from anvil_serving.workbench_app.host_pi import HostPi
    from anvil_serving.workbench_app.store import PrivateStore

    class Access:
        def permit(self, session, resource, action=None): pass
    class Projects:
        def project(self, session, project_id):
            return {"id": "product", "roots": [{"id": "primary", "path": str(tmp_path / "checkout"), "task_access": "read-write"}], "primary_root_id": "primary"}
    class Client:
        def __init__(self): self.associations = []
        def associate(self, **body): self.associations.append(body); return body["native_id"]
        def list_native(self):
            return [{"native_id": "native-owned", "title": "Native title", "running": True},
                    {"native_id": "native-free", "title": "Unassigned", "running": False}]
    session = Session("key", "csrf", Principal("alice", "alice", "operator", frozenset({"serve-a"}), frozenset()), 0,
                      ConnectBinding("alice", "resource", "issuer", "subject", "proof", "admin"))
    client = Client()
    owner = HostPi(host_owner_config(), PrivateStore(tmp_path / "private.sqlite"), Projects(), Access(), client)
    associated = owner.associate(session, "product", "adopt-1", "native-owned", "Preferred title")
    assert associated["native_id"] == "native-owned"
    assert client.associations[0]["cwd"] == str(tmp_path / "checkout")
    rows = owner.list(session, "product")
    assert rows == [
        {"project_id": "product", "request_id": "adopt-1", "native_id": "native-owned", "title": "Preferred title", "archived": False,
         "running": True, "source": "host-pi", "provenance": "associated"},
        {"native_id": "native-free", "title": "Unassigned", "running": False, "archived": False, "source": "native", "provenance": "unassigned"},
    ]
    assert "checkout" not in json.dumps(rows)

    class Mismatch(Client):
        def associate(self, **body): return "different-native"
    refused = HostPi(host_owner_config(), PrivateStore(tmp_path / "mismatch.sqlite"), Projects(), Access(), Mismatch())
    with pytest.raises(ObservatoryError, match="unavailable"):
        refused.associate(session, "product", "adopt-2", "native-owned")
    assert refused.list(session, "product")[0]["provenance"] == "unassigned"


def test_host_owner_allows_context_only_roots_without_task_runner_authority(tmp_path):
    from anvil_serving.observability.dashboard.access import ConnectBinding, Principal, Session
    from anvil_serving.workbench_app.host_pi import HostPi
    from anvil_serving.workbench_app.store import PrivateStore

    class Access:
        def permit(self, session, resource, action=None): pass
    class Projects:
        def project(self, session, project_id):
            return {"id": "product", "roots": [{"id": "readonly", "path": str(tmp_path / "readonly"), "task_access": "read-only"}], "primary_root_id": "readonly"}
    class Client:
        def ensure(self, **body): return "native-context"
    session = Session("key", "csrf", Principal("alice", "alice", "operator", frozenset({"serve-a"}), frozenset()), 0,
                      ConnectBinding("alice", "resource", "issuer", "subject", "proof", "admin"))
    owner = HostPi(host_owner_config(), PrivateStore(tmp_path / "private.sqlite"), Projects(), Access(), Client())
    assert owner.create(session, "product", "new-thread")["native_id"] == "native-context"


def test_host_inventory_authority_binds_connect_and_configuration(site):  # noqa: F811
    from anvil_serving.workbench_app.host_pi import HostPi
    console, _, mode = site
    if mode != "connect":
        pytest.skip("Connect administrator grant boundary")
    config = console.workbench.config
    config["host_pi"] = host_config() | {"token_ref": "BRIDGE_TOKEN"}
    config["projects"] = [{"id": "product", "resource_id": "serve-a"}]
    session = next(iter(console.access._sessions.values()))
    first = HostPi.authority(config, console.workbench.projects, console.access, session)
    changed = replace(session, connect_binding=replace(session.connect_binding, epoch="c" * 64))
    assert HostPi.authority(config, console.workbench.projects, console.access, changed)["authority_key"] != first["authority_key"]
    config["host_pi"]["version"] = "0.9.1"
    assert HostPi.authority(config, console.workbench.projects, console.access, session)["authority_key"] != first["authority_key"]
    denied = replace(session, principal=replace(session.principal, resources=frozenset({"*"})))
    from anvil_serving.observability.dashboard.contracts import ObservatoryError
    with pytest.raises(ObservatoryError) as error:
        HostPi.authority(config, console.workbench.projects, console.access, denied)
    assert error.value.status == 403
