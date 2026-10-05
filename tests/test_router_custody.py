import json
import subprocess
from types import SimpleNamespace

import pytest

from anvil_serving import cli, router_manage


def observed():
    return {"container_id": "a" * 64, "image_id": "sha256:" + "b" * 64,
            "image_reference": "example/router:pinned", "started_at": "2026-10-05T12:00:00Z",
            "restart_count": 0, "compose_project": "anvil-serving", "compose_service": "router",
            "mounts": [{"Type": "volume", "Name": "router-keys", "Source": "/data/keys",
                        "Destination": "/var/lib/router-keys", "RW": True,
                        "Driver": "EXCLUDED_DRIVER"}],
            "Env": ["TOKEN=EXCLUDED_SECRET"], "Cmd": ["EXCLUDED_ARGUMENT"],
            "Labels": {"private": "EXCLUDED_LABEL"}}


def runner(payload=None, reference=None):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout=reference or "sha256:" + "b" * 64)
        return SimpleNamespace(returncode=0, stdout=json.dumps(observed() if payload is None else payload))

    return run, calls


def test_allowlisted_custody_and_local_reference_drift():
    run, calls = runner(reference="sha256:" + "c" * 64)
    result = router_manage._restart_custody("anvil-router", run)
    assert result["available"] is True
    assert result["local_reference_matches"] is False
    assert result["container_id"] == "a" * 64
    assert result["mounts"] == [{"type": "volume", "name": "router-keys", "source": "/data/keys",
                                  "destination": "/var/lib/router-keys", "read_only": False}]
    assert "EXCLUDED" not in json.dumps(result)
    assert len(calls) == 2
    assert all(call[1]["timeout"] == 5 for call in calls)
    assert ".Env" not in calls[0][0][3] and ".Cmd" not in calls[0][0][3]
    assert calls[1][0][-1] == "example/router:pinned"


@pytest.mark.parametrize("change", [
    {"container_id": "short"}, {"image_id": "not-an-id"}, {"mounts": [{}]},
    {"mounts": [{}] * 129}, {"image_reference": "--all"}, {"restart_count": True},
    {"compose_project": "control\ncharacter"},
])
def test_malformed_custody_never_claims_available(change):
    payload = observed() | change
    run, calls = runner(payload)
    assert router_manage._restart_custody("router", run)["available"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [FileNotFoundError("PRIVATE"), subprocess.TimeoutExpired("PRIVATE", 5)])
def test_custody_failure_does_not_expose_diagnostics(failure):
    def run(*args, **kwargs):
        raise failure
    assert router_manage._restart_custody("router", run) == {
        "available": False, "error": "router_custody_unavailable"}


def test_missing_reference_is_unknown_not_mismatch():
    run, calls = runner()
    def missing(argv, **kwargs):
        if argv[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="PRIVATE")
        return run(argv, **kwargs)
    result = router_manage._restart_custody("router", missing)
    assert result["available"] is True
    assert result["local_reference_matches"] is None


def test_human_status_unchanged_and_json_structured(monkeypatch, capsys):
    summary = {"container": "anvil-router", "docker_state": "running", "running": True,
               "health_status": 200, "health_url": "http://127.0.0.1:8000/", "ok": True,
               "custody": {"available": False, "error": "router_custody_unavailable"}}
    monkeypatch.setattr(router_manage, "status_summary", lambda *args, **kwargs: summary)
    assert cli.main(["router", "status"]) == 0
    assert capsys.readouterr().out == "router container: anvil-router\ndocker state:     running\n"
    assert cli.main(["router", "status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data"] == summary


def test_existing_health_fields_survive_custody_failure(monkeypatch):
    monkeypatch.setattr(router_manage, "docker_state", lambda *args, **kwargs: "running")
    monkeypatch.setattr(router_manage, "_health", lambda *args: 200)
    result = router_manage.status_summary("router", _run=lambda *args, **kwargs: None)
    assert result["running"] is True and result["health_status"] == 200 and result["ok"] is True
    assert result["custody"]["available"] is False
