"""Serve the packaged read-only observability dashboard."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from importlib.resources import files
from ..api import TelemetryRegistry, build_default_registry, create_server


def create_dashboard_server(
    registry: TelemetryRegistry | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8766,
    auth_env: str | None = None,
    environment: Mapping[str, str] | None = None,
):
    """Create a metrics server with the packaged single-page shell."""

    document = (
        files("anvil_serving.observability.dashboard.static").joinpath("index.html").read_bytes()
    )
    return create_server(
        registry or build_default_registry(),
        host=host,
        port=port,
        auth_env=auth_env,
        environment=environment,
        static_routes={
            "/": ("text/html; charset=utf-8", document),
            "/index.html": ("text/html; charset=utf-8", document),
        },
    )


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
    server = create_dashboard_server(host=args.host, port=args.port, auth_env=args.auth_env)
    print(f"Anvil dashboard: http://{args.host}:{server.server_address[1]}/")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
