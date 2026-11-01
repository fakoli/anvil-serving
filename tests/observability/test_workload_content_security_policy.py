import urllib.request
from importlib.resources import files
from anvil_serving.observability.api import TelemetryRegistry, run_server_in_thread
from anvil_serving.observability.dashboard.app import create_dashboard_server


def _directives(value):
    return {part.strip().split()[0]: frozenset(part.strip().split()[1:]) for part in value.split(";")}


def test_dashboard_document_and_packaged_workload_script_have_exact_safe_csp():
    server = create_dashboard_server(TelemetryRegistry(), port=0); thread = run_server_in_thread(server); base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/", timeout=2) as response: document, document_headers = response.read(), dict(response.headers.items())
        with urllib.request.urlopen(base + "/workloads.js", timeout=2) as response: script, script_headers = response.read(), dict(response.headers.items())
    finally: server.shutdown(); server.server_close(); thread.join(timeout=2)
    expected = {"default-src": frozenset({"'self'"}), "script-src": frozenset({"'self'", "'unsafe-inline'"}), "style-src": frozenset({"'unsafe-inline'"}), "connect-src": frozenset({"'self'"}), "object-src": frozenset({"'none'"}), "base-uri": frozenset({"'none'"}), "frame-ancestors": frozenset({"'none'"})}
    for headers, content_type in ((document_headers, "text/html; charset=utf-8"), (script_headers, "text/javascript; charset=utf-8")):
        assert headers["Content-Type"] == content_type and headers["Cache-Control"] == "no-store" and headers["X-Content-Type-Options"] == "nosniff" and "Access-Control-Allow-Origin" not in headers and _directives(headers["Content-Security-Policy"]) == expected
    assert document.startswith(b"<!doctype html>") and script == files("anvil_serving.observability.dashboard.static").joinpath("workloads.js").read_bytes()
