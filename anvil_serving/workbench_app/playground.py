"""Bounded conversations against explicitly declared local inference connections."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

from ..observability.dashboard.contracts import ObservatoryError, digest, fields, identifier, strict_json
from .credentials import resolve_secret


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Playground:
    def __init__(self, config, store, access, environment, *, open_request=None):
        self.config, self.store, self.access = config, store, access
        self.environment = environment
        self.open_request = open_request or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open
        self.lock = threading.RLock()
        self.active = {}
        self.workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="workbench-chat")
        self.store.recover("conversation", {"running", "cancel_requested"})

    def connector(self, session, connector_id, *, mutation=False):
        connector = next((c for c in self.config.get("connectors", []) if c["id"] == connector_id), None)
        if not connector:
            raise ObservatoryError("not_found", "This connection is unavailable.", 404)
        self.access.permit(session, connector["resource_id"], "playground.request" if mutation else None)
        if mutation and (reference := connector.get("token_ref", connector.get("token_env"))):
            resolve_secret(reference, self.environment)
        return connector

    def catalog(self, session):
        connectors = []
        for row in self.config.get("connectors", []):
            if not session.principal.can_read(row["resource_id"]):
                continue
            configured = True
            try:
                if reference := row.get("token_ref", row.get("token_env")):
                    resolve_secret(reference, self.environment)
            except ObservatoryError:
                configured = False
            connectors.append({key: row[key] for key in ("id", "label", "resource_id", "models", "host_id", "max_output_tokens") if key in row} | {
                "configured": configured,
                "permitted": bool(self.access.operate and session.principal.can_operate(row["resource_id"], "playground.request")),
            })
        return {"connectors": connectors, "presets": self.config.get("presets", []), "retention_days": self.config.get("retention_days", 30)}

    def conversations(self, session):
        rows = self.store.list("conversation", session.principal.identity)
        allowed = {c["id"] for c in self.config.get("connectors", []) if session.principal.can_read(c["resource_id"])}
        return {"items": [{k: row.get(k) for k in ("id", "title", "connector_id", "model", "status", "updated_at")} for row in rows if row["connector_id"] in allowed]}

    def read(self, session, key):
        item = self.store.get("conversation", session.principal.identity, identifier(key))
        self.connector(session, item["connector_id"])
        return item

    def send(self, session, body):
        fields(body, required=("connector_id", "model", "preset_id", "message", "request_id"), optional=("conversation_id",))
        connector = self.connector(session, identifier(body["connector_id"]), mutation=True)
        if body["model"] not in connector["models"]:
            raise ObservatoryError("model_unavailable", "Choose an explicitly declared model.")
        preset = next((p for p in self.config.get("presets", []) if p["id"] == body["preset_id"]), None)
        if not preset:
            raise ObservatoryError("preset_unavailable", "Choose a declared request preset.")
        message = body["message"]
        if type(message) is not str or not message.strip() or len(message.encode()) > 32768:
            raise ObservatoryError("message_limit", "Enter a message of at most 32 KiB.")
        request_id = identifier(body["request_id"])
        owner = session.principal.identity
        fingerprint = digest(body)
        with self.lock:
            try:
                receipt = self.store.get("chat-request", owner, request_id)
            except ObservatoryError as error:
                if error.status != 404:
                    raise
                receipt = None
            if receipt:
                if receipt["digest"] != fingerprint:
                    raise ObservatoryError("request_conflict", "This request identity belongs to different input.", 409)
                try:
                    return self.read(session, receipt["conversation_id"])
                except ObservatoryError as error:
                    if error.status != 404:
                        raise
                    raise ObservatoryError("request_retained", "This request was already accepted or removed. It will not be replayed.", 409) from None
            if len(self.active) >= 2:
                raise ObservatoryError("chat_busy", "The conversation workers are busy. Retry after a request completes.", 429)
            if body.get("conversation_id"):
                item = self.read(session, body["conversation_id"])
                if item["status"] in {"running", "cancel_requested"}:
                    raise ObservatoryError("chat_busy", "Wait for the active turn or cancel it.", 409)
                if item["connector_id"] != connector["id"] or item["model"] != body["model"]:
                    raise ObservatoryError("target_conflict", "Start a new conversation to change its model or connection.", 409)
                if len(item["messages"]) >= 100:
                    raise ObservatoryError("conversation_limit", "Start a new conversation after 50 turns.", 409)
            else:
                if len(self.store.list("conversation", owner)) >= 200:
                    raise ObservatoryError("conversation_limit", "Delete an old conversation before creating another.", 409)
                item = {"id": str(uuid.uuid4()), "title": message[:80], "connector_id": connector["id"], "model": body["model"], "messages": []}
            effective = {"temperature": preset["temperature"], "max_tokens": min(preset["max_tokens"], connector.get("max_output_tokens", 4096)), "system": preset.get("system", ""), "preset_id": preset["id"]}
            item["messages"].append({"role": "user", "content": message, "effective": effective})
            item.update(status="running", request_id=request_id, request_digest=fingerprint, effective=effective, output="", usage=None, error=None, updated_at=time.time())
            # Retain the request identity independently from the conversation's
            # latest turn. A delayed retry of an older turn cannot dispatch again.
            self.store.put("chat-request", owner, request_id, {"digest": fingerprint, "conversation_id": item["id"]})
            self.store.put("conversation", owner, item["id"], item)
            event = threading.Event()
            self.active[(owner, item["id"])] = event
            self.workers.submit(self._run, owner, item, connector, event)
            return dict(item)

    def _save(self, owner, item):
        item["updated_at"] = time.time()
        self.store.put("conversation", owner, item["id"], item)

    def _run(self, owner, item, connector, cancelled):
        started = time.monotonic()
        response = None
        try:
            effective = item["effective"]
            history = [{"role": message["role"], "content": message["content"]} for message in item["messages"]]
            messages = ([{"role": "system", "content": effective["system"]}] if effective["system"] else []) + history
            payload = {"model": item["model"], "messages": messages, "temperature": effective["temperature"], "max_tokens": effective["max_tokens"], "stream": True, "stream_options": {"include_usage": True}}
            headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
            if reference := connector.get("token_ref", connector.get("token_env")):
                headers["Authorization"] = "Bearer " + resolve_secret(reference, self.environment)
            req = urllib.request.Request(connector["base_url"].rstrip("/") + "/chat/completions", data=json.dumps(payload).encode(), headers=headers, method="POST")
            # Connection is configured by the operator; redirects and ambient proxies are disabled.
            response = self.open_request(req, timeout=min(30, connector.get("timeout_seconds", 120)))
            total = 0
            complete = False
            last_save = 0
            for _ in range(10000):
                if cancelled.is_set():
                    break
                if time.monotonic() - started > connector.get("timeout_seconds", 120):
                    raise TimeoutError()
                line = response.readline(65537)
                if not line:
                    break
                total += len(line)
                if len(line) > 65536 or total > 2 * 1024 * 1024:
                    raise ValueError("upstream stream limit")
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    complete = True
                    break
                packet = strict_json(data)
                choices = packet.get("choices", [])
                for choice in choices[:1]:
                    delta = choice.get("delta", {}).get("content", "") or ""
                    if type(delta) is not str:
                        raise ValueError("invalid model delta")
                    item["output"] += delta
                    if len(item["output"].encode()) > 131072:
                        raise ValueError("model output limit")
                    if choice.get("finish_reason"):
                        item["finish_reason"] = str(choice["finish_reason"])[:80]
                usage = packet.get("usage")
                if type(usage) is dict:
                    item["usage"] = {key: val for key, val in usage.items() if key in {"prompt_tokens", "completion_tokens", "total_tokens"} and type(val) is int and 0 <= val <= 10**9}
                if time.monotonic() - last_save > 0.25:
                    self._save(owner, item)
                    last_save = time.monotonic()
            item["status"] = "cancelled" if cancelled.is_set() else "completed" if complete else "interrupted"
            if item["status"] == "interrupted":
                item["error"] = "The model stream ended before completion. Partial output is retained."
        except Exception:
            item["status"] = "cancelled" if cancelled.is_set() else "failed"
            item["error"] = None if cancelled.is_set() else "The selected model request failed. Inspect its authorized logs; no alternate model was called."
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            item["duration_seconds"] = round(time.monotonic() - started, 3)
            if item["output"]:
                item["messages"].append({"role": "assistant", "content": item["output"], "status": item["status"]})
            with self.lock:
                try:
                    self._save(owner, item)
                finally:
                    self.active.pop((owner, item["id"]), None)

    def cancel(self, session, key):
        with self.lock:
            item = self.read(session, key)
            self.connector(session, item["connector_id"], mutation=True)
            event = self.active.get((session.principal.identity, key))
            if event:
                event.set()
                item["status"] = "cancel_requested"
                self._save(session.principal.identity, item)
            return item

    def delete(self, session, key):
        with self.lock:
            item = self.read(session, key)
            if (session.principal.identity, key) in self.active:
                raise ObservatoryError("chat_busy", "Cancel the active turn and wait for it to stop before deletion.", 409)
            self.store.delete("conversation", session.principal.identity, key)
            return {"deleted": item["id"]}

    def close(self):
        with self.lock:
            for event in self.active.values():
                event.set()
        self.workers.shutdown(wait=True)
