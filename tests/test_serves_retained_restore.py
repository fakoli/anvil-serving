"""Exact retained exclusive restoration: fake Docker/GPU/HTTP, real guards/locks."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from anvil_serving import cli, reservations, serve_recipes, serves
from tests.conftest import proc

CID = 'a' * 64
IMAGE = 'sha256:' + 'b' * 64
DEVICES = ['GPU-01234567-89ab-cdef-0123-456789abcdef', 'GPU-fedcba98-7654-3210-fedc-ba9876543210']


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv('ANVIL_SERVING_HOME', str(tmp_path))
    registry = tmp_path / 'recipes.toml'
    registry.write_text('''[[recipe]]
model = "vendor/model"
[recipe.serve]
image = "example/model:pinned"
port = 30001
flags = ["--served-model-name exact-model", "--tensor-parallel-size 2"]
''')
    manifest = tmp_path / 'serves.toml'
    manifest.write_text('''[[gpu_roles]]
id = "compute-a"
vram_mib = 98304
reserve_mib = 1024
[[gpu_roles]]
id = "compute-b"
vram_mib = 98304
reserve_mib = 1024
[[serve]]
name = "exclusive"
runtime = "docker"
container = "retained-model"
port = 30001
model = "exact-model"
engine = "vllm"
health = "/v1/models"
gpu_roles = ["compute-a", "compute-b"]
vram_mib = 80000
residency = "on-demand"
operating_mode = "dual-gpu-exclusive"
tensor_parallel_size = 2
up = "python -m anvil_serving.cli models recipes load vendor/model --registry {dir}/recipes.toml --gpu-device ''' + ','.join(DEVICES) + '''"
''')
    topology = tmp_path / 'operator-topology.toml'
    topology.write_text('# fixed synthetic topology\n')
    monkeypatch.setattr('anvil_serving.topology.load_topology', lambda _: SimpleNamespace(
        gpu_role=lambda role: SimpleNamespace(uuid=DEVICES[['compute-a', 'compute-b'].index(role)])))
    monkeypatch.setattr(serves, '_unmanaged_recipe_ownership', lambda *a, **k: {'owners': [], 'discovery_error': None})
    monkeypatch.setattr(serves, '_storage_write_check', lambda *a, **k: k['repair'] is False)
    monkeypatch.setattr(serves, '_await_healthy', lambda *a, **k: True)
    monkeypatch.setattr(serves, '_serve_identity_ready', lambda *a, **k: True)
    monkeypatch.setattr(serves, '_serve_env', lambda *a: pytest.fail('retained environment must not be read'))
    recipe = serve_recipes.load_registry(registry)['recipe'][0]
    command = serve_recipes.docker_run_argv(recipe, gpu_device=','.join(DEVICES))
    command = command[command.index(recipe['serve']['image']) + 1:]
    row = {'Id': CID, 'Image': IMAGE, 'Name': '/retained-model',
           'State': {'Status': 'exited', 'Running': False, 'Pid': 0, 'StartedAt': '2026-01-01T01:00:00Z'},
           'Config': {'Image': recipe['serve']['image'], 'Cmd': command,
                      'Labels': {serve_recipes.RECIPE_MANAGED_LABEL: serve_recipes.RECIPE_MANAGED_VALUE,
                                 serve_recipes.RECIPE_MODEL_LABEL: recipe['model'],
                                 serve_recipes.RECIPE_DIGEST_LABEL: serve_recipes.recipe_digest(recipe)}},
           'Args': command, 'HostConfig': {'DeviceRequests': [{'Driver': 'nvidia', 'Count': 0, 'DeviceIDs': DEVICES, 'Capabilities': [['gpu']], 'Options': {}}],
                                         'PortBindings': {'8000/tcp': [{'HostPort': '30001'}]}}}
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ['docker', 'ps', '-a']:
            return proc(0, json.dumps({'Names': 'retained-model', 'State': row['State']['Status']}))
        if argv[:2] == ['docker', 'inspect']:
            return proc(0, json.dumps(row))
        if argv[:2] == ['docker', 'start']:
            row['State'].update(Status='running', Running=True, Pid=123)
            return proc(0)
        if argv[:2] == ['docker', 'stop']:
            row['State'].update(Status='exited', Running=False, Pid=0)
            return proc(0)
        if argv[0] == 'nvidia-smi':
            if '--query-gpu=' in argv[1]:
                return proc(0, '\n'.join(device + ', Synthetic GPU, 00000000:01:00.0, 98304, 0, Disabled, 1.0' for device in DEVICES))
            return proc(0)
        pytest.fail('unexpected command: ' + repr(argv))
    kwargs = {'manifest_path': str(manifest), 'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
              'registry_sha256': hashlib.sha256(registry.read_bytes()).hexdigest(),
              'expected_id': CID, 'expected_image': IMAGE, '_run': run}
    return SimpleNamespace(manifest=manifest, registry=registry, topology=topology, row=row,
                           calls=calls, kwargs=kwargs, scope=serves.load_manifest_set(manifest))


def actions(p):
    return [a for a in p.calls if a[:2] in (['docker', 'start'], ['docker', 'stop'])]


def invoke(p, **changes):
    return serves.cmd_restore_retained(p.scope, 'exclusive', **{**p.kwargs, **changes})


def test_exact_retained_restore_and_default_preview(prepared):
    p = prepared
    assert invoke(p) == 0
    assert actions(p) == []
    assert not (p.registry.parent / '.serve-recipes.lock').exists()
    assert invoke(p, dry_run=False, confirm=True) == 0
    assert actions(p) == [['docker', 'start', CID]]
    assert all('.Config.Env' not in repr(a) for a in p.calls)


@pytest.mark.parametrize('kind', ['id', 'image', 'name', 'model', 'recipe', 'revision', 'cmd', 'devices', 'gpu-count', 'gpu-options', 'port', 'never-started', 'created', 'paused', 'running', 'restarting', 'pid'])
def test_identity_mismatches_never_start(prepared, kind):
    p = prepared
    if kind in {'id', 'image', 'name'}:
        p.row[{'id': 'Id', 'image': 'Image', 'name': 'Name'}[kind]] += '-changed'
    elif kind == 'model': p.row['Args'] = ['--served-model-name', 'different']
    elif kind == 'recipe': p.row['Config']['Labels'][serve_recipes.RECIPE_DIGEST_LABEL] = 'c' * 64
    elif kind == 'revision': p.row['Config']['Labels'][serve_recipes.RECIPE_REVISION_LABEL] = 'changed'
    elif kind == 'cmd': p.row['Config']['Cmd'] = []
    elif kind == 'devices': p.row['HostConfig']['DeviceRequests'] = [{'DeviceIDs': ['all']}]
    elif kind == 'gpu-count': p.row['HostConfig']['DeviceRequests'][0]['Count'] = -1
    elif kind == 'gpu-options': p.row['HostConfig']['DeviceRequests'][0]['Options'] = {'unexpected': 'selection'}
    elif kind == 'port': p.row['HostConfig']['PortBindings'] = {}
    elif kind == 'never-started': p.row['State']['StartedAt'] = '0001-01-01T00:00:00Z'
    elif kind in {'created', 'paused', 'running', 'restarting'}: p.row['State']['Status'] = kind
    elif kind == 'pid': p.row['State']['Pid'] = 55
    assert invoke(p, dry_run=False, confirm=True) == 1
    assert actions(p) == []


@pytest.mark.parametrize('kind', ['manifest', 'registry', 'topology', 'replacement', 'missing', 'unknown-owner', 'competing', 'recipe-owner', 'discovery-error', 'compute', 'budget', 'unmanaged-container', 'container-discovery'])
def test_last_moment_source_and_owner_guards(prepared, monkeypatch, kind):
    p = prepared
    if kind in {'manifest', 'registry'}:
        p.kwargs[kind + '_sha256'] = 'c' * 64
    elif kind == 'topology':
        monkeypatch.setattr('anvil_serving.topology.load_topology', lambda _: SimpleNamespace(gpu_role=lambda _: SimpleNamespace(uuid='GPU-other')))
    elif kind in {'unknown-owner', 'competing'}:
        p.scope.append({'name': 'other', 'container': 'other'})
        monkeypatch.setattr(serves, 'load_manifest_set', lambda _: p.scope)
        monkeypatch.setattr(serves, 'docker_states', lambda *a, **k: {'retained-model': 'exited', 'other': 'unknown' if kind == 'unknown-owner' else 'running'})
    elif kind in {'recipe-owner', 'discovery-error'}:
        monkeypatch.setattr(serves, '_unmanaged_recipe_ownership', lambda *a, **k: {'owners': [1] if kind == 'recipe-owner' else [], 'discovery_error': kind == 'discovery-error'})
    elif kind == 'budget': monkeypatch.setattr(reservations, 'deny_over_budget', lambda *a: ['no budget'])
    else:
        original = p.kwargs['_run']; count = [0]
        def run(argv, **kwargs):
            if kind == 'container-discovery' and argv[:3] == ['docker', 'ps', '-a']: return proc(1)
            if kind == 'unmanaged-container' and argv[:3] == ['docker', 'ps', '-a']:
                return proc(0, json.dumps({'Names': 'foreign', 'State': 'running'}))
            if kind == 'unmanaged-container' and argv[:2] == ['docker', 'inspect']: return proc(0, '[{}]')
            if kind == 'compute' and argv[0] == 'nvidia-smi': return proc(0, DEVICES[0] + ', 33')
            if argv[:2] == ['docker', 'inspect']:
                count[0] += 1
                if kind == 'missing': return proc(1)
                if kind == 'replacement' and count[0] == 2: p.row['Id'] = 'c' * 64
            return original(argv, **kwargs)
        p.kwargs['_run'] = run
    assert invoke(p, dry_run=False, confirm=True) == 1
    assert actions(p) == []


@pytest.mark.parametrize('guard', ['health', 'protocol', 'storage'])
def test_failure_stops_only_same_owner_and_retains(prepared, monkeypatch, guard):
    p = prepared
    monkeypatch.setattr(serves, {'health': '_await_healthy', 'protocol': '_serve_identity_ready', 'storage': '_storage_write_check'}[guard], lambda *a, **k: False)
    assert invoke(p, dry_run=False, confirm=True) == 1
    assert actions(p) == [['docker', 'start', CID], ['docker', 'stop', CID]]
    assert p.row['State']['Status'] == 'exited'


def test_running_replacement_after_start_is_hold_without_cleanup(prepared, monkeypatch):
    p = prepared
    def health(*a, **k):
        p.row['Id'] = 'c' * 64
        return False
    monkeypatch.setattr(serves, '_await_healthy', health)
    assert invoke(p, dry_run=False, confirm=True) == 1
    assert actions(p) == [['docker', 'start', CID]]


def test_real_promotion_lock_and_pending_experiment_refuse(prepared, monkeypatch):
    p = prepared
    # A distinct thread models an independent cooperating owner: flock is real.
    import threading
    errors = []
    with serves._switch_role_lock('promotion'):
        def contender():
            try: invoke(p, dry_run=False, confirm=True)
            except RuntimeError: errors.append('lock-refused')
        thread = threading.Thread(target=contender); thread.start(); thread.join()
    assert errors == ['lock-refused']
    assert actions(p) == []
    def pending(): raise RuntimeError('pending synthetic experiment')
    monkeypatch.setattr('anvil_serving.control_plane.mcp.tools.runtime_experiment.assert_no_pending_runtime_experiment', pending)
    with pytest.raises(RuntimeError, match='pending synthetic'):
        invoke(p, dry_run=False, confirm=True)
    assert actions(p) == []


def test_real_cli_dispatch_preview_confirm_and_bad_options(prepared, monkeypatch):
    p = prepared
    monkeypatch.setattr(cli, '_resolve_dispatch_plan', lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(serves, 'cmd_restore_retained', lambda *a, **k: calls.append((a, k)) or 0)
    base = ['serves', 'mode', 'restore-retained', 'exclusive', '--manifest', str(p.manifest),
            '--expected-container-id', CID, '--expected-image', IMAGE,
            '--manifest-sha256', p.kwargs['manifest_sha256'], '--registry-sha256', p.kwargs['registry_sha256']]
    assert cli.main(base) == 0
    assert calls[-1][1]['dry_run'] is True
    assert cli.main(base + ['--confirm']) == 0
    assert calls[-1][1]['confirm'] is True and calls[-1][1]['dry_run'] is False
    assert calls[-1][1]['expected_id'] == CID
    for extra in [['--restore-group', 'different'], ['--recreate'], ['--router-url', 'http://127.0.0.1:30000']]:
        assert cli.main(base + extra) == 2
    assert len(calls) == 2


def test_ordinary_exclusive_up_remains_denied(prepared):
    assert serves.cmd_up(prepared.scope, ['exclusive'], dry_run=True, _run=prepared.kwargs['_run']) == 1
    assert actions(prepared) == []
