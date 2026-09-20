"""Unreleased owner boundary for the pinned native Pi Web bridge."""
from __future__ import annotations

import hashlib
import re
import threading
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path

from ..observability.dashboard.contracts import ObservatoryError, canonical, digest, identifier, strict_json

_TITLE = re.compile(r"[^\x00-\x1f\x7f]{1,192}\Z")
_KIND = "host-pi-thread"
_MAX_NATIVE_ROWS = 512


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HostPiClient:
    """One private, pinned owner endpoint; browser input never chooses it."""
    def __init__(self, origin: str, token: str, *, open_url=None):
        self._origin, self._token = origin, token
        self._open = open_url or urllib.request.build_opener(_NoRedirect()).open

    def _call(self, path: str, body=None, *, timeout=10):
        headers = {"Authorization": "Bearer " + self._token}
        data = None
        method = "GET"
        if body is not None:
            data, method = canonical(body), "POST"
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self._origin + path, data=data, method=method, headers=headers)
        try:
            with self._open(request, timeout=timeout) as response:
                return strict_json(response.read(128 * 1024))
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, ObservatoryError):
            raise ObservatoryError("host_pi_unavailable", "The native Pi owner is unavailable.", 503) from None

    @staticmethod
    def _session(value):
        if type(value) is not dict or set(value) != {"v", "session_id"} or value.get("v") != 1:
            raise ObservatoryError("host_pi_unavailable", "The native Pi owner is unavailable.", 503)
        return identifier(value["session_id"])

    def ensure(self, *, request_key: str, project_id: str, cwd: str) -> str:
        return self._session(self._call("/api/workbench/ensure", {
            "v": 1, "request_key": request_key, "project_id": project_id, "cwd": cwd,
        }))

    def associate(self, *, request_key: str, project_id: str, cwd: str, native_id: str) -> str:
        return self._session(self._call("/api/workbench/associate", {
            "v": 1, "request_key": request_key, "project_id": project_id, "cwd": cwd,
            "native_id": identifier(native_id),
        }))

    def list_native(self):
        value = self._call("/api/workbench/sessions", timeout=1.5)
        if type(value) is not dict or set(value) != {"v", "items"} or value.get("v") != 1 or type(value["items"]) is not list or len(value["items"]) > _MAX_NATIVE_ROWS:
            raise ObservatoryError("host_pi_unavailable", "The native Pi owner is unavailable.", 503)
        items = []
        for row in value["items"]:
            if type(row) is not dict or set(row) != {"native_id", "title", "running"} or type(row["title"]) is not str or not _TITLE.fullmatch(row["title"]) or type(row["running"]) is not bool:
                raise ObservatoryError("host_pi_unavailable", "The native Pi owner is unavailable.", 503)
            items.append({"native_id": identifier(row["native_id"]), "title": row["title"], "running": row["running"]})
        return items


