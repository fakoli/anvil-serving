"""Bounded read projections for Workbench-owned task and Pi state."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from ..observability.dashboard.contracts import ObservatoryError, canonical, digest
from ..observability.dashboard.run_projection import projected_run_id
from .pi_sessions import PiSessionError


_TASK_SOURCE = "workspace-tasks"
_PI_SOURCE = "workspace-pi"
_OWNER_ID = "workbench-private"
_CURSOR_TTL = 60.0
_MAX_PAGE_BYTES = 128 * 1024
_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,128}\Z")


def _text(value: Any) -> str:
    if type(value) is not str or not _TEXT.fullmatch(value):
        raise ValueError("invalid workspace run metadata")
    return value


def _stamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class WorkspaceRuns:
    """Read-only pages from the existing Workbench task and Pi owners."""

    def __init__(self, store, projects, pi_store, *, clock=time.time, cursor_secret=None):
        self._store, self._projects, self._pi_store = store, projects, pi_store
        self._clock = clock
        self._cursor_secret = cursor_secret or secrets.token_bytes(32)

    def page(self, session, source: str, *, limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
        if source == _TASK_SOURCE:
            return self.task_page(session, limit=limit, cursor=cursor)
        if source == _PI_SOURCE:
            return self.pi_page(session, limit=limit, cursor=cursor)
        raise ObservatoryError("not_found", "This Workbench run source is unavailable.", 404)

    def sources(self, session) -> dict[str, Any]:
        """Return configured, currently authorized source descriptors only."""
        projects = self._authorized_projects(session)
        grants = sorted({project["resource_id"] for project in projects})
        if not grants:
            return {"items": []}
        items = [{"id": _TASK_SOURCE, "label": "Task bindings", "kind": "workspace",
                  "resource_ids": grants}]
        if self._pi_store is not None:
            items.append({"id": _PI_SOURCE, "label": "Managed Pi sessions", "kind": "workspace",
                          "resource_ids": grants})
        return {"items": items}

    def _authorized_projects(self, session) -> tuple[dict[str, str], ...]:
        projects = []
        # Project authorization is complete before an owner page/cursor is read.
        for configured in self._projects.config.get("projects", []):
            project_id, resource_id = configured["id"], configured["resource_id"]
            try:
                self._projects.project(session, project_id)
            except ObservatoryError as error:
                if error.status in {403, 404}:
                    continue
                raise
            projects.append({"id": project_id, "resource_id": resource_id})
        return tuple(projects)

    @staticmethod
    def _policy(session, projects: tuple[dict[str, str], ...]) -> str:
        return digest({"principal": session.principal.identity, "projects": projects})

    def _encode_cursor(self, *, policy: str, high_water: int, before: int) -> str:
        value = {"v": 1, "policy": policy, "high_water": high_water,
                 "before": before, "expires_at": self._clock() + _CURSOR_TTL}
        raw = canonical(value)
        signature = hmac.new(self._cursor_secret, raw, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode() + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

    def _decode_cursor(self, value: str | None, *, principal: str, policy: str) -> tuple[int, int] | None:
        if value is None:
            return None
        if type(value) is not str or not 1 <= len(value) <= 512 or value.count(".") != 1:
            raise ObservatoryError("invalid_workspace_run_cursor", "Select a current Workbench run page.", 400)
        try:
            encoded, signed = value.split(".")
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            signature = base64.urlsafe_b64decode(signed + "=" * (-len(signed) % 4))
            expected = hmac.new(self._cursor_secret, raw, hashlib.sha256).digest()
            parsed = json.loads(raw)
            if (not hmac.compare_digest(signature, expected) or parsed.get("v") != 1
                    or parsed.get("policy") != policy
                    or type(parsed.get("high_water")) is not int or parsed["high_water"] < 1
                    or type(parsed.get("before")) is not int or not 1 <= parsed["before"] <= parsed["high_water"] + 1
                    or type(parsed.get("expires_at")) not in {int, float} or parsed["expires_at"] < self._clock()):
                raise ValueError
        except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
            raise ObservatoryError("invalid_workspace_run_cursor", "Select a current Workbench run page.", 400) from None
        return parsed["high_water"], parsed["before"]

    @staticmethod
    def _task_item(value: dict[str, Any], *, observed_at: str) -> dict[str, Any]:
        binding_id = _text(value["binding_id"])
        project_id, task_id, native_state = (_text(value[key]) for key in ("project_id", "task_id", "status"))
        artifact = value.get("artifact_digest")
        references = ([{"owner_id": _OWNER_ID, "artifact_id": artifact}]
                      if type(artifact) is str and re.fullmatch(r"[0-9a-f]{64}", artifact) else [])
        verification = value.get("verification_passed")
        return {
            "id": projected_run_id(_OWNER_ID, _TASK_SOURCE, binding_id), "owner_id": _OWNER_ID,
            "source": _TASK_SOURCE, "native_id": binding_id, "kind": "task_binding",
            "title": f"Task binding {task_id}", "project_id": project_id, "task_id": task_id,
            "native_state": native_state, "status": "retained", "updated_at": _stamp(value["updated_at"]),
            "freshness": "fresh", "observed_at": observed_at,
            "evidence_refs": references,
            "verification": ({"status": "passed" if verification in {True, 1} else "failed"}
                             if type(verification) in {bool, int} and verification in {0, 1, True, False} else None),
            "correlation_id": None,
        }

    def task_page(self, session, *, limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
        if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ObservatoryError("invalid_workspace_run_page", "Select a bounded Workbench run page.", 400)
        projects = self._authorized_projects(session)
        if not projects:
            return {"items": [], "next_cursor": None, "sources": [{"id": _TASK_SOURCE, "owner_id": _OWNER_ID,
                    "status": "fresh", "observed_at": _stamp(self._clock()), "deadline_seconds": 2.0, "partial": False, "truncated": False}]}
        principal, policy = session.principal.identity, self._policy(session, projects)
        snapshot = self._decode_cursor(cursor, principal=principal, policy=policy)
        page = self._store.task_binding_run_page(principal, frozenset(row["id"] for row in projects),
                                                 high_water=snapshot[0] if snapshot else None,
                                                 before=snapshot[1] if snapshot else None, limit=limit)
        observed_at = _stamp(self._clock())
        items, page_bytes, partial = [], 0, bool(page["partial"])
        for row in page["items"]:
            try:
                item = self._task_item(row, observed_at=observed_at)
                encoded = canonical(item)
            except (KeyError, ValueError, TypeError):
                partial = True
                continue
            if page_bytes + len(encoded) > _MAX_PAGE_BYTES:
                raise ObservatoryError("workspace_source_unavailable", "The task binding projection exceeded its page bound.", 503)
            items.append(item)
            page_bytes += len(encoded)
        next_cursor = None
        if page["truncated"] and page["high_water"] is not None and page["frontier"] is not None:
            next_cursor = self._encode_cursor(policy=policy,
                                               high_water=page["high_water"], before=page["frontier"])
        return {"items": items, "next_cursor": next_cursor, "sources": [{"id": _TASK_SOURCE, "owner_id": _OWNER_ID,
                "status": "fresh", "observed_at": _stamp(self._clock()), "deadline_seconds": 2.0, "partial": partial,
                "truncated": next_cursor is not None}]}

    def pi_page(self, session, *, limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
        if cursor is not None or type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ObservatoryError("invalid_workspace_run_page", "Select a bounded Workbench run page.", 400)
        projects = self._authorized_projects(session)
        source = {"id": _PI_SOURCE, "owner_id": _OWNER_ID, "observed_at": _stamp(self._clock()), "deadline_seconds": 2.0,
                  "partial": False, "truncated": False}
        if not projects:
            source["status"] = "fresh"
            return {"items": [], "next_cursor": None, "sources": [source]}
        if self._pi_store is None:
            source["status"] = "unavailable"
            return {"items": [], "next_cursor": None, "sources": [source]}
        try:
            page = self._pi_store.run_metadata_page(session.principal.identity,
                                                    frozenset(row["id"] for row in projects), limit=limit)
        except PiSessionError:
            source["status"] = "unavailable"
            return {"items": [], "next_cursor": None, "sources": [source]}
        observed_at = _stamp(self._clock())
        items = []
        for row in page["items"]:
            active = row["status"] in {"reserved", "starting", "running", "recoverable", "quarantined"}
            items.append({"id": projected_run_id(_OWNER_ID, _PI_SOURCE, row["session_id"]), "owner_id": _OWNER_ID,
                          "source": _PI_SOURCE, "native_id": row["session_id"], "kind": "managed_pi_session",
                          "title": f"Managed Pi session for {row['binding'].task_id}", "project_id": row["binding"].project_id,
                          "task_id": row["binding"].task_id, "native_state": row["status"],
                          "status": "running" if active else "retained", "created_at": _stamp(row["created_at"]),
                          "updated_at": _stamp(row["updated_at"]), "provider_id": row["binding"].provider_id,
                          "freshness": "fresh", "observed_at": observed_at,
                          "model_id": row["model_id"], "parent_native_id": row["parent_session_id"],
                          "evidence_refs": [], "correlation_id": None})
        source.update(status="fresh", partial=bool(page["partial"]), truncated=bool(page["truncated"]))
        return {"items": items, "next_cursor": None, "sources": [source]}
