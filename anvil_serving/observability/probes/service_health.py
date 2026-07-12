"""Configuration-driven, read-only service and endpoint health probes."""

from __future__ import annotations

import socket
import subprocess
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from ..schema import CapabilityStatus, TelemetrySample
from ..status import prepare_sample


ObservationProvider = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]
COMPONENTS = (
    "router",
    "model-serve",
    "openclaw-gateway",
    "voice-realtime",
    "voice-proxy",
    "stt",
    "tts",
)


def collect_service_health(
    configuration: Mapping[str, Mapping[str, Any]],
    *,
    provider: ObservationProvider | None = None,
    collected_at: datetime | None = None,
) -> list[TelemetrySample]:
    """Return one complete health entry for every supported component role."""

    if not isinstance(configuration, Mapping):
        raise TypeError("service health configuration must be a mapping")
    now = _timestamp(collected_at)
    samples: list[TelemetrySample] = []
    for component in COMPONENTS:
        target = configuration.get(component)
        if not isinstance(target, Mapping) or target.get("enabled") is False:
            samples.append(_not_configured(now, component, target))
            continue
        try:
            normalized = _normalize_target(component, target)
            observation = dict((provider or _observe_target)(component, normalized))
            samples.append(_observed_sample(now, component, normalized, observation))
        except PermissionError as exc:
            samples.append(
                _degraded_sample(
                    now,
                    component,
                    target,
                    CapabilityStatus.PERMISSION_DENIED,
                    "permission-denied",
                    str(exc),
                )
            )
        except Exception as exc:
            samples.append(
                _degraded_sample(
                    now,
                    component,
                    target,
                    CapabilityStatus.FAILED,
                    "failed",
                    str(exc),
                )
            )
    return samples


def _normalize_target(component: str, target: Mapping[str, Any]) -> dict[str, Any]:
    host = target.get("host")
    port = target.get("port")
    if not isinstance(host, str) or not host.strip():
        raise ValueError(f"{component} host must be configured")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError(f"{component} port must be between 1 and 65535")
    container = target.get("container")
    identity = target.get("served_identity")
    if container is not None and not isinstance(container, str):
        raise TypeError(f"{component} container must be text or null")
    if identity is not None and not isinstance(identity, str):
        raise TypeError(f"{component} served_identity must be text or null")
    return {
        "host": host,
        "port": port,
        "container": container or None,
        "served_identity": identity or "unknown",
    }


def _observe_target(component: str, target: Mapping[str, Any]) -> Mapping[str, Any]:
    del component
    host = str(target["host"])
    port = int(target["port"])
    try:
        with socket.create_connection((host, port), timeout=0.75):
            port_listening = True
    except PermissionError:
        raise
    except (ConnectionRefusedError, TimeoutError, socket.timeout, OSError):
        port_listening = False

    container = target.get("container")
    container_state = "not-applicable"
    if isinstance(container, str) and container:
        completed = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", container],
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or "docker inspect failed"
            if "permission denied" in message.lower() or "access is denied" in message.lower():
                raise PermissionError(message)
            container_state = "unavailable"
        else:
            container_state = completed.stdout.strip() or "unknown"
    return {
        "port_listening": port_listening,
        "container_state": container_state,
        "served_identity": target["served_identity"],
    }


def _observed_sample(
    now: datetime,
    component: str,
    target: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> TelemetrySample:
    listening = observation.get("port_listening") is True
    container_state = str(observation.get("container_state") or "unknown")
    identity = str(
        observation.get("served_identity") or target.get("served_identity") or "unknown"
    )
    container_ok = container_state in {"running", "not-applicable"}
    healthy = listening and container_ok
    return _sample(
        now,
        component,
        target,
        CapabilityStatus.OK if healthy else CapabilityStatus.FAILED,
        healthy if healthy else None,
        "healthy" if healthy else "failed",
        container_state,
        identity,
        None if healthy else "expected service endpoint is not healthy",
    )


def _not_configured(
    now: datetime, component: str, target: Mapping[str, Any] | None
) -> TelemetrySample:
    return _sample(
        now,
        component,
        target or {},
        CapabilityStatus.UNSUPPORTED,
        None,
        "not-configured",
        "not-configured",
        "not-configured",
        "component is not configured",
    )


def _degraded_sample(
    now: datetime,
    component: str,
    target: Mapping[str, Any],
    status: CapabilityStatus,
    health: str,
    detail: str,
) -> TelemetrySample:
    return _sample(
        now,
        component,
        target,
        status,
        None,
        health,
        "unknown",
        str(target.get("served_identity") or "unknown"),
        detail,
    )


def _sample(
    now: datetime,
    component: str,
    target: Mapping[str, Any],
    status: CapabilityStatus,
    value: bool | None,
    health: str,
    container_state: str,
    served_identity: str,
    detail: str | None,
) -> TelemetrySample:
    host = str(target.get("host") or "not-configured")
    port = target.get("port")
    port_label = str(port) if isinstance(port, int) and not isinstance(port, bool) else "not-configured"
    labels = (
        ("component", component),
        ("configured", "false" if health == "not-configured" else "true"),
        ("container_state", container_state[:1024]),
        ("expected_port", port_label),
        ("health", health),
        ("owning_host", host[:1024]),
        ("served_identity", served_identity[:1024]),
    )
    return prepare_sample(
        TelemetrySample(
            metric="service.health",
            source_timestamp=now,
            collection_timestamp=now,
            host_id=host[:256],
            collector_id="service-health",
            capability="service-health",
            capability_status=status,
            value=value,
            unit=None,
            stale_after_seconds=10.0,
            labels=labels,
            detail=detail[:4096] if detail else None,
        )
    )


def _timestamp(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise TypeError("collected_at must be a datetime or None")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("collected_at must be timezone-aware")
    return value.astimezone(timezone.utc)
