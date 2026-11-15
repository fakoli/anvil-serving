"""Hermetic all-path ownership and native managed-drain checks."""
import http.client
import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving.router.admission import (
    RouterAdmission, RouterAdmissionClosed, owned_dispatch,
)
from anvil_serving.router import admission
from anvil_serving.router.config import ServerConfig
from anvil_serving.router.front_door import make_server
from anvil_serving.router import usage_store as ledger
from anvil_serving.router.usage_store import UsageError
from anvil_serving import router_manage
from tests.router.test_usage_lifecycle import store as store, END
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


def expire_drain_deadlines(monkeypatch, count):
    """Advance only the owned drain clock after each required ownership scan."""
    instants = iter(value for deadline in range(count) for value in (float(deadline), float(deadline + 1)))
    monkeypatch.setattr(admission, 'time', SimpleNamespace(monotonic=lambda: next(instants)))


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
        expire_drain_deadlines(monkeypatch, 1)
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


@pytest.mark.parametrize('family',['chat','purpose','audio','memory','media','internal','delivery','maintenance'])
def test_each_owned_family_holds_zero_and_timeout_does_not_cancel(gate, family, monkeypatch):
    permit = gate.acquire(family)
    token = close(gate)
    # The first scan observes the held permit; a virtual deadline avoids
    # repeating that same immutable state for a wall-clock second.
    # The second pair leaves a clock value for the post-release zero scan.
    instants = iter((0.0, 1.0, 1.0))
    monkeypatch.setattr(admission, 'time', SimpleNamespace(monotonic=lambda: next(instants)))
    result = gate.drain_router(token,1)
    assert result['timed_out'] and result['counts'][family] == 1
    assert not permit._released and gate.status()['state'] == 'quiesced'
    permit.release()
    assert gate.drain_router(token,1)['drained']


@pytest.mark.parametrize('timeout',[True,0,-1,901,1.0,float('nan'),float('inf'),'1'])
def test_strict_bounded_deadline(gate, timeout):
    with pytest.raises(ValueError):
        gate.drain_router(close(gate),timeout)


@pytest.mark.parametrize('path',['/v1/chat/completions','/v1/messages','/v1/responses'])
@pytest.mark.parametrize('stream',[True,False])
@pytest.mark.parametrize('managed',[True,False])
def test_all_chat_dialects_modes_and_managed_workers_refuse_before_upstream(gate,path,stream,managed):
    calls=[]
    class Backend:
        def generate(self,request): calls.append(request);return iter(['ok'])
    server=make_server('127.0.0.1',0,Backend(),auth_token='synthetic',router_admission=gate,
                       server_config=ServerConfig() if managed else None)
    thread=threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01});thread.start()
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


