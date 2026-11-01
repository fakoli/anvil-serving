from dataclasses import replace
import http.client
import json
from pathlib import Path

import pytest

from anvil_serving import jev
from tests.observability.test_observatory_fallback import fallback_site as fallback_site
from tests.workbench.test_service import site as site  # real authenticated HTTP fixture


def settings():
    return jev.validate_policy({"enabled": True, "capabilities": list(jev.CAPABILITIES), "allow_api": True,
                                "allow_export": True, "anvil_binary": str(Path(__file__).absolute())})


def body(resource="serve-a"):
    return {"resource_id": resource, "generation": "request-a", "allow_export": True,
            "input": {"observation": "Selected synthetic 401 observation"}}


@pytest.fixture
def fallback_advice(fallback_site):
    console, call, state, _ = fallback_site
    # Narrow the real fallback login's grant so this exercises resource ACLs,
    # not only the wildcard owner used by the underlying fallback fixture.
    owner = replace(console.fallback_access.users["operator"], resources=frozenset({"serve-a"}))
    console.fallback_access.users["operator"] = owner
    console.fallback_access.users_by_id[owner.identity] = owner
    status, result, cookie = call("POST", "session", body={"username": "operator", "password": "accepted"})
    assert status == 200
    assert not console.access._sessions and console.fallback_access._sessions
    def advise(value=None, *, extra=None):
        headers = {"Host": "backup.example.test", "Origin": "https://backup.example.test",
                   "Cookie": cookie.split(";", 1)[0], "X-CSRF-Token": result["data"]["csrf_token"],
                   "Content-Type": "application/json", **(extra or {})}
        connection = http.client.HTTPConnection("127.0.0.1", state["port"], timeout=5)
        try:
            connection.request("POST", "/workbench/api/workbench/v1/advisories/incident_triage",
                               body=json.dumps(body() if value is None else value), headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    return console, advise


def test_fallback_advice_rechecks_its_own_session_and_resource_grants(fallback_advice, monkeypatch):
    console, advise = fallback_advice
    monkeypatch.setattr(jev, "load_policy", settings)
    calls = []
    def offline(*args, **kwargs):
        assert kwargs["policy_reader"]() == settings()
        calls.append(args)
        return jev.report("incident_triage", "unavailable", "offline")
    monkeypatch.setattr(jev, "advise", offline)
    status, result = advise()
    assert status == 200 and result["data"]["annotation"]["advisory"]
    assert advise(extra={"X-CSRF-Token": "bad"})[0] == 403
    assert advise(body("other"))[0] == 403
    assert advise(extra={"Origin": "https://console.example.test"})[0] == 403
    assert len(calls) == 1 and not console.access._sessions


def test_disabled_fallback_advice_does_not_read_selected_content(fallback_advice, monkeypatch):
    _, advise = fallback_advice
    monkeypatch.setattr(jev, "load_policy", lambda: jev.validate_policy({}))
    monkeypatch.setattr(jev, "advise", lambda *_args, **_kwargs: pytest.fail("disabled advice dispatched"))
    status, result = advise(body() | {"input": "not inspected"})
    assert status == 200 and result["data"]["annotation"]["status"] == "disabled"


@pytest.mark.parametrize("phase", ["before_dispatch", "after_advice"])
def test_revoked_fallback_session_is_rejected_at_each_recheck(fallback_advice, monkeypatch, phase):
    console, advise = fallback_advice
    monkeypatch.setattr(jev, "load_policy", settings)
    reached = []
    def revoke():
        reached.append(phase)
        with console.fallback_access._lock:
            console.fallback_access._sessions.clear()
    if phase == "before_dispatch":
        original = jev.selected_input
        def prepare(value):
            revoke()
            return original(value)
        monkeypatch.setattr(jev, "selected_input", prepare)
        monkeypatch.setattr(jev.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("revoked fallback session dispatched"))
    else:
        def offline(*_args, **_kwargs):
            revoke()
            return jev.report("incident_triage", "unavailable", "offline", started=True)
        monkeypatch.setattr(jev, "advise", offline)
    assert advise()[0] == 401
    assert reached == [phase]


def test_attributed_advice_uses_same_auth_csrf_and_no_resource_actions(site, monkeypatch):
    console, call, _ = site
    monkeypatch.setattr(jev, "load_policy", settings)
    calls = []
    monkeypatch.setattr(jev, "advise", lambda *args, **kwargs: calls.append(args) or jev.report("incident_triage", "unavailable", "offline"))
    status, result = call("POST", "advisories/incident_triage", body())
    assert status == 200 and result["data"]["annotation"]["advisory"]
    assert result["data"]["generation"] == "request-a" and len(calls) == 1
    assert call("POST", "advisories/incident_triage", body("other"))[0] == 403
    assert call("POST", "advisories/incident_triage", body(), extra={"X-CSRF-Token": "bad"})[0] == 403
    assert len(calls) == 1
    assert "Selected synthetic" not in json.dumps(result)
