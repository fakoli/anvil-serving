"""The fixed chunk profile preserves other engine behavior and existing containment."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'configs/runtime/strata-6f32ec0-sm120'
LEAF = ROOT / 'variants-prefill4096'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_manifest_and_same_engine_arguments():
    name = '32k-c1-r36-m50-p4096'
    manifest = json.loads((LEAF / 'runtime-configs/manifest.json').read_text())
    assert set(manifest['configs']) == {name}
    data = (LEAF / f'runtime-configs/{name}.json').read_bytes()
    row = manifest['configs'][name]
    assert row['sha256'] == hashlib.sha256(data).hexdigest()
    assert row['bytes'] == len(data)
    assert row['required_ram_limit_mib'] == 51200
    cfg = json.loads(data)
    old = json.loads((ROOT / 'variants-m50/runtime-configs/32k-c1-r36-m50.json').read_text())
    for key in ('model_name', 'log'):
        assert cfg[key] == old[key].replace('m50', 'm50-p4096')
        cfg.pop(key)
        old.pop(key)
    at = old['args'].index('--prefill')
    assert old['args'][at + 1] == 'auto'
    assert cfg['args'][at + 1] == '4096'
    assert '--no-prefill-borrow' not in cfg['args']
    old['args'][at + 1] = '4096'
    assert cfg == old
    dockerfile = (LEAF / 'Dockerfile').read_text()
    assert dockerfile.startswith(f"FROM anvil-strata:6f32ec0-sm120-trace-m50-v1@{manifest['base_image_id']}\n")
    assert '\nENV ' not in dockerfile


@pytest.mark.parametrize('fail', [False, True])
def test_forwarded_launcher_keeps_metadata_and_restores_prepare(monkeypatch, fail):
    calls = []

    def original():
        calls.append('original prepare')

    def run():
        assert runtime.NAMES == ('32k-c1-r36-m50-p4096',)
        assert runtime.CONFIG_DIR == Path('/opt/strata/anvil/runtime-configs-prefill4096')
        runtime.base.prepare()
        if fail:
            raise ValueError('existing launcher refused')

    runtime = SimpleNamespace(Path=Path, base=SimpleNamespace(prepare=original), main=run)
    monkeypatch.setitem(sys.modules, 'runtime', runtime)
    monkeypatch.setitem(sys.modules, 'diagnostics', SimpleNamespace(
        emit_pack_metadata=lambda: calls.append('verified metadata')))
    module = load(LEAF / 'runtime_prefill4096.py', 'prefill4096_test')
    if fail:
        with pytest.raises(ValueError, match='launcher refused'):
            module.main()
    else:
        module.main()
    assert calls == ['original prepare', 'verified metadata']
    assert runtime.base.prepare is original


@pytest.mark.parametrize('maximum,swap,passes', [
    (51200 * 1024 * 1024, 0, True),
    (49152 * 1024 * 1024, 0, False),
    (51200 * 1024 * 1024, 1024, False),
    ('max', 0, False),
])
def test_existing_guard_requires_exact_ceiling_and_no_swap(tmp_path, monkeypatch, maximum, swap, passes):
    monkeypatch.setitem(sys.modules, 'prepare', SimpleNamespace())
    runtime = load(ROOT / 'variants/runtime.py', 'existing_runtime_prefill4096_test')
    monkeypatch.setattr(runtime, 'CGROUP', tmp_path)
    (tmp_path / 'memory.max').write_text(str(maximum))
    (tmp_path / 'memory.swap.max').write_text(str(swap))
    if passes:
        runtime.check_runtime_memory({'required_ram_limit_mib': 51200})
    else:
        with pytest.raises(ValueError, match='exact managed RAM ceiling and zero swap'):
            runtime.check_runtime_memory({'required_ram_limit_mib': 51200})
