"""Protected real HTTP usage dispatch; all fixtures and credentials are synthetic."""
from contextlib import contextmanager
from datetime import datetime
from dataclasses import replace
import http.client
import json
import socket
import threading
import time
from urllib.parse import urlencode

import pytest

from anvil_serving.control_plane.authorization import WORKLOADS_READ, INFERENCE_USE, NODE_ADMIN_BOOTSTRAP
from anvil_serving.router.config import ServerConfig
from anvil_serving.router.decision_log import DecisionLog
from anvil_serving.router.front_door import make_server, USAGE_ENDPOINT, USAGE_METRICS_ENDPOINT
from anvil_serving.router.internal import UsageInvocation
from anvil_serving.router.usage_store import UsageError
from anvil_serving.router.workloads import RouterWorkloadRegistry
from tests.router.helpers import StaticBackend
from tests.router.test_front_door_auth import _scoped_policy
from tests.router.test_usage_lifecycle import store as store, ident, terminal, tokens, AT, END
from tests.router.test_usage_retention import forwarded_caller
from tests.router.key_fixtures import tmp_path as tmp_path

ADMIN = 'synthetic-admin-token'
LEGACY = 'synthetic-legacy-token'
def CLOCK():
    return datetime.fromisoformat(END.replace('Z', '+00:00'))


@pytest.fixture
def policy(tmp_path):
    return _scoped_policy(tmp_path, [dict(id=name, scopes=[scope], credential_env='TEST_'+name.upper(), token=token)
        for name, scope, token in [('admin', WORKLOADS_READ, ADMIN), ('infer', INFERENCE_USE, 'synthetic-infer-token'),
                                   ('node', NODE_ADMIN_BOOTSTRAP, 'synthetic-node-token')]])


@contextmanager
def server(policy, **options):
    httpd = make_server('127.0.0.1', 0, options.pop('backend', StaticBackend(['ok'])), authorization_policy=policy,
                        auth_token=options.pop('auth_token', LEGACY), workload_clock=CLOCK, **options)
    # This fixture owns every handler: accept-loop completion alone is insufficient.
    httpd.daemon_threads = False
    httpd.block_on_close = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
    try:
        yield httpd.server_address
    finally:
        httpd.shutdown(); httpd.server_close(); thread.join(5)
        assert not thread.is_alive()


def request(address, path=USAGE_ENDPOINT, *, token=ADMIN, method='GET', headers=None):
    conn = http.client.HTTPConnection(*address, timeout=5)
    try:
        h = {} if token is None else {'Authorization': 'Bearer '+token}
        h.update(headers or {})
        conn.request(method, path, headers=h)
        response = conn.getresponse(); raw = response.read()
        assert response.getheader('Cache-Control') == 'no-store'
        assert response.getheader('Content-Length') == str(len(raw))
        return response.status, dict(response.getheaders()), raw
    finally:
        conn.close()


@pytest.mark.parametrize('path', [USAGE_ENDPOINT+'?granularity=cumulative', USAGE_ENDPOINT+'?view=active', USAGE_METRICS_ENDPOINT])
@pytest.mark.parametrize('token', [None, LEGACY, 'wrong', 'synthetic-infer-token', 'synthetic-node-token', 'eyJprofile.admin'])
def test_denied_before_every_collection(policy, path, token):
    calls=[]
    class Spy:
        def query(self,*a,**k): calls.append('query'); raise AssertionError()
        def health(self,*a,**k): calls.append('health'); raise AssertionError()
        def usage_snapshot(self): calls.append('active'); raise AssertionError()
    with server(policy, usage_store=Spy(), workload_registry=Spy(), usage_metrics=lambda: calls.append('metrics')) as address:
        status, _, raw = request(address, path, token=token, headers={'X-User-Role':'admin','X-Client-Id':ADMIN})
        assert status == 403
        if token:
            assert token not in raw.decode()
    assert not calls


