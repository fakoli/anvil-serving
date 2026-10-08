"""Hermetic all-path ownership and native managed-drain checks."""
import contextvars
import http.client
import json
import os
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from anvil_serving.router.admission import (
    RouterAdmission, RouterAdmissionClosed, managed_router_admission, owned_dispatch,
)
from anvil_serving.router.config import ServerConfig, load_server_config, ConfigError
from anvil_serving.router.front_door import make_server
from anvil_serving.router.front_door_runtime import DeliveryWorker
from anvil_serving.router.request_control import RequestControl
from anvil_serving.router import usage_store as ledger
from anvil_serving.router.usage_store import UsageError
from anvil_serving import router_manage, serves
from tests.router.test_usage_lifecycle import store, AT, END, rows
from tests.router.key_fixtures import tmp_path as tmp_path


@pytest.fixture
def gate():
    # A synthetic owner callback is source behavior evidence, never live roster proof.
    writes = []
    owner = RouterAdmission('config_fixture', persist=writes.append,
                            owner_scope=lambda:object(), roster_revision='roster_fixture')
    owner.writes = writes
    return owner


def close(gate):
    return gate.quiesce_router(confirm=True, dry_run=False)['barrier_token']


def test_storage_completion_never_grants_inference_ownership(gate):
    permit = gate.acquire('maintenance', storage_only=True)
    token = close(gate)
    with permit.bind():
        with pytest.raises(RouterAdmissionClosed):
            gate.acquire('chat')
        child = gate.acquire('maintenance', storage_only=True)
    permit.release()
    with pytest.raises(ValueError, match='router_drain_required'):
        gate.consume(token)
    child.release()
    assert gate.consume(token)['drained']


def test_final_audit_retains_request_and_storage_ownership(gate, monkeypatch):
    import io
    from http.server import BaseHTTPRequestHandler
    from anvil_serving.router.front_door import _make_handler
    entered, release = threading.Event(), threading.Event()
    class Keys:
        def record(self, *args):
            entered.set()
            assert release.wait(5)
    kind = _make_handler(object(), None, {}, api_keys=Keys(), router_admission=gate)
    handler = object.__new__(kind)
    handler.wfile = io.BytesIO()
    handler.command, handler.path = 'POST', '/v1/chat/completions'
    def request(self):
        assert self._admit_router('chat')
        self._anvil_client_id, self._anvil_http_status = 'key_fixture', 200
    monkeypatch.setattr(BaseHTTPRequestHandler, 'handle_one_request', request)
    errors = []
    def serve():
        try:
            handler.handle_one_request()
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=serve)
    thread.start()
    try:
        assert entered.wait(2)
        token = close(gate)
        result = gate.drain_router(token, 1)
        assert not result['drained'] and result['counts']['chat'] == 1
        assert result['counts']['maintenance'] == 1 and thread.is_alive()
        with pytest.raises(ValueError, match='router_drain_required'):
            gate.consume(token)
    finally:
        release.set(); thread.join(3)
    assert not thread.is_alive() and not errors
    assert gate.consume(token)['drained']


def test_consumed_device_request_cannot_enter_a_credential_writer(gate):
    from email.message import Message
    from types import SimpleNamespace
    from anvil_serving.router.front_door import _make_handler
    writes = []
    class Keys:
        def authenticate(self, *args, **kwargs):
            return SimpleNamespace(key_id='key_fixture', owner=None, allows_path=lambda *args: True)
        def admit(self, *args):
            writes.append('admit')
            return 0
    kind = _make_handler(object(), None, {}, auth_token='legacy_fixture', api_keys=Keys(), router_admission=gate)
    handler = object.__new__(kind); handler._reset_request_correlation()
    handler.command, handler.path = 'GET', '/v1/models'
    handler.headers = Message(); handler.headers['Authorization'] = 'Bearer ask_fixture'
    errors = []; handler._device_error = lambda *args: errors.append(args)
    token = close(gate); gate.consume(token)
    assert handler._device_access() is False
    assert writes == [] and errors[0][0] == 503
    assert not any(gate.status()['counts'].values())


