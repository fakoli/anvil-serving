from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).parents[2]
requires_posix = pytest.mark.skipif(os.name != 'posix', reason='Native POSIX installer filesystem contract; Windows bundles are unsupported')


def load_module(name: str):
    spec = importlib.util.spec_from_file_location('connect_' + name, ROOT / 'connect/packaging' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bundle(tmp_path: Path):
    installer = load_module('install')
    root = tmp_path / 'bundle'
    root.mkdir()
    files = {}
    for name in ('bin/anvil-connect', 'share/LICENSE', 'share/THIRD-PARTY.json', 'share/THIRD-PARTY-LICENSES.txt'):
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'fixture bytes')
        path.chmod(0o644)
        files[name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size': path.stat().st_size, 'mode': 0o755 if name.startswith('bin/') else 0o644}
    value = {'schema': installer.SCHEMA, 'version': '0.1.0', 'source_revision': 'a' * 40, 'platform': 'darwin-arm64', 'roles': ['client'], 'files': files}
    raw = json.dumps(value).encode()
    (root / 'bundle.json').write_bytes(raw)
    return installer, root, hashlib.sha256(raw).hexdigest()


def test_standalone_manager_contains_no_router_or_third_party_runtime(tmp_path: Path) -> None:
    builder = load_module('build_bundle')
    output = tmp_path / 'anvil-connect-ctl'
    builder.manager_archive(output)
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert not any('/router/' in name or '/controller/' in name or '/observability/' in name for name in names)
        assert 'anvil_serving/connect/components.lock.json' in names
    completed = subprocess.run([sys.executable, '-I', str(output), '--help'], cwd=tmp_path, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert 'anvil-connect-ctl' in completed.stdout
    invalid = subprocess.run([sys.executable, '-I', str(output), 'validate', '--manifest', '/missing.json'], cwd=tmp_path, capture_output=True, text=True)
    assert invalid.returncode != 0
    assert json.loads(invalid.stdout)['ok'] is False
    assert 'Traceback' not in invalid.stderr


@requires_posix
def test_installer_preview_repeat_and_drift_preserve_existing_files(tmp_path: Path, monkeypatch) -> None:
    installer, root, digest = bundle(tmp_path)
    monkeypatch.setattr(installer, 'host_platform', lambda: 'darwin-arm64')
    # The harness lives below pytest's temporary tree. Validate prefix safety
    # separately; the install behavior uses a private synthetic ancestry here.
    def directory(path, create=False):
        if create:
            path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(installer, 'checked_directory', directory)
    prefix = tmp_path / 'installed'
    result = installer.install(root, prefix, role='client', expected=digest, apply=False)
    assert result['state'] == 'planned'
    assert not prefix.exists()
    result = installer.install(root, prefix, role='client', expected=digest, apply=True)
    assert result['services_started'] is False
    assert (prefix / 'bin/anvil-connect').read_bytes() == b'fixture bytes'
    assert not (prefix / 'bin/authelia').exists()
    assert installer.install(root, prefix, role='client', expected=digest, apply=True)['state'] == 'current'
    command = prefix / 'bin/anvil-connect'
    command.unlink()
    command.write_text('unrelated command')
    with pytest.raises(ValueError, match='drifted'):
        installer.install(root, prefix, role='client', expected=digest, apply=True)
    assert command.read_text() == 'unrelated command'


@requires_posix
def test_installer_rejects_corruption_roles_and_foreign_prefix(tmp_path: Path, monkeypatch) -> None:
    installer, root, digest = bundle(tmp_path)
    monkeypatch.setattr(installer, 'host_platform', lambda: 'darwin-arm64')
    monkeypatch.setattr(installer, 'checked_directory', lambda *a, **k: None)
    for role in ('gateway', 'connector'):
        with pytest.raises(ValueError, match='role'):
            installer.install(root, tmp_path / 'prefix', role=role, expected=digest, apply=True)
    with pytest.raises(ValueError, match='digest'):
        installer.load_bundle(root, '0' * 64)
    prefix = tmp_path / 'foreign'
    prefix.mkdir()
    (prefix / 'unrelated').write_text('keep')
    with pytest.raises(ValueError, match='unowned'):
        installer.install(root, prefix, role='client', expected=digest, apply=True)
    (root / 'bin/anvil-connect').write_bytes(b'corrupt bytes')
    with pytest.raises(ValueError, match='checksum'):
        installer.load_bundle(root, digest)
    assert (prefix / 'unrelated').read_text() == 'keep'


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
    monkeypatch.setattr(installer, 'checked_directory', lambda path, create=False: path.mkdir(parents=True, exist_ok=True) if create else None)
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


@requires_posix
def test_installed_receipt_and_selected_file_closure_cannot_drift(tmp_path: Path, monkeypatch) -> None:
    installer, root, digest = bundle(tmp_path)
    monkeypatch.setattr(installer, 'host_platform', lambda: 'darwin-arm64')
    monkeypatch.setattr(installer, 'checked_directory', lambda path, create=False: path.mkdir(parents=True, exist_ok=True) if create else None)
    with pytest.raises(ValueError, match='members are missing'):
        installer.load_bundle(root, digest, installed_names={'bin/anvil-connect', 'bin/caddy'})
    prefix = tmp_path / 'prefix'
    installer.install(root, prefix, role='client', expected=digest, apply=True)
    receipt = prefix / 'installation.json'
    data = json.loads(receipt.read_text())
    data['source_revision'] = 'f' * 40
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='receipt drifted'):
        installer.install(root, prefix, role='client', expected=digest, apply=True)


@pytest.mark.parametrize('target', ['windows-amd64', 'linux-arm64'])
def test_unsupported_platform_is_rejected_before_posix_file_access(tmp_path: Path, monkeypatch, target: str) -> None:
    installer = load_module('install')
    monkeypatch.setattr(installer, 'host_platform', lambda: target)
    def forbidden_read(*args, **kwargs):
        pytest.fail('Unsupported platform reached native filesystem access')
    monkeypatch.setattr(installer, 'load_bundle', forbidden_read)
    prefix = tmp_path / 'untouched'
    with pytest.raises(ValueError, match='platform is not supported'):
        installer.install(tmp_path / 'missing-bundle', prefix, role='client', expected='0' * 64, apply=True)
    assert not prefix.exists()
