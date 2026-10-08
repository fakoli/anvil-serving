"""Offline bootstrap is custody, never a predecessor drain receipt."""
import json
import os
from types import SimpleNamespace

import pytest

from anvil_serving import router_manage
from anvil_serving.router import keys
from anvil_serving.router.usage_store import UsageStore
from tests.router.key_fixtures import tmp_path as tmp_path


def fixture(tmp_path):
    store = keys.KeyStore.initialize(tmp_path / 'owned' / 'keys.sqlite3')
    metadata, secret = store.create('synthetic', ['llm.primary'], ['/v1/chat/completions'])
    config = tmp_path / 'router.toml'
    config.write_text('[server]\nauth_env="SYNTHETIC_MASTER"\napi_keys_path=' + json.dumps(str(store.path)) +
                      '\nadmission_state_path=' + json.dumps(str(store.path.parent / 'admission.json')) +
                      '\nrouter_owner_id="synthetic-owner"\nrouter_owner_roster=["synthetic-owner"]\nusage_domain_id="synthetic-domain"\n')
    return store, metadata, secret, config


def test_offline_cli_migration_preserves_authority_and_protected_snapshot(tmp_path, capsys):
    from anvil_serving.cli import main
    store, metadata, secret, config = fixture(tmp_path)
    backup = tmp_path / 'backup' / 'keys.sqlite3'
    before = store.list_keys()
    args = ['migrate', '--config', str(config), '--backup-out', str(backup), '--offline', '--confirm']
    assert main(['router', 'keys', *args]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {'schema_version': 3, 'migrated': True, 'backup_schema_version': 1, 'offline': True}
    assert keys.KeyStore(store.path).list_keys() == before
    assert keys.KeyStore(store.path).authenticate(secret).key_id == metadata['key_id']
    assert keys.KeyStore(backup).version == 1
    assert backup.stat().st_mode & 0o777 == 0o600
    assert keys.dispatch(args) == 2  # Never overwrite the rollback snapshot.


@pytest.mark.skipif(os.name != 'posix', reason='native managed owner requires POSIX locks')
@pytest.mark.parametrize('lock', ['producer', 'writer'])
def test_offline_migration_refuses_actual_competing_lock_before_snapshot(tmp_path, lock):
    import fcntl
    store, _, _, config = fixture(tmp_path)
    path = (store.path.parent / 'admission.json.router.lock' if lock == 'producer'
            else type(store.path)(str(store.path) + '.router-writers.lock'))
    with path.open('w') as held:
        path.chmod(0o600)
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        backup = tmp_path / 'backup' / 'keys.sqlite3'
        assert keys.dispatch(['migrate', '--config', str(config), '--backup-out', str(backup), '--offline', '--confirm']) == 2
    assert not backup.exists()
    assert keys.KeyStore(store.path).version == 1


def test_offline_migration_preserves_bound_closure_and_refuses_unknown_owner(tmp_path):
    store, _, _, config = fixture(tmp_path)
    state = store.path.parent / 'admission.json.router'
    state.write_text(json.dumps({'schema': 'router-admission/v1', 'owner_id': 'synthetic-owner',
                                'closure': {'consumed': True}}))
    state.chmod(0o600)
    keys._bind_router_store(store.path, state, 'synthetic-owner')
    before = state.read_bytes()
    backup = tmp_path / 'backup' / 'keys.sqlite3'
    assert keys.dispatch(['migrate', '--config', str(config), '--backup-out', str(backup), '--offline', '--confirm']) == 2
    assert state.read_bytes() == before and not backup.exists()


@pytest.mark.parametrize('state', ['absent', 'exited', 'created'])
def test_cold_up_uses_offline_custody_without_fabricating_old_drain(monkeypatch, state):
    calls = []
    monkeypatch.setattr(router_manage, '_container_compose_project', lambda *a, **k: (state, None if state == 'absent' else 'anvil-serving'))
    monkeypatch.setattr(router_manage, 'require_router_drain', lambda *a, **k: pytest.fail('cold predecessor drain'))
    monkeypatch.setattr(router_manage, 'require_router_offline', lambda *a, **k: calls.append('offline'))
    assert router_manage.cmd_up('synthetic.yml', 'router', _run=lambda *a, **k: SimpleNamespace(returncode=0, stdout='', stderr='')) == 0
    assert calls == ['offline']


@pytest.mark.parametrize('state', ['running', 'restarting', 'paused', 'unknown'])
def test_active_or_unknown_up_cannot_take_cold_path(monkeypatch, state):
    monkeypatch.setattr(router_manage, '_container_compose_project', lambda *a, **k: (state, 'anvil-serving'))
    monkeypatch.setattr(router_manage, 'require_router_offline', lambda *a, **k: pytest.fail('active cold path'))
    monkeypatch.setattr(router_manage, 'require_router_drain', lambda *a, **k: (_ for _ in ()).throw(ValueError('hold')))
    assert router_manage.cmd_up('synthetic.yml', 'router') == 1


def test_offline_compose_roster_change_refuses_before_launch(monkeypatch):
    calls = []
    observations = 0
    rendered = {'name': 'anvil-serving', 'services': {'router': {'container_name': 'anvil-router',
                'volumes': [{'type': 'volume', 'source': 'keys', 'target': '/var/lib/anvil-serving/router-keys'},
                            {'type': 'bind', 'source': '/synthetic/router.toml',
                             'target': router_manage.DEFAULT_INSTALLED_CONFIG, 'read_only': True}]}},
                'volumes': {'keys': {'name': 'synthetic-keys'}}}
    monkeypatch.setattr(router_manage, '_container_compose_project', lambda *a, **k: ('absent', None))
    def run(argv, **kwargs):
        nonlocal observations
        calls.append(argv)
        if 'config' in argv:
            value = json.dumps(rendered)
        elif argv[:2] == ['docker', 'ps']:
            observations += 1
            value = '' if observations == 1 else 'a' * 64
        elif argv[:2] == ['docker', 'inspect']:
            value = json.dumps({'id': 'a' * 64, 'status': 'running', 'mounts': [
                {'Type': 'volume', 'Name': 'synthetic-keys', 'RW': True}]})
        elif 'run' in argv:
            value = json.dumps({'schema': 'router-offline-start/v1', 'offline': True, 'schema_version': 3})
        else:
            value = ''
        return SimpleNamespace(returncode=0, stdout=value, stderr='')
    assert router_manage.cmd_up('synthetic.yml', 'router', _run=run) == 1
    assert not any('up' in call for call in calls)


@pytest.mark.parametrize('rendered', [[], {'services': []},
    {'name': 'anvil-serving', 'services': {'router': {'container_name': 'anvil-router',
      'volumes': [{'type': 'volume', 'source': 'keys', 'target': '/var/lib/anvil-serving/router-keys'}]}},
      'volumes': {'keys': {'name': 'synthetic-keys'}}}])
def test_unknown_or_unmounted_compose_config_refuses_before_probe(rendered):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(rendered), stderr='')
    with pytest.raises(ValueError):
        router_manage.require_router_offline('synthetic.yml', 'router', _run=run)
    assert not any('run' in call for call in calls)


