"""Bounded Loki reads with explicit host/serve grants and no browser LogQL."""
from __future__ import annotations

import json
import re
import threading
import time
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from .contracts import ObservatoryError, fields, identifier
from .metrics_client import WINDOWS, _NoRedirect, _origin

MAX_BYTES = 2 * 1024 * 1024
LIMIT = 500
CONTAINER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,191}\Z")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class LogsClient:
    def __init__(self, config, inventory, *, clock=time.time):
        fields(config, required=("url", "hosts"), optional=("serves",))
        self.origin = _origin(config["url"])
        self.clock = clock
        self.slots = threading.BoundedSemaphore(2)
        self.hosts = config["hosts"]
        self.serves = config.get("serves", {})
        hosts = {h["id"] for h in inventory.get("hosts", [])}
        serves = {s["id"]: s["host_id"] for s in inventory.get("serves", [])}
        if type(self.hosts) is not dict or not self.hosts or len(self.hosts) > 32:
            raise ValueError("Invalid log host mappings")
        for host, label in self.hosts.items():
            identifier(host)
            identifier(label)
            if host not in hosts:
                raise ValueError("Log host must be in inventory")
        if len(set(self.hosts.values())) != len(self.hosts):
            raise ValueError("Log host labels must be unique")
        if type(self.serves) is not dict or len(self.serves) > 128:
            raise ValueError("Invalid log serve mappings")
        for serve, binding in self.serves.items():
            fields(binding, required=("host_id", "containers"))
            containers = binding["containers"]
            if (serve not in serves or binding["host_id"] != serves[serve]
                    or binding["host_id"] not in self.hosts or type(containers) is not list
                    or not 1 <= len(containers) <= 16
                    or any(not isinstance(c, str) or not CONTAINER.fullmatch(c) for c in containers)):
                raise ValueError("Invalid log serve binding")

    def _request(self, path, parameters):
        if not self.slots.acquire(blocking=False):
            raise ObservatoryError("logs_busy", "Log searches are busy; try again shortly.", 429)
        deadline = time.monotonic() + 5
        try:
            request = Request(self.origin + "/loki/api/v1/" + path + "?" + urlencode(parameters),
                              headers={"Accept": "application/json"})
            with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=5) as response:
                if response.headers.get_content_type() != "application/json":
                    raise ValueError("invalid content type")
                body = bytearray()
                while not response.isclosed():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    # Reset the socket timeout against a total request deadline.
                    response.fp.raw._sock.settimeout(remaining)
                    chunk = response.read1(min(65536, MAX_BYTES + 1 - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise ValueError("response bound")
                result = json.loads(body)
                if result.get("status") != "success":
                    raise ValueError("upstream failure")
                return result["data"]
        except Exception:
            raise ObservatoryError("logs_unavailable", "The log source is unavailable or exceeded its read limit. Narrow the filters and retry.", 503) from None
        finally:
            self.slots.release()

    def _scope(self, query, principal):
        fields(query, optional=("host", "serve", "container", "stream", "range", "search"))
        if query.get("range", "1h") not in WINDOWS:
            raise ObservatoryError("invalid_log_filter", "Select a supported log time range.")
        search = query.get("search", "")
        if not isinstance(search, str) or len(search) > 128 or any(ord(c) < 32 for c in search):
            raise ObservatoryError("invalid_log_filter", "Search with up to 128 printable characters.")
        container = query.get("container")
        if container is not None and not CONTAINER.fullmatch(container):
            raise ObservatoryError("invalid_log_filter", "Select a valid container name.")
        stream = query.get("stream")
        if stream is not None and stream not in {"stdout", "stderr"}:
            raise ObservatoryError("invalid_log_filter", "Select stdout or stderr.")
        host, serve = query.get("host"), query.get("serve")
        containers = None
        if serve:
            binding = self.serves.get(serve)
            if not principal.can_read(serve):
                raise ObservatoryError("permission_denied", "This serve is outside your log access.", 403)
            if not binding:
                raise ObservatoryError("logs_not_configured", "No log collector binding is configured for this serve.", 404)
            if host and host != binding["host_id"]:
                raise ObservatoryError("invalid_log_filter", "Select a serve on the selected workstation.")
            host = binding["host_id"]
            containers = binding["containers"]
        elif host and not principal.can_read(host):
            raise ObservatoryError("permission_denied", "This workstation is outside your log access.", 403)
        elif not host and "*" not in principal.resources:
            raise ObservatoryError("permission_denied", "Select an authorized workstation or serve for logs.", 403)
        if host and host not in self.hosts:
            raise ObservatoryError("logs_not_configured", "No log collector is configured for this workstation.", 404)
        if containers is not None and container and container not in containers:
            raise ObservatoryError("permission_denied", "This container is outside the selected serve.", 403)
        labels = [self.hosts[host]] if host else list(self.hosts.values())
        selectors = ['job="docker"', 'stream=~"stdout|stderr"', 'host=~' + json.dumps("|".join(re.escape(x) for x in labels))]
        if container:
            selectors.append('container=' + json.dumps(container))
        elif containers:
            selectors.append('container=~' + json.dumps("|".join(re.escape(x) for x in containers)))
        if stream:
            selectors.append('stream=' + json.dumps(stream))
        return "{" + ",".join(selectors) + "}", set(labels), containers

    def read(self, query, principal, *, sources=False):
        selector, hosts, containers = self._scope(query, principal)
        end = int(self.clock() * 1_000_000_000)
        start = end - WINDOWS[query.get("range", "1h")] * 1_000_000_000
        params = {"start": str(start), "end": str(end)}
        if sources:
            data = self._request("series", {**params, "match[]": selector})
            if type(data) is not list or len(data) > 1000:
                raise ObservatoryError("logs_unavailable", "The container list exceeded its read limit.", 503)
            rows = [{"stream": item, "values": []} for item in data]
        else:
            expression = selector + (" |= " + json.dumps(query["search"]) if query.get("search") else "")
            data = self._request("query_range", {**params, "query": expression, "direction": "backward", "limit": LIMIT})
            if not isinstance(data, dict) or data.get("resultType") != "streams" or type(data.get("result")) is not list:
                raise ObservatoryError("logs_unavailable", "The log source returned an invalid result.", 503)
            rows = data["result"]
        items, available = [], set()
        by_label = {v: k for k, v in self.hosts.items()}
        for row in rows:
            labels = row["stream"]
            host, container = labels.get("host"), labels.get("container", "")
            stream = labels.get("stream", "")
            if (labels.get("job") != "docker" or host not in hosts
                    or not CONTAINER.fullmatch(container) or stream not in {"stdout", "stderr"}
                    or (containers is not None and container not in containers)
                    or (query.get("container") and container != query["container"])
                    or (query.get("stream") and stream != query["stream"])):
                raise ObservatoryError("logs_unavailable", "The log source returned data outside the requested scope.", 503)
            available.add((by_label[host], container))
            for value in row["values"]:
                timestamp, line = value[:2]
                if not isinstance(timestamp, str) or not re.fullmatch(r"[0-9]{1,20}", timestamp) or not start <= int(timestamp) <= end or not isinstance(line, str):
                    raise ObservatoryError("logs_unavailable", "The log source returned an invalid entry.", 503)
                if len(items) >= LIMIT:
                    raise ObservatoryError("logs_unavailable", "The log source exceeded its entry limit.", 503)
                items.append({"timestamp_ns": timestamp, "host_id": by_label[host], "container": container,
                              "stream": stream, "line": ANSI.sub("", line)[:4096], "line_truncated": len(line) > 4096})
        items.sort(key=lambda x: int(x["timestamp_ns"]), reverse=True)
        return {"status": "available", "items": items, "containers": [{"host_id": h, "name": c} for h, c in sorted(available)],
                "limit": LIMIT, "limit_reached": len(items) == LIMIT, "range": query.get("range", "1h"),
                "observed_at": self.clock(), "reason": "No matching entries in this time range." if not items and not sources else None}
