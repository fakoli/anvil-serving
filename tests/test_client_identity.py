"""Portable managed attribution through the real public command and key store."""
import json
import os
from pathlib import Path
import pytest
from anvil_serving import client_identity as client
from anvil_serving.cli import main
from anvil_serving.router.keys import KeyStore
from anvil_serving.router.usage_store import UsageStore
from tests.router.key_fixtures import tmp_path as tmp_path


def setup(tmp_path):
    private = tmp_path / 'private'
    store = KeyStore.initialize(private / 'keys.sqlite3')
    UsageStore(store).migrate()
    metadata, _ = store.create('synthetic', ['llm.primary'], ['/v1/chat/completions'])
    router = private / 'router.toml'
    router.write_text('[server]\nauth_env="SYNTHETIC_MASTER"\napi_keys_path=' + json.dumps(str(store.path)) +
                      '\n[[server.webui_identity]]\ncredential_id=' + json.dumps(metadata['key_id']) +
                      '\ncredential_kind="device_key"\ninstance="synthetic"\nsigner_file=' + json.dumps(str(private / 'signer')) + '\n')
    os.chmod(router, 0o600)
    value = {'schema': client.SCHEMA, 'router_config': str(router), 'installed_path': str(private / 'bindings.json'),
             'bindings': [{'client_id': 'webui', 'key_id': metadata['key_id'], 'kind': 'service',
                           'owner_id': 'service:synthetic', 'expected_revision': 0, 'policy': 'webui'}],
             'webui': [{'client_id': 'webui', 'instance': 'synthetic',
                        'native': {'openai.api_base_urls': ['https://router.invalid/v1'],
                                   'openai.api_configs': {'0': {'enable': True, 'custom_header_names': []}},
                                   'other_recipients': []},
                        'approved_recipients': ['https://router.invalid/v1'],
                        'request_paths': {p: [0] for p in client.PATHS}}]}
    config = private / 'client-identity.json'
    save(config, value)
    return store, config, value


def save(path, value):
    path.write_text(json.dumps(value)); os.chmod(path, 0o600)


def command(capsys, action, config, *flags):
    code = main(['router', 'clients', action, '--config', str(config), *flags])
    output = capsys.readouterr()
    return code, json.loads(output.out) if code == 0 else output.err


def test_real_cli_preview_install_idempotent_independent_readback(tmp_path, capsys):
    store, config, value = setup(tmp_path)
    before = store.list_keys()
    code, preview = command(capsys, 'preview', config)
    assert code == 0 and preview['configuration_status'] == 'incomplete'
    assert not Path(value['installed_path']).exists()
    assert command(capsys, 'install', config)[1]['applied'] is False
    assert command(capsys, 'install', config, '--dry-run', '--confirm')[1]['applied'] is False
    code, installed = command(capsys, 'install', config, '--confirm')
    assert code == 0 and installed['configuration_status'] == 'installed'
    raw = Path(value['installed_path']).read_bytes()
    assert command(capsys, 'install', config, '--confirm')[0] == 0
    assert Path(value['installed_path']).read_bytes() == raw
    readback = command(capsys, 'readback', config)[1]
    assert readback['installed_sha256'] == installed['installed_sha256']
    assert readback['live_status'] == 'unqualified' and not readback['providers_modified']
    assert set(readback['request_paths']['webui']) == client.PATHS
    assert store.list_keys() == before
    with store._connect() as db:
        assert db.execute('SELECT revision FROM key_owner_bindings').fetchone() == (1,)
    # Neither signer material nor account storage exists or is needed for config proof.
    assert not (config.parent / 'signer').exists()


@pytest.mark.parametrize('policy,kind', [('direct','human'), ('service','service'), ('detached_service_only','service')])
def test_direct_service_detached_preserve_wide_unicode_grants(tmp_path, capsys, policy, kind):
    store, config, value = setup(tmp_path)
    value['webui'] = []
    value['bindings'][0].update(policy=policy, kind=kind)
    with store._connect() as db:
        db.execute('UPDATE keys SET models=?', (json.dumps(['alias.' + str(i) + 'é' * 116 for i in range(64)]),))
    before = store.list_keys()
    save(config, value)
    assert command(capsys, 'install', config, '--confirm')[0] == 0
    assert store.list_keys() == before
    # Actual independent owner readback detects later conflicting rebinding.
    store.bind_owner(value['bindings'][0]['key_id'], kind, 'other', 1)
    assert command(capsys, 'readback', config)[0] == 2


def test_revocation_expiry_and_cas_not_masked_by_idempotent_document(tmp_path, capsys):
    store, config, value = setup(tmp_path)
    assert command(capsys, 'install', config, '--confirm')[0] == 0
    with store._connect() as db: db.execute('UPDATE keys SET expires_at=0')
    assert command(capsys, 'install', config, '--confirm')[0] == 2
    assert command(capsys, 'readback', config)[0] == 2


def test_accepted_legacy_full_principal_large_unicode_boundary(tmp_path, capsys):
    from tests.router.test_usage_identity import wide_key
    _, config, value = setup(tmp_path)
    store, metadata, token, models = wide_key(tmp_path / 'legacy', False, True)
    principal = store.authenticate(token)
    value['webui'] = []
    value['bindings'][0].update(key_id=metadata['key_id'], kind='human', policy='direct')
    router = Path(value['router_config'])
    router.write_text('[server]\nauth_env="SYNTHETIC_MASTER"\napi_keys_path=' + json.dumps(str(store.path)) + '\n')
    save(config, value)
    assert command(capsys, 'install', config, '--confirm')[0] == 0
    assert command(capsys, 'readback', config)[0] == 0
    assert store.authenticate(token) == principal
    assert store.list_keys()[0]['models'] == models
    assert principal.caller_snapshot is None and len(principal.models) == 64
    from anvil_serving.router.identity import IdentityError
    with pytest.raises(IdentityError): store.authenticate(token, snapshot=True)


@pytest.mark.parametrize('header', ['Authorization', 'X-Api-Key'])
def test_native_custom_authentication_override_refused(tmp_path, capsys, header):
    _, config, value = setup(tmp_path)
    value['webui'][0]['native']['openai.api_configs']['0']['custom_header_names'] = [header]
    save(config, value)
    assert command(capsys, 'preview', config)[0] == 2


def test_native_readback_authenticates_inside_the_owned_writer(tmp_path, monkeypatch):
    import hashlib
    from contextlib import contextmanager
    store, config, value = setup(tmp_path)
    metadata, token = store.create('native readback', ['llm.primary'], ['/v1/chat/completions'])
    old_id = value['bindings'][0]['key_id']
    value['bindings'][0]['key_id'] = metadata['key_id']
    router = Path(value['router_config'])
    router.write_text(router.read_text().replace(old_id, metadata['key_id']))
    save(config, value)
    client.run('install', str(config), confirm=True, _store=store)
    monkeypatch.setattr(client, 'WORKER_ROOT', config.parent)
    original = store._connect
    active = []
    @contextmanager
    def no_nesting():
        assert not active, 'native readback opened a nested SQLite connection'
        with original() as db:
            active.append(db)
            try: yield db
            finally: active.pop()
    monkeypatch.setattr(store, '_connect', no_nesting)
    result = client._native_readback(store, {'action': 'client-readback', 'store_path': str(store.path),
        'declaration_path': str(config), 'declaration_sha256': hashlib.sha256(client.canonical(value)).hexdigest(),
        'router_config_sha256': hashlib.sha256(router.read_bytes()).hexdigest(),
        'client_id': 'webui', 'credential': token})
    assert result['credential_validated'] is True
