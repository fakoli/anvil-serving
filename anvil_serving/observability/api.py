"""Structured stdlib-only telemetry registry and authenticated HTTP API."""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import platform
import re
import threading
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .probes.containers import collect_containers
from .probes.gpu_nvidia import collect_nvidia_gpus
from .probes.gpu_shared_memory import collect_windows_shared_gpu_memory
from .probes.macos_host import collect_macos_host
from .probes.service_health import collect_service_health
from .probes.windows_host import collect_windows_host
from .probes.wsl_docker import collect_wsl_docker_boundaries
from .redaction import redact_record
from .schema import SCHEMA_VERSION, CapabilityStatus, TelemetrySample
from .status import prepare_sample


Probe = Callable[[], Sequence[TelemetrySample]]
JsonRoute = Callable[[], Mapping[str, Any]]
QueryRoute = Callable[[Mapping[str, list[str]]], Mapping[str, Any]]
MetricsProvider = Callable[[Sequence[str] | None], Mapping[str, Any]]
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")
_MAX_SAMPLES = 10_000
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_PRIVATE_V4 = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
)
_PRIVATE_V6 = ipaddress.ip_network("fc00::/7")


@dataclass(frozen=True, slots=True)
class ProbeRegistration:
    capability: str
    probe: Probe
    host_id: str
    collector_id: str

    def __post_init__(self) -> None:
        if not _CAPABILITY.fullmatch(self.capability):
            raise ValueError("probe capability identifier is invalid")
        if not callable(self.probe):
            raise TypeError("probe must be callable")
        for field, value in (("host_id", self.host_id), ("collector_id", self.collector_id)):
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError(f"{field} must contain 1-256 characters")


class TelemetryRegistry:
    """A bounded collection of independent capability probes."""

    def __init__(self, registrations: Sequence[ProbeRegistration] = ()) -> None:
        self._registrations: dict[str, ProbeRegistration] = {}
        for registration in registrations:
            if registration.capability in self._registrations:
                raise ValueError(f"duplicate probe capability: {registration.capability}")
            self._registrations[registration.capability] = registration

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(sorted(self._registrations))

    def snapshot(
        self,
        capabilities: Sequence[str] | None = None,
        *,
        generated_at: datetime | None = None,
        secrets: Sequence[str] = (),
    ) -> dict[str, Any]:
        now = _timestamp(generated_at)
        requested = self.capabilities if capabilities is None else _requested(capabilities)
        samples: list[TelemetrySample] = []
        for capability in requested:
            registration = self._registrations.get(capability)
            if registration is None:
                samples.append(
                    _failure_sample(
                        now,
                        "unknown-host",
                        "telemetry-registry",
                        capability,
                        CapabilityStatus.UNSUPPORTED,
                        "capability is not registered",
                    )
                )
                continue
            try:
                produced = registration.probe()
                if isinstance(produced, (str, bytes)) or not isinstance(produced, Sequence):
                    raise TypeError("probe must return a sequence of telemetry samples")
                if len(produced) + len(samples) > _MAX_SAMPLES:
                    raise ValueError(f"snapshot exceeds {_MAX_SAMPLES} samples")
                for sample in produced:
                    if not isinstance(sample, TelemetrySample):
                        raise TypeError("probe returned a non-telemetry sample")
                    samples.append(prepare_sample(sample, observed_at=now, secrets=secrets))
            except Exception as exc:
                samples.append(
                    _failure_sample(
                        now,
                        registration.host_id,
                        registration.collector_id,
                        capability,
                        CapabilityStatus.FAILED,
                        str(exc),
                        secrets=secrets,
                    )
                )
        payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "capabilities": list(requested),
            "available_capabilities": list(self.capabilities),
            "sample_count": len(samples),
            "degraded_count": sum(s.capability_status is not CapabilityStatus.OK for s in samples),
            "samples": [s.to_dict() for s in samples],
        }
        safe = redact_record(payload, secrets=secrets)
        if (
            len(json.dumps(safe, separators=(",", ":"), sort_keys=True).encode())
            > _MAX_RESPONSE_BYTES
        ):
            raise ValueError("telemetry snapshot exceeds 8 MiB")
        return safe


def build_default_registry(
    *, service_configuration: Mapping[str, Mapping[str, Any]] | None = None
) -> TelemetryRegistry:
    """Build local capability registrations without topology-specific names."""

    host_id = platform.node() or "local-host"
    items: list[ProbeRegistration] = []
    if platform.system() == "Darwin":
        items.append(ProbeRegistration("host-resources", collect_macos_host, host_id, "macos-host"))
    elif platform.system() == "Windows":
        items.extend(
            (
                ProbeRegistration("host-resources", collect_windows_host, host_id, "windows-host"),
                ProbeRegistration(
                    "boundary-resources",
                    collect_wsl_docker_boundaries,
                    host_id,
                    "wsl-docker-boundaries",
                ),
                ProbeRegistration(
                    "shared-gpu-memory",
                    collect_windows_shared_gpu_memory,
                    host_id,
                    "windows-gpu-shared-memory",
                ),
            )
        )
    if platform.system() != "Darwin":
        items.extend(
            (
                ProbeRegistration("nvidia-gpu", collect_nvidia_gpus, host_id, "nvidia-smi"),
                ProbeRegistration("containers", collect_containers, host_id, "docker-engine"),
            )
        )
    items.append(
        ProbeRegistration(
            "service-health",
            lambda: collect_service_health(service_configuration or {}),
            host_id,
            "service-health",
        )
    )
    return TelemetryRegistry(items)


