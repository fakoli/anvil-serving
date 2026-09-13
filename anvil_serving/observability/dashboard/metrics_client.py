"""Read-only Observatory projection of an explicitly configured Prometheus.

Private inventory schema (unknown fields rejected):
  hosts: [{id, display_name, platform, metric_host,
           gpus: [{uuid, id, label, role}], maintenance?}]
  serves: [{id, host_id, model, engine, metric_serve, gpu_ids, aliases?,
            display_name?, controller?}]
``controller`` is an opaque private binding for the owner adapter; this module
does not interpret, contact, or expose it. Inventory does not prove readiness,
ownership, admission, or current model identity. Those remain owner reads.

The metric names below implement the existing canonical monitoring contract.
No browser input is accepted as PromQL, a metric name, URL, or label matcher.
"""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
import copy
import hashlib
import ipaddress
import json
import math
import re
import threading
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


MAX_SERIES = 8
MAX_POINTS = 1000  # across all series in one chart
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
FRESH_SECONDS = 90
WINDOWS = {"15m": 900, "1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}
INFERENCE = {
    "generation": ("Generation", "tokens/s", "output_tokens_per_second"),
    "ttft": ("Time to first token · p95", "seconds", "ttft_p95_seconds"),
    "queue": ("Waiting requests", "requests", "requests_waiting"),
    "prompt": ("Prompt input", "tokens/s", "input_tokens_per_second"),
    "kv_cache": ("Active KV cache", "ratio", "kv_cache_ratio"),
    "errors": ("Request errors", "requests/s", None),
}
CHARTS = {
    **{key: (value[0], value[1]) for key, value in INFERENCE.items()},
    "gpu_memory": ("GPU memory used", "bytes"),
    "gpu_utilization": ("GPU utilization", "ratio"),
    "host_cpu": ("Host CPU busy", "ratio"),
    "host_ram": ("Host physical memory used", "bytes"),
    "host_disk": ("Disk I/O by device", "bytes/s"),
    "host_network": ("Network I/O by interface", "bytes/s"),
}
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")
_MODEL = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:/@+\-]{0,191}\Z")
_UUID = re.compile(r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


class MetricsError(ValueError):
    """Safe local validation or bounded collection failure."""


def _text(value, *, identifier=False):
    if not isinstance(value, str) or not value or len(value) > 192:
        raise MetricsError("Invalid inventory text")
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or "://" in value:
        raise MetricsError("Invalid inventory text")
    if identifier and not _ID.fullmatch(value):
        raise MetricsError("Invalid inventory identifier")
    return value


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or set(value) - set(required) - set(optional):
        raise MetricsError("Invalid inventory fields")


def validate_inventory(inventory):
    """Validate explicit mappings without discovery, file reads or owner contact."""
    _keys(inventory, {"hosts", "serves"})
    if not isinstance(inventory["hosts"], list) or not 1 <= len(inventory["hosts"]) <= 32:
        raise MetricsError("Invalid host inventory bound")
    if not isinstance(inventory["serves"], list) or len(inventory["serves"]) > 128:
        raise MetricsError("Invalid serve inventory bound")
    hosts, metric_hosts, uuids, ids = {}, set(), set(), set()
    for host in inventory["hosts"]:
        _keys(host, {"id", "display_name", "platform", "metric_host", "gpus"}, {"maintenance"})
        for field in ("id", "metric_host"):
            _text(host[field], identifier=True)
        _text(host["display_name"])
        if host["id"] in hosts or host["metric_host"] in metric_hosts:
            raise MetricsError("Duplicate host mapping")
        if not isinstance(host["platform"], str) or host["platform"] not in {"linux", "windows", "macos", "unknown"}:
            raise MetricsError("Invalid host platform")
        if host.get("maintenance", "none") not in {"none", "planned"}:
            raise MetricsError("Invalid maintenance value")
        if not isinstance(host["gpus"], list) or len(host["gpus"]) > 16:
            raise MetricsError("Invalid GPU inventory bound")
        gpu_ids = set()
        for gpu in host["gpus"]:
            _keys(gpu, {"uuid", "id", "label", "role"})
            _text(gpu["id"], identifier=True)
            _text(gpu["label"])
            _text(gpu["role"], identifier=True)
            if not isinstance(gpu["uuid"], str) or not _UUID.fullmatch(gpu["uuid"]):
                raise MetricsError("Invalid GPU UUID")
            if gpu["uuid"] in uuids or gpu["id"] in gpu_ids:
                raise MetricsError("Duplicate GPU mapping")
            uuids.add(gpu["uuid"])
            gpu_ids.add(gpu["id"])
        hosts[host["id"]] = host
        metric_hosts.add(host["metric_host"])
    bindings = set()
    for serve in inventory["serves"]:
        _keys(serve, {"id", "host_id", "model", "engine", "metric_serve", "gpu_ids"},
              {"aliases", "display_name", "controller"})
        for field in ("id", "host_id", "engine", "metric_serve"):
            _text(serve[field], identifier=True)
        if serve["id"] in ids or serve["host_id"] not in hosts:
            raise MetricsError("Invalid serve mapping")
        binding = (serve["host_id"], serve["metric_serve"])
        if binding in bindings:
            raise MetricsError("Duplicate serve metric mapping")
        bindings.add(binding)
        ids.add(serve["id"])
        if not isinstance(serve["model"], str) or not _MODEL.fullmatch(serve["model"]) or "://" in serve["model"]:
            raise MetricsError("Invalid model identifier")
        if "display_name" in serve:
            _text(serve["display_name"])
        gpu_ids = serve["gpu_ids"]
        if not isinstance(gpu_ids, list) or not all(isinstance(g, str) for g in gpu_ids) or len(set(gpu_ids)) != len(gpu_ids) or not set(gpu_ids) <= {g["id"] for g in hosts[serve["host_id"]]["gpus"]}:
            raise MetricsError("Unknown or duplicate serve GPU")
        aliases = serve.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases) or len(aliases) > 32 or len(set(aliases)) != len(aliases):
            raise MetricsError("Invalid aliases")
        for alias in aliases:
            _text(alias, identifier=True)
        if "controller" in serve and not isinstance(serve["controller"], dict):
            raise MetricsError("Invalid private controller binding")
    return copy.deepcopy(inventory)


