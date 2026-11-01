"""Native Agents adapter for one whole-process Hermes OpenShell owner.

Hermes owns sessions, admissions, execution and memory. This adapter stores only
broker identity mappings and dispatch receipts; it never replays uncertain work.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import uuid

SESSION = re.compile(r"native-[0-9a-f]{64}\Z")
REQUEST = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


class Rejected(Exception):
    def __init__(self, status=400):
        self.status = status


def require(condition, status=400):
    if not condition:
        raise Rejected(status)


def exact(value, keys):
    require(type(value) is dict and set(value) == set(keys))


def private_directory(value):
    path = Path(value)
    require(path.is_absolute() and str(path.resolve()) == str(path))
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077)
    return path


def private_file(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and info.st_nlink == 1 and not info.st_mode & 0o077 and info.st_size <= 4096)
        raw = os.read(descriptor, 4097)
        require(len(raw) == info.st_size)
        return raw
    finally:
        os.close(descriptor)


class Owner:
    def __init__(self, client, directory, storage):
        self.client = client
        self.directory = str(private_directory(directory))
        self.storage = private_directory(storage)
        require(not self.storage.is_relative_to(self.directory)
                and not Path(self.directory).is_relative_to(self.storage))
        database = self.storage / "adapter.sqlite3"
        try:
            descriptor = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except FileExistsError:
            pass
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(database) + suffix)
            if path.exists() or path.is_symlink():
                info = path.lstat()
                require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                        and info.st_nlink == 1 and not info.st_mode & 0o077)
        self.db = sqlite3.connect(database, isolation_level=None)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, native_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS requests (
                session TEXT NOT NULL, id TEXT NOT NULL, digest TEXT, state TEXT NOT NULL,
                admission TEXT, cancelled INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(session,id));
        """)
        self.instance = str(uuid.uuid4())
        self.creating = set()
        self.submitting = set()
        self.stopping = set()
        self.subscribers = {}

    def row(self, session):
        row = self.db.execute("SELECT native_id FROM sessions WHERE id=?", (session,)).fetchone()
        require(row is not None, 404)
        return row[0]

    def receipt(self, session, request):
        return self.db.execute("SELECT digest,state,admission,cancelled FROM requests WHERE session=? AND id=?",
                               (session, request)).fetchone()

    async def snapshot(self, session, *, history=False):
        snapshot = await self.client.rpc("session.resume", session_id=self.row(session))
        # The pinned transport caps frames at 8 MiB. This adapter additionally caps
        # rendering work; the canonical API currently has no paged snapshot verb.
        if history:
            require(type(snapshot.get("messages")) is list and len(snapshot["messages"]) <= 4096, 502)
        require(type(snapshot.get("pending")) is list and len(snapshot["pending"]) <= 128, 502)
        return snapshot

    async def handle(self, data):
        require(type(data) is dict)
        action = data.get("action")
        if action == "sessions":
            exact(data, ["action"])
            listing = await self.client.rpc("session.list", scope="stored", limit=200)
            require(type(listing.get("sessions")) is list and len(listing["sessions"]) <= 200, 502)
            mapped = dict(self.db.execute("SELECT native_id,id FROM sessions"))
            rows = []
            for row in listing["sessions"]:
                identity = mapped.get(row.get("id"))
                if identity is not None:
                    title = row.get("title") or "Hermes"
                    count = row.get("message_count")
                    require(type(title) is str and len(title.encode()) <= 1024
                            and type(count) is int and count >= 0 and type(row.get("running")) is bool, 502)
                    rows.append({"id": identity, "title": title, "messageCount": count, "isRunning": row["running"]})
            return {"sessions": rows}
        session = data.get("sessionID")
        require(type(session) is str and SESSION.fullmatch(session))
        minimal = ["action", "sessionID"]
        if action == "ensure":
            exact(data, minimal + ["cwd"])
            require(data["cwd"] == "/workspace/project")
            found = self.db.execute("SELECT native_id FROM sessions WHERE id=?", (session,)).fetchone()
            if not found:
                require(session not in self.creating, 409)
                require(self.db.execute("SELECT count(*) FROM sessions").fetchone()[0] + len(self.creating) < 200, 503)
                self.creating.add(session)
                try:
                    # Canonical gateway request_id is durable; standalone TUI's
                    # idempotency_key is a different protocol and must not be used.
                    snapshot = await self.client.rpc("session.create", request_id=session, source="cli",
                                                     cwd=self.directory, accept_hooks=False)
                    native_id = snapshot.get("stored_session_id")
                    require(type(native_id) is str and 1 <= len(native_id) <= 256, 502)
                    self.db.execute("INSERT INTO sessions VALUES (?,?)", (session, native_id))
                finally:
                    self.creating.discard(session)
            await self.snapshot(session)
            return {"sessionID": session}
        native_id = self.row(session)
        if action == "catalog":
            exact(data, minimal)
            return {"ownerInstance": self.instance, "revision": "hermes-v1", "commands": []}
        if action == "interactions":
            exact(data, minimal)
            return {"ownerInstance": self.instance, "interactions": []}
        if action == "state":
            exact(data, minimal)
            snapshot = await self.snapshot(session)
            running = snapshot.get("running") is True
            return {"running": True, "state": {"isStreaming": running, "isPromptRunning": running,
                    "isBashRunning": False, "isCompacting": False}}
        if action == "history":
            exact(data, minimal + ["before"])
            before = data["before"]
            require(before is None or type(before) is str and re.fullmatch(r"[1-9][0-9]{0,18}", before))
            snapshot = await self.snapshot(session, history=True)
            rows = []
            for row in snapshot["messages"]:
                if row.get("role") not in ("user", "assistant", "tool"):
                    continue
                identity = row.get("row_id", row.get("_row_id"))
                require(type(identity) is int and identity > 0, 502)
                if before is None or identity < int(before):
                    rows.append((str(identity), row))
            page = rows[-50:]
            messages = [message(row[1]) for row in page]
            require(len(json.dumps(messages).encode()) <= 262144, 502)
            return {"context": {"messages": messages, "entryIds": [row[0] for row in page],
                                "hasMore": len(rows) > 50},
                    "session": {"id": session, "cwd": "/workspace/project"}, "messages": messages}
        if action in ("operation", "invoke"):
            exact(data, minimal + (["requestID"] if action == "operation" else ["requestID", "ownerInstance", "message"]))
            request = data["requestID"]
            require(type(request) is str and REQUEST.fullmatch(request))
            existing = self.receipt(session, request)
            if action == "operation":
                require(existing is not None, 404)
                return {"requestID": request, "sessionID": session, "state": existing[1]}
            require(data["ownerInstance"] == self.instance and session not in self.stopping, 409)
            text = data["message"]
            require(type(text) is str and text.strip() and "\0" not in text and len(text.encode()) <= 32768)
            digest = hashlib.sha256(text.encode()).hexdigest()
            if existing:
                require(existing[0] == digest and not existing[3], 409)
                return {"requestID": request, "sessionID": session, "state": existing[1]}
            require(self.db.execute("SELECT count(*) FROM requests").fetchone()[0] < 10000, 503)
            # Commit uncertainty before crossing the gateway boundary. Neither restart nor
            # a retry resends this prompt; Stop can retire it without claiming non-execution.
            self.db.execute("INSERT INTO requests(session,id,digest,state) VALUES (?,?,?,'dispatched')",
                            (session, request, digest))
            self.submitting.add((session, request))
            try:
                result = await self.client.rpc("prompt.submit", session_id=native_id, input_id=request,
                                               text=text, finite=True, unattended=True)
                require(type(result.get("admission_id")) is str, 502)
                self.db.execute("UPDATE requests SET state='accepted',admission=? WHERE session=? AND id=?",
                                (result["admission_id"], session, request))
                if self.receipt(session, request)[3]:
                    await self.stop(session)
                else:
                    self.publish(session, {"type": "agent_start"})
                return {"requestID": request, "sessionID": session, "state": "accepted"}
            finally:
                self.submitting.discard((session, request))
        if action == "abort":
            exact(data, minimal + ["pendingRequests"])
            pending = data["pendingRequests"]
            require(type(pending) is list and len(pending) <= 128
                    and all(type(item) is str and REQUEST.fullmatch(item) for item in pending))
            require(self.db.execute("SELECT count(*) FROM requests").fetchone()[0] + len(pending) <= 10000, 503)
            with self.db:
                for request in pending:
                    self.db.execute("INSERT OR IGNORE INTO requests(session,id,state,cancelled) VALUES (?,?,'rejected',1)",
                                    (session, request))
                    self.db.execute("UPDATE requests SET cancelled=1 WHERE session=? AND id=?", (session, request))
            require(session not in self.stopping, 409)
            self.stopping.add(session)
            try:
                async with asyncio.timeout(5):
                    await self.stop(session)
                # A still-unacknowledged submit may reach the gateway later. Its
                # durable fence remains, but Stop cannot claim completion yet.
                require(not any(key[0] == session for key in self.submitting), 503)
                require(self.db.execute("SELECT 1 FROM requests WHERE session=? AND state='dispatched' LIMIT 1", (session,)).fetchone() is None, 503)
                return {"accepted": True}
            finally:
                self.stopping.discard(session)
        raise Rejected(422)

    async def stop(self, session):
        native_id = self.row(session)
        snapshot = await self.snapshot(session)
        for admission in snapshot["pending"]:
            if admission["status"] == "queued":
                await self.client.rpc("prompt.cancel", session_id=native_id, admission_id=admission["admission_id"])
        if snapshot.get("running"):
            await self.client.rpc("session.interrupt", session_id=native_id,
                                  execution_generation=snapshot["execution_generation"])
        settled = await self.snapshot(session)
        require(not settled.get("running") and all(row.get("status") == "terminal" for row in settled["pending"]), 503)

    def disconnect(self, session):
        for queue in self.subscribers.pop(session, ()):
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(None)

    def publish(self, session, event):
        raw = json.dumps(event, separators=(",", ":")).encode()
        if len(raw) > 1048576:
            self.disconnect(session)
            return False
        for queue in tuple(self.subscribers.get(session, ())):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
                self.subscribers[session].remove(queue)
            else:
                queue.put_nowait(raw)
        return True

    async def events(self):
        partial = {}
        while True:
            frame = await self.client.events.get()
            if isinstance(frame, Exception):
                raise frame
            event = frame.get("params", {})
            native_id = event.get("session_id")
            row = self.db.execute("SELECT id FROM sessions WHERE native_id=?", (native_id,)).fetchone()
            if not row:
                continue
            session = row[0]
            kind, payload = event.get("type"), event.get("payload", {})
            if kind == "session.replay_gap":
                # Reconnect requires canonical history before a new stream. Ignore
                # remaining deltas until a complete message restores the boundary.
                partial[session] = None
                self.disconnect(session)
            elif kind in ("tool.start", "tool.complete"):
                if session in partial and partial[session] is None:
                    continue
                if session in partial and partial[session] is not None:
                    self.publish(session, {"type": "message_end", "message": message({"role": "assistant", "content": partial.pop(session)})})
                identity, name = payload.get("tool_call_id"), payload.get("tool_name")
                if not (type(identity) is str and 1 <= len(identity) <= 256
                        and type(name) is str and 1 <= len(name) <= 256):
                    self.disconnect(session)
                    continue
                if kind == "tool.start":
                    self.publish(session, {"type": "tool_execution_start", "toolCallId": identity,
                                           "toolName": name, "args": payload.get("args") or {}})
                else:
                    result = payload.get("result")
                    if type(result) is not str or len(result.encode()) > 65536:
                        self.disconnect(session)
                        continue
                    self.publish(session, {"type": "tool_execution_end", "toolCallId": identity,
                        "toolName": name, "result": {"content": [{"type": "text", "text": result}]},
                        "isError": payload.get("is_error") is True})
            elif kind == "message.delta":
                text = payload.get("text") or payload.get("delta") or payload.get("content") or ""
                if session in partial and partial[session] is None:
                    continue
                if type(text) is not str:
                    partial[session] = None
                    self.disconnect(session)
                    continue
                value = partial.get(session, "") + text
                if len(value.encode()) > 65536:
                    partial[session] = None
                    self.disconnect(session)
                    continue
                if session not in partial:
                    self.publish(session, {"type": "message_start", "message": message({"role": "assistant", "content": ""})})
                partial[session] = value
                rendered = {"role": "assistant", "content": [{"type": "text", "text": value}]}
                if not self.publish(session, {"type": "message_update", "message": rendered,
                    "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": text, "partial": rendered}}):
                    partial[session] = None
            elif kind == "message.complete":
                if session in partial and partial[session] is None:
                    partial.pop(session, None)
                    self.disconnect(session)
                    continue
                text = payload.get("text") or payload.get("content") or partial.get(session, "")
                partial.pop(session, None)
                try:
                    rendered = message({"role": "assistant", "content": text})
                except Rejected:
                    self.disconnect(session)
                    continue
                if payload.get("status") in ("error", "interrupted"):
                    rendered["stopReason"] = "error" if payload["status"] == "error" else "aborted"
                    rendered["errorMessage"] = "Hermes turn " + payload["status"]
                self.publish(session, {"type": "message_end", "message": rendered})
                self.publish(session, {"type": "agent_end", "messages": []})

    def close(self):
        self.db.close()


