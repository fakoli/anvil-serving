"""Explicit whole-cache retirement; never a prune or selective blob collector."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from . import model_cache_native as native
from . import model_cache_volume as volume_ops
from . import paths, topology

Error = volume_ops.ModelCacheVolumeError
_HF_ENTRIES = {"hub", "xet", "modules", "runtime_wheels", ".agent_harnesses.json",
               ".check_for_update_done", ".check_for_skill_update_done"}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _safe_name(value):
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise Error("cache path contains control characters")


def _reject_mounts(root):
    # ismount() does not reliably detect bind mounts on the same filesystem.
    # Linux mountinfo is the authority for both those and ordinary mounts.
    raw = volume_ops._read_metadata(Path('/proc/self/mountinfo'), 4 * 1024 * 1024)
    lines = raw.splitlines()
    if not lines or len(lines) > volume_ops._MAX_ENTRIES:
        raise Error('mount audit is empty or exceeds its bound')
    for line in lines:
        fields = line.split()
        if len(fields) < 10 or '-' not in fields[6:] or not fields[4].startswith('/'):
            raise Error('mount audit contains an invalid record')
        mount = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4])
        if Path(mount).is_relative_to(root):
            raise Error('cache contains a mount boundary')


def inventory(root, *, native_unlink=False):
    """Bind a complete bounded tree, including partial downloads and orphan blobs."""
    root = volume_ops._plain_dir(root, "cache root")
    _safe_name(str(root))
    if root in (Path('/'), Path.home()) or os.path.ismount(root) or (root / '.git').exists() or (root / 'operator-topology.toml').exists():
        raise Error("refusing a home, checkout, operator home, or filesystem root")
    _reject_mounts(root)
    before = root.stat()
    budget = {"entries": 0, "deadline": time.monotonic() + volume_ops._MAX_SECONDS}
    def read(path, limit, *, binary=False):
        if time.monotonic() > budget['deadline']:
            raise Error('cache inventory timed out')
        value = volume_ops._read_metadata(path, limit, binary=binary)
        if time.monotonic() > budget['deadline']:
            raise Error('cache inventory timed out')
        return value

    records = {}
    pending = [root]
    allocated = 0
    shared_files = []
    shared_inodes = set()
    while pending:
        current = pending.pop()
        for entry in volume_ops._children(current, budget):
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            _safe_name(relative)
            if path.name == '.env':
                raise Error('cache contains a secret environment file')
            info = path.lstat()
            if info.st_dev != before.st_dev or os.path.ismount(path) or len(path.relative_to(root).parts) > 32:
                raise Error("cache crosses a mount or depth boundary")
            row = {"binding": native._binding(info)}
            if stat.S_ISDIR(info.st_mode):
                row['kind'] = 'directory'
                pending.append(path)
            elif stat.S_ISLNK(info.st_mode):
                if info.st_nlink != 1:
                    raise Error("cache contains a hardlinked symlink")
                target = os.readlink(path)
                _safe_name(target)
                if os.path.isabs(target) or not Path(os.path.realpath(path)).is_relative_to(root):
                    raise Error("cache symlink escapes its root")
                row.update(kind='symlink', target=target)
            elif stat.S_ISREG(info.st_mode) and (info.st_nlink == 1 or native_unlink):
                row.update(kind='file', bytes=info.st_size)
                if info.st_nlink == 1:
                    allocated += info.st_blocks * 512
                else:
                    inode = (info.st_dev, info.st_ino)
                    if inode in shared_inodes or relative.endswith('.lock'):
                        raise Error('native cache contains internal hardlinks or a shared lock')
                    shared_inodes.add(inode)
                    shared_files.append(relative)
                # Small metadata is bound by content as well as inode/timestamps.
                if info.st_size <= 1024 * 1024:
                    row['sha256'] = hashlib.sha256(read(path, 1024 * 1024, binary=True)).hexdigest()
            else:
                raise Error("cache contains a hardlink or special file")
            records[relative] = row
    top = {name.split('/')[0] for name in records}
    identities = set()
    if 'hub' in top:
        if top - _HF_ENTRIES:
            raise Error("HF cache contains unknown root entries")
        layout = 'huggingface-home'
        repos = set()
        for name in records:
            parts = name.split('/')
            if len(parts) >= 2 and parts[1].startswith('models--'):
                encoded = parts[1].split('--')
                if len(encoded) != 3 or not volume_ops._REPO.fullmatch('/'.join(encoded[1:])):
                    raise Error("invalid HF repository identity")
                repos.add('/'.join(encoded[1:]))
                if len(parts) == 4 and parts[2] in {'snapshots', 'trees'}:
                    revision = parts[3].removesuffix('.json')
                    if not volume_ops._REVISION.fullmatch(revision):
                        raise Error("invalid HF snapshot/tree identity")
                    identities.add(revision)
            if len(parts) == 2 and parts[0] == 'hub' and not (
                parts[1].startswith(('models--', 'kernels--')) or parts[1] in {'blobs', '.locks', 'CACHEDIR.TAG'}
            ):
                raise Error("HF cache contains an unknown hub entry")
            if name.startswith('hub/blobs/') and name.endswith('.refs'):
                for alias in read(root / name, 16 * 1024).splitlines():
                    _safe_name(alias)
                    if os.path.isabs(alias) or not Path(os.path.abspath(root / 'hub' / alias)).is_relative_to(root / 'hub'):
                        raise Error("global blob references another cache")
        if not repos:
            raise Error("HF cache lacks a model repository")
        identities.update(repos)
    else:
        layout = 'flat-model'
        if ('config.json' not in records or records['config.json']['kind'] != 'file'
                or not any('/' not in name and name.endswith('.safetensors') for name in records)):
            raise Error("native directory lacks a flat model signature")
        config = json.loads(read(root / 'config.json', 1024 * 1024))
        if not isinstance(config, dict) or not isinstance(config.get('model_type'), str):
            raise Error("flat model config lacks model_type")
        source = root / '.source-revision'
        revisions = set()
        if source.exists():
            revisions.add(read(source, 256).strip())
        for name in records:
            if name.startswith('.cache/huggingface/download/') and name.endswith('.metadata'):
                lines = read(root / name, 4096).splitlines()
                if not lines:
                    raise Error('flat model source metadata is empty')
                revisions.add(lines[0])
        if len(revisions) != 1 or not all(volume_ops._REVISION.fullmatch(r) for r in revisions):
            raise Error("flat model lacks one unambiguous source revision")
        identities.update(revisions)
        known = {'.gitattributes', '.quantization-complete', '.source-revision', 'LICENSE', 'README.md',
                 'chat_template.jinja', 'config.json', 'generation_config.json', 'tokenizer.json',
                 'tokenizer_config.json', 'processor_config.json', 'preprocessor_config.json',
                 'video_preprocessor_config.json', 'merges.txt', 'vocab.json', 'crc32.txt',
                 'safetensors-md5sum.txt', 'model.safetensors.index.json', 'model-sha256.json',
                 'quantization-manifest.json', 'reconstruction-metrics-experts.json',
                 'reconstruction-metrics-fp8.json', 'runpod-build-receipt.json',
                 'hf_quant_config.json', 'amax_checkpoint.json'}
        for name, row in records.items():
            if row['kind'] == 'directory':
                if name not in {'.cache', '.cache/huggingface', '.cache/huggingface/download'} and not any(
                    entry.startswith(name + '/') and records[entry]['kind'] != 'directory' for entry in records
                ):
                    raise Error('flat model contains an unknown empty directory')
                continue
            if name.startswith('.cache/'):
                prefix = '.cache/huggingface/download/'
                if name in {'.cache/huggingface/.gitignore', '.cache/huggingface/CACHEDIR.TAG'} or (
                    name.startswith('.cache/huggingface/trees/') and name.split('/')[-1] == next(iter(revisions)) + '.json'
                    and len(name.split('/')) == 4
                ):
                    continue
                if not name.startswith(prefix) or not name.endswith(('.metadata', '.lock')):
                    raise Error('flat model contains unknown download metadata')
                artifact = name[len(prefix):].rsplit('.', 1)[0]
                if artifact not in records or records[artifact]['kind'] != 'file':
                    raise Error('download metadata has no matching model artifact')
            elif '/' in name:
                receipt = '.cache/huggingface/download/' + name + '.metadata'
                if receipt not in records or records[receipt]['kind'] != 'file':
                    raise Error('nested flat-model asset lacks a source receipt')
            elif name not in known and not name.endswith('.safetensors'):
                raise Error('flat model contains an unknown root file')
    if native._binding(root.stat()) != native._binding(before):
        raise Error("cache changed during inventory")
    return {'root': str(root), 'root_binding': native._binding(before), 'layout': layout,
            'identities': sorted(identities), 'entries': records, 'allocated_bytes': allocated, 'shared_files_preserved_elsewhere': shared_files}


@contextmanager
def _fences(root, tree):
    """Hold root and every existing download lock; external writers must be quiescent."""
    if volume_ops.fcntl is None:
        raise Error("whole-cache discard requires POSIX locks")
    with ExitStack() as stack:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, fd)
        native._require_binding(fd, tree['root_binding'], 'root')
        volume_ops.fcntl.flock(fd, volume_ops.fcntl.LOCK_EX | volume_ops.fcntl.LOCK_NB)
        for name, row in tree['entries'].items():
            if name.endswith('.lock'):
                if row['kind'] != 'file':
                    raise Error("download lock is not a regular file")
                parent = fd
                for part in Path(name).parts[:-1]:
                    parent = native._open_directory(part, parent)
                    stack.callback(os.close, parent)
                lock = os.open(Path(name).name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                stack.callback(os.close, lock)
                native._require_binding(lock, row['binding'], 'download lock')
                volume_ops.fcntl.flock(lock, volume_ops.fcntl.LOCK_EX | volume_ops.fcntl.LOCK_NB)
        yield fd


def inspect(*, config_home, cache_dir=None, volume=None, non_model_root_views=(), runner=subprocess.run,
            check_locks=True):
    if os.name != 'posix' or volume_ops.fcntl is None:
        raise Error("whole-cache discard requires POSIX")
    if bool(cache_dir) == bool(volume):
        raise Error("select exactly one cache directory or Docker volume")
    home = volume_ops._config_home(config_home)
    if home != Path(paths.config_home()).resolve(strict=True):
        raise Error('config home must match the selected ANVIL_SERVING_HOME')
    topology.load_topology(str(home / 'operator-topology.toml'))
    home_binding = native._binding(home.stat())
    identity = None
    if volume:
        if not volume_ops._VOLUME.fullmatch(volume):
            raise Error("invalid Docker volume name")
        root, identity, _ = volume_ops._volume(volume, runner=runner)
    else:
        root = volume_ops._plain_dir(cache_dir, 'cache root')
    if volume_ops._overlaps(home, root):
        raise Error("cache overlaps operator configuration")
    tree = inventory(root, native_unlink=not volume)
    if check_locks:
        with _fences(root, tree):
            pass
    containers = volume_ops._container_refs(volume, root, non_model_root_views=non_model_root_views, runner=runner)
    bindings = []
    configured = volume_ops._config_refs(home, volume=volume, repo_id=None, revision=None, mountpoint=root,
                                        runner=runner, model_identities=tree['identities'], bindings=bindings)
    fds = volume_ops._native_fd_refs(root)
    if native._binding(home.stat()) != home_binding:
        raise Error("operator home changed during audit")
    fingerprint = _digest({'tree': tree, 'volume': identity, 'home': str(home),
                           'home_binding': home_binding, 'config': bindings,
                           'root_views': sorted(non_model_root_views)})
    blocked = []
    if containers: blocked.append('referenced by a running or stopped container')
    if configured: blocked.append('referenced by operator configuration')
    if fds: blocked.append('open by a native process')
    return {'schema': 'whole-model-cache-discard/v1', 'root': str(root), 'volume': identity,
            'tree': tree, 'fingerprint': fingerprint, 'operator_config_home': str(home),
            'config_bindings': bindings, 'references': {'containers': containers, 'configured': configured, 'fds': fds},
            'blocked_reasons': blocked, 'removable': not blocked}


def _unlink_tree(fd, records, prefix=''):
    names = set(os.listdir(fd))
    expected = {name[len(prefix):].split('/')[0] for name in records if name.startswith(prefix)}
    if names != expected:
        raise Error("cache entries changed before deletion")
    for name in sorted(names):
        relative = prefix + name
        row = records[relative]
        if native._binding(os.stat(name, dir_fd=fd, follow_symlinks=False)) != row['binding']:
            raise Error("cache entry changed before deletion")
        if row['kind'] == 'directory':
            child = native._open_directory(name, fd)
            try:
                native._require_binding(child, row['binding'], 'directory')
                _unlink_tree(child, records, relative + '/')
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=fd)
        else:
            os.unlink(name, dir_fd=fd)


def discard(*, expected_fingerprint=None, writers_quiesced=False, inventory_reviewed=False,
            confirm=False, dry_run=False, runner=subprocess.run, **selection):
    first = inspect(runner=runner, **selection)
    result = {'applied': False, 'outcome': 'blocked' if first['blocked_reasons'] else 'preview', 'inspection': first}
    if dry_run or not confirm or first['blocked_reasons']:
        return result
    if not writers_quiesced or not inventory_reviewed:
        raise Error("apply requires --writers-quiesced and --inventory-reviewed")
    if expected_fingerprint != first['fingerprint']:
        raise Error("preview fingerprint changed or missing")
    root = Path(first['root'])
    anchor = root.parent.parent if selection.get('volume') else root.parent
    if anchor.stat().st_dev != root.stat().st_dev:
        raise Error('no surviving free-space measurement path')
    before = os.statvfs(anchor)
    with _fences(root, first['tree']) as fd:
        second = inspect(runner=runner, check_locks=False, **selection)
        if not second['removable'] or second['fingerprint'] != first['fingerprint']:
            raise Error("cache, configuration, or consumers changed before deletion")
        result['verification_fingerprint'] = second['fingerprint']
        if selection.get('volume'):
            result['removal_attempted'] = True
            try:
                volume_ops._run(['volume', 'rm', selection['volume']], runner=runner)
            except Error as exc:
                return {**result, 'outcome': 'unknown', 'error': str(exc)}
        else:
            parent = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                if native._binding(os.stat(root.name, dir_fd=parent, follow_symlinks=False)) != first['tree']['root_binding']:
                    raise Error("cache root changed before deletion")
                result['removal_attempted'] = True
                try:
                    _unlink_tree(fd, first['tree']['entries'])
                    current = os.stat(root.name, dir_fd=parent, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) != tuple(first['tree']['root_binding'][:2]):
                        raise Error('cache root replaced during deletion')
                    os.rmdir(root.name, dir_fd=parent)
                except (Error, native.NativeCacheError, OSError) as exc:
                    return {**result, 'outcome': 'partial', 'error': str(exc)}
            finally:
                os.close(parent)
    try:
        if selection.get('volume'):
            try:
                volume_ops._volume(selection['volume'], runner=runner)
            except Error as exc:
                if 'no such volume' not in str(exc).lower():
                    raise Error('post-discard volume verification failed') from exc
            else:
                raise Error('Docker still resolves the discarded volume')
        if os.path.lexists(root):
            raise Error('cache still exists after discard')
    except (Error, OSError) as exc:
        # A failed postcheck is not proof that an already dispatched deletion
        # had no effect. Never report a safe-to-retry refusal here.
        return {**result, 'outcome': 'unknown', 'error': str(exc)}
    try:
        after = os.statvfs(anchor)
        delta = after.f_bavail * after.f_frsize - before.f_bavail * before.f_frsize
    except OSError:
        delta = None
    return {**result, 'applied': True, 'outcome': 'removed',
            'actual_free_space_delta_bytes': delta}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='anvil-serving models cache discard',
        description='Preview or discard one entirely retired model cache. Local Linux owner only; no force or selective pruning.')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--cache-dir')
    group.add_argument('--volume')
    parser.add_argument('--config-home', required=True)
    parser.add_argument('--non-model-root-view', action='append', default=[], dest='non_model_root_views')
    parser.add_argument('--expected-fingerprint')
    parser.add_argument('--writers-quiesced', action='store_true')
    parser.add_argument('--inventory-reviewed', action='store_true')
    parser.add_argument('--confirm', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = discard(**vars(args))
    except (Error, native.NativeCacheError, OSError, ValueError) as exc:
        print(json.dumps({'applied': False, 'outcome': 'refused', 'error': str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result['outcome'] in {'preview', 'removed'} else 1
