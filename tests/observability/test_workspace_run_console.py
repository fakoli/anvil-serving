"""Workspace run cache authority is recomputed before every owner read."""

from dataclasses import replace
import time

import pytest

from anvil_serving.observability.dashboard.access import Session
from anvil_serving.observability.dashboard.console import Console
from anvil_serving.observability.dashboard.contracts import ObservatoryError


def test_workspace_sources_pages_and_stale_rows_follow_current_project_grants(tmp_path, monkeypatch):
    config = {
        "origin": "https://console.example.test", "base_path": "/", "operate": False,
        "users": [{"id": "reader", "username": "reader", "role": "viewer",
                   "resources": ["project-a", "project-b"], "actions": []}],
        "authentication": {"mode": "legacy"}, "state_path": str(tmp_path / "intents.sqlite"),
        "prometheus_url": "http://127.0.0.1:9090", "inventory": {},
        "workbench": {"state_path": str(tmp_path / "workspace.sqlite"), "projects": [
            {"id": key, "label": key, "resource_id": key, "checkout": str(tmp_path / key),
             "anvil_binary": str(tmp_path / "anvil")} for key in ("project-a", "project-b")
        ]},
    }
    console = Console(config, metrics=object(), authenticate=lambda *_: False)
    session = Session("session", "csrf", console.access.users["reader"], time.time() + 60)
    try:
        for project in ("project-a", "project-b"):
            for index in range(51):
                key = f"{project}-{index}"
                console.workbench.store.put("task-binding", "reader", key, {
                    "id": key, "owner": "reader", "project_id": project,
                    "task_id": f"task-{index}", "status": "ready",
                })
        sources = console.read("run-sources", {}, session)["items"]
        assert {item["id"] for item in sources} == {"operations", "workspace-tasks"}
        first = console.read("runs/workspace-tasks", {}, session)
        second = console.read("runs/workspace-tasks", {"cursor": first["next_cursor"]}, session)
        ids = [item["id"] for item in first["items"] + second["items"]]
        assert len(ids) == len(set(ids)) == 102
        denied = replace(session, principal=replace(session.principal, resources=frozenset()))
        reduced = replace(session, principal=replace(session.principal, resources=frozenset({"project-a"})))
        with pytest.raises(ObservatoryError) as old_cursor:
            console.read("runs/workspace-tasks", {"cursor": first["next_cursor"]}, reduced)
        assert old_cursor.value.status == 400
        calls = []

        def unavailable(*args, **kwargs):
            calls.append((args, kwargs))
            raise ObservatoryError("workspace_source_unavailable", "Owner unavailable.", 503)

        monkeypatch.setattr(console.workbench, "workspace_run_page", unavailable)
        retained = console.read("runs/workspace-tasks", {}, session)
        assert len(retained["items"]) == 100
        assert all(item["freshness"] == "stale" for item in retained["items"])
        reduced_page = console.read("runs/workspace-tasks", {}, reduced)
        assert reduced_page["items"] == []
        assert reduced_page["sources"][0]["status"] == "unavailable"
        assert len(calls) == 2
        with pytest.raises(ObservatoryError) as revoked:
            console.read("runs/workspace-tasks", {"cursor": "x" * 1000}, denied)
        assert revoked.value.status == 403 and len(calls) == 2
        assert {item["id"] for item in console.read("run-sources", {}, denied)["items"]} == {"operations"}
        with pytest.raises(ObservatoryError) as absent:
            console.read("runs/workspace-pi", {}, session)
        assert absent.value.status == 403 and len(calls) == 2
    finally:
        console.close()


def test_native_run_stale_cache_marks_loaded_history_updates_stale():
    from types import SimpleNamespace
    cached = {"items": [{"id": "head", "freshness": "fresh"}], "sources": [{"status": "fresh"}],
              "refresh": {"items": [{"id": "history", "freshness": "fresh", "status": "running"}]}}
    result = Console._stale_runs(SimpleNamespace(_cached_runs=lambda _: cached), "cache", "workspace-host-pi")
    assert result["refresh"]["items"][0] == {"id": "history", "freshness": "stale", "status": "running"}
    assert result["items"][0]["freshness"] == result["sources"][0]["status"] == "stale"


def test_native_workspace_source_is_discovered_read_paged_and_reauthorized(tmp_path, monkeypatch):
    config = {
        "origin": "https://console.example.test", "base_path": "/", "operate": False,
        "users": [{"id": "reader", "username": "reader", "role": "viewer",
                   "resources": ["project-a"], "actions": []}],
        "authentication": {"mode": "legacy"}, "state_path": str(tmp_path / "intents.sqlite"),
        "prometheus_url": "http://127.0.0.1:9090", "inventory": {},
        "workbench": {"state_path": str(tmp_path / "workspace.sqlite"), "projects": [{
            "id": "project-a", "label": "project-a", "resource_id": "project-a",
            "checkout": str(tmp_path / "project-a"), "anvil_binary": str(tmp_path / "anvil"),
        }]},
    }
    console = Console(config, metrics=object(), authenticate=lambda *_: False)
    session = Session("session", "csrf", console.access.users["reader"], time.time() + 60)
    available = [{"id": "workspace-host-pi", "label": "Native Pi sessions", "kind": "workspace",
                  "resource_ids": ["host-pi", "project-a"], "authority_key": "owner-grant"}]
    calls = []

    def sources(_session):
        return {"items": available}

    def page(_session, source, *, limit, cursor):
        calls.append((source, limit, cursor))
        assert source == "workspace-host-pi" and limit == 100
        row = {"id": "native-" + ("second" if cursor else "first"), "freshness": "fresh"}
        next_cursor = None if cursor else "page-1"
        return {"items": [row], "next_cursor": next_cursor,
                "sources": [{"id": source, "status": "fresh", "deadline_seconds": 2,
                             "truncated": next_cursor is not None, "partial": False}]}

    try:
        monkeypatch.setattr(console.workbench, "workspace_run_sources", sources)
        monkeypatch.setattr(console.workbench, "workspace_run_page", page)
        assert "workspace-host-pi" in {item["id"] for item in console.read("run-sources", {}, session)["items"]}
        first = console.read("runs/workspace-host-pi", {}, session)
        second = console.read("runs/workspace-host-pi", {"cursor": first["next_cursor"]}, session)
        assert [item["id"] for item in first["items"] + second["items"]] == ["native-first", "native-second"]
        available.clear()
        with pytest.raises(ObservatoryError) as denied:
            console.read("runs/workspace-host-pi", {"cursor": first["next_cursor"]}, session)
        assert denied.value.status == 403
        assert calls == [("workspace-host-pi", 100, None), ("workspace-host-pi", 100, "page-1")]
    finally:
        console.close()
