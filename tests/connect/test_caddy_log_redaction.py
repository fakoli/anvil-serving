"""Exercise the rendered logging policy with a real, isolated Caddy process."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import time

import pytest

from anvil_serving.connect.render import render
from tests.connect.test_render import isolated_manifest


def test_error_logs_do_not_disclose_request_credentials(tmp_path: Path) -> None:
    logging = json.loads(render(isolated_manifest())["files"]["caddy.json"])["logging"]
    fields = logging["logs"]["default"]["encoder"]["fields"]
    assert fields["request>headers"] == fields["request>uri"] == {"filter": "delete"}
    binary = os.environ.get("ANVIL_CONNECT_TEST_CADDY")
    if not binary:
        pytest.skip("set ANVIL_CONNECT_TEST_CADDY to run the native logging regression")
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    config = {"admin": {"disabled": True}, "logging": logging, "apps": {"http": {"servers": {
        "test": {"listen": [f"127.0.0.1:{port}"], "routes": [{"handle": [{
            "handler": "reverse_proxy", "upstreams": [{"dial": "unix/" + str(tmp_path / "absent.sock")}],
        }]}]},
    }}}}
    path = tmp_path / "caddy.json"
    path.write_text(json.dumps(config))
    with (tmp_path / "log").open("w+") as logs:
        process = subprocess.Popen([binary, "run", "--config", str(path)], stdout=logs, stderr=logs,
                                   env={**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "config"),
                                        "XDG_DATA_HOME": str(tmp_path / "data")})
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.1):
                        break
                except OSError:
                    assert process.poll() is None and time.monotonic() < deadline
                    time.sleep(.02)
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                connection.request("GET", "/callback?code=synthetic-query-secret", headers={
                    "Sec-WebSocket-Protocol": "synthetic-tunnel-secret",
                    "Authorization": "Bearer synthetic-bearer-secret",
                    "Cookie": "session=synthetic-cookie-secret",
                })
                response = connection.getresponse()
                assert response.status == 502
                response.read()
            finally:
                connection.close()
        finally:
            process.terminate()
            process.wait(timeout=5)
        logs.seek(0)
        output = logs.read()
    assert "synthetic-" not in output
    events = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    error = next(event for event in events if event.get("status") == 502)
    assert error["request"]["method"] == "GET"
    assert "headers" not in error["request"] and "uri" not in error["request"]
