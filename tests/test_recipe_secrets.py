"""Protected credential custody and rotation are tested with synthetic local files."""
import json
import os

import pytest

from anvil_serving import recipe_monitoring as m, recipe_secrets as s
from tests.test_recipe_monitoring import monitoring_slot


def test_protected_key_file(tmp_path):
    key = tmp_path / 'key'
    key.write_text('synthetic-key\n')
    key.chmod(0o600)
    assert s.read_key(str(key))[0] == 'synthetic-key'
    key.chmod(0o644)
    with pytest.raises(ValueError, match='protected'):
        s.read_key(str(key))
    key.unlink()
    os.mkfifo(key)
    with pytest.raises(ValueError, match='regular'):
        s.read_key(str(key))
    key.unlink()
    key.symlink_to(tmp_path / 'missing')
    with pytest.raises(ValueError, match='unavailable'):
        s.read_key(str(key))


@pytest.mark.parametrize('path', ['relative', '/tmp/a,b', '/tmp/../key', '/tmp//key',
                                  '/tmp/key\n', '/tmp/$(key)', '/tmp/key:ro'])
def test_unsafe_paths(path):
    with pytest.raises(ValueError):
        s.safe_path(path)


def test_key_directory_and_hardlink_custody(tmp_path):
    key = tmp_path / 'key'
    key.write_text('synthetic-key')
    key.chmod(0o600)
    link = tmp_path / 'link'
    os.link(key, link)
    with pytest.raises(ValueError, match='protected'):
        s.read_key(str(key))
    link.unlink()
    tmp_path.chmod(0o770)
    with pytest.raises(ValueError, match='parent'):
        s.read_key(str(key))
    tmp_path.chmod(0o700)
    assert s.read_key(str(key))[0] == 'synthetic-key'


@pytest.mark.parametrize('rotate', [False, True])
def test_authenticated_monitor_and_rotation_fence(tmp_path, monkeypatch, rotate):
    from anvil_serving.router import model_capacity
    recipe, row = monitoring_slot(tmp_path, monkeypatch)
    key = tmp_path / 'key'
    key.write_text('synthetic-key')
    key.chmod(0o600)
    config_path = tmp_path / 'recipe-monitoring.json'
    config = json.loads(config_path.read_text())
    config['model_api_key_files'] = {'30000': str(key)}
    config_path.write_text(json.dumps(config))
    calls = []
    def read(url, headers, *, timeout, max_bytes):
        calls.append((url, headers))
        if rotate and url.endswith('/v1/models'):
            key.write_text('replacement-key')
        return 200, b'{"data":[{"id":"served"}]}'
    monkeypatch.setattr(model_capacity, '_default_metrics_read', read)
    if rotate:
        with pytest.raises(ValueError, match='credential changed'):
            m.publish(recipe, 'candidate', registry_digest='c' * 64)
        assert m.read_json(m.receipt_path())['state'] == 'pending'
    else:
        value = m.publish(recipe, 'candidate', registry_digest='c' * 64)
        assert 'synthetic-key' not in json.dumps(value) and str(key) not in json.dumps(value)
    assert calls == [('http://127.0.0.1:30000/ready', {}),
                     ('http://127.0.0.1:30000/v1/models', {'Authorization': 'Bearer synthetic-key'})]
