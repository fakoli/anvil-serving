"""Exact retained exclusive restoration: fake Docker/GPU/HTTP, real guards/locks."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from anvil_serving import cli, reservations, serve_recipes, serves
from tests.conftest import proc

CID = 'a' * 64
IMAGE = 'sha256:' + 'b' * 64
DEVICES = ['GPU-11111111-2222-3333-4444-555555555555', 'GPU-99999999-8888-7777-6666-555555555555']


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
    topology.write_text('''schema_version = 1
id = "synthetic-restoration"
command_host = "host:primary"
command_runtime = "runtime:primary-native"
[[capacity_policies]]
id = "model-capable"
allow_model_workloads = true
[[hosts]]
id = "primary"
roles = ["serve"]
capacity_policy = "model-capable"
[[hosts]]
id = "remote"
roles = ["operator"]
[[runtimes]]
id = "primary-native"
host = "primary"
role = "native"
[[runtimes]]
id = "primary-docker"
host = "primary"
role = "docker"
[[runtimes]]
id = "remote-native"
host = "remote"
role = "native"
[[gpu_roles]]
id = "compute-a"
host = "primary"
runtime = "primary-docker"
uuid = "''' + DEVICES[0] + '''"
[[gpu_roles]]
id = "compute-b"
host = "primary"
runtime = "primary-docker"
uuid = "''' + DEVICES[1] + '''"
[[resources]]
id = "model-operator"
role = "model-serve"
host = "primary"
runtime = "primary-docker"
gpu_role = "compute-a"
workload = "llm"
''')
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
    calls = []
    original = serves.cmd_restore_retained
    def restore(*a, **k):
        calls.append((a, k))
        return original(*a, **k, _run=p.kwargs['_run'])
    monkeypatch.setattr(serves, 'cmd_restore_retained', restore)
    base = ['serves', 'mode', 'restore-retained', 'exclusive', '--manifest', str(p.manifest),
            '--expected-container-id', CID, '--expected-image', IMAGE,
            '--manifest-sha256', p.kwargs['manifest_sha256'], '--registry-sha256', p.kwargs['registry_sha256'],
            '--topology', str(p.topology), '--command-host', 'host:primary',
            '--command-runtime', 'runtime:primary-native', '--target', 'host:primary', '--transport', 'local']
    assert cli.main(base) == 0
    assert calls[-1][1]['dry_run'] is True
    assert cli.main(base + ['--confirm']) == 0
    assert calls[-1][1]['confirm'] is True and calls[-1][1]['dry_run'] is False
    assert calls[-1][1]['expected_id'] == CID
    for extra in [['--restore-group', 'different'], ['--recreate'], ['--router-url', 'http://127.0.0.1:30000']]:
        assert cli.main(base + extra) == 2
    assert len(calls) == 2
    assert actions(p) == [['docker', 'start', CID]]


@pytest.mark.parametrize('entry', ['cli', 'direct'])
@pytest.mark.parametrize('kind', ['docker-caller', 'remote-caller', 'controller', 'ssh', 'missing-identity', 'capacity', 'gpu', 'other-gpu-owner'])
def test_retained_owner_resolution_refuses_before_docker(prepared, monkeypatch, entry, kind):
    p = prepared
    if kind in {'missing-identity', 'capacity', 'gpu', 'other-gpu-owner'}:
        text = p.topology.read_text()
        if kind == 'missing-identity':
            text = text.replace('command_host = "host:primary"', '').replace('command_runtime = "runtime:primary-native"', '')
        elif kind == 'capacity':
            text = text.replace('allow_model_workloads = true', 'allow_model_workloads = false')
        elif kind == 'gpu':
            text = text.replace('gpu_role = "compute-a"', '')
        else:
            text = text.replace('id = "compute-b"\nhost = "primary"\nruntime = "primary-docker"',
                                'id = "compute-b"\nhost = "remote"\nruntime = "remote-native"')
        p.topology.write_text(text)
    original = serves.cmd_restore_retained
    monkeypatch.setattr(serves, 'cmd_restore_retained', lambda *a, **k: original(*a, **k, _run=p.kwargs['_run']))
    args = ['mode', 'restore-retained', 'exclusive', '--manifest', str(p.manifest),
            '--expected-container-id', CID, '--expected-image', IMAGE,
            '--manifest-sha256', p.kwargs['manifest_sha256'], '--registry-sha256', p.kwargs['registry_sha256'],
            '--topology', str(p.topology), '--confirm']
    if kind == 'docker-caller': args += ['--command-runtime', 'runtime:primary-docker']
    if kind == 'remote-caller': args += ['--command-host', 'host:remote', '--command-runtime', 'runtime:remote-native']
    if kind in {'controller', 'ssh'}: args += ['--transport', kind]
    assert (cli.main(['serves', *args]) if entry == 'cli' else serves.main(args)) != 0
    assert p.calls == []


def test_ordinary_exclusive_up_remains_denied(prepared):
    assert serves.cmd_up(prepared.scope, ['exclusive'], dry_run=True, _run=prepared.kwargs['_run']) == 1
    assert actions(prepared) == []


@pytest.mark.parametrize('utility', ['utility', 'compute', 'duplicate', 'privileged', 'manual-device', 'capability-override', 'unavailable'])
def test_gpu_utility_access_is_not_compute_ownership(prepared, utility):
    p = prepared
    original = p.kwargs['_run']
    def run(argv, **kwargs):
        if argv[:3] == ['docker', 'ps', '-a']:
            return proc(0, json.dumps({'Names': 'observer', 'State': 'running'}))
        if argv[:2] == ['docker', 'inspect'] and argv[-1] == 'observer':
            if argv[3] == '{{json .HostConfig.DeviceRequests}}':
                return proc(0, json.dumps([{'Driver': 'nvidia', 'Capabilities': [['gpu', 'compute']] if utility == 'capability-override' else [['gpu']]}]))
            text = 'false null null\nNVIDIA_DRIVER_CAPABILITIES=utility\n'
            if utility == 'compute': text = text.replace('=utility', '=compute,utility')
            if utility == 'duplicate': text += 'NVIDIA_DRIVER_CAPABILITIES=compute\n'
            if utility == 'privileged': text = text.replace('false', 'true')
            if utility == 'manual-device': text = text.replace('false null null', 'false [{}] null')
            return proc(1 if utility == 'unavailable' else 0, text)
        return original(argv, **kwargs)
    assert invoke(p, dry_run=False, confirm=True, _run=run) == (0 if utility == 'utility' else 1)
    assert actions(p) == ([['docker', 'start', CID]] if utility == 'utility' else [])
