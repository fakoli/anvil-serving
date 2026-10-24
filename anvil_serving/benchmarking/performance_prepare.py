"""Isolated, byte-verified preparation for the two pinned performance harnesses."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

from . import harnesses
from .artifacts import atomic_write_json
from .jobs import BenchmarkJobError, canonical_json_bytes

SCHEMA = "anvil-serving.performance-environment/v1"
SOURCES = {
    "lil": {"source": "https://github.com/local-inference-lab/llm-inference-bench",
            "revision": "533ef609325390361f6bd8a3640bb4764454a7bd"},
    "aiperf": {"source": "https://github.com/ai-dynamo/aiperf",
               "revision": "794f8bb75f8582f22e412d7e650fc71ca2a3d21a"},
}
LIL_REQUIREMENTS = ["httpx==0.28.1", "rich==14.1.0"]
MAX_ENVIRONMENT_BYTES = 8 * 1024**3
_INVENTORY = """import importlib.metadata as m, json, platform, re
rows=[]
for d in m.distributions():
    direct=json.loads(d.read_text('direct_url.json') or '{}')
    if direct.get('dir_info',{}).get('editable'): raise RuntimeError('editable package')
    rows.append(re.sub(r'[-_.]+', '-', d.metadata['Name'].lower())+'=='+d.version)
print(json.dumps({'version':platform.python_version(),'packages':sorted(rows)}))
"""
_TOKENIZER = """import os, socket, sys
os.environ['TIKTOKEN_CACHE_DIR']=sys.argv[1]
if sys.argv[2]=='offline':
    def blocked(*args, **kwargs): raise RuntimeError('tokenizer attempted network access')
    socket.socket.connect=blocked
    socket.create_connection=blocked
    socket.getaddrinfo=blocked
