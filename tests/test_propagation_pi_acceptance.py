from __future__ import annotations

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from anvil_serving.control_plane.propagation import ReceiptIdentity
from anvil_serving.propagation_pi_acceptance import (
    PiAcceptanceError,
    PiFixtureAcceptance,
    PiFixtureProfile,
    PiWebFixtureAcceptance,
    PiWebFixtureProfile,
    write_acceptance_receipt,
    write_fixture_record,
    _default_client_factory,
)
from anvil_serving.propagation_sessions import (
    NativeSessionCheck,
    native_session_state,
    pi_catalog_digest,
)
from anvil_serving.workbench_app.pi_rpc import PiCommandId, PiEvent, PiResponse, PiProtocolError


MODEL = {
    "provider": "anvil",
    "id": "model-one",
    "api": "openai-completions",
    "baseUrl": "http://127.0.0.1:30000/v1",
    "contextWindow": 8192,
    "maxTokens": 512,
}


@pytest.mark.skipif(os.name != "posix", reason="native Pi fixture process groups require POSIX")
@pytest.mark.parametrize("payload_bytes", [300_000, 4 * 1024 * 1024])
def test_native_catalog_rpc_accepts_large_records_with_a_finite_limit(tmp_path, payload_bytes):
    program = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line)\n"
        " print(json.dumps({'type':'response','id':request['id'],"
        "'command':request['type'],'success':True,'data':{'models':["
        "{'provider':'other','description':'x'*int(sys.argv[1])}]}}),flush=True)\n"
    )
    client = _default_client_factory(
        (sys.executable, "-u", "-c", program, str(payload_bytes)), tmp_path, {},
    )
    client.start()
    try:
        command = client.send("get_available_models")
        deadline = time.monotonic() + 5
        response = None
        if payload_bytes == 4 * 1024 * 1024:
            with pytest.raises(PiProtocolError, match="exceeds configured limit"):
                while time.monotonic() < deadline:
                    client.poll()
                    time.sleep(0.01)
        else:
            while response is None and time.monotonic() < deadline:
                client.poll()
                response = client.take_response(command)
                time.sleep(0.01)
            assert response is not None and response.ok
            assert len(response.data['models'][0]['description']) == payload_bytes
    finally:
        client.close()


class FakePi:
    def __init__(self, argv, cwd, environment, *, model=None, models=None, fail_turn=False):
        self.argv = tuple(argv)
        self.cwd = cwd
        self.environment = dict(environment)
        self.model = dict(model or MODEL)
        self.models = list(models or [MODEL])
        self.fail_turn = fail_turn
        self.started = False
        self.closed = False
        self.session_id = self.argv[self.argv.index("--session-id") + 1]
        self.message_count = 0
        self._responses = {}
        self._events = []
        self.prompts = []
        self.last_response = None

    def start(self):
        self.started = True

    def _reply(self, command, data=None, *, ok=True):
        key = PiCommandId(f"command-{len(self._responses)}")
        self._responses[key] = PiResponse(key, command, ok, data=data)
        return key

    def get_state(self):
        return self._reply("get_state", {
            "sessionId": self.session_id,
            "isStreaming": False,
            "messageCount": self.message_count,
            "model": self.model,
        })

    def send(self, command_type, **_fields):
        assert command_type == "get_available_models"
        return self._reply(command_type, {"models": self.models})

    def prompt(self, message):
        self.prompts.append(message)
        key = self._reply("prompt")
        path = Path(message.rsplit(": ", 1)[1])
        marker = path.read_text()
        call = f"tool-{len(self.prompts)}"
        self.message_count += 2
        if self.fail_turn:
            self._events.append(PiEvent("error", {"type": "error"}, {"type": "error"}))
        else:
            response_text = marker
            if "immediately preceding assistant response" in message:
                response_text = f"{self.last_response}\n{marker}"
            self.last_response = response_text
            rows = [
                {"type": "tool_execution_start", "toolCallId": call,
                 "toolName": "read", "args": {"path": str(path)}},
                {"type": "tool_execution_end", "toolCallId": call, "toolName": "read",
                 "result": {"content": [{"type": "text", "text": marker}]}, "isError": False},
                {"type": "message_update", "assistantMessageEvent": {
                    "type": "text_end", "contentIndex": 0, "content": response_text}},
                {"type": "agent_end"},
            ]
            self._events.extend(PiEvent("event", row, row) for row in rows)
        return key

    def poll(self):
        events, self._events = self._events, []
        return events

    def take_response(self, command_id):
        return self._responses.pop(command_id, None)

    def close(self):
        self.closed = True


