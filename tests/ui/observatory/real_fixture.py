"""Loopback-only browser fixture using the real facade/session/journal composition.

Owner behavior comes from the independent observability operation regression
fixture. This process has no real controller binding and accepts no endpoints.
"""

import json
import runpy
import sys
import tempfile
from pathlib import Path

from anvil_serving.observability.api import TelemetryRegistry, run_server_in_thread
from anvil_serving.observability.dashboard.app import create_dashboard_server
from anvil_serving.observability.dashboard.console import Console, attach_console


def main():
    fixtures = runpy.run_path(str(Path(__file__).parents[2] / "observability" / "test_observatory_operations.py"))
    owner = fixtures["FakeOwner"]()
    metrics = fixtures["FakeMetrics"]()
    server = create_dashboard_server(TelemetryRegistry(), port=0, environment={})
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    with tempfile.TemporaryDirectory(prefix="observatory-browser-", dir=sys.argv[1]) as directory:
        config = {
            "origin": origin, "base_path": "/real-fixture/", "operate": True,
            "users": [{"id": "operator-fixture", "username": "operator", "role": "operator", "resources": ["*"], "actions": ["configuration.apply", "tier.quiesce"]}],
            "authentication": {}, "state_path": str(Path(directory) / "journal.sqlite"),
            "fixture": True, "build": "real-facade-fixture",
            "controller": {"resources": [{"id": "serve-fixture-a", "host_id": "host-fixture-a", "kind": "configuration", "label": "Exact fixture policy"}]},
        }
        console = Console(config, adapter=owner, metrics=metrics,
                          authenticate=lambda username, password: username == "operator" and password == "fixture-password")
        attach_console(server, console)
        thread = run_server_in_thread(server)
        print(json.dumps({"url": origin + "/real-fixture/"}), flush=True)
        try:
            for line in sys.stdin:
                command = json.loads(line).get("command")
                if command == "stop":
                    break
                if command == "fail_verification":
                    owner.fail_verification = True
                elif command != "status":
                    raise ValueError("unsupported fixture command")
                print(json.dumps({"mutations": owner.mutations, "configured": owner.current, "observed": owner.observed}), flush=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            console.close()


if __name__ == "__main__":
    main()