@pytest.mark.parametrize('suffix', ['?view=unknown','?view=active&group_by=%5B%5D','?view=active&from_utc=x',
    '?view=active&require_complete=false','?view=active&limit=201','?view=active&view=active',
    '?granularity=cumulative&limit=true','?granularity=cumulative&require_complete=1',
    '?granularity=cumulative&unknown=x','?granularity=cumulative&filters=%5B%5B%22actor_id%22%2C%22a%22%5D%2C%5B%22actor_id%22%2C%22b%22%5D%5D',
    '?granularity=cumulative&filters=%7B%7D','?granularity=cumulative&group_by=%5B%22model%22%2C%22model%22%5D',
    '?granularity=cumulative&filters=%5B%5B%22input_partial%22%2C%221%22%5D%5D', '?x=%ZZ','?x=%FF', '?x='+('a'*8192)])
def test_invalid_query_zero_collectors(policy, suffix):
    calls=[]
    with server(policy, usage_store=object(), usage_authority=lambda:calls.append('authority')) as address:
        status, _, raw = request(address, USAGE_ENDPOINT+suffix)
        assert status==400 and b'error' in raw
    assert not calls


@pytest.mark.parametrize('method', ['POST','PUT','DELETE','HEAD','OPTIONS','PATCH'])
def test_methods_are_fixed_and_private(policy, method):
    with server(policy) as address:
        conn=http.client.HTTPConnection(*address,timeout=5)
        conn.request(method, USAGE_ENDPOINT+'?private_sentinel=1', headers={'Authorization':'Bearer '+ADMIN})
        r=conn.getresponse(); body=r.read(); conn.close()
        assert r.status==405 and r.getheader('Allow')=='GET' and r.getheader('Cache-Control')=='no-store'
        assert b'private_sentinel' not in body


@pytest.mark.parametrize('path', [USAGE_ENDPOINT+'/',USAGE_ENDPOINT+'/suffix',USAGE_METRICS_ENDPOINT+'/', '/v1/admin/%75sage', '//v1/admin/usage'])
def test_exact_paths_only(policy,path):
    with server(policy) as address:
        assert request(address,path)[0]==404


@pytest.mark.parametrize('headers', [{'Content-Length':'1'}, {'Content-Length':'00'}, {'Transfer-Encoding':'chunked'},
    {'x-api-key':ADMIN}, {'Authorization':'Bearer '+('a'*4097)}])
def test_auth_and_bodyless_framing_zero_reads(policy,headers):
    with server(policy,usage_store=object()) as address:
        assert request(address,USAGE_ENDPOINT+'?granularity=cumulative',headers=headers)[0] in {400,403}


def test_real_retained_query_roundtrip_and_typed_coverage(policy,store,monkeypatch):
    usage,_,run,scope=store
    from anvil_serving.router.usage_store import RequestStart
    start=RequestStart(ident(),run,AT,forwarded_caller(),'chat','llm.primary')
    usage.start(start,authority_scope=scope); usage.finalize(terminal(start,tokens=tokens()))
    options=dict(usage_store=usage,usage_domain_id=scope.domain_id)
    for granularity in ['detail','daily','cumulative']:
        fields={'granularity':granularity,'filters':json.dumps([['end_user_subject','subject_fixture']])}
        if granularity!='cumulative':
            fields.update(from_utc='2026-10-07T00:00:00Z',to_utc='2026-10-08T00:00:00Z')
        with server(policy,**options) as address:
            status,_,raw=request(address,USAGE_ENDPOINT+'?'+urlencode(fields))
            assert status==200
            result=json.loads(raw)
            assert result['measured_input']==7 and result['measured_output']==5
            assert not result['coverage_complete'] and result['coverage_gaps']
            assert result['granularity']==granularity
            if granularity=='detail':
                assert result['records'][0]['caller']==start.caller.to_dict()
            fields['require_complete']='true'
            status,_,raw=request(address,USAGE_ENDPOINT+'?'+urlencode(fields))
            assert status==422 and json.loads(raw)['error']['type']=='usage_coverage_unavailable'
            assert 'owner_roster_unknown' in json.loads(raw)['coverage']['gap_reasons']


