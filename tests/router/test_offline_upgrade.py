"""Offline bootstrap is custody, never a predecessor drain receipt."""
import json
import os
from types import SimpleNamespace

import pytest

from anvil_serving import router_manage
from anvil_serving.router import keys
from tests.router.key_fixtures import tmp_path as tmp_path


@pytest.fixture(autouse=True)
def isolated_operator_home(tmp_path, monkeypatch):
    monkeypatch.setenv('ANVIL_SERVING_HOME', str(tmp_path / 'operator-home'))


def fixture(tmp_path):
    store = keys.KeyStore.initialize(tmp_path / 'owned' / 'keys.sqlite3')
    metadata, secret = store.create('synthetic', ['llm.primary'], ['/v1/chat/completions'])
    config = tmp_path / 'router.toml'
    config.write_text('[server]\nauth_env="SYNTHETIC_MASTER"\napi_keys_path=' + json.dumps(str(store.path)) +
                      '\nadmission_state_path=' + json.dumps(str(store.path.parent / 'admission.json')) +
                      '\nrouter_owner_id="synthetic-owner"\nrouter_owner_roster=["synthetic-owner"]\nusage_domain_id="synthetic-domain"\n')
    return store, metadata, secret, config


@pytest.mark.skipif(os.name != 'posix', reason='native managed owner requires POSIX locks')
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


@pytest.mark.parametrize('state', ['absent', 'exited', 'created'])
def test_cold_up_uses_offline_custody_without_fabricating_old_drain(monkeypatch, state):
    calls = []
    monkeypatch.setattr(router_manage, '_compose_owner_backend', lambda *a, **k: 'native-process')
    monkeypatch.setattr(router_manage, '_container_compose_project', lambda *a, **k: (state, None if state == 'absent' else 'anvil-serving'))
    monkeypatch.setattr(router_manage, 'require_router_drain', lambda *a, **k: pytest.fail('cold predecessor drain'))
    monkeypatch.setattr(router_manage, 'require_router_offline', lambda *a, **k: calls.append('offline'))
    assert router_manage.cmd_up('synthetic.yml', 'router', _run=lambda *a, **k: SimpleNamespace(returncode=0, stdout='', stderr='')) == 0
    assert calls == ['offline']


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


def test_offline_custody_refuses_unsupported_platform(tmp_path, monkeypatch):
    from anvil_serving.router.config import load_server_config
    store, _, _, config = fixture(tmp_path)
    monkeypatch.setattr(keys, 'os', SimpleNamespace(name='nt'))
    with pytest.raises(keys.KeyStoreError, match='custody is unavailable'):
        with store._offline_custody(load_server_config(str(config))):
            pytest.fail('unsupported writer custody')


@pytest.mark.skipif(os.name != 'posix', reason='native owner storage uses POSIX mount paths')
@pytest.mark.parametrize('consumer,scoped,blocked', [
    ('media', True, False), ('media', False, True), ('keys', True, True),
    ('parent', True, True), ('child', True, True), ('alias', True, True),
    ('readonly', True, False), ('tmpfs', True, False), ('bind-alias', True, True),
    ('bind-parent', True, True), ('bind-child', True, True),
])
def test_owner_roster_separates_media_but_rejects_accounting_writers(tmp_path, monkeypatch, consumer, scoped, blocked):
    private = tmp_path/'private'; private.mkdir()
    (private/'subdir').mkdir()
    media = tmp_path/'media'; media.mkdir()
    config = tmp_path/'router.toml'
    config.write_text('[server]\nauth_env="SYNTHETIC_AUTH"\napi_keys_path="/state/keys/keys.sqlite3"\n'
                      'admission_state_path="/state/keys/intent.json"\n')
    mounts = [dict(type='bind', source=str(tmp_path), target='/state'),
              dict(type='volume', source='keys', target='/state/keys'),
              dict(type='bind', source=str(media), target='/media'),
              dict(type='bind', source=str(config), target=router_manage.DEFAULT_INSTALLED_CONFIG, read_only=True)]
    rendered = {'name':'anvil-serving', 'services':{'router':{'container_name':'anvil-router', 'volumes':mounts}},
                'volumes':{'keys':{'name':'synthetic-keys'}}}
    actual = [dict(type=m['type'], source=str(private) if m['type']=='volume' else m['source'],
                   name='synthetic-keys' if m['type']=='volume' else '', destination=m['target'],
                   read_only=m.get('read_only',False)) for m in mounts]
    incumbent = dict(available=True, container_id='a'*64, mounts=actual)
    monkeypatch.setattr(router_manage, '_restart_custody', lambda *a: incumbent)
    monkeypatch.setattr(router_manage, '_container_compose_project', lambda *a, **k: ('running','anvil-serving'))
    shared = dict(Type='bind', Source=str(media), RW=True)
    if consumer in {'keys','readonly'}:
        shared = dict(Type='volume', Name='synthetic-keys', Source=str(private), RW=consumer!='readonly')
    elif consumer == 'parent': shared['Source'] = str(tmp_path)
    elif consumer == 'child': shared['Source'] = str(private/'subdir')
    elif consumer == 'alias':
        alias = tmp_path/'alias'; alias.symlink_to(private, target_is_directory=True); shared['Source']=str(alias)
    elif consumer == 'tmpfs': shared = dict(Type='tmpfs', Source='', RW=True)
    elif consumer.startswith('bind-'):
        from pathlib import Path
        alias = tmp_path/'bind'; alias.mkdir()
        (alias/'subdir').mkdir()
        source = tmp_path if consumer == 'bind-parent' else private
        destination = alias
        shared['Source'] = str(alias/'subdir' if consumer == 'bind-child' else alias)
        original_stat = Path.stat
        monkeypatch.setattr(Path, 'stat', lambda p, **k: original_stat(source if p == destination else p, **k))
    rows = [{'id':'a'*64,'status':'running','mounts':[]}, {'id':'b'*64,'status':'running','mounts':[shared]}]
    def run(argv, **kwargs):
        if 'config' in argv:
            assert '--no-env-resolution' in argv and '--no-interpolate' in argv
            result = json.dumps(rendered)
        elif argv[:2] == ['docker','ps']:
            result = 'a'*64 if '--filter' in argv else 'a'*64+'\n'+'b'*64
        elif argv[:2] == ['docker','inspect']: result = '\n'.join(json.dumps(r) for r in rows)
        else: raise AssertionError(argv)
        return SimpleNamespace(returncode=0, stdout=result)
    def observe():
        return router_manage._offline_compose_roster('owned.json','router','anvil-router',_run=run,
            live_target=True,_metadata_only=True,_owner_state_only=scoped)
    if blocked:
        with pytest.raises(ValueError, match='router_offline_storage_busy'): observe()
    else:
        before=observe()
        assert before['roster']==rows  # Bystander drift remains visible.
        assert before['owner_storage']['sources']==[('volume','synthetic-keys')]
        assert before['owner_storage']['directories']==[str(private)]
        config.write_text(config.read_text()+'\n# configuration changed\n')
        assert before['owner_storage'] != observe()['owner_storage']
        actual[1]['name']='changed-key-volume'
        with pytest.raises(ValueError): observe()
