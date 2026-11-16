"""Protected real HTTP usage dispatch; all fixtures and credentials are synthetic."""
from contextlib import contextmanager
from datetime import datetime
from dataclasses import replace
import http.client
import json
import threading

import pytest

from anvil_serving.control_plane.authorization import WORKLOADS_READ, INFERENCE_USE, NODE_ADMIN_BOOTSTRAP
from anvil_serving.router.config import ServerConfig
from anvil_serving.router.decision_log import DecisionLog
from anvil_serving.router.front_door import make_server, USAGE_ENDPOINT, USAGE_METRICS_ENDPOINT
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
