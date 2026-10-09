"""Authorization type/expiry, private crash publication and typed frontier guards."""
import os
import time

import pytest

from anvil_serving.router import maintenance
from anvil_serving.router.admission import RouterAdmission
from tests.router.key_fixtures import tmp_path as tmp_path


def authorization():
    return {'schema':'router-maintenance-authorization/v1','operation_id':'a'*64,
        'phase':'legacy-stop','authorization_sha256':'b'*64,'expected_container_id':'c'*64,
        'expected_image_id':'sha256:'+'d'*64,'expected_configuration_revision':'e'*64,
        'preview_sha256':None,'expires_at':int(time.time())+120,'legacy_receipt_sha256':None,
        'acknowledge_uncertainty':'legacy-local-and-remote','ingress_barrier':'exact-container-stop'}


@pytest.mark.parametrize('change',['held_claim','unknown_field','wrong_phase','boolean_expiry','expired','long_expiry',
                                   'wrong_barrier','wrong_gap','partial_identity','no_legacy'])
def test_protected_authorization_refuses_ambiguous_or_unapproved_effect(change):
    value=authorization()
    if change=='held_claim':value['submitters_held']=True
    elif change=='unknown_field':value['force']=True
    elif change=='wrong_phase':value['phase']='offline'
    elif change=='boolean_expiry':value['expires_at']=True
    elif change=='expired':value['expires_at']=int(time.time())
    elif change=='long_expiry':value['expires_at']=int(time.time())+901
    elif change=='wrong_barrier':value['ingress_barrier']='none'
    elif change=='wrong_gap':value['acknowledge_uncertainty']='all-unknown'
    elif change=='partial_identity':value['expected_container_id']='partial'
    else:
        value.update(phase='successor-readmit',acknowledge_uncertainty='remote-memory-terminal-only',
                     ingress_barrier='closed-generation')
    with pytest.raises(ValueError):maintenance.authorization(value)


def test_typed_native_gap_retains_unknown_and_known_local_work_is_counted():
    owner=RouterAdmission('fixture',owner_scope=lambda:None)
    owner.observe('memory',lambda:(0,maintenance.REMOTE_MEMORY_TERMINAL_UNKNOWN))
    counts,unknown=owner._drain_counts()
    assert unknown==['memory'] and not any(counts.values()) and owner._remote_memory_gap
    permit=owner.acquire('memory')
    assert owner._drain_counts()[0]['memory']==1
    permit.release()
    owner._observers['memory']=lambda:(0,True)
    assert owner._drain_counts()[1]==['memory'] and not owner._remote_memory_gap
    owner._observers['memory']=lambda:(_ for _ in ()).throw(OSError('unavailable'))
    assert owner._drain_counts()[1]==['memory'] and not owner._remote_memory_gap
    owner._observers['memory']=lambda:(True,maintenance.REMOTE_MEMORY_TERMINAL_UNKNOWN)
    assert owner._drain_counts()[1]==['memory'] and not owner._remote_memory_gap


@pytest.mark.skipif(os.name!='posix',reason='native maintenance publication uses POSIX directory durability')
@pytest.mark.parametrize('phase',['file_fsync','directory_fsync'])
def test_interrupted_publication_remains_exclusive_and_never_consumes_twice(tmp_path,monkeypatch,phase):
    target=tmp_path/'ack.json'; actual=maintenance.os.fsync; calls=0
    def fsync(fd):
        nonlocal calls
        calls+=1
        if calls==(1 if phase=='file_fsync' else 2):raise OSError('synthetic durability failure')
        return actual(fd)
    value={'schema':'synthetic-ack','operation_id':'a'*64}
    monkeypatch.setattr(maintenance.os,'fsync',fsync)
    with pytest.raises(OSError):maintenance._publish(target,value)
    assert not list(tmp_path.glob('.maintenance-*'))
    if phase=='directory_fsync':
        assert maintenance._private_json(target)==value
        with pytest.raises(ValueError):maintenance._publish(target,value)
    else:assert not target.exists()


@pytest.mark.skipif(os.name!='posix',reason='accepted legacy mode uses native no-follow owned reader')
def test_legacy_nonsecret_mode_preserved_but_links_replacements_and_changed_cas_refuse(tmp_path):
    target=tmp_path/'router.toml';target.write_text('[server]\n');target.chmod(0o664)
    raw,identity=maintenance._legacy_config(target)
    assert raw==b'[server]\n' and target.stat().st_mode&0o777==0o664
    target.write_text('[server]\n# concurrent edit\n')
    assert maintenance._legacy_config(target)!=(raw,identity)
    link=tmp_path/'linked.toml';link.symlink_to(target)
    with pytest.raises(ValueError):maintenance._legacy_config(link)
    hard=tmp_path/'hard.toml';os.link(target,hard)
    with pytest.raises(ValueError):maintenance._legacy_config(target)
