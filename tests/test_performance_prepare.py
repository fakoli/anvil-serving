"""Fake-command preparation tests: no package downloads or model calls."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from anvil_serving.benchmarking import performance_prepare as prep
from anvil_serving.benchmarking.jobs import BenchmarkJobError


class FakePreparation:
    def __init__(self, tmp_path, monkeypatch):
        self.cache = tmp_path / 'cache'
        self.run = tmp_path / 'run'
        self.calls = []
        self.drift_packages = False
        self.fail_offline = False
        self.assets = {}
        sources = {
            'lil': {'llm_decode_bench.py': '# pinned LIL fixture\n'},
            'aiperf': {'src/aiperf/transports/aiohttp_client.py': '# pinned AIPerf fixture\n',
                       'pyproject.toml': '[project]\nname="aiperf"\nversion="0.13.0"\n',
                       'uv.lock': '\n'.join('[[package]]\nname="' + name + '"\nversion="' + version + '"'
                                           for name, version in [('aiperf', '0.13.0'), ('aiohttp', '3.14.3'), ('tiktoken', '0.14.0'), ('ruamel.yaml', '0.18.17')])},
        }
        hashes = {}
        for name, files in sources.items():
            key = name + '/' + prep.SOURCES[name]['revision']
            self.assets[name] = {**prep.SOURCES[name], 'cache_key': key}
            root = self.cache / key
            hashes[name] = {}
            for relative, content in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                hashes[name][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        source = self.cache / self.assets['aiperf']['cache_key']
        (source / 'lock-link').symlink_to('uv.lock')
        monkeypatch.setattr(prep, 'SOURCE_HASHES', hashes)
        monkeypatch.setattr(prep.shutil, 'which', lambda name: '/fake/' + name)
        monkeypatch.setattr(prep.platform, 'system', lambda: 'Linux')

    def runner(self, command, cwd, timeout):
        assert command[:2] == ('/usr/bin/env', '-i')
        argv = list(command[2:])
        env = {}
        while '=' in argv[0]:
            key, value = argv.pop(0).split('=', 1)
            env[key] = value
        assert env['PIP_CONFIG_FILE'] == os.devnull
        assert 'OPENAI_API_KEY' not in env and 'PYTHONPATH' not in env
        assert Path(cwd).name == 'cwd' and not list(Path(cwd).iterdir())
        assert Path(env['HOME']).name == 'home'
        self.calls.append((argv, env))
        output = ''
        if argv[0] == 'git':
            root = Path(argv[2])
            if 'rev-parse' in argv:
                output = prep.SOURCES[root.parent.name]['revision']
            elif 'ls-files' in argv:
                rows = []
                for path in sorted(root.rglob('*')):
                    if path.is_file():
                        data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
                        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
                        rows.append(('120000 ' if path.is_symlink() else '100644 ') + blob + ' 0\t' + path.relative_to(root).as_posix())
                output = '\0'.join(rows) + '\0'
        elif 'venv' in argv:
            root = Path(argv[-1])
            (root / 'bin').mkdir()
            (root / 'bin/python').write_text('fake interpreter')
            (root / 'bin/python').chmod(0o755)
        elif 'sync' in argv:
            root = Path(env['UV_PROJECT_ENVIRONMENT'])
            for path in (root / 'source/src').rglob('*.py'):
                destination = root / 'lib/python3.12/site-packages' / path.relative_to(root / 'source/src')
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(path.read_bytes())
        elif prep._INVENTORY in argv:
            root = Path(argv[0]).parent.parent
            packages = ['aiohttp==3.14.3', 'aiperf==0.13.0', 'tiktoken==0.14.0'] if (root / 'source/uv.lock').exists() else sorted(prep.LIL_REQUIREMENTS)
            if self.drift_packages:
                packages.append('unexpected==1.0')
            output = json.dumps({'version': '3.12.9', 'packages': packages})
        elif prep._TOKENIZER in argv:
            cache = Path(argv[-2])
            if argv[-1] == 'online':
                (cache / 'tokenizer-data').write_text('owned o200k fixture')
            else:
                assert (cache / 'tokenizer-data').read_text() == 'owned o200k fixture'
                if self.fail_offline:
                    return subprocess.CompletedProcess(command, 1, stdout='', stderr='network attempted')
            output = 'o200k_base ready'
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr='')

    def prepare(self, *, offline=False):
        return prep.prepare_performance_environments({}, self.assets, cache_root=str(self.cache),
            run_path=str(self.run), offline=offline, runner=self.runner)


def test_offline_reuse_checks_package_inventory_and_tokenizer_in_child(tmp_path, monkeypatch):
    fake = FakePreparation(tmp_path, monkeypatch)
    fake.prepare()
    fake.drift_packages = True
    with pytest.raises(BenchmarkJobError, match='package inventory changed'):
        fake.prepare(offline=True)
    fake.drift_packages = False
    fake.fail_offline = True
    with pytest.raises(BenchmarkJobError, match='command failed'):
        fake.prepare(offline=True)
