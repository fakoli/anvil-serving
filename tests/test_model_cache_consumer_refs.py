"""Native mappings and directory handles remain consumers after data FDs close."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving import model_cache_volume as volumes

pytestmark = pytest.mark.skipif(not Path('/proc').is_dir(), reason='requires Linux procfs')


@pytest.mark.parametrize('kind', ['mapping', 'cwd', 'exe'])
def test_consumer_audit_blocks_without_open_data_fd(tmp_path, monkeypatch, kind):
    cache = tmp_path / 'model cache'; cache.mkdir()
    process = tmp_path / '999999'; process.mkdir(); (process / 'fd').mkdir()
    maps = '1000-2000 r--p 00000000 08:01 123 ' + str(cache / 'weights.safetensors') + '\n'
    (process / 'maps').write_text(maps if kind == 'mapping' else '')
    if kind != 'mapping': (process / kind).symlink_to(cache, target_is_directory=True)
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    monkeypatch.setattr(volumes, '_children', lambda *_args: [SimpleNamespace(name='999999', path=str(process))])
    assert volumes._native_fd_refs(cache) == [{'pid': '999999', 'kind': kind}]


def test_mapping_budget_is_separate_and_still_bounded(tmp_path, monkeypatch):
    cache = tmp_path / 'cache'; cache.mkdir()
    process = tmp_path / '999999'; process.mkdir(); (process / 'fd').mkdir()
    (process / 'maps').write_text('1000-2000 r--p 00000000 08:01 123 /synthetic/shared-library\n' * 3)
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    monkeypatch.setattr(volumes, '_children', lambda *_args: [SimpleNamespace(name='999999', path=str(process))])
    monkeypatch.setattr(volumes, '_MAX_ENTRIES', 1)
    assert volumes._native_fd_refs(cache) == []
    monkeypatch.setattr(volumes, '_MAX_NATIVE_MAPPINGS', 2)
    with pytest.raises(volumes.ModelCacheVolumeError, match='exceeds its bound'):
        volumes._native_fd_refs(cache)
