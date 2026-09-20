from __future__ import annotations

import threading
import time
import os
from types import SimpleNamespace

import pytest

from anvil_serving.observability.dashboard.contracts import ObservatoryError
from anvil_serving.workbench_app.pi_sessions import PiConversationService, PiSessionError, PiSessionStore, PiTaskBinding
from anvil_serving.workbench_app.store import PrivateStore
from anvil_serving.workbench_app.workspace_runs import WorkspaceRuns


_POSIX_METADATA = os.name == "posix" and all(hasattr(os, name) for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"))


class _Principal:
    def __init__(self, identity="alice", resources=()):
        self.identity, self.resources = identity, set(resources)


class _Projects:
    config = {"projects": [
        {"id": "alpha", "resource_id": "project-alpha"},
        {"id": "beta", "resource_id": "project-beta"},
    ]}

    def project(self, session, project_id):
        project = next(item for item in self.config["projects"] if item["id"] == project_id)
        if project["resource_id"] not in session.principal.resources:
            raise ObservatoryError("forbidden", "not granted", 403)
        return project


def _session(resources=("project-alpha",)):
    return SimpleNamespace(principal=_Principal(resources=resources))


def _binding(index, project="alpha"):
    return {"id": f"binding-{index:03d}", "owner": "alice", "project_id": project,
            "task_id": f"task:{index:03d}", "lease_id": f"lease-{index:03d}",
            "provider_id": "fixture", "status": "ready"}


def test_task_pages_are_authorized_stable_and_do_not_materialize_large_bodies(tmp_path):
    store = PrivateStore(tmp_path / "private.sqlite")
    for index in range(105):
        store.put("task-binding", "alice", f"binding-{index:03d}", _binding(index))
    oversized = _binding(999) | {"padding": "x" * (80 * 1024)}
    store.put("task-binding", "alice", "binding-999", oversized)
    runs = WorkspaceRuns(store, _Projects(), None, clock=lambda: 1000, cursor_secret=b"x" * 32)
    session = _session()
    first = runs.task_page(session)
    assert len(first["items"]) == 100
    assert first["sources"][0]["partial"] is True
    store.put("task-binding", "alice", "binding-new", _binding(777) | {"id": "binding-new"})
    second = runs.task_page(session, cursor=first["next_cursor"])
    native_ids = [row["native_id"] for row in first["items"] + second["items"]]
    assert len(native_ids) == len(set(native_ids)) == 105
    assert "binding-new" not in native_ids and "binding-999" not in native_ids
    assert {row["status"] for row in first["items"]} == {"retained"}
    store.close()


def test_task_cursor_is_bound_to_current_project_grants_before_owner_read(tmp_path, monkeypatch):
    store = PrivateStore(tmp_path / "private.sqlite")
    store.put("task-binding", "alice", "binding-a", _binding(1))
    runs = WorkspaceRuns(store, _Projects(), None, clock=lambda: 1000, cursor_secret=b"x" * 32)
    session = _session()
    first = runs.task_page(session)
    session.principal.resources.clear()
    monkeypatch.setattr(store, "task_binding_run_page", lambda *_a, **_k: pytest.fail("revoked caller reached owner"))
    assert runs.task_page(session, cursor=first["next_cursor"])["items"] == []
    store.close()


def test_source_discovery_returns_only_currently_granted_projects_without_owner_reads(tmp_path, monkeypatch):
    store = PrivateStore(tmp_path / "private.sqlite")
    runs = WorkspaceRuns(store, _Projects(), None)
    monkeypatch.setattr(store, "task_binding_run_page", lambda *_a, **_k: pytest.fail("discovery read owner state"))
    sources = runs.sources(_session())
    assert sources == {"items": [{"id": "workspace-tasks", "label": "Task bindings", "kind": "workspace", "resource_ids": ["project-alpha"]}]}
    assert runs.sources(_session(()))["items"] == []
    store.close()


def test_task_owner_lock_has_a_deadline(tmp_path):
    store = PrivateStore(tmp_path / "private.sqlite")
    store.put("task-binding", "alice", "binding-a", _binding(1))
    ready, release = threading.Event(), threading.Event()

    def hold():
        with store.lock:
            ready.set()
            release.wait(1)

    thread = threading.Thread(target=hold)
    thread.start()
    assert ready.wait(1)
    started = time.monotonic()
    with pytest.raises(ObservatoryError, match="busy"):
        store.task_binding_run_page("alice", frozenset({"alpha"}), deadline_seconds=0.02)
    assert time.monotonic() - started < 0.2
    release.set()
    thread.join()
    store.close()


@pytest.mark.skipif(not _POSIX_METADATA, reason="requires safe POSIX metadata descriptors")
def test_pi_metadata_uses_authorized_index_and_never_returns_transcript_paths(tmp_path, monkeypatch):
    pi = PiSessionStore(tmp_path / "pi")
    binding = PiTaskBinding("alice", "alpha", "task:pi", "lease-pi", "runner", "fixture")
    created, _ = pi.create(binding, "start-pi", model_id="fixture-model", thinking_level="low")
    pi.append(created.session_id, "message", {"text": "private transcript"})
    runs = WorkspaceRuns(PrivateStore(tmp_path / "private.sqlite"), _Projects(), pi, clock=lambda: 1000)
    page = runs.pi_page(_session())
    assert page["items"][0]["native_id"] == created.session_id
    assert "official_session_file" not in page["items"][0]
    assert "private transcript" not in str(page)
    assert runs.pi_page(_session(("project-beta",)))["items"] == []


def test_workspace_cursor_stays_bounded_for_a_maximum_principal(tmp_path):
    principal = "p" * 192
    store = PrivateStore(tmp_path / "private.sqlite")
    for index in range(101):
        row = _binding(index) | {"owner": principal}
        store.put("task-binding", principal, row["id"], row)
    runs = WorkspaceRuns(store, _Projects(), None, clock=lambda: 1000, cursor_secret=b"x" * 32)
    session = SimpleNamespace(principal=_Principal(principal, {"project-alpha"}))
    cursor = runs.task_page(session)["next_cursor"]
    assert cursor is not None and len(cursor) <= 512
    assert len(runs.task_page(session, cursor=cursor)["items"]) == 1
    store.close()


@pytest.mark.skipif(not _POSIX_METADATA, reason="requires safe POSIX metadata descriptors")
def test_pi_startup_backfill_includes_stopped_legacy_sessions_and_repairs_optional_index(tmp_path):
    pi = PiSessionStore(tmp_path / "pi")
    binding = PiTaskBinding("alice", "alpha", "task:pi", "lease-pi", "runner", "fixture")
    stopped, _ = pi.create(binding, "start-stopped")
    stopped = pi._replace(stopped.session_id, status="stopped")
    second, _ = pi.create(binding, "start-second")
    index = pi._metadata_index_path("alice", "alpha")
    index.write_text("{", encoding="utf-8")
    pi._metadata_coverage.unlink()

    # A malformed optional projection cannot make the canonical write fail or
    # overwrite the index with just this one row.
    updated = pi.mark_running(second.session_id)
    assert updated.status == "running" and index.read_text(encoding="utf-8") == "{"

    reopened = PiSessionStore(tmp_path / "pi")
    PiConversationService(reopened, object(), lambda *_args: None,
                          allowed_models={}, allowed_thinking=frozenset())
    page = reopened.run_metadata_page("alice", frozenset({"alpha"}))
    assert page["partial"] is False
    assert {row["session_id"] for row in page["items"]} == {stopped.session_id, second.session_id}
    assert next(row for row in page["items"] if row["session_id"] == stopped.session_id)["status"] == "stopped"


@pytest.mark.skipif(not _POSIX_METADATA or not hasattr(os, "mkfifo"), reason="requires safe POSIX metadata descriptors")
def test_pi_metadata_io_worker_is_reaped_at_its_deadline(tmp_path):
    pi = PiSessionStore(tmp_path / "pi")
    path = pi._metadata_index_path("alice", "alpha")
    os.mkfifo(path)
    started = time.monotonic()
    page = pi.run_metadata_page("alice", frozenset({"alpha"}), deadline_seconds=0.2)
    assert page["items"] == () and page["partial"] is True
    assert time.monotonic() - started < 1.0


def test_pi_metadata_setup_failure_releases_the_source_slot(tmp_path, monkeypatch):
    from anvil_serving.workbench_app import pi_sessions

    pi = PiSessionStore(tmp_path / "pi")
    monkeypatch.setattr(pi_sessions.tempfile, "mkstemp", lambda **_kwargs: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(PiSessionError):
        pi.run_metadata_page("alice", frozenset({"alpha"}), deadline_seconds=0.2)
    assert not pi._metadata_slot.locked() and pi._metadata_poisoned is False


def test_missing_safe_descriptor_primitive_keeps_canonical_sessions_durable(tmp_path, monkeypatch):
    from anvil_serving.workbench_app import pi_sessions

    monkeypatch.delattr(pi_sessions.os, "O_NOFOLLOW", raising=False)
    pi = PiSessionStore(tmp_path / "pi")
    binding = PiTaskBinding("alice", "alpha", "task:pi", "lease-pi", "runner", "fixture")
    session, created = pi.create(binding, "start-portable")
    assert created is True and pi.get(session.session_id).session_id == session.session_id
    with pytest.raises(PiSessionError, match="unavailable"):
        pi.run_metadata_page("alice", frozenset({"alpha"}))