class HostPi:
    """Durable project-to-native associations with no browser-visible paths."""
    def __init__(self, config, store, projects, access, client):
        self.config, self.store, self.projects, self.access, self.client = config, store, projects, access, client
        self.lock = threading.RLock()

    def _project(self, session, project_id):
        host = self.config.get("host_pi")
        if (not host or not session.connect_binding or session.connect_binding.subject != host["owner_subject"]
                or host["resource_id"] not in session.principal.resources):
            raise ObservatoryError("permission_denied", "Your session does not grant this resource action.", 403)
        self.access.permit(session, host["resource_id"])
        return self.projects.project(session, identifier(project_id))

    @staticmethod
    def authority(config, projects, access, session):
        host = config.get("host_pi")
        binding = getattr(session, "connect_binding", None)
        if (not host or "token_ref" not in host or not binding or binding.subject != host["owner_subject"]
                or host["resource_id"] not in session.principal.resources):
            raise ObservatoryError("permission_denied", "This native Pi source is unavailable.", 403)
        access.permit(session, host["resource_id"])
        allowed = []
        for project in config.get("projects", []):
            try:
                projects.project(session, project["id"])
            except ObservatoryError as error:
                if error.status in {403, 404}:
                    continue
                raise
            allowed.append((project["id"], project["resource_id"]))
        if not allowed:
            raise ObservatoryError("permission_denied", "This native Pi source is unavailable.", 403)
        return {"project_ids": sorted(item[0] for item in allowed),
                "resource_ids": sorted({host["resource_id"], *(item[1] for item in allowed)}),
                "authority_key": digest({"principal": session.principal.identity, "binding": asdict(binding),
                                         "host": host, "projects": sorted(allowed)})}

    @staticmethod
    def _request_key(owner, project_id, request_id):
        request_id = identifier(request_id)
        return hashlib.sha256((owner + "\0" + project_id + "\0" + request_id).encode()).hexdigest()

    @staticmethod
    def _private_row(value):
        if type(value) is not dict or value.get("v") != 1:
            raise ObservatoryError("host_pi_unavailable", "The native Pi association is unavailable.", 409)
        for key in ("project_id", "request_id", "root_id"):
            identifier(value.get(key))
        if type(value.get("cwd")) is not str or not Path(value["cwd"]).is_absolute():
            raise ObservatoryError("host_pi_unavailable", "The native Pi association is unavailable.", 409)
        if type(value.get("title", "")) is not str or not _TITLE.fullmatch(value["title"]) or type(value.get("archived")) is not bool:
            raise ObservatoryError("host_pi_unavailable", "The native Pi association is unavailable.", 409)
        if value.get("operation") not in {"create", "associate"}:
            raise ObservatoryError("host_pi_unavailable", "The native Pi association is unavailable.", 409)
        requested = value.get("requested_native_id")
        if requested is not None:
            identifier(requested)
        if value.get("operation") == "create" and requested is not None:
            raise ObservatoryError("host_pi_unavailable", "The native Pi association is unavailable.", 409)
        if value.get("operation") == "associate" and requested is None:
            raise ObservatoryError("host_pi_unavailable", "The native Pi association is unavailable.", 409)
        if value.get("native_id") is not None:
            identifier(value["native_id"])
        return value

    @staticmethod
    def _root(project, root_id):
        root_id = project["primary_root_id"] if root_id is None else identifier(root_id)
        for root in project["roots"]:
            if root["id"] == root_id:
                return root
        raise ObservatoryError("invalid_host_pi_root", "Choose one declared project root.")

    @classmethod
    def _new_row(cls, project, request_id, title, root_id, operation, requested_native_id=None):
        root = cls._root(project, root_id)
        return {"v": 1, "project_id": project["id"], "request_id": identifier(request_id),
                "root_id": root["id"], "cwd": root["path"], "operation": operation,
                "requested_native_id": requested_native_id, "native_id": None,
                "title": title, "archived": False}

    def _row(self, owner, key, project, request_id, title, root_id, operation, requested_native_id=None):
        try:
            row = self._bound_row(self.store.get(_KIND, owner, key), owner, key, project["id"], request_id)
            if row["operation"] != operation or row["requested_native_id"] != requested_native_id:
                raise ObservatoryError("host_pi_conflict", "This request already names another host Pi operation.", 409)
            return row
        except ObservatoryError as error:
            if error.status != 404:
                raise
            row = self._new_row(project, request_id, title, root_id, operation, requested_native_id)
            # Persist the trusted root before contacting Pi: retrying an
            # uncertain request must never follow later configuration drift.
            self.store.put(_KIND, owner, key, row)
            return row

    @classmethod
    def _bound_row(cls, value, owner, key, project_id=None, request_id=None):
        row = cls._private_row(value)
        if (cls._request_key(owner, row["project_id"], row["request_id"]) != key
                or (project_id is not None and row["project_id"] != project_id)
                or (request_id is not None and row["request_id"] != request_id)):
            raise ObservatoryError("host_pi_conflict", "The native Pi association does not match this request.", 409)
        return row

    def create(self, session, project_id, request_id, title="New Pi session", root_id=None):
        project = self._project(session, project_id)
        if type(title) is not str or not _TITLE.fullmatch(title):
            raise ObservatoryError("invalid_host_pi_thread", "Choose a bounded thread title.")
        owner, project_id = session.principal.identity, project["id"]
        key = self._request_key(owner, project_id, request_id)
        with self.lock:
            row = self._row(owner, key, project, request_id, title, root_id, "create")
            if row["native_id"] is None:
                native_id = self.client.ensure(request_key=key, project_id=project_id, cwd=row["cwd"])
                row = row | {"native_id": native_id}
                self.store.put(_KIND, owner, key, row)
            return self.public(row)

    def associate(self, session, project_id, request_id, native_id, title="Imported Pi session", root_id=None):
        project = self._project(session, project_id)
        if type(title) is not str or not _TITLE.fullmatch(title):
            raise ObservatoryError("invalid_host_pi_thread", "Choose a bounded thread title.")
        owner, project_id = session.principal.identity, project["id"]
        key, native_id = self._request_key(owner, project_id, request_id), identifier(native_id)
        with self.lock:
            row = self._row(owner, key, project, request_id, title, root_id, "associate", native_id)
            if row["native_id"] is not None and row["native_id"] != native_id:
                raise ObservatoryError("host_pi_conflict", "This request already names another native Pi thread.", 409)
            verified = self.client.associate(request_key=key, project_id=project_id, cwd=row["cwd"], native_id=native_id)
            if verified != native_id:
                raise ObservatoryError("host_pi_unavailable", "The native Pi owner is unavailable.", 503)
            row = row | {"native_id": native_id}
            self.store.put(_KIND, owner, key, row)
            return self.public(row)

    @staticmethod
    def public(row):
        return {key: row[key] for key in ("project_id", "request_id", "native_id", "title", "archived")}

    def list(self, session, project_id):
        project = self._project(session, project_id)
        return [row for row in self.inventory(session) if row.get("project_id") in {None, project["id"]}]

    def inventory(self, session):
        authority = self.authority(self.config, self.projects, self.access, session)
        native = self.client.list_native()
        if len({row["native_id"] for row in native}) != len(native):
            raise ObservatoryError("host_pi_unavailable", "The native Pi owner returned duplicate identities.", 503)
        associated = {}
        for key, value in sorted(self.store.host_pi_associations(session.principal.identity)):
            row = self._bound_row(value, session.principal.identity, key)
            if row["native_id"] is not None:
                previous = associated.get(row["native_id"])
                if previous and previous["project_id"] != row["project_id"]:
                    raise ObservatoryError("host_pi_unavailable", "The native Pi associations conflict.", 503)
                associated.setdefault(row["native_id"], row)
        items = []
        for row in native:
            native_id = row["native_id"]
            private = associated.get(native_id)
            if private is not None and private["project_id"] not in authority["project_ids"]:
                continue
            if private is not None:
                items.append(self.public(private) | {"running": row["running"], "source": "host-pi", "provenance": "associated"})
            else:
                items.append({"native_id": native_id, "title": row["title"], "running": row["running"], "archived": False,
                              "source": "native", "provenance": "unassigned"})
        return items

    def reopen(self, session, project_id, request_id):
        self._project(session, project_id)
        key = self._request_key(session.principal.identity, identifier(project_id), request_id)
        row = self._bound_row(self.store.get(_KIND, session.principal.identity, key), session.principal.identity, key, project_id, request_id)
        if row["native_id"] is None:
            raise ObservatoryError("host_pi_pending", "The native Pi thread is still being created.", 409)
        return self.public(row)

    def rename(self, session, project_id, request_id, title):
        if type(title) is not str or not _TITLE.fullmatch(title):
            raise ObservatoryError("invalid_host_pi_thread", "Choose a bounded thread title.")
        self._project(session, project_id)
        key = self._request_key(session.principal.identity, identifier(project_id), request_id)
        with self.lock:
            row = self._bound_row(self.store.get(_KIND, session.principal.identity, key), session.principal.identity, key, project_id, request_id)
            self.store.put(_KIND, session.principal.identity, key, row | {"title": title})
            return self.public(row | {"title": title})

    def archive(self, session, project_id, request_id, archived):
        if type(archived) is not bool:
            raise ObservatoryError("invalid_host_pi_thread", "Choose an archive state.")
        self._project(session, project_id)
        key = self._request_key(session.principal.identity, identifier(project_id), request_id)
        with self.lock:
            row = self._bound_row(self.store.get(_KIND, session.principal.identity, key), session.principal.identity, key, project_id, request_id)
            self.store.put(_KIND, session.principal.identity, key, row | {"archived": archived})
            return self.public(row | {"archived": archived})