@pytest.mark.parametrize('action', ['status', 'drain', 'readmit', 'consume'])
def test_stateless_management_never_counts_itself_or_writes_after_closure(gate, monkeypatch, action):
    import io
    from email.message import Message
    from http.server import BaseHTTPRequestHandler
    from anvil_serving.router.front_door import _make_handler
    writes = []
    class Keys:
        def record(self, *args):
            writes.append(args)
    kind = _make_handler(object(), None, {}, auth_token='legacy_fixture', api_keys=Keys(), router_admission=gate)
    handler = object.__new__(kind); handler.wfile = io.BytesIO()
    handler.command, handler.path = 'POST', '/v1/admin/transition'
    handler.headers = Message(); handler.headers['Authorization'] = 'Bearer legacy_fixture'
    token = close(gate); responses = []
    def respond(code, value, **kwargs):
        handler._anvil_http_status = code
        responses.append(value)
    handler._json = respond
    def request(self):
        assert self._device_access()
        self._handle_transition({'scope':'router', 'action':action, 'barrier_token':token,
                                 'timeout':1, 'confirm':True, 'dry_run':False})
    monkeypatch.setattr(BaseHTTPRequestHandler, 'handle_one_request', request)
    handler.handle_one_request()
    assert handler._anvil_http_status == 200 and responses
    assert not any(gate.status()['counts'].values())
    if action == 'readmit':
        assert writes and gate.status()['state'] == 'admitting'
    else:
        assert writes == []
    if action == 'consume':
        assert gate.status()['cutover_pending']


@pytest.mark.parametrize('phase', ['replay', 'dispatch'])
def test_connect_replay_and_dispatch_stay_owned_and_refuse_new_closed_work(gate, monkeypatch, phase):
    from types import SimpleNamespace
    from anvil_serving.router.front_door import _make_handler
    entered, release = threading.Event(), threading.Event()
    calls, errors = [], []
    def operation(name):
        calls.append(name)
        if phase == name:
            entered.set(); assert release.wait(5)
    class Verifier:
        def verify(self, *args, **kwargs):
            operation('replay')
            return SimpleNamespace(binding=SimpleNamespace(subject='fixture', policy_generation=1, epoch='fixture', role='admin'))
    class Connect:
        def dispatch(self, value):
            operation('dispatch')
            return {'ok':True}
    kind = _make_handler(object(), None, {}, api_keys=object(), connect_keys=Connect(),
                         connect_verifier=Verifier(), router_admission=gate)
    def handler():
        h = object.__new__(kind); h._reset_request_correlation()
        h.path, h.headers = '/v1/connect/keys', {}
        h.connection = SimpleNamespace(settimeout=lambda _:None)
        h._protocol_body = lambda **kwargs: {'action':'request'}
        h._json = lambda *args, **kwargs: None
        h._device_error = lambda *args: calls.append('refused')
        return h
    first = handler()
    def serve():
        try:
            first.do_POST()
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=serve); thread.start()
    try:
        assert entered.wait(2)
        token = close(gate)
        assert not gate.drain_router(token, 1)['drained']
        with pytest.raises(ValueError, match='router_drain_required'):
            gate.consume(token)
    finally:
        release.set(); thread.join(3)
    assert not thread.is_alive() and not errors
    gate.consume(token)
    before = list(calls); handler().do_POST()
    assert calls == before + ['refused']
    assert not any(gate.status()['counts'].values())


@pytest.mark.parametrize('operation', ['create', 'bind', 'admit', 'revoke', 'audit', 'connect', 'migrate', 'usage'])
def test_shared_store_writers_refuse_before_sqlite_but_authentication_stays_read_only(gate, tmp_path, monkeypatch, operation):
    from anvil_serving.router.keys import KeyStore, KeyStoreError
    from anvil_serving.router.connect_keys import ConnectKeys
    keys = KeyStore.initialize(tmp_path/'private'/'keys.sqlite3')
    metadata, secret = keys.create('fixture', ['model_fixture'], ['/v1/models', '/v1/chat/completions'])
    usage = ledger.UsageStore(keys); usage.migrate()
    connect = ConnectKeys(keys, ['model_fixture'])
    keys.owner_check = lambda *args: True
    keys._router_admission = gate
    token = close(gate); gate.consume(token)
    assert keys.authenticate(secret) is not None
    def opened():
        pytest.fail('closed writer opened SQLite')
    monkeypatch.setattr(keys, '_connect', opened)
    actions = {
        'create':lambda: keys.create('new', ['model_fixture'], ['/v1/models', '/v1/chat/completions']),
        'bind':lambda: keys.bind_owner(metadata['key_id'], 'service', 'service_fixture', 0),
        'admit':lambda: keys.admit(metadata['key_id']),
        'revoke':lambda: keys.revoke(metadata['key_id']),
        'audit':lambda: keys.record(metadata['key_id'], None, 'GET', '/v1/models', 200, 0),
        'connect':lambda: connect.dispatch({'principal':'human:'+'a'*64,'generation':'1','epoch':'b'*64,
                                           'administrator':False,'operation':{'action':'request'}}),
        'migrate':usage.migrate,
    }
    with pytest.raises((KeyStoreError, UsageError)):
        if operation == 'usage':
            with usage._write():
                pytest.fail('closed accounting writer entered')
        else:
            actions[operation]()
    assert not any(gate.status()['counts'].values())