def test_active_same_registry_coherence_finalizing_cleanup_and_generic_privacy(policy,store,monkeypatch):
    usage,_,run,scope=store
    monkeypatch.setattr('anvil_serving.router.usage_store._now', lambda: END)
    registry=RouterWorkloadRegistry(DecisionLog(),clock=CLOCK)
    invocation=UsageInvocation(usage,run,scope,forwarded_caller(),'chat','llm.primary',registry=registry,clock=CLOCK,
                               gateway_request_id='req_'+ident().replace('-',''))
    invocation.dispatch(); invocation.capture(StaticBackend(['ok']),'success')
    with server(policy,usage_store=usage,usage_authority=lambda:scope,workload_registry=registry) as address:
        status,_,raw=request(address,USAGE_ENDPOINT+'?view=active')
        assert status==200
        data=json.loads(raw); [record]=data['records']
        assert record['caller']==invocation.start.caller.to_dict() and record['phase']=='finalizing'
        assert data['freshness']=='fresh' and data['accounting_health']['unresolved_requests']==1
        assert 'caller' not in json.dumps(registry.active_requests())
        assert 'subject_fixture' not in json.dumps(registry.active_requests())
        from anvil_serving.observability.workloads import source_result_to_json, parse_workload_query
        generic = source_result_to_json(registry.source_result('synthetic', parse_workload_query({}), CLOCK()))
        assert 'subject_fixture' not in generic and 'caller' not in generic
        original=usage.health
        monkeypatch.setattr(usage,'health',lambda domain:(registry.observe_usage(invocation.registry_id,phase='streaming'),original(domain))[1])
        assert request(address,USAGE_ENDPOINT+'?view=active')[0]==503
        monkeypatch.setattr(usage,'health',original)
        with registry.usage_mutation():
            assert request(address,USAGE_ENDPOINT+'?view=active')[0]==503
        invocation.finish('success'); invocation.finish('success')
        assert request(address,USAGE_ENDPOINT+'?view=active')[0]==200
        assert registry.active_count==0


def test_metrics_after_auth_no_queries_and_unavailable(policy):
    calls=[]
    with server(policy,usage_metrics=lambda: calls.append(True) or b'synthetic_metric 1\n') as address:
        for query in ['?x=1','?','?x=1&x=1']:
            assert request(address,USAGE_METRICS_ENDPOINT+query)[0]==400
        status,headers,raw=request(address,USAGE_METRICS_ENDPOINT)
        assert status==200 and headers['Content-Type']=='text/plain; version=0.0.4' and raw==b'synthetic_metric 1\n'
    assert calls==[True]
    with server(policy) as address:
        assert request(address,USAGE_METRICS_ENDPOINT)[0]==503


def test_device_key_cannot_grant_admin(policy,store):
    usage,*_=store
    _,secret=usage.key_store.create('synthetic',['llm.primary'],['/v1/chat/completions'])
    config=ServerConfig(api_keys_path=str(usage.key_store.path))
    with server(policy,server_config=config,usage_store=object()) as address:
        assert request(address,USAGE_ENDPOINT+'?view=active',token=secret)[0]==403
        assert request(address,USAGE_ENDPOINT+'?view=active',token=None)[0]==401


def test_keepalive_reauthorizes_and_no_policy_fails_closed(policy):
    with server(policy,usage_metrics=lambda:b'metric 1\n') as address:
        c=http.client.HTTPConnection(*address,timeout=5)
        c.request('GET',USAGE_METRICS_ENDPOINT,headers={'Authorization':'Bearer '+ADMIN})
        r=c.getresponse();assert r.status==200;r.read()
        c.request('GET',USAGE_METRICS_ENDPOINT,headers={'Authorization':'Bearer '+LEGACY})
        r=c.getresponse();assert r.status==403;r.read();c.close()
    for absent in [None,replace(policy)]:
        with server(absent,auth_token=None) as address:
            assert request(address,USAGE_METRICS_ENDPOINT)[0]==403


