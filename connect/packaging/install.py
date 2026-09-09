#!/usr/bin/env python3
"""Install a verified Connect bundle into its own prefix; never start services."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import tempfile

SCHEMA = 'anvil-connect.bundle/v1'


def read_file(path: Path, maximum: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum or info.st_nlink != 1:
            raise ValueError('Bundle contains an unsafe file')
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('Bundle file exceeds its size limit')
    return data


def checked_directory(path: Path, *, create: bool = False) -> None:
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Install prefix must be a clean absolute path')
    for parent in reversed([path, *path.parents][:-1]):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            if not create:
                continue
            parent.mkdir(mode=0o755)
            info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
            raise ValueError('Install ancestry must be owned and not writable by other users')


def load_bundle(root: Path, expected: str, *, installed_names: set[str] | None = None) -> tuple[dict, bytes]:
    raw = read_file(root / 'bundle.json', 128 * 1024)
    if not re.fullmatch('[a-f0-9]{64}', expected) or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Bundle manifest digest does not match the release receipt')
    manifest = json.loads(raw)
    if set(manifest) != {'schema', 'version', 'source_revision', 'platform', 'roles', 'files'} or manifest['schema'] != SCHEMA:
        raise ValueError('Unsupported bundle manifest')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?', manifest['version']):
        raise ValueError('Invalid Connect version')
    if not re.fullmatch('[a-f0-9]{40}', manifest['source_revision']):
        raise ValueError('Invalid source revision')
    allowed = {'linux-amd64': {'gateway', 'connector', 'client'}, 'darwin-arm64': {'client'}, 'darwin-amd64': {'client'}}
    target = manifest['platform']
    if target not in allowed or set(manifest['roles']) != allowed[target]:
        raise ValueError('Invalid role/platform bundle')
    files = manifest['files']
    if not isinstance(files, dict) or not 1 <= len(files) <= 64:
        raise ValueError('Invalid file list')
    if installed_names is not None and not installed_names <= set(files):
        raise ValueError('Installed role members are missing from the release manifest')
    for name, entry in files.items():
        if not re.fullmatch(r'(?:bin|share)/[a-zA-Z0-9._-]+', name) or set(entry) != {'sha256', 'size', 'mode'}:
            raise ValueError('Invalid bundle member')
        if entry['mode'] not in (0o644, 0o755) or not isinstance(entry['size'], int) or not 0 <= entry['size'] <= 256 * 1024 * 1024:
            raise ValueError('Invalid bundle metadata')
        if installed_names is not None and name not in installed_names:
            continue
        data = read_relative(root, name, entry['size'])
        if len(data) != entry['size'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('Bundle checksum mismatch')
    if 'bin/anvil-connect' not in files:
        raise ValueError('Missing native component')
    return manifest, raw


def host_platform() -> str:
    architecture = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(platform.machine().lower(), 'unsupported')
    return platform.system().lower() + '-' + architecture


def read_relative(root: Path, name: str, maximum: int) -> bytes:
    """Open every member beneath retained no-follow directory descriptors."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in name.split('/')[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        member = os.open(name.split('/')[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(member, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum or info.st_mode & 0o022:
                raise ValueError('Unsafe release member')
            data = stream.read(maximum + 1)
            if len(data) > maximum:
                raise ValueError('Release member exceeds its bound')
            return data
    finally:
        os.close(fd)


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_durable(path: Path, data: bytes, mode: int = 0o644) -> None:
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fchmod(stream.fileno(), mode)
        os.fsync(stream.fileno())


