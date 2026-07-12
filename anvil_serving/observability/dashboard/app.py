"""Serve the packaged read-only observability dashboard."""

from __future__ import annotations

import argparse
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from importlib.resources import files

from ..api import TelemetryRegistry, build_default_registry, create_server
from ..retention import NORMAL_PROFILE, RetentionStore, SamplingProfile
from .indicators import build_indicators
from .history import BenchmarkHistory, bounded_history
from .timeseries import retained_timeseries


_CORE_CAPABILITIES = frozenset({"host-resources", "boundary-resources", "shared-gpu-memory"})


def create_dashboard_server(
    registry: TelemetryRegistry | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8766,
    auth_env: str | None = None,
    environment: Mapping[str, str] | None = None,
    retention: RetentionStore | None = None,
    benchmark_history: BenchmarkHistory | None = None,
):
    """Create a metrics server with the packaged single-page shell."""

    document = (
        files("anvil_serving.observability.dashboard.static").joinpath("index.html").read_bytes()
    )
    history = retention or RetentionStore()
    telemetry = registry or build_default_registry()
    sessions = benchmark_history or BenchmarkHistory()

    def current(capabilities=None):
        return (
            _latest_snapshot(history, capabilities)
            if history.frames(0)
            else telemetry.snapshot(capabilities)
        )

    return create_server(
        telemetry,
        host=host,
        port=port,
        auth_env=auth_env,
        environment=environment,
        static_routes={
            "/": ("text/html; charset=utf-8", document),
            "/index.html": ("text/html; charset=utf-8", document),
        },
        json_routes={
            "/v1/timeseries": lambda: retained_timeseries(history),
            "/v1/indicators": lambda: build_indicators(current(), retention=history),
        },
        metrics_provider=current,
        query_routes={
            "/v1/history": lambda _query: {
                "recent": bounded_history(history),
                "sessions": sessions.list_sessions(),
            },
            "/v1/compare": lambda query: sessions.compare(
                _one(query, "session"),
                history,
                metric=_one(query, "metric"),
            ),
        },
    )


def _one(query: Mapping[str, list[str]], field: str) -> str:
    if set(query) - {"session", "metric"}:
        raise ValueError("history comparison query contains unknown fields")
    values = query.get(field)
    if not isinstance(values, list) or len(values) != 1 or not values[0]:
        raise ValueError(f"history comparison requires one {field}")
    return values[0]


def _latest_snapshot(
    retention: RetentionStore, capabilities: Sequence[str] | None = None
) -> dict[str, object]:
    requested = set(capabilities or ())
    selected: dict[str, Mapping[str, object]] = {}
    available: set[str] = set()
    for frame in reversed(retention.frames(0)):
        frame_capabilities = frame.snapshot.get("capabilities", [])
        if isinstance(frame_capabilities, list):
            available.update(str(item) for item in frame_capabilities)
            if requested and not requested.intersection(frame_capabilities):
                continue
        samples = frame.snapshot.get("samples", [])
        if not isinstance(samples, list):
            continue
        for sample in samples:
            if not isinstance(sample, Mapping):
                continue
            raw_labels = sample.get("labels")
            labels = raw_labels if isinstance(raw_labels, Mapping) else {}
            key = repr((sample.get("host_id"), sample.get("metric"), sorted(labels.items())))
            selected.setdefault(key, sample)
    now_value = datetime.now(timezone.utc)
    samples = [_refresh_freshness(sample, now_value) for sample in selected.values()]
    now = now_value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return {
        "schema_version": 1,
        "generated_at": now,
        "capabilities": sorted(requested or available),
        "available_capabilities": sorted(available),
        "sample_count": len(samples),
        "degraded_count": sum(sample.get("capability_status") != "ok" for sample in samples),
        "samples": samples,
    }


def _refresh_freshness(sample: Mapping[str, object], now: datetime) -> dict[str, object]:
    result = dict(sample)
    freshness = sample.get("freshness")
    source = sample.get("source_timestamp")
    if not isinstance(freshness, Mapping) or not isinstance(source, str):
        return result
    stale_after = freshness.get("stale_after_seconds")
    if not isinstance(stale_after, (int, float)) or isinstance(stale_after, bool):
        return result
    try:
        source_time = datetime.fromisoformat(source.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return result
    age = (now - source_time).total_seconds()
    updated = dict(freshness)
    updated["age_seconds"] = age
    updated["is_stale"] = age > stale_after
    result["freshness"] = updated
    if updated["is_stale"] and result.get("capability_status") == "ok":
        result["capability_status"] = "stale"
    return result


class DashboardSampler:
    """Collect core and costly capability groups at their approved normal cadence."""

    def __init__(
        self,
        registry: TelemetryRegistry,
        retention: RetentionStore,
        *,
        profile: SamplingProfile = NORMAL_PROFILE,
    ) -> None:
        self.registry = registry
        self.retention = retention
        self.profile = profile
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("dashboard sampler is already started")
        self._thread = threading.Thread(
            target=self._run, name="anvil-dashboard-sampler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=7)

    def _run(self) -> None:
        capabilities = set(self.registry.capabilities)
        groups = (
            (
                tuple(sorted(capabilities & _CORE_CAPABILITIES)),
                self.profile.core_seconds,
                "core",
            ),
            (
                tuple(sorted(capabilities - _CORE_CAPABILITIES)),
                self.profile.costly_seconds,
                "costly",
            ),
        )
        deadlines = {name: 0.0 for _, _, name in groups}
        while not self._stop.is_set():
            monotonic = time.monotonic()
            for requested, interval, name in groups:
                if not requested or monotonic < deadlines[name]:
                    continue
                started = time.monotonic()
                observed = datetime.now(timezone.utc)
                snapshot = self.registry.snapshot(requested, generated_at=observed)
                snapshot["sampling_group"] = name
                try:
                    self.retention.add(
                        snapshot,
                        observed_at=observed,
                        probe_duration_seconds=time.monotonic() - started,
                        expected_interval_seconds=interval,
                    )
                except ValueError:
                    pass
                deadlines[name] = monotonic + interval
            self.retention.flush_if_due(now=datetime.now(timezone.utc))
            self._stop.wait(0.1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anvil-serving dashboard serve",
        description="Serve Anvil's read-only local observability dashboard.",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Explicit bind IP (default: 127.0.0.1)."
    )
    parser.add_argument("--port", type=int, default=8766, help="Bind port (default: 8766).")
    parser.add_argument(
        "--auth-env",
        help="Bearer-token environment variable; required for non-loopback binds.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry = build_default_registry()
    retention = RetentionStore()
    sampler = DashboardSampler(registry, retention)
    server = create_dashboard_server(
        registry, host=args.host, port=args.port, auth_env=args.auth_env, retention=retention
    )
    print(f"Anvil dashboard: http://{args.host}:{server.server_address[1]}/")
    sampler.start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        sampler.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
