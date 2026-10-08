"""Cross-seam synthetic integration; timings do not qualify installed capacity."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
import json
from statistics import median
from threading import Barrier, Event
from time import perf_counter, sleep

import pytest

from anvil_serving.router import usage_store as ledger
from anvil_serving.router.decision_log import TokenDirection, TokenUsage
from anvil_serving.router.internal import UsageInvocation
from anvil_serving.router.keys import KeyStore
from anvil_serving.router.usage_store import Observation, UsageError, UsageQuery, UsageStore
from tests.router.key_fixtures import tmp_path as tmp_path
from tests.router.test_usage_lifecycle import store as store, start_at, terminal, tokens, rows, AT, END, ROUTE
from tests.router.test_usage_retention import forwarded_caller
from tests.router.test_usage_metrics import registry, render, metric, global_value
from tests.router.test_usage_admin import policy as policy, server, request, ADMIN, LEGACY

NOW = datetime.fromisoformat(END.replace('Z', '+00:00'))
DOMAIN = 'domain_fixture'


def cumulative(db):
    return db.query(UsageQuery(granularity='cumulative'), domain_id=DOMAIN)


@pytest.mark.parametrize('delivery', ['success', 'disconnected', 'error'])
def test_finalization_failure_active_cleanup_protected_readback_and_retry(store, policy, monkeypatch, delivery):
    db, _, run, scope = store
    monkeypatch.setattr(ledger, '_now', lambda: END)
    active = registry()
    caller = forwarded_caller()
    invocation = UsageInvocation(db, run, scope, caller, 'chat', 'llm.primary', registry=active, clock=lambda: datetime.fromisoformat(AT.replace('Z', '+00:00')))
    invocation.dispatch()
    class Backend:
        def get_last_normalized_usage(self):
            return TokenUsage(TokenDirection(0, 'measured'), TokenDirection(3, 'estimated', partial=True))
    invocation.capture(Backend(), 'success')
    actual_finalize = db.finalize
    def unavailable(_):
        raise UsageError('accounting_unavailable')
    monkeypatch.setattr(db, 'finalize', unavailable)
    invocation.finish(delivery)
    assert invocation.terminal.delivery_outcome == delivery
    assert rows(db, 'usage_details') == []
    assert db.health(DOMAIN)['unresolved_requests'] == 1
    raw = render(store, active)
    assert global_value(raw, 'accounting_failures') == '1'
    assert global_value(raw, 'unresolved_requests') == '1'
    assert not metric(raw, 'requests_total')
    assert all(line.endswith(' 0') for line in metric(raw, 'active_requests'))
    # Ordinary credentials cannot read either the unresolved history or exporter.
    with server(policy, usage_store=db, usage_domain_id=DOMAIN, usage_metrics=lambda: render(store, active)) as address:
        for path in ('/v1/admin/usage?granularity=cumulative', '/v1/admin/usage/metrics'):
            assert request(address, path, token=LEGACY)[0] == 403
            assert request(address, path, token=ADMIN)[0] == 200
    monkeypatch.setattr(db, 'finalize', actual_finalize)
    assert db.finalize(invocation.terminal) == 'committed'
    assert db.finalize(invocation.terminal) == 'same'
    result = cumulative(db)
    assert (result['requests'], result['measured_input'], result['estimated_output'], result['unresolved_requests']) == (1, 0, 3, 0)
    [detail] = rows(db, 'usage_starts')
    assert json.loads(detail['caller']) == caller.to_dict()
    assert 'subject_fixture' in render(store, active).decode()
    assert 'synthetic-admin-token' not in json.dumps(result)


@pytest.mark.parametrize('observation', ['live', 'unknown', 'dead'])
def test_checkpoint_recovery_retention_and_export_are_one_observed_subtotal(store, monkeypatch, observation):
    db, _, run, scope = store
    start = replace(start_at(run), caller=forwarded_caller())
    db.start(start, authority_scope=scope)
    db.note_dispatch(start.request_id, ROUTE)
    observed = TokenUsage(TokenDirection(source='unknown', partial=True), TokenDirection(2, 'measured'), cache_read_input_tokens=3)
    db.note_observation(start.request_id, Observation(1, END, observed))
    reopened = UsageStore(KeyStore(db.key_store.path))
    monkeypatch.setattr(ledger, 'observe_run', lambda _: observation)
    recovery = reopened.recover((run,), host_domain_id='host_fixture')
    assert recovery['recovered_requests'] == int(observation == 'dead')
    reopened.prune('2028-10-07T12:00:00Z', domain_id=DOMAIN)
    result = cumulative(reopened)
    raw = render((reopened, *store[1:]))
    assert global_value(raw, 'coverage_complete') == '0'
    if observation == 'dead':
        assert result['requests'] == 1 and result['measured_output'] == 2
        assert result['unknown_input_requests'] == result['partial_output_requests'] == 1
        assert result['cache_read_input'] == 3 and result['unresolved_requests'] == 0
        assert not rows(reopened, 'usage_details')
        assert metric(raw, 'unknown_requests_total')[0].endswith(' 1')
        assert reopened.recover((run,), host_domain_id='host_fixture')['recovered_requests'] == 0
    else:
        assert result['requests'] is None and reopened.health(DOMAIN)['unresolved_requests'] == 1
        assert len(rows(reopened, 'usage_starts')) == 1
        assert not metric(raw, 'tokens_total')


def test_exact_fixed_projection_omission_never_changes_authoritative_history(store):
    db, _, run, scope = store
    for index in range(3):
        start = replace(start_at(run), caller=forwarded_caller(), model='llm.primary' if index < 2 else 'llm.secondary')
        db.start(start, authority_scope=scope)
        db.finalize(terminal(start, route=replace(ROUTE, backend_id='backend_'+str(index)), tokens=tokens(7, 0)))
    full = cumulative(db)
    assert full['requests'] == 3 and full['measured_input'] == 21
    raw = render(store, series_budget=41)
    assert global_value(raw, 'represented_groups') == '1'
    assert global_value(raw, 'omitted_groups') == '1'
    identities = [line.rsplit(' ', 1)[0] for line in raw.decode().splitlines() if line and not line.startswith('#')]
    assert len(identities) == len(set(identities))
    assert cumulative(db) == full
    assert len(rows(db, 'usage_details')) == 3
    assert sum(group['requests'] for group in rows(db, 'usage_cumulative')) == 3


def test_c4_start_and_finalize_cost_against_same_store_authentication_baseline(store):
    db, _, run, scope = store
    _, credential = db.key_store.create('synthetic benchmark', ['llm.primary'], ['/v1/chat/completions'], rpm=100000)
    barrier = Barrier(4)
    def worker(tracked):
        observations = []
        barrier.wait(timeout=10)
        for _ in range(16):
            start = start_at(run)
            before = perf_counter()
            principal = db.key_store.authenticate(credential, snapshot=tracked)
            assert principal is not None
            if tracked:
                db.start(start, authority_scope=scope)
            admission_ms = (perf_counter() - before) * 1000
            before = perf_counter()
            if tracked:
                db.finalize(terminal(start))
            observations.append((admission_ms, (perf_counter() - before) * 1000))
        return observations
    def sample(tracked):
        with ThreadPoolExecutor(max_workers=4) as pool:
            batches = list(pool.map(worker, [tracked] * 4))
        return [value for batch in batches for value in batch]
    sample(False)  # Warm the existing interpreter/store without ledger contributions.
    baseline = sample(False)
    tracked = sample(True)
    def p95(values):
        return sorted(values)[int(.95 * (len(values) - 1))]
    base = [pair[0] for pair in baseline]
    starts = [pair[0] for pair in tracked]
    finishes = [pair[1] for pair in tracked]
    added_p95 = p95(starts) - p95(base)
    report = dict(workers=4, samples=len(tracked), baseline='same-store authenticate(snapshot=False), accounting disabled',
                  baseline_p50_ms=median(base), baseline_p95_ms=p95(base), tracked_p50_ms=median(starts),
                  tracked_p95_ms=p95(starts), added_p50_ms=median(starts)-median(base), added_p95_ms=added_p95,
                  finalize_p50_ms=median(finishes), finalize_p95_ms=p95(finishes),
                  admission_max_ms=max(starts), finalize_max_ms=max(finishes),
                  provisional_target_ms=20, provisional_target_met=added_p95 <= 20, source_only=True)
    print('SOURCE_C4_COST '+json.dumps(report, sort_keys=True))
    # The target is reported for independent acceptance; a miss holds capacity
    # qualification. Several sequential operations/fsyncs are not one lock wait.
    # Verify the actual per-connection SQLite bound without weakening durability.
    with db.key_store._connect() as connection:
        assert connection.execute('PRAGMA busy_timeout').fetchone()[0] == 1000
    result = cumulative(db)
    assert result['requests'] == 64 and result['measured_input'] == 448 and result['measured_output'] == 320
    assert len(rows(db, 'usage_details')) == 64


def test_writer_queue_and_sqlite_share_one_actual_wait_bound(store):
    db, _, run, scope = store
    entered, release = Event(), Event()
    def prior_writer():
        with db.key_store._write():
            entered.set()
            assert release.wait(5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(prior_writer)
        assert entered.wait(2)
        try:
            with db.key_store._connect() as external:
                external.execute('BEGIN EXCLUSIVE')
                started = perf_counter()
                waiting = pool.submit(db.start, start_at(run), authority_scope=scope)
                sleep(.55)
                assert not waiting.done()
                release.set()
                first.result(timeout=2)
                with pytest.raises(UsageError, match='accounting_unavailable'):
                    waiting.result(timeout=2)
                elapsed = perf_counter() - started
                assert .85 <= elapsed < 1.35
                external.execute('ROLLBACK')
        finally:
            release.set()
    assert rows(db, 'usage_starts') == []
    # A timed-out ticket cannot strand later credential/accounting writers.
    start = start_at(run)
    db.start(start, authority_scope=scope)
    db.finalize(terminal(start))
    assert cumulative(db)['requests'] == 1


def test_writer_begin_and_commit_share_one_actual_wait_bound(store, monkeypatch):
    db, _, run, scope = store
    original = db.key_store._connect
    attempting = Event()
    from contextlib import contextmanager
    @contextmanager
    def traced_connection():
        with original() as connection:
            connection.set_trace_callback(
                lambda sql: attempting.set() if sql == 'BEGIN IMMEDIATE' else None
            )
            yield connection
    monkeypatch.setattr(db.key_store, '_connect', traced_connection)
    with original() as reader, original() as prior_writer, ThreadPoolExecutor(max_workers=1) as pool:
        assert reader.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        reader.execute('BEGIN')
        reader.execute('SELECT COUNT(*) FROM usage_domains').fetchone()
        prior_writer.execute('BEGIN IMMEDIATE')
        started = perf_counter()
        waiting = pool.submit(db.start, start_at(run), authority_scope=scope)
        try:
            assert attempting.wait(1)
            sleep(.65)
            prior_writer.execute('ROLLBACK')
            with pytest.raises(UsageError, match='accounting_unavailable'):
                waiting.result(timeout=2)
            assert .85 <= perf_counter() - started < 1.35
        finally:
            if prior_writer.in_transaction:
                prior_writer.execute('ROLLBACK')
            reader.execute('ROLLBACK')
    assert rows(db, 'usage_starts') == []
    monkeypatch.setattr(db.key_store, '_connect', original)
    start = start_at(run)
    assert db.start(start, authority_scope=scope) == 'started'
    db.finalize(terminal(start))
    assert cumulative(db)['requests'] == 1