def message(row):
    content = row.get("content") or ""
    require(type(content) is str and len(content.encode()) <= 65536, 502)
    result = {"role": "toolResult" if row["role"] == "tool" else row["role"],
              "content": [{"type": "text", "text": content}]}
    if row["role"] == "tool":
        result.update(toolCallId=row.get("tool_call_id", ""), toolName=row.get("name", "tool"), isError=False)
    return result


def application_for(owner, authorization):
    from aiohttp import web

    inflight = 0
    ordinary = 0
    controls = 0

    async def handler(request):
        nonlocal inflight, ordinary, controls
        headers = request.headers.getall("Authorization", [])
        if len(headers) != 1 or not hmac.compare_digest(headers[0].encode(), authorization):
            return web.json_response({"error": "unauthorized"}, status=401)
        if inflight >= 32:
            return web.json_response({"error": "unavailable"}, status=503)
        inflight += 1
        lane = None
        try:
            require(request.method == "POST" and request.path_qs == "/api/native-agents-owner", 404)
            require(request.content_type == "application/json")
            async with asyncio.timeout(5):
                data = await request.json()
            if type(data) is dict and data.get("action") == "abort":
                require(controls < 2, 503)
                controls += 1
                lane = "control"
            else:
                require(ordinary < 14, 503)
                ordinary += 1
                lane = "ordinary"
            if type(data) is dict and data.get("action") == "events":
                exact(data, ["action", "sessionID"])
                session = data["sessionID"]
                require(type(session) is str and SESSION.fullmatch(session))
                owner.row(session)
                queue = asyncio.Queue(maxsize=64)
                viewers = owner.subscribers.setdefault(session, set())
                require(len(viewers) < 8, 503)
                viewers.add(queue)
                response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store"})
                try:
                    await response.prepare(request)
                    await response.write(b'data: {"type":"connected"}\n\n')
                    while True:
                        try:
                            value = await asyncio.wait_for(queue.get(), 1)
                        except TimeoutError:
                            value = b""
                        if value is None:
                            break
                        async with asyncio.timeout(3):
                            await response.write(b"data: " + value + b"\n\n" if value else b": heartbeat\n\n")
                except (ConnectionError, TimeoutError):
                    pass
                finally:
                    viewers.discard(queue)
                return response
            result = await owner.handle(data)
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except web.HTTPException as error:
            return web.json_response({"error": "invalid_request"}, status=error.status)
        except Rejected as error:
            return web.json_response({"error": "request_rejected"}, status=error.status)
        except (ValueError, UnicodeError):
            return web.json_response({"error": "invalid_request"}, status=400)
        except Exception as error:
            # Messages can contain provider diagnostics or user text. Keep logs metadata-only.
            print("owner_request_failed:" + type(error).__name__, flush=True)
            return web.json_response({"error": "owner_unavailable"}, status=503)
        finally:
            inflight -= 1
            if lane == "control":
                controls -= 1
            elif lane == "ordinary":
                ordinary -= 1

    application = web.Application(client_max_size=65536)
    application.router.add_route("*", "/{path:.*}", handler)
    return application


async def serve():
    # aiohttp and the canonical client are supplied by the pinned Hermes image.
    from aiohttp import web
    from hermes_cli.gateway_client import connect_gateway

    config = json.loads(os.environ["ANVIL_HERMES_CONFIG"])
    exact(config, ["directory", "storage", "port"])
    require(type(config["port"]) is int and 1024 <= config["port"] <= 65535)
    storage = private_directory(config["storage"])
    token_file = storage / "owner-token"
    try:
        descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            token = secrets.token_hex(32).encode()
            require(os.write(descriptor, token) == len(token))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except FileExistsError:
        pass
    token = private_file(token_file).decode()
    require(re.fullmatch(r"[0-9a-f]{64}", token))
    authorization = ("Bearer " + token).encode()
    async with connect_gateway() as client:
        owner = Owner(client, config["directory"], config["storage"])
        application = application_for(owner, authorization)
        runner = web.AppRunner(application, access_log=None, shutdown_timeout=5)
        try:
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", config["port"]).start()
            await owner.events()
        finally:
            await runner.cleanup()
            owner.close()


if __name__ == "__main__":
    os.umask(0o077)
    asyncio.run(serve())
