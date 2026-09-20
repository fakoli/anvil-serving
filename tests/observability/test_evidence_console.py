from dataclasses import replace
import json
import time

import pytest

from anvil_serving.observability.dashboard.access import Session
from anvil_serving.observability.dashboard.console import Console, _run_bindings
from anvil_serving.observability.dashboard.contracts import ObservatoryError


def test_catalog_without_safe_descriptors_is_unavailable_before_spawning(tmp_path, monkeypatch):
    from anvil_serving.observability.dashboard import evidence_runs

    monkeypatch.delattr(evidence_runs.os, "O_NOFOLLOW", raising=False)
    source = evidence_runs.EvidenceRuns({"root": str(tmp_path), "owner_id": "catalog", "resource_id": "runs"})
    with pytest.raises(ObservatoryError) as error:
        source.page(authority_key="reader")
    assert error.value.code == "owner_unavailable" and source._active is None
    source.close()


@pytest.fixture
def catalog_console(tmp_path):
    config = {
        "origin": "https://console.example.test", "base_path": "/", "operate": False,
        "users": [{"id": name, "username": name, "role": "viewer",
                   "resources": ["evidence.runs"] if name != "denied" else [], "actions": []}
                  for name in ("first", "second", "denied")],
        "authentication": {"mode": "legacy"}, "state_path": str(tmp_path / "intents.sqlite3"),
        "prometheus_url": "http://127.0.0.1:9090", "inventory": {},
        "runs": {"evidence": {"resource_id": "evidence.runs", "owner_id": "catalog-owner",
                              "root": str(tmp_path / "private-catalog")}},
    }
    console = Console(config, metrics=object(), authenticate=lambda _user, _password: False)
    sessions = {name: Session(name, "csrf", console.access.users[name], time.time() + 60)
                for name in ("first", "second", "denied")}
    try:
        yield console, sessions
    finally:
        console.close()


def test_catalog_authorization_precedes_cursor_owner_and_stale_cache(catalog_console, monkeypatch):
    console, sessions = catalog_console
    calls = []

    def page(**kwargs):
        calls.append(kwargs)
        return {"items": [{"id": "run-fixture", "native_id": "artifact-fixture", "source": "evidence",
                            "kind": "imported", "freshness": "fresh"}], "next_cursor": None,
                "sources": [{"id": "catalog-owner", "status": "fresh", "deadline_seconds": 2.0}]}

    monkeypatch.setattr(console.evidence_runs, "page", page)
    sources = console.read("run-sources", {}, sessions["first"])
    assert any(item["id"] == "evidence" for item in sources["items"])
    assert "private-catalog" not in json.dumps(sources)
    assert all(item["id"] != "evidence" for item in console.read("run-sources", {}, sessions["denied"])["items"])
    with pytest.raises(ObservatoryError) as denied:
        console.read("runs/evidence", {"cursor": "x" * 129}, sessions["denied"])
    assert denied.value.status == 403
    assert calls == []
    fresh = console.read("runs/evidence", {}, sessions["first"])
    assert fresh["items"][0]["freshness"] == "fresh"

    def unavailable(**kwargs):
        calls.append(kwargs)
        raise ObservatoryError("owner_unavailable", "The catalog is unavailable.", 503)

    monkeypatch.setattr(console.evidence_runs, "page", unavailable)
    stale = console.read("runs/evidence", {}, sessions["first"])
    assert stale["sources"][0]["status"] == "stale"
    assert stale["items"][0]["freshness"] == "stale"
    second = console.read("runs/evidence", {}, sessions["second"])
    assert second["items"] == []
    assert calls[-1]["authority_key"] != calls[0]["authority_key"]
    revoked = replace(sessions["first"], principal=replace(sessions["first"].principal, resources=frozenset()))
    count = len(calls)
    with pytest.raises(ObservatoryError) as denied:
        console.read("runs/evidence", {}, revoked)
    assert denied.value.status == 403
    assert len(calls) == count
    assert all(item["id"] != "evidence" for item in console.read("run-sources", {}, revoked)["items"])


