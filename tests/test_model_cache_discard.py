import json
import os

import pytest

from anvil_serving import model_cache_discard as discard
from anvil_serving import model_cache_volume as volumes
from tests.test_model_cache_volume import Docker, VOLUME, _cache, _home as basic_home

fcntl = pytest.importorskip("fcntl", reason="whole-cache discard requires POSIX locks")

def _home(tmp_path):
    return basic_home(tmp_path, 'schema_version = 1\nid = "test"\n')


def flat(tmp_path):
    root = tmp_path / 'retired'; root.mkdir()
    (root / 'config.json').write_text('{"model_type":"example"}')
    (root / '.source-revision').write_text('a' * 40)
    (root / 'model.safetensors').write_bytes(b'model')
    return root


@pytest.fixture(autouse=True)
def no_host_fds(monkeypatch, tmp_path):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / "operator"))
    monkeypatch.setattr(volumes, '_native_fd_refs', lambda root: [])


def args(tmp_path, root):
    return dict(cache_dir=root, config_home=_home(tmp_path), runner=Docker(root))


def apply(selection, **changes):
    preview = discard.inspect(**selection)
    return discard.discard(**selection, expected_fingerprint=preview['fingerprint'],
                           confirm=True, writers_quiesced=True, inventory_reviewed=True, **changes)


def test_native_retirement_preserves_outside(tmp_path):
    root = flat(tmp_path); selection = args(tmp_path, root)
    outside = tmp_path / 'keep'; outside.write_text('protected')
    result = apply(selection)
    assert result['outcome'] == 'removed' and not root.exists()
    assert outside.read_text() == 'protected'


def test_partial_shared_volume_and_timeout(tmp_path):
    root = _cache(tmp_path)
    (root / 'hub' / 'models--other--model' / 'snapshots' / ('b' * 40)).mkdir(parents=True)
    docker = Docker(root, timeout=True)
    selection = dict(volume=VOLUME, config_home=_home(tmp_path), runner=docker)
    result = apply(selection)
    assert result['outcome'] == 'unknown' and root.exists()
    assert sum(c[1:3] == ['volume', 'rm'] for c in docker.calls) == 1
    docker.timeout = False
    assert apply(selection)['outcome'] == 'removed'
    assert not root.parent.exists()


@pytest.mark.parametrize('hazard', ['symlink', 'hardlink', 'fifo', 'control', 'mount', 'empty-metadata', 'secret'])
def test_invalid_tree_never_removed(tmp_path, monkeypatch, hazard):
    root = flat(tmp_path); outside = tmp_path / 'keep'; outside.write_text('protected')
    if hazard == 'symlink': (root / 'escape').symlink_to(outside)
    if hazard == 'hardlink': os.link(outside, root / 'linked')
    if hazard == 'fifo': os.mkfifo(root / 'pipe')
    if hazard == 'control': (root / 'bad\nname').touch()
    if hazard == 'mount': monkeypatch.setattr(os.path, 'ismount', lambda p: p == root / 'config.json')
    if hazard == 'secret': (root / '.env').write_text('never read')
    if hazard == 'empty-metadata':
        metadata = root / '.cache/huggingface/download/x.metadata'
        metadata.parent.mkdir(parents=True); metadata.touch()
    with pytest.raises(discard.Error): discard.inventory(root)
    assert outside.read_text() == 'protected' and root.exists()


def test_parent_and_broad_roots_refused(tmp_path):
    flat(tmp_path)
    with pytest.raises(discard.Error): discard.inventory(tmp_path)
    with pytest.raises(discard.Error): discard.inventory('/')


@pytest.mark.parametrize('consumer', ['config', 'container', 'fd', 'lock', 'missing'])
def test_reference_and_lock_blockers(tmp_path, monkeypatch, consumer):
    root = flat(tmp_path); selection = args(tmp_path, root)
    if consumer == 'config': (selection['config_home'] / 'model.toml').write_text('model_path = ' + json.dumps(str(root)))
    if consumer == 'container':
        selection['runner'] = Docker(root, containers='c' * 64, mounts=[{'Type':'bind', 'Source':str(root)}])
    if consumer == 'fd': monkeypatch.setattr(volumes, '_native_fd_refs', lambda root: [{'pid':'123'}])
    if consumer == 'missing':
        (selection['config_home'] / 'voice.toml').write_text('[voice.stt]\nmanifest_path="missing.toml"')
        with pytest.raises(discard.Error, match='missing'): discard.inspect(**selection)
    elif consumer == 'lock':
        metadata = root / '.cache/huggingface/download/model.safetensors.lock'
        metadata.parent.mkdir(parents=True)
        with metadata.open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(OSError): discard.inspect(**selection)
    else:
        assert not discard.inspect(**selection)['removable']
    assert root.exists()