def test_real_sqlite_writer_wait_is_owned_and_finishes_normally_after_quiesce(gate, tmp_path, monkeypatch):
    from contextlib import contextmanager
    from anvil_serving.router.keys import KeyStore
    keys = KeyStore.initialize(tmp_path/'private'/'keys.sqlite3')
    keys._router_admission = gate
    entered, errors = threading.Event(), []
    original = keys._connect
    @contextmanager
    def connected():
        with original() as db:
            db.set_trace_callback(lambda sql: entered.set() if sql == 'BEGIN IMMEDIATE' else None)
            yield db
    def write():
        try:
            keys.record('_legacy', 'request_fixture', 'GET', '/v1/models', 200, 0)
        except BaseException as error:
            errors.append(error)
    with original() as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        monkeypatch.setattr(keys, '_connect', connected)
        thread = threading.Thread(target=write); thread.start()
        try:
            assert entered.wait(2)
            token = close(gate)
            assert gate.status()['counts']['maintenance'] == 1
            with pytest.raises(ValueError, match='router_drain_required'):
                gate.consume(token)
        finally:
            blocker.execute('ROLLBACK'); thread.join(3)
    assert not thread.is_alive() and not errors
    assert keys.usage('_legacy')[0]['request_id'] == 'request_fixture'
    assert gate.consume(token)['drained']


def test_acquire_and_closure_share_one_lock_and_persist_before_response(gate):
    ready = threading.Barrier(2)
    acquired = []
    def dispatch():
        ready.wait()
        try:
            acquired.append(gate.acquire('chat'))
        except RouterAdmissionClosed:
            pass
    thread = threading.Thread(target=dispatch)
    thread.start(); ready.wait()
    token = close(gate); thread.join(2)
    assert not thread.is_alive() and gate.writes[-1]['barrier_token'] == token
    with pytest.raises(RouterAdmissionClosed):
        gate.acquire('purpose')
    assert gate.status()['counts']['chat'] == len(acquired)
    for permit in acquired:
        permit.release(); permit.release()
    assert gate.drain_router(token, 1)['drained'] is True


@pytest.mark.parametrize('family',['chat','purpose','audio','memory','media','internal','delivery','maintenance'])
def test_each_owned_family_holds_zero_and_timeout_does_not_cancel(gate, family):
    permit = gate.acquire(family)
    token = close(gate)
    result = gate.drain_router(token,1)
    assert result['timed_out'] and result['counts'][family] == 1
    assert not permit._released and gate.status()['state'] == 'quiesced'
    permit.release()
    assert gate.drain_router(token,1)['drained']


def test_children_survive_parent_release_and_detached_children_refuse(gate):
    parent = gate.acquire('chat')
    token = close(gate)
    with parent.bind():
        child = gate.acquire('internal')
    parent.release()
    assert gate.status()['counts']['internal'] == 1
    with pytest.raises(RouterAdmissionClosed):
        gate.acquire('internal')
    with pytest.raises(RouterAdmissionClosed):
        gate.acquire('internal',parent=parent)
    with child.bind():
        grandchild = gate.acquire('purpose')
    child.release(); grandchild.release()
    assert gate.drain_router(token,1)['drained']


@pytest.mark.parametrize('timeout',[True,0,-1,901,1.0,float('nan'),float('inf'),'1'])
def test_strict_bounded_deadline(gate, timeout):
    with pytest.raises(ValueError):
        gate.drain_router(close(gate),timeout)


