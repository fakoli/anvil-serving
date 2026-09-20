"""The host UI is an exact Connect-owner entry, separate from task authority."""

import json
from dataclasses import replace

import pytest

from anvil_serving.workbench_app.config import validate_config
from test_service import site  # noqa: F401


def host_config():
    return {"id": "host-pi", "resource_id": "serve-a", "origin": "https://pi.example.test",
            "owner_subject": "alice", "version": "0.9.0", "runtime_sha256": "a" * 64}


def host_owner_config(*names):
    return {"host_pi": host_config() | {"token_ref": "BRIDGE_TOKEN"}, "projects": [
        {"id": name, "resource_id": "serve-a"} for name in (names or ("product",))
    ]}


@pytest.mark.parametrize("change", [
    {"origin": "http://pi.example.test"}, {"origin": "https://pi.example.test/path"},
    {"origin": "https://user:password@pi.example.test"}, {"origin": "https://pi.example.test;*"},
    {"origin": "https://pi.example.test\r\nInjected:value"}, {"origin": "https://localhost"},
    {"origin": "https://pi.example.test:99999"}, {"version": "latest"},
    {"runtime_sha256": "unverified"}, {"owner_subject": ""}, {"owner_subject": "a\nb"},
])
def test_host_pin_rejects_unsafe_or_unqualified_configuration(tmp_path, change):
    with pytest.raises(ValueError):
        validate_config({"state_path": str(tmp_path / "state"), "host_pi": host_config() | change})


def test_host_catalog_requires_connect_subject_and_resource(site):  # noqa: F811
    console, call, mode = site
    host = host_config()
    validate_config({"state_path": console.workbench.config["state_path"], "host_pi": host})
    console.workbench.config["host_pi"] = host
    public = call("GET", "catalog")[1]["data"]["host_pi"]
    assert public["available"] is (mode == "connect")
    assert ("origin" in public) is (mode == "connect")
    assert "owner_subject" not in public and "runtime_sha256" not in public

    # A shared native role/profile must not confer another Connect subject's host.
    host["owner_subject"] = "another-subject"
    denied = call("GET", "catalog")[1]["data"]["host_pi"]
    assert denied["available"] is False and host["origin"] not in json.dumps(denied)
    host["owner_subject"] = "alice"
    host["resource_id"] = "ungranted-host"
    denied = call("GET", "catalog")[1]["data"]["host_pi"]
    assert denied["available"] is False and "origin" not in denied


def test_host_catalog_requires_an_explicit_grant_even_for_wildcard_owner(site):  # noqa: F811
    console, _, mode = site
    if mode != "connect":
        pytest.skip("Connect owner grant boundary")
    console.workbench.config["host_pi"] = host_config()
    session = next(iter(console.access._sessions.values()))
    for grants, available in (({"*"}, False), ({"unrelated"}, False), ({"*", "serve-a"}, True)):
        changed = replace(session, principal=replace(session.principal, resources=frozenset(grants)))
        host = console.workbench.catalog(changed)["host_pi"]
        assert host["available"] is available and ("origin" in host) is available


def test_host_owner_retries_the_same_native_request_and_keeps_only_safe_metadata(tmp_path):
    from anvil_serving.observability.dashboard.access import ConnectBinding, Principal, Session
    from anvil_serving.workbench_app.host_pi import HostPi
    from anvil_serving.workbench_app.store import PrivateStore

    class Access:
        def permit(self, session, resource, action=None):
            assert resource in session.principal.resources
    class Projects:
        def project(self, session, project_id):
            assert project_id == "product"
            return {"id": "product", "roots": [{"id": "primary", "path": str(tmp_path / "private-checkout"), "task_access": "read-write"}], "primary_root_id": "primary"}
    class Client:
        def __init__(self): self.calls = []
        def ensure(self, **body): self.calls.append(body); return "native-thread"
    session = Session("key", "csrf", Principal("alice", "alice", "operator", frozenset({"serve-a", "project-a"}), frozenset()), 0,
                      ConnectBinding("alice", "resource", "issuer", "subject", "proof", "operator"))
    client = Client()
    owner = HostPi(host_owner_config(), PrivateStore(tmp_path / "private.sqlite"), Projects(), Access(), client)
    first = owner.create(session, "product", "retry-1", "Safe title")
    assert owner.create(session, "product", "retry-1", "Ignored title") == first
    assert len(client.calls) == 1 and set(first) == {"project_id", "request_id", "native_id", "title", "archived"}
    assert "private-checkout" not in json.dumps(first)
    assert owner.archive(session, "product", "retry-1", True)["archived"] is True
    assert owner.archive(session, "product", "retry-1", False)["archived"] is False


