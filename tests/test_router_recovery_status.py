"""Recovery diagnostics must remain bounded and cannot confer authority."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest

from anvil_serving import router_recovery_status as diagnostic
from anvil_serving.router import container_owner as custody, usage_store as ledger
from anvil_serving.router.keys import KeyStore
from tests.router.test_container_owner import record as record
from tests.router.test_owner_recovery import config as config

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='native Docker custody uses Linux')


@pytest.mark.parametrize('change', ['none', 'atime', 'old_boot', 'broad_mount', 'duplicate_mount', 'readonly_mount', 'bad_binding', 'symlink', 'oversized',
                                  'public_record', 'invalid_record', 'changed_container',
                                  'ledger', 'ledger_restart', 'closure_replace', 'closure_write'])
def test_bounded_sidecar_diagnostic_preserves_store_and_never_authorizes(
        record, config, monkeypatch, change):
    store, _, value = record
    state_path = Path(custody.binding(store)['state_path'])
    use_ledger = change in {'ledger', 'ledger_restart', 'closure_replace', 'closure_write'}
    value = deepcopy(value)
    value['phase'] = 'live'
    value['anchor']['configuration_revision'] = hashlib.sha256(config.read_bytes()).hexdigest()
    if change == 'old_boot':
        owner = replace(ledger.RunOwner(**value['anchor']['run_owner']), boot_id=str(uuid.uuid4()))
        value['anchor']['run_owner']['boot_id'] = owner.boot_id
        value['anchor']['native_view']['boot_id'] = owner.boot_id
        value['anchor']['roster_revision'] = custody.roster(owner)
    # Configure the diagnostic after the fixture has established native storage.
    config.write_text(config.read_text() + '\nrouter_owner_backend="managed-container"\n')
    value['anchor']['configuration_revision'] = hashlib.sha256(config.read_bytes()).hexdigest()
    custody.write(store, value, None)
    path = custody.location(store, value['anchor']['run_id'])
    observed = {**value['anchor']['docker'], 'available': True, 'mounts': [
        {'destination': diagnostic.router_manage.DEFAULT_INSTALLED_CONFIG,
         'source': str(config), 'read_only': True, 'type': 'bind'},
        {'destination': str(store.path.parent), 'source': str(store.path.parent),
         'read_only': False, 'type': 'volume'}]}
    observed['compose_project'] = diagnostic.router_manage.DEFAULT_COMPOSE_PROJECT
    observed['compose_service'] = diagnostic.router_manage.DEFAULT_SERVICE
    if change == 'broad_mount':
        observed['mounts'].append({'destination': str(store.path.parent.parent),
                                  'source': str(config.parent), 'read_only': False, 'type': 'bind'})
    elif change == 'duplicate_mount':
        observed['mounts'].append(dict(observed['mounts'][-1]))
    elif change == 'readonly_mount':
        observed['mounts'][-1]['read_only'] = True
    value['anchor']['docker'].update(compose_project=observed['compose_project'],
                                    compose_service=observed['compose_service'])
    path.write_text(json.dumps(value))
    if change == 'bad_binding':
        binding_path = Path(str(store.path) + '.router-owner')
        binding = json.loads(binding_path.read_text())
        binding['store_identity'][1] += 1
        binding_path.write_text(json.dumps(binding))
    elif change == 'symlink':
        target = config.parent / 'retained.json'; path.rename(target); path.symlink_to(target)
    elif change == 'oversized':
        path.write_text('x' * 16385)
    elif change == 'public_record':
        path.chmod(0o644)
    elif change == 'invalid_record':
        path.write_text('{"private-sentinel": "not for output"}')
    calls = 0
    def inspect(*args):
        nonlocal calls
        calls += 1
        return {**observed, 'restart_count': calls} if change in {'changed_container', 'ledger_restart'} else observed
    monkeypatch.setattr(diagnostic.router_manage, '_restart_custody', inspect)
    def native_reader(*args, **kwargs):
        if change == 'closure_replace':
            replacement = state_path.with_suffix('.replacement')
            replacement.write_bytes(state_path.read_bytes()); replacement.chmod(0o600)
            replacement.replace(state_path)
        elif change == 'closure_write':
            state_path.write_bytes(state_path.read_bytes() + b' ')
        return dict.fromkeys(('domain_matches', 'binding_matches', 'closure_matches', 'snapshot_matches'), True) | dict.fromkeys(
            ('missing_sidecars', 'extra_sidecars', 'invalid_records', 'phase_mismatches', 'partial_records', 'transfer_mismatches'), 0)
    monkeypatch.setattr(diagnostic, '_native_ledger', native_reader)
    monkeypatch.setattr(KeyStore, '_connect', lambda *a, **k: pytest.fail('diagnostic opened SQLite'))
    if change == 'atime':
        metadata = diagnostic._metadata
        reads = 0
        def advancing_atime(*args, **kwargs):
            nonlocal reads
            reads += 1
            info = metadata(*args, **kwargs)
            values = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
            values.update(st_atime=info.st_atime + reads, st_atime_ns=info.st_atime_ns + reads * 10**9)
            return SimpleNamespace(**values)
        monkeypatch.setattr(diagnostic, '_metadata', advancing_atime)
    before = store.path.read_bytes()
    def execute(argv, **kwargs):
        output = value['anchor']['docker']['daemon_id'] if argv[1] == 'info' else json.dumps(f'{os.getuid()}:{os.getgid()}')
        return SimpleNamespace(returncode=0, stdout=output)
    if change in {'none', 'atime', 'old_boot', 'broad_mount', 'ledger', 'ledger_restart'}:
        result = diagnostic.recovery_status(ledger=use_ledger, _run=execute)
        assert result['phases']['live'] == dict(records=1, same_boot=int(change != 'old_boot'),
            same_container=1, same_image=1, same_configuration=1, same_incarnation=int(change != 'ledger_restart'),
            same_daemon=1, same_compose=1, same_started_at=1, same_restart_count=int(change != 'ledger_restart'))
        assert result['read_only'] and not result['recovery_eligible']
        if use_ledger:
            assert result['ledger_correlation'] == 'MATCH' and result['unanchored_successor'] == 'ABSENT'
        else:
            assert result['ledger_correlation'] == result['unanchored_successor'] == 'UNKNOWN'
        assert value['anchor']['run_id'] not in json.dumps(result)
    else:
        with pytest.raises((OSError, ValueError)):
            diagnostic.recovery_status(ledger=use_ledger, _run=execute)
    assert store.path.read_bytes() == before


def test_diagnostic_errors_never_forward_private_content(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError('private-sentinel')
    monkeypatch.setattr(diagnostic, 'recovery_status', fail)
    result = diagnostic.dispatch([])
    assert result.error.code == 'router_recovery_status_unavailable'
    assert 'private-sentinel' not in str(result)


@pytest.mark.parametrize('change', ['none', 'missing', 'pending', 'unanchored', 'wrong_config', 'wrong_owner', 'extra'])
def test_native_ledger_reads_one_snapshot_without_writes(record, config, monkeypatch, change):
    import sqlite3
    from contextlib import contextmanager
    store, usage, value = record
    value = deepcopy(value)
    revision = hashlib.sha256(config.read_bytes()).hexdigest()
    value['anchor']['configuration_revision'] = revision
    value['phase'] = 'live-pending' if change == 'pending' else 'live'
    old_boot = str(uuid.uuid4())
    value['anchor']['run_owner']['boot_id'] = old_boot
    value['anchor']['native_view']['boot_id'] = old_boot
    value['anchor']['roster_revision'] = custody.roster(ledger.RunOwner(**value['anchor']['run_owner']))
    with store._connect() as db:
        db.execute('UPDATE usage_domains SET configuration_revision=?', (revision,))
        db.execute('UPDATE usage_coverage_segments SET configuration_revision=?', (revision,))
        db.execute('UPDATE usage_runs SET boot_id=?', (old_boot,))
    if change == 'wrong_owner':
        value['anchor']['run_owner']['pid'] += 1
        value['anchor']['roster_revision'] = custody.roster(ledger.RunOwner(**value['anchor']['run_owner']))
    custody.write(store, value, None)
    path = custody.location(store, value['anchor']['run_id'])
    snapshots = {path.name: custody.digest(value)}
    if change == 'missing':
        path.unlink()
    elif change == 'extra':
        extra = path.with_name(str(uuid.uuid4()) + '.json')
        extra.write_text(path.read_text()); extra.chmod(0o600)
    elif change == 'unanchored':
        usage.register_run(ledger.RunOwner.observe('fixture'), domain_id='fixture', configuration_revision=revision)
    request = {'configuration_revision': revision, 'sidecar_snapshot_sha256': custody.digest(snapshots),
               'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(), 'incumbent': {k: value['anchor']['docker'][k] for k in
                   ('daemon_id', 'container_id', 'image_id', 'compose_project', 'compose_service')}}
    if change == 'wrong_config':
        request['configuration_revision'] = 'f' * 64
    original = KeyStore._connect
    calls = 0
    @contextmanager
    def connect(self):
        nonlocal calls
        calls += 1
        with original(self) as db:
            # Only metadata tables may be read; reject application writes at SQLite's boundary.
            def authorize(action, table, *rest):
                if action == sqlite3.SQLITE_READ and table not in {
                        'sqlite_master', 'usage_runs', 'usage_domains', 'usage_coverage_segments'}:
                    return sqlite3.SQLITE_DENY
                if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            db.set_authorizer(authorize)
            yield db
            if change != 'wrong_config':
                with pytest.raises(sqlite3.DatabaseError, match='prohibited|not authorized'):
                    db.execute('SELECT * FROM keys')
                with pytest.raises(sqlite3.DatabaseError, match='prohibited|not authorized'):
                    db.execute('DELETE FROM usage_runs')
    monkeypatch.setattr(KeyStore, '_connect', connect)
    before = store.path.read_bytes()
    if change == 'wrong_config':
        with pytest.raises(ValueError):
            diagnostic._ledger_status(request, str(config))
        assert calls == 0
    else:
        result = diagnostic._ledger_status(request, str(config))
        assert calls == 1
        assert result['missing_sidecars'] == int(change in {'missing', 'unanchored'})
        assert result['invalid_records'] == int(change == 'wrong_owner')
        assert result['extra_sidecars'] == int(change == 'extra')
        assert result['partial_records'] == int(change == 'pending')
        assert result['previous_boot_live_candidates'] == int(change not in {'missing', 'pending', 'wrong_owner'})
        assert value['anchor']['run_id'] not in json.dumps(result)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize('failure', ['none', 'start', 'drift', 'persistent_drift', 'lost_create',
                                   'timeout', 'cleanup', 'retained_helper', 'private_output'])
def test_native_helper_is_isolated_and_cleaned(monkeypatch, failure):
    import subprocess
    image = 'sha256:' + 'c' * 64
    identity = 'b' * 64
    mounts = [({'type': 'bind', 'source': '/synthetic/router.toml', 'destination': '/etc/anvil/config.toml'}, True),
              ({'type': 'volume', 'name': 'fixture-keys', 'destination': '/keys'}, False)]
    calls = []
    name = None
    inspect_count = 0
    started = False
    def observe(*args):
        nonlocal inspect_count
        inspect_count += 1
        return {'available': True, 'container_id': identity, 'image_id': image,
                'mounts': [dict(m, read_only=ro) for m, ro in mounts]}
    monkeypatch.setattr(diagnostic.router_manage, '_restart_custody', observe)
    def run(argv, **kwargs):
        nonlocal name, started
        calls.append(argv)
        output = ''
        rc = 0
        if argv[1] == 'create':
            name = argv[argv.index('--name') + 1]
            assert all(flag in argv for flag in ('--network=none', '--read-only', '--cap-drop=ALL',
                '--security-opt=no-new-privileges:true', '--pull=never', '--memory=256m', '--pids-limit=32'))
            assert '--env' not in argv and '--env-file' not in argv and image in argv
            if failure == 'lost_create':
                raise subprocess.TimeoutExpired('synthetic', 15)
            output = identity
        elif argv[1] == 'inspect':
            if argv[3].startswith('{{.Id}}'):
                output = identity + ' ' + name
            else:
                output = json.dumps(dict(user='1000:1000', network='none', readonly=True, caps=['ALL'],
                    security=['no-new-privileges:true'], cpu=10**9, memory=256*1024**2, swap=256*1024**2,
                    pids=32, restart='no', privileged=False, pid_mode='',
                    status='exited' if started else 'created', exit=0, ports={}, devices=[]))
                if failure == 'persistent_drift' or (failure == 'drift' and inspect_count == 2):
                    output = 'changed'
        elif argv[1] == 'start':
            started = True
            assert kwargs['stderr'] == subprocess.DEVNULL and kwargs['timeout'] == 35
            if failure == 'timeout':
                raise subprocess.TimeoutExpired('synthetic', 35)
            rc = int(failure == 'start')
            output = json.dumps(dict.fromkeys(('ledger_rows', 'live_rows', 'missing_sidecars', 'extra_sidecars',
                'invalid_records', 'phase_mismatches', 'partial_records', 'transfer_mismatches',
                'previous_boot_live_candidates'), 0) | dict.fromkeys(
                    ('domain_matches', 'binding_matches', 'closure_matches', 'snapshot_matches'), True))
            if failure == 'private_output':
                output = '{"private-sentinel":"hidden"}'
        elif argv[1] == 'rm':
            assert argv[-1] == identity
            rc = int(failure == 'cleanup')
        elif argv[1] == 'container':
            output = identity if failure == 'retained_helper' else ''
        return SimpleNamespace(returncode=rc, stdout=output)
    if failure == 'none':
        assert diagnostic._native_ledger({'image_id': image}, '1000:1000', mounts, {}, _run=run)['snapshot_matches']
    else:
        with pytest.raises((ValueError, subprocess.SubprocessError)):
            diagnostic._native_ledger({'image_id': image}, '1000:1000', mounts, {}, _run=run)
    assert any(call[1] == 'rm' for call in calls)
    assert calls[-1][1] == ('rm' if failure == 'cleanup' else 'container')
