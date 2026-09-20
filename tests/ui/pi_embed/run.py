"""Disposable validation fixture: no production sessions, credentials or models."""

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

root = Path(sys.argv[1]).resolve()
if root.exists():
    raise SystemExit("Use a new output directory to preserve prior evidence")
root.mkdir(parents=True)
home = root / "home"
agent = home / ".pi" / "agent"
work = root / "project"
for path in (agent / "extensions", work):
    path.mkdir(parents=True, exist_ok=True)
requests = []


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests.append({"model": payload["model"], "messages": len(payload.get("messages", []))})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta, finish in [
            ({"role": "assistant", "content": "Synthetic Pi integration response."}, None),
            ({}, "stop"),
        ]:
            chunk = {
                "id": "fixture",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": payload["model"],
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.flush()
            if finish is None:
                deadline = time.monotonic() + 40
                while not (root / "release-stream").exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Browser did not release synthetic stream")
                    time.sleep(0.05)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
threading.Thread(target=provider.serve_forever, daemon=True).start()
(agent / "models.json").write_text(
    json.dumps(
        {
            "providers": {
                "fixture": {
                    "baseUrl": f"http://127.0.0.1:{provider.server_port}/v1",
                    "api": "openai-completions",
                    "apiKey": "synthetic-fixture",
                    "models": [
                        {
                            "id": "fixture-a",
                            "reasoning": False,
                            "contextWindow": 32768,
                            "maxTokens": 1024,
                        }
                    ],
                }
            }
        }
    )
)
(agent / "settings.json").write_text(
    json.dumps(
        {"defaultProvider": "fixture", "defaultModel": "fixture-a", "defaultThinkingLevel": "off"}
    )
)
(agent / "extensions" / "fixture.js").write_text(
    'export default function(pi) { pi.registerCommand("fixture-confirm", {description:"Synthetic confirmation",handler:async(args,ctx)=>{const ok=await ctx.ui.confirm("Synthetic confirmation", "Confirm this fixture?");ctx.ui.notify(ok?"Fixture confirmed":"Fixture cancelled", "info");}}); }\n'
)
subprocess.run(["git", "init", "-q", str(work)], check=True)
installed = Path(os.environ["PI_WEB_PACKAGE"]).resolve()
if json.loads((installed / "package.json").read_text()).get("version") != "0.9.0":
    raise SystemExit("This fixture requires the reviewed Pi Web 0.9.0 pin")


def runtime_digest():
    identity = hashlib.sha256()
    dependencies = installed.parents[1]
    for source in sorted(dependencies.rglob("*")):
        relative = source.relative_to(dependencies).as_posix()
        if source.is_symlink():
            if not source.resolve().is_relative_to(dependencies):
                raise RuntimeError("Runtime dependency link escapes the pinned installation")
            identity.update(relative.encode() + b"\0link\0" + os.readlink(source).encode())
        elif source.is_file():
            identity.update(relative.encode() + b"\0file\0")
            with source.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    identity.update(block)
    return identity.hexdigest()


identity = runtime_digest()
(root / "package-identity.json").write_text(
    json.dumps(
        {
            "package": "@agegr/pi-web",
            "version": "0.9.0",
            "runtime_tree_sha256": identity,
            "node_version": subprocess.check_output(["node", "--version"], text=True).strip(),
        },
        indent=2,
    )
)
app = root / "pi-web"
if not app.exists():
    shutil.copytree(installed, app)
    (app / "node_modules").symlink_to(installed.parents[1], target_is_directory=True)
with socket.socket() as port:
    port.bind(("127.0.0.1", 0))
    number = port.getsockname()[1]
env = {
    "PATH": os.environ["PATH"],
    "HOME": str(home),
    "PI_CODING_AGENT_DIR": str(agent),
    "PI_WEB_PASSWORD": "synthetic-pi-fixture",
    "PI_WEB_ALLOWED_HOSTS": "dash.example.test,127.0.0.1",
    "PI_WEB_NO_OPEN": "1",
    "PI_WEB_RPC_IDLE_TIMEOUT_MS": "60000",
    "NEXT_TELEMETRY_DISABLED": "1",
}
log = (root / "pi-web.log").open("w")
process = subprocess.Popen(
    [
        "node",
        str(app / "bin/pi-web.js"),
        "--hostname",
        "127.0.0.1",
        "--port",
        str(number),
        "--no-open",
    ],
    cwd=work,
    env=env,
    stdout=log,
    stderr=subprocess.STDOUT,
    start_new_session=True,
)
try:
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{number}/", timeout=1)
            break
        except urllib.error.HTTPError as e:
            if e.code == 401:
                break
        except OSError:
            pass
        if process.poll() is not None:
            raise RuntimeError("isolated Pi Web exited; inspect retained log")
        time.sleep(0.1)
    else:
        raise RuntimeError("isolated Pi Web readiness timeout")
    fixture = root / "browser-fixture.test"
    subprocess.run(
        ["go", "test", "-c", "-p", "2", "-o", str(fixture), "./test"],
        cwd=Path(__file__).resolve().parents[3] / "connect",
        env={**os.environ, "GOMAXPROCS": "2"},
        check=True,
        timeout=90,
    )
    test_env = {
        **os.environ,
        "PI_FIXTURE_OUTPUT": str(root),
        "PI_FIXTURE_BINARY": str(fixture),
        "PI_FIXTURE_ORIGIN": f"http://127.0.0.1:{number}",
        "PI_FIXTURE_PROJECT": str(work),
    }
    browser_test = subprocess.Popen(
        ["node", str(Path(__file__).with_name("browser.cjs"))], env=test_env, start_new_session=True
    )
    try:
        returncode = browser_test.wait(timeout=150)
    finally:
        if browser_test.poll() is None:
            os.killpg(browser_test.pid, signal.SIGKILL)
            browser_test.wait()
    (root / "provider-requests.json").write_text(json.dumps(requests, indent=2))
    if runtime_digest() != identity:
        raise RuntimeError("Pinned runtime dependencies changed during the proof")
    if returncode == 0 and len(requests) != 1:
        raise RuntimeError("Expected exactly one synthetic model turn, without replay")
    raise SystemExit(returncode)
finally:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    provider.shutdown()
    provider.server_close()
    log.close()