@pytest.mark.skipif(os.name != 'posix', reason='native managed owner requires POSIX locks')
def test_actual_start_refuses_producer_acquired_after_offline_probe(tmp_path, monkeypatch):
    import fcntl
    from anvil_serving.router.config import load_server_config
    from anvil_serving.router.admission import managed_router_admission
    store, _, _, config = fixture(tmp_path)
    UsageStore(store).migrate()
    monkeypatch.setattr(router_manage, 'DEFAULT_INSTALLED_CONFIG', str(config))
    assert router_manage._offline_router_start()['offline'] is True
    lock = store.path.parent / 'admission.json.router.lock'
    with lock.open('r+') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            managed_router_admission(load_server_config(str(config)), 'c' * 64)


def test_offline_migration_requires_closed_owner_and_explicit_confirmation(tmp_path):
    store, _, _, config = fixture(tmp_path)
    backup = tmp_path / 'backup' / 'keys.sqlite3'
    base = ['migrate', '--config', str(config), '--backup-out', str(backup)]
    for flags in ([], ['--offline'], ['--confirm'], ['--offline', '--confirm', '--container', 'anvil-router']):
        assert keys.dispatch(base + flags) == 2
    config.write_text(config.read_text().replace('router_owner_roster=["synthetic-owner"]',
                                              'router_owner_roster=["synthetic-owner","other-owner"]'))
    assert keys.dispatch(base + ['--offline', '--confirm']) == 2
    assert keys.KeyStore(store.path).version == 1 and not backup.exists()