def test_stale_token_config_roster_and_consumption_refuse_readmit(gate):
    token = close(gate)
    for field in ['revision','roster_revision']:
        before = getattr(gate,field); setattr(gate,field,'changed')
        with pytest.raises(ValueError): gate.readmit_router(token,confirm=True,dry_run=False)
        setattr(gate,field,before)
    with pytest.raises(ValueError): gate.drain_router('0'*64,1)
    gate.consume(token)
    assert gate.consume(token)['drained'] is True
    with pytest.raises(ValueError): gate.readmit_router(token,confirm=True,dry_run=False)
    with pytest.raises(RouterAdmissionClosed): gate.acquire('maintenance',completion=True)


def test_prior_tier_intent_untouched_by_readmit(gate):
    from anvil_serving.router.admission import TierAdmission
    tier = TierAdmission(['existing','other'])
    tier.quiesce('existing','maintenance')
    token = close(gate)
    assert gate.readmit_router(token)['applied'] is False
    gate.readmit_router(token,confirm=True,dry_run=False)
    assert tier.snapshot('existing').quiesced and not tier.snapshot('other').quiesced
    gate.acquire('chat').release()


def test_persistence_failure_remains_closed_and_refuses_success(gate):
    def fail(_): raise OSError('synthetic')
    gate._persist = fail
    with pytest.raises(OSError): close(gate)
    assert gate.status()['state'] == 'quiesced' and not gate.status()['durable']
    with pytest.raises(RouterAdmissionClosed): gate.acquire('chat')
    with pytest.raises(ValueError): gate.readmit_router(gate._token,confirm=True,dry_run=False)


def test_failed_readmit_does_not_open(gate):
    token=close(gate)
    gate._persist=lambda state: (_ for _ in ()).throw(OSError('synthetic'))
    with pytest.raises(OSError): gate.readmit_router(token,confirm=True,dry_run=False)
    assert gate.status()['state']=='quiesced'


def test_unconfigured_or_unknown_remote_never_zero(gate):
    owner = RouterAdmission(persist=lambda _:None)
    assert owner.drain_router(close(owner),1)['unknown']==['owner_roster_unknown']
    gate.observe('memory',lambda:(0,True))
    assert gate.drain_router(close(gate),1)['unknown']==['memory']


def test_generator_close_during_execution_does_not_release(gate):
    entered, release=threading.Event(),threading.Event()
    class Dispatcher:
        _router_admission=gate
        @owned_dispatch('internal')
        def generate(self):
            entered.set(); release.wait(5); yield 'done'
    stream=Dispatcher().generate()
    thread=threading.Thread(target=lambda:list(stream));thread.start();assert entered.wait(2)
    with pytest.raises(ValueError): stream.close()
    assert gate.status()['counts']['internal']==1
    release.set();thread.join(2)
    # A refused external close must retain rather than falsify completion.
    assert not thread.is_alive()
    stream.close()
    assert gate.status()['counts']['internal']==0


def test_disconnected_worker_and_finished_callback_keep_actual_thread_owned(gate):
    root=gate.acquire('chat'); entered=threading.Event();release=threading.Event()
    def operation(send):
        entered.set();release.wait(5)
    with root.bind(): worker=DeliveryWorker(operation,RequestControl())
    gate.track_thread(worker.thread);assert entered.wait(2)
    token=close(gate);worker.close()
    worker.when_finished(root.release)
    assert worker.thread.is_alive() and gate.status()['counts']['chat']==1
    release.set();worker.thread.join(2)
    assert not worker.thread.is_alive() and gate.drain_router(token,1)['drained']


@pytest.mark.parametrize('path',['/v1/chat/completions','/v1/messages','/v1/responses'])
@pytest.mark.parametrize('stream',[True,False])
@pytest.mark.parametrize('managed',[True,False])
def test_all_chat_dialects_modes_and_managed_workers_refuse_before_upstream(gate,path,stream,managed):
    calls=[]
    class Backend:
        def generate(self,request): calls.append(request);return iter(['ok'])
    server=make_server('127.0.0.1',0,Backend(),auth_token='synthetic',router_admission=gate,
                       server_config=ServerConfig() if managed else None)
    thread=threading.Thread(target=server.serve_forever);thread.start()
    token=close(gate)
    body={'model':'llm.primary','messages':[{'role':'user','content':'hello'}],'max_tokens':10,'stream':stream}
    if path=='/v1/responses':body={'model':'llm.primary','input':'hello','stream':stream}
    conn=http.client.HTTPConnection(*server.server_address,timeout=3)
    try:
        conn.request('POST',path,json.dumps(body),{'Authorization':'Bearer synthetic','Content-Type':'application/json'})
        response=conn.getresponse();response.read()
        assert response.status==503 and not calls
        assert gate.drain_router(token,1)['drained']
    finally:
        conn.close();server.shutdown();server.server_close();thread.join(3)
    assert not thread.is_alive()


