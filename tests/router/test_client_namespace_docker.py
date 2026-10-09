"""Owned offline enrollment in a real, distinct Docker SQLite namespace."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid

import pytest

from anvil_serving import client_identity as client, router_manage
from anvil_serving.observability.dashboard.contracts import canonical
from tests.router.key_fixtures import tmp_path as tmp_path

pytestmark = pytest.mark.skipif(os.environ.get('ANVIL_ROUTER_DOCKER_TESTS') != '1',
                               reason='explicit owned Docker qualification required')


def save(path, value):
    path.write_text(json.dumps(value)); path.chmod(0o600)


@pytest.fixture
def native_clients(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    build = tmp_path / 'build'; build.mkdir(mode=0o700)
    shutil.copytree(root / 'anvil_serving', build / 'anvil_serving', ignore=shutil.ignore_patterns('__pycache__'))
    base, expected = os.environ['ANVIL_ROUTER_DOCKER_BASE'], os.environ['ANVIL_ROUTER_DOCKER_BASE_ID']
    assert re.fullmatch(r'[A-Za-z0-9_./:-]+', base) and not base.startswith('-')
    assert re.fullmatch(r'sha256:[a-f0-9]{64}', expected)
    assert subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', base], text=True).strip() == expected
    tag = 'ruu-clients-test:' + uuid.uuid4().hex
    volume = 'ruu-clients-' + uuid.uuid4().hex
    name = 'ruu-clients-' + uuid.uuid4().hex
    (build / 'Dockerfile').write_text('FROM ' + base + '\nUSER 0:0\n'
        'RUN mkdir -p /var/lib/anvil-serving/router-keys && chown 1000:1000 /var/lib/anvil-serving/router-keys && chmod 700 /var/lib/anvil-serving/router-keys\n'
        'RUN chown 0:0 /etc/anvil && chmod 755 /etc/anvil\n'
        'COPY --chown=1000:1000 anvil_serving /fixture-source/anvil_serving\n'
        'ENV PYTHONPATH=/fixture-source\nUSER 1000:1000\n')
    monkeypatch.setattr(router_manage, 'DEFAULT_COMPOSE_PROJECT', name)
    monkeypatch.setattr(router_manage, 'DEFAULT_CONTAINER', name)
    monkeypatch.setenv('ANVIL_SERVING_HOME', str(tmp_path / 'operator-home'))
    owned = []
    try:
        subprocess.run(['docker', 'build', '--pull=false', '--network=none', '-t', tag, str(build)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=90)
        image = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], text=True).strip()
        subprocess.run(['docker', 'volume', 'create', volume], check=True, stdout=subprocess.DEVNULL, timeout=10)
        code = ('import json; from anvil_serving.router.keys import KeyStore; '
                'from anvil_serving.router.usage_store import UsageStore; '
                's=KeyStore.initialize("/var/lib/anvil-serving/router-keys/keys.sqlite3"); UsageStore(s).migrate(); '
                'm,t=s.create("synthetic enrollment",["llm.primary"],["/v1/chat/completions"]); '
                'print(json.dumps({"key_id":m["key_id"],"credential":t}))')
        issued = json.loads(subprocess.check_output(['docker', 'run', '--rm', '--network=none', '--read-only',
            '--label=com.docker.compose.project=' + name, '--label=com.docker.compose.service=router',
            '--cpus=2', '--memory=512m', '--memory-swap=512m', '--pids-limit=64', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '-v', volume + ':/var/lib/anvil-serving/router-keys',
            '--entrypoint=python', tag, '-c', code], timeout=30))
        owned = []
        yield {'root': tmp_path, 'tag': tag, 'volume': volume, 'name': name, 'image': image, 'owned': owned, **issued}
    finally:
        # Only this fixture's exact unique image/volume names and its verified
        # one-off labels are eligible for cleanup. No real store is mounted.
        ids = subprocess.check_output(['docker', 'ps', '--all', '--quiet', '--no-trunc',
            '--filter', 'label=com.docker.compose.project=' + name], text=True, timeout=10).split()
        for cid in dict.fromkeys(ids + owned):
            assert re.fullmatch(r'[a-f0-9]{64}', cid)
            assert subprocess.check_output(['docker', 'inspect', '--format', '{{.Image}}', cid], text=True, timeout=5).strip() == image
            subprocess.run(['docker', 'rm', '-f', cid], check=True, stdout=subprocess.DEVNULL, timeout=15)
        subprocess.run(['docker', 'volume', 'rm', volume], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        subprocess.run(['docker', 'image', 'rm', tag], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)


@pytest.mark.parametrize('webui', [False, True])
def test_supported_offline_worker_actual_namespace_publication_auth_and_expiry(native_clients, webui, monkeypatch):
    f = native_clients
    material = f['root'] / 'material'; material.mkdir(mode=0o700)
    worker = f['root'] / 'worker'; worker.mkdir(mode=0o700)
    router = f['root'] / 'router.toml'
    config = '[server]\nauth_env="SYNTHETIC_MASTER"\napi_keys_path="/var/lib/anvil-serving/router-keys/keys.sqlite3"\n'
    config += 'admission_state_path="/var/lib/anvil-serving/router-keys/admission.json"\nrouter_owner_backend="managed-container"\n'
    config += 'router_owner_id="synthetic-owner"\nrouter_owner_roster=["synthetic-owner"]\nusage_domain_id="synthetic-domain"\n'
    if webui:
        config += '[[server.webui_identity]]\ncredential_id=' + json.dumps(f['key_id']) + '\ncredential_kind="device_key"\ninstance="synthetic"\nsigner_file=' + json.dumps(str(material / 'signer')) + '\n'
    router.write_text(config); router.chmod(0o600)
    # Initial issuance uses only the mounted config, named authority volume
    # and exclusive material parent. It precedes any client declaration/job.
    issuance = f['root'] / 'issuance.json'
    save(issuance, {'name': f['name'], 'services': {'router': {'image': f['tag'], 'container_name': f['name'],
        'network_mode': 'none', 'read_only': True, 'init': True, 'user': '1000:1000', 'cpus': 2,
        'mem_limit': 512*1024**2, 'memswap_limit': 512*1024**2, 'pids_limit': 64,
        'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'], 'restart': 'no',
        'entrypoint': ['/bin/false'], 'command': [], 'volumes': [
            {'type': 'volume', 'source': 'keys', 'target': '/var/lib/anvil-serving/router-keys'},
            {'type': 'bind', 'source': str(router), 'target': router_manage.DEFAULT_INSTALLED_CONFIG, 'read_only': True},
            {'type': 'bind', 'source': str(material), 'target': str(material)}]}},
        'volumes': {'keys': {'name': f['volume']}}})
    from anvil_serving.router.keys import KeyStore
    def no_host_store(*args, **kwargs):
        raise AssertionError('host must not open the namespaced key store')
    monkeypatch.setattr(KeyStore, '__init__', no_host_store)
    request = {'name': 'unique synthetic client', 'model': ['llm.primary'],
               'path': ['/v1/chat/completions'], 'rpm': 60, 'expires_days': None,
               'out': str(material / 'issued-credential')}
    with tempfile.TemporaryFile() as error:
        def native_popen(argv, **options):
            options['stderr'] = error
            return subprocess.Popen(argv, **options)
        try:
            metadata = router_manage.create_key_offline(str(issuance), request, _popen=native_popen)
        except BaseException:
            error.seek(0); raw = error.read(65537)
            # Owned synthetic worker only; retain numeric/type failure context,
            # never source lines, arguments or credential contents.
            lines = raw.decode('utf-8', errors='replace').splitlines()
            exceptions = [line.split(':', 1)[0] for line in lines if re.fullmatch(r'[A-Za-z_.]*Error:.*', line)]
            print('NATIVE_ISSUANCE_FAILURE ' + json.dumps({'stderr_sha256': hashlib.sha256(raw).hexdigest(),
                'exception_type': exceptions[-1:] if exceptions else None,
                'safe_auth_refusal': [line for line in lines if line.startswith('anvil_serving.control_plane.mcp.auth_file.AuthFileError:')],
                'mount_guard': [line for line in lines if line.startswith(('FIXTURE_MOUNT_METADATA ', 'FIXTURE_GUARD_METADATA '))],
                'stage': re.findall(r'in ([A-Za-z_][A-Za-z_0-9]*)\n', raw.decode('utf-8', errors='replace'))[-1:]}))
            raise
    new_secret = Path(request['out']).read_text().strip()
    assert metadata['key_id'] != f['key_id'] and new_secret != f['credential']
    assert new_secret not in json.dumps(metadata)
    assert metadata['models'] == ['llm.primary'] and metadata['paths'] == ['/v1/models', '/v1/chat/completions']
    with pytest.raises(ValueError):
        router_manage.create_key_offline(str(issuance), request)
    assert Path(request['out']).read_text().strip() == new_secret
    if webui:
        config = config.replace(json.dumps(f['key_id']), json.dumps(metadata['key_id']))
        router.write_text(config)
    f['key_id'], f['credential'] = metadata['key_id'], new_secret
    origin = router.with_name('client-router-origin.json'); save(origin, {'origin': 'https://router.invalid/v1'})
    declaration = {'schema': client.SCHEMA, 'router_config': str(router), 'installed_path': str(material / 'bindings.json'),
        'bindings': [{'client_id': 'synthetic-client', 'key_id': f['key_id'], 'kind': 'service' if webui else 'human',
                      'owner_id': 'synthetic:client', 'expected_revision': 0, 'policy': 'webui' if webui else 'direct'}], 'webui': []}
    if webui:
        declaration['webui'] = [{'client_id': 'synthetic-client', 'instance': 'synthetic',
            'native': {'openai.api_base_urls': ['https://router.invalid/v1'],
                       'openai.api_configs': {'0': {'enable': True, 'custom_header_names': []}}, 'other_recipients': []},
            'approved_recipients': ['https://router.invalid/v1'], 'request_paths': {p: [0] for p in client.PATHS}}]
        credential = material / 'service-credential'; credential.write_text(f['credential']); credential.chmod(0o600)
    path = worker / 'client-identity.json'; save(path, declaration)
    job = {'schema': client.WORKER_SCHEMA, 'declaration_sha256': hashlib.sha256(canonical(declaration)).hexdigest(),
           'helper_sha256': {'webui': None, 'recipient': None}, 'recipients': [], 'material_directories': [str(material)]}
    mounts = [{'type': 'volume', 'source': 'keys', 'target': '/var/lib/anvil-serving/router-keys'},
              {'type': 'bind', 'source': str(material), 'target': str(material)},
              *[{'type': 'bind', 'source': str(p), 'target': str(p), 'read_only': True} for p in (router, origin)],
              {'type': 'bind', 'source': str(router), 'target': router_manage.DEFAULT_INSTALLED_CONFIG, 'read_only': True},
              {'type': 'bind', 'source': str(path), 'target': '/run/anvil-client-worker/client-identity.json', 'read_only': True},
              {'type': 'bind', 'source': str(worker / 'worker.json'), 'target': '/run/anvil-client-worker/worker.json', 'read_only': True}]
    if webui:
        helper = Path(os.environ['ANVIL_CLIENT_TEST_INFRA_ROOT']) / 'modern/scripts/webui-integrations.py'
        copied = worker / helper.name; copied.write_bytes(helper.read_bytes()); copied.chmod(0o600)
        job['helper_sha256']['webui'] = hashlib.sha256(copied.read_bytes()).hexdigest()
        mounts.append({'type': 'bind', 'source': str(copied), 'target': '/run/anvil-client-worker/' + helper.name, 'read_only': True})
    if not webui:
        stage = f['root'] / 'staging'; stage.mkdir(mode=0o700)
        device = material / 'device-credential'; device.write_text(f['credential']); device.chmod(0o600)
        home = f['root'] / 'recipient-home'
        recipient = {'client_id': 'synthetic-client', 'host': 'synthetic', 'kind': 'pi', 'home': str(home),
            'profile': 'default', 'source_file': str(device),
            'destination_file': str(home / '.config/anvil-serving/pi/router-key'),
            'reference_file': str(home / '.pi/agent/models.json'), 'reference_sha256': 'a' * 64,
            'environment_name': None, 'expected_sha256': None, 'python': '/usr/local/bin/python3',
            'router_url': 'https://router.invalid/v1'}
        helper = Path(os.environ['ANVIL_CLIENT_TEST_INFRA_ROOT']) / 'modern/scripts/configure-pi.py'
        copied = worker / helper.name; copied.write_bytes(helper.read_bytes()); copied.chmod(0o600)
        job['helper_sha256']['recipient'] = hashlib.sha256(copied.read_bytes()).hexdigest()
        job['material_directories'].append(str(stage))
        job['recipients'] = [{'declaration': recipient, 'stage_directory': str(stage)}]
        mounts.extend([{'type': 'bind', 'source': str(stage), 'target': str(stage)},
            {'type': 'bind', 'source': str(copied), 'target': '/run/anvil-client-worker/' + helper.name, 'read_only': True}])
    save(worker / 'worker.json', job)
    compose = f['root'] / 'compose.json'
    save(compose, {'name': f['name'], 'services': {'router': {'image': f['tag'], 'container_name': f['name'],
        'network_mode': 'none', 'read_only': True, 'init': True, 'user': '1000:1000', 'cpus': 2,
        'mem_limit': 512*1024**2, 'memswap_limit': 512*1024**2, 'pids_limit': 64,
        'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'], 'restart': 'no',
        'entrypoint': ['/bin/false'], 'command': [], 'volumes': mounts}}, 'volumes': {'keys': {'name': f['volume']}}})
    namespace = {'compose': str(compose), 'container': f['name'], 'expected_image_id': f['image'],
                 'declaration_path': '/run/anvil-client-worker/client-identity.json'}
    def native_run(argv, **kwargs):
        result = subprocess.run(argv, **kwargs)
        if result.returncode:
            error = result.stderr or ''
            print('NATIVE_CLIENT_FAILURE ' + json.dumps({'operation': argv[1] if len(argv) > 1 else None,
                'exit_code': result.returncode, 'stdout_bytes': len(result.stdout or ''),
                'stderr_sha256': hashlib.sha256(error.encode()).hexdigest(),
                'missing_object': bool(re.fullmatch(r'(?:Error(?: response from daemon)?: )?No such (?:object|container): [a-f0-9]+\s*', error))}))
        return result
    preview = router_manage.enroll_clients_offline(namespace, 'preview', job['declaration_sha256'], _run=native_run)
    assert preview['configuration_status'] == 'incomplete' and preview['recipients_staged'] == 0
    assert not (material / 'bindings.json').exists()
    if not webui:
        assert not (stage / 'recipient').exists()
    result = router_manage.enroll_clients_offline(namespace, 'install', job['declaration_sha256'], confirm=True, _run=native_run)
    assert result['configuration_status'] == 'installed' and result['live_status'] == 'unqualified'
    assert f['credential'] not in json.dumps(result)
    assert (material / 'bindings.json').exists()
    if not webui:
        assert result['recipients_staged'] == 1 and (stage / 'recipient').read_text().strip() == f['credential']
    if webui:
        assert (material / 'approval.json').exists() and (material / 'signer').stat().st_mode & 0o777 == 0o600
    auth = {'credential': f['credential'], 'client_id': 'synthetic-client',
            'router_config_sha256': hashlib.sha256(router.read_bytes()).hexdigest()}
    fresh = router_manage.enroll_clients_offline(namespace, 'readback', job['declaration_sha256'], _client_auth=auth, _run=native_run)
    assert fresh['credential_validated'] is True
    # Host callers validate through the actual private Docker exec transport.
    # This owned inert process exercises the namespace boundary, not live router
    # readiness or admission. It has only the individual required readonly files.
    live_mounts = [('volume', f['volume'], '/var/lib/anvil-serving/router-keys', False),
                   ('bind', str(path), '/run/anvil-client-worker/client-identity.json', True),
                   ('bind', str(router), str(router), True),
                   ('bind', str(material / 'bindings.json'), str(material / 'bindings.json'), True)]
    argv = ['docker', 'run', '--detach', '--rm', '--name', f['name'], '--network=none', '--read-only',
            '--label=com.docker.compose.project=anvil-serving', '--label=com.docker.compose.service=router',
            '--cpus=2', '--memory=512m', '--memory-swap=512m', '--pids-limit=64', '--cap-drop=ALL',
            '--security-opt=no-new-privileges']
    for kind, source, target, readonly in live_mounts:
        argv.extend(['--mount', f'type={kind},source={source},target={target}' + (',readonly' if readonly else '')])
    cid = subprocess.check_output([*argv, '--entrypoint=python', f['tag'], '-c',
                                  'import time; time.sleep(60)'], text=True, timeout=15).strip()
    f['owned'].append(cid)
    running = client._managed('readback', path, namespace, credential=f['credential'], client_id='synthetic-client')
    assert running['credential_validated'] is True and running['grant_sha256'] == fresh['grant_sha256']
    assert f['credential'] not in json.dumps(running)
    expire = 'from anvil_serving.router.keys import KeyStore; s=KeyStore("/var/lib/anvil-serving/router-keys/keys.sqlite3");\nwith s._write() as db: db.execute("UPDATE keys SET expires_at=0")'
    subprocess.run(['docker', 'exec', cid, 'python', '-c', expire], check=True, timeout=15,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    with pytest.raises((ValueError, client.KeyStoreError)):
        client._managed('readback', path, namespace, credential=f['credential'], client_id='synthetic-client')
    subprocess.run(['docker', 'rm', '--force', cid], check=True, stdout=subprocess.DEVNULL, timeout=15)
    assert router_manage.docker_state(cid) == 'absent'
    f['owned'].remove(cid)
    before = (material / 'bindings.json').read_bytes()
    with pytest.raises(ValueError):
        router_manage.enroll_clients_offline(namespace, 'readback', job['declaration_sha256'], _client_auth=auth, _run=native_run)
    assert (material / 'bindings.json').read_bytes() == before