@pytest.mark.parametrize('drift', ['config', 'cache', 'consumer'])
def test_second_audit_drift_refuses(tmp_path, monkeypatch, drift):
    root = flat(tmp_path); selection = args(tmp_path, root)
    fingerprint = discard.inspect(**selection)['fingerprint']
    real = discard.inspect; count = 0
    def changed(**kwargs):
        nonlocal count
        count += 1
        if count == 2:
            if drift == 'config': (selection['config_home'] / 'new.toml').write_text('name="unrelated"')
            if drift == 'cache': (root / 'README.md').touch()
            if drift == 'consumer': monkeypatch.setattr(volumes, '_native_fd_refs', lambda r: [{'pid':'1'}])
        return real(**kwargs)
    monkeypatch.setattr(discard, 'inspect', changed)
    with pytest.raises(discard.Error, match='changed'):
        discard.discard(**selection, expected_fingerprint=fingerprint, confirm=True,
                        writers_quiesced=True, inventory_reviewed=True)
    assert root.exists()


def test_partial_native_failure_reported(tmp_path, monkeypatch):
    root = flat(tmp_path); selection = args(tmp_path, root)
    def fail(fd, records):
        os.unlink('model.safetensors', dir_fd=fd)
        raise OSError('disk error')
    monkeypatch.setattr(discard, '_unlink_tree', fail)
    result = apply(selection)
    assert result['outcome'] == 'partial' and result['removal_attempted']
    assert not (root / 'model.safetensors').exists()


def test_preview_and_attestations(tmp_path):
    root = flat(tmp_path); selection = args(tmp_path, root)
    assert discard.discard(**selection)['outcome'] == 'preview'
    with pytest.raises(discard.Error, match='requires'): discard.discard(**selection, confirm=True)
    with pytest.raises(discard.Error, match='fingerprint'):
        discard.discard(**selection, confirm=True, writers_quiesced=True, inventory_reviewed=True)
    assert root.exists()


def test_native_shared_hardlink_only_unlinks_selected_name(tmp_path):
    root = flat(tmp_path); outside = tmp_path / 'retained-weight'
    os.link(root / 'model.safetensors', outside)
    before = outside.stat()
    selection = args(tmp_path, root)
    preview = discard.inspect(**selection)
    assert preview['tree']['shared_files_preserved_elsewhere'] == ['model.safetensors']
    assert apply(selection)['outcome'] == 'removed'
    assert outside.read_bytes() == b'model'
    assert outside.stat().st_ino == before.st_ino and outside.stat().st_nlink == 1


def test_internal_native_hardlinks_refused(tmp_path):
    root = flat(tmp_path)
    os.link(root / 'model.safetensors', root / 'other.safetensors')
    with pytest.raises(discard.Error, match='internal hardlinks'):
        discard.inventory(root, native_unlink=True)


def test_reduced_home_and_unknown_flat_assets_refused(tmp_path, monkeypatch):
    root = flat(tmp_path); selection = args(tmp_path, root)
    real = tmp_path / 'real'; real.mkdir()
    monkeypatch.setenv('ANVIL_SERVING_HOME', str(real))
    with pytest.raises(discard.Error, match='selected'): discard.inspect(**selection)
    monkeypatch.setenv('ANVIL_SERVING_HOME', str(selection['config_home']))
    (root / 'rollback').mkdir(); (root / 'rollback/personal.txt').write_text('keep')
    with pytest.raises(discard.Error, match='receipt'): discard.inspect(**selection)


@pytest.mark.parametrize('field', ['up', 'command', 'argv', 'environment'])
def test_embedded_command_reference_refused(tmp_path, field):
    root = flat(tmp_path); selection = args(tmp_path, root)
    (selection['config_home'] / 'launch.toml').write_text(field + ' = ' + json.dumps('launcher --model-path=' + str(root) + ' --start'))
    assert not discard.inspect(**selection)['removable']


