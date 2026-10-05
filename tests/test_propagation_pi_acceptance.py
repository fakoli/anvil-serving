from __future__ import annotations

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
import threading
import time

import pytest

from anvil_serving.control_plane.propagation import ReceiptIdentity
from anvil_serving.propagation_pi_acceptance import (
    PiAcceptanceError,
    PiQuiescenceError,
    PiFixtureAcceptance,
    PiFixtureProfile,
    PiWebFixtureAcceptance,
    PiWebFixtureProfile,
    write_acceptance_receipt,
    write_fixture_record,
    _owned_process_factory,
)
from anvil_serving.propagation_sessions import (
    NativeSessionCheck,
    native_session_state,
    pi_catalog_digest,
)
from anvil_serving.workbench_app.pi_rpc import PiCommandId, PiEvent, PiResponse


MODEL = {
    "provider": "anvil",
    "id": "model-one",
    "api": "openai-completions",
    "baseUrl": "http://127.0.0.1:30000/v1",
    "contextWindow": 8192,
    "maxTokens": 512,
}


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
def test_existing_fixture_retains_exact_process_and_history(tmp_path):
    clients = []
    current = [datetime(2026, 1, 1, tzinfo=timezone.utc)]

    def factory(argv, cwd, environment):
        client = FakePi(argv, cwd, environment)
        clients.append(client)
        return client

    runner = PiFixtureAcceptance(
        client_factory=factory, now=lambda: current[0], sleep=lambda _seconds: None,
        token=iter(["a" * 32, "b" * 32, "c" * 32,
                    "d" * 32, "e" * 32, "f" * 32]).__next__,
    )
    declared = profile(tmp_path)
    prepared = runner.prepare_existing(declared, "existing-fixture")
    assert prepared.process_generation == "pi-" + "a" * 32
    assert prepared.history_marker == "PI-FIXTURE-" + "b" * 32
    assert prepared.message_count == 2
    current[0] = datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc)
    item = check(
        declared, kind="existing_session", fixture_id="existing-fixture",
        previous="existing-fixture", prepared_at=prepared.prepared_at,
        generation=prepared.process_generation,
    )
    receipt = runner.complete_existing(item, prepared)
    assert len(clients) == 1
    assert len(clients[0].prompts) == 2
    assert prepared.history_marker not in clients[0].prompts[1]
    assert clients[0].last_response.startswith(prepared.history_marker + "\n")
    assert receipt["continuity_kind"] == "same_process"
    assert receipt["process_generation"] == prepared.process_generation
    assert receipt["history_probe_passed"] is True
    assert receipt["turn_completed"] is True
    assert clients[0].closed is True
    assert native_session_state(
        item, receipt, now=datetime(2026, 1, 1, 0, 0, 3, tzinfo=timezone.utc),
    )["state"] == "accepted"


@pytest.mark.skipif(os.name != "posix", reason="native fixture custody requires POSIX ownership and modes")
def test_new_fixture_uses_configured_route_and_direct_loaded_catalog(tmp_path):
    clients = []
    current = datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc)

    def factory(argv, cwd, environment):
        client = FakePi(argv, cwd, environment)
        clients.append(client)
        return client

    declared = profile(tmp_path)
    item = check(
        declared, kind="new_session", fixture_id="new-fixture",
        previous="prior-fixture", prepared_at="2026-01-01T00:00:00Z",
    )
    runner = PiFixtureAcceptance(
        client_factory=factory, now=lambda: current, sleep=lambda _seconds: None,
        token=iter(["a" * 32, "b" * 32, "c" * 32]).__next__,
    )
    receipt = runner.accept_new(item, declared, "new-native-session")
    assert receipt["native_session_id"] == "new-native-session"
    assert receipt["continuity_kind"] == "loaded_fixture"
    assert receipt["context_tokens"] == 8192
    assert receipt["max_output_tokens"] == 512
    assert receipt["configured_route"] is True
    argv = clients[0].argv
    assert "--provider" not in argv and "--model" not in argv
    assert argv[-2:] == ("--tools", "read")
    assert native_session_state(
        item, receipt, now=datetime(2026, 1, 1, 0, 0, 3, tzinfo=timezone.utc),
    )["state"] == "accepted"


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
def test_existing_fixture_never_resumes_after_process_boundary_loss(tmp_path):
    clients = []
    current = [datetime(2026, 1, 1, tzinfo=timezone.utc)]

    def factory(argv, cwd, environment):
        client = FakePi(argv, cwd, environment)
        clients.append(client)
        return client

    runner = PiFixtureAcceptance(
        client_factory=factory, now=lambda: current[0], sleep=lambda _seconds: None,
        token=iter(["a" * 32, "b" * 32, "c" * 32,
                    "d" * 32, "e" * 32, "f" * 32]).__next__,
    )
    declared = profile(tmp_path)
    prepared = runner.prepare_existing(declared, "existing-fixture")
    prepared.client.fail_turn = True
    current[0] = datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc)
    item = check(
        declared, kind="existing_session", fixture_id="existing-fixture",
        previous="existing-fixture", prepared_at=prepared.prepared_at,
        generation=prepared.process_generation,
    )
    with pytest.raises(PiAcceptanceError, match="turn failed"):
        runner.complete_existing(item, prepared)
    assert len(clients) == 1 and prepared.closed is True