def profile(tmp_path, *, catalog=None):
    executable = tmp_path / "pi"
    executable.write_bytes(b"pinned pi fixture")
    executable.chmod(0o700)
    root = tmp_path / "fixtures"
    root.mkdir(mode=0o700)
    agent = tmp_path / "agent"
    agent.mkdir(mode=0o700)
    models = catalog or [MODEL]
    return PiFixtureProfile(
        executable=executable,
        executable_digest=hashlib.sha256(executable.read_bytes()).hexdigest(),
        agent_dir=agent,
        fixture_root=root,
        loaded_catalog_digest=pi_catalog_digest(models),
        environment={"PATH": "/usr/bin", "HOME": str(tmp_path),
                     "PI_CODING_AGENT_DIR": str(agent)},
    )


def check(profile, *, kind, fixture_id, previous, prepared_at,
          generation=None, effect="2026-01-01T00:00:01Z"):
    return NativeSessionCheck(
        "target-one", ReceiptIdentity("install-one", "profile-one", "runtime-one"),
        "1" * 64, "2" * 64, profile.executable_digest, "3" * 64,
        fixture_id, previous, prepared_at, effect, kind,
        "anvil", "model-one", 8192, 512, generation,
    )


@pytest.mark.skipif(os.name != "posix", reason="native fixture custody requires POSIX ownership and modes")
def test_new_fixture_refuses_wrong_loaded_catalog_and_failed_turn(tmp_path):
    declared = profile(tmp_path)
    item = check(
        declared, kind="new_session", fixture_id="new-fixture",
        previous="prior-fixture", prepared_at="2026-01-01T00:00:00Z",
    )

    def wrong_catalog(argv, cwd, environment):
        return FakePi(argv, cwd, environment, models=[MODEL | {"id": "other"}])

    runner = PiFixtureAcceptance(
        client_factory=wrong_catalog,
        now=lambda: datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
        token=iter(["a" * 32, "b" * 32, "c" * 32]).__next__,
    )
    with pytest.raises(PiAcceptanceError, match="approved route and catalog"):
        runner.accept_new(item, declared, "new-native-session")

    failed = PiFixtureAcceptance(
        client_factory=lambda argv, cwd, environment: FakePi(
            argv, cwd, environment, fail_turn=True,
        ),
        now=lambda: datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
        token=iter(["d" * 32, "e" * 32, "f" * 32]).__next__,
    )
    with pytest.raises(PiAcceptanceError, match="turn failed"):
        failed.accept_new(item, declared, "new-native-session")


@pytest.mark.skipif(os.name != "posix", reason="native fixture custody requires POSIX ownership and modes")
def test_protected_fixture_and_receipt_writers_are_idempotent_and_conflict_closed(tmp_path):
    clients = []
    runner = PiFixtureAcceptance(
        client_factory=lambda argv, cwd, environment: clients.append(
            FakePi(argv, cwd, environment)
        ) or clients[-1],
        now=lambda: datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
        token=iter(["a" * 32, "b" * 32, "c" * 32,
                    "d" * 32, "e" * 32, "f" * 32]).__next__,
    )
    declared = profile(tmp_path)
    prepared = runner.prepare_existing(declared, "existing-fixture")
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    fixture_path = evidence / "fixture.json"
    first = write_fixture_record(
        fixture_path, prepared, fixture_id="fixture:existing", target_id="target:one",
        contract_digest="1" * 64,
    )
    assert write_fixture_record(
        fixture_path, prepared, fixture_id="fixture:existing", target_id="target:one",
        contract_digest="1" * 64,
    ) == first
    fixture = json.loads(fixture_path.read_text())
    assert set(fixture) == {
        "schema", "fixture_id", "target_id", "contract_digest", "executable_digest",
        "prepared_at", "native_session_id", "process_generation", "history_marker",
    }
    assert fixture_path.stat().st_mode & 0o077 == 0
    assert fixture["fixture_id"] == "fixture:existing"
    assert fixture["native_session_id"] == "existing-fixture"

    item = check(
        declared, kind="existing_session", fixture_id="existing-fixture",
        previous="existing-fixture", prepared_at=prepared.prepared_at,
        generation=prepared.process_generation, effect="2026-01-01T00:00:02Z",
    )
    receipt = runner.complete_existing(item, prepared)
    receipt_path = evidence / "receipt.json"
    digest = write_acceptance_receipt(receipt_path, receipt)
    assert write_acceptance_receipt(receipt_path, receipt) == digest
    receipt_path.write_text("{}\n")
    with pytest.raises(PiAcceptanceError, match="conflicts"):
        write_acceptance_receipt(receipt_path, receipt)