@pytest.mark.skipif(os.name != 'posix', reason='actual POSIX producer/writer flock fence')
def test_native_external_admin_writers_share_producer_fence(store, monkeypatch):
    import subprocess
    from anvil_serving.router.keys import KeyStore, KeyStoreError, _bind_router_store
    usage, _, _, _ = store
    state = usage.key_store.path.parent / 'external-owner.json'
    def persist(closure):
        state.write_text(json.dumps({'schema':'router-admission/v1', 'owner_id':'owner_fixture', 'closure':closure}))
        state.chmod(0o600)
    persist(None)
    owner = RouterAdmission('external_fixture', persist=persist, owner_scope=lambda:object(), roster_revision='external_roster')
    owner.observe('maintenance', _bind_router_store(usage.key_store.path, state, 'owner_fixture'))
    external = KeyStore(usage.key_store.path)
    # The protected producer binding keeps ordinary admitting administration.
    external.create('admin_fixture', ['llm.primary'], ['/v1/chat/completions'])
    script = """import sys
from anvil_serving.router.keys import KeyStore
store = KeyStore(sys.argv[1])
with store._write() as db:
    db.execute('BEGIN IMMEDIATE')
    print('writer-entered', flush=True)
    sys.stdin.readline()
    db.execute('COMMIT')
"""
    worker = subprocess.Popen([sys.executable, '-c', script, str(external.path)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert worker.stdout.readline().strip() == 'writer-entered'
        token = close(owner)
        expire_drain_deadlines(monkeypatch, 1)
        result = owner.drain_router(token, 1)
        assert not result['drained'] and result['counts']['maintenance'] == 1
        with pytest.raises(ValueError, match='router_drain_required'):
            owner.consume(token)
        with pytest.raises(KeyStoreError, match='quiesced'):
            external.create('closed_fixture', ['llm.primary'], ['/v1/chat/completions'])
        # Ordinary protected-store snapshot reads do not count or deadlock.
        assert external.list_keys()[0]['name'] == 'admin_fixture'
    finally:
        stdout, stderr = worker.communicate('\n', timeout=5)
        assert worker.returncode == 0, stderr
    assert owner.consume(token)['drained']
    # The actual private container pipe also constructs a standalone store.
    result = subprocess.run([sys.executable, '-m', 'anvil_serving.router.key_container'],
                            input=json.dumps({'action':'revoke', 'store_path':str(external.path),
                                              'key_id':external.list_keys()[0]['key_id']}),
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 2 and external.list_keys()[0]['revoked_at'] is None
    assert KeyStore(external.path).list_keys() == external.list_keys()


@pytest.mark.skipif(os.name != 'posix', reason='actual POSIX producer/writer flock fence')
def test_external_writer_missing_or_replaced_native_custody_holds(store, monkeypatch):
    from anvil_serving.router.keys import KeyStore, KeyStoreError, _bind_router_store
    usage, _, _, _ = store
    external = KeyStore(usage.key_store.path)
    state = external.path.parent / 'missing-owner.json'
    owner = RouterAdmission('external_fixture', persist=lambda _:None, owner_scope=lambda:object(), roster_revision='external_roster')
    owner.observe('maintenance', _bind_router_store(external.path, state, 'owner_fixture'))
    with pytest.raises(KeyStoreError):
        external.create('missing_fixture', ['llm.primary'], ['/v1/chat/completions'])
    gate_path = Path(str(external.path) + '.router-writers.lock')
    replacement = gate_path.with_suffix('.replacement')
    replacement.write_bytes(b''); replacement.chmod(0o600); replacement.replace(gate_path)
    token = close(owner)
    expire_drain_deadlines(monkeypatch, 1)
    assert owner.drain_router(token, 1)['unknown'] == ['maintenance']
    with pytest.raises(KeyStoreError):
        external.create('changed_fixture', ['llm.primary'], ['/v1/chat/completions'])
    Path(str(external.path) + '.router-owner').unlink()
    gate_path.write_bytes(b'router-managed-store/v1\n')
    with pytest.raises(KeyStoreError):
        external.create('lost_fixture', ['llm.primary'], ['/v1/chat/completions'])


def test_native_scope_foreign_run_and_unknown_process_hold(store,monkeypatch):
    usage,owner,run,_=store
    monkeypatch.setattr(ledger,'_now',lambda:END)
    assert usage.managed_owner_scope(run,owner,domain_id='domain_fixture',configuration_revision='config_fixture')
    usage.register_run(owner,domain_id='domain_fixture',configuration_revision='config_fixture',enabled=False)
    with pytest.raises(UsageError):usage.managed_owner_scope(run,owner,domain_id='domain_fixture',configuration_revision='config_fixture')


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


def test_retained_media_thread_and_ambiguous_submission_never_become_zero(gate, monkeypatch):
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
        expire_drain_deadlines(monkeypatch, 2)
        result=gate.drain_router(token,1)
        assert not result['drained'] and result['counts']['media']>=2
        jobs.clear();finished.set();worker.stop(timeout=2)
        assert not worker.is_alive
        result=gate.drain_router(token,1)
        assert not result['drained'] and 'media' in result['unknown']
    finally:
        finished.set();worker.stop(timeout=2)
    assert not worker.is_alive


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