import tiktoken
enc=tiktoken.get_encoding('o200k_base')
assert enc.decode(enc.encode('offline tokenizer closure'))=='offline tokenizer closure'
print('o200k_base ready')
"""


def _fail(message, code="performance_preparation_mismatch"):
    raise BenchmarkJobError(code, message)


def _digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _files(root, *, allow_links=False):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            if not allow_links or not path.resolve(strict=True).is_relative_to(root.resolve()):
                _fail('performance source or tokenizer contains an unsafe link')
            result[path.relative_to(root).as_posix()] = hashlib.sha256(os.readlink(path).encode()).hexdigest()
            continue
        if path.is_file():
            result[path.relative_to(root).as_posix()] = _file_hash(path)
        elif not path.is_dir():
            _fail('performance source or tokenizer contains a non-regular file')
    return result


def _environment_digest(root):
    """Include directory modes and symlink targets as well as executable bytes."""
    digest = hashlib.sha256()
    for path in [root, *sorted(root.rglob('*'))]:
        relative = path.relative_to(root).as_posix()
        if relative == 'environment.json':
            continue
        mode = path.lstat().st_mode
        if path.is_symlink():
            interpreter = path.parent == root / 'bin' and re.fullmatch(r'python(?:3(?:\.12)?)?', path.name)
            if not path.resolve(strict=True).is_relative_to(root.resolve()) and not interpreter:
                _fail('performance environment contains an external link')
            digest.update(canonical_json_bytes([relative, 'link', mode, os.readlink(path)]))
        elif path.is_dir():
            digest.update(canonical_json_bytes([relative, 'directory', mode]))
        elif not path.is_file():
            _fail('performance environment contains a non-regular file')
        if path.is_file():
            digest.update(canonical_json_bytes([relative, 'file', path.stat().st_mode, _file_hash(path)]))
    return digest.hexdigest()


def _target(cache_root, key):
    root = Path(cache_root).resolve()
    path = root / key
    if (Path(key).is_absolute() or '..' in Path(key).parts or
            any(p.is_symlink() for p in (path, *path.parents) if p != root and p.is_relative_to(root)) or
            not path.resolve().is_relative_to(root)):
        _fail('performance environment escaped its cache')
    return path


def _identity(name, source_hashes):
    return {'schema': SCHEMA, 'runner': name, 'python_minor': '3.12',
            'platform': platform.system().lower(), 'architecture': platform.machine().lower(),
            'source': {**SOURCES[name], 'files_sha256': _digest(source_hashes)},
            'requirements': LIL_REQUIREMENTS if name == 'lil' else {'uv.lock': source_hashes.get('uv.lock'),
                'pyproject.toml': source_hashes.get('pyproject.toml'), 'frozen': True, 'editable': False}}


def _packages_valid(name, packages, source):
    if not isinstance(packages, list) or not packages or packages != sorted(set(packages)):
        return False
    if not all(isinstance(p, str) and re.fullmatch(r'[a-z0-9][a-z0-9.-]*==[^\s=]+', p) for p in packages):
        return False
    if name == 'lil':
        return all(p in packages for p in LIL_REQUIREMENTS)
    lock = tomllib.loads((source / 'uv.lock').read_text())
    allowed = {re.sub(r'[-_.]+', '-', row['name'].lower()) + '==' + row['version'] for row in lock['package']}
    return 'aiperf==0.13.0' in packages and all(p in allowed for p in packages)


def verify_performance_environment(receipt, *, cache_root, runner_name):
    """Validate receipt and current bytes before executing any cached Python."""
    try:
        if runner_name not in SOURCES or not isinstance(receipt, dict):
            _fail('performance environment receipt is absent or runner unsupported')
        source_hashes = receipt.get('source_hashes')
        if not isinstance(source_hashes, dict) or not source_hashes:
            _fail('performance source inventory is absent')
        identity = _identity(runner_name, source_hashes)
        key = 'performance-python-environments/' + _digest(identity)
        target = _target(cache_root, key)
        packages = receipt.get('resolved_packages')
        if (receipt.get('identity') != identity or any(receipt.get(k) != v for k, v in identity.items()) or
                receipt.get('cache_key') != key or receipt.get('executable') != 'bin/python' or
                receipt.get('source_path') != 'source' or not isinstance(receipt.get('python'), dict) or
                not re.fullmatch(r'3\.12\.[0-9]+', str(receipt.get('python', {}).get('version', ''))) or
                receipt.get('resolved_packages_sha256') != _digest(packages)):
            _fail('performance environment receipt identity changed')
        lock = target / 'environment.json'
        if lock.is_symlink() or not lock.is_file() or lock.stat().st_size > 8 * 1024**2:
            _fail('performance environment ownership receipt is absent')
        recorded = {k: v for k, v in receipt.items() if k not in {'cache_key', 'executable', 'reused'}}
        if (json.loads(lock.read_text()) != recorded or not (target / 'bin/python').is_file() or
                receipt.get('files_sha256') != _environment_digest(target) or
                _files(target / 'source', allow_links=True) != source_hashes or
                not _packages_valid(runner_name, packages, target / 'source')):
            _fail('performance environment bytes, packages or receipt changed')
        from .performance_adapter import SOURCE_HASHES
        if any(source_hashes.get(p) != digest for p, digest in SOURCE_HASHES[runner_name].items()):
            _fail('performance source guard changed')
        if runner_name == 'aiperf':
            for relative, expected_hash in SOURCE_HASHES[runner_name].items():
                if relative.startswith('src/'):
                    installed = target / 'lib/python3.12/site-packages' / relative.removeprefix('src/')
                    if installed.is_symlink() or _file_hash(installed) != expected_hash:
                        _fail('installed AIPerf source guard changed')
        tokenizer = receipt.get('tokenizer')
        if runner_name == 'aiperf':
            files = _files(target / 'tokenizer')
            expected = {'name': 'o200k_base', 'kind': 'builtin', 'target_model_tokenizer': False,
                        'cache_key': key + '/tokenizer', 'files': files, 'offline_verified': True}
            if not files or tokenizer != expected:
                _fail('performance offline tokenizer closure changed')
        elif tokenizer is not None:
            _fail('unexpected LIL tokenizer receipt')
        return str(target / 'bin/python')
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        raise BenchmarkJobError('performance_preparation_mismatch', 'performance environment could not be verified') from exc


def _bounded_runner(argv, cwd, timeout):
    from ..transports import _run_bounded_process
    return _run_bounded_process(argv, cwd=cwd, timeout=timeout,
                                max_output_bytes=8 * 1024**2, env={})


def _checked(runner, command, logs, *, cwd, env=None):
    # The command runner contract has no env argument. env -i also makes fake
    # runner assertions cover the exact production environment boundary.
    argv = ('/usr/bin/env', '-i', *(f'{key}={value}' for key, value in sorted((env or {}).items())), *command)
    try:
        result = runner(argv, str(cwd), 1800)
    except subprocess.TimeoutExpired as exc:
        raise BenchmarkJobError('performance_preparation_timeout', 'performance preparation command timed out') from exc
    stdout = result.stdout.decode('utf-8') if isinstance(result.stdout, bytes) else result.stdout or ''
    stderr, truncated = harnesses._bounded_text(result.stderr)
    bounded, output_truncated = harnesses._bounded_text(stdout)
    logs.append({'command': list(argv), 'returncode': result.returncode, 'stdout': bounded,
                 'stderr': stderr, 'output_truncated': truncated or output_truncated})
    if result.returncode or len(stdout.encode()) > 8 * 1024**2:
        _fail('managed performance preparation command failed', 'performance_preparation_failure')
    return stdout.strip()


def _sources(assets, cache_root, runner, logs, cwd, env):
    from .performance_adapter import SOURCE_HASHES
    roots, inventories = {}, {}
    for name, expected in SOURCES.items():
        asset = assets.get(name, {})
        if any(asset.get(k) != v for k, v in expected.items()) or not asset.get('cache_key'):
            _fail(f'{name} does not match the supported immutable source')
        root = _target(cache_root, asset['cache_key'])
        git = ('git', '-C', str(root))
        head = _checked(runner, (*git, 'rev-parse', 'HEAD'), logs, cwd=cwd, env=env)
        status = _checked(runner, (*git, 'status', '--porcelain', '--untracked-files=all'), logs, cwd=cwd, env=env)
        if head != expected['revision'] or any(line != '?? .anvil-harness-lock.json' for line in status.splitlines()):
            _fail(f'{name} cached source identity changed')
        listing = _checked(runner, (*git, 'ls-files', '--stage', '-z'), logs, cwd=cwd, env=env)
        files = {}
        for entry in filter(None, listing.split('\0')):
            metadata, relative = entry.split('\t', 1)
            mode, blob, stage = metadata.split()
            path = root / relative
            if (stage != '0' or mode not in {'100644', '100755', '120000'} or
                    not path.resolve(strict=True).is_relative_to(root.resolve()) or
                    (mode == '120000') != path.is_symlink() or
                    (mode != '120000' and not path.is_file())):
                _fail('unsupported performance source path or mode')
            data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
            if hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() != blob:
                _fail('performance source bytes differ from Git inventory')
            files[relative] = hashlib.sha256(data).hexdigest()
        if not files or any(files.get(p) != value for p, value in SOURCE_HASHES[name].items()):
            _fail('pinned performance source guard changed')
        roots[name], inventories[name] = root, files
    return roots, inventories


def prepare_performance_environments(profile, assets, *, cache_root, run_path, offline, runner=None, max_download_bytes=MAX_ENVIRONMENT_BYTES):
    """Prepare dependencies only; never contact a model or change serving state."""
    if platform.system() != 'Linux':
        _fail('managed performance runtime currently requires Linux', 'performance_platform_unsupported')
    if type(max_download_bytes) is not int or not 1 <= max_download_bytes <= 1024**4:
        _fail('invalid performance preparation disk bound')
    runner = runner or _bounded_runner
    Path(run_path).mkdir(parents=True, exist_ok=True)
    receipts, logs = {}, []
    try:
        with tempfile.TemporaryDirectory(prefix='performance-prepare-', dir=run_path) as scratch:
            scratch = Path(scratch)
            cwd, home = scratch / 'cwd', scratch / 'home'
            cwd.mkdir()
            home.mkdir()
            env = {'PATH': os.defpath, 'HOME': str(home), 'PYTHONDONTWRITEBYTECODE': '1',
                   'PIP_CONFIG_FILE': os.devnull, 'UV_NO_CONFIG': '1', 'UV_PYTHON_DOWNLOADS': 'never',
                   'UV_CACHE_DIR': str(scratch / 'uv-cache'), 'GIT_CONFIG_NOSYSTEM': '1',
                   'GIT_CONFIG_GLOBAL': os.devnull}
            roots, inventories = _sources(assets, cache_root, runner, logs, cwd, env)
            for name in SOURCES:
                identity = _identity(name, inventories[name])
                key = 'performance-python-environments/' + _digest(identity)
                target = _target(cache_root, key)
                python = str(target / 'bin/python')
                if target.exists():
                    try:
                        lock = json.loads((target / 'environment.json').read_text())
                    except (OSError, ValueError):
                        _fail('cached performance environment has no complete receipt')
                    receipt = {**lock, 'cache_key': key, 'executable': 'bin/python', 'reused': True}
                    verify_performance_environment(receipt, cache_root=cache_root, runner_name=name)
                    observed = json.loads(_checked(runner, (python, '-I', '-B', '-c', _INVENTORY), logs, cwd=cwd, env=env))
                    if observed != {'version': receipt['python']['version'], 'packages': receipt['resolved_packages']}:
                        _fail('cached performance Python or package inventory changed')
                    if name == 'aiperf':
                        _checked(runner, (python, '-I', '-B', '-c', _TOKENIZER, str(target / 'tokenizer'), 'offline'), logs, cwd=cwd, env=env)
                    verify_performance_environment(receipt, cache_root=cache_root, runner_name=name)
                    receipts[name] = receipt
                    continue
                if offline:
                    _fail('performance Python environment is absent offline', 'harness_assets_offline')
                executable = sys.executable if sys.version_info[:2] == (3, 12) else shutil.which('python3.12')
                uv = shutil.which('uv')
                if not executable or not uv:
                    _fail('performance preparation requires Python 3.12 and uv', 'performance_tool_absent')
                target.mkdir(parents=True)
                try:
                    _checked(runner, (executable, '-I', '-B', '-m', 'venv', '--without-pip', str(target)), logs, cwd=cwd, env=env)
                    for relative in inventories[name]:
                        destination = target / 'source' / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        original = roots[name] / relative
                        if original.is_symlink():
                            destination.symlink_to(os.readlink(original))
                        else:
                            shutil.copy2(original, destination)
                    if name == 'lil':
                        _checked(runner, (uv, 'pip', 'install', '--python', python, *LIL_REQUIREMENTS), logs, cwd=cwd, env=env)
                    else:
                        _checked(runner, (uv, 'sync', '--project', str(target / 'source'), '--python', python,
                                         '--frozen', '--no-dev', '--no-editable'), logs, cwd=cwd,
                                 env={**env, 'UV_PROJECT_ENVIRONMENT': str(target)})
                    observed = json.loads(_checked(runner, (python, '-I', '-B', '-c', _INVENTORY), logs, cwd=cwd, env=env))
                    if (not re.fullmatch(r'3\.12\.[0-9]+', observed['version']) or
                            not _packages_valid(name, observed['packages'], target / 'source')):
                        _fail('installed performance Python or packages do not match their pins')
                    tokenizer = None
                    if name == 'aiperf':
                        (target / 'tokenizer').mkdir()
                        for mode in ('online', 'offline'):
                            _checked(runner, (python, '-I', '-B', '-c', _TOKENIZER, str(target / 'tokenizer'), mode), logs, cwd=cwd, env=env)
                        tokenizer = {'name': 'o200k_base', 'kind': 'builtin', 'target_model_tokenizer': False,
                                     'cache_key': key + '/tokenizer', 'files': _files(target / 'tokenizer'), 'offline_verified': True}
                        if not tokenizer['files']:
                            _fail('offline tokenizer cache is empty')
                    if _files(target / 'source', allow_links=True) != inventories[name]:
                        _fail('performance build changed its pinned source snapshot')
                    occupied = sum(harnesses._directory_bytes(str(_target(cache_root, r['cache_key']))) for r in receipts.values())
                    occupied += harnesses._directory_bytes(str(target)) + harnesses._directory_bytes(str(scratch))
                    if occupied > max_download_bytes:
                        _fail('performance environment exceeded its disk bound')
                    for path in sorted((target / 'source').rglob('*'), reverse=True):
                        if not path.is_symlink():
                            path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)
                    (target / 'source').chmod(0o555)
                    lock = {**identity, 'identity': identity, 'python': {'version': observed['version']},
                            'source_path': 'source', 'source_hashes': inventories[name],
                            'resolved_packages': observed['packages'], 'resolved_packages_sha256': _digest(observed['packages']),
                            'tokenizer': tokenizer, 'files_sha256': _environment_digest(target)}
                    atomic_write_json(str(target / 'environment.json'), lock)
                    receipt = {**lock, 'cache_key': key, 'executable': 'bin/python', 'reused': False}
                    verify_performance_environment(receipt, cache_root=cache_root, runner_name=name)
                    receipts[name] = receipt
                except BaseException:
                    for path in [target / 'source', *(target / 'source').rglob('*')]:
                        if path.is_dir() and not path.is_symlink():
                            path.chmod(0o755)
                    shutil.rmtree(target)
                    raise
        atomic_write_json(str(Path(run_path) / 'performance-preparation.json'), {'status': 'ready', 'environments': receipts, 'command_logs': logs})
        return receipts
    except BaseException as exc:
        atomic_write_json(str(Path(run_path) / 'performance-preparation.json'), {'status': 'failed', 'error': str(exc), 'command_logs': logs})
        raise