def test_catalog_invalid_cursor_is_not_replaced_by_stale_success(catalog_console, monkeypatch):
    console, sessions = catalog_console

    def invalid(**_kwargs):
        raise ObservatoryError("invalid_run_cursor", "Refresh the catalog page.", 409)

    monkeypatch.setattr(console.evidence_runs, "page", invalid)
    with pytest.raises(ObservatoryError, match="Refresh"):
        console.read("runs/evidence", {"cursor": "invalid"}, sessions["first"])


def test_catalog_detail_and_compare_authorize_before_refs(catalog_console, monkeypatch):
    console, sessions = catalog_console
    calls = []

    def detail(**kwargs):
        calls.append(("detail", kwargs))
        return {"id": "run", "source": "evidence"}

    def compare(**kwargs):
        calls.append(("compare", kwargs))
        return {"comparable": True, "artifacts": [], "differences": [],
                "unknown_fields": [], "invalid_artifacts": []}

    monkeypatch.setattr(console.evidence_runs, "detail", detail)
    monkeypatch.setattr(console.evidence_runs, "compare", compare)
    detail_query = {"artifact_id": "artifact-" + "a" * 64, "sha256": "b" * 64}
    compare_query = {
        "refs": json.dumps([
            {"owner_id": "catalog-owner", "artifact_id": "artifact-" + "a" * 64, "sha256": "b" * 64},
            {"owner_id": "catalog-owner", "artifact_id": "artifact-" + "c" * 64, "sha256": "d" * 64},
        ]),
    }
    with pytest.raises(ObservatoryError) as denied_detail:
        console.read("runs/evidence/detail", detail_query, sessions["denied"])
    assert denied_detail.value.status == 403 and calls == []
    with pytest.raises(ObservatoryError) as denied:
        console.read("runs/evidence/compare", {"refs": "not-json"}, sessions["denied"])
    assert denied.value.status == 403 and calls == []
    detail_result = console.read("runs/evidence/detail", detail_query, sessions["first"])
    compare_result = console.read("runs/evidence/compare", compare_query, sessions["first"])
    assert detail_result["source"] == "evidence" and compare_result["comparable"] is True
    assert [kind for kind, _kwargs in calls] == ["detail", "compare"]
    revoked = replace(sessions["first"], principal=replace(
        sessions["first"].principal, resources=frozenset()
    ))
    for route, query in (("runs/evidence/detail", detail_query),
                         ("runs/evidence/compare", compare_query)):
        with pytest.raises(ObservatoryError) as denied_after_revoke:
            console.read(route, query, revoked)
        assert denied_after_revoke.value.status == 403
    assert [kind for kind, _kwargs in calls] == ["detail", "compare"]


@pytest.mark.parametrize("root", ["relative", "/", "/catalog/../private", "/catalog\nprivate"])
def test_catalog_requires_a_bounded_declared_root(root):
    with pytest.raises(ValueError):
        _run_bindings({"runs": {"evidence": {"root": root, "owner_id": "catalog", "resource_id": "read.runs"}}})


def test_catalog_and_benchmark_cannot_share_cache_authority(tmp_path):
    with pytest.raises(ValueError, match="distinct dedicated"):
        _run_bindings({"runs": {
            "benchmark": {"resource_id": "read.runs"},
            "evidence": {"resource_id": "read.runs", "owner_id": "catalog", "root": str(tmp_path)},
        }})


@pytest.mark.parametrize("kind", ["experiment", "serve", "host"])
@pytest.mark.parametrize("source", ["benchmark", "evidence"])
def test_run_grants_cannot_reuse_controller_resource_authority(tmp_path, kind, source):
    binding = {"resource_id": "read.runs"}
    if source == "evidence":
        binding.update(owner_id="catalog", root=str(tmp_path))
    with pytest.raises(ValueError, match="distinct dedicated"):
        _run_bindings({"runs": {source: binding}, "controller": {
            "resources": [{"id": "read.runs", "kind": kind}],
        }})
