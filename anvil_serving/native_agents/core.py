"""Bounded workspace/session authorization and non-replaying command lifecycle."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import threading
import time
from urllib.parse import urlsplit, parse_qs
from uuid import UUID
from .types import AgentsError, NativeAdmission, ID, PRINCIPAL

PREFIX = "/api/native-agents/v1"
MAX_BODY = 65536

def exact(body, keys):
    if not isinstance(body, dict) or set(body) != set(keys): raise AgentsError(400, "invalid_request")

def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value): raise AgentsError(400, "invalid_request")
    return value

def request_id(value):
    if not isinstance(value, str): raise AgentsError(400, "invalid_request")
    try:
        result = str(UUID(value))
        if result != value.lower(): raise ValueError()
        return result
    except ValueError: raise AgentsError(400, "invalid_request") from None

def bounded(value, maximum, empty=False):
    if not isinstance(value, str) or len(value.encode()) > maximum or (not empty and not value.strip()) or "\x00" in value:
        raise AgentsError(400, "invalid_request")
    return value

class Broker:
    def __init__(self, deployment_id, workspaces, store, owners, authorize, recheck):
        self.deployment_id = identifier(deployment_id)
        self.workspaces = {w.id: w for w in workspaces}
        if len(self.workspaces) != len(workspaces): raise ValueError("duplicate workspace")
        self.store, self.owners, self.authorize, self.recheck = store, owners, authorize, recheck
        self.worker = ThreadPoolExecutor(max_workers=16, thread_name_prefix="native-agents")
        self.stop_worker = ThreadPoolExecutor(max_workers=4, thread_name_prefix="native-agents-stop")
        self.capacity = threading.BoundedSemaphore(32)
        self.stop_capacity = threading.BoundedSemaphore(16)
        self.interaction_worker = ThreadPoolExecutor(max_workers=4, thread_name_prefix="native-agents-interaction")
        self.interaction_capacity = threading.BoundedSemaphore(16)
        self.stream_lock = threading.Lock()
        self.stream_counts = {}
        self.stream_total = 0
        self.principal_lock = threading.Lock()
        self.principal_counts = {}

    def admission(self, bearer):
        a = self.authorize(bearer)
        self.check(a)
        return a

    def check(self, a):
        if (not isinstance(a, NativeAdmission) or not PRINCIPAL.fullmatch(a.principal)
            or type(a.generation) is not int or a.generation < 1 or a.resource != "native-agents"
            or not isinstance(a.expires_at,(int,float)) or not math.isfinite(a.expires_at) or a.expires_at <= time.time() or not self.recheck(a)):
            raise AgentsError(401, "authorization_expired")

    def workspace(self, a, wid):
        self.check(a)
        w = self.workspaces.get(wid)
        if not w or w.principal != a.principal: raise AgentsError(404, "not_found")
        return w

    def directory(self, w, did):
        d = next((d for d in w.directories if d.id == did), None)
        if not d: raise AgentsError(404, "not_found")
        # Managed registry paths are canonical. A replaced symlink never widens access.
        if not d.path.is_absolute() or d.path.resolve(strict=True) != d.path or not d.path.is_dir():
            raise AgentsError(409, "directory_unavailable")
        return d

    def session(self, a, w, sid):
        did = self.store.session(a.principal, w.id, identifier(sid))
        return self.directory(w, did)

    def operation(self, a, rid):
        op = self.store.operation(a.principal, request_id(rid))
        w = self.workspace(a, op["workspaceID"])
        if op["kind"] in ("prompt","invocation") and op["state"] in ("dispatched","unknown"):
            try:
                receipt = self.owners[w.id].operation(op["sessionID"],op["id"])
                if receipt.get("requestID") != op["id"] or receipt.get("sessionID") != op["sessionID"]:
                    raise AgentsError(502,"owner_invalid_receipt")
                state = receipt.get("state")
                if state in ("accepted","rejected"):
                    self.store.update(a.principal,op["id"],state,"command_rejected" if state == "rejected" else None)
                    op = self.store.operation(a.principal,op["id"])
            except AgentsError as error:
                if error.status not in (404,503): raise
        return op

    def submit(self, a, w, sid, kind, body, work, directory=None, before_dispatch=None):
        rid = request_id(body["requestID"])
        digest = hashlib.sha256(json.dumps([w.id, sid, kind, body], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        control = kind in ("abort","interaction")
        capacity = self.stop_capacity if kind == "abort" else self.interaction_capacity if kind == "interaction" else self.capacity
        owner_key = (a.principal,kind if control else "work")
        with self.principal_lock:
            if self.principal_counts.get(owner_key,0) >= (4 if control else 8): raise AgentsError(429,"principal_busy")
            if not capacity.acquire(blocking=False): raise AgentsError(503,"busy")
            self.principal_counts[owner_key] = self.principal_counts.get(owner_key,0)+1
        def release():
            capacity.release()
            with self.principal_lock: self.principal_counts[owner_key] -= 1
        try:
            operation, fresh = self.store.reserve(a.principal, rid, digest, w.id, sid, kind, directory)
        except BaseException:
            release()
            raise
        if not fresh:
            release()
            return operation
        try:
            if before_dispatch: before_dispatch()
        except AgentsError as error:
            self.store.update(a.principal,rid,"rejected",error.code); release(); raise
        self.store.update(a.principal, rid, "dispatched")
        def run():
            try:
                self.check(a)
                result = work(rid)
                state = "completed" if kind in ("create","abort","interaction") else "accepted"
                if kind in ("prompt","invocation") and isinstance(result,dict) and result.get("state") == "dispatched": state = "dispatched"
                self.store.update(a.principal,rid,state)
            except AgentsError as error:
                # Explicit owner rejection is conclusive; connection/process failure isn't.
                known = error.status in (400, 401, 403, 404, 409, 422)
                self.store.update(a.principal, rid, "rejected" if known else "unknown", error.code)
            except Exception:
                self.store.update(a.principal, rid, "unknown", "owner_outcome_unknown")
            finally:
                release()
        (self.stop_worker if kind == "abort" else self.interaction_worker if kind == "interaction" else self.worker).submit(run)
        return self.store.operation(a.principal, rid)

    def handle(self, a, method, target, body=None):
        self.check(a)
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.fragment or "%" in parsed.path or "//" in parsed.path:
            raise AgentsError(400, "invalid_request")
        if not parsed.path.startswith(PREFIX + "/"): raise AgentsError(404, "not_found")
        parts = parsed.path[len(PREFIX) + 1:].split("/")
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        if method not in ("GET", "POST"): raise AgentsError(405, "method_not_allowed")
        if method == "GET" and body is not None: raise AgentsError(400, "invalid_request")
        if parsed.query and not (len(parts) == 5 and parts[4] == "context" and method == "GET"):
            raise AgentsError(400, "invalid_request")
        if parts == ["capabilities"] and method == "GET":
            return 200, {"version": 1, "deploymentID": self.deployment_id, "ownerID": a.principal,
                         "capabilities": ["workspaces", "create", "commands", "interactions", "operations", "stop"]}
        if parts == ["workspaces"] and method == "GET":
            return 200, {"workspaces": [w.public() for w in self.workspaces.values() if w.principal == a.principal]}
        if len(parts) == 2 and parts[0] == "operations" and method == "GET":
            return 200, {"operation": self.operation(a, parts[1])}
        if len(parts) < 3 or parts[0] != "workspaces" or parts[2] != "sessions": raise AgentsError(404, "not_found")
        w = self.workspace(a, identifier(parts[1])); owner = self.owners[w.id]
        if len(parts) == 3:
            if method == "GET":
                native = {s["id"]: s for s in owner.sessions()}
                return 200, {"sessions": [{**native.get(sid, {"id": sid, "messageCount": 0, "isRunning": False}),
                            "cwd": self.directory(w, did).display_path, "directoryID": did}
                            for sid, did in self.store.sessions(a.principal, w.id)]}
            exact(body, ["requestID", "directoryID"])
            rid = request_id(body["requestID"]); d = self.directory(w, identifier(body["directoryID"]))
            sid = "native-" + hashlib.sha256((a.principal + "\0" + w.id + "\0" + rid).encode()).hexdigest()
            return 202, {"operation": self.submit(a, w, sid, "create", body,
                          lambda request: owner.ensure(sid, d.runtime_path or str(d.path)), d.id)}
        sid = identifier(parts[3]); directory = self.session(a, w, sid)
        def ensure(): owner.ensure(sid, directory.runtime_path or str(directory.path))
        if len(parts) == 4 and method == "GET":
            if query: raise AgentsError(400,"invalid_request")
            ensure(); return 200, owner.history(sid)
        if len(parts) == 5 and method == "GET":
            action = parts[4]
            if action == "state":
                if query: raise AgentsError(400,"invalid_request")
                ensure(); return 200, owner.state(sid)
            if action == "context":
                if set(query) - {"before"} or any(len(v) != 1 for v in query.values()): raise AgentsError(400, "invalid_request")
                before = identifier(query["before"][0]) if "before" in query else None
                ensure(); return 200, owner.history(sid, before)
            if action in ("commands","interactions"):
                if query: raise AgentsError(400,"invalid_request")
                ensure(); return 200, owner.catalog(sid) if action == "commands" else owner.interactions(sid)
        if method != "POST": raise AgentsError(404, "not_found")
        if len(parts) == 5 and parts[4] == "prompts":
            exact(body, ["requestID", "message"]); request_id(body["requestID"]); bounded(body["message"], 32768); ensure()
            instance = owner.catalog(sid)["ownerInstance"]
            return 202, {"operation": self.submit(a, w, sid, "prompt", body,
                lambda rid: owner.invoke(sid, rid, instance, body["message"]))}
        if len(parts) == 5 and parts[4] == "invocations":
            exact(body, ["requestID", "commandID", "catalogRevision", "arguments"])
            request_id(body["requestID"]); bounded(body["commandID"],160); bounded(body["catalogRevision"],160); bounded(body["arguments"],32768,empty=True); ensure()
            catalog = owner.catalog(sid)
            if body["catalogRevision"] != catalog["revision"]: raise AgentsError(409, "catalog_changed")
            command = next((c for c in catalog["commands"] if c["id"] == body["commandID"]), None)
            if not command or not command["supported"]: raise AgentsError(422, "command_unavailable")
            arguments = bounded(body["arguments"], 32768, empty=True)
            message = "/" + command["name"] + (" " + arguments if arguments else "")
            bounded(message, 32768)
            return 202, {"operation": self.submit(a, w, sid, "invocation", body,
                lambda rid: owner.invoke_command(sid, rid, catalog["ownerInstance"], command["id"], catalog["revision"], arguments))}
        if len(parts) == 5 and parts[4] == "abort":
            exact(body, ["requestID","pendingRequestIDs"])
            request_id(body["requestID"])
            supplied = body["pendingRequestIDs"]
            if not isinstance(supplied,list) or len(supplied) > 128: raise AgentsError(400,"invalid_request")
            pending = sorted(set(request_id(value) for value in supplied) | set(self.store.pending(a.principal,w.id,sid)))
            if len(pending)>128: raise AgentsError(409,"too_many_pending")
            return 202, {"operation": self.submit(a, w, sid, "abort", body, lambda rid: (ensure(), owner.abort(sid, pending))[1],
                before_dispatch=lambda: self.store.cancel(a.principal,w.id,sid,pending))}
        if len(parts) == 7 and parts[4] == "interactions" and parts[6] == "response":
            exact(body, ["requestID", "ownerInstance", "answer"])
            interaction_id = identifier(parts[5]); request_id(body["requestID"]); bounded(body["ownerInstance"],160)
            if not isinstance(body["answer"],dict): raise AgentsError(400,"invalid_request")
            ensure()
            snapshot = owner.interactions(sid)
            if snapshot["ownerInstance"] != body["ownerInstance"]: raise AgentsError(409, "interaction_changed")
            interaction = next((i for i in snapshot["interactions"] if i["id"] == interaction_id), None)
            if not interaction: raise AgentsError(409, "interaction_changed")
            validate_answer(interaction, body["answer"])
            return 202, {"operation": self.submit(a, w, sid, "interaction", body,
                lambda rid: owner.respond(sid, interaction_id, body["ownerInstance"], body["answer"]))}
        raise AgentsError(404, "not_found")

    def events(self, a, wid, sid):
        w = self.workspace(a, identifier(wid)); directory = self.session(a, w, sid)
        # At most two streams per principal and 24 total leave 40 server slots
        # for control requests even when all admitted streams are long-lived.
        with self.stream_lock:
            if self.stream_total >= 24 or self.stream_counts.get(a.principal,0) >= 2:
                raise AgentsError(429,"stream_limit")
            self.stream_total += 1
            self.stream_counts[a.principal] = self.stream_counts.get(a.principal,0)+1
        try:
            self.owners[w.id].ensure(sid, directory.runtime_path or str(directory.path))
            # The owner heartbeat/read deadline bounds quiet-stream revocation.
            for chunk in self.owners[w.id].events(sid):
                self.check(a)
                yield chunk
        finally:
            with self.stream_lock:
                self.stream_total -= 1
                self.stream_counts[a.principal] -= 1



def validate_answer(interaction, answer):
    if interaction.get("expiresAt", time.time() + 1) <= time.time(): raise AgentsError(409, "interaction_expired")
    if not isinstance(answer, dict): raise AgentsError(400, "invalid_request")
    if answer == {"cancelled": True}: return
    method = interaction["method"]
    if method == "confirm":
        exact(answer, ["confirmed"])
        if type(answer["confirmed"]) is not bool: raise AgentsError(400, "invalid_request")
    elif method in ("input", "editor", "select"):
        exact(answer, ["value"]); value = bounded(answer["value"], 32768, empty=True)
        if method == "select" and value not in interaction.get("options", []): raise AgentsError(400, "invalid_request")
    else: raise AgentsError(422, "interaction_unsupported")
