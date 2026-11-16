import json

import pytest

from anvil_serving import mcp, recipe_memory as rm
from anvil_serving.control_plane.mcp.errors import ToolError
from anvil_serving.control_plane.mcp.tools import models as mt, serves as st
from anvil_serving.workbench_app import recipe_admission


def setup_manage(monkeypatch, tmp_path, current, *, action='load', lifecycle=None):
    recipe = {'model': 'example/model', 'serve': {
        'image': 'example/runtime', 'memory_limit_mib': 64,
        'memory_swap_limit_mib': 64, 'host_memory_reserve_mib': 1024}}
    snapshot = {'registry_sha256': 'a' * 64, 'recipe_sha256': 'b' * 64,
                'inventory_sha256': 'c' * 64, 'container': 'candidate', 'model': recipe['model']}
    values = iter([current] if action == 'status' else [None, current])
    monkeypatch.setattr(mt, '_recipe_manage_snapshot', lambda *_: (dict(snapshot), recipe, next(values)))
    from anvil_serving import serve_recipes
    monkeypatch.setattr(serve_recipes, 'discover_recipe_containers', lambda: {'containers': []})
    monkeypatch.setattr(recipe_admission, 'load_plan', lambda *_: {
        'admission_sha256': 'd' * 64, 'manifest': 'serves.toml', 'serve': 'candidate',
        'gpu_roles': ['compute'], 'reservation': 'full-card'})
    calls = []
    def manage(args):
        calls.append(args)
        return lifecycle() if lifecycle else {'ok': True, 'data': {'returncode': 0}}
    monkeypatch.setattr(st, 'tool_serves_manage', manage)
    args = {'action': action, 'registry': str(tmp_path / 'recipes.toml'),
            'model': recipe['model'], 'container': 'candidate', 'dry_run': False, 'confirm': True,
            'expected_registry_sha256': 'a' * 64, 'expected_recipe_sha256': 'b' * 64,
            'expected_inventory_sha256': 'c' * 64, 'expected_admission_sha256': 'd' * 64}
    return args, calls


def current_record(swap):
    return {'container': 'candidate', 'container_id': 'e' * 64, 'model': 'example/model',
            'recipe_digest': 'b' * 64, 'registry_digest': 'a' * 64,
            'host_memory': {'limit_bytes': 64 * rm.MIB, 'ram_plus_swap_limit_bytes': 64 * rm.MIB + 1,
                            'effective_limit_bytes': 64 * rm.MIB, 'effective_swap_limit_bytes': swap}}


@pytest.mark.parametrize('raised', [False, True])
def test_mcp_failed_load_preserves_only_closed_containment_evidence(monkeypatch, tmp_path, raised):
    evidence = rm.failure_evidence('e' * 64, 'stopped_logs_retained', current_record(None)['host_memory'])
    # A marker can never smuggle arbitrary keys or string-valued secret data.
    evidence['environment'] = 'must-not-leak'
    evidence['host_memory']['peak_bytes'] = 'must-not-leak'
    stderr = 'ENV=must-not-leak\n' + rm.FAILURE_PREFIX + json.dumps(evidence) + '\n'
    def lifecycle():
        data = {'returncode': 1, 'stderr': stderr, 'stdout': 'must-not-leak', 'command': ['must-not-leak']}
        if raised:
            raise ToolError('command_failed', 'must-not-leak', data)
        return {'ok': True, 'data': data}
    args, calls = setup_manage(monkeypatch, tmp_path, None, lifecycle=lifecycle)
    result = mcp.call_tool('recipe_manage', args)
    assert not result['ok'] and len(calls) == 1
    assert result['error']['code'] == 'recipe_lifecycle_failed'
    details = result['error']['details']
    assert details['containment'] == rm.failure_evidence(
        'e' * 64, 'stopped_logs_retained', current_record(None)['host_memory'])
    assert 'must-not-leak' not in json.dumps(result)
    assert set(details) == {'action', 'returncode', 'containment'}
