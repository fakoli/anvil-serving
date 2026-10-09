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


@pytest.mark.parametrize('defect', ['recipient', 'header', 'path', 'missing_config', 'completeness', 'profile', 'no_user'])
def test_native_global_inventory_and_profile_fail_before_enrollment(tmp_path, capsys, defect):
    store, config, value = setup(tmp_path)
    webui = value['webui'][0]
    if defect == 'recipient': webui['native']['other_recipients'] = ['https://unapproved.invalid']
    elif defect == 'header': webui['native']['openai.api_configs']['0']['custom_header_names'] = ['x-OpenWebUI-User-Jwt']
    elif defect == 'path': del webui['request_paths']['title']
    elif defect == 'missing_config': webui['native']['openai.api_configs'] = {}
    elif defect == 'completeness': webui['native']['complete'] = True
    elif defect == 'profile': webui['instance'] = 'other'
    else:
        p = Path(value['router_config']); p.write_text(p.read_text() + 'require_user=false\n')
    save(config, value)
    assert command(capsys, 'install', config, '--confirm')[0] == 2
    assert not Path(value['installed_path']).exists()
    with store._connect() as db: assert db.execute('SELECT count(*) FROM key_owner_bindings').fetchone() == (0,)


@pytest.mark.parametrize('bad', ['NaN', 'Infinity', '1e400', 'null', '[]'])
def test_strict_trust_boundary_safe_failure(tmp_path, capsys, bad):
    _, config, value = setup(tmp_path)
    config.write_text(config.read_text().replace('"service:synthetic"', bad))
    code, error = command(capsys, 'install', config, '--confirm')
    assert code == 2 and 'Traceback' not in error and 'synthetic' not in error


def test_duplicate_json_protected_files_and_absent_destination(tmp_path, capsys):
    _, config, value = setup(tmp_path)
    original = config.read_text()
    config.write_text(original[:-1] + ',"schema":"duplicate"}')
    assert command(capsys, 'preview', config)[0] == 2
    config.write_text(original)
    if os.name == 'nt':
        from tests.bootstrap_windows_fixtures import WindowsFixtureTree
        tree = WindowsFixtureTree(tmp_path)
        tree.owner_readonly_with_everyone_write(config)
        try:
            assert command(capsys, 'preview', config)[0] == 2
        finally:
            tree.restore_full_control(config)
    else:
        os.chmod(config, 0o644)
        assert command(capsys, 'preview', config)[0] == 2
        os.chmod(config, 0o600)
    link = config.parent / 'hardlink.json'; os.link(config, link)
    assert command(capsys, 'preview', config)[0] == 2
    link.unlink()
    original_file = config.with_suffix(".original")
    config.rename(original_file)
    config.symlink_to(original_file)
    assert command(capsys, "preview", config)[0] == 2
    config.unlink(); original_file.rename(config)
    target = Path(value['installed_path']); target.write_text('{}'); os.chmod(target, 0o600)
    assert command(capsys, 'install', config, '--confirm')[0] == 2
    assert target.read_text() == '{}'


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


def test_actual_file_readback_not_declaration_echo_and_providers_untouched(tmp_path, capsys):
    store, config, value = setup(tmp_path)
    provider = config.parent / 'providers.json'
    provider.write_text('{"selected":"independent-cloud","model":"selected-model"}')
    before = provider.read_bytes()
    assert command(capsys, 'install', config, '--confirm')[0] == 0
    target = Path(value['installed_path'])
    target.write_text('{}')
    assert command(capsys, 'readback', config)[0] == 2
    assert provider.read_bytes() == before


def test_owner_expiry_is_sampled_after_writer_lock(tmp_path, capsys, monkeypatch):
    store, config, value = setup(tmp_path)
    assert command(capsys, 'install', config, '--confirm')[0] == 0
    with store._connect() as db: db.execute('UPDATE keys SET expires_at=100')
    class Connection:
        def __init__(self, wrapped): self.wrapped = wrapped
        def execute(self, sql, *args):
            result = self.wrapped.execute(sql, *args)
            if sql == 'BEGIN IMMEDIATE': monkeypatch.setattr(client.time, 'time', lambda: 101)
            return result
    from contextlib import contextmanager
    original_connect = KeyStore._connect
    @contextmanager
    def connect(self):
        with original_connect(self) as db: yield Connection(db)
    monkeypatch.setattr(client.time, 'time', lambda: 99)
    monkeypatch.setattr(KeyStore, '_connect', connect)
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


