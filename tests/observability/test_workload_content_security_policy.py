from __future__ import annotations

import urllib.request
from importlib.resources import files

from anvil_serving.observability.api import TelemetryRegistry, run_server_in_thread
from anvil_serving.observability.dashboard.app import create_dashboard_server


def _directives(value: str) -> dict[str, frozenset[str]]:
    result: dict[str, frozenset[str]] = {}
    for directive in value.split(";"):
        name, *sources = directive.strip().split()
        result[name] = frozenset(sources)
    return result


def test_dashboard_document_and_packaged_workload_script_have_exact_safe_csp() -> None:
    server = create_dashboard_server(TelemetryRegistry(), port=0)
    thread = run_server_in_thread(server)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/", timeout=2) as response:
            document = response.read()
            document_headers = dict(response.headers.items())
        with urllib.request.urlopen(base + "/workloads.js", timeout=2) as response:
            script = response.read()
            script_headers = dict(response.headers.items())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    expected = {
        "default-src": frozenset({"'self'"}),
        "script-src": frozenset({"'self'", "'unsafe-inline'"}),
        "style-src": frozenset({"'unsafe-inline'"}),
        "connect-src": frozenset({"'self'"}),
        "object-src": frozenset({"'none'"}),
        "base-uri": frozenset({"'none'"}),
        "frame-ancestors": frozenset({"'none'"}),
    }
    for headers, content_type in (
        (document_headers, "text/html; charset=utf-8"),
        (script_headers, "text/javascript; charset=utf-8"),
    ):
        assert headers["Content-Type"] == content_type
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "Access-Control-Allow-Origin" not in headers
        assert _directives(headers["Content-Security-Policy"]) == expected

    assert document.startswith(b"<!doctype html>")
    assert script == files("anvil_serving.observability.dashboard.static").joinpath(
        "workloads.js"
    ).read_bytes()

