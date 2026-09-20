"""Disposable canonical State + the production Console/Projects read path."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from anvil_serving.observability.api import TelemetryRegistry, run_server_in_thread
from anvil_serving.observability.dashboard.app import create_dashboard_server
from anvil_serving.observability.dashboard.console import Console, attach_console


class Metrics:
    def snapshot(self):
        return {"hosts": [], "serves": [], "coverage": {"status": "unknown"}}

    def integration_status(self):
        return {"status": "unknown"}


def main():
    binary = Path(os.environ["ANVIL_TEST_BINARY"]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="anvil-state-browser-") as temporary:
        root = Path(temporary)
        checkout, home = root / "project", root / "home"
        checkout.mkdir(); home.mkdir()
        os.environ["HOME"] = str(home)
        def run(*args):
            environment = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "SYSTEMROOT") if key in os.environ}
            result = subprocess.run(args, cwd=checkout, env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise RuntimeError(f"Disposable fixture command failed: {args[0]} {args[1]}: {(result.stdout + result.stderr)[-2048:]}")
            return result.stdout
        run("git", "init", "-q")
        (checkout / "README.md").write_text("# Disposable acceptance project\n", encoding="utf-8")
        run("git", "add", "README.md")
        run("git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "Fixture baseline")
        run(str(binary), "init", "--with-sample")
        plans = json.loads(run(str(binary), "prd", "list", "--json"))["data"]["prds"]
        plan = plans[0]
        canonical = json.loads(run(str(binary), "prd", "show", plan["id"], "--json"))["data"]
        server = create_dashboard_server(TelemetryRegistry(), port=0, environment={})
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        config = {"origin": origin, "base_path": "/state-fixture/", "operate": False, "fixture": True,
                  "state_path": str(root / "journal.sqlite"),
                  "authentication": {}, "users": [{"id": "fixture", "username": "fixture", "role": "viewer",
                      "resources": ["project-fixture"], "actions": []}],
                  "workbench": {"state_path": str(root / "workbench.sqlite"), "projects": [{
                      "id": "disposable", "label": "Disposable State", "resource_id": "project-fixture",
                      "checkout": str(checkout), "anvil_binary": str(binary)}]}}
        console = Console(config, metrics=Metrics(), authenticate=lambda username, password: username == "fixture" and password == "fixture-password", environment={})
        attach_console(server, console)
        thread = run_server_in_thread(server)
        print(json.dumps({"url": origin + "/state-fixture/", "plan_id": plan["id"], "plan_title": plan["title"],
                          "source_digest": canonical.get("source_digest"), "prd_revision": canonical.get("prd_revision")}), flush=True)
        try:
            for line in sys.stdin:
                if json.loads(line).get("command") == "stop":
                    break
        finally:
            server.shutdown(); server.server_close(); thread.join(); console.close()


if __name__ == "__main__":
    main()
