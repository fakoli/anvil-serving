"""Best-effort, metadata-only OTLP/HTTP trace export for router decisions."""
from __future__ import annotations

import ipaddress
import json
import os
import queue
import threading
import urllib.parse
import urllib.request
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .decision_log import DecisionRecord


_MAX_QUEUE = 128
_BATCH_SIZE = 16
_PRIVATE_NETWORKS = tuple(map(ipaddress.ip_network, (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12",
    "192.168.0.0/16", "100.64.0.0/10", "fc00::/7",
)))


def validate_export_url(value: object) -> str:
    """Accept a collector URL only when it names a private literal address."""
    if not isinstance(value, str) or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise ValueError("trace_export_url must be an explicit private http(s) collector URL")
    try:
        parsed = urllib.parse.urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or "\\" in value
        ):
            raise ValueError
        if parsed.port == 0:
            raise ValueError
        address = ipaddress.ip_address(parsed.hostname)
        if not any(address in network for network in _PRIVATE_NETWORKS):
            raise ValueError
        path = parsed.path or "/v1/traces"
        if not path.startswith("/"):
            raise ValueError
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    except (ValueError, TypeError):
        raise ValueError("trace_export_url must be an explicit private http(s) collector URL") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TraceExporter:
    """Queue terminal metadata for a private OTLP/HTTP collector without retries."""

    def __init__(self, url: str, *, queue_size: int = _MAX_QUEUE, timeout: float = 1.0) -> None:
        self.url = validate_export_url(url)
        if isinstance(queue_size, bool) or not isinstance(queue_size, int) or not 1 <= queue_size <= _MAX_QUEUE:
            raise ValueError(f"trace queue_size must be an integer from 1 to {_MAX_QUEUE}")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 5:
            raise ValueError("trace timeout must be greater than zero and at most five seconds")
        self._timeout = float(timeout)
        self._queue: queue.Queue[dict | None] = queue.Queue(maxsize=queue_size)
        self._lock = threading.Lock()
        self._closed = False
        self._dropped = self._failed = self._exported = 0
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open
        self._worker = threading.Thread(target=self._run, name="anvil-router-traces", daemon=True)
        self._worker.start()

    def __call__(self, record: "DecisionRecord") -> None:
        self.export(record)

    def export(self, record: "DecisionRecord") -> None:
        span = _span(record)
        with self._lock:
            if self._closed:
                self._dropped += 1
                return
            try:
                self._queue.put_nowait(span)
            except queue.Full:
                self._dropped += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "queued": self._queue.qsize(), "dropped": self._dropped,
                "failed": self._failed, "exported": self._exported,
            }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        discarded = 0
        while True:
            try:
                self._queue.get_nowait()
                discarded += 1
            except queue.Empty:
                break
        with self._lock:
            self._dropped += discarded
        self._queue.put_nowait(None)
        self._worker.join(self._timeout + 0.1)

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                with self._lock:
                    if self._closed:
                        return
                continue
            if item is None:
                return
            batch = [item]
            while len(batch) < _BATCH_SIZE:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    self._send(batch)
                    return
                batch.append(item)
            self._send(batch)

    def _send(self, spans: list[dict]) -> None:
        # urllib's timeout bounds each blocking transport operation. This
        # worker is isolated from inference and its queue stays bounded.
        body = json.dumps({"resourceSpans": [{"scopeSpans": [{
            "scope": {"name": "anvil-serving.router"}, "spans": spans,
        }]}]}, separators=(",", ":")).encode()
        request = urllib.request.Request(
            self.url, data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                if not 200 <= response.status < 300:
                    raise OSError("collector rejected export")
            with self._lock:
                self._exported += len(spans)
        except Exception:
            with self._lock:
                self._failed += len(spans)


def _span(record: "DecisionRecord") -> dict[str, Any]:
    # Import lazily so config validation does not create a decision-log cycle.
    from .decision_log import decision_record_dict

    item = decision_record_dict(record)
    attributes = []
    for key in ("route", "kind", "requested_tier", "served_tier", "session_id", "client_id", "config_sha256", "router_version", "gateway_request_id"):
        value = item.get(key)
        if isinstance(value, str) and value != "-":
            attributes.append({"key": "anvil.router." + key, "value": {"stringValue": value}})
    for key in ("total_prompt_tokens", "total_completion_tokens", "cache_read_input_tokens", "admission_wait_ms", "latency_ms"):
        value = item.get(key)
        if type(value) is int:
            attributes.append({"key": "anvil.router." + key, "value": {"intValue": str(value)}})
    attempts = item.get("attempts", [])
    attempt = attempts[-1] if isinstance(attempts, list) and attempts else None
    upstream_outcome = (
        "success" if isinstance(attempt, dict) and attempt.get("succeeded") is True
        else "error" if attempt is not None else "unknown"
    )
    delivery_outcome = item.get("workload_outcome")
    outcome = delivery_outcome if isinstance(delivery_outcome, str) else upstream_outcome
    attributes.append({"key": "anvil.router.outcome", "value": {"stringValue": outcome}})
    attributes.append({"key": "anvil.router.upstream_outcome", "value": {"stringValue": upstream_outcome}})
    attributes.append({"key": "anvil.router.trace_mode", "value": {"stringValue": "standalone"}})
    end = int(max(float(getattr(record, "unix_ts", 0.0)), 0.0) * 1_000_000_000)
    duration = item.get("latency_ms") if isinstance(item.get("latency_ms"), int) else 0
    start = max(0, end - duration * 1_000_000)
    # OTLP JSON explicitly requires hex IDs, unlike generic protobuf JSON bytes.
    # https://opentelemetry.io/docs/specs/otlp/#json-protobuf-encoding
    return {
        "traceId": os.urandom(16).hex(), "spanId": os.urandom(8).hex(),
        "name": "anvil.router.request", "kind": 2,
        "startTimeUnixNano": str(start), "endTimeUnixNano": str(end),
        "attributes": attributes,
    }