def _origin(value, *, grafana=False):
    if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 33 for c in value):
        raise MetricsError("Invalid integration origin")
    if grafana and value == "/grafana":
        return value
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise MetricsError("Invalid integration origin") from None
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or
            parsed.query or parsed.fragment or parsed.hostname == "localhost" or
            not re.fullmatch(r"(?:/[a-zA-Z0-9_.-]+)*/?", parsed.path) or
            any(p in {".", ".."} for p in parsed.path.split("/")) or port == 0):
        raise MetricsError("Invalid integration origin")
    if parsed.scheme == "http":
        try:
            ip = ipaddress.ip_address(parsed.hostname)
            allowed = ip.is_loopback if grafana else (
                (ip.is_private or ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10"))
                and not (ip.is_unspecified or ip.is_multicast or ip.is_link_local))
        except ValueError:
            allowed = not grafana and bool(re.fullmatch(r"[a-zA-Z0-9-]+", parsed.hostname))
        if not allowed:
            raise MetricsError("Insecure integration origin")
    return value.rstrip("/")


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and not isinstance(value, bool) else None


def _selector(metric_name, **labels):
    return metric_name + "{" + ",".join(key + "=" + json.dumps(value) for key, value in labels.items()) + "}"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class MetricsClient:
    """Bounded read facade. No controller or model request is ever dispatched."""

    def __init__(self, *, prometheus_url, inventory, grafana_url=None, timeout=5, clock=time.time):
        self._origin = _origin(prometheus_url)
        self._grafana = _origin(grafana_url, grafana=True) if grafana_url else None
        self.inventory = validate_inventory(inventory)
        self._hosts = {h["id"]: h for h in self.inventory["hosts"]}
        self._serves = {s["id"]: s for s in self.inventory["serves"]}
        if type(timeout) not in (int, float) or not 0 < timeout <= 5:
            raise MetricsError("Timeout must be at most five seconds")
        self.timeout, self.clock = timeout, clock
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(4)
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="observatory-metrics")
        self._inflight, self._cache, self._last_known = {}, OrderedDict(), {}
        self._last_success = self._last_failure = None

    def close(self):
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _request(self, path, parameters):
        """Identical in-flight reads coalesce; response cache is bounded and short."""
        key = (path, tuple(sorted(parameters.items())))
        with self._lock:
            cached = self._cache.get(key)
            if cached and 0 <= self.clock() - cached[0] <= 10:
                return copy.deepcopy(cached[1])
            if self._last_failure is not None and 0 <= self.clock() - self._last_failure < 2:
                raise MetricsError("Metrics read unavailable")
            future = self._inflight.get(key)
            leader = future is None
            if leader:
                future = Future()
                self._inflight[key] = future
        if not leader:
            try:
                return copy.deepcopy(future.result(timeout=self.timeout))
            except Exception:
                raise MetricsError("Metrics read unavailable") from None
        try:
            deadline = time.monotonic() + self.timeout
            if not self._slots.acquire(timeout=self.timeout):
                raise MetricsError("Metrics read capacity reached")
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MetricsError("Metrics read timed out")
                request = Request(self._origin + path + "?" + urlencode(parameters), headers={"Accept": "application/json"})
                with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=remaining) as response:
                    if response.headers.get_content_type() != "application/json":
                        raise MetricsError("Unexpected metrics response")
                    chunks, size = [], 0
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise MetricsError("Metrics read timed out")
                        # HTTPResponse.read1 makes one socket read, allowing the total
                        # request deadline to be reapplied between chunks.
                        sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                        if sock is not None:
                            sock.settimeout(remaining)
                        chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise MetricsError("Metrics response exceeds bound")
                data = json.loads(b"".join(chunks))
                if (not isinstance(data, dict) or data.get("status") != "success" or
                        not isinstance(data.get("data"), dict) or
                        not isinstance(data["data"].get("result"), list)):
                    raise MetricsError("Unexpected metrics response")
                with self._lock:
                    self._last_success = self.clock()
                    self._cache[key] = (self.clock(), data["data"])
                    self._cache.move_to_end(key)
                    while len(self._cache) > 128:
                        self._cache.popitem(last=False)
                future.set_result(data["data"])
                return copy.deepcopy(data["data"])
            finally:
                self._slots.release()
        except Exception:
            with self._lock:
                self._last_failure = self.clock()
            failure = MetricsError("Metrics read unavailable")
            future.set_exception(failure)
            raise failure from None
        finally:
            with self._lock:
                self._inflight.pop(key, None)

    def integration_status(self):
        with self._lock:
            observed = self._last_success
        fresh = observed is not None and 0 <= self.clock() - observed <= FRESH_SECONDS
        return {"status": "ok" if fresh else "unknown", "observed_at": observed,
                "source": "prometheus", "grafana_configured": bool(self._grafana),
                "hosts": list(self._hosts), "serves": list(self._serves),
                "charts": list(CHARTS)}

    def _scope(self, host_id=None, serve_id=None):
        if host_id is not None and host_id not in self._hosts:
            raise MetricsError("Unknown host")
        if serve_id is not None and serve_id not in self._serves:
            raise MetricsError("Unknown serve")
        if serve_id and host_id and self._serves[serve_id]["host_id"] != host_id:
            raise MetricsError("Host and serve do not match")
        if serve_id:
            host_id = self._serves[serve_id]["host_id"]
        return ([self._hosts[host_id]] if host_id else list(self._hosts.values()),
                [self._serves[serve_id]] if serve_id else [s for s in self._serves.values() if not host_id or s["host_id"] == host_id])

    def _gated(self, expression, source, host, *, serve=None, gpu=False, group=None):
        labels = {"host": host["metric_host"]}
        if serve:
            labels.update(service=serve["metric_serve"], engine=serve["engine"], model=serve["model"], exporter="engine")
        else:
            labels["exporter"] = "nvidia_smi" if gpu else ("windows" if host["platform"] == "windows" else "node")
        up = _selector("up", **labels)
        gate = f"(max({up}) == 1) and (time() - max(timestamp({up})) >= 0) and (time() - max(timestamp({up})) <= {FRESH_SECONDS})"
        if gpu:
            collected = _selector("fakoli:gpu_last_success_timestamp_seconds", host=host["metric_host"])
            source = f"({source}) * 0 + scalar({collected})"
        fresh = f"(time() - ({source}) >= 0) and (time() - ({source}) <= {FRESH_SECONDS})"
        # All selectors are for exactly one configured host; the source mask
        # preserves each physical GPU/device series independently.
        return f"({expression}) and on() ({gate}) and on({group or ''}) ({fresh})"

    def _specs(self, chart_id, hosts, serves):
        """Return (safe id, label, expr, timestamp expr, grouping label) tuples."""
        specs = []
        if chart_id in INFERENCE:
            suffix = INFERENCE[chart_id][2]
            if suffix is None:
                return []
            for serve in serves:
                host = self._hosts[serve["host_id"]]
                raw = _selector("fakoli:inference_" + suffix, host=host["metric_host"], service=serve["metric_serve"], engine=serve["engine"], model=serve["model"])
                source = f"max(timestamp({raw}))"
                specs.append((serve["id"], serve.get("display_name", serve["id"]), self._gated(f"max({raw})", source, host, serve=serve), source, None))
        elif chart_id.startswith("gpu_"):
            for host in hosts:
                selected = {g for s in serves for g in s["gpu_ids"]} if serves else None
                for gpu in host["gpus"]:
                    if selected is not None and gpu["id"] not in selected:
                        continue
                    suffix = "memory_used_bytes" if chart_id == "gpu_memory" else "utilization_ratio"
                    raw = _selector("fakoli:gpu_" + suffix, host=host["metric_host"], gpu_uuid=gpu["uuid"])
                    source = _selector("fakoli:gpu_last_success_timestamp_seconds", host=host["metric_host"])
                    specs.append((host["id"] + "." + gpu["id"], gpu["label"], self._gated(f"max({raw})", f"max(timestamp({raw}))", host, gpu=True), f"max({source})", None))
        else:
            for host in hosts:
                if host["platform"] not in {"linux", "windows"}:
                    continue
                windows = host["platform"] == "windows"
                h = "host=" + json.dumps(host["metric_host"])
                if chart_id == "host_cpu":
                    raw = ("windows_cpu_time_total" if windows else "node_cpu_seconds_total") + "{" + h + ',mode="idle"}'
                    expr, source = f"1 - avg(rate({raw}[5m]))", f"max(timestamp({raw}))"
                    specs.append((host["id"], host["display_name"], self._gated(expr, source, host), source, None))
                elif chart_id == "host_ram":
                    total = ("windows_memory_physical_total_bytes" if windows else "node_memory_MemTotal_bytes") + "{" + h + "}"
                    available = ("windows_memory_available_bytes" if windows else "node_memory_MemAvailable_bytes") + "{" + h + "}"
                    source = f'min(label_replace(timestamp({total}), "observatory_part", "total", "__name__", ".*") or label_replace(timestamp({available}), "observatory_part", "available", "__name__", ".*"))'
                    specs.append((host["id"], host["display_name"], self._gated(f"max({total}) - max({available})", source, host), source, None))
                else:
                    if chart_id == "host_disk":
                        metrics = ["windows_logical_disk_read_bytes_total", "windows_logical_disk_write_bytes_total"] if windows else ["node_disk_read_bytes_total", "node_disk_written_bytes_total"]
                        group = "volume" if windows else "device"
                        extra = ',volume=~"[A-Z]:"' if windows else ',device!~"loop.*|ram.*|zram.*|dm-.*"'
                        directions = ("read", "write")
                    else:
                        metrics = ["windows_net_bytes_received_total", "windows_net_bytes_sent_total"] if windows else ["node_network_receive_bytes_total", "node_network_transmit_bytes_total"]
                        group = "nic" if windows else "device"
                        extra = ',nic!~".*Loopback.*|.*vEthernet.*"' if windows else ',device!~"lo|veth.*|docker.*|br-.*|virbr.*|tun.*|tap.*"'
                        directions = ("receive", "send")
                    for metric, direction in zip(metrics, directions):
                        raw = metric + "{" + h + extra + "}"
                        source = f"max by ({group}) (timestamp({raw}))"
                        expr = f"max by ({group}) (rate({raw}[5m]))"
                        specs.append((host["id"] + "." + direction, host["display_name"] + " · " + direction, self._gated(expr, source, host, group=group), source, group))
        return specs

    def _instant(self, spec, unit):
        key, _, expression, source, _ = spec
        query = f'label_replace(({expression}), "observatory_field", "value", "__name__", ".*") or label_replace(({source}), "observatory_field", "timestamp", "__name__", ".*")'
        value = timestamp = None
        reason = "Metric absent or collection is not fresh"
        try:
            result = self._request("/api/v1/query", {"query": query})
            if result.get("resultType") != "vector" or len(result["result"]) > 2:
                raise MetricsError("Ambiguous metric result")
            fields = {}
            for sample in result["result"]:
                field = sample.get("metric", {}).get("observatory_field")
                pair = sample.get("value", [])
                if field not in {"value", "timestamp"} or field in fields or len(pair) != 2:
                    raise MetricsError("Ambiguous metric result")
                fields[field] = _number(pair[1])
            timestamp = fields.get("timestamp")
            if timestamp is not None and 0 <= self.clock() - timestamp <= FRESH_SECONDS:
                value = fields.get("value")
        except Exception:
            reason = "Metrics source unavailable"
        with self._lock:
            if value is not None:
                self._last_known[(key, unit, expression)] = (value, timestamp)
            last = self._last_known.get((key, unit, expression))
        return {"value": value, "last_known_value": last[0] if last and value is None else None,
                "status": "fresh" if value is not None else ("stale" if last else "unavailable"),
                "unit": unit, "source_timestamp": timestamp if value is not None else (last[1] if last else timestamp),
                **({"reason": reason} if value is None else {})}

    @staticmethod
    def _missing(unit, reason="No qualified source metric", status="unsupported"):
        return {"value": None, "last_known_value": None, "status": status, "unit": unit,
                "source_timestamp": None, "reason": reason}

    def _serve_metric(self, chart, serve, spec, unit):
        if chart == "kv_cache" and serve["engine"] == "llamacpp":
            return self._missing(unit, "This engine exposes no qualified KV occupancy gauge")
        result = self._instant(spec, unit)
        if chart == "ttft" and result["value"] is None and serve["engine"] == "sglang":
            host = self._hosts[serve["host_id"]]
            raw = _selector("fakoli_metric_coverage", host=host["metric_host"],
                            service=serve["metric_serve"], engine=serve["engine"],
                            model=serve["model"], metric="fakoli:inference_ttft_p95_seconds", status="exposed")
            source = f"max(timestamp({raw}))"
            coverage = self._instant((serve["id"] + ".ttft-coverage", "", self._gated(f"max({raw})", source, host, serve=serve), source, None), "count")
            if coverage["value"] == 1:
                result.update(status="insufficient_data", reason="Native TTFT measurements are exposed but the recent window has no qualified p95 estimate")
        return result

    def snapshot(self):
        """Project configured resources; current owner states intentionally unknown."""
        hosts, serves = [], []
        tasks = []
        def collect(target, key, spec, unit, serve=None):
            future = (self._pool.submit(self._serve_metric, key, serve, spec, unit) if serve else
                      self._pool.submit(self._instant, spec, unit))
            tasks.append((target, key, future))
        for h in self._hosts.values():
            host = {"id": h["id"], "display_name": h["display_name"], "platform": h["platform"],
                    "maintenance": h.get("maintenance", "none"),
                    "controller": {"status": "unknown", "version": None, "observed_at": None, "reason": "Owner read required"},
                    "telemetry": {"status": "unknown", "observed_at": None}, "gpus": [], "resources": {},
                    "profiles": [], "mode": "unknown", "ownership_status": "unknown"}
            for chart, key in [("host_cpu", "cpu_utilization"), ("host_ram", "memory_used")]:
                specs = self._specs(chart, [h], [])
                host["resources"][key] = self._missing(CHARTS[chart][1])
                if specs:
                    collect(host["resources"], key, specs[0], CHARTS[chart][1])
            # Disk and interface readings remain separate through their charts;
            # there is no misleading host-wide sum of overlapping devices.
            host["resources"]["disk"] = {"chart": "host_disk", "scope": "per-device"}
            host["resources"]["network"] = {"chart": "host_network", "scope": "per-interface"}
            if h["platform"] in {"linux", "windows"}:
                name = "windows_memory_physical_total_bytes" if h["platform"] == "windows" else "node_memory_MemTotal_bytes"
                raw = _selector(name, host=h["metric_host"])
                source = f"max(timestamp({raw}))"
                collect(host["resources"], "memory_total", (h["id"], "", self._gated(f"max({raw})", source, h), source, None), "bytes")
            else:
                host["resources"]["memory_total"] = self._missing("bytes")
            for gpu in h["gpus"]:
                item = {"id": gpu["id"], "uuid": gpu["uuid"], "label": gpu["label"], "role": gpu["role"], "owners": []}
                for chart, key in [("gpu_memory", "memory_used"), ("gpu_utilization", "utilization")]:
                    spec = next(s for s in self._specs(chart, [h], []) if s[0] == h["id"] + "." + gpu["id"])
                    collect(item, key, spec, CHARTS[chart][1])
                raw = _selector("fakoli:gpu_memory_total_bytes", host=h["metric_host"], gpu_uuid=gpu["uuid"])
                source = f'max({_selector("fakoli:gpu_last_success_timestamp_seconds", host=h["metric_host"])})'
                collect(item, "memory_total", (h["id"] + "." + gpu["id"], "", self._gated(f"max({raw})", f"max(timestamp({raw}))", h, gpu=True), source, None), "bytes")
                host["gpus"].append(item)
            hosts.append(host)
        for s in self._serves.values():
            serve = {"id": s["id"], "host_id": s["host_id"], "display_name": s.get("display_name", s["id"]),
                     "model": s["model"], "observed_model": None, "engine": s["engine"], "aliases": s.get("aliases", []),
                     "runtime_state": "unknown", "readiness": "unknown", "admission": "unknown",
                     "gpu_ids": s["gpu_ids"], "observed_at": None, "metrics": {}, "ownership_status": "unknown"}
            for chart, (_, unit, _) in INFERENCE.items():
                specs = self._specs(chart, [self._hosts[s["host_id"]]], [s])
                serve["metrics"][chart] = self._missing(unit)
                if specs:
                    collect(serve["metrics"], chart, specs[0], unit, serve=s)
            serves.append(serve)
        for target, key, future in tasks:
            target[key] = future.result()
        for host in hosts:
            metrics = [v for v in host["resources"].values() if isinstance(v, dict) and "value" in v]
            metrics += [g[k] for g in host["gpus"] for k in ("memory_used", "memory_total", "utilization")]
            observed = [m["source_timestamp"] for m in metrics if m["value"] is not None]
            host["telemetry"] = {"status": "ok" if observed else "unknown", "observed_at": min(observed) if observed else None}
        for serve in serves:
            observed = [m["source_timestamp"] for m in serve["metrics"].values() if m["value"] is not None]
            serve["observed_at"] = min(observed) if observed else None
        status = self.integration_status()
        observed_hosts = sum(h["telemetry"]["status"] == "ok" for h in hosts)
        coverage = "ok" if observed_hosts == len(hosts) else ("partial" if observed_hosts else "unknown")
        return {"hosts": hosts, "serves": serves, "coverage": {"status": coverage, "sources": ["prometheus"]}, "observed_at": status["observed_at"]}

    def host(self, host_id):
        self._scope(host_id=host_id)
        return next(h for h in self.snapshot()["hosts"] if h["id"] == host_id)

    def serve(self, serve_id):
        self._scope(serve_id=serve_id)
        return next(s for s in self.snapshot()["serves"] if s["id"] == serve_id)

    def _grafana_link(self, chart, hosts, serves, window):
        if not self._grafana:
            return None
        uid = "fakoli-gpu" if chart.startswith("gpu_") else ("fakoli-inference" if chart in INFERENCE else ("fakoli-windows" if len(hosts) == 1 and hosts[0]["platform"] == "windows" else "fakoli-linux"))
        query = {"from": "now-" + window, "to": "now"}
        if len(hosts) == 1:
            query["var-host"] = hosts[0]["metric_host"]
        if len(serves) == 1 and chart in INFERENCE:
            query.update({"var-service": serves[0]["metric_serve"], "var-model": serves[0]["model"]})
        return self._grafana + "/d/" + uid + "?" + urlencode(query)

    def chart(self, chart_id, host_id=None, serve_id=None, window="1h"):
        if chart_id not in CHARTS or window not in WINDOWS:
            raise MetricsError("Unknown chart or window")
        hosts, serves = self._scope(host_id, serve_id)
        specs = self._specs(chart_id, hosts, serves if serve_id or not chart_id.startswith("gpu_") else [])
        end = math.floor(self.clock() / 10) * 10
        step = math.ceil(WINDOWS[window] / (MAX_POINTS // MAX_SERIES - 1))
        start = end - (WINDOWS[window] // step) * step
        ticks = list(range(start, end + 1, step))
        def query(spec):
            key, label, expression, _, group = spec
            try:
                data = self._request("/api/v1/query_range", {"query": expression, "start": str(start), "end": str(end), "step": str(step)})
                if data.get("resultType") != "matrix" or len(data["result"]) > MAX_SERIES:
                    raise MetricsError("Metrics series bound exceeded")
                if group is None and len(data["result"]) > 1:
                    raise MetricsError("Ambiguous metric series")
                output = []
                for sample in data["result"]:
                    values = sample.get("values")
                    if not isinstance(values, list) or len(values) > MAX_POINTS:
                        raise MetricsError("Metrics point bound exceeded")
                    suffix = sample.get("metric", {}).get(group) if group else None
                    if group and (not isinstance(suffix, str) or not re.fullmatch(r"[a-zA-Z0-9 ._():-]{1,96}", suffix)):
                        continue
                    points = {}
                    for pair in values:
                        if not isinstance(pair, list) or len(pair) != 2:
                            raise MetricsError("Invalid metric point")
                        stamp = _number(pair[0])
                        if stamp is None or stamp not in ticks or stamp in points:
                            raise MetricsError("Invalid metric timestamp")
                        points[stamp] = _number(pair[1])
                    identity = key if suffix is None else key + "." + hashlib.sha256(suffix.encode()).hexdigest()[:12]
                    output.append({"id": identity, "label": label + (" · " + suffix if suffix else ""), "points": [[t, points.get(t)] for t in ticks]})
                return output, False
            except Exception:
                return [], True
        futures = [self._pool.submit(query, spec) for spec in specs[:MAX_SERIES]]
        series, failed = [], False
        for future in futures:
            rows, error = future.result()
            series.extend(rows)
            failed |= error
        truncated = len(specs) > MAX_SERIES or len(series) > MAX_SERIES
        series = series[:MAX_SERIES]
        any_values = any(value is not None for s in series for _, value in s["points"])
        status = "partial" if (failed or truncated) and any_values else ("ok" if any_values else "unknown")
        reason = None if status == "ok" else ("No qualified source metric" if not specs else "Some source readings are absent, unavailable or outside the display bound")
        return {"id": chart_id, "title": CHARTS[chart_id][0], "unit": CHARTS[chart_id][1], "source": "prometheus",
                "window": window, "status": status, "reason": reason, "series": series,
                "grafana_url": self._grafana_link(chart_id, hosts, serves, window)}
