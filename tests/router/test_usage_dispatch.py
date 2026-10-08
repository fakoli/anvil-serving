"""Actual admission, transport and worker accounting with synthetic owners."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import http.client
import json
import threading
import time

import pytest

from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.config import ServerConfig
from anvil_serving.router.decision_log import DecisionLog, normalize_usage
from anvil_serving.router.front_door import make_server
from anvil_serving.router.identity import legacy_caller
from anvil_serving.router.internal import InternalRequest, Message, UsageInvocation
from anvil_serving.router.purpose import PurposeRouter
from anvil_serving.router.serve import ReplicaRuntime, _AutoConcurrencyGate, _ConcurrencyLimitedBackend
from anvil_serving.router.usage_store import Terminal, UsageError
from anvil_serving.router.workloads import RouterWorkloadRegistry
from tests.router.helpers import make_tier
from tests.router.test_embeddings import EMBED_PM, RERANK_PM, FakeTransport
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
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield http.client.HTTPConnection(*httpd.server_address, timeout=10)
    finally:
        httpd.shutdown(); httpd.server_close(); thread.join(5)


def post(conn, path=CHAT, body=None, headers=None):
    conn.request("POST", path, json.dumps(body or {"model":"llm.primary","messages":[{"role":"user","content":"hello"}]}),
                 {"Content-Type":"application/json","Authorization":"Bearer synthetic-token", **(headers or {})})
    response = conn.getresponse()
    result = response.status, response.read()
    return result


def wait_details(usage, count=1):
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


@pytest.mark.parametrize("kind,pm,path,body", [
    ("embedding",EMBED_PM,"/v1/embeddings",{"model":EMBED_PM.model,"input":"hi"}),
    ("rerank",RERANK_PM,"/v1/rerank",{"model":RERANK_PM.model,"query":"hi","documents":["doc"]}),
])
def test_purpose_actual_start_attempt_and_na_output(store, kind, pm, path, body):
    usage,*_=store
    calls=[]
    def transport(*args,**kwargs):
        assert rows(usage,"usage_starts")[0]["dispatched"]==1
        calls.append(True)
        return b'{"usage":{"total_tokens":0},"data":[]}'
    purpose=PurposeRouter([pm],transport=transport)
    with server(relay(usage,[]),store,purpose=purpose) as conn:
        assert post(conn,path,body)[0]==200
    [terminal]=wait_details(usage)
    assert calls==[True] and terminal.dispatched is True
    assert terminal.tokens.input.count==0 and terminal.tokens.output.applicability=="not_applicable"


@pytest.mark.parametrize("failure", ["missing_scope","start_write"])
def test_start_failure_refuses_without_any_transport(store,monkeypatch,failure):
    usage,_,run,scope=store
    calls=[]
    if failure=="start_write":
        monkeypatch.setattr(usage,"start",lambda *a,**k: (_ for _ in ()).throw(UsageError()))
    if failure=="missing_scope":
        with server(relay(usage,calls),store,usage_authority=None) as conn:
            assert post(conn)[0]==503
    else:
        with server(relay(usage,calls),store) as conn:
            assert post(conn)[0]==503
    assert calls==[] and rows(usage,"usage_starts")==[]


def test_terminal_failure_preserves_success_and_unresolved_failure_health(store,monkeypatch):
    usage,*_=store
    monkeypatch.setattr(usage,"finalize",lambda *args: (_ for _ in ()).throw(UsageError()))
    with server(relay(usage,[]),store) as conn:
        assert post(conn)[0]==200
    assert rows(usage,"usage_details")==[] and len(rows(usage,"usage_starts"))==1
    assert usage.health("domain_fixture")["accounting_failures"]==1


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


@pytest.mark.parametrize("wrapper", [lambda b:_ConcurrencyLimitedBackend(b,1),lambda b:_AutoConcurrencyGate(b,"fixture"),
                                     lambda b:ReplicaRuntime({"member_fixture":b})])
def test_actual_wrappers_delegate_on_generating_worker_even_close(wrapper,store):
    usage,_,run,scope=store
    invocation=UsageInvocation(usage,run,scope,legacy_caller(),"chat","llm.primary",clock=CLOCK)
    backend=wrapper(relay(usage,[]))
    request=InternalRequest("llm.primary",[Message("user","hi")],raw={"_anvil_usage":invocation})
    def run():
        iterator=backend.generate_member("member_fixture",request) if isinstance(backend,ReplicaRuntime) else backend.generate(request)
        assert list(iterator)
        return backend.get_last_normalized_usage()
    with ThreadPoolExecutor(max_workers=1) as pool:
        observed=pool.submit(run).result()
    assert observed.input.count==7 and observed.output.count==3
    assert backend.get_last_normalized_usage() is None


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


@pytest.mark.parametrize("expires", ["key","assertion"])
def test_real_writer_wait_rechecks_key_and_short_assertion_before_buckets(store,monkeypatch,expires):
    from anvil_serving.router.identity import WebUIBinding,WebUIProfile
    from tests.router.test_usage_webui_identity import token,SIGNER
    from anvil_serving.router import front_door
    usage,*_=store
    keys=usage.key_store
    metadata,secret=keys.create("display",["llm.primary"],[CHAT])
    keys.bind_owner(metadata["key_id"],"service","service:fixture",0)
    expiry=int(time.time())+2
    if expires=="key":
        with keys._connect() as db:
            db.execute("UPDATE keys SET expires_at=? WHERE key_id=?",(expiry,metadata["key_id"]))
    bound=WebUIBinding(WebUIProfile(metadata["key_id"],"device_key","webui:fixture",signer_env="SYNTHETIC_SIGNER",clock_skew_seconds=0),SIGNER)
    assertion=token({"sub":"user:fixture","iss":"open-webui","iat":expiry-2,"exp":expiry if expires=="assertion" else expiry+100})
    original=keys._connect
    attempting=threading.Event()
    @contextmanager
    def observed():
        with original() as db:
            db.set_trace_callback(lambda sql: attempting.set() if sql=="BEGIN IMMEDIATE" else None)
            yield db
    monkeypatch.setattr(keys,"_connect",observed)
    monkeypatch.setattr(front_door,"KeyStore",lambda path:keys)
    class NoTransport:
        def generate(self,request):
            assert False,"expired admission dispatched"
    with server(NoTransport(),store,server_config=ServerConfig(api_keys_path=str(keys.path)),accounting=False,
                webui_bindings=(bound,),workload_clock=lambda:datetime.now(timezone.utc)) as conn:
        with original() as writer,ThreadPoolExecutor(max_workers=1) as pool:
            writer.execute("BEGIN IMMEDIATE")
            time.sleep(max(0,expiry-.25-time.time()))
            future=pool.submit(post,conn,CHAT,None,{"Authorization":"Bearer "+secret,"X-OpenWebUI-User-Jwt":assertion})
            assert attempting.wait(1) and not future.done()
            time.sleep(max(0,expiry+.1-time.time()))
            writer.execute("COMMIT")
            assert future.result()[0]==(401 if expires=="assertion" else 503)
    with original() as db:
        assert db.execute("SELECT COUNT(*) FROM buckets").fetchone()[0]==0
    assert rows(usage,"usage_starts")==[]


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


def test_partial_stream_interruption_captured_after_close_on_worker(store):
    from tests.router.test_streaming_relay import FakeStreamTransport,_openai_sse
    usage,_,run,scope=store
    invocation=UsageInvocation(usage,run,scope,legacy_caller(),"chat","llm.primary",clock=CLOCK)
    transport=FakeStreamTransport(_openai_sse(
        {"choices":[],"usage":{"prompt_tokens":11,"completion_tokens":2}},
        {"choices":[{"delta":{"content":"first"}}]},
        {"choices":[{"delta":{"content":"later"}}]},done=False))
    backend=_ConcurrencyLimitedBackend(RelayBackend(make_tier("openai"),stream_transport=transport),1)
    request=InternalRequest("llm.primary",[Message("user","hi")],stream=True,raw={"_anvil_usage":invocation})
    def worker():
        iterator=backend.generate(request)
        assert next(iterator)=="first"
        iterator.close()
        invocation.capture(backend,"cancelled")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(worker).result()
    invocation.finish("disconnected")
    [terminal]=wait_details(usage)
    assert terminal.dispatched is True and terminal.outcome=="disconnected"
    assert terminal.tokens.input.count==11 and terminal.tokens.output.count==2
    assert terminal.tokens.output.partial and "usage_incomplete" in terminal.coverage
    assert transport.response.closed
    assert backend.get_last_normalized_usage() is None


def test_transport_and_unknown_model_rejection_distinct_and_attempt_write_failure_zero_calls(store,monkeypatch):
    from tests.router.test_serve import _routing
    usage,*_=store
    calls=[]
    routing=_routing(backends={"primary-local":relay(usage,calls)})
    with server(routing,store) as conn:
        assert post(conn,body={"model":"unknown_fixture","messages":[{"role":"user","content":"hello"}]})[0]==404
    [terminal]=wait_details(usage)
    assert terminal.dispatched is False and terminal.generation_outcome=="rejected" and calls==[]
    monkeypatch.setattr(usage,"note_dispatch",lambda *a:(_ for _ in ()).throw(UsageError()))
    with server(relay(usage,calls),store) as conn:
        assert post(conn)[0]==500
    assert calls==[]


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


@pytest.mark.parametrize("relation",["exclusive","inclusive_parent","unobserved"])
def test_declared_actual_child_inherits_caller_and_contributes_once(store,relation):
    from anvil_serving.router.usage_store import RequestStart
    usage,_,run,scope=store
    parent=UsageInvocation(usage,run,scope,legacy_caller(),"chat","llm.primary",clock=CLOCK)
    parent.dispatch(); parent.tokens=normalize_usage({"prompt_tokens":7,"completion_tokens":3},"openai")
    child=UsageInvocation(usage,run,scope,parent.start.caller,"embedding",EMBED_PM.model,clock=CLOCK,
                          parent=parent.start,usage_relation=relation)
    purpose=PurposeRouter([EMBED_PM],transport=FakeTransport({"usage":{"prompt_tokens":5}}))
    purpose.dispatch("embedding",{"model":EMBED_PM.model,"input":"fixture"},invocation=child)
    child.capture(purpose,"success");child.finish("success")
    parent.generation="success";parent.finish("success")
    child.finish("success")
    saved=[RequestStart.from_json(r["start_payload"]) for r in rows(usage,"usage_starts")]
    assert saved[0].caller==saved[1].caller and saved[1].parent_request_id==parent.start.request_id
    totals=rows(usage,"usage_cumulative")
    assert sum(r["requests"] for r in totals)==1
    assert sum(r["attempts"] for r in totals)==2
    assert sum(r["measured_input"] for r in totals)==(12 if relation=="exclusive" else 7)
    assert len(rows(usage,"usage_details"))==2


def test_connect_checker_once_outside_sqlite_and_full_local_cas(store,tmp_path,monkeypatch):
    from tests.router.test_usage_identity import connect
    from anvil_serving.router import front_door
    from anvil_serving.router.usage_store import UsageStore
    _,owner,_,scope=store
    keys,_,key_id,secret,account=connect(tmp_path / "second")
    usage=UsageStore(keys)
    run=usage.register_run(owner,domain_id=scope.domain_id,configuration_revision=scope.configuration_revision,enabled=True)
    scope=replace(scope,run_ids=(run,))
    checked=[]
    def checker(*owner_identity):
        with keys._connect() as db:
            db.execute("BEGIN IMMEDIATE")  # Would fail if admission held its writer.
            db.execute("ROLLBACK")
        checked.append(owner_identity)
        return True
    keys.owner_check=checker
    monkeypatch.setattr(front_door,"KeyStore",lambda path:keys)
    calls=[]
    with server(relay(usage,calls),(usage,owner,run,scope),server_config=ServerConfig(api_keys_path=str(keys.path))) as conn:
        assert post(conn,headers={"Authorization":"Bearer "+secret})[0]==200
    assert len(checked)==len(calls)==1
    from anvil_serving.router.usage_store import RequestStart
    caller=RequestStart.from_json(rows(usage,"usage_starts")[0]["start_payload"]).caller
    assert caller.grant==keys.authenticate(secret,check_owner=False,snapshot=True).caller_snapshot.grant


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
