"""Actual admission, transport and worker accounting with synthetic owners."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
import http.client
import json
import threading
import time

import pytest

from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.config import ServerConfig
from anvil_serving.router.decision_log import DecisionLog
from anvil_serving.router.front_door import make_server
from anvil_serving.router.identity import legacy_caller
from anvil_serving.router.internal import UsageInvocation
from anvil_serving.router.usage_store import Terminal, UsageError
from anvil_serving.router.workloads import RouterWorkloadRegistry
from tests.router.helpers import make_tier
from tests.router.test_usage_lifecycle import AT, rows, store as store
from tests.router.key_fixtures import tmp_path as tmp_path

def CLOCK():
    return datetime.fromisoformat(AT.replace("Z", "+00:00"))
CHAT = "/v1/chat/completions"


@contextmanager
def server(backend, store_tuple, *, managed=True, accounting=True, **options):
    usage, _, run, scope = store_tuple
    config = options.pop("server_config", ServerConfig() if managed else None)
    authority = options.pop("usage_authority", lambda:scope)
    clock = options.pop("workload_clock", CLOCK)
    httpd = make_server("127.0.0.1", 0, backend, auth_token="synthetic-token",
                        server_config=config, usage_store=usage if accounting else None, usage_run_id=run,
                        usage_authority=authority, workload_clock=clock, **options)
    events = {}
    handlers = []
    lock = threading.Lock()
    original_handle = httpd.RequestHandlerClass.handle_one_request

    def handle(handler):
        done = threading.Event()
        with lock:
            handlers.append(done)
        try:
            return original_handle(handler)
        finally:
            done.set()  # Includes key audit and credential/router custody release.

    def wait_handlers():
        end = time.monotonic() + 5
        with lock:
            pending = tuple(handlers)
        for event in pending:
            assert event.wait(max(0, end-time.monotonic())), "request handler did not complete"
    original_init, original_finish = UsageInvocation.__init__, UsageInvocation.finish

    def initialize(invocation, *args, **kwargs):
        original_init(invocation, *args, **kwargs)
        if invocation.store is usage:
            with lock:
                events[invocation.start.request_id] = threading.Event()

    def finish(invocation, *args, **kwargs):
        try:
            return original_finish(invocation, *args, **kwargs)
        finally:
            with lock:
                event = events.get(invocation.start.request_id)
            if event is not None:
                event.set()  # Includes failure handling and registry token cleanup.

    def wait_finalizers():
        end = time.monotonic() + 5
        with lock:
            pending = tuple(events.values())
        for event in pending:
            assert event.wait(max(0, end-time.monotonic())), "request finalizer did not complete"

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(httpd.RequestHandlerClass, "handle_one_request", handle)
        patch.setattr(usage, "_wait_dispatch_fixture_handlers", wait_handlers, raising=False)
        patch.setattr(UsageInvocation, "__init__", initialize)
        patch.setattr(UsageInvocation, "finish", finish)
        patch.setattr(usage, "_wait_dispatch_fixture_finalizers", wait_finalizers, raising=False)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        conn = http.client.HTTPConnection(*httpd.server_address, timeout=10)
        try:
            yield conn
        finally:
            conn.close()
            httpd.shutdown(); httpd.server_close(); thread.join(5)
            usage._wait_dispatch_fixture_handlers()
            wait_finalizers()


def post(conn, path=CHAT, body=None, headers=None):
    conn.request("POST", path, json.dumps(body or {"model":"llm.primary","messages":[{"role":"user","content":"hello"}]}),
                 {"Content-Type":"application/json","Authorization":"Bearer synthetic-token", **(headers or {})})
    response = conn.getresponse()
    result = response.status, response.read()
    return result


def wait_details(usage, count=1):
    wait = getattr(usage, "_wait_dispatch_fixture_finalizers", None)
    if wait is not None:
        wait()  # A flushed chunk terminator/worker.finished is not finalization.
    end = time.monotonic()+5
    while time.monotonic()<end:
        result = rows(usage,"usage_details")
        if len(result)==count:
            return [Terminal.from_json(r["terminal_payload"]) for r in result]
        time.sleep(.01)
    assert False, "worker accounting did not finalize"


def relay(usage, calls, payload=None):
    def transport(*args, **kwargs):
        # The start and dispatch row are durable before actual transport runs.
        assert rows(usage,"usage_starts")[0]["dispatched"] == 1
        calls.append(threading.get_ident())
        return json.dumps(payload or {"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}],
                                   "usage":{"prompt_tokens":7,"completion_tokens":3}}).encode()
    return RelayBackend(make_tier("openai"),transport=transport)


@pytest.mark.parametrize("managed", [True,False])
@pytest.mark.parametrize("stream", [True,False])
@pytest.mark.parametrize("path", [CHAT,"/v1/messages","/v1/responses"])
def test_three_dialects_both_modes_actual_transport_start_and_worker_capture(store, managed, stream, path):
    usage,*_ = store
    calls=[]
    backend=relay(usage,calls)
    body={"model":"llm.primary","messages":[{"role":"user","content":"hello"}],"max_tokens":20,"stream":stream}
    if path=="/v1/responses":
        body={"model":"llm.primary","input":"hello","max_output_tokens":20,"stream":stream}
    registry=RouterWorkloadRegistry(DecisionLog(),clock=CLOCK)
    with server(backend,store,managed=managed,workload_registry=registry) as conn:
        status, wire=post(conn,path,body)
        assert status==200 and wire
    [terminal]=wait_details(usage)
    assert len(calls)==1 and terminal.dispatched is True
    assert terminal.generation_outcome==terminal.delivery_outcome==terminal.outcome=="success"
    assert (terminal.tokens.input.count,terminal.tokens.output.count)==(7,3)
    assert rows(usage,"usage_cumulative")[0]["requests"]==1
    assert registry.active_count==0
    assert backend.get_last_normalized_usage() is None  # Handler/test cannot read worker TLS.


@pytest.mark.parametrize("failure", [False, True])
def test_streaming_readback_waits_for_actual_finalizer_completion(store, monkeypatch, failure):
    usage, *_ = store
    finalizing, release, waiting, completed, opened = (threading.Event() for _ in range(5))
    reader_thread = {}
    finalize = usage.finalize
    original_connect = usage.key_store._connect

    def held_finalizer(terminal):
        finalizing.set()
        assert release.wait(5)
        if failure:
            raise UsageError()
        return finalize(terminal)

    @contextmanager
    def observed_connect():
        if threading.get_ident() == reader_thread.get("id"):
            opened.set()
            assert completed.is_set(), "readback opened before finalizer completion"
        with original_connect() as connection:
            yield connection

    monkeypatch.setattr(usage, "finalize", held_finalizer)
    monkeypatch.setattr(usage.key_store, "_connect", observed_connect)
    with server(relay(usage, []), store, managed=False) as conn:
        wait = usage._wait_dispatch_fixture_finalizers

        def observed_wait():
            waiting.set()
            result = wait()
            completed.set()
            return result

        def read_details():
            reader_thread["id"] = threading.get_ident()
            usage._wait_dispatch_fixture_finalizers()
            details = rows(usage, "usage_details")
            if len(details) != 1:
                raise AssertionError("worker accounting did not finalize")
            return [Terminal.from_json(details[0]["terminal_payload"])]

        monkeypatch.setattr(usage, "_wait_dispatch_fixture_finalizers", observed_wait)
        assert post(conn, body={"model":"llm.primary", "messages":[{"role":"user", "content":"hello"}], "stream":True})[0] == 200
        assert finalizing.wait(2)  # Client already consumed the terminal chunk.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(read_details)
            try:
                assert waiting.wait(2)
                assert not future.done() and not opened.is_set()
            finally:
                release.set()
            if failure:
                with pytest.raises(AssertionError, match="worker accounting did not finalize"):
                    future.result(timeout=7)
            else:
                [terminal] = future.result(timeout=7)
                assert (terminal.tokens.input.count, terminal.tokens.output.count) == (7, 3)
                assert terminal.outcome == "success"
    assert opened.is_set()
    assert len(rows(usage, "usage_details")) == (0 if failure else 1)
    if failure:
        assert usage.health("domain_fixture")["accounting_failures"] == 1


def test_same_registry_caller_immutable_generic_redacted_and_revision_changes(store):
    usage,_,run,scope=store
    registry=RouterWorkloadRegistry(DecisionLog(),clock=CLOCK)
    invocation=UsageInvocation(usage,run,scope,legacy_caller(),"chat","llm.primary",registry=registry,clock=CLOCK)
    revision,entries,omitted=registry.usage_snapshot()
    assert len(entries)==1 and entries[0].usage_start.caller==legacy_caller()
    assert "caller" not in json.dumps(registry.active_requests()) and "usage_start" not in json.dumps(registry.active_requests())
    registry.observe_request(invocation.start.request_id,{},"llm.primary",{"phase":"checking"})
    assert registry.usage_snapshot()[1][0].usage_start==invocation.start
    invocation.capture(None,"rejected"); invocation.finish("error"); invocation.finish("error")
    assert registry.usage_snapshot()[0]>revision and registry.active_count==0
    assert len(rows(usage,"usage_details"))==1


def test_device_admission_preserves_actual_grant_and_required_webui_before_bucket(store,monkeypatch):
    from anvil_serving.router.identity import WebUIBinding,WebUIProfile
    from tests.router.test_usage_webui_identity import token,SIGNER
    from anvil_serving.router import front_door
    usage,*_=store
    keys=usage.key_store
    metadata,secret=keys.create("display",["llm.primary"],[CHAT])
    keys.bind_owner(metadata["key_id"],"service","service:fixture",0)
    bound=WebUIBinding(WebUIProfile(metadata["key_id"],"device_key","webui:fixture",signer_env="SYNTHETIC_SIGNER"),SIGNER)
    epoch=int(time.time())
    assertion=token({"sub":"user:fixture","iss":"open-webui","iat":epoch,"exp":epoch+300})
    # This fixture clock is for forwarding auth only; ledger keeps synthetic trusted scope.
    monkeypatch.setattr(front_door,"verify_webui",lambda headers,binding,now: __import__(
        'anvil_serving.router.identity',fromlist=['verify_webui']).verify_webui(headers,binding,epoch))
    config=ServerConfig(api_keys_path=str(keys.path))
    calls=[]
    with server(relay(usage,calls),store,server_config=config,webui_bindings=(bound,)) as conn:
        assert post(conn,headers={"Authorization":"Bearer "+secret})[0]==401
        with keys._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM buckets").fetchone()[0]==0
        assert post(conn,headers={"Authorization":"Bearer "+secret,"X-OpenWebUI-User-Jwt":assertion})[0]==200
    [terminal]=wait_details(usage)
    from anvil_serving.router.usage_store import RequestStart
    [saved]=rows(usage,"usage_starts")
    caller=RequestStart.from_json(saved["start_payload"]).caller
    assert caller.actor.id=="service:fixture" and caller.end_user.subject=="user:fixture"
    assert caller.grant==keys.authenticate(secret,snapshot=True).caller_snapshot.grant
    assert len(calls)==1


def test_wide_legacy_auth_unchanged_and_tracked_refuses_without_truncation(tmp_path):
    from tests.router.test_usage_identity import wide_key
    from anvil_serving.router.keys import KeyStoreError
    keys,key_id,secret,models=wide_key(tmp_path,False,True)
    principal=keys.authenticate(secret)
    assert len(principal.models)==len(models)==64
    assert keys.admit(key_id["key_id"])==0
    with pytest.raises(KeyStoreError):
        keys.authenticate(secret,snapshot=True)
    assert keys.authenticate(secret).models==principal.models


def test_registry_fence_refuses_during_ledger_write_and_preserves_finalizing_metadata(store,monkeypatch):
    usage,_,run,scope=store
    registry=RouterWorkloadRegistry(DecisionLog(),clock=CLOCK)
    invocation=UsageInvocation(usage,run,scope,legacy_caller(),"chat","llm.primary",registry=registry,clock=CLOCK)
    invocation.capture(None,"rejected")
    assert registry.usage_snapshot()[1][0].usage_phase=="finalizing"
    finalize=usage.finalize
    def blocked(terminal):
        with pytest.raises(ValueError,match="busy"):
            registry.usage_snapshot()
        return finalize(terminal)
    monkeypatch.setattr(usage,"finalize",blocked)
    invocation.finish("error")
    assert registry.usage_snapshot()[1]==() and registry.usage_snapshot()[2]==0


@pytest.mark.parametrize("kind,path,body",[
    ("stt","/v1/audio/transcriptions",{"audio_b64":"AQACAA==","format":"pcm16","sample_rate":16000,"is_final":True}),
    ("tts","/v1/audio/speech",{"input":"hello","response_format":"pcm16"}),
])
def test_audio_actual_transport_na_and_validation_zero_calls(store,kind,path,body):
    from tests.router.test_audio_gateway import gateway,FakeAudioTransport
    usage,*_=store
    transport=FakeAudioTransport()
    audio=gateway(transport)
    with server(relay(usage,[]),store,audio=audio) as conn:
        assert post(conn,path,{"purpose":kind,**body})[0]==200
        assert post(conn,path,{})[0]==400
    terminals=wait_details(usage,2)
    assert len(transport.calls)==1
    assert terminals[0].dispatched is True and terminals[1].dispatched is False
    assert all(t.tokens.input.applicability==t.tokens.output.applicability=="not_applicable" for t in terminals)
    assert terminals[1].generation_outcome=="rejected"


@pytest.mark.parametrize("mode",["rest","mcp","notification"])
def test_memory_common_gateway_hook_and_mcp_no_dispatch_notification(store,mode):
    from anvil_serving.router.config import MemoryRoute
    from anvil_serving.router.memory import MemoryRouter
    from anvil_serving.router.front_door import MEMORY_PATH,MEMORY_MCP_PATH
    usage,*_=store
    keys=usage.key_store
    metadata,secret=keys.create("device",["memory.fixture"],[MEMORY_PATH,MEMORY_MCP_PATH])
    calls=[]
    def transport(*args,**kwargs):
        assert rows(usage,"usage_starts")[0]["dispatched"]==1
        calls.append(True)
        return b'{"ok":true,"results":[]}'
    memory=MemoryRouter([MemoryRoute("memory.fixture",metadata["key_id"],"hindsight","bank_fixture", "http://127.0.0.1:9010","MEMORY_TOKEN")],
                         env={"MEMORY_TOKEN":"synthetic-upstream"},transport=transport)
    body={"alias":"memory.fixture","operation":"recall","arguments":{"query":"hello"}}
    path=MEMORY_PATH
    if mode!="rest":
        path=MEMORY_MCP_PATH
        body={"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"memory_recall","arguments":{"alias":"memory.fixture","query":"hello"}}}
        if mode=="notification":
            body={"jsonrpc":"2.0","method":"notifications/initialized","params":{}}
    with server(relay(usage,[]),store,memory=memory,server_config=ServerConfig(api_keys_path=str(keys.path))) as conn:
        assert post(conn,path,body,headers={"Authorization":"Bearer "+secret})[0]==(202 if mode=="notification" else 200)
    [terminal]=wait_details(usage)
    assert len(calls)==(0 if mode=="notification" else 1)
    assert terminal.dispatched is (mode!="notification")
    assert terminal.tokens.input.applicability==terminal.tokens.output.applicability=="not_applicable"
    assert getattr(memory._tracking,"invocation",None) is None


def test_keepalive_webui_two_users_then_direct_key_and_configured_scope(store,monkeypatch,tmp_path):
    from anvil_serving.router.identity import WebUIBinding,WebUIProfile
    from tests.router.test_usage_webui_identity import token,SIGNER
    from tests.router.test_front_door_auth import _scoped_policy
    from anvil_serving.control_plane.authorization import INFERENCE_USE
    from anvil_serving.router import front_door
    from anvil_serving.router.usage_store import RequestStart
    usage,*_=store
    keys=usage.key_store
    first,first_secret=keys.create("forwarded",["llm.primary"],[CHAT])
    second,second_secret=keys.create("direct",["llm.primary"],[CHAT])
    keys.bind_owner(first["key_id"],"service","service:fixture",0)
    keys.bind_owner(second["key_id"],"human","human:fixture",0)
    bound=WebUIBinding(WebUIProfile(first["key_id"],"device_key","webui:fixture",signer_env="SYNTHETIC_SIGNER"),SIGNER)
    epoch=int(time.time())
    original_verify=front_door.verify_webui
    monkeypatch.setattr(front_door,"verify_webui",lambda headers,binding,now:original_verify(headers,binding,epoch))
    policy=_scoped_policy(tmp_path,[{"id":"scope_fixture","credential_env":"SYNTHETIC_SCOPE_TOKEN","token":"synthetic-scope-token", "scopes":[INFERENCE_USE]}])
    calls=[]
    with server(relay(usage,calls),store,server_config=ServerConfig(api_keys_path=str(keys.path)),
                webui_bindings=(bound,),authorization_policy=policy) as conn:
        for index,subject in enumerate(("user:first","user:second"),1):
            assertion=token({"sub":subject,"iss":"open-webui","iat":epoch,"exp":epoch+300})
            assert post(conn,headers={"Authorization":"Bearer "+first_secret,"X-OpenWebUI-User-Jwt":assertion})[0]==200
            wait_details(usage,index)
        assert post(conn,headers={"Authorization":"Bearer "+second_secret})[0]==200
        wait_details(usage,3)
        assert post(conn,headers={"Authorization":"Bearer synthetic-scope-token"})[0]==200
    wait_details(usage,4)
    callers=[RequestStart.from_json(row["start_payload"]).caller for row in rows(usage,"usage_starts")]
    assert [c.end_user.subject if c.end_user else None for c in callers]==["user:first","user:second",None,None]
    assert callers[0].grant==callers[1].grant and callers[2].actor.id=="human:fixture"
    assert callers[3].grant.kind=="configured_scope" and callers[3].actor.id=="scope_fixture"
    assert len(calls)==4


@pytest.mark.parametrize("message",[
    {"content":"","reasoning_content":"private reasoning"},
    {"content":None,"tool_calls":[{"id":"call_fixture","type":"function","function":{"name":"fixture","arguments":"{}"}}]},
])
def test_serve_wire_fallbacks_do_not_invent_consumed_usage(store,message):
    from tests.router.test_serve import _routing
    usage,*_=store
    calls=[]
    routing=_routing(backends={"primary-local":relay(usage,calls,{"choices":[{"message":message,"finish_reason":"tool_calls"}]})})
    with server(routing,store) as conn:
        assert post(conn)[0]==200
    [terminal]=wait_details(usage)
    assert len(calls)==1 and terminal.dispatched is True
    assert terminal.tokens.input.count is terminal.tokens.output.count is None
    assert "usage_incomplete" in terminal.coverage
    assert sum(r["measured_input"]+r["estimated_input"]+r["measured_output"]+r["estimated_output"] for r in rows(usage,"usage_cumulative"))==0
