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
import subprocess
import sys
import tarfile
import tempfile

SCHEMA = 'anvil-connect.bundle/v1'
# Release archives are signed keylessly by this repository's release workflow
# at the matching connect-v* tag. Changing the workflow file name or repository
# breaks verification of every later release, by design.
SIGNER = 'https://github.com/fakoli/anvil-serving/.github/workflows/connect-release.yml@refs/tags/connect-v'
ISSUER = 'https://token.actions.githubusercontent.com'
ARCHIVE_NAME = r'anvil-connect-([0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?)-(linux-amd64|darwin-arm64|darwin-amd64)\.tar\.gz'
MAX_ARCHIVE = 1024 * 1024 * 1024


class SignatureError(ValueError):
    """The publisher signature policy refused the release."""


def unsigned(reason: str) -> dict:
    print('WARNING: installing WITHOUT publisher signature verification (--allow-unsigned): ' + reason
          + '. Only the checksums you supplied protect these bytes.', file=sys.stderr)
    return {'verified': False, 'reason': reason}


def verify_signature(archive: Path, signature: Path, version: str, *, allow_unsigned: bool) -> dict:
    """Verify a Sigstore bundle with an installed verifier; never skip silently."""
    identity = SIGNER + version
    if not signature.is_file():
        if not allow_unsigned:
            raise SignatureError('No Sigstore signature at ' + str(signature) + '. Download it from the same release,'
                                 ' or pass --allow-unsigned to install a release published without one')
        return unsigned('no signature was published')
    if shutil.which('cosign'):
        verifier = 'cosign'
        command = [shutil.which('cosign'), 'verify-blob', '--bundle', str(signature), '--certificate-identity', identity,
                   '--certificate-oidc-issuer', ISSUER, str(archive)]
    elif shutil.which('sigstore'):
        verifier = 'sigstore'
        command = [shutil.which('sigstore'), 'verify', 'identity', '--bundle', str(signature), '--cert-identity', identity,
                   '--cert-oidc-issuer', ISSUER, str(archive)]
    else:
        if not allow_unsigned:
            raise SignatureError('Neither cosign nor sigstore is installed, so the release signature cannot be checked.'
                                 ' Install one, or pass --allow-unsigned')
        return unsigned('no Sigstore verifier is installed')
    try:
        completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SignatureError('Sigstore verification failed: ' + verifier + ' could not run') from error
    if completed.returncode != 0:
        detail = (completed.stderr or b'').decode('utf-8', 'replace').strip().splitlines()[-1:]
        # An invalid or foreign signature is evidence of tampering: --allow-unsigned never overrides it.
        raise SignatureError('Sigstore verification failed: the archive is not signed by ' + identity
                             + ' (issuer ' + ISSUER + ')' + ('; ' + detail[0][:300] if detail else ''))
    return {'verified': True, 'verifier': verifier, 'identity': identity, 'issuer': ISSUER}


def private_copy(source: Path, destination: Path) -> None:
    """Snapshot the download so the verified bytes are the extracted bytes."""
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream, destination.open('xb') as output:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARCHIVE:
            raise ValueError('Release archive is not a bounded regular file')
        shutil.copyfileobj(stream, output)


def extract_archive(archive: Path, destination: Path, top: str) -> Path:
    """Extract only regular files and directories beneath the expected top directory."""
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        for member in members:
            parts = member.name.split('/')
            if (len(members) > 128 or parts[0] != top or '' in parts or '.' in parts or '..' in parts
                    or not (member.isfile() or member.isdir()) or member.size > 256 * 1024 * 1024):
                raise ValueError('Release archive contains an unsafe member')
        for member in members:
            target = destination.joinpath(*member.name.split('/'))
            if member.isdir():
                target.mkdir(mode=0o755, parents=True, exist_ok=True)
                continue
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            with source.extractfile(member) as stream, target.open('xb') as output:
                shutil.copyfileobj(stream, output)
            target.chmod(member.mode & 0o755)
    return destination / top


def install_archive(archive: Path, prefix: Path, *, role: str, expected: str, apply: bool, allow_unsigned: bool = False) -> dict:
    """Verify a downloaded release archive's signature, then extract and install it."""
    match = re.fullmatch(ARCHIVE_NAME, archive.name)
    if not match:
        raise ValueError('Release archive name is not a Connect bundle')
    version, platform_name = match.groups()
    with tempfile.TemporaryDirectory(prefix='anvil-connect-verify-') as work:
        snapshot = Path(work) / archive.name
        private_copy(archive, snapshot)
        signature = verify_signature(snapshot, Path(str(archive) + '.sigstore.json'), version, allow_unsigned=allow_unsigned)
        root = extract_archive(snapshot, Path(work) / 'bundle', archive.name[:-len('.tar.gz')])
        manifest, _ = load_bundle(root, expected)
        if manifest['version'] != version or manifest['platform'] != platform_name:
            raise ValueError('Release archive name does not match its manifest')
        return {**install(root, prefix, role=role, expected=expected, apply=apply), 'signature': signature}


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
            try:
                parent.mkdir(mode=0o755)
            except FileExistsError:
                # Another installer may have created it. Treat that as an
                # existing directory: never repair metadata we did not create.
                pass
            else:
                fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    created = os.fstat(fd)
                    if not stat.S_ISDIR(created.st_mode) or created.st_uid != os.geteuid():
                        raise ValueError('Install ancestry changed during creation')
                    # mkdir honors the caller's umask. These are installer-owned
                    # directories, so publish their fixed traversal mode before
                    # anything is placed underneath them.
                    os.fchmod(fd, 0o755)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
            raise ValueError('Install ancestry must be owned and not writable by other users')