def test_phase_timeout_is_bounded_by_caller_budget(tmp_path):
    declared = profile(tmp_path)
    runner = PiFixtureAcceptance(client_factory=lambda *_args: None)
    with pytest.raises(PiAcceptanceError, match="timeout"):
        runner.prepare_existing(declared, "existing-fixture", timeout_seconds=0)
    with pytest.raises(PiAcceptanceError, match="timeout"):
        runner.prepare_existing(declared, "existing-fixture", timeout_seconds=121)


def test_pi_web_loopback_adapter_emits_shared_receipts_and_cancels_uncertain_prepare():
    actions = []
    token = "acceptance-" + "a" * 40
    fail_prepare = [False]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            assert self.path == "/api/propagation/session-acceptance"
            assert self.headers["Authorization"] == "Bearer " + token
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            actions.append(body)
            action = body["action"]
            if action == "prepare_existing" and fail_prepare[0]:
                self.send_response(500)
                value = {"error": "unavailable"}
            else:
                self.send_response(200)
                if action == "prepare_existing":
                    value = {"schema": "pi-web-propagation-fixture/v1",
                        "prepared_at": "2026-01-01T00:00:00Z",
                        "native_session_id": body["native_id"],
                        "process_generation": "piweb-one",
                        "history_marker": "PI-FIXTURE-" + "a" * 32}
                elif action == "cancel_existing":
                    value = {"schema": "pi-web-propagation-cancel/v1", "closed": True}
                else:
                    value = {"schema": "pi-web-propagation-turn/v1",
                        "started_at": "2026-01-01T00:01:01Z",
                        "completed_at": "2026-01-01T00:01:02Z",
                        "native_session_id": body["native_id"],
                        "process_generation": "piweb-one" if action == "complete_existing" else "piweb-two",
                        "configured_route": True, "provider": "anvil", "model": "model-one",
                        "context_tokens": 8192, "max_output_tokens": 512,
                        "loaded_catalog_digest": "5" * 64, "turn_completed": True,
                        "tool_probe_passed": True,
                        "history_probe_passed": action == "complete_existing",
                        "fallback_used": False}
            payload = json.dumps(value).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        profile = PiWebFixtureProfile(server.server_port, token, "4" * 64, "5" * 64)
        adapter = PiWebFixtureAcceptance()
        prepared = adapter.prepare_existing(profile, "operation-one", "propagation-existing")
        item = NativeSessionCheck(
            "target-one", ReceiptIdentity("install-one", "profile-one", "runtime-one"),
            "1" * 64, "2" * 64, "4" * 64, "3" * 64, "fixture:existing",
            "propagation-existing", prepared.prepared_at, "2026-01-01T00:01:00Z",
            "existing_session", "anvil", "model-one", 8192, 512,
            prepared.process_generation,
        )
        receipt = adapter.complete_existing(item, prepared)
        assert native_session_state(
            item, receipt, now=datetime(2026, 1, 1, 0, 1, 3, tzinfo=timezone.utc),
        )["state"] == "accepted"
        new = NativeSessionCheck(
            "target-one", item.identity, "1" * 64, "2" * 64, "4" * 64, "3" * 64,
            "fixture:new", "propagation-existing", "2026-01-01T00:00:00Z",
            "2026-01-01T00:01:00Z", "new_session", "anvil", "model-one", 8192, 512,
        )
        fresh = adapter.accept_new(new, profile, "operation-two", "propagation-new")
        assert native_session_state(
            new, fresh, now=datetime(2026, 1, 1, 0, 1, 3, tzinfo=timezone.utc),
        )["state"] == "accepted"

        fail_prepare[0] = True
        with pytest.raises(PiAcceptanceError, match="unavailable"):
            adapter.prepare_existing(profile, "operation-three", "propagation-uncertain")
        assert [row["action"] for row in actions[-2:]] == ["prepare_existing", "cancel_existing"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
