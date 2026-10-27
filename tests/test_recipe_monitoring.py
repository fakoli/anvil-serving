"""Registration follows exact lifecycle ownership, never a reusable port alone."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from anvil_serving import recipe_monitoring as m, serve_recipes


@pytest.fixture(autouse=True)
def local_docker(monkeypatch):
    monkeypatch.setattr(serve_recipes.recipe_memory, '_docker_endpoint',
                        lambda run: 'unix:///var/run/docker.sock')


def test_registration_transition_and_guards(tmp_path, monkeypatch):
    monkeypatch.setenv('ANVIL_SERVING_HOME', str(tmp_path))
    config = {'schema': m.SCHEMA, 'ports': [30000, 30001],
              'engine_contracts': {'vllm': 'vllm', 'flare-pinned': 'flare'}}
    (tmp_path / 'recipe-monitoring.json').write_text(json.dumps(config))
    recipe = {'model': 'example/model', 'serve': {'engine': 'vllm', 'port': 30000}}
    row = {'container': 'candidate', 'container_id': 'a' * 64,
           'image_digest': 'sha256:' + 'b' * 64, 'recipe_digest': serve_recipes.recipe_digest(recipe),
           'registry_digest': 'c' * 64, 'model': 'example/model', 'served_identity': 'served', 'bound_port': 30000,
           'bound_ports': [30000], 'bound_endpoints': [{'host_ip': '127.0.0.1', 'host_port': 30000, 'container_port': 30000}], 'gpu_selection': ['GPU-example'], 'running': True,
           'started_at': '2026-10-21T00:00:00Z'}
    inventory = {'containers': [row, row | {'container': 'retained', 'running': False}]}
    def fetch(path):
        return b'{}' if path == '/health' else b'{"data":[{"id":"served"}]}'
    with patch.object(serve_recipes, 'discover_recipe_containers', return_value=inventory):
        m.withdraw(port=30000, container='candidate', pending=True)
        active = m.publish(recipe, 'candidate', registry_digest='c' * 64, _fetch=fetch)
        assert active['model'] == 'served' and active['metrics_contract'] == 'vllm'
        assert m.read_json(m.receipt_path()) == active
        m.withdraw(container='unrelated')
        assert m.read_json(m.receipt_path()) == active
        m.withdraw(container='candidate')
        assert m.read_json(m.receipt_path())['state'] == 'withdrawn'
        with pytest.raises(ValueError, match='withdrawn'):
            m.publish(recipe, 'candidate', registry_digest='c' * 64, _fetch=fetch)
        m.withdraw(port=30000, container='candidate', pending=True)
        inventory['containers'].append(row | {'container': 'competing'})
        with pytest.raises(ValueError, match='exactly one'):
            m.publish(recipe, 'candidate', registry_digest='c' * 64, _fetch=fetch)
        inventory['containers'].pop()
        with pytest.raises(ValueError, match='identity, digest'):
            m.publish(recipe, 'candidate', registry_digest='f' * 64, _fetch=fetch)
        with pytest.raises(ValueError, match='live model'):
            m.publish(recipe, 'candidate', registry_digest='c' * 64, _fetch=lambda p: b'{"data":[{"id":"wrong"}]}')
        def raced_fetch(path):
            if path == '/v1/models':
                m.withdraw(container='candidate')
            return fetch(path)
        with pytest.raises(ValueError, match='changed during'):
            m.publish(recipe, 'candidate', registry_digest='c' * 64, _fetch=raced_fetch)
        assert m.read_json(m.receipt_path())['state'] == 'withdrawn'
        recipe['serve'].update(engine='flare-pinned', port=30001)
        row.update(bound_port=30001, bound_ports=[30001], bound_endpoints=[{'host_ip': '127.0.0.1', 'host_port': 30001, 'container_port': 30001}], recipe_digest=serve_recipes.recipe_digest(recipe))
        m.withdraw(port=30001, container='candidate', pending=True)
        flare = m.publish(recipe, 'candidate', registry_digest='c' * 64, _fetch=fetch)
        assert flare['metrics_contract'] == 'flare' and flare['generation'] != active['generation']
    m.receipt_path().unlink()
    m.receipt_path().symlink_to(tmp_path / 'recipe-monitoring.json')
    with pytest.raises(OSError):
        m.read_json(m.receipt_path())


def test_disabled_and_invalid_monitoring(tmp_path, monkeypatch):
    monkeypatch.setenv('ANVIL_SERVING_HOME', str(tmp_path))
    assert m.withdraw(port=30000) is None
    assert m.publish({'serve': {}}, 'candidate') is None
    (tmp_path / 'recipe-monitoring.json').write_text('{}')
    with pytest.raises(ValueError):
        m.withdraw(port=30000)
    assert not m.receipt_path().exists()


@pytest.mark.parametrize('listed', ['not-an-id\n', ('a' * 12 + '\n') * 2])
def test_strict_discovery_rejects_corrupt_owner_list(listed):
    with pytest.raises(ValueError, match='invalid or duplicate'):
        serve_recipes.discover_recipe_containers(strict=True,
            _run=lambda *a, **k: SimpleNamespace(returncode=0, stdout=listed))


def test_load_readiness_and_unload_failure_with_enabled_monitoring(tmp_path, monkeypatch):
    from anvil_serving import cli, models
    monkeypatch.setenv('ANVIL_SERVING_HOME', str(tmp_path))
    (tmp_path / 'recipe-monitoring.json').write_text(json.dumps({
        'schema': m.SCHEMA, 'ports': [30000], 'engine_contracts': {'vllm': 'vllm'}}))
    registry = tmp_path / 'recipes.toml'
    registry.write_text('''[[recipe]]
model = "vendor/model"
[recipe.serve]
engine = "vllm"
image = "example/model:pinned"
port = 30000
''')
    recipe = serve_recipes.load_registry(registry)['recipe'][0]
    row = {'container': 'candidate', 'container_id': 'a' * 64,
           'image_digest': 'sha256:' + 'b' * 64, 'recipe_digest': serve_recipes.recipe_digest(recipe),
           'registry_digest': serve_recipes.registry_digest(registry), 'model': 'vendor/model', 'served_identity': 'vendor/model',
           'bound_port': 30000, 'bound_ports': [30000], 'bound_endpoints': [{'host_ip': '127.0.0.1', 'host_port': 30000, 'container_port': 30000}], 'gpu_selection': ['GPU-example'],
           'running': True, 'started_at': '2026-10-21T00:00:00Z'}
    real_load, real_publish = serve_recipes.load_recipe, m.publish
    docker_calls = []
    def fake_run(argv, **kwargs):
        docker_calls.append(argv)
        return SimpleNamespace(returncode=0, stdout='a' * 64)
    monkeypatch.setattr(serve_recipes, 'load_recipe', lambda *a, **k: real_load(*a, **k, _run=fake_run))
    monkeypatch.setattr(serve_recipes, 'discover_recipe_containers', lambda **k: {'containers': [row]})
    monkeypatch.setattr(m, 'publish', lambda *a, **k: real_publish(*a, **k,
        _fetch=lambda p: b'{"data":[{"id":"vendor/model"}]}'))
    monkeypatch.setattr(models, '_await_recipe_container_healthy', lambda *a: (False, 'exited'))
    args = ['models', 'recipes', 'load', 'vendor/model', '--registry', str(registry),
            '--container', 'candidate', '--gpu-device', '0']
    assert cli.main(args + ['--dry-run']) == 0
    assert not m.receipt_path().exists() and not docker_calls
    assert cli.main(args + ['--confirm']) == 1
    assert m.read_json(m.receipt_path())['state'] == 'pending'
    monkeypatch.setattr(models, '_await_recipe_container_healthy', lambda *a: (True, 'running'))
    assert cli.main(args + ['--confirm']) == 0
    assert m.read_json(m.receipt_path())['state'] == 'active'
    identity = {'container': 'candidate', 'container_id': 'a' * 64,
                'model': 'vendor/model', 'state': 'running', 'native_kv_offload': False}
    monkeypatch.setattr(models, '_recheck_discovered_recipe_container', lambda *a, **k: identity)
    assert models._discovered_recipe_container_unload(identity, confirm=True,
        _run=lambda *a, **k: SimpleNamespace(returncode=1, stdout='', stderr='synthetic failure')) == 1
    assert m.read_json(m.receipt_path())['state'] == 'withdrawn'


def monitoring_slot(tmp_path, monkeypatch, engine='ignis'):
    monkeypatch.setattr(serve_recipes.recipe_memory, '_docker_endpoint', lambda run: 'unix:///var/run/docker.sock')
    monkeypatch.setenv('ANVIL_SERVING_HOME', str(tmp_path))
    (tmp_path / 'recipe-monitoring.json').write_text(json.dumps({
        'schema': m.SCHEMA, 'ports': [30000], 'engine_contracts': {engine: engine}}))
    recipe = {'model': 'example/model', 'serve': {'engine': engine, 'port': 30000}}
    row = {'container': 'candidate', 'container_id': 'a' * 64, 'model': recipe['model'],
           'image_digest': 'sha256:' + 'b' * 64, 'recipe_digest': serve_recipes.recipe_digest(recipe),
           'registry_digest': 'c' * 64, 'served_identity': 'served', 'bound_port': 30000,
           'bound_ports': [30000], 'gpu_selection': ['GPU-example'], 'running': True,
           'started_at': '2026-10-24T00:00:00Z',
           'bound_endpoints': [{'host_ip': '127.0.0.1', 'host_port': 30000, 'container_port': 30000}]}
    monkeypatch.setattr(serve_recipes, 'discover_recipe_containers', lambda **kw: {'containers': [row]})
    m.withdraw(port=30000, container='candidate', pending=True)
    return recipe, row


@pytest.mark.parametrize('reported_model', ['qwen3.8-flash-next', 'wrong-model'])
def test_model_id_discovery_binds_monitoring_publication(tmp_path, monkeypatch, reported_model):
    from tests.test_recipe_container_discovery import _row
    real_discover = serve_recipes.discover_recipe_containers
    recipe, _ = monitoring_slot(tmp_path, monkeypatch)
    monkeypatch.setattr(serve_recipes, 'discover_recipe_containers', real_discover)
    before = m.read_json(m.receipt_path())
    row = _row('candidate', container_id='a' * 64, model=recipe['model'],
               labels={serve_recipes.RECIPE_DIGEST_LABEL: serve_recipes.recipe_digest(recipe)})
    row['Args'] = ['/weights/model', '--model-id', 'qwen3.8-flash-next']
    row['HostConfig']['PortBindings'] = {
        '30000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '30000'}]}
    row['State']['StartedAt'] = '2026-10-24T00:00:00Z'
    docker_calls, fetch_calls = [], []
    def run(argv, **kwargs):
        docker_calls.append(argv[1])
        assert argv[:2] in (['docker', 'ps'], ['docker', 'inspect'])
        return SimpleNamespace(returncode=0,
            stdout='a' * 64 + '\n' if argv[1] == 'ps' else json.dumps([row]))
    def fetch(path):
        fetch_calls.append(path)
        return b'{}' if path == '/ready' else json.dumps({'data': [{'id': reported_model}]}).encode()
    if reported_model == 'qwen3.8-flash-next':
        active = m.publish(recipe, 'candidate', registry_digest='c' * 64, _run=run, _fetch=fetch)
        assert active['state'] == 'active'
        assert active['model'] == 'qwen3.8-flash-next'
        assert active['metrics_contract'] == 'ignis'
        assert active['container_id'] == 'a' * 64
        assert m.read_json(m.receipt_path()) == active
        assert docker_calls == ['ps', 'inspect', 'ps', 'inspect']
    else:
        with pytest.raises(ValueError, match='monitoring live model identity differs from recipe container'):
            m.publish(recipe, 'candidate', registry_digest='c' * 64, _run=run, _fetch=fetch)
        assert m.read_json(m.receipt_path()) == before
        assert before['state'] == 'pending'
        assert docker_calls == ['ps', 'inspect']
    assert fetch_calls == ['/ready', '/v1/models']


@pytest.mark.parametrize('engine', ['vllm', 'sglang', 'flare', 'ignis', 'llamacpp'])
def test_exact_binding_single_port_receipt(tmp_path, monkeypatch, engine):
    recipe, row = monitoring_slot(tmp_path, monkeypatch, engine)
    calls = []
    def fetch(path):
        calls.append(path)
        return b'{"data":[{"id":"served"}]}'
    receipt = m.publish(recipe, 'candidate', registry_digest='c' * 64, _fetch=fetch)
    assert calls == ['/ready' if engine == 'ignis' else '/health', '/v1/models']
    assert receipt['metrics_contract'] == engine and receipt['bound_port'] == 30000
    assert receipt['bound_endpoints'] == row['bound_endpoints']
    assert 'metrics_port' not in receipt


@pytest.mark.parametrize('endpoints', [
    [], [{'host_ip': '0.0.0.0', 'host_port': 30000, 'container_port': 30000}],
    [{'host_ip': '127.0.0.2', 'host_port': 30000, 'container_port': 30000}],
    [{'host_ip': '::1', 'host_port': 30000, 'container_port': 30000}],
    [{'host_ip': '127.0.0.1', 'host_port': 30001, 'container_port': 30000}],
    [{'host_ip': '127.0.0.1', 'host_port': 30000, 'container_port': 30001}],
    [{'host_ip': '127.0.0.1', 'host_port': 30000, 'container_port': 30000}] * 2,
])
def test_invalid_binding_refuses_before_probe(tmp_path, monkeypatch, endpoints):
    recipe, row = monitoring_slot(tmp_path, monkeypatch)
    row['bound_endpoints'] = endpoints
    def unexpected(path):
        pytest.fail('invalid binding must not be probed')
    with pytest.raises(ValueError, match='identity, digest, port'):
        m.publish(recipe, 'candidate', registry_digest='c' * 64, _fetch=unexpected)
    assert m.read_json(m.receipt_path())['state'] == 'pending'


@pytest.mark.parametrize('field,value', [
    ('container_id', 'd' * 64), ('image_digest', 'sha256:' + 'd' * 64),
    ('recipe_digest', 'd' * 64), ('registry_digest', 'd' * 64),
    ('served_identity', 'another-model'), ('started_at', '2026-10-24T00:01:00Z'),
    ('gpu_selection', ['GPU-other']),
    ('bound_endpoints', [{'host_ip': '127.0.0.2', 'host_port': 30000, 'container_port': 30000}]),
])
def test_identity_changes_during_probe_withhold_publication(tmp_path, monkeypatch, field, value):
    recipe, row = monitoring_slot(tmp_path, monkeypatch)
    def fetch(path):
        if path == '/v1/models':
            row[field] = value
        return b'{"data":[{"id":"served"}]}'
    with pytest.raises(ValueError):
        m.publish(recipe, 'candidate', registry_digest='c' * 64, _fetch=fetch)
    assert m.read_json(m.receipt_path())['state'] == 'pending'


def test_mutated_nested_identity_is_not_shared_with_snapshot(tmp_path, monkeypatch):
    recipe, row = monitoring_slot(tmp_path, monkeypatch)
    def fetch(path):
        row['gpu_selection'][0] = 'GPU-other'
        return b'{"data":[{"id":"served"}]}'
    with pytest.raises(ValueError, match='changed'):
        m.publish(recipe, 'candidate', registry_digest='c' * 64, _fetch=fetch)


@pytest.mark.parametrize('kind', ['duplicate', 'udp', 'missing_ip'])
def test_strict_discovery_rejects_ambiguous_bindings(kind):
    bindings = {'30000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '30000'}]}
    if kind == 'duplicate':
        bindings['30000/tcp'] *= 2
    elif kind == 'udp':
        bindings['30000/udp'] = bindings.pop('30000/tcp')
    else:
        bindings['30000/tcp'][0].pop('HostIp')
    responses = iter(['a' * 64, json.dumps([{'State': {'Running': True},
                                          'HostConfig': {'PortBindings': bindings}}])])
    with pytest.raises(ValueError, match='exact TCP bindings'):
        serve_recipes.discover_recipe_containers(strict=True,
            _run=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=next(responses)))


def test_monitor_sync_preview_and_confirmation(tmp_path, monkeypatch, capsys):
    from anvil_serving import cli
    recipe, row = monitoring_slot(tmp_path, monkeypatch)
    registry = tmp_path / 'recipes.toml'
    registry.write_text(serve_recipes.format_recipe(recipe))
    row['registry_digest'] = serve_recipes.registry_digest(registry)
    before = m.read_json(m.receipt_path())
    real_publish = m.publish
    monkeypatch.setattr(m, 'publish', lambda *a, **kw: real_publish(*a, **kw,
        _fetch=lambda path: b'{"data":[{"id":"served"}]}'))
    args = ['models', 'recipes', 'monitor-sync', recipe['model'], '--container', 'candidate',
            '--registry', str(registry)]
    assert cli.main(args + ['--dry-run']) == 0
    assert m.read_json(m.receipt_path()) == before
    assert cli.main(args + ['--confirm']) == 0
    assert m.read_json(m.receipt_path())['state'] == 'active'
    assert m.read_json(m.receipt_path())['generation'] != before['generation']


def test_probe_uses_bounded_direct_reader_and_rejects_redirect(tmp_path, monkeypatch):
    from anvil_serving.router import model_capacity
    recipe, row = monitoring_slot(tmp_path, monkeypatch)
    calls = []
    def read(url, headers, *, timeout, max_bytes):
        calls.append((url, headers, timeout, max_bytes))
        return 302, b''
    monkeypatch.setattr(model_capacity, '_default_metrics_read', read)
    with pytest.raises(ValueError, match='probe failed'):
        m.publish(recipe, 'candidate', registry_digest='c' * 64)
    assert len(calls) == 1 and calls[0][0] == 'http://127.0.0.1:30000/ready'
    assert calls[0][1] == {} and 0 < calls[0][2] <= 3 and calls[0][3] == m.MAX_BYTES
    assert m.read_json(m.receipt_path())['state'] == 'pending'


def test_remote_docker_cannot_attest_local_binding(tmp_path, monkeypatch):
    recipe, row = monitoring_slot(tmp_path, monkeypatch)
    monkeypatch.setattr(serve_recipes.recipe_memory, '_docker_endpoint', lambda run: 'ssh://example.test')
    with pytest.raises(ValueError, match='local Linux Docker'):
        m.publish(recipe, 'candidate', registry_digest='c' * 64,
                  _fetch=lambda path: pytest.fail('remote ownership must not be probed'))


@pytest.mark.parametrize('payload', ['[]', 'null', '"invalid"'])
def test_receipt_requires_object(tmp_path, payload):
    path = tmp_path / 'receipt.json'
    path.write_text(payload)
    with pytest.raises(ValueError, match='object'):
        m.read_json(path)


@pytest.mark.parametrize('ready_status', [200, 503])
def test_ignis_requires_fixed_ready_even_when_model_identity_matches(tmp_path, monkeypatch, ready_status):
    from anvil_serving.router import model_capacity
    recipe, row = monitoring_slot(tmp_path, monkeypatch)
    calls = []
    def read(url, headers, **kwargs):
        calls.append((url, headers))
        if url.endswith('/ready'):
            return ready_status, b'{}'
        return 200, b'{"data":[{"id":"served","status":"loading"}]}'
    monkeypatch.setattr(model_capacity, '_default_metrics_read', read)
    if ready_status == 503:
        with pytest.raises(ValueError, match='probe failed'):
            m.publish(recipe, 'candidate', registry_digest='c' * 64)
        assert m.read_json(m.receipt_path())['state'] == 'pending'
        assert len(calls) == 1
    else:
        assert m.publish(recipe, 'candidate', registry_digest='c' * 64)['state'] == 'active'
    assert calls[0] == ('http://127.0.0.1:30000/ready', {})


@pytest.mark.parametrize('action', ['unload', 'discovered-unload', 'same-port-load'])
def test_disabled_monitoring_invalidates_receipt_before_lifecycle(tmp_path, monkeypatch, action):
    from anvil_serving import models
    recipe, row = monitoring_slot(tmp_path, monkeypatch)
    active = m.publish(recipe, 'candidate', registry_digest='c' * 64,
                       _fetch=lambda path: b'{"data":[{"id":"served"}]}')
    (tmp_path / 'recipe-monitoring.json').unlink()
    assert m.withdraw(port=30001, container='unrelated') is None
    assert m.read_json(m.receipt_path()) == active
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        receipt = m.read_json(m.receipt_path())
        assert receipt['state'] == 'withdrawn' and receipt['generation'] != active['generation']
        return SimpleNamespace(returncode=1, stdout='', stderr='synthetic failure')
    if action == 'same-port-load':
        recipe['serve']['image'] = 'example/model:pinned'
        assert serve_recipes.load_recipe(recipe, 'replacement', _run=run)[1] == 1
    elif action == 'unload':
        monkeypatch.setattr(models, '_recipe_container_identity', lambda *a, **kw: row)
        assert models._recipe_container_unload(recipe, 'candidate', confirm=True, _run=run) == 1
    else:
        row['native_kv_offload'] = False
        monkeypatch.setattr(models, '_recheck_discovered_recipe_container', lambda *a, **kw: row)
        assert models._discovered_recipe_container_unload(row, confirm=True, _run=run) == 1
    assert len(calls) == 1
    assert m.publish(recipe, 'candidate') is None
    assert m.read_json(m.receipt_path())['state'] == 'withdrawn'


@pytest.mark.parametrize('kind', ['malformed', 'wrong-schema', 'symlink'])
def test_disabled_monitoring_refuses_unsafe_retained_receipt(tmp_path, monkeypatch, kind):
    recipe, row = monitoring_slot(tmp_path, monkeypatch)
    (tmp_path / 'recipe-monitoring.json').unlink()
    if kind == 'malformed':
        m.receipt_path().write_text('[]')
    elif kind == 'wrong-schema':
        m.receipt_path().write_text(json.dumps({'schema': 'other', 'container': 'candidate'}))
    else:
        source = tmp_path / 'other.json'
        source.write_bytes(m.receipt_path().read_bytes())
        m.receipt_path().unlink()
        m.receipt_path().symlink_to(source)
    with pytest.raises((ValueError, OSError)):
        m.withdraw(port=30000, container='candidate')



def test_monitor_sync_help_declares_required_model_without_monitoring(monkeypatch, capsys):
    from anvil_serving import cli
    def forbidden():
        pytest.fail('help or missing MODEL must not access monitoring configuration')
    monkeypatch.setattr(m, 'configuration', forbidden)
    assert cli.main(['models', 'recipes', 'monitor-sync', '--help']) == 0
    rendered = ' '.join(capsys.readouterr().out.split())
    assert 'MODEL is a required positional recipe selector.' in rendered
    assert '--container NAME' in rendered and '--registry PATH' in rendered
    with pytest.raises(SystemExit) as error:
        m.main(['--container', 'candidate', '--registry', 'recipes.toml'])
    assert error.value.code == 2
    assert 'required: model' in capsys.readouterr().err
