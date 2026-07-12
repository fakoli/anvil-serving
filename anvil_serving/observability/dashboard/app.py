"""Serve the packaged read-only observability dashboard."""

from __future__ import annotations

import argparse
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from importlib.resources import files
from ..api import TelemetryRegistry, build_default_registry, create_server
from ..retention import RetentionStore
from .indicators import build_indicators
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
):
    """Create a metrics server with the packaged single-page shell."""

    document = (
        files("anvil_serving.observability.dashboard.static").joinpath("index.html").read_bytes()
    )
    history = retention or RetentionStore()
    telemetry = registry or build_default_registry()
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
            "/v1/indicators": lambda: build_indicators(telemetry.snapshot(), retention=history),
        },
    )


class DashboardSampler:
    """Collect core and costly capability groups at their approved normal cadence."""

    def __init__(self, registry: TelemetryRegistry, retention: RetentionStore) -> None:
        self.registry = registry
        self.retention = retention
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
            (tuple(sorted(capabilities & _CORE_CAPABILITIES)), 2.0, "core"),
            (tuple(sorted(capabilities - _CORE_CAPABILITIES)), 5.0, "costly"),
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