@pytest.mark.parametrize('module,class_name,method,family',[
    ('purpose','PurposeRouter','dispatch','purpose'),('audio','AudioGateway','dispatch_transcription','audio'),
    ('audio','AudioGateway','dispatch_speech','audio'),('memory','MemoryRouter','dispatch','memory'),
    ('serve','RoutingBackend','_generate','chat'),('backends.relay','RelayBackend','generate','internal'),
])
def test_actual_common_internal_dispatchers_share_gate_before_validation(gate,module,class_name,method,family):
    import importlib
    cls=getattr(importlib.import_module('anvil_serving.router.'+module),class_name)
    dispatcher=cls.__new__(cls);dispatcher._router_admission=gate
    close(gate)
    with pytest.raises(RouterAdmissionClosed): getattr(dispatcher,method)(None)
    assert not any(gate.status()['counts'].values())


def test_native_producer_registers_actual_owner_and_disabled_segment(store,tmp_path,monkeypatch):
    usage,owner,run,_=store
    # Separate empty protected native domain, same synthetic physical owner.
    import itertools
    from datetime import datetime, timedelta
    ticks=itertools.count()
    monkeypatch.setattr(ledger,'_now',lambda:(datetime.fromisoformat(END.replace('Z','+00:00')) + timedelta(microseconds=next(ticks))).isoformat().replace('+00:00','Z'))
    private=usage.key_store.path.parent
    config=ServerConfig(auth_env='SYNTHETIC_TOKEN',admission_state_path=str(private/'intent.json'),
                        api_keys_path=str(usage.key_store.path),router_owner_id=owner.host_domain_id,
                        router_owner_roster=(owner.host_domain_id,),usage_domain_id='native_fixture')
    native,actual,registered=managed_router_admission(config,'config_fixture')
    try:
        assert registered != run and actual is not None
        assert native.usage_scope().run_ids==(registered,)
        segments=[r for r in rows(actual,'usage_coverage_segments') if r['run_id']==registered]
        assert len(segments)==1 and segments[0]['enabled']==0
        token=close(native);assert native.drain_router(token,1)['drained']
        with pytest.raises(BlockingIOError):managed_router_admission(config,'config_fixture')
        # Restore retains closure and refuses a different actual owner identity.
        state=json.loads(Path(config.admission_state_path+'.router').read_text())['closure']
        restored=RouterAdmission('config_fixture',persist=lambda _:None,restored=state,
                                 owner_scope=lambda:object(),roster_revision='different_owner')
        with pytest.raises(ValueError):restored.readmit_router(token,confirm=True,dry_run=False)
    finally:
        os.close(native._owner_descriptor)


@pytest.mark.parametrize('roster',[(),('other',),('owner','owner'),('owner','other')])
def test_native_roster_requires_one_closed_configured_owner(tmp_path,roster):
    config=ServerConfig(router_owner_id='owner',router_owner_roster=roster)
    with pytest.raises(ValueError):managed_router_admission(config,'config')


def test_native_scope_foreign_run_and_unknown_process_hold(store,monkeypatch):
    usage,owner,run,_=store
    monkeypatch.setattr(ledger,'_now',lambda:END)
    assert usage.managed_owner_scope(run,owner,domain_id='domain_fixture',configuration_revision='config_fixture')
    usage.register_run(owner,domain_id='domain_fixture',configuration_revision='config_fixture',enabled=False)
    with pytest.raises(UsageError):usage.managed_owner_scope(run,owner,domain_id='domain_fixture',configuration_revision='config_fixture')


