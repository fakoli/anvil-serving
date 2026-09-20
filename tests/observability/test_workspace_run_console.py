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
             "anvil_binary": "/opt/anvil/bin/anvil"} for key in ("project-a", "project-b")
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
