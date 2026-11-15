from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys

import pytest

ROOT = Path(__file__).parents[2]
requires_posix = pytest.mark.skipif(os.name != 'posix', reason='Native POSIX installer filesystem contract; Windows bundles are unsupported')


def load_module(name: str):
    spec = importlib.util.spec_from_file_location('connect_' + name, ROOT / 'connect/packaging' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bundle(tmp_path: Path, *, platform_name: str = 'darwin-arm64', roles: list[str] | None = None, executable: bytes = b'fixture bytes'):
    installer = load_module('install')
    root = tmp_path / 'bundle'
    root.mkdir()
    files = {}
    for name in ('bin/anvil-connect', 'share/LICENSE', 'share/THIRD-PARTY.json', 'share/THIRD-PARTY-LICENSES.txt'):
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(executable if name == 'bin/anvil-connect' else b'fixture bytes')
        path.chmod(0o755 if name.startswith('bin/') else 0o644)
        files[name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size': path.stat().st_size, 'mode': 0o755 if name.startswith('bin/') else 0o644}
    value = {'schema': installer.SCHEMA, 'version': '0.1.0', 'source_revision': 'a' * 40, 'platform': platform_name, 'roles': roles or ['client'], 'files': files}
    raw = json.dumps(value).encode()
    (root / 'bundle.json').write_bytes(raw)
    return installer, root, hashlib.sha256(raw).hexdigest()


def private_test_directory(path: Path, create: bool = False) -> None:
    """Model only the installer-owned paths below a private pytest fixture."""
    if create:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o755)


ROOT_FIXTURE_PARENTS = (Path('/opt'), Path('/usr/local'))


def root_owned_nonwritable_ancestry(path: Path) -> bool:
    if not path.is_absolute():
        return False
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except OSError:
            return False
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            return False
    return True


def root_fixture_parent() -> Path | None:
    for parent in ROOT_FIXTURE_PARENTS:
        if root_owned_nonwritable_ancestry(parent):
            return parent
    return None


def root_fixture_directory() -> Path:
    if platform.system() != 'Linux' or shutil.which('sudo') is None:
        pytest.skip('requires Linux sudo for the root/service installer contract')
    parent = root_fixture_parent()
    if parent is None:
        pytest.skip('requires a root-owned non-writable /opt or /usr/local fixture parent')
    created = subprocess.run(
        ['sudo', '-n', '/usr/bin/mktemp', '-d', '-p', str(parent), 'anvil-connect-install-test.XXXXXXXX'],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip('requires passwordless sudo and an isolated root fixture directory')
    root = Path(created.stdout.strip())
    if root.parent != parent or not root.name.startswith('anvil-connect-install-test.'):
        raise AssertionError('sudo fixture allocation returned an unsafe path')
    subprocess.run(['sudo', '-n', '/bin/chmod', '0755', str(root)], check=True)
    return root


def remove_root_fixture(root: Path) -> None:
    if root.parent not in ROOT_FIXTURE_PARENTS or not root_owned_nonwritable_ancestry(root.parent) or not root.name.startswith('anvil-connect-install-test.'):
        raise AssertionError('refusing to remove an unexpected root fixture')
    subprocess.run(['sudo', '-n', '/bin/rm', '-rf', '--', str(root)], check=True)


ROOT_FIXTURE_DIAGNOSTIC = '''import importlib.util
import sys

installer_path = sys.argv[1]
spec = importlib.util.spec_from_file_location("connect_fixture_install", installer_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original_install = module.install

def diagnostic_install(*args, **kwargs):
    try:
        return original_install(*args, **kwargs)
    except (OSError, ValueError, KeyError, TypeError) as error:
        message = str(error).replace("\\n", " ")[:256]
        print(f"root fixture installer error: {type(error).__name__}: {message}", file=sys.stderr)
        raise

module.install = diagnostic_install
sys.argv = [installer_path, *sys.argv[2:]]
module.main()
'''


def stage_root_fixture_diagnostic(destination: Path) -> None:
    # Stream fixed test bytes into the already root-owned fixture so sudo never
    # reads a mutable wrapper path before Python executes it.
    subprocess.run(
        ['sudo', '-n', '/usr/bin/tee', '--', str(destination)],
        input=ROOT_FIXTURE_DIAGNOSTIC,
        text=True,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    subprocess.run(['sudo', '-n', '/bin/chmod', '0700', '--', str(destination)], check=True)


def root_owned_path(path: Path, *, directory: bool) -> Path | None:
    """Resolve a test interpreter target before it is ever passed to sudo."""
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    for candidate in (resolved, *resolved.parents):
        try:
            info = candidate.lstat()
        except OSError:
            return None
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            return None
        if candidate == resolved:
            expected = stat.S_ISDIR if directory else stat.S_ISREG
            if not expected(info.st_mode):
                return None
        elif not stat.S_ISDIR(info.st_mode):
            return None
    return resolved


def root_safe_interpreter() -> str:
    """Return a root-safe isolated interpreter, or skip before any sudo call."""
    candidates = (Path(sys.executable), Path('/usr/bin/python3'))
    for candidate in candidates:
        interpreter = root_owned_path(candidate, directory=False)
        if interpreter is None:
            continue
        probe = subprocess.run(
            [str(interpreter), '-I', '-S', '-c', 'import sys, sysconfig; print(sys.base_prefix); print(sys.exec_prefix); print(sysconfig.get_path("stdlib"))'],
            capture_output=True,
            text=True,
            env={'PATH': '/usr/bin:/bin'},
        )
        locations = probe.stdout.splitlines() if probe.returncode == 0 else []
        if len(locations) == 3 and all(root_owned_path(Path(location), directory=True) is not None for location in locations):
            return str(interpreter)
    pytest.skip('requires a root-owned non-writable Python interpreter and standard library')


@requires_posix
def test_installer_rejects_symlink_ancestry(tmp_path: Path) -> None:
    installer = load_module('install')
    link = tmp_path / 'link'
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match='ancestry'):
        installer.checked_directory(link / 'prefix')


@requires_posix
def test_atomic_upgrade_rollback_and_interrupted_first_install(tmp_path: Path, monkeypatch) -> None:
    installer, root, digest = bundle(tmp_path)
    monkeypatch.setattr(installer, 'host_platform', lambda: 'darwin-arm64')
    monkeypatch.setattr(installer, 'checked_directory', private_test_directory)
    prefix = tmp_path / 'prefix'
    original_rename = installer.os.rename

    def crash_publish(source, destination):
        if destination == prefix:
            raise OSError('simulated power interruption before prefix publication')
        return original_rename(source, destination)

    monkeypatch.setattr(installer.os, 'rename', crash_publish)
    with pytest.raises(OSError):
        installer.install(root, prefix, role='client', expected=digest, apply=True)
    assert not prefix.exists()
    monkeypatch.setattr(installer.os, 'rename', original_rename)
    installer.install(root, prefix, role='client', expected=digest, apply=True)
    old_raw = (root / 'bundle.json').read_bytes()
    updated = json.loads(old_raw)
    updated['version'] = '0.1.1'
    new_raw = json.dumps(updated).encode()
    (root / 'bundle.json').write_bytes(new_raw)
    new_digest = hashlib.sha256(new_raw).hexdigest()
    installer.install(root, prefix, role='client', expected=new_digest, apply=True)
    assert json.loads((prefix / 'installation.json').read_text())['version'] == '0.1.1'
    (root / 'bundle.json').write_bytes(old_raw)
    installer.install(root, prefix, role='client', expected=digest, apply=True)
    assert json.loads((prefix / 'installation.json').read_text())['version'] == '0.1.0'
    assert len(list((prefix / 'releases').iterdir())) == 2
