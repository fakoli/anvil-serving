"""Protected persisted exporter checks; synthetic fixtures are not live proof."""

from dataclasses import replace
from datetime import datetime

import pytest

from anvil_serving.router import usage_store as ledger, router_telemetry as telemetry
from anvil_serving.router.decision_log import DecisionLog
from anvil_serving.router.identity import legacy_caller
from anvil_serving.router.usage_store import RequestStart, UsageError, UsageQuery
from anvil_serving.router.workloads import RouterWorkloadRegistry
from anvil_serving.router.internal import UsageInvocation
from tests.router.test_usage_lifecycle import store as store, ident, terminal, tokens, AT, END, ROUTE
from tests.router.test_usage_retention import connect_caller, forwarded_caller
from tests.router.test_usage_admin import policy as policy, server, request, LEGACY
from tests.router.key_fixtures import tmp_path as tmp_path

NOW = datetime.fromisoformat(END.replace('Z', '+00:00'))
DOMAIN = 'domain_fixture'


def registry(**kw):
    return RouterWorkloadRegistry(DecisionLog(), clock=lambda: NOW, **kw)


def put(store, *, model='llm.primary', caller=None, usage=None, latency=1000, route=ROUTE):
    db, _, run, scope = store
    usage = usage or tokens()
    start = RequestStart(ident(), run, AT, caller or legacy_caller(), 'chat', model, attempt_id=ident(),
                         input_applicability=usage.input.applicability, output_applicability=usage.output.applicability)
    db.start(start, authority_scope=scope)
    final = terminal(start, tokens=usage, latency_ms=latency, route=route)
    db.finalize(final)
    return start, final


def snapshot(store, active=None, **kw):
    return telemetry.collect_usage_snapshot(store[0], active or registry(), NOW,
        domain_id=DOMAIN, authority_scope=store[3], **kw)


def render(store, active=None, **kw):
    return telemetry.render_usage_prometheus(snapshot(store, active, **kw))


def samples(raw):
    return [line for line in raw.decode().splitlines() if line and not line.startswith('#')]


def global_value(raw, name):
    return next(line.split(' ')[-1] for line in samples(raw) if line.startswith('anvil_router_usage_'+name+' '))


def metric(raw, name):
    return [line for line in samples(raw) if line.startswith('anvil_router_usage_'+name+'{')]


def test_route_differences_never_duplicate_fixed_series_and_grant_versions_stay_distinct(store):
    put(store, caller=connect_caller(), route=ROUTE)
    put(store, caller=connect_caller(), route=replace(ROUTE, backend_id='backend_other'))
    put(store, caller=connect_caller('2','c'*64,2))
    raw = render(store)
    assert global_value(raw, 'represented_groups') == '2'
    assert sorted(int(line.rsplit(' ',1)[1]) for line in metric(raw,'requests_total')) == [1,2]
    identities = [line.rsplit(' ',1)[0] for line in samples(raw)]
    assert len(identities) == len(set(identities))
    assert all(set(part.split('=')[0] for part in line.split('{',1)[1].split('}',1)[0].split(',') if '=' in part) >= set(ledger._USAGE_LABELS)
               for line in metric(raw,'requests_total'))
    assert 'backend_fixture' not in raw.decode() and 'backend_other' not in raw.decode()


def test_real_owned_active_finalizing_fence_and_unresolved_is_not_active(store, monkeypatch):
    db,_,run,scope=store
    active=registry()
    monkeypatch.setattr(ledger,'_now',lambda:END)
    pending=RequestStart(ident(),run,AT,legacy_caller(),'chat','llm.primary')
    db.start(pending,authority_scope=scope)
    assert not metric(render(store,active),'active_requests')
    invocation=UsageInvocation(store=db,run_id=run,scope=scope,caller=legacy_caller(),kind='chat',model='llm.primary',
                               registry=active,clock=lambda:NOW)
    raw=render(store,active)
    assert metric(raw,'active_requests')[0].endswith(' 1')
    assert 'outcome="active"' in raw.decode() and not metric(raw,'tokens_total')
    invocation.dispatch()
    class Backend:
        def get_last_normalized_usage(self): return tokens()
    invocation.capture(Backend(),'success')
    assert metric(render(store,active),'active_requests')[0].endswith(' 1')
    with active.usage_mutation():
        with pytest.raises(UsageError,match='accounting_unavailable'): render(store,active)
    original=db._usage_metrics_snapshot
    def changing(**kw):
        result=original(**kw)
        active.observe_usage(invocation.registry_id,phase='finalizing')
        return result
    monkeypatch.setattr(db,'_usage_metrics_snapshot',changing)
    with pytest.raises(UsageError,match='accounting_unavailable'): render(store,active)
    monkeypatch.setattr(db,'_usage_metrics_snapshot',original)
    invocation.finish('success')
    raw=render(store,active)
    assert all(line.endswith(' 0') for line in metric(raw,'active_requests'))
    assert global_value(raw,'unresolved_requests') == '1'


