"""Registration follows exact lifecycle ownership, never a reusable port alone."""
import json

import pytest

from anvil_serving import recipe_monitoring as m, serve_recipes


@pytest.fixture(autouse=True)
def local_docker(monkeypatch):
    monkeypatch.setattr(serve_recipes.recipe_memory, '_docker_endpoint',
                        lambda run: 'unix:///var/run/docker.sock')


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


def test_remote_docker_cannot_attest_local_binding(tmp_path, monkeypatch):
    recipe, row = monitoring_slot(tmp_path, monkeypatch)
    monkeypatch.setattr(serve_recipes.recipe_memory, '_docker_endpoint', lambda run: 'ssh://example.test')
    with pytest.raises(ValueError, match='local Linux Docker'):
        m.publish(recipe, 'candidate', registry_digest='c' * 64,
                  _fetch=lambda path: pytest.fail('remote ownership must not be probed'))


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