@pytest.mark.skipif(os.name != "posix", reason="native fixture custody requires POSIX ownership and modes")
def test_existing_fixture_refuses_lost_pre_effect_history(tmp_path):
    runner = PiFixtureAcceptance(
        client_factory=lambda argv, cwd, environment: FakePi(argv, cwd, environment),
        now=lambda: datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
        token=iter(["a" * 32, "b" * 32, "c" * 32,
                    "d" * 32, "e" * 32, "f" * 32]).__next__,
    )
    declared = profile(tmp_path)
    prepared = runner.prepare_existing(declared, "existing-fixture")
    prepared.client.last_response = "history-was-reset"
    item = check(
        declared, kind="existing_session", fixture_id="existing-fixture",
        previous="existing-fixture", prepared_at=prepared.prepared_at,
        generation=prepared.process_generation, effect="2026-01-01T00:00:03Z",
    )
    with pytest.raises(PiAcceptanceError, match="did not complete exactly"):
        runner.complete_existing(item, prepared)
    assert prepared.closed is True


@pytest.mark.skipif(os.name != "posix", reason="native fixture custody requires POSIX ownership and modes")
def test_profile_rejects_unpinned_executable_and_nonprivate_root(tmp_path):
    declared = profile(tmp_path)
    declared.executable.write_bytes(b"changed")
    item = check(
        declared, kind="new_session", fixture_id="new-fixture",
        previous="prior-fixture", prepared_at="2026-01-01T00:00:00Z",
    )
    runner = PiFixtureAcceptance(client_factory=lambda *_args: None)
    with pytest.raises(PiAcceptanceError, match="approved digest"):
        runner.accept_new(item, declared, "new-native-session")

    declared.executable.write_bytes(b"pinned pi fixture")
    declared.fixture_root.chmod(0o755)
    with pytest.raises(PiAcceptanceError, match="private"):
        runner.accept_new(item, declared, "new-native-session")


@pytest.mark.skipif(os.name != "posix", reason="native fixture custody requires POSIX ownership and modes")
def test_receipt_has_only_closed_native_acceptance_fields(tmp_path):
    declared = profile(tmp_path)
    item = check(
        declared, kind="new_session", fixture_id="new-fixture",
        previous="prior-fixture", prepared_at="2026-01-01T00:00:00Z",
    )
    runner = PiFixtureAcceptance(
        client_factory=lambda argv, cwd, environment: FakePi(argv, cwd, environment),
        now=lambda: datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
        token=iter(["a" * 32, "b" * 32, "c" * 32]).__next__,
    )
    receipt = runner.accept_new(item, declared, "new-native-session")
    assert set(receipt) == {
        "schema", "check_digest", "started_at", "completed_at", "native_session_id",
        "continuity_kind", "configured_route", "provider", "model", "context_tokens",
        "max_output_tokens", "turn_completed", "tool_probe_passed", "history_probe_passed",
        "fallback_used", "physical_catalog_digest", "process_generation",
    }
    assert "prompt" not in json.dumps(receipt)


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


@pytest.mark.skipif(os.name != "posix", reason="native Pi fixture process groups require POSIX")
def test_owned_process_group_cleanup_reaps_fixture_descendant(tmp_path):
    child_file = tmp_path / "child.pid"
    program = (
        "import pathlib,signal,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
        "def stop(*_):\n"
        " child.terminate(); child.wait(timeout=2); raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM,stop)\n"
        # Publish readiness only after graceful group cleanup is installed.
        f"pathlib.Path({str(child_file)!r}).write_text(str(child.pid))\n"
        "time.sleep(30)\n"
    )
    process = _owned_process_factory(
        (sys.executable, "-c", program), cwd=str(tmp_path), env=dict(os.environ),
    )
    deadline = time.monotonic() + 2
    while not child_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert child_file.exists()
    pgid = process._pgid
    process.terminate()
    assert process.wait(timeout=2) == 0
    with pytest.raises(ProcessLookupError):
        os.killpg(pgid, 0)


