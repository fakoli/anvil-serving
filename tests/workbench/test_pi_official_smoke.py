"""Real pinned official Pi RPC against a deterministic local provider fixture.

No inference service or real credential is used. Runs when Pi 0.85.1 is installed.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

import pytest

from anvil_serving.workbench_app.pi_rpc import PiRpcClient


def test_official_pi_native_history_fork_resume_and_model_controls(tmp_path):
    binary = shutil.which("pi")
    if not binary or subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10).stdout.strip() != "0.85.1":
        pytest.skip("requires the pinned official Pi 0.85.1 binary")
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, finish in (({"role": "assistant", "content": "deterministic fixture response"}, None), ({}, "stop")):
                chunk = {"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": payload["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    agent, sessions, work = (tmp_path / part for part in ("agent", "sessions", "work"))
    for path in (agent, sessions, work):
        path.mkdir()
    model = {"id": "fixture-a", "reasoning": True, "contextWindow": 32768, "maxTokens": 1024}
    (agent / "models.json").write_text(json.dumps({"providers": {"local-fixture": {"baseUrl": f"http://127.0.0.1:{server.server_port}/v1", "api": "openai-completions", "apiKey": "$FIXTURE_API_TOKEN", "models": [model, model | {"id": "fixture-b"}]}}}))
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PI_CODING_AGENT_DIR": str(agent), "FIXTURE_API_TOKEN": "non-secret-fixture"}
    clients = []

    def start(identity, *, fork=None, selected="fixture-a", thinking="low"):
        args = [binary, "--mode", "rpc", "--no-extensions", "--session-dir", str(sessions), "--session-id", identity, "--provider", "local-fixture", "--model", selected, "--thinking", thinking]
        if fork:
            args.extend(["--fork", str(fork)])
        client = PiRpcClient(args, cwd=work, environment=env)
        client.start()
        clients.append(client)
        return client

    def response(client, key):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            client.poll()
            result = client.take_response(key)
            if result:
                assert result.ok, result.error
                return result.data
            time.sleep(0.02)
        pytest.fail("official Pi did not return the bounded RPC response")

    def turn(client, text):
        key = client.prompt(text)
        response(client, key)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = response(client, client.get_state())
            if not state["isStreaming"] and state["messageCount"] >= len(requests) * 2:
                return state
            time.sleep(0.02)
        pytest.fail("official Pi fixture turn did not finish")

    try:
        parent_id, child_id = str(uuid.uuid4()), str(uuid.uuid4())
        parent = start(parent_id)
        first = response(parent, parent.get_state())
        assert first["sessionId"] == parent_id and first["sessionFile"].startswith(str(sessions))
        assert first["model"]["provider"] == "local-fixture" and first["thinkingLevel"] == "low"
        assert not Path(first["sessionFile"]).exists()  # Official deferred persistence.
        turn(parent, "first fixture turn")
        second = turn(parent, "second fixture turn")
        native_file = Path(second["sessionFile"])
        assert native_file.is_file() and second["messageCount"] == 4
        response(parent, parent.set_model("local-fixture", "fixture-b"))
        response(parent, parent.set_thinking_level("high"))
        changed = response(parent, parent.get_state())
        assert changed["model"]["id"] == "fixture-b" and changed["thinkingLevel"] == "high"
        parent.close()
        resumed = start(parent_id, selected="fixture-b", thinking="high")
        restored = response(resumed, resumed.get_state())
        assert restored["sessionId"] == parent_id and restored["messageCount"] == 4
        resumed.close()
        child = start(child_id, fork=native_file, selected="fixture-b", thinking="high")
        branched = response(child, child.get_state())
        assert branched["sessionId"] == child_id != parent_id
        assert branched["sessionFile"] != str(native_file) and branched["messageCount"] == 4
        assert len(requests) == 2
        assert any("first fixture turn" in json.dumps(m.get("content")) for m in requests[1]["messages"])
    finally:
        for client in clients:
            client.close()
        server.shutdown()
        server.server_close()
