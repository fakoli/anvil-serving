"""Protected real HTTP usage dispatch; all fixtures and credentials are synthetic."""
from contextlib import ExitStack, contextmanager
from datetime import datetime
from dataclasses import replace
import http.client
import json
import threading

import pytest

from anvil_serving.control_plane.authorization import WORKLOADS_READ, INFERENCE_USE, NODE_ADMIN_BOOTSTRAP
from anvil_serving.router.config import ServerConfig
from anvil_serving.router.decision_log import DecisionLog
from anvil_serving.router.front_door import make_server, USAGE_ENDPOINT, USAGE_METRICS_ENDPOINT, _USAGE_READ_ATTEMPTS
from anvil_serving.router.internal import UsageInvocation
from anvil_serving.router.workloads import RouterWorkloadRegistry
from tests.router.helpers import StaticBackend
from tests.router.test_front_door_auth import _scoped_policy
from tests.router.test_usage_lifecycle import store as store, ident, END
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
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True); thread.start()
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


@pytest.mark.parametrize('headers', [{'Content-Length':'1'}, {'Content-Length':'00'}, {'Transfer-Encoding':'chunked'},
    {'x-api-key':ADMIN}, {'Authorization':'Bearer '+('a'*4097)}])
def test_auth_and_bodyless_framing_zero_reads(policy,headers):
    with server(policy,usage_store=object()) as address:
        assert request(address,USAGE_ENDPOINT+'?granularity=cumulative',headers=headers)[0] in {400,403}


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


ACTIVE = USAGE_ENDPOINT + '?view=active'
REFUSED = (503, {'error': {'type': 'accounting_unavailable', 'message': 'accounting unavailable'}})


@contextmanager
def active_read(policy, store, monkeypatch):
    """One dispatched request behind a real server.

    ``reads`` records the registry revision at each ledger health read; each hook in
    ``hooks`` runs inside that read with the read's ordinal.
    """
    usage, _, run, scope = store
    monkeypatch.setattr('anvil_serving.router.usage_store._now', lambda: END)
    registry = RouterWorkloadRegistry(DecisionLog(), clock=CLOCK)
    invocation = UsageInvocation(usage, run, scope, forwarded_caller(), 'chat', 'llm.primary', registry=registry,
                                 clock=CLOCK, gateway_request_id='req_' + ident().replace('-', ''))
    invocation.dispatch()
    reads, hooks, health = [], [], usage.health

    def counted(domain):
        reads.append(registry.usage_snapshot()[0])
        for during in hooks:
            during(len(reads))
        return health(domain)

    monkeypatch.setattr(usage, 'health', counted)
    with server(policy, usage_store=usage, usage_authority=lambda: scope, workload_registry=registry) as address:
        def read():
            status, _, raw = request(address, ACTIVE)
            return status, json.loads(raw)
        yield registry, invocation, reads, hooks, read


def test_active_read_is_unchanged_when_nothing_changes(policy, store, monkeypatch):
    with active_read(policy, store, monkeypatch) as (registry, _, reads, _, read):
        revision = registry.usage_snapshot()[0]
        status, page = read()
        assert status == 200 and reads == [revision]
        assert page['registry_revision'] == revision == registry.usage_snapshot()[0]
        assert [record['phase'] for record in page['records']] == ['dispatched']


def test_active_read_is_not_refused_by_delivery_poll_samples(policy, store, monkeypatch):
    with active_read(policy, store, monkeypatch) as (registry, invocation, reads, hooks, read):
        revision = registry.usage_snapshot()[0]
        hooks.append(lambda count: registry.observe_request(
            invocation.registry_id, {}, 'llm.primary', {'phase': 'streaming', 'last_activity_ms': 5}))
        status, page = read()
        assert status == 200 and reads == [revision] and page['registry_revision'] == revision
        # The page is the snapshot taken before the sample; the next read shows the sample.
        assert page['records'][0]['phase'] == 'dispatched'
        assert read()[1]['records'][0]['phase'] == 'streaming'


def test_active_read_retries_until_the_registry_is_stable(policy, store, monkeypatch):
    with active_read(policy, store, monkeypatch) as (registry, invocation, reads, hooks, read):
        revision = registry.usage_snapshot()[0]
        hooks.append(lambda count: count < 3 and registry.observe_usage(invocation.registry_id, phase='streaming'))
        status, page = read()
        assert status == 200 and reads == [revision, revision + 1, revision + 2]
        # The reported revision is the one that held across the last health read.
        assert page['registry_revision'] == revision + 2 == registry.usage_snapshot()[0]


def test_active_read_gives_up_when_the_registry_never_settles(policy, store, monkeypatch):
    with active_read(policy, store, monkeypatch) as (registry, invocation, reads, hooks, read):
        hooks.append(lambda count: registry.observe_usage(invocation.registry_id, phase='streaming'))
        assert read() == REFUSED and len(reads) == _USAGE_READ_ATTEMPTS


def test_active_read_never_reads_the_ledger_during_a_ledger_write(policy, store, monkeypatch):
    with active_read(policy, store, monkeypatch) as (registry, _, reads, hooks, read):
        with registry.usage_mutation():
            assert read() == REFUSED and reads == []
        # A write that begins during the health read discards that attempt; the next one
        # starts after the write has finished and reports the revision that follows it.
        snapshot, writes = registry.usage_snapshot, ExitStack()

        def write_ends_when_seen():
            try:
                return snapshot()
            except ValueError:
                writes.close()
                raise

        monkeypatch.setattr(registry, 'usage_snapshot', write_ends_when_seen)
        hooks.append(lambda count: count == 1 and writes.enter_context(registry.usage_mutation()))
        revision = snapshot()[0]
        status, page = read()
        assert status == 200 and reads == [revision, revision + 2] and page['registry_revision'] == revision + 2


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