@pytest.mark.parametrize('action',['restart','reload','down','up'])
def test_old_runtime_lifecycle_refuses_before_any_mutating_docker(action,monkeypatch):
    commands=[]
    monkeypatch.setattr(router_manage,'require_router_drain',lambda *a,**k: (_ for _ in ()).throw(ValueError('hold')))
    def run(argv,**kwargs):
        from tests.conftest import proc
        commands.append(argv)
        if argv[:2]==['docker','inspect']:
            return proc(0,'running\n' if 'State.Status' in ' '.join(argv) else 'anvil-serving\n')
        raise AssertionError('mutation bypass')
    if action in {'restart','reload'}:result=getattr(router_manage,'cmd_'+action)('synthetic-router',_run=run)
    elif action=='down':result=router_manage.cmd_down('synthetic-compose','router',_run=run)
    else:result=router_manage.cmd_up('synthetic-compose','router',_run=run)
    assert result==1 and all(argv[:2]==['docker','inspect'] for argv in commands)


def test_install_shared_native_seam_holds_before_backup_or_replacement(monkeypatch):
    from anvil_serving.router.topology_validation import ValidatedRouterConfigSnapshot
    monkeypatch.setattr(router_manage,'require_router_drain',lambda *a,**k: (_ for _ in ()).throw(ValueError('hold')))
    snapshot=ValidatedRouterConfigSnapshot.__new__(ValidatedRouterConfigSnapshot)
    object.__setattr__(snapshot,'config_bytes',b'synthetic')
    assert serves._install_router_config(snapshot,_run=lambda *a,**k: (_ for _ in ()).throw(AssertionError('mutation bypass')))==1


def test_remote_transition_scope_token_and_strict_integer_bound(monkeypatch):
    requests=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,_):return b'{"scope":"router","result":{"drained":true}}'
    def opened(request,**kwargs):requests.append(json.loads(request.data));return Response()
    token='a'*64
    result=router_manage.transition_request('drain',scope='router',barrier_token=token,timeout=30,
        env={'ANVIL_ROUTER_TOKEN':'synthetic'},_open=opened)
    assert result['result']['drained'] and requests[0]['timeout']==30 and type(requests[0]['timeout']) is int
    for kwargs in [{'tier_id':'tier'}, {'member_id':'member'}, {'timeout':1.0}, {'timeout':True}, {'barrier_token':'bad'}]:
        values={'scope':'router','barrier_token':token,'timeout':30,**kwargs}
        with pytest.raises(ValueError):router_manage.transition_request('drain',env={'ANVIL_ROUTER_TOKEN':'synthetic'},_open=opened,**values)
    assert len(requests)==1


def test_policy_change_and_failed_background_resume_remain_hold(gate):
    policy=['before'];gate._policy_revision=lambda:policy[0]
    token=close(gate);policy[0]='after'
    with pytest.raises(ValueError):gate.readmit_router(token,confirm=True,dry_run=False)
    policy[0]='before'
    gate._on_readmit.append(lambda:(_ for _ in ()).throw(ValueError('resume_failed')))
    with pytest.raises(ValueError):gate.readmit_router(token,confirm=True,dry_run=False)
    assert gate.status()['state']=='quiesced' and gate.writes[-1]['barrier_token']==token


def test_retained_media_thread_and_ambiguous_submission_never_become_zero(gate):
    from anvil_serving.media.worker import MediaReconciliationLoop
    from anvil_serving.media.contracts import JobState
    from types import SimpleNamespace
    entered=threading.Event();finished=threading.Event()
    job=SimpleNamespace(id='synthetic_job',state=JobState.SUBMITTING,backend_prompt_id=None)
    jobs=[job]
    class Store:
        def nonterminal(self):return list(jobs)
    class Reconciler:
        store=Store()
        def reconcile_all(self):entered.set();finished.wait(3);return []
    worker=MediaReconciliationLoop(Reconciler(),poll_seconds=.01,router_admission=gate)
    gate.observe('media',worker.drain_readback)
    worker.start();assert entered.wait(1)
    token=close(gate)
    try:
        result=gate.drain_router(token,1)
        assert not result['drained'] and result['counts']['media']>=2
        jobs.clear();finished.set();worker.stop(timeout=2)
        assert not worker.is_alive
        result=gate.drain_router(token,1)
        assert not result['drained'] and 'media' in result['unknown']
    finally:
        finished.set();worker.stop(timeout=2)
    assert not worker.is_alive