def controller_collect(capabilities: Sequence[str]) -> dict[str, Any]:
    return build_default_registry().snapshot(capabilities)


def create_server(
    registry: TelemetryRegistry,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    auth_env: str | None = None,
    environment: Mapping[str, str] | None = None,
    static_routes: Mapping[str, tuple[str, bytes]] | None = None,
    public_static_routes: Sequence[str] = (),
    json_routes: Mapping[str, JsonRoute] | None = None,
    metrics_provider: MetricsProvider | None = None,
    query_routes: Mapping[str, QueryRoute] | None = None,
) -> ThreadingHTTPServer:
    """Create, but do not start, a bounded read-only telemetry server."""

    non_loopback = _validate_bind(host)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    env = os.environ if environment is None else environment
    token = ""
    if auth_env is not None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", auth_env):
            raise ValueError("auth_env must name an uppercase environment variable")
        token = (env.get(auth_env) or "").strip()
        if not token:
            raise ValueError("configured API authentication environment is unset")
    if non_loopback and not token:
        raise ValueError("non-loopback telemetry API binds require authentication")
    assets = dict(static_routes or {})
    public_assets = frozenset(public_static_routes)
    routes = dict(json_routes or {})
    parameterized_routes = dict(query_routes or {})
    for route, asset in assets.items():
        if not isinstance(route, str) or not route.startswith("/"):
            raise ValueError("static route paths must begin with /")
        if (
            not isinstance(asset, tuple)
            or len(asset) != 2
            or not isinstance(asset[0], str)
            or not isinstance(asset[1], bytes)
        ):
            raise TypeError("static routes must map paths to (content type, bytes)")
    if any(not isinstance(route, str) for route in public_assets):
        raise TypeError("public static routes must be path strings")
    if not public_assets.issubset(assets):
        raise ValueError("public static routes must also exist in static_routes")

    class Handler(BaseHTTPRequestHandler):
        server_version = "AnvilTelemetry/1"

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in public_assets and not parsed.query:
                content_type, body = assets[parsed.path]
                self._bytes(HTTPStatus.OK, content_type, body)
                return
            if token and not _authorized(self.headers.get("Authorization"), token):
                self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                return
            if parsed.path in assets and not parsed.query:
                content_type, body = assets[parsed.path]
                self._bytes(HTTPStatus.OK, content_type, body)
                return
            if parsed.path in routes and not parsed.query:
                try:
                    self._json(HTTPStatus.OK, {"ok": True, "data": routes[parsed.path]()})
                except Exception as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"ok": False, "error": str(exc)[:4096]},
                    )
                return
            if parsed.path in parameterized_routes:
                try:
                    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                    self._json(
                        HTTPStatus.OK,
                        {"ok": True, "data": parameterized_routes[parsed.path](query)},
                    )
                except (TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return
            if parsed.path == "/health":
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "service": "anvil-serving-observability",
                        "capabilities": list(registry.capabilities),
                    },
                )
                return
            if parsed.path != "/v1/metrics":
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not-found"})
                return
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            if set(query) - {"capability"}:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "bad-query"})
                return
            try:
                requested = query.get("capability") or None
                payload = (
                    registry.snapshot(requested, secrets=(token,))
                    if metrics_provider is None
                    else metrics_provider(requested)
                )
            except (TypeError, ValueError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return
            self._json(HTTPStatus.OK, {"ok": True, "data": payload})

        def do_POST(self) -> None:
            self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": "read-only-api"})

        def _json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
            body = json.dumps(
                redact_record(payload, secrets=(token,)), separators=(",", ":"), sort_keys=True
            ).encode()
            if len(body) > _MAX_RESPONSE_BYTES:
                status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                body = b'{"error":"response-too-large","ok":false}'
            self._bytes(status, "application/json", body)

        def _bytes(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def run_server_in_thread(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    return thread


def _requested(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("capabilities must be a sequence")
    if not value or len(value) > 32:
        raise ValueError("capabilities must contain 1-32 entries")
    result = tuple(value)
    if any(not isinstance(item, str) or not _CAPABILITY.fullmatch(item) for item in result):
        raise ValueError("capability identifier is invalid")
    if len(set(result)) != len(result):
        raise ValueError("capabilities must not contain duplicates")
    return result


def _failure_sample(
    now: datetime,
    host_id: str,
    collector_id: str,
    capability: str,
    status: CapabilityStatus,
    detail: str,
    *,
    secrets: Sequence[str] = (),
) -> TelemetrySample:
    return prepare_sample(
        TelemetrySample(
            metric="collector.status",
            source_timestamp=now,
            collection_timestamp=now,
            host_id=host_id,
            collector_id=collector_id,
            capability=capability,
            capability_status=status,
            value=None,
            stale_after_seconds=10.0,
            detail=detail[:4096],
        ),
        secrets=secrets,
    )


def _validate_bind(host: str) -> bool:
    if not isinstance(host, str):
        raise TypeError("host must be an IP address string")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("telemetry API host must be an explicit IP address") from exc
    if address.is_loopback:
        return False
    private = any(address in network for network in _PRIVATE_V4 if address.version == 4) or (
        address.version == 6 and address in _PRIVATE_V6
    )
    if not private:
        raise ValueError("telemetry API non-loopback host must be private or tailnet scoped")
    return True


def _authorized(header: str | None, token: str) -> bool:
    return (
        isinstance(header, str)
        and header.startswith("Bearer ")
        and hmac.compare_digest(header[7:], token)
    )


def _timestamp(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise TypeError("generated_at must be a datetime or None")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    return value.astimezone(timezone.utc)
