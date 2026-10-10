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
                                  'public_record', 'invalid_record', 'changed_container'])
def test_bounded_sidecar_diagnostic_preserves_store_and_never_authorizes(
        record, config, monkeypatch, change):
    store, _, value = record
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
        return {**observed, 'restart_count': calls} if change == 'changed_container' else observed
    monkeypatch.setattr(diagnostic.router_manage, '_restart_custody', inspect)
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
    if change in {'none', 'atime', 'old_boot', 'broad_mount'}:
        result = diagnostic.recovery_status(_run=execute)
        assert result['phases']['live'] == dict(records=1, same_boot=int(change != 'old_boot'),
            same_container=1, same_image=1, same_configuration=1, same_incarnation=1,
            same_daemon=1, same_compose=1, same_started_at=1, same_restart_count=1)
        assert result['read_only'] and not result['recovery_eligible']
        assert result['ledger_correlation'] == result['unanchored_successor'] == 'UNKNOWN'
        assert value['anchor']['run_id'] not in json.dumps(result)
    else:
        with pytest.raises((OSError, ValueError)):
            diagnostic.recovery_status(_run=execute)
    assert store.path.read_bytes() == before


def test_diagnostic_errors_never_forward_private_content(monkeypatch):
    def fail(*args):
        raise ValueError('private-sentinel')
    monkeypatch.setattr(diagnostic, 'recovery_status', fail)
    result = diagnostic.dispatch([])
    assert result.error.code == 'router_recovery_status_unavailable'
    assert 'private-sentinel' not in str(result)
