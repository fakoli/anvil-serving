"""Public manifests and private credentials have deliberately different custody."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving.native_agents.auth import AdmissionDenied, protected_read
from anvil_serving.native_agents.sandbox import _read_rootfs_manifest


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    item=tmp_path.resolve()/"rootfs.manifest.json"
    item.write_bytes(b'{"public":"runtime closure"}')
    item.chmod(0o644)
    # Unit tests simulate root ownership only, never bypass the actual reader.
    # Live Linux acceptance additionally reads the installed artifact as UID62031.
    monkeypatch.setattr('anvil_serving.native_agents.sandbox._root_custody',lambda p:Path(p))
    def root_info(info):
        return SimpleNamespace(**{k:(0 if k=='st_uid' else getattr(info,k))
                                  for k in dir(info) if k.startswith('st_')})
    fstat=os.fstat;lstat=Path.lstat
    monkeypatch.setattr(os,'fstat',lambda fd:root_info(fstat(fd)))
    monkeypatch.setattr(Path,'lstat',lambda p:root_info(lstat(p)))
    return item


@pytest.mark.parametrize('mode',[0o444,0o644])
def test_public_manifest_readable_without_weakening_credential_reader(manifest,monkeypatch,mode):
    manifest.chmod(mode)
    assert _read_rootfs_manifest(manifest)==manifest.read_bytes()
    # Custody above the temporary directory is irrelevant to this separate mode
    # assertion, so supply safe directory metadata to the credential reader.
    original=Path.lstat
    def info(p):
        v=original(p)
        if p!=manifest:v.st_mode=(v.st_mode&~0o777)|0o755
        return v
    monkeypatch.setattr(Path,'lstat',info)
    with pytest.raises(AdmissionDenied):protected_read(manifest,4096)


@pytest.mark.parametrize('kind',['uid','symlink','hardlink','fifo','directory','empty','oversize','parent'])
def test_manifest_custody_and_bounds_fail_closed(manifest,monkeypatch,kind):
    if kind=='uid':
        original=os.fstat
        def info(fd):
            v=original(fd);v.st_uid=62031;return v
        monkeypatch.setattr(os,'fstat',info)
    elif kind=='symlink':
        target=manifest.with_name('target');manifest.rename(target);manifest.symlink_to(target)
    elif kind=='hardlink':os.link(manifest,manifest.with_name('alias'))
    elif kind=='fifo':manifest.unlink();os.mkfifo(manifest,0o644)
    elif kind=='directory':manifest.unlink();manifest.mkdir(mode=0o755)
    elif kind=='empty':manifest.write_bytes(b'')
    elif kind=='oversize':manifest.write_bytes(b'x'*33)
    elif kind=='parent':
        def unsafe(_):raise ValueError('unsafe parent')
        monkeypatch.setattr('anvil_serving.native_agents.sandbox._root_custody',unsafe)
    with pytest.raises(ValueError):_read_rootfs_manifest(manifest,32)