@pytest.mark.parametrize('method',['workflow_run','_resume_existing','_resume_accepted','_resume_preparing','_submit_rendered','job_cancel'])
def test_all_media_submit_resume_common_paths_refuse_before_backend(gate,method):
    from anvil_serving.media.operations import MediaOperations
    operations=MediaOperations.__new__(MediaOperations);operations._router_admission=gate
    close(gate)
    with pytest.raises(RouterAdmissionClosed):getattr(operations,method)(None)


def test_completion_delivery_can_finish_while_closed_and_blocks_consume(gate):
    token=close(gate)
    subscriber=gate.acquire('delivery',completion=True)
    with pytest.raises(ValueError):gate.consume(token)
    subscriber.release();assert gate.drain_router(token,1)['drained']
    gate.consume(token)
    with pytest.raises(RouterAdmissionClosed):gate.acquire('delivery',completion=True)


@pytest.mark.parametrize('kind',['fifo','symlink','public'])
def test_native_state_file_guard_refuses_before_open(store,tmp_path,kind):
    usage,owner,run,_=store
    private=usage.key_store.path.parent
    state=private/'protected-intent.json.router'
    if kind=='fifo':os.mkfifo(state,0o600)
    elif kind=='symlink':state.symlink_to(private/'missing-target')
    else:state.write_text('{}');state.chmod(0o644)
    config=ServerConfig(admission_state_path=str(private/'protected-intent.json'),
        api_keys_path=str(usage.key_store.path),router_owner_id=owner.host_domain_id,
        router_owner_roster=(owner.host_domain_id,),usage_domain_id='native_fixture')
    with pytest.raises(Exception):managed_router_admission(config,'config_fixture')


def test_real_native_factory_and_http_management_share_owner(store,tmp_path,monkeypatch):
    from anvil_serving.router.serve import build_server
    from tests.router.helpers import StaticBackend
    usage,physical,run,_=store
    monkeypatch.setattr(ledger,'_now',lambda:END)
    config=tmp_path/'native-router.toml'
    content=Path('configs/example.toml').read_text()
    content+='\n[server]\nauth_env="SYNTHETIC_AUTH"\nadmission_state_path='+json.dumps(str(usage.key_store.path.parent/'factory-intent.json'))+'\napi_keys_path='+json.dumps(str(usage.key_store.path))+'\nrouter_owner_id="host_fixture"\nrouter_owner_roster=["host_fixture"]\nusage_domain_id="factory_fixture"\n'
    config.write_text(content)
    server=build_server(str(config),host='127.0.0.1',port=0,
                       backends={'primary-local':StaticBackend([]),'omni-local':StaticBackend([])},
                       env={'SYNTHETIC_AUTH':'synthetic'})
    thread=threading.Thread(target=server.serve_forever);thread.start()
    owner=server.anvil_router_admission
    conn=http.client.HTTPConnection(*server.server_address,timeout=3)
    try:
        token=close(owner)
        assert owner.drain_router(token,1)['drained']
        conn.request('GET','/v1/admin/transition?scope=router',headers={'Authorization':'Bearer synthetic'})
        response=conn.getresponse();body=json.loads(response.read())
        assert response.status==200 and body['result']['durable'] and body['result']['state']=='quiesced'
        assert server.anvil_routing._router_admission is owner
        owner.readmit_router(token,confirm=True,dry_run=False)
        token=close(owner);assert owner.drain_router(token,1)['drained']
    finally:
        conn.close();server.shutdown();server.server_close();thread.join(3)
    assert not thread.is_alive() and owner._owner_descriptor is None


def test_eager_dispatch_returned_iterator_remains_owned_until_real_cleanup(gate):
    events=[]
    class Dispatcher:
        _router_admission=gate
        @owned_dispatch('internal')
        def generate(self):
            events.append('opened')
            return iter(['first','second'])
    stream=Dispatcher().generate();token=close(gate)
    assert events==['opened'] and gate.status()['counts']['internal']==1
    assert list(stream)==['first','second']
    assert gate.drain_router(token,1)['drained']


def test_direct_tracked_checking_root_can_finish_children_after_closure(gate):
    from anvil_serving.router.serve import RoutingBackend
    from anvil_serving.router.config import load
    from anvil_serving.router.internal import InternalRequest,Message
    from tests.router.helpers import StaticBackend
    routing=RoutingBackend(load('configs/example.toml'),{'primary-local':StaticBackend(['ok'])})
    routing._router_admission=gate
    stream=routing.generate_tracked(InternalRequest(model='llm.primary',messages=(Message('user','synthetic'),)),gateway_request_id='synthetic')
    token=close(gate)
    assert gate.status()['counts']['chat']==1
    assert list(stream)
    assert gate.status()['counts']['chat']==1
    stream.finish_delivery()
    assert gate.drain_router(token,1)['drained']
    routing.close()


