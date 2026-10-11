"""Never-admitted managed successor recovery preserves typed custody history."""
from copy import deepcopy
import os
from pathlib import Path
import time
import uuid

import pytest

from anvil_serving.router import container_owner as custody, reboot, startup_abort, usage_store as ledger
from anvil_serving.router.admission import managed_router_admission
from tests.router.test_container_owner import record as record
from tests.router.test_owner_recovery import config as config
from tests.router.test_reboot import acknowledgment, retained as retained


def failed_successor(f):
    with f.store._offline_custody(f.settings, allow_retained=True):
        observed, record, state = reboot.fenced_candidate(f.usage, f.auth, f.incumbent)
        recovered = reboot.recovered_record(record, state, observed, f.stopped,
                                             acknowledgment(f, observed))
        custody.write(f.store, recovered, custody.digest(record))
        ready = {**recovered, 'phase': 'ready', 'sequence': 1}
        custody.write(f.store, ready, custody.digest(recovered))
    owner, usage, run_id = managed_router_admission(f.settings, f.revision)
    return owner, usage, run_id


def scan(owner):
    return {'boot_id': owner.boot_id, 'pid_namespace_device': owner.pid_namespace_device,
            'pid_namespace_inode': owner.pid_namespace_inode, 'processes_scanned': 931,
            'inaccessible': 0, 'matches': 0}


def auth(preview):
    now = int(time.time())
    return {'schema': 'router-startup-abort-authorization/v1', 'operation_id': 'a' * 64,
            **{name: preview[name] for name in ('run_id', 'configuration_revision', 'roster_revision',
                                                'generation', 'closure_sha256', 'sidecar_snapshot_sha256')},
            'acknowledged_at': now, 'expires_at': now + 600}


def test_aborted_startup_is_terminal_without_fabricating_anchor(retained, monkeypatch):
    f = retained
    owner, usage, run_id = failed_successor(f)
    try:
        with usage.key_store._offline_custody(
                f.settings, producer_descriptor=owner._owner_descriptor, allow_retained=True):
            preview = startup_abort.preview_startup_abort(usage, run_id)
            result = startup_abort.abort_startup(usage, auth(preview), namespace_scan=scan)
            assert result['phase'] == 'aborted' and 'anchor' not in result
            assert result['registration']['run_id'] == run_id
            assert startup_abort.preview_startup_abort(usage, run_id) == preview
            assert custody.ready_transfers(usage) == []
        with f.store._connect() as db:
            row = db.execute('SELECT state,ended_at FROM usage_runs WHERE run_id=?', (run_id,)).fetchone()
            segment = db.execute('SELECT ended_at,closure_reason,end_uncertain FROM usage_coverage_segments '
                                 'WHERE run_id=?', (run_id,)).fetchone()
            assert row[0] == 'dead' and row[1] == result['ended_at']
            assert segment == (result['ended_at'], 'owner_dead', 0)
            assert db.execute('SELECT COUNT(*) FROM usage_starts WHERE run_id=?', (run_id,)).fetchone() == (0,)
        monkeypatch.setattr(ledger, 'observe_run', lambda *_args, **_kwargs: 'unknown')
    finally:
        os.close(owner._owner_descriptor)
        owner._owner_descriptor = None
    successor, _usage, successor_run = managed_router_admission(f.settings, f.revision)
    try:
        assert successor_run != run_id and successor._closed
        with f.store._connect() as db:
            assert sorted(db.execute('SELECT state FROM usage_runs').fetchall()) == [
                ('dead',), ('dead',), ('live',)]
    finally:
        os.close(successor._owner_descriptor)
        successor._owner_descriptor = None


def test_aborted_startup_retry_finishes_pending_exact_record(retained, monkeypatch):
    f = retained
    owner, usage, run_id = failed_successor(f)
    original = startup_abort._database_state
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError('synthetic interruption')
        return original(*args, **kwargs)

    try:
        with usage.key_store._offline_custody(
                f.settings, producer_descriptor=owner._owner_descriptor, allow_retained=True):
            preview = startup_abort.preview_startup_abort(usage, run_id)
            authorization = auth(preview)
            monkeypatch.setattr(startup_abort, '_database_state', interrupted)
            with pytest.raises(OSError):
                startup_abort.abort_startup(usage, authorization, namespace_scan=scan)
            pending = deepcopy(startup_abort.validate(
                startup_abort._private_json(custody.location(f.store, run_id))))
            assert pending['phase'] == 'abort-pending'
            result = startup_abort.abort_startup(usage, authorization, namespace_scan=scan)
            assert result == {**pending, 'phase': 'aborted', 'sequence': 1}
            assert startup_abort.abort_startup(usage, authorization, namespace_scan=scan) == result
    finally:
        os.close(owner._owner_descriptor)
        owner._owner_descriptor = None


def test_preview_refuses_sidecar_filename_identity_drift(retained):
    f = retained
    owner, usage, run_id = failed_successor(f)
    try:
        records = sorted(Path(custody.location(f.store, run_id)).parent.glob('*.json'))
        predecessor = next(path for path in records if path.stem != run_id)
        predecessor.rename(predecessor.with_name(str(uuid.uuid4()) + '.json'))
        with usage.key_store._offline_custody(
                f.settings, producer_descriptor=owner._owner_descriptor, allow_retained=True):
            with pytest.raises(ValueError):
                startup_abort.preview_startup_abort(usage, run_id)
    finally:
        os.close(owner._owner_descriptor)
        owner._owner_descriptor = None


@pytest.mark.parametrize('change', ['request', 'closure', 'scan'])
def test_abort_refuses_traffic_drift_or_incomplete_extinction(retained, change):
    f = retained
    owner, usage, run_id = failed_successor(f)
    try:
        with usage.key_store._offline_custody(
                f.settings, producer_descriptor=owner._owner_descriptor, allow_retained=True):
            preview = startup_abort.preview_startup_abort(usage, run_id)
            authorization = auth(preview)
            scanner = scan
            if change == 'request':
                with usage.key_store._connect() as db:
                    source = db.execute('SELECT * FROM usage_starts LIMIT 1').fetchone()
                    assert source is None
                    db.execute("INSERT INTO usage_starts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        'request-fixture', run_id, f.settings.usage_domain_id,
                        preview['run_id'], f.revision, '2026-01-01T00:00:00.000000Z', '{}',
                        'chat', None, None, None, 'exclusive', '{}', None, None, None))
            elif change == 'closure':
                authorization['closure_sha256'] = '0' * 64
            else:
                def scanner(retained_owner):
                    return {**scan(retained_owner), 'matches': 1}
            with pytest.raises((ValueError, custody.KeyStoreError)):
                startup_abort.abort_startup(usage, authorization, namespace_scan=scanner)
            assert not custody.location(f.store, run_id).exists()
    finally:
        os.close(owner._owner_descriptor)
        owner._owner_descriptor = None
