"""Fixed native enrollment with real storage, custody and private pipe protocol."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys

import pytest

from anvil_serving import client_identity as client
from anvil_serving.observability.dashboard.contracts import canonical
from anvil_serving.router.keys import KeyStore, KeyStoreError
from anvil_serving.router.usage_store import RunOwner, UsageStore
from tests.router.key_fixtures import tmp_path as tmp_path


def fixture(tmp_path):
    # Native Windows 3.13 mode700 adds an untrusted OWNER_RIGHTS ACE.
    # Inherit the fixture's protected owner-only DACL, as KeyStore does.
    mode = 0o777 if os.name == 'nt' else 0o700
    private = tmp_path / 'material'; private.mkdir(mode=mode)
    worker = tmp_path / 'worker'; worker.mkdir(mode=mode)
    store = KeyStore.initialize(tmp_path / 'store' / 'keys.sqlite3')
    UsageStore(store).migrate()
    metadata, credential = store.create('synthetic enrollment', ['llm.primary'], ['/v1/chat/completions'])
    router = tmp_path / 'router.toml'
    router.write_text('[server]\nauth_env="SYNTHETIC_MASTER"\napi_keys_path=' + json.dumps(str(store.path)) +
                      '\nadmission_state_path=' + json.dumps(str(store.path.parent / 'admission.json')) +
                      '\nrouter_owner_id="synthetic-owner"\nrouter_owner_roster=["synthetic-owner"]\n'
                      'usage_domain_id="synthetic-domain"\n')
    router.chmod(0o600)
    value = {'schema': client.SCHEMA, 'router_config': str(router), 'installed_path': str(private / 'bindings.json'),
             'bindings': [{'client_id': 'synthetic-client', 'key_id': metadata['key_id'], 'kind': 'human',
                           'owner_id': 'human:synthetic', 'expected_revision': 0, 'policy': 'direct'}], 'webui': []}
    declaration = worker / 'client-identity.json'; save(declaration, value)
    job = {'schema': client.WORKER_SCHEMA, 'declaration_sha256': hashlib.sha256(canonical(value)).hexdigest(),
           'helper_sha256': {'webui': None, 'recipient': None}, 'recipients': [],
           'material_directories': [str(private)]}
    save(worker / 'worker.json', job)
    return store, declaration, value, job, credential


@pytest.fixture
def empty_history_owner(monkeypatch):
    # Storage/fence tests have no retained runs. Namespace comparability is
    # qualified separately by the actual managed Docker fixture.
    owner = RunOwner('synthetic-owner', '00000000-0000-4000-8000-000000000000',
                     1, 2, 1, 2, 1000, 1, 3, 123, 456)
    monkeypatch.setattr(RunOwner, 'observe', lambda *a, **k: owner)
    return owner


def save(path, value):
    path.write_text(json.dumps(value)); path.chmod(0o600)


@pytest.mark.skipif(os.name != 'posix', reason='native offline custody requires POSIX producer/writer fences')
def test_same_store_enrollment_holds_real_producer_and_writer_fences(tmp_path, empty_history_owner):
    import fcntl
    store, declaration, value, _, _ = fixture(tmp_path)
    with client._offline_store(declaration) as (owned, loaded, server):
        for path in (store.path.parent / 'admission.json.router.lock', Path(str(store.path) + '.router-writers.lock')):
            with path.open('r+') as stream, pytest.raises(BlockingIOError):
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # A second instance cannot inherit the thread-local custody permission.
        with pytest.raises((BlockingIOError, KeyStoreError)):
            store.bind_owner(value['bindings'][0]['key_id'], 'human', 'foreign', 0)
        result = client.run('install', declaration, confirm=True, _store=owned, _declaration=(loaded, server))
        assert result['configuration_status'] == 'installed'
    assert store.list_keys()[0]['key_id'] == value['bindings'][0]['key_id']
    assert client.run('readback', declaration)['configuration_status'] == 'installed'


@pytest.mark.skipif(os.name != 'posix', reason='native offline custody requires POSIX producer/writer fences')
@pytest.mark.parametrize('gate', ['producer', 'writer'])
def test_competing_owner_refuses_before_enrollment_publication(tmp_path, gate, empty_history_owner):
    import fcntl
    store, declaration, value, _, _ = fixture(tmp_path)
    path = store.path.parent / 'admission.json.router.lock' if gate == 'producer' else Path(str(store.path) + '.router-writers.lock')
    with path.open('w') as stream:
        path.chmod(0o600); fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(KeyStoreError):
            client._run_offline('install', declaration, confirm=True)
    assert not Path(value['installed_path']).exists()
    with store._connect() as db:
        assert db.execute('SELECT count(*) FROM key_owner_bindings').fetchone() == (0,)


@pytest.mark.skipif(os.name != 'posix', reason='native offline custody requires POSIX producer/writer fences')
@pytest.mark.parametrize('commit', [True, False])
def test_actual_child_pending_pipe_holds_custody_until_verified_ack(tmp_path, commit, empty_history_owner):
    store, declaration, value, job, credential = fixture(tmp_path)
    code = ('from pathlib import Path; from anvil_serving import client_identity as c; '
            'from anvil_serving.router.usage_store import RunOwner; '
            f'RunOwner.observe=lambda *a,**k: RunOwner(**{asdict(empty_history_owner)!r}); '
            f'c.WORKER_ROOT=Path({str(declaration.parent)!r}); '
            f'c._native_offline_clients("install",{str(declaration)!r},confirm=True)')
    child = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert select.select([child.stdout], [], [], 10)[0]
        raw = child.stdout.readline()
        assert raw, child.stderr.read().decode()
        pending = json.loads(raw)
        assert pending == {'pending_sha256': hashlib.sha256(canonical(job)).hexdigest(), 'run_id': job['declaration_sha256']}
        assert not Path(value['installed_path']).exists()
        with pytest.raises((KeyStoreError, BlockingIOError)):
            store.bind_owner(value['bindings'][0]['key_id'], 'human', 'foreign', 0)
        child.stdin.write(json.dumps({'commit': pending['pending_sha256'] if commit else '0' * 64}).encode() + b'\n')
        child.stdin.flush()
        output, error = child.communicate(timeout=10)
        assert credential.encode() not in output + error
        if commit:
            assert child.returncode == 0
            result = json.loads(output)['result']
            assert result['configuration_status'] == 'installed' and result['clients_count'] == 1
            assert result['recipients_staged'] == 0 and result['live_status'] == 'unqualified'
        else:
            assert child.returncode != 0 and not Path(value['installed_path']).exists()
    finally:
        if child.poll() is None:
            child.terminate(); child.wait(timeout=5)
        assert child.poll() is not None


def test_native_readback_samples_actual_expiry_grants_and_binding_without_token_output(tmp_path, monkeypatch):
    store, declaration, value, job, credential = fixture(tmp_path)
    monkeypatch.setattr(client, 'WORKER_ROOT', declaration.parent)
    client.run('install', declaration, confirm=True)
    payload = {'action': 'client-readback', 'store_path': str(store.path), 'declaration_path': str(declaration),
               'declaration_sha256': job['declaration_sha256'],
               'router_config_sha256': hashlib.sha256(Path(value['router_config']).read_bytes()).hexdigest(),
               'client_id': 'synthetic-client', 'credential': credential}
    before = client._native_readback(store, payload)
    assert before['credential_validated'] is True and credential not in json.dumps(before)
    assert hashlib.sha256(credential.encode()).hexdigest() not in json.dumps(before)
    with store._write() as db:
        db.execute('UPDATE keys SET models=?', (json.dumps(['llm.secondary']),))
    after = client._native_readback(store, payload)
    assert before['grant_sha256'] != after['grant_sha256']
    with store._write() as db:
        db.execute('UPDATE keys SET expires_at=0')
    with pytest.raises(ValueError): client._native_readback(store, payload)
    with store._write() as db:
        db.execute('UPDATE keys SET expires_at=NULL,models=?', (json.dumps(['*']),))
    with pytest.raises(ValueError): client._native_readback(store, payload)
    with store._write() as db:
        db.execute('UPDATE keys SET models=?', (json.dumps(['llm.primary']),))
    store.bind_owner(value['bindings'][0]['key_id'], 'human', 'replacement', 1)
    with pytest.raises(ValueError): client._native_readback(store, payload)


@pytest.mark.parametrize('defect', ['unknown', 'relative', 'image', 'root', 'helper', 'directory'])
def test_protected_namespace_and_fixed_job_refuse_unknown_or_unbound_inputs(tmp_path, monkeypatch, defect):
    _, declaration, value, job, _ = fixture(tmp_path)
    namespace = {'schema': client.NAMESPACE_SCHEMA, 'compose': str(tmp_path / 'worker.yml'),
                 'container': 'anvil-router', 'expected_image_id': 'sha256:' + 'a' * 64,
                 'declaration_path': '/run/anvil-client-worker/client-identity.json'}
    if defect == 'unknown': namespace['callback'] = 'forbidden'
    elif defect == 'relative': namespace['compose'] = 'relative.yml'
    elif defect == 'image': namespace['expected_image_id'] = 'unbound'
    elif defect == 'root': job['material_directories'] = ['/']
    elif defect == 'helper': job['helper_sha256']['webui'] = 'a' * 64
    elif defect == 'directory': job['material_directories'] = [str(Path(value['router_config']).parent)]
    save(declaration.parent / 'client-namespace.json', namespace)
    if defect in {'unknown', 'relative', 'image'}:
        with pytest.raises(ValueError): client._namespace(declaration)
    else:
        monkeypatch.setattr(client, 'WORKER_ROOT', declaration.parent)
        save(declaration.parent / 'worker.json', job)
        with pytest.raises(ValueError): client._worker_job(declaration, *client._load(declaration))