def test_oversized_headers_and_stdlib_request_error_private(policy):
    with server(policy) as address:
        for raw in [b'GET /v1/admin/usage?private_sentinel=1 HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer '+ADMIN.encode()+b'\r\nX-Large: '+b'a'*16384+b'\r\n\r\n',
                    b'GET /v1/admin/usage?private_sentinel='+b'x'*66000+b' HTTP/1.1\r\n\r\n']:
            with socket.create_connection(address,timeout=5) as sock:
                sock.sendall(raw); response=b''
                while True:
                    part=sock.recv(65536)
                    if not part:break
                    response+=part
            assert b'431' in response or b'414' in response
            assert b'no-store' in response and b'private_sentinel' not in response


def test_active_bounds_filters_provenance_and_omissions(policy,store,monkeypatch):
    usage,_,run,scope=store
    monkeypatch.setattr('anvil_serving.router.usage_store._now',lambda:END)
    registry=RouterWorkloadRegistry(DecisionLog(),clock=CLOCK,max_active=1)
    inv=UsageInvocation(usage,run,scope,forwarded_caller(),'chat','llm.primary',registry=registry,clock=CLOCK)
    unrepresented=registry.begin('req_'+ident().replace('-','')); unrepresented.activate()
    registry.observe_request(inv.registry_id, {'session_id':'untrusted-session'},'llm.primary',{'phase':'queued'})
    with server(policy,usage_store=usage,usage_authority=lambda:scope,workload_registry=registry) as address:
        status,_,raw=request(address,USAGE_ENDPOINT+'?'+urlencode({'view':'active','filters':json.dumps([['actor_kind','service']])}))
        data=json.loads(raw);assert status==200 and data['truncation']['omitted'] is None
        assert data['truncation']['unrepresented']==1 and data['truncation']['truncated']
        [record]=data['records'];assert record['phase']=='queued'
        assert record['tokens']['input']['source']=='unknown'
        assert b'untrusted-session' not in raw
        status,_,raw=request(address,USAGE_ENDPOINT+'?'+urlencode({'view':'active','filters':json.dumps([['end_user_subject',None]])}))
        assert status==200 and json.loads(raw)['records']==[]
    inv.finish('error');unrepresented.finish();assert registry.active_count==0


@pytest.mark.parametrize('code,status', [('usage_cursor_invalid',422),('usage_cursor_stale',422),
    ('usage_query_limited',422),('usage_granularity_unsupported',422),('accounting_unavailable',503)])
def test_native_error_mapping_never_echoes_arbitrary_details(policy,code,status):
    class Store:
        def query(self,*args,**options):
            raise UsageError(code, {'secret':'private_sentinel','coverage_gaps':[{'reason':'private_sentinel'}],
                                   'requested_range':{'from_utc':'private_sentinel','to_utc':None}})
    with server(policy,usage_store=Store(),usage_domain_id='domain_fixture') as address:
        actual,_,raw=request(address,USAGE_ENDPOINT+'?granularity=cumulative')
        assert actual==status and b'private_sentinel' not in raw


def test_duplicate_headers_oversize_and_callback_failure_release_capacity(policy):
    with server(policy,usage_metrics=lambda:(_ for _ in ()).throw(RuntimeError('private_sentinel'))) as address:
        c=http.client.HTTPConnection(*address,timeout=5)
        c.putrequest('GET',USAGE_METRICS_ENDPOINT);c.putheader('Authorization','Bearer '+ADMIN)
        c.putheader('Authorization','Bearer '+ADMIN);c.endheaders()
        r=c.getresponse();assert r.status==403;r.read();c.close()
        status,_,raw=request(address,USAGE_METRICS_ENDPOINT)
        assert status==500 and b'private_sentinel' not in raw
    with server(policy,usage_metrics=lambda:b'a'*(8*1024*1024+1)) as address:
        assert request(address,USAGE_METRICS_ENDPOINT)[0]==500
    with server(policy,usage_metrics=lambda:b'metric 1\n') as address:
        assert request(address,USAGE_METRICS_ENDPOINT)[0]==200