def test_embedded_named_volume_reference_refused(tmp_path):
    root = _cache(tmp_path); home = _home(tmp_path)
    (home / 'launch.toml').write_text('up = "docker run -v ' + VOLUME + ':/root/.cache model"')
    assert not discard.inspect(volume=VOLUME, config_home=home, runner=Docker(root))['removable']


def test_metadata_read_deadline_refuses(tmp_path, monkeypatch):
    root = flat(tmp_path); clock = [0.0]
    monkeypatch.setattr(discard.time, 'monotonic', lambda: clock[0])
    read = volumes._read_metadata
    def slow(*args, **kwargs):
        value = read(*args, **kwargs); clock[0] += volumes._MAX_SECONDS + 1
        return value
    monkeypatch.setattr(volumes, '_read_metadata', slow)
    with pytest.raises(discard.Error, match='timed out'): discard.inventory(root)
    assert root.exists()


@pytest.mark.parametrize('failure', ['remove-error', 'postcheck-error', 'space-error'])
def test_dispatched_removal_never_reports_retryable_refusal(tmp_path, monkeypatch, failure):
    root = _cache(tmp_path)
    class FaultyDocker(Docker):
        def __call__(self, argv, **kwargs):
            if failure == 'remove-error' and argv[1:3] == ['volume', 'rm']:
                return __import__('subprocess').CompletedProcess(argv, 1, '', 'daemon disconnected')
            if failure == 'postcheck-error' and self.removed:
                raise OSError('daemon unavailable')
            return super().__call__(argv, **kwargs)
    selection = dict(volume=VOLUME, config_home=_home(tmp_path), runner=FaultyDocker(root))
    statvfs = os.statvfs; count = 0
    def measured(path):
        nonlocal count
        count += 1
        if failure == 'space-error' and count == 2: raise OSError('measurement unavailable')
        return statvfs(path)
    monkeypatch.setattr(os, 'statvfs', measured)
    result = apply(selection)
    if failure == 'space-error':
        assert result['applied'] and result['outcome'] == 'removed'
        assert result['actual_free_space_delta_bytes'] is None
    else:
        assert result['outcome'] == 'unknown' and result['removal_attempted']


@pytest.mark.parametrize('escaped', [False, True])
def test_same_filesystem_bind_mount_refused_before_inventory(tmp_path, monkeypatch, escaped):
    root = flat(tmp_path)
    target = root / ('mounted data' if escaped else 'mounted')
    target.mkdir()
    encoded = str(target).replace(' ', r'\040')
    # Actual Linux mountinfo field order, with the same filesystem device.
    mountinfo = f'42 21 8:1 /protected {encoded} rw,relatime - ext4 /dev/example rw\n'
    real = volumes._read_metadata
    def read(path, *args, **kwargs):
        if str(path) == '/proc/self/mountinfo': return mountinfo
        return real(path, *args, **kwargs)
    monkeypatch.setattr(volumes, '_read_metadata', read)
    monkeypatch.setattr(os.path, 'ismount', lambda _path: False)
    with pytest.raises(discard.Error, match='mount boundary'): discard.inventory(root)
    assert target.exists()


@pytest.mark.parametrize('name', ['special_tokens_map.json', 'added_tokens.json', 'tokenizer.model', 'vocab.txt'])
def test_standard_flat_tokenizer_assets_supported(tmp_path, name):
    root = flat(tmp_path); (root / name).write_bytes(b'synthetic tokenizer metadata')
    assert name in discard.inventory(root)['entries']


@pytest.mark.parametrize('kind', ['directory', 'symlink'])
def test_flat_weight_signature_requires_regular_file(tmp_path, kind):
    root = flat(tmp_path); weight = root / 'model.safetensors'; weight.unlink()
    if kind == 'symlink': weight.symlink_to('config.json')
    else:
        weight.mkdir(); (weight / 'payload.json').write_text('{}')
        receipt = root / '.cache/huggingface/download/model.safetensors/payload.json.metadata'
        receipt.parent.mkdir(parents=True); receipt.write_text('a' * 40 + '\n')
    with pytest.raises(discard.Error, match='flat model signature'): discard.inventory(root)
    assert weight.exists()