def checked_managed_directory(path: Path, *, create: bool = False) -> None:
    """Require an installer-owned release directory to remain executable."""
    checked_directory(path, create=create)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o7777 != 0o755:
        raise ValueError('Managed installation directory metadata drifted')


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
    if installed_names is not None:
        checked_managed_directory(root)
        checked_managed_directory(root / 'bin')
        checked_managed_directory(root / 'share')
    for name, entry in files.items():
        if not re.fullmatch(r'(?:bin|share)/[a-zA-Z0-9._-]+', name) or set(entry) != {'sha256', 'size', 'mode'}:
            raise ValueError('Invalid bundle member')
        if entry['mode'] not in (0o644, 0o755) or not isinstance(entry['size'], int) or not 0 <= entry['size'] <= 256 * 1024 * 1024:
            raise ValueError('Invalid bundle metadata')
        if installed_names is not None and name not in installed_names:
            continue
        data = read_relative(root, name, entry['size'], expected_mode=entry['mode'] if installed_names is not None else None)
        if len(data) != entry['size'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('Bundle checksum mismatch')
    if 'bin/anvil-connect' not in files:
        raise ValueError('Missing native component')
    return manifest, raw


def host_platform() -> str:
    architecture = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(platform.machine().lower(), 'unsupported')
    return platform.system().lower() + '-' + architecture


def read_relative(root: Path, name: str, maximum: int, *, expected_mode: int | None = None) -> bytes:
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
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum
                    or info.st_mode & 0o022 or (expected_mode is not None and info.st_mode & 0o7777 != expected_mode)):
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
        checked_managed_directory(prefix)
        checked_managed_directory(prefix / 'bin')
        checked_managed_directory(prefix / 'releases')
        checked_managed_directory(prior_root)
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
        checked_managed_directory(destination)
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
            checked_managed_directory(work / 'releases', create=True)
            checked_managed_directory(work / 'bin', create=True)
            dest = work / result['release']
            if not dest.exists():
                stage = Path(tempfile.mkdtemp(prefix='.release-', dir=work / 'releases'))
                try:
                    stage.chmod(0o755)
                    for name in sorted(names):
                        target = stage / name
                        checked_managed_directory(target.parent, create=True)
                        data = read_relative(root, name, manifest['files'][name]['size'])
                        if hashlib.sha256(data).hexdigest() != manifest['files'][name]['sha256']:
                            raise ValueError('Bundle changed during installation')
                        write_durable(target, data, manifest['files'][name]['mode'])
                    write_durable(stage / 'bundle.json', raw)
                    write_durable(stage / 'installation.json', (json.dumps(result, sort_keys=True) + '\n').encode())
                    load_bundle(stage, expected, installed_names=names)
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
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--archive', type=Path, help='Downloaded release .tar.gz; its .sigstore.json must sit beside it')
    source.add_argument('--bundle', type=Path, default=Path(__file__).resolve().parent,
                        help='Already-extracted bundle; requires --allow-unsigned')
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--role', choices=('gateway', 'connector', 'client'), required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--allow-unsigned', action='store_true',
                        help='Proceed when no signature is published or no verifier is installed (never on a bad signature)')
    parser.add_argument('--confirm', action='store_true')
    args = parser.parse_args()
    try:
        if args.archive is not None:
            result = install_archive(args.archive, args.prefix, role=args.role, expected=args.manifest_sha256,
                                     apply=args.confirm, allow_unsigned=args.allow_unsigned)
        else:
            if not args.allow_unsigned:
                raise SignatureError('An extracted bundle cannot be checked against its release signature.'
                                     ' Pass --archive with the downloaded .tar.gz, or pass --allow-unsigned')
            signature = unsigned('installing from an already-extracted bundle')
            result = {**install(args.bundle, args.prefix, role=args.role, expected=args.manifest_sha256, apply=args.confirm),
                      'signature': signature}
    except SignatureError as error:
        parser.exit(1, 'Connect installation refused: ' + str(error) + '.\n')
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(1, 'Connect installation failed: check bundle integrity, platform, role and prefix ownership.\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
