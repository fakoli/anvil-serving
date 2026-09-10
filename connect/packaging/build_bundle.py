#!/usr/bin/env python3
"""Build a standalone Connect release from clean committed source and pinned tools."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def manager_archive(output: Path) -> None:
    # Reuse the product's actual Connect implementation, without installing or
    # bundling the router/controller/serving package and its CLI entrypoint.
    files = {'anvil_serving/__init__.py': b'', '__main__.py': b'from anvil_serving.connect.standalone import main\nmain()\n'}
    for source in sorted((ROOT / 'anvil_serving/connect').glob('*.py')):
        files[str(source.relative_to(ROOT))] = source.read_bytes()
    for name in ('anvil_serving/operator_output.py', 'anvil_serving/connect/components.lock.json'):
        files[name] = (ROOT / name).read_bytes()
    with output.open('wb') as stream:
        stream.write(b'#!/usr/bin/env python3\n')
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                entry = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, data)
    output.chmod(0o755)


def license_notices(environment: dict[str, str]) -> str:
    sections = []
    for source in sorted((ROOT / 'connect/packaging/licenses').glob('*.txt')):
        sections.append(source.stem + '\n\n' + source.read_text())
    # Only modules in the target executable's dependency graph are bundled.
    # `go list -m all` also includes unloaded test-only modules with no Dir.
    modules = subprocess.check_output(['go', 'list', '-deps', '-json', './cmd/anvil-connect'], cwd=ROOT / 'connect', env=environment, text=True)
    decoder = json.JSONDecoder()
    seen = set()
    while modules.strip():
        package, offset = decoder.raw_decode(modules.lstrip())
        modules = modules.lstrip()[offset:]
        module = package.get('Module')
        if not module or module.get('Main') or module['Path'] in seen:
            continue
        seen.add(module['Path'])
        directory = Path(module['Dir'])
        notices = [directory / name for name in ('LICENSE', 'LICENSE.txt', 'LICENSE.md', 'COPYING', 'NOTICE') if (directory / name).is_file()]
        if not notices:
            raise ValueError('Dependency license notice is missing: ' + module['Path'])
        for notice in notices:
            sections.append(module['Path'] + ' ' + module['Version'] + '\n\n' + notice.read_text())
    return '\n\n'.join(sections)


def build(platform_name: str, output: Path, components: dict[str, Path], cache: Path) -> dict:
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).strip():
        raise ValueError('Build requires a clean committed checkout')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    version = (ROOT / 'connect/VERSION').read_text().strip()
    target_os, target_arch = platform_name.split('-')
    roles = ['gateway', 'connector', 'client'] if target_os == 'linux' else ['client']
    output.mkdir(parents=True, exist_ok=False)
    bundle = output / ('anvil-connect-' + version + '-' + platform_name)
    (bundle / 'bin').mkdir(parents=True)
    (bundle / 'share').mkdir()
    environment = {**os.environ, 'GOOS': target_os, 'GOARCH': target_arch, 'CGO_ENABLED': '0', 'GOMAXPROCS': '4', 'GOFLAGS': '-p=4', 'GOMEMLIMIT': '2GiB', 'GOCACHE': str(cache / 'go-build'), 'GOMODCACHE': str(cache / 'go-mod')}
    subprocess.run(['go', 'build', '-trimpath', '-o', str(bundle / 'bin/anvil-connect'), './cmd/anvil-connect'], cwd=ROOT / 'connect', env=environment, check=True)
    lock = json.loads((ROOT / 'connect/lab/edge-tools.json').read_text())
    tunnel = json.loads((ROOT / 'connect/transport.lock.json').read_text())
    if target_os == 'linux':
        expected = {item['name']: item['binary_sha256'] for item in lock['components']}
        expected['wstunnel'] = tunnel['artifacts']['linux/amd64']['binary_sha256']
        for name, digest in expected.items():
            source = components[name]
            if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                raise ValueError('Pinned component checksum mismatch')
            shutil.copyfile(source, bundle / 'bin' / name)
        manager_archive(bundle / 'bin/anvil-connect-ctl')
    shutil.copyfile(ROOT / 'LICENSE', bundle / 'share/LICENSE')
    (bundle / 'share/THIRD-PARTY-LICENSES.txt').write_text(license_notices(environment))
    (bundle / 'share/THIRD-PARTY.json').write_text(json.dumps({'edge': lock, 'transport': tunnel, 'go_dependencies': (ROOT / 'connect/go.mod').read_text()}, sort_keys=True, indent=2) + '\n')
    files = {}
    for file in sorted(bundle.rglob('*')):
        if file.is_file():
            name = str(file.relative_to(bundle))
            mode = 0o755 if name.startswith('bin/') else 0o644
            file.chmod(mode)
            data = file.read_bytes()
            files[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'mode': mode}
    manifest = {'schema': 'anvil-connect.bundle/v1', 'version': version, 'source_revision': revision, 'platform': platform_name, 'roles': roles, 'files': files}
    raw = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    (bundle / 'bundle.json').write_bytes(raw)
    shutil.copyfile(ROOT / 'connect/packaging/install.py', bundle / 'install.py')
    archive_path = output / (bundle.name + '.tar.gz')
    epoch = int(subprocess.check_output(['git', 'show', '-s', '--format=%ct', 'HEAD'], cwd=ROOT, text=True))
    import gzip
    with archive_path.open('wb') as stream, gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=epoch) as compressed, tarfile.open(fileobj=compressed, mode='w') as archive:
        for file in sorted(bundle.rglob('*')):
            info = archive.gettarinfo(str(file), arcname=str(file.relative_to(output)))
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mtime = epoch
            if file.is_file():
                with file.open('rb') as source:
                    archive.addfile(info, source)
            else:
                archive.addfile(info)
    receipt = {'archive': archive_path.name, 'sha256': hashlib.sha256(archive_path.read_bytes()).hexdigest(), 'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'version': version, 'platform': platform_name, 'source_revision': revision}
    (output / 'receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', choices=['linux-amd64', 'darwin-arm64', 'darwin-amd64'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    for component in ('caddy', 'authelia', 'wstunnel'):
        parser.add_argument('--' + component, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.platform, args.output, {name: getattr(args, name) for name in ('caddy', 'authelia', 'wstunnel')}, args.cache), sort_keys=True))


if __name__ == '__main__':
    main()
