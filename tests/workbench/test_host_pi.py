"""The host UI is an exact Connect-owner entry, separate from task authority."""

import json
from dataclasses import replace

import pytest

from anvil_serving.workbench_app.config import validate_config
from test_service import site  # noqa: F401


def host_config():
    return {"id": "host-pi", "resource_id": "serve-a", "origin": "https://pi.example.test",
            "owner_subject": "alice", "version": "0.9.0", "runtime_sha256": "a" * 64}


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
