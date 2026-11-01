"""Log scope and escaping checks against a separate synthetic Loki HTTP server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from anvil_serving.observability.dashboard.access import Principal
from anvil_serving.observability.dashboard.contracts import ObservatoryError
from anvil_serving.observability.dashboard.logs_client import LogsClient

NOW = 1_000_000
INVENTORY = {"hosts": [{"id": "host-a"}, {"id": "host-b"}],
             "serves": [{"id": "serve-a", "host_id": "host-a"}]}


def principal(*resources):
    return Principal("fixture", "fixture", "viewer", frozenset(resources), frozenset())


@pytest.fixture
def logs():
    state = {"calls": [], "status": 200, "host": "collector-a", "container": "chat-a", "line": '<img src=x onerror="alert(1)">'}

    class Loki(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            state["calls"].append(parse_qs(urlsplit(self.path).query))
            labels = {"job": "docker", "host": state["host"], "container": state["container"], "stream": "stderr", "private_label": "never-projected"}
            if urlsplit(self.path).path.endswith("/series"):
                data = [labels]
            else:
                data = {"resultType": "streams", "result": [{"stream": labels, "values": [[str(NOW * 10**9 - 1), state["line"]]]}]}
            raw = json.dumps({"status": "success", "data": data}).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            if state["status"] == 302:
                self.send_header("Location", "/canary")
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Loki)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    config = {"url": f"http://127.0.0.1:{server.server_port}",
              "hosts": {"host-a": "collector-a", "host-b": "collector-b"},
              "serves": {"serve-a": {"host_id": "host-a", "containers": ["chat-a"]}}}
    try:
        yield LogsClient(config, INVENTORY, clock=lambda: NOW), state
    finally:
        server.shutdown()
        server.server_close()


def test_serve_grant_does_not_expand_to_host_logs(logs):
    client, state = logs
    assert client.read({"serve": "serve-a"}, principal("serve-a"))["items"]
    assert 'container=~"chat\\\\-a"' in state["calls"][0]["query"][0]
    state["container"] = "other"
    with pytest.raises(ObservatoryError):
        client.read({"serve": "serve-a"}, principal("serve-a"))


def test_sources_and_upstream_scope_are_validated(logs):
    client, state = logs
    assert client.read({"host": "host-a"}, principal("host-a"), sources=True)["containers"] == [{"host_id": "host-a", "name": "chat-a"}]
    state["host"] = "collector-b"
    for sources in (False, True):
        with pytest.raises(ObservatoryError):
            client.read({"host": "host-a"}, principal("host-a"), sources=sources)
