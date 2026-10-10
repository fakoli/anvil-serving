"""Cross-seam synthetic integration; timings do not qualify installed capacity."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
import json
from statistics import median
import sys
from threading import Barrier, Event, Lock, get_ident
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

_C4_PHASES = frozenset({'barrier', 'authenticate', 'start', 'finalize', 'complete', 'failed'})


class _C4FirstFailure:
    """Retain one bounded source-benchmark snapshot without frame values."""

    def __init__(self, workers):
        self._lock = Lock()
        self._workers = {worker: None for worker in range(workers)}
        self._captured = False

    def register(self, worker):
        now = perf_counter()
        with self._lock:
            assert worker in self._workers and self._workers[worker] is None
            self._workers[worker] = {
                'thread_id': get_ident(), 'phase': 'barrier', 'started': now,
                'phase_started': now, 'admission_max_ms': None, 'finalize_max_ms': None,
            }

    def phase(self, worker, phase):
        assert phase in _C4_PHASES
        with self._lock:
            state = self._workers[worker]
            assert state is not None
            state['phase'] = phase
            state['phase_started'] = perf_counter()

    def observe(self, worker, admission_ms, finalize_ms):
        with self._lock:
            state = self._workers[worker]
            assert state is not None
            for key, value in (('admission_max_ms', admission_ms), ('finalize_max_ms', finalize_ms)):
                previous = state[key]
                state[key] = value if previous is None else max(previous, value)

    def first_failure(self, worker):
        now = perf_counter()
        frames = sys._current_frames()
        with self._lock:
            if self._captured:
                self._workers[worker]['phase'] = 'failed'
                self._workers[worker]['phase_started'] = now
                return None
            self._captured = True
            rows = []
            for worker_id, state in self._workers.items():
                if state is None:
                    continue
                stack = []
                frame = frames.get(state['thread_id'])
                while frame is not None and len(stack) < 12:
                    stack.append({'function': frame.f_code.co_name, 'line': frame.f_lineno})
                    frame = frame.f_back
                rows.append({
                    'worker': worker_id,
                    'phase': state['phase'],
                    'elapsed_ms': round((now - state['started']) * 1000, 6),
                    'phase_elapsed_ms': round((now - state['phase_started']) * 1000, 6),
                    'admission_max_ms': state['admission_max_ms'],
                    'finalize_max_ms': state['finalize_max_ms'],
                    'stack': stack,
                })
            self._workers[worker]['phase'] = 'failed'
            self._workers[worker]['phase_started'] = now
            return {
                'schema': 'source-c4-first-failure/v1',
                'failed_worker': worker,
                'workers': rows,
            }


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
    def worker(worker_id, tracked, diagnostic):
        observations = []
        diagnostic.register(worker_id)
        barrier.wait(timeout=10)
        for _ in range(16):
            start = start_at(run)
            before = perf_counter()
            phase = 'authenticate'
            try:
                diagnostic.phase(worker_id, phase)
                principal = db.key_store.authenticate(credential, snapshot=tracked)
                assert principal is not None
                if tracked:
                    phase = 'start'
                    diagnostic.phase(worker_id, phase)
                    db.start(start, authority_scope=scope)
                admission_ms = (perf_counter() - before) * 1000
                before = perf_counter()
                if tracked:
                    phase = 'finalize'
                    diagnostic.phase(worker_id, phase)
                    db.finalize(terminal(start))
                finalize_ms = (perf_counter() - before) * 1000
                diagnostic.observe(worker_id, admission_ms, finalize_ms)
                observations.append((admission_ms, finalize_ms))
            except Exception as error:
                snapshot = diagnostic.first_failure(worker_id)
                if snapshot is not None:
                    # Preserve the raising frames and bounded numeric/type
                    # metadata while adding the four-worker causal snapshot.
                    # Exception text, frame values, SQL and paths stay absent.
                    import sqlite3
                    chain, seen, current = [], set(), error
                    while current is not None and id(current) not in seen and len(chain) < 4:
                        seen.add(id(current))
                        frames, trace = [], current.__traceback__
                        while trace is not None and len(frames) < 16:
                            frames.append({'function': trace.tb_frame.f_code.co_name, 'line': trace.tb_lineno})
                            trace = trace.tb_next
                        chain.append({'exception': type(current).__name__, 'frames': frames,
                                      **{name: getattr(current, name, None) for name in
                                         ('sqlite_errorcode', 'errno', 'winerror')}})
                        current = current.__cause__ or current.__context__
                    print('SOURCE_C4_FAILURE ' + json.dumps({
                        'phase': phase, 'tracked': tracked,
                        'elapsed_ms': (perf_counter() - before) * 1000,
                        'sqlite_version': sqlite3.sqlite_version, 'chain': chain,
                        'worker_snapshot': snapshot,
                    }, sort_keys=True))
                raise
        diagnostic.phase(worker_id, 'complete')
        return observations
    def sample(tracked):
        diagnostic = _C4FirstFailure(4)
        with ThreadPoolExecutor(max_workers=4) as pool:
            batches = list(pool.map(lambda worker_id: worker(worker_id, tracked, diagnostic), range(4)))
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


def test_c4_first_failure_attributes_held_head_without_exposing_values_and_queue_recovers(store):
    db, _, run, scope = store
    starts = [start_at(run) for _ in range(3)]
    for start in starts:
        db.start(start, authority_scope=scope)
    entered, release = Event(), Event()
    diagnostic = _C4FirstFailure(4)

    def held_head():
        diagnostic.register(0)
        diagnostic.observe(0, 2.5, 3.5)
        diagnostic.phase(0, 'finalize')
        with db.key_store._write():
            entered.set()
            assert release.wait(5)
        diagnostic.phase(0, 'complete')

    def waiting_finalizer(worker, start):
        diagnostic.register(worker)
        diagnostic.phase(worker, 'finalize')
        began = perf_counter()
        try:
            db.finalize(terminal(start))
        except UsageError as error:
            return error.code, perf_counter() - began, diagnostic.first_failure(worker)
        raise AssertionError('queued finalizer unexpectedly succeeded')

    with ThreadPoolExecutor(max_workers=4) as pool:
        head = pool.submit(held_head)
        assert entered.wait(2)
        waiters = [pool.submit(waiting_finalizer, index + 1, start)
                   for index, start in enumerate(starts)]
        queue_deadline = perf_counter() + 2
        while perf_counter() < queue_deadline:
            with db.key_store._writer_condition:
                if len(db.key_store._writer_queue) == 4:
                    break
            sleep(.005)
        else:
            raise AssertionError('all synthetic writers did not enter the FIFO')
        try:
            refused = [future.result(timeout=2) for future in waiters]
        finally:
            release.set()
        head.result(timeout=2)

    assert all(code == 'accounting_unavailable' and .85 <= elapsed < 1.35
               for code, elapsed, _ in refused)
    [snapshot] = [snapshot for _, _, snapshot in refused if snapshot is not None]
    assert set(snapshot) == {'schema', 'failed_worker', 'workers'}
    assert snapshot['schema'] == 'source-c4-first-failure/v1'
    assert snapshot['failed_worker'] in {1, 2, 3}
    assert len(snapshot['workers']) == 4
    assert {row['worker'] for row in snapshot['workers']} == {0, 1, 2, 3}
    assert all(set(row) == {'worker', 'phase', 'elapsed_ms', 'phase_elapsed_ms',
                           'admission_max_ms', 'finalize_max_ms', 'stack'}
               and row['phase'] in _C4_PHASES and row['elapsed_ms'] >= 0
               and row['phase_elapsed_ms'] >= 0 and 0 < len(row['stack']) <= 12
               for row in snapshot['workers'])
    assert all(set(frame) == {'function', 'line'} and isinstance(frame['function'], str)
               and type(frame['line']) is int for row in snapshot['workers'] for frame in row['stack'])
    held = next(row for row in snapshot['workers'] if row['worker'] == 0)
    assert held['phase'] == 'finalize'
    assert held['admission_max_ms'] == 2.5 and held['finalize_max_ms'] == 3.5
    assert any(frame['function'] == 'held_head' for frame in held['stack'])
    encoded = json.dumps(snapshot, sort_keys=True)
    assert all(value not in encoded for value in (
        str(db.key_store.path), 'SELECT ', 'INSERT ', 'synthetic benchmark', 'parameters', 'locals',
    ))
    with db.key_store._writer_condition:
        assert not db.key_store._writer_queue
    assert rows(db, 'usage_cumulative') == []
    assert db.finalize(terminal(starts[0])) == 'committed'
    assert rows(db, 'usage_cumulative')[0]['requests'] == 1


def test_writer_queue_and_sqlite_share_one_actual_wait_bound(store, monkeypatch):
    db, _, run, scope = store
    entered, release = Event(), Event()
    queued = Event()
    observed = {}
    from anvil_serving.router import keys
    original_wait = db.key_store._writer_condition.wait
    original_execute = keys._WriterConnection.execute

    def queue_wait(timeout=None):
        if not queued.is_set():
            observed['entered'] = perf_counter()
            observed['queue_remaining'] = timeout
            queued.set()
        return original_wait(timeout)

    def execute(connection, sql, parameters=(), /):
        if get_ident() == observed.get('worker') and sql == 'PRAGMA synchronous=FULL':
            observed['sqlite_remaining_ms'] = keys.sqlite3.Connection.execute(
                connection, 'PRAGMA busy_timeout').fetchone()[0]
        return original_execute(connection, sql, parameters)

    monkeypatch.setattr(db.key_store._writer_condition, 'wait', queue_wait)
    monkeypatch.setattr(keys._WriterConnection, 'execute', execute)
    request_start = start_at(run)

    def waiting_writer():
        observed['worker'] = get_ident()
        try:
            db.start(request_start, authority_scope=scope)
        except UsageError:
            observed['refused'] = perf_counter()
            raise

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
                waiting = pool.submit(waiting_writer)
                assert queued.wait(2)
                sleep(.55)
                assert not waiting.done()
                release.set()
                first.result(timeout=2)
                with pytest.raises(UsageError, match='accounting_unavailable'):
                    waiting.result(timeout=2)
                elapsed = observed['refused'] - observed['entered']
                assert .85 <= elapsed < 1.35
                assert 0 < observed['queue_remaining'] <= 1
                assert 0 <= observed['sqlite_remaining_ms'] <= 500
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