def test_connect_owned_key_not_relabelled(tmp_path, capsys):
    from tests.router.test_usage_identity import wide_key
    _, config, value = setup(tmp_path)
    store, metadata, token, models = wide_key(tmp_path / 'legacy', True, True)
    before = store.authenticate(token)
    value['webui'] = []
    value['bindings'][0].update(key_id=metadata['key_id'], kind='human', policy='direct')
    router = Path(value['router_config'])
    router.write_text('[server]\nauth_env="SYNTHETIC_MASTER"\napi_keys_path=' + json.dumps(str(store.path)) + '\n')
    save(config, value)
    assert command(capsys, 'install', config, '--confirm')[0] == 2
    assert store.authenticate(token) == before
    assert not Path(value['installed_path']).exists()


def test_global_json_envelope_contains_complete_default_output(tmp_path, capsys):
    _, config, _ = setup(tmp_path)
    assert main(['router', 'clients', 'preview', '--config', str(config), '--json']) == 0
    envelope = capsys.readouterr().out
    wrapped = json.loads(envelope)
    assert wrapped['ok'] and type(wrapped['data']) is str
    assert json.loads(wrapped['data'])['clients'][0]['desired_actor']['id'] == 'service:synthetic'


@pytest.mark.parametrize('header', ['Authorization', 'X-Api-Key'])
def test_native_custom_authentication_override_refused(tmp_path, capsys, header):
    _, config, value = setup(tmp_path)
    value['webui'][0]['native']['openai.api_configs']['0']['custom_header_names'] = [header]
    save(config, value)
    assert command(capsys, 'preview', config)[0] == 2


def test_installed_destination_cannot_inspect_signer_reference(tmp_path, capsys, monkeypatch):
    _, config, value = setup(tmp_path)
    signer = config.parent / 'signer'
    value['installed_path'] = str(signer)
    save(config, value)
    original = client._read
    def read(path):
        assert Path(path) != signer
        return original(path)
    monkeypatch.setattr(client, '_read', read)
    assert command(capsys, 'install', config, '--confirm')[0] == 2


@pytest.mark.parametrize('action', ['preview', 'readback', 'install'])
def test_real_concurrent_rebind_cannot_splice_owner_observations(tmp_path, monkeypatch, action):
    """A second SQLite writer at the old second-read boundary, always joined."""
    import threading
    store, config, value = setup(tmp_path)
    client.run('install', config, confirm=True)
    original = client._owner
    release_writer, writer_done = threading.Event(), threading.Event()
    errors, calls = [], []
    old_second_read = 4 if action == 'install' else 2
    def rebind():
        try:
            assert release_writer.wait(5)
            store.bind_owner(value['bindings'][0]['key_id'], 'service', 'service:replacement', 1)
        except BaseException as error:
            errors.append(type(error).__name__)
        finally:
            writer_done.set()
    thread = threading.Thread(target=rebind)
    thread.start()
    def interleave(current_store, binding):
        calls.append(binding['key_id'])
        if len(calls) == old_second_read:
            release_writer.set()
            assert writer_done.wait(5)
        return original(current_store, binding)
    monkeypatch.setattr(client, '_owner', interleave)
    try:
        result = client.run(action, config, confirm=action == 'install')
    finally:
        release_writer.set()
        thread.join(5)
    assert not thread.is_alive() and not errors
    status = result['clients'][0]
    assert status['binding_installed'] == (status['current_actor'] == status['desired_actor'])
    assert result['configuration_status'] != 'installed' or status['current_actor'] == status['desired_actor']
    assert len(calls) == (2 if action == 'install' else 1)
    assert original(store, value['bindings'][0]).id == 'service:replacement'