def install(root: Path, prefix: Path, *, role: str, expected: str, apply: bool) -> dict:
    target = host_platform()
    if target not in {'linux-amd64', 'darwin-amd64', 'darwin-arm64'}:
        raise ValueError('This platform is not supported by the standalone installer')
    manifest, raw = load_bundle(root, expected)
    if manifest['platform'] != target or role not in manifest['roles']:
        raise ValueError('This bundle does not support the selected platform and role')
    checked_directory(prefix)
    release_name = manifest['version'] + '-' + manifest['source_revision'][:12]
    names = {'bin/anvil-connect', 'share/LICENSE', 'share/THIRD-PARTY.json', 'share/THIRD-PARTY-LICENSES.txt'}
    if role in {'gateway', 'connector'}:
        names |= {'bin/anvil-connect-ctl', 'bin/wstunnel'}
    if role == 'gateway':
        names |= {'bin/caddy', 'bin/authelia'}
    if not names <= set(manifest['files']):
        raise ValueError('Bundle is missing required role components')
    result = {'schema': 'anvil-connect.installation/v1', 'role': role, 'version': manifest['version'], 'platform': manifest['platform'], 'source_revision': manifest['source_revision'], 'release': 'releases/' + release_name, 'files': sorted(names), 'manifest_sha256': expected}
    previous = None
    existing = prefix.exists() and any(prefix.iterdir())
    if existing:
        marker = prefix / 'installation.json'
        active = prefix / 'current'
        if not marker.is_symlink() or os.readlink(marker) != 'current/installation.json' or not active.is_symlink():
            raise ValueError('Refusing to adopt an unowned nonempty prefix')
        selected = os.readlink(active)
        if not re.fullmatch(r'releases/[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?-[a-f0-9]{12}', selected):
            raise ValueError('Active installation link drifted')
        prior_root = prefix / selected
        checked_directory(prior_root)
        previous = json.loads(read_relative(prior_root, 'installation.json', 128 * 1024))
        if previous.get('schema') != result['schema'] or previous.get('role') != role or previous.get('release') != selected or previous.get('files') != sorted(names):
            raise ValueError('Existing installation has a different owner or role')
        prior_manifest, _ = load_bundle(prior_root, previous['manifest_sha256'], installed_names=names)
        expected_previous = {**result, 'version': prior_manifest['version'], 'platform': prior_manifest['platform'], 'source_revision': prior_manifest['source_revision'], 'release': 'releases/' + prior_manifest['version'] + '-' + prior_manifest['source_revision'][:12], 'manifest_sha256': previous['manifest_sha256']}
        if previous != expected_previous or prior_manifest['platform'] != host_platform() or role not in prior_manifest['roles']:
            raise ValueError('Existing release receipt drifted from its manifest')
        for name in sorted(names):
            if name.startswith('bin/'):
                link = prefix / name
                if not link.is_symlink() or os.readlink(link) != '../current/' + name:
                    raise ValueError('Installed command link drifted')
    destination = prefix / result['release']
    if os.path.lexists(destination):
        checked_directory(destination)
        load_bundle(destination, expected, installed_names=names)
        if json.loads(read_relative(destination, 'installation.json', 128 * 1024)) != result:
            raise ValueError('Existing release receipt drifted')
    current = previous == result
    if apply and not current:
        checked_directory(prefix.parent, create=True)
        # A first install is published with one directory rename. Until then a
        # crash leaves only a disposable sibling, never a half-owned prefix.
        first_stage = None
        work = prefix
        if not existing:
            first_stage = Path(tempfile.mkdtemp(prefix='.anvil-connect-', dir=prefix.parent))
            first_stage.chmod(0o755)
            work = first_stage
        try:
            checked_directory(work / 'releases', create=True)
            checked_directory(work / 'bin', create=True)
            dest = work / result['release']
            if not dest.exists():
                stage = Path(tempfile.mkdtemp(prefix='.release-', dir=work / 'releases'))
                try:
                    stage.chmod(0o755)
                    for name in sorted(names):
                        target = stage / name
                        target.parent.mkdir(mode=0o755, exist_ok=True)
                        data = read_relative(root, name, manifest['files'][name]['size'])
                        if hashlib.sha256(data).hexdigest() != manifest['files'][name]['sha256']:
                            raise ValueError('Bundle changed during installation')
                        write_durable(target, data, manifest['files'][name]['mode'])
                    write_durable(stage / 'bundle.json', raw)
                    write_durable(stage / 'installation.json', (json.dumps(result, sort_keys=True) + '\n').encode())
                    sync_directory(stage / 'bin')
                    sync_directory(stage / 'share')
                    sync_directory(stage)
                    os.rename(stage, dest)
                    sync_directory(work / 'releases')
                finally:
                    if stage.exists():
                        shutil.rmtree(stage)
            if not existing:
                for name in sorted(names):
                    if name.startswith('bin/'):
                        (work / name).symlink_to('../current/' + name)
                (work / 'installation.json').symlink_to('current/installation.json')
                sync_directory(work / 'bin')
            # The immutable release includes its receipt. One atomic current
            # switch changes both command bytes and metadata, including rollback.
            temporary = work / ('.activate-' + next(tempfile._get_candidate_names()))
            temporary.symlink_to(result['release'])
            os.replace(temporary, work / 'current')
            sync_directory(work)
            if first_stage is not None:
                os.rename(first_stage, prefix)
                sync_directory(prefix.parent)
        finally:
            if first_stage is not None and first_stage.exists():
                shutil.rmtree(first_stage)
    return {**result, 'applied': apply, 'state': 'current' if current else 'installed' if apply else 'planned', 'services_started': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--bundle', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--role', choices=('gateway', 'connector', 'client'), required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--confirm', action='store_true')
    args = parser.parse_args()
    try:
        result = install(args.bundle, args.prefix, role=args.role, expected=args.manifest_sha256, apply=args.confirm)
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(1, 'Connect installation failed: check bundle integrity, platform, role and prefix ownership.\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