def test_host_owner_persists_the_trusted_root_before_a_lost_response_and_rejects_wildcards(tmp_path):
    from anvil_serving.observability.dashboard.access import ConnectBinding, Principal, Session
    from anvil_serving.observability.dashboard.contracts import ObservatoryError
    from anvil_serving.workbench_app.host_pi import HostPi
    from anvil_serving.workbench_app.store import PrivateStore

    class Access:
        def permit(self, session, resource, action=None):
            assert resource in session.principal.resources
    class Projects:
        roots = [{"id": "first", "path": str(tmp_path / "first"), "task_access": "read-write"}]
        def project(self, session, project_id):
            return {"id": "product", "roots": self.roots, "primary_root_id": self.roots[0]["id"]}
    class Client:
        def __init__(self): self.calls = 0; self.body = []
        def ensure(self, **body):
            self.calls += 1; self.body.append(body)
            if self.calls == 1:
                raise ObservatoryError("host_pi_unavailable", "lost", 503)
            return "native-thread"
    def session(resources):
        return Session("key", "csrf", Principal("alice", "alice", "operator", frozenset(resources), frozenset()), 0,
                       ConnectBinding("alice", "resource", "issuer", "subject", "proof", "operator"))

    client, projects = Client(), Projects()
    owner = HostPi(host_owner_config(), PrivateStore(tmp_path / "private.sqlite"), projects, Access(), client)
    with pytest.raises(ObservatoryError, match="lost"):
        owner.create(session({"serve-a"}), "product", "create-once")
    projects.roots = [{"id": "later", "path": str(tmp_path / "later"), "task_access": "read-write"}]
    assert owner.create(session({"serve-a"}), "product", "create-once")["native_id"] == "native-thread"
    assert client.body == [
        {"request_key": client.body[0]["request_key"], "project_id": "product", "cwd": str(tmp_path / "first")},
        {"request_key": client.body[0]["request_key"], "project_id": "product", "cwd": str(tmp_path / "first")},
    ]
    with pytest.raises(ObservatoryError, match="does not grant"):
        owner.create(session({"*"}), "product", "denied")
    assert client.calls == 2


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
                      ConnectBinding("alice", "resource", "issuer", "subject", "proof", "operator"))
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
                      ConnectBinding("alice", "resource", "issuer", "subject", "proof", "operator"))
    owner = HostPi(host_owner_config(), PrivateStore(tmp_path / "private.sqlite"), Projects(), Access(), Client())
    assert owner.create(session, "product", "new-thread")["native_id"] == "native-context"


def test_host_owner_hides_threads_bound_to_other_projects_and_freezes_operation_kind(tmp_path):
    from anvil_serving.observability.dashboard.access import ConnectBinding, Principal, Session
    from anvil_serving.observability.dashboard.contracts import ObservatoryError
    from anvil_serving.workbench_app.host_pi import HostPi
    from anvil_serving.workbench_app.store import PrivateStore

    class Access:
        def permit(self, session, resource, action=None): pass
    class Projects:
        def project(self, session, project_id):
            return {"id": project_id, "roots": [{"id": "primary", "path": str(tmp_path / project_id), "task_access": "read-only"}], "primary_root_id": "primary"}
    class Client:
        def ensure(self, **body): return "bound-other"
        def associate(self, **body): return body["native_id"]
        def list_native(self):
            return [{"native_id": "bound-other", "title": "Other project", "running": False}, {"native_id": "free", "title": "Free", "running": False}]
    session = Session("key", "csrf", Principal("alice", "alice", "operator", frozenset({"serve-a"}), frozenset()), 0,
                      ConnectBinding("alice", "resource", "issuer", "subject", "proof", "operator"))
    owner = HostPi(host_owner_config("other", "current"), PrivateStore(tmp_path / "private.sqlite"), Projects(), Access(), Client())
    owner.create(session, "other", "same-request")
    assert owner.list(session, "current") == [{"native_id": "free", "title": "Free", "running": False, "archived": False, "source": "native", "provenance": "unassigned"}]
    with pytest.raises(ObservatoryError, match="another host Pi operation"):
        owner.associate(session, "other", "same-request", "bound-other")


def test_host_inventory_authority_binds_connect_and_configuration(site):  # noqa: F811
    from anvil_serving.workbench_app.host_pi import HostPi
    console, _, mode = site
    if mode != "connect":
        pytest.skip("Connect owner grant boundary")
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


def test_host_association_inventory_never_silently_truncates(tmp_path):
    from anvil_serving.workbench_app.store import PrivateStore
    from anvil_serving.observability.dashboard.contracts import ObservatoryError
    store = PrivateStore(tmp_path / "state.sqlite")
    for i in range(256):
        store.put("host-pi-thread", "owner", str(i), {"native_id": f"id-{i}"})
    assert len(store.host_pi_associations("owner")) == 256
    store.put("host-pi-thread", "owner", "overflow", {})
    with pytest.raises(ObservatoryError, match="bound"):
        store.host_pi_associations("owner")
    store.close()


def test_host_association_corruption_cannot_redirect_reads_retries_or_edits(tmp_path):
    from types import SimpleNamespace
    from anvil_serving.observability.dashboard.contracts import ObservatoryError
    from anvil_serving.workbench_app.host_pi import HostPi
    from anvil_serving.workbench_app.store import PrivateStore
    store = PrivateStore(tmp_path / "state.sqlite")
    project = {"id": "product", "primary_root_id": "primary", "roots": [{"id": "primary", "path": str(tmp_path)}]}
    owner = HostPi(host_owner_config(), store,
                   SimpleNamespace(project=lambda *_: project), SimpleNamespace(permit=lambda *_: None),
                   SimpleNamespace(ensure=lambda **_: pytest.fail("corruption reached native owner")))
    session = SimpleNamespace(principal=SimpleNamespace(identity="alice", resources={"serve-a"}), connect_binding=SimpleNamespace(subject="alice"))
    key = owner._request_key("alice", "product", "request")
    poisoned = owner._new_row(project | {"id": "foreign"}, "other-request", "Foreign title", None, "create")
    store.put("host-pi-thread", "alice", key, poisoned)
    for action in (
        lambda: owner.reopen(session, "product", "request"),
        lambda: owner.create(session, "product", "request"),
        lambda: owner.rename(session, "product", "request", "Changed"),
        lambda: owner.archive(session, "product", "request", True),
    ):
        with pytest.raises(ObservatoryError, match="does not match"):
            action()
        assert store.get("host-pi-thread", "alice", key) == poisoned
    store.close()
