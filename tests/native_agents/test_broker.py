import threading
import time
from uuid import uuid4
import pytest
from anvil_serving.native_agents.core import Broker, PREFIX
from anvil_serving.native_agents.store import Store
from anvil_serving.native_agents.types import AgentsError, Directory, Workspace, NativeAdmission

A = "human:" + "a" * 64
B = "human:" + "b" * 64

class Owner:
    def __init__(self):
        self.calls = []; self.entered = threading.Event(); self.release = threading.Event(); self.wait = False
        self.catalog_value = {"revision":"rev", "ownerInstance":"instance", "commands":[{"id":"cmd","name":"review","description":"Review","source":"prompt","supported":True}]}
        self.pending = {"ownerInstance":"instance", "interactions":[{"id":"dialog","method":"select","title":"Choose","options":["yes","no"]}]}
    def sessions(self): self.calls.append("sessions"); return []
    def ensure(self,sid,cwd): self.calls.append(("ensure",sid,cwd))
    def catalog(self,sid): self.calls.append(("catalog",sid)); return self.catalog_value
    def operation(self,sid,rid): raise AgentsError(404,"not_found")
    def interactions(self,sid): return self.pending
    def invoke(self,sid,rid,instance,message):
        self.calls.append(("invoke",sid,rid,instance,message)); self.entered.set()
        if self.wait: self.release.wait(5)
    def invoke_command(self,sid,rid,instance,command,revision,arguments):
        assert revision == self.catalog_value["revision"]
        return self.invoke(sid,rid,instance,"/review"+(" "+arguments if arguments else ""))
    def abort(self,sid,pending): self.calls.append(("abort",sid,pending)); self.release.set()
    def respond(self,*args): self.calls.append(("respond",*args))
    def history(self,sid,before=None): return {"context":{"messages":[],"hasMore":False}}
    def state(self,sid): return {"running":False}
    def events(self,sid):
        yield b": heartbeat\n\n"
        yield b"data: secret\n\n"

@pytest.fixture(autouse=True)
def synthetic_store_ancestry(monkeypatch):
    # Production checks every ancestor; unit stores live under pytest's temp root.
    monkeypatch.setattr("anvil_serving.native_agents.store.protected_directory",lambda path: None)

@pytest.fixture
def setup(tmp_path):
    roots=[]
    for i,p in [("one",A),("two",B)]:
        path=tmp_path/i; path.mkdir()
        roots.append(Workspace(i,p,i,(Directory("project","Project",path,"/workspace/project"),),"http://127.0.0.1:9001",tmp_path/"token"))
    owners={w.id:Owner() for w in roots}
    state={"valid":True}
    broker=Broker("test",roots,Store(tmp_path/"store.db"),owners,lambda token: NativeAdmission(A if token=="a" else B,1,time.time()+300),lambda a:state["valid"])
    yield broker,owners,state
    for o in owners.values(): o.release.set()
    broker.worker.shutdown(); broker.stop_worker.shutdown(); broker.interaction_worker.shutdown()


def settle(broker,a,rid):
    for _ in range(100):
        op=broker.operation(a,rid)
        if op["state"] not in ("reserved","dispatched"): return op
        time.sleep(.005)
    raise AssertionError("operation did not settle")


def create(broker,a,w="one",rid=None):
    rid=rid or str(uuid4())
    status,body=broker.handle(a,"POST",f"{PREFIX}/workspaces/{w}/sessions",{"requestID":rid,"directoryID":"project"})
    assert status==202
    assert settle(broker,a,rid)["state"]=="completed"
    return body["operation"]["sessionID"],rid


def test_two_principals_cannot_list_read_mutate_or_stop_other_workspace(setup):
    broker,owners,_=setup; a=broker.admission("a"); b=broker.admission("b")
    sid,rid=create(broker,a)
    assert [w["id"] for w in broker.handle(b,"GET",PREFIX+"/workspaces")[1]["workspaces"]]==["two"]
    before=len(owners["one"].calls)
    for target,method,body in [(f"workspaces/one/sessions/{sid}","GET",None),(f"workspaces/one/sessions/{sid}/abort","POST",{"requestID":str(uuid4())}),(f"operations/{rid}","GET",None)]:
        with pytest.raises(AgentsError) as error: broker.handle(b,method,PREFIX+"/"+target,body)
        assert error.value.status==404
    with pytest.raises(AgentsError): next(broker.events(b,"one",sid))
    assert len(owners["one"].calls)==before