def test_quiescence_failure_is_not_an_acceptance_error():
    assert not issubclass(PiQuiescenceError, PiAcceptanceError)


def test_phase_timeout_is_bounded_by_caller_budget(tmp_path):
    declared = profile(tmp_path)
    runner = PiFixtureAcceptance(client_factory=lambda *_args: None)
    with pytest.raises(PiAcceptanceError, match="timeout"):
        runner.prepare_existing(declared, "existing-fixture", timeout_seconds=0)
    with pytest.raises(PiAcceptanceError, match="timeout"):
        runner.prepare_existing(declared, "existing-fixture", timeout_seconds=121)


@pytest.mark.skipif(os.name != "posix", reason="native fixture custody requires POSIX ownership and modes")
def test_packaged_pi_web_acceptance_uses_live_manager_and_exact_wrapper_generation(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the packaged Pi Web acceptance gate")
    patch = Path(__file__).parents[1] / "anvil_serving/_pi_web_bridge/0.9.2-host-bridge.patch"
    raw = patch.read_text()

    def added_file(name):
        block = raw.split(f"+++ b/{name}\n", 1)[1].split("diff --git ", 1)[0]
        return "\n".join(line[1:] for line in block.splitlines() if line.startswith("+"))

    rpc = raw.split("diff --git a/lib/rpc-manager.ts", 1)[1].split("diff --git ", 1)[0]
    proxy = raw.split("diff --git a/proxy.ts", 1)[1]
    assert "propagationProcessGeneration" in rpc
    assert "propagationFixture?: boolean" in rpc
    assert "noExtensions: true" in rpc and 'inner.setActiveToolsByName(["read"])' in rpc
    assert 'pathname === "/api/propagation/session-acceptance"' in proxy
    payload = {
        "source": added_file("lib/propagation-acceptance.ts"),
        "auth": added_file("lib/propagation-observer-auth.ts"),
        "root": str(tmp_path / "agent"),
    }
    program = r'''
import assert from "node:assert/strict";
import * as fs from "node:fs";
import { join } from "node:path";
import { stripTypeScriptTypes } from "node:module";
import { timingSafeEqual } from "node:crypto";
const input = JSON.parse(fs.readFileSync(0, "utf8"));
fs.mkdirSync(input.root, {recursive:true, mode:0o700});
const sessions = new Map(); let uuid = 0; let releaseDelayedStart;
let enteredDelayedStart;
const delayedStartEntered = new Promise(resolve => { enteredDelayedStart=resolve; });
const delayedStartGate = new Promise(resolve => { releaseDelayedStart=resolve; });
class Session {
  constructor(id) { this.sessionId=id; this.inner={sessionId:id}; this.propagationProcessGeneration=`piweb-${++uuid}`; this.messageCount=0; this.alive=true; this.listeners=[]; this.lastResponse=null; }
  isAlive(){return this.alive;} isRunning(){return false;}
  onEvent(listener){this.listeners.push(listener); return ()=>this.listeners.splice(this.listeners.indexOf(listener),1);}
  emit(row){for(const listener of [...this.listeners]) listener(row);}
  async send(command){
    if(command.type==='get_state') return {sessionId:this.sessionId,isStreaming:false,messageCount:this.messageCount};
    assert.equal(command.type,'prompt'); const path=command.message.split(': ').at(-1); const marker=fs.readFileSync(path,'utf8');
    const call=`call-${this.messageCount}`; this.messageCount+=2;
    const response=command.message.includes('immediately preceding assistant response')?`${this.lastResponse}\n${marker}`:marker; this.lastResponse=response;
    this.emit({type:'tool_execution_start',toolCallId:call,toolName:'read',args:{path}});
    this.emit({type:'tool_execution_end',toolCallId:call,toolName:'read',isError:false,result:{content:[{type:'text',text:marker}]}});
    this.emit({type:'message_update',assistantMessageEvent:{type:'text_end',content:response}}); this.emit({type:'agent_end'}); return null;
  }
  async shutdown(){this.alive=false;}
}
const getRpcSession=id=>sessions.get(id);
const startRpcSession=async(id,file,cwd,options)=>{ assert.equal(file,''); assert.equal(cwd,join(input.root,'propagation-fixtures')); assert.deepEqual(options,{sessionId:id,toolNames:['read'],propagationFixture:true}); if(id==='propagation-delayed'){enteredDelayedStart(); await delayedStartGate;} const session=new Session(id); sessions.set(id,session); return {session,realSessionId:id}; };
const observePropagationSession=id=>{const session=sessions.get(id); return {loaded:session?.isAlive()===true,busy:false,model:{provider:'anvil',id:'model-one',contextWindow:8192,maxTokens:512},catalog_digest:'a'.repeat(64)};};
const getAgentDir=()=>input.root;
const randomUUID=()=>`${String(++uuid).padStart(8,'0')}-0000-4000-8000-000000000000`;
let source=input.source.replace(/^import[\s\S]*?;\n/gm,''); source=stripTypeScriptTypes(source).replace(/export /g,'');
const api=new Function('randomUUID','closeSync','fsyncSync','lstatSync','mkdirSync','openSync','unlinkSync','writeFileSync','join','getRpcSession','startRpcSession','observePropagationSession','getAgentDir',source+'\nreturn {runPropagationAcceptance};')(
  randomUUID,fs.closeSync,fs.fsyncSync,fs.lstatSync,fs.mkdirSync,fs.openSync,fs.unlinkSync,fs.writeFileSync,join,getRpcSession,startRpcSession,observePropagationSession,getAgentDir);
const before=await api.runPropagationAcceptance({v:1,action:'prepare_existing',operation_id:'operation-one',native_id:'propagation-existing'});
const after=await api.runPropagationAcceptance({v:1,action:'complete_existing',operation_id:'operation-one',native_id:'propagation-existing'});
assert.equal(after.process_generation,before.process_generation); assert.equal(after.history_probe_passed,true); assert.equal(after.loaded_catalog_digest,'a'.repeat(64)); assert.equal(sessions.get('propagation-existing').alive,false);
await api.runPropagationAcceptance({v:1,action:'prepare_existing',operation_id:'operation-history-loss',native_id:'propagation-history-loss'});
sessions.get('propagation-history-loss').lastResponse='history-was-reset';
await assert.rejects(api.runPropagationAcceptance({v:1,action:'complete_existing',operation_id:'operation-history-loss',native_id:'propagation-history-loss'}));
assert.equal(sessions.get('propagation-history-loss').alive,false);
const fresh=await api.runPropagationAcceptance({v:1,action:'accept_new',operation_id:'operation-two',native_id:'propagation-new',previous_native_id:'propagation-existing'});
assert.equal(fresh.history_probe_passed,false); assert.equal(fresh.turn_completed,true); assert.equal(sessions.get('propagation-new').alive,false);
await assert.rejects(api.runPropagationAcceptance({v:1,action:'accept_new',operation_id:'operation-three',native_id:'propagation-existing',previous_native_id:'propagation-existing'}));
const delayed=api.runPropagationAcceptance({v:1,action:'prepare_existing',operation_id:'operation-delayed',native_id:'propagation-delayed'});
await delayedStartEntered;
let cancellationClosed=false;
const cancellation=api.runPropagationAcceptance({v:1,action:'cancel_existing',operation_id:'operation-delayed',native_id:'propagation-delayed'}).then(value=>{cancellationClosed=true; return value;});
await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(cancellationClosed,false);
releaseDelayedStart();
assert.deepEqual(await cancellation,{schema:'pi-web-propagation-cancel/v1',closed:true});
await assert.rejects(delayed);
assert.equal(sessions.get('propagation-delayed').alive,false);
await assert.rejects(api.runPropagationAcceptance({v:1,action:'prepare_existing',operation_id:'operation-delayed',native_id:'propagation-delayed'}));
await assert.rejects(api.runPropagationAcceptance({v:1,action:'cancel_existing',operation_id:'operation-unknown',native_id:'propagation-unknown'}));
let auth=stripTypeScriptTypes(input.auth.replace(/^import .*;\n/gm,'')).replace(/export /g,'');
const authorize=new Function('timingSafeEqual',auth+'\nreturn {propagationObserverAuthorized,propagationAcceptanceAuthorized};')(timingSafeEqual);
const observer='observer-'+'o'.repeat(40), acceptance='accept-'+'a'.repeat(40), bridge='bridge-'+'b'.repeat(40), password='password-'+'p'.repeat(40);
Object.assign(process.env,{PI_WEB_PROPAGATION_OBSERVER_TOKEN:observer,PI_WEB_PROPAGATION_ACCEPTANCE_TOKEN:acceptance,PI_WEB_WORKBENCH_BRIDGE_TOKEN:bridge,PI_WEB_PASSWORD:password});
assert.equal(authorize.propagationAcceptanceAuthorized('Bearer '+acceptance),true); assert.equal(authorize.propagationAcceptanceAuthorized('Bearer '+observer),false);
process.env.PI_WEB_PROPAGATION_ACCEPTANCE_TOKEN=observer; assert.equal(authorize.propagationAcceptanceAuthorized('Bearer '+observer),false);
'''
    result = subprocess.run(
        [node, "--no-warnings", "--input-type=module", "-e", program],
        input=json.dumps(payload), text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
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