@pytest.mark.parametrize('stale',[False,True])
def test_native_local_cutover_consumes_actual_owner_zero_and_refuses_stale(gate,tmp_path,monkeypatch,stale):
    import hashlib
    config=tmp_path/'local-config.toml';config.write_text('synthetic owner bytes')
    gate.revision=hashlib.sha256(config.read_bytes()).hexdigest()
    monkeypatch.setattr(router_manage,'DEFAULT_INSTALLED_CONFIG',str(config))
    monkeypatch.setenv('SYNTHETIC_NATIVE_TOKEN','synthetic')
    monkeypatch.setattr('anvil_serving.router.config.load_server_config',lambda _:ServerConfig(router_owner_id='owner_fixture',auth_env='SYNTHETIC_NATIVE_TOKEN'))
    calls=[]
    def transition(action,**kwargs):
        calls.append(action)
        assert kwargs['scope']=='router' and kwargs['router_url']=='http://127.0.0.1:8000'
        if action=='status':result=gate.status()
        elif action=='quiesce':result=gate.quiesce_router(confirm=True,dry_run=False)
        elif action=='drain':result=gate.drain_router(kwargs['barrier_token'],kwargs['timeout'])
        else:
            if stale:gate.roster_revision='changed'
            result=gate.consume(kwargs['barrier_token'])
        return {'scope':'router','result':result}
    monkeypatch.setattr(router_manage,'_transition_request',transition)
    if stale:
        with pytest.raises(ValueError):router_manage._local_router_cutover(timeout=1)
        assert gate.status()['state']=='quiesced' and not gate.status()['cutover_pending']
    else:
        receipt=router_manage._local_router_cutover(timeout=1)
        assert receipt['closed'] and receipt['drained'] and gate.status()['cutover_pending']
        assert router_manage._local_router_cutover(timeout=1)==receipt
    assert calls[:4]==['status','quiesce','drain','consume']


def test_readmit_checks_fresh_native_owner_scope(gate):
    token=close(gate)
    gate._owner_scope=lambda:(_ for _ in ()).throw(ValueError('foreign_live_run'))
    with pytest.raises(ValueError):gate.readmit_router(token,confirm=True,dry_run=False)
    assert gate.status()['state']=='quiesced'


def test_failed_iterator_cleanup_keeps_owned_reference_and_unknown(gate):
    class BadIterator:
        def __iter__(self):return self
        def __next__(self):raise StopIteration
        def close(self):raise OSError('synthetic cleanup failure')
    class Dispatcher:
        _router_admission=gate
        @owned_dispatch('internal')
        def generate(self):return BadIterator()
    stream=Dispatcher().generate();token=close(gate)
    stream.close()
    result=gate.drain_router(token,1)
    assert not result['drained'] and result['counts']['internal']==1 and 'internal' in result['unknown']


@pytest.mark.parametrize("target", ["different-service", "different-container", "multiple", "unknown"])
def test_compose_mutation_target_refused_before_old_owner_transition(target):
    from tests.conftest import proc
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "inspect", "--format"]:
            return proc(0, json.dumps({"container_id":"a"*64,"image_id":"sha256:"+"b"*64,
                "image_reference":"anvil-serving:synthetic","started_at":"2026-01-01T00:00:00Z",
                "restart_count":0,"compose_project":"anvil-serving",
                "compose_service":"other" if target == "different-service" else "router","mounts":[]}))
        if argv[:3] == ["docker", "image", "inspect"]:
            return proc(0, "sha256:"+"b"*64)
        if argv[:2] == ["docker", "compose"]:
            return proc(1 if target == "unknown" else 0,
                        "c"*64 if target == "different-container" else "a"*64+"\n"+"c"*64)
        raise AssertionError("old owner was closed or mutation ran")
    with pytest.raises(ValueError):
        router_manage.require_router_drain("synthetic-router", compose="synthetic-compose",
                                          service="router", _run=run)
    assert not any(argv[:2] == ["docker", "exec"] for argv in calls)