def test_invocation_stop_does_not_wait_for_extension_preflight(setup):
    broker,owners,_=setup; a=broker.admission("a"); sid,_=create(broker,a); owner=owners["one"]; owner.wait=True
    rid=str(uuid4()); base=f"{PREFIX}/workspaces/one/sessions/{sid}"
    _,result=broker.handle(a,"POST",base+"/invocations",{"requestID":rid,"commandID":"cmd","catalogRevision":"rev","arguments":"carefully"})
    assert owner.entered.wait(1); assert result["operation"]["state"]=="dispatched"
    stop=str(uuid4()); broker.handle(a,"POST",base+"/abort",{"requestID":stop,"pendingRequestIDs":[]})
    assert settle(broker,a,stop)["state"]=="completed"
    assert settle(broker,a,rid)["state"]=="accepted"
    abort=next(c for c in owner.calls if c[0]=="abort"); assert rid in abort[2]
    assert [c for c in owner.calls if c[0]=="invoke"][0][-1]=="/review carefully"


def test_stream_saturation_preserves_another_principals_controls(setup):
    broker,owners,_=setup; a=broker.admission("a"); b=broker.admission("b")
    sid,_=create(broker,a); other,_=create(broker,b,w="two")
    streams=[broker.events(a,"one",sid) for _ in range(2)]
    for stream in streams: next(stream)
    with pytest.raises(AgentsError) as error: next(broker.events(a,"one",sid))
    assert error.value.status==429
    stop=str(uuid4()); broker.handle(b,"POST",f"{PREFIX}/workspaces/two/sessions/{other}/abort",{"requestID":stop,"pendingRequestIDs":[]})
    assert settle(broker,b,stop)["state"]=="completed"
    for stream in streams: stream.close()
    assert broker.stream_total==0


def test_conclusive_failed_creation_does_not_list_phantom_session(setup):
    broker,owners,_=setup; a=broker.admission("a")
    def reject(sid,cwd): raise AgentsError(409,"directory_changed")
    owners["one"].ensure=reject
    rid=str(uuid4()); _,value=broker.handle(a,"POST",f"{PREFIX}/workspaces/one/sessions",{"requestID":rid,"directoryID":"project"})
    assert settle(broker,a,rid)["state"]=="rejected"
    assert broker.handle(a,"GET",f"{PREFIX}/workspaces/one/sessions")[1]["sessions"]==[]


def test_stop_overtakes_prompt_and_cancellation_survives_restart(setup,tmp_path):
    broker,owners,_=setup; a=broker.admission("a"); sid,_=create(broker,a)
    rid=str(uuid4()); stop=str(uuid4()); base=f"{PREFIX}/workspaces/one/sessions/{sid}"
    broker.handle(a,"POST",base+"/abort",{"requestID":stop,"pendingRequestIDs":[rid]})
    assert settle(broker,a,stop)["state"]=="completed"
    # Simulate a delayed POST arriving only after Stop has completed.
    broker.store.db.close(); broker.store=Store(tmp_path/"store.db")
    _,result=broker.handle(a,"POST",base+"/prompts",{"requestID":rid,"message":"must never execute"})
    assert result["operation"]["state"]=="rejected"
    assert result["operation"]["code"]=="cancelled_before_dispatch"
    assert not any(c[0]=="invoke" for c in owners["one"].calls)


def test_blocked_interactions_do_not_starve_stop(setup):
    broker,owners,_=setup; a=broker.admission("a"); sid,_=create(broker,a)
    entered=threading.Event(); release=threading.Event(); owner=owners["one"]
    def respond(*args): entered.set(); release.wait(2)
    owner.respond=respond
    route=f"{PREFIX}/workspaces/one/sessions/{sid}"
    try:
        for _ in range(4):
            broker.handle(a,"POST",route+"/interactions/dialog/response",{"requestID":str(uuid4()),"ownerInstance":"instance","answer":{"value":"yes"}})
        assert entered.wait(1)
        rid=str(uuid4()); broker.handle(a,"POST",route+"/abort",{"requestID":rid,"pendingRequestIDs":[]})
        assert settle(broker,a,rid)["state"]=="completed"
    finally: release.set()


def test_invalid_session_routes_fail_before_native_access(setup):
    broker,owners,_=setup; a=broker.admission("a"); sid,_=create(broker,a)
    base=f"{PREFIX}/workspaces/one/sessions/{sid}"
    for method,suffix,body in [("GET","/unknown",None),("POST","/state",{}),("POST","/prompts",{"message":"x"}),("POST","/prompts",{"message":"x","requestID":"invalid"}),("GET","/commands?x=y",None)]:
        before=list(owners["one"].calls)
        with pytest.raises(AgentsError): broker.handle(a,method,base+suffix,body)
        assert owners["one"].calls==before
