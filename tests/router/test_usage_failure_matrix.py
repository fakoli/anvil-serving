"""Cross-seam synthetic integration; timings do not qualify installed capacity."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
import json
from statistics import median
import sys
from threading import Barrier, Lock, get_ident
from time import perf_counter

import pytest

from anvil_serving.router import usage_store as ledger
from anvil_serving.router.decision_log import TokenDirection, TokenUsage
from anvil_serving.router.keys import KeyStore
from anvil_serving.router.usage_store import Observation, UsageQuery, UsageStore
from tests.router.key_fixtures import tmp_path as tmp_path
from tests.router.test_usage_lifecycle import store as store, start_at, terminal, tokens, rows, END, ROUTE
from tests.router.test_usage_retention import forwarded_caller
from tests.router.test_usage_metrics import render, metric, global_value
from tests.router.test_usage_admin import policy as policy

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
        with self._lock:
            frames = sys._current_frames()
            now = perf_counter()
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


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Source capacity fixture requires supported Linux accounting ownership; installed FULL/WAL capacity is qualified separately",
)
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
