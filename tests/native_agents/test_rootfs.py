import hashlib
import io
import json
import tarfile
import pytest
from anvil_serving.native_agents.rootfs import Builder,safe_relative


@pytest.mark.parametrize('name',['/etc/passwd','../secret','dir/../secret','dir//file','./file','.','a\\b','a\x00b'])
def test_unsafe_archive_names_denied(name):
    with pytest.raises(ValueError):safe_relative(name)


def test_copy_bounds_and_manifest(tmp_path,monkeypatch):
    builder=Builder(tmp_path.resolve()/'root');builder.text('app/file','hello')
    result=builder.finish();raw=(builder.root/'rootfs.manifest.json').read_bytes()
    assert result['rootfs_sha256']==hashlib.sha256(raw).hexdigest()
    assert json.loads(raw)['files']=={'app/file':hashlib.sha256(b'hello').hexdigest()}
    assert result['bytes']==5 and result['files']==1
    with pytest.raises(ValueError):builder.text('app/file','duplicate')
    monkeypatch.setattr('anvil_serving.native_agents.rootfs.MAX_BYTES',6)
    with pytest.raises(ValueError):builder.text('other','too large')
    with pytest.raises(ValueError):builder.add('short',io.BytesIO(b''),1)


@pytest.mark.parametrize('kind',['symlink','hardlink','fifo','setuid','private','duplicate','traversal'])
def test_pi_archive_rejects_unsafe_inputs(tmp_path,kind):
    archive=tmp_path/'pi.tar.gz'
    with tarfile.open(archive,'w:gz') as target:
        member=tarfile.TarInfo('../outside' if kind=='traversal' else '.env.local' if kind=='private' else 'file')
        if kind=='symlink':member.type=tarfile.SYMTYPE;member.linkname='/etc/passwd'
        if kind=='hardlink':member.type=tarfile.LNKTYPE;member.linkname='other'
        if kind=='fifo':member.type=tarfile.FIFOTYPE
        if kind=='setuid':member.mode=0o4755
        target.addfile(member)
        if kind=='duplicate':target.addfile(member)
    builder=Builder(tmp_path.resolve()/'root')
    with pytest.raises(ValueError):builder.archive(archive)
    assert not (tmp_path/'outside').exists()


def test_output_requires_new_canonical_directory(tmp_path):
    output=tmp_path.resolve()/'root';output.mkdir()
    with pytest.raises(FileExistsError):Builder(output)
    link=tmp_path/'alias';link.symlink_to(tmp_path.resolve(),target_is_directory=True)
    with pytest.raises(ValueError):Builder(link/'new')
