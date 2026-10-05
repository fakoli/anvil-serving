"""Offline integrity and failure gates for the pinned Strata image artifacts."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tomllib
import types

import pytest

ARTIFACT = Path(__file__).resolve().parents[1] / 'configs/runtime/strata-6f32ec0-sm120'


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    # The artifact runs on Linux. Only the lock primitive is replaced on Windows;
    # input hashing and configuration/receipt logic are the real artifact code.
    monkeypatch.setitem(sys.modules, 'fcntl', types.SimpleNamespace(
        LOCK_EX=1, LOCK_NB=2, flock=lambda *_: None))
    base = _module(ARTIFACT / 'anvil/prepare.py', 'strata_base_test')
    monkeypatch.setitem(sys.modules, 'prepare', base)
    module = _module(ARTIFACT / 'variants/runtime.py', 'strata_runtime_test')
    copied = tmp_path / 'configs'
    shutil.copytree(ARTIFACT / 'variants/runtime-configs', copied)
    monkeypatch.setattr(module, 'CONFIG_DIR', copied)
    return module


def test_frozen_inputs_match_actual_built_image_provenance():
    provenance = json.loads((ARTIFACT / 'build-provenance.json').read_text())
    for name, expected in provenance['files'].items():
        assert hashlib.sha256((ARTIFACT / name).read_bytes()).hexdigest() == expected
    for name, expected in provenance['derived_image']['files'].items():
        assert hashlib.sha256((ARTIFACT / name).read_bytes()).hexdigest() == expected
    manifest = json.loads((ARTIFACT / 'anvil/artifact-manifest.json').read_text())
    assert len(manifest['mtp']['files']) == 28
    assert sum(row['size'] for row in manifest['mtp']['files']) == 55167376024
    assert hashlib.sha256((ARTIFACT / 'anvil/model.safetensors.index.json').read_bytes()).hexdigest() == manifest['mtp_index_sha256']


def test_profiles_preserve_pack_identity_and_explicit_capacity(runtime):
    for name in runtime.NAMES:
        _, row, cfg = runtime.selected_config(name)
        args = cfg['args']
        assert args[args.index('--pack') + 1] == '/data/prepared/pack'
        assert args[args.index('--mtp') + 1] == '/data/prepared/mtp/rt'
        assert int(args[args.index('--max-context') + 1]) == row['context_tokens']
        assert int(args[args.index('--resident-budget-gib') + 1]) == row['resident_budget_gib']
        assert cfg['parallel'] == row['parallel']
        assert args[args.index('--kv') + 1] == 'int8'
        assert '\\' not in json.dumps(cfg)


def test_tampered_config_refuses_before_preparation(runtime):
    path, _, _ = runtime.selected_config('32k-c1-r36')
    path.write_bytes(path.read_bytes().replace(b'32768', b'65536'))
    with pytest.raises(ValueError, match='identity mismatch'):
        runtime.selected_config('32k-c1-r36')


@pytest.mark.parametrize('name', ['../config', '/tmp/config.json', 'custom'])
def test_unknown_or_path_configuration_refuses(runtime, name):
    with pytest.raises(ValueError, match='Unknown baked'):
        runtime.selected_config(name)


def test_larger_budget_requires_its_exact_ceiling_and_no_swap(runtime, tmp_path, monkeypatch):
    group = tmp_path / 'cgroup'
    group.mkdir()
    monkeypatch.setattr(runtime, 'CGROUP', group)
    _, row, _ = runtime.selected_config('128k-c1-r48')
    (group / 'memory.max').write_text(str(52 * 1024**3))
    (group / 'memory.swap.max').write_text('0')
    with pytest.raises(ValueError, match='exact managed RAM'):
        runtime.check_runtime_memory(row)
    (group / 'memory.max').write_text(str(66 * 1024**3))
    runtime.check_runtime_memory(row)
    (group / 'memory.swap.max').write_text('1073741824')
    with pytest.raises(ValueError, match='zero swap'):
        runtime.check_runtime_memory(row)


def test_arbitrary_server_overrides_refuse(runtime):
    with pytest.raises(SystemExit) as error:
        runtime.main(['--config', '/tmp/another.json'])
    assert error.value.code == 2


def test_prepare_receipt_emitted_after_verification_without_mutation(runtime, tmp_path, monkeypatch, capsys):
    data = tmp_path / 'data'
    final = data / 'prepared'
    final.mkdir(parents=True)
    receipt = {'schema': 'anvil-strata-preparation/v1', 'outputs': {'pack/test': {'sha256': 'a' * 64, 'bytes': 1}}}
    original = {'original': 'profile'}
    receipt_path = final / 'preparation.json'
    receipt_path.write_text(json.dumps(receipt))
    (final / 'config.json').write_text(json.dumps(original))
    before = receipt_path.read_bytes()
    calls = []
    monkeypatch.setattr(runtime.base, 'DATA', data)
    monkeypatch.setattr(runtime.base, 'FINAL', final)
    monkeypatch.setattr(runtime.base, 'prepare', lambda: calls.append('verified'))
    monkeypatch.setattr(runtime, 'check_runtime_memory', lambda _: pytest.fail('CPU preparation must use its own recipe limit'))
    runtime.main(['--prepare-only'])
    assert calls == ['verified']
    event = json.loads(capsys.readouterr().out)
    assert event['preparation_receipt'] == receipt
    assert event['original_config'] == original
    assert event['runtime_config_name'] == '32k-c1-r36'
    assert receipt_path.read_bytes() == before


def test_failed_preparation_never_emits_verified_receipt(runtime, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runtime.base, 'DATA', tmp_path)
    def failed():
        raise ValueError('corrupt source')
    monkeypatch.setattr(runtime.base, 'prepare', failed)
    with pytest.raises(ValueError, match='corrupt source'):
        runtime.main(['--prepare-only'])
    assert capsys.readouterr().out == ''


def test_inference_passes_baked_port_to_server(runtime, tmp_path, monkeypatch):
    final = tmp_path / 'prepared'
    final.mkdir()
    (final / 'preparation.json').write_text('{}')
    (final / 'config.json').write_text('{}')
    monkeypatch.setattr(runtime.base, 'DATA', tmp_path)
    monkeypatch.setattr(runtime.base, 'FINAL', final)
    monkeypatch.setattr(runtime.base, 'ROOT', tmp_path)
    monkeypatch.setattr(runtime.base, 'prepare', lambda: None)
    monkeypatch.setattr(runtime, 'check_runtime_memory', lambda _: None)
    calls = []
    monkeypatch.setattr(runtime.os, 'chdir', lambda _: None)
    monkeypatch.setattr(runtime.os, 'execv', lambda exe, args: calls.append((exe, args)))
    runtime.main(['--runtime-config', '128k-c1-r36'])
    _, args = calls[0]
    assert args == [sys.executable, '-m', 'serve.server', '--engine', 'strata',
                    '--config', str(runtime.CONFIG_DIR / '128k-c1-r36.json'),
                    '--port', '39128']



def test_derived_recipes_bind_image_config_and_memory_contracts(runtime):
    provenance = json.loads((ARTIFACT / 'build-provenance.json').read_text())
    recipes = tomllib.loads((ARTIFACT / 'variants/serve-recipes.toml').read_text())['recipe']
    assert len(recipes) == len(runtime.NAMES)
    found = set()
    for recipe in recipes:
        serve = recipe['serve']
        name = serve['flags'][0].removeprefix('--runtime-config ')
        assert name not in found
        found.add(name)
        _, row, cfg = runtime.selected_config(name)
        assert recipe['status'] == 'unqualified'
        assert serve['image'] == provenance['derived_image']['image_id']
        assert serve['context_tokens'] == row['context_tokens']
        assert serve['served_model_name'] == cfg['model_name']
        assert serve['memory_limit_mib'] == row['required_ram_limit_mib']
        assert serve['memory_swap_limit_mib'] == serve['memory_limit_mib']
        assert serve['host_memory_reserve_mib'] == 8192
        assert row['sha256'] in recipe['source']
        assert serve['env'] == ['PORT=39128', 'STRATA_ALLOWED_HOSTS=models.example.invalid']
        if name.endswith('r48'):
            assert 'initial-64gib-wsl-ceiling' in recipe['fit']['not_suited']
            assert 'NOT initially runnable' in recipe['fit']['rationale']
    assert found == set(runtime.NAMES)


def test_m50_reuses_launcher_with_stricter_bound_and_same_engine(runtime, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'runtime', runtime)
    wrapper = _module(ARTIFACT / 'variants-m50/runtime_m50.py', 'strata_m50_test')
    original = runtime.selected_config('32k-c1-r36')[2]
    monkeypatch.setattr(runtime, 'Path', lambda _: ARTIFACT / 'variants-m50/runtime-configs')
    group = tmp_path / 'cgroup'
    group.mkdir()
    monkeypatch.setattr(runtime, 'CGROUP', group)
    (group / 'memory.max').write_text(str(50 * 1024**3))
    (group / 'memory.swap.max').write_text('0')
    calls = []
    def launch():
        _, row, cfg = runtime.selected_config(runtime.NAMES[0])
        runtime.check_runtime_memory(row)
        assert cfg['args'] == original['args']
        assert cfg['model_name'].endswith('-m50')
        calls.append(row['required_ram_limit_mib'])
    monkeypatch.setattr(runtime, 'main', launch)
    wrapper.main()
    assert calls == [51200]
    with pytest.raises(ValueError, match='Unknown baked'):
        runtime.selected_config('32k-c1-r36')
    (group / 'memory.max').write_text(str(52 * 1024**3))
    with pytest.raises(ValueError, match='exact managed RAM'):
        wrapper.main()
