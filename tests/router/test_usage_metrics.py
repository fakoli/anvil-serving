"""Protected persisted exporter checks; synthetic fixtures are not live proof."""
from dataclasses import replace
from datetime import datetime
import sqlite3

import pytest

from anvil_serving.router import usage_store as ledger, router_telemetry as telemetry
from anvil_serving.router.decision_log import DecisionLog, TokenDirection, TokenUsage
from anvil_serving.router.identity import legacy_caller
from anvil_serving.router.keys import KeyStore
from anvil_serving.router.usage_store import RequestStart, UsageError, UsageQuery, UsageStore
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


def test_committed_histogram_replay_restart_prune_and_exact_decimal(store):
    db = store[0]
    start, final = put(store, usage=tokens(7, 0, cache_read_input_tokens=3, reasoning_output_tokens=0))
    assert db.finalize(final) == 'same'
    with db.key_store._connect() as conn:
        conn.execute('UPDATE usage_cumulative SET measured_input=?',(2**53+1,))
    before = render(store)
    assert str(2**53+1) in metric(before, 'tokens_total')[0]
    assert metric(before, 'tokens_total')[1].endswith(' 0')
    assert global_value(before, 'float_precision_safe') == '0'
    assert len(samples(before)) == 41
    assert global_value(before, 'coverage_epoch_timestamp_seconds') == str(datetime.fromisoformat(AT.replace('Z','+00:00')).timestamp())
    assert metric(before, 'latency_seconds_sum')[0].endswith(' 1')
    assert metric(before, 'latency_seconds_count')[0].endswith(' 1')
    assert 'cache' not in before.decode() and 'reasoning' not in before.decode()
    db.prune('2028-10-07T12:00:00Z', domain_id=DOMAIN)
    reopened = (UsageStore(KeyStore(db.key_store.path)), *store[1:])
    after = render(reopened)
    for family in ('requests_total','attempts_total','tokens_total','latency_seconds_bucket','latency_seconds_sum','latency_seconds_count'):
        assert metric(before, family) == metric(after, family)
    assert global_value(after, 'retained_detail_start_timestamp_seconds') != 'NaN'
    assert db.query(UsageQuery(granularity='cumulative'), domain_id=DOMAIN)['measured_input'] == 2**53+1


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