def test_actual_frontdoor_request_visible_then_owned_cleanup(policy,store,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    usage,_,run,scope=store
    monkeypatch.setattr('anvil_serving.router.usage_store._now',lambda:END)
    entered=threading.Event();resume=threading.Event()
    class BlockingBackend(StaticBackend):
        def generate(self,request):
            entered.set();assert resume.wait(5)
            yield from super().generate(request)
    registry=RouterWorkloadRegistry(DecisionLog(),clock=CLOCK)
    with server(policy,backend=BlockingBackend(['ok']),server_config=ServerConfig(),usage_store=usage,
                usage_run_id=run,usage_authority=lambda:scope,workload_registry=registry) as address:
        def post():
            c=http.client.HTTPConnection(*address,timeout=5)
            try:
                c.request('POST','/v1/chat/completions',json.dumps({'model':'llm.primary',
                    'messages':[{'role':'user','content':'synthetic content sentinel'}],'stream':False}),
                    {'Authorization':'Bearer synthetic-infer-token','Content-Type':'application/json'})
                r=c.getresponse();r.read();return r.status
            finally:c.close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(post)
            try:
                assert entered.wait(5)
                status,_,raw=request(address,USAGE_ENDPOINT+'?view=active')
                assert status==200 and b'synthetic content sentinel' not in raw
                [record]=json.loads(raw)['records']
                assert record['caller']['actor']['id']=='infer'
            finally:resume.set()
            assert future.result(5)==200
        deadline=time.monotonic()+5
        while registry.active_count and time.monotonic()<deadline:
            time.sleep(.01)
        assert registry.active_count==0
        assert json.loads(request(address,USAGE_ENDPOINT+'?view=active')[2])['records']==[]
        fields={'granularity':'detail','from_utc':AT,'to_utc':'2026-10-07T13:00:00Z'}
        status,_,raw=request(address,USAGE_ENDPOINT+'?'+urlencode(fields))
        assert status==200 and json.loads(raw)['records'][0]['caller']==record['caller']


def test_owned_stream_activity_ages_between_samples_without_poll_or_phase_reset(store):
    from datetime import timedelta
    from anvil_serving.router.request_control import RequestControl
    usage,_,run,scope=store
    clock=[datetime.fromisoformat(AT.replace('Z','+00:00'))]
    monotonic=[0.0]
    registry=RouterWorkloadRegistry(DecisionLog(),clock=lambda:clock[0])
    inv=UsageInvocation(usage,run,scope,forwarded_caller(),'chat','llm.primary',registry=registry,clock=lambda:clock[0])
    inv.dispatch()
    control=RequestControl(clock=lambda:monotonic[0],idle_timeout_s=120,total_timeout_s=600)
    def advance(seconds):
        monotonic[0]+=seconds;clock[0]+=timedelta(seconds=seconds)
    def poll():
        # The common delivery loop passes the OWNED RequestControl snapshot.
        registry.observe_request(inv.registry_id,{},'llm.primary',control.snapshot())
    def age():
        _,entries,omitted=registry.usage_snapshot()
        return registry.active_usage_page(entries,omitted,clock[0])['records'][0]['last_activity_ms']
    assert age() is None
    advance(60);poll();assert age() is None  # No upstream activity yet.
    control.note_activity('streaming');advance(.005);poll()
    assert age()==control.snapshot()['last_activity_ms']==5  # Not the60s phase age.
    from anvil_serving.observability.workloads import WorkloadState
    assert inv.token.advance(WorkloadState.ADMITTED) and age()==5
    advance(10);assert age()==10005  # Stopped sampling still ages.
    advance(20);poll();activity_age=control.snapshot()['last_activity_ms']
    assert age()==activity_age and activity_age>=30000
    poll();assert age()==activity_age  # Polling does not fake upstream activity.
    registry.observe_usage(inv.registry_id,phase='finalizing')
    assert age()==activity_age  # Changing phase cannot reset stream inactivity.
    advance(5);assert age()==activity_age+5000
    control.note_activity('streaming');poll();assert age()==0
    clock[0]-=timedelta(seconds=1);assert age() is None  # Clock rewind is unknown.
    inv.finish('error')


@pytest.mark.parametrize('value',[None,True,-1,10**100,'5'])
def test_missing_or_invalid_owned_activity_is_unknown(store,value):
    usage,_,run,scope=store
    def clock():
        return datetime.fromisoformat(AT.replace('Z','+00:00'))
    registry=RouterWorkloadRegistry(DecisionLog(),clock=clock)
    inv=UsageInvocation(usage,run,scope,forwarded_caller(),'chat','llm.primary',registry=registry,clock=clock)
    registry.observe_request(inv.registry_id,{},'llm.primary',{'phase':'streaming','last_activity_ms':value})
    _,entries,omitted=registry.usage_snapshot()
    assert registry.active_usage_page(entries,omitted,clock())['records'][0]['last_activity_ms'] is None
    inv.finish('error')


def test_activity_sample_clock_and_count_bounds(store):
    from datetime import timedelta
    from anvil_serving.observability.workloads import MAX_COUNT
    usage,_,run,scope=store
    clock=[datetime.fromisoformat(AT.replace('Z','+00:00'))]
    registry=RouterWorkloadRegistry(DecisionLog(),clock=lambda:clock[0])
    inv=UsageInvocation(usage,run,scope,forwarded_caller(),'chat','llm.primary',registry=registry,clock=lambda:clock[0])
    registry.observe_request(inv.registry_id,{},'llm.primary',{'last_activity_ms':MAX_COUNT})
    clock[0]+=timedelta(seconds=2)
    _,entries,omitted=registry.usage_snapshot()
    assert registry.active_usage_page(entries,omitted,clock[0])['records'][0]['last_activity_ms']==MAX_COUNT
    registry._clock=lambda:None
    registry.observe_request(inv.registry_id,{},'llm.primary',{'last_activity_ms':5})
    _,entries,omitted=registry.usage_snapshot()
    assert registry.active_usage_page(entries,omitted,clock[0])['records'][0]['last_activity_ms'] is None
    registry._clock=lambda:clock[0];inv.finish('error')


def test_server_fixture_waits_for_owned_handler_database_release(policy, store, monkeypatch):
    usage = store[0]
    entered, release, finished = (threading.Event() for _ in range(3))
    accept_stopped, closed = threading.Event(), threading.Event()
    actual_make_server = make_server
    errors = []
    def tracked_server(*args, **kwargs):
        httpd = actual_make_server(*args, **kwargs)
        original_finish = httpd.RequestHandlerClass.finish
        original_shutdown = httpd.shutdown
        def delayed_finish(handler):
            try:
                with usage.key_store._connect():
                    entered.set()
                    assert release.wait(5), 'owned handler release timed out'
                    original_finish(handler)
            finally:
                finished.set()
        def shutdown():
            original_shutdown()
            accept_stopped.set()
        monkeypatch.setattr(httpd.RequestHandlerClass, 'finish', delayed_finish)
        httpd.shutdown = shutdown
        return httpd
    monkeypatch.setattr(__name__ + '.make_server', tracked_server)
    context = server(policy, usage_metrics=lambda: b'synthetic_metric 1\n')
    address = context.__enter__()
    def close():
        try:
            context.__exit__(None, None, None)
        except BaseException as error:
            errors.append(error)
        finally:
            closed.set()
    closer = None
    try:
        assert request(address, USAGE_METRICS_ENDPOINT)[0] == 200
        assert entered.wait(5)
        closer = threading.Thread(target=close)
        closer.start()
        assert accept_stopped.wait(5)
        assert not closed.wait(0.05), 'fixture exited while its handler held the database'
    finally:
        release.set()
        if closer is None:
            context.__exit__(None, None, None)
        else:
            closer.join(5)
    assert finished.is_set() and closed.is_set()
    assert closer is not None and not closer.is_alive()
    assert not errors
