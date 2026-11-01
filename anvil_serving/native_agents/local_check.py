"""Finite local operator checks; never installed as a production authenticator.

The only listener is a temporary loopback broker. Random admissions stay in
memory, expire after two minutes, and are discarded when this command exits.
No browser, email, account activation, or Connect grant is involved.
"""
from contextlib import ExitStack
import http.client
import json
from pathlib import Path
import secrets
import tempfile
import threading
import time
from uuid import uuid4

from .auth import AdmissionDenied
from .core import Broker, PREFIX
from .owner import PiOwner
from .server import BoundedServer, handler, load_config
from .store import Store, protected_directory
from .types import Directory, NativeAdmission, Workspace


class _FixtureOwner:
    """No shell, model, memory backend, or external network in the default check."""
    def __init__(self):
        self.sessions_by_id = {}

    def ensure(self, session, cwd):
        self.sessions_by_id.setdefault(session, {"id": session, "cwd": cwd})

    def sessions(self):
        return list(self.sessions_by_id.values())

    def history(self, session, before=None):
        return {"context": {"messages": [], "hasMore": False}}

    def state(self, session):
        return {"running": False}

    def abort(self, session, pending):
        return None


def _exercise(workspaces, owners, root, result):
    principals = sorted({workspace.principal for workspace in workspaces})
    tokens = {principal: secrets.token_urlsafe(32) for principal in principals}
    admissions = {token: NativeAdmission(principal, 1, time.time() + 120)
                  for principal, token in tokens.items()}

    def authorize(token):
        admission = admissions.get(token)
        if admission is None:
            raise AdmissionDenied()
        return admission

    def recheck(admission):
        return admission in admissions.values()

    with ExitStack() as cleanup:
        store = Store(root / "broker.sqlite", maximum_receipts_per_principal=32)
        cleanup.callback(store.db.close)
        broker = Broker("local-check", workspaces, store, owners, authorize, recheck)
        for worker in (broker.worker, broker.stop_worker, broker.interaction_worker):
            cleanup.callback(worker.shutdown)
        server = BoundedServer(("127.0.0.1", 0), handler(broker), maximum_connections=8)
        cleanup.callback(server.server_close)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        cleanup.callback(serving.join, 5)
        cleanup.callback(server.shutdown)
        checks = result["checks"]

        def request(name, token, path, expected=200, body=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=15)
            try:
                headers = {"Content-Type": "application/json", "Connection": "close"}
                if token is not None:
                    headers["Authorization"] = "Bearer " + token
                connection.request("POST" if body is not None else "GET", PREFIX + path,
                                   json.dumps(body) if body is not None else None, headers)
                response = connection.getresponse()
                raw = response.read(1048577)
                if response.status != expected or len(raw) > 1048576:
                    raise ValueError("local check failed: " + name)
                value = json.loads(raw)
                checks.append(name)
                return value
            finally:
                connection.close()

        def settle(token, operation):
            for _ in range(40):
                value = request("operation", token, "/operations/" + operation["id"])["operation"]
                if value["state"] == "completed":
                    return
                if value["state"] not in ("reserved", "dispatched"):
                    raise ValueError("local owner operation did not complete")
                time.sleep(0.05)
            raise ValueError("local owner operation timed out")

        request("missing-token-denied", None, "/workspaces", 401)
        request("unknown-token-denied", secrets.token_urlsafe(32), "/workspaces", 401)
        for principal in principals:
            catalog = request("own-workspaces", tokens[principal], "/workspaces")["workspaces"]
            if {item["id"] for item in catalog} != {w.id for w in workspaces if w.principal == principal}:
                raise ValueError("local workspace catalog crossed principals")
        sessions = result["sessions"]
        for workspace in workspaces:
            token = tokens[workspace.principal]
            other = tokens[next(p for p in principals if p != workspace.principal)]
            base = "/workspaces/" + workspace.id + "/sessions"
            request_id = str(uuid4())
            result["create_requests"].append({"workspace_id": workspace.id, "request_id": request_id})
            operation = request("create", token, base, 202,
                {"requestID": request_id, "directoryID": workspace.directories[0].id})["operation"]
            session = base + "/" + operation["sessionID"]
            sessions.append({"workspace_id": workspace.id, "session_id": operation["sessionID"]})
            settle(token, operation)
            request("own-session", token, session)
            request("own-state", token, session + "/state")
            request("other-session-denied", other, session, 404)
            request("other-operation-denied", other, "/operations/" + operation["id"], 404)
            request("other-stop-denied", other, session + "/abort", 404,
                    {"requestID": str(uuid4()), "pendingRequestIDs": []})
            stop = request("own-stop", token, session + "/abort", 202,
                           {"requestID": str(uuid4()), "pendingRequestIDs": []})["operation"]
            settle(token, stop)
        revoked = tokens[principals[0]]
        del admissions[revoked]
        request("local-admission-revoked", revoked, "/workspaces", 401)
        request("other-admission-preserved", tokens[principals[1]], "/workspaces")
        admissions.clear()


def check(config_path=None, *, apply=False):
    """Default uses fixtures; a protected guest-only config opts into real owners.

    Real-owner checks create empty native sessions and Stop them. They never send
    prompts or use a production broker store. Native sessions remain for audit.
    """
    workspaces = None
    if config_path is not None:
        configuration, workspaces = load_config(config_path)
        if (not 2 <= len(workspaces) <= 4 or len({w.principal for w in workspaces}) != 2
                or any(w.isolation != "sandboxed" or w.principal == configuration.get("personal_principal")
                       for w in workspaces)):
            raise ValueError("local checks require two guest principals and two to four sandboxed workspaces")
        if not apply:
            return {"mode": "owner-preview", "workspaces": [w.id for w in workspaces],
                    "action": "create empty test sessions, read state, verify cross-user denial, and Stop",
                    "oauth_tested": False}
    # Do not relax Store custody for /tmp or reuse any live broker database.
    parent = Path.home()
    protected_directory(parent)
    with tempfile.TemporaryDirectory(prefix=".anvil-agents-check-", dir=parent) as temporary:
        root = Path(temporary)
        if workspaces is None:
            workspaces = []
            for index in range(2):
                directory = root / str(index)
                directory.mkdir(mode=0o700)
                workspaces.append(Workspace(f"guest-{index}", "human:" + str(index + 1) * 64,
                    "Local fixture", (Directory("project", "Project", directory, "/workspace/project"),),
                    "http://127.0.0.1:1", root / f"unused-token-{index}", "sandboxed"))
            owners = {w.id: _FixtureOwner() for w in workspaces}
        else:
            owners = {w.id: PiOwner(w) for w in workspaces}
        result = {"checks": [], "sessions": [], "create_requests": []}
        try:
            _exercise(workspaces, owners, root, result)
            result["ok"] = True
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
            result.update(ok=False, error_code="local_check_failed")
        result["checks"] = sorted(set(result["checks"]))
    return {"schema": "anvil-native-agents-local-check/v1",
            "mode": "owner-control-check" if config_path else "synthetic-owner-check",
            "oauth_tested": False, "runtime_isolation_tested": False, "model_tested": False,
            "temporary_state_removed": True, **result}