def test_fixed_projection_reducer_sums_distinct_retained_rows_before_selection(store):
    db = store[0]
    put(store)
    # Synthetic duplicate contributions exercise reduction independently of the
    # current writer's one-row canonical key; no production store is touched.
    with db.key_store._connect() as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute('SELECT * FROM usage_cumulative').fetchone())
        row['group_key'] = 'synthetic_distinct_retained_key'
        names = list(row)
        conn.execute('INSERT INTO usage_cumulative('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',tuple(row.values()))
    with db.key_store._connect() as conn:
        conn.execute('UPDATE usage_cumulative SET measured_input=?',(2**63-1,))
    raw = render(store, series_budget=41)
    assert global_value(raw,'represented_groups') == '1' and global_value(raw,'omitted_groups') == '0'
    assert metric(raw,'requests_total')[0].endswith(' 2')
    assert metric(raw,'tokens_total')[0].endswith(' '+str(2*(2**63-1)))
    assert db.query(UsageQuery(granularity='cumulative'),domain_id=DOMAIN)['measured_input']==2*(2**63-1)
    assert metric(raw,'latency_seconds_count')[0].endswith(' 2')


@pytest.mark.parametrize('usage', [
    TokenUsage(TokenDirection(),TokenDirection()),
    TokenUsage(TokenDirection(0,'estimated',partial=True),TokenDirection(7,'estimated',partial=True)),
    TokenUsage(TokenDirection(applicability='not_applicable'),TokenDirection(applicability='not_applicable')),
])
def test_unknown_estimated_partial_and_non_token_are_distinct(store, usage):
    put(store, usage=usage)
    raw = render(store)
    if usage.input.source == 'estimated':
        assert len(metric(raw,'tokens_total')) == 2
        assert all('input_source="estimated"' in line and 'input_partial="true"' in line for line in metric(raw,'tokens_total'))
        assert all(line.endswith(' 1') for line in metric(raw,'partial_requests_total'))
    else:
        assert not metric(raw,'tokens_total')
        family = 'not_applicable_requests_total' if usage.input.applicability == 'not_applicable' else 'unknown_requests_total'
        assert all(line.endswith(' 1') for line in metric(raw,family))


def test_unknown_roster_and_failures_do_not_invent_complete_zero(store):
    raw = telemetry.render_usage_prometheus(telemetry.collect_usage_snapshot(store[0],registry(),NOW,domain_id=DOMAIN))
    assert global_value(raw,'coverage_complete') == '0'
    assert global_value(raw,'coverage_start_timestamp_seconds') == 'NaN'
    assert global_value(raw,'available') == '1'
    assert not metric(raw,'requests_total')
    store[0].record_failure(DOMAIN)
    raw=render(store)
    assert global_value(raw,'accounting_failures') == '1'
    store[0]._pending_failure=True
    with pytest.raises(UsageError,match='accounting_unavailable'): render(store)


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


def test_active_overflow_omissions_are_unknown(store,monkeypatch):
    db,_,run,scope=store
    active=registry(max_active=1)
    monkeypatch.setattr(ledger,'_now',lambda:END)
    invocations=[UsageInvocation(store=db,run_id=run,scope=scope,caller=legacy_caller(),kind='chat',model='llm.primary',
                                registry=active,clock=lambda:NOW) for _ in range(2)]
    raw=render(store,active)
    assert global_value(raw,'omitted_groups') == 'NaN'
    assert global_value(raw,'export_complete') == '0'
    for invocation in invocations: invocation.finish('success')


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


@pytest.mark.parametrize('budget',[True,0,20,40,8193,41.0,None])
def test_invalid_trusted_budget_rejected(store,budget):
    with pytest.raises(UsageError,match='accounting_invalid'): snapshot(store,series_budget=budget)


def test_utf8_escape_byte_cap_whole_histograms_and_determinism(store):
    caller=forwarded_caller()
    caller=replace(caller,end_user=replace(caller.end_user,subject='😀'*30+'"\\'))
    for i in range(256): put(store,caller=caller,model=('😀'*120)+f'{i:03}')
    raw=render(store)
    assert raw==render(store)
    represented=int(global_value(raw,'represented_groups'))
    assert 0 < represented < 256 and len(raw)<=2*1024*1024
    assert int(global_value(raw,'omitted_groups'))==256-represented
    assert len(metric(raw,'latency_seconds_bucket'))==represented*7
    assert len(metric(raw,'latency_seconds_count'))==represented
    assert int(global_value(raw,'export_bytes'))==len(raw)
    assert '\\"' in raw.decode() and '\\\\' in raw.decode()


@pytest.mark.parametrize('field',['model','end_user_subject','actor_id'])
def test_literal_none_refuses_ambiguous_null_export(store,field):
    caller=forwarded_caller()
    if field=='end_user_subject': caller=replace(caller,end_user=replace(caller.end_user,subject='none'))
    if field=='actor_id':
        from anvil_serving.router.identity import Actor,EffectiveGrant,CallerSnapshot,scope_digest
        grant=EffectiveGrant('configured_scope',reference='none',client_id='none',scopes=('workloads:read',),policy_digest=scope_digest('none',('workloads:read',)))
        caller=CallerSnapshot('none',Actor('service','none'),grant,'owned_service')
    put(store,caller=caller,model='none' if field=='model' else 'llm.primary')
    with pytest.raises(UsageError,match='accounting_unavailable'):render(store)
    assert store[0].query(UsageQuery(granularity='cumulative'),domain_id=DOMAIN)['requests']==1


def test_limits_deadline_locked_store_and_bad_histogram_fail_closed(store,monkeypatch):
    put(store)
    monkeypatch.setattr(ledger,'_QUERY_SCAN_LIMIT',0)
    with pytest.raises(UsageError,match='accounting_unavailable'):render(store)
    monkeypatch.setattr(ledger,'_QUERY_SCAN_LIMIT',10000)
    with store[0].key_store._connect() as conn:
        conn.execute('UPDATE usage_cumulative SET latency_le_inf=2')
    with pytest.raises(UsageError,match='accounting_unavailable'):render(store)


def test_default_real_protected_http_and_stale_200_remains_original(store,policy):
    put(store)
    active=registry()
    with server(policy,usage_store=store[0],usage_domain_id=DOMAIN,usage_authority=lambda:store[3],workload_registry=active) as address:
        status,headers,raw=request(address,'/v1/admin/usage/metrics')
        assert status==200 and headers['Content-Type']=='text/plain; version=0.0.4'
        assert global_value(raw,'snapshot_timestamp_seconds') == str(NOW.timestamp())
        assert request(address,'/v1/admin/usage/metrics',token=LEGACY)[0]==403
        assert request(address,'/v1/admin/usage/metrics?')[0]==400
    old=snapshot(store)
    with server(policy,usage_metrics=lambda:telemetry.render_usage_prometheus(old)) as address:
        status,_,raw=request(address,'/v1/admin/usage/metrics')
        assert status==200 and global_value(raw,'snapshot_timestamp_seconds')==str(NOW.timestamp())
        assert global_value(raw,'freshness_limit_seconds')=='30'


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


def test_empty_and_non_token_globals_have_exactly20_samples(store):
    raw=render(store)
    assert len(samples(raw))==20 and global_value(raw,'omitted_groups')=='0'
    assert global_value(raw,'snapshot_revision')=='1'
    assert global_value(raw,'export_bytes')==str(len(raw))


def test_historical_epoch_timestamp_unknown_and_epoch_change_cannot_reuse_old_time(store):
    db=store[0]
    before=render(store)
    with db.key_store._connect() as conn:
        conn.execute('DROP TABLE usage_epoch_metadata')
    assert global_value(render(store),'coverage_epoch_timestamp_seconds')=='NaN'
    assert db.migrate()=={'schema_version':3,'migrated':True}
    assert global_value(render(store),'coverage_epoch_timestamp_seconds')=='NaN'
    assert db.migrate()=={'schema_version':3,'migrated':False}
    with db.key_store._connect() as conn:
        conn.execute('UPDATE usage_epoch_metadata SET created_at=?',(AT,))
        conn.execute('UPDATE usage_domains SET coverage_epoch=?',(ident(),))
    assert global_value(render(store),'coverage_epoch_timestamp_seconds')=='NaN'
    assert global_value(before,'coverage_epoch_timestamp_seconds')!='NaN'


def test_new_epoch_time_is_creation_clock_not_supplied_run_start(store,monkeypatch):
    db,owner,_,_=store
    monkeypatch.setattr(ledger,'_now',lambda:END)
    run=db.register_run(owner,domain_id='other_domain',configuration_revision='config_fixture',enabled=True,started_at=AT)
    scope=ledger.AuthorityScope('other_domain','config_fixture',(run,),AT,'2026-10-07T13:00:00Z')
    raw=telemetry.render_usage_prometheus(telemetry.collect_usage_snapshot(db,registry(),NOW,domain_id='other_domain',authority_scope=scope))
    assert global_value(raw,'coverage_epoch_timestamp_seconds')==str(NOW.timestamp())


@pytest.mark.parametrize('at',['invalid','2026-10-08T00:00:00.000000Z'])
def test_invalid_or_future_recorded_epoch_time_refuses_collection(store,at):
    with store[0].key_store._connect() as conn:
        conn.execute('UPDATE usage_epoch_metadata SET created_at=?',(at,))
    with pytest.raises(UsageError,match='accounting_unavailable'):render(store)