def test_257_groups_series_budget_exact_omissions_and_ledger_access(store):
    for i in range(257): put(store,model=f'llm.fixture_{i:03}')
    raw=render(store)
    represented=int(global_value(raw,'represented_groups'))
    assert len(samples(raw)) == 21*represented+20 <= 5396
    assert 1 <= represented <= 256 and int(global_value(raw,'omitted_groups')) == 257-represented
    assert global_value(raw,'export_complete') == '0' and int(global_value(raw,'export_bytes')) == len(raw)
    assert 'fixture_256' not in raw.decode()
    native=store[0].query(UsageQuery(granularity='cumulative',group_by=('model',),limit=500),domain_id=DOMAIN)
    assert len(native['groups']) == 257 and native['requests'] == 257
    small=render(store,series_budget=41)
    assert len(samples(small)) == 41 and global_value(small,'omitted_groups') == '256'


def test_utf8_escape_byte_cap_whole_histograms_and_determinism(store):
    caller=forwarded_caller()
    caller=replace(caller,end_user=replace(caller.end_user,subject='😀'*30+'"\\'))
    for i in range(256): put(store,caller=caller,model=('😀'*40)+f'{i:03}')
    raw=render(store)
    assert raw==render(store)
    represented=int(global_value(raw,'represented_groups'))
    assert 0 < represented < 256 and len(raw)<=2*1024*1024
    assert int(global_value(raw,'omitted_groups'))==256-represented
    assert len(metric(raw,'latency_seconds_bucket'))==represented*7
    assert len(metric(raw,'latency_seconds_count'))==represented
    assert int(global_value(raw,'export_bytes'))==len(raw)
    assert '\\"' in raw.decode() and '\\\\' in raw.decode()


def test_limits_deadline_locked_store_and_bad_histogram_fail_closed(store,monkeypatch):
    put(store)
    monkeypatch.setattr(ledger,'_QUERY_SCAN_LIMIT',0)
    with pytest.raises(UsageError,match='accounting_unavailable'):render(store)
    monkeypatch.setattr(ledger,'_QUERY_SCAN_LIMIT',10000)
    with store[0].key_store._connect() as conn:
        conn.execute('UPDATE usage_cumulative SET latency_le_inf=2')
    with pytest.raises(UsageError,match='accounting_unavailable'):render(store)


def test_scrape_byte_boundary_unavailable_and_denial_before_native_reducer(store,policy,monkeypatch):
    calls=[]
    original=store[0]._usage_metrics_snapshot
    def spy(**kw): calls.append('read'); return original(**kw)
    monkeypatch.setattr(store[0],'_usage_metrics_snapshot',spy)
    with server(policy,usage_store=store[0],usage_domain_id=DOMAIN,usage_authority=lambda:store[3],workload_registry=registry()) as address:
        for token in (None,LEGACY,'synthetic-infer-token','synthetic-node-token','profile-admin'):
            assert request(address,'/v1/admin/usage/metrics',token=token)[0]==403
        assert request(address,'/v1/admin/usage/metrics?x=1')[0]==400
        assert request(address,'/v1/admin/usage/metrics',method='POST')[0]==405
        assert not calls
        store[0]._pending_failure=True
        assert request(address,'/v1/admin/usage/metrics')[0]==503
    with server(policy,usage_metrics=lambda:b'x'*(2*1024*1024+1)) as address:
        assert request(address,'/v1/admin/usage/metrics')[0]==500


def test_read_deadline_and_locked_store_fail_closed(store,monkeypatch):
    put(store)
    clock=iter([0,0,10])
    monkeypatch.setattr(ledger.time,'monotonic',lambda:next(clock,10))
    with pytest.raises(UsageError,match='accounting_unavailable'):render(store)
    monkeypatch.undo()
    with store[0].key_store._connect() as conn:
        conn.execute('BEGIN EXCLUSIVE')
        with pytest.raises(UsageError,match='accounting_unavailable'):render(store)
        conn.execute('ROLLBACK')
