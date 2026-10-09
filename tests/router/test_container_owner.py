"""Deterministic protected record and commit failure tests; no live resources."""
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import uuid

import pytest

from anvil_serving import router_manage
from anvil_serving.router import container_owner as custody, usage_store as ledger
from anvil_serving.router.config import load_server_config
from anvil_serving.router.keys import KeyStore, KeyStoreError, _bind_router_store
from tests.router.test_owner_recovery import config as config

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='native custody uses POSIX fences')


@pytest.fixture
def record(config):
    settings = load_server_config(str(config)); store = KeyStore(settings.api_keys_path)
    usage = ledger.UsageStore(store)
    owner = ledger.RunOwner.observe(settings.router_owner_id)
    revision = 'a' * 64
    run = usage.register_run(owner, domain_id='fixture', configuration_revision=revision)
    state = Path(settings.admission_state_path + '.router')
    state.write_text(json.dumps({'schema':'router-admission/v1','owner_id':'fixture','closure':None})); state.chmod(0o600)
    _bind_router_store(store.path,state,'fixture')
    docker = {'daemon_id':'fixture-daemon','container_id':'b'*64,'image_id':'sha256:'+'c'*64,
              'started_at':'2026-01-01T00:00:00Z','restart_count':0,'compose_project':'fixture','compose_service':'router'}
    native = {k:v for k,v in asdict(owner).items() if k not in {'host_domain_id','pid','start_ticks'}}
    native['container_init_start_ticks'] = 1
    value = {'schema':'router-managed-incarnation/v1','phase':'live-pending','sequence':0,'death':None,'transfer':None,
             'anchor':{'owner_backend':'managed-container','owner_id':'fixture','domain_id':'fixture','run_id':run,
                       'run_owner':asdict(owner),'configuration_revision':revision,'roster_revision':custody.roster(owner),
                       'store_binding_sha256':custody.digest(custody.binding(store)),'docker':docker,'native_view':native}}
    return store, usage, value


@pytest.mark.parametrize('failure', ['file_fsync','replace','directory_fsync'])
def test_stage_failure_never_creates_authoritative_record(record, monkeypatch, failure):
    store, _, value = record
    actual = os.fsync
    calls = 0
    def fsync(fd):
        nonlocal calls
        calls += 1
        if (failure == 'file_fsync' and calls == 1) or (failure == 'directory_fsync' and calls == 2):
            raise OSError('synthetic failure')
        return actual(fd)
    monkeypatch.setattr(custody.os,'fsync',fsync)
    if failure == 'replace':
        monkeypatch.setattr(custody.os,'replace',lambda *a: (_ for _ in ()).throw(OSError('synthetic failure')))
    with pytest.raises(OSError):
        custody.write(store,value,None)
    path = custody.location(store,value['anchor']['run_id'])
    assert not path.exists() or json.loads(path.read_text())['phase']=='live-pending'
    assert not list(path.parent.glob('.pending-*'))


@pytest.mark.parametrize('change', ['unknown_key','boolean_sequence','bad_owner','wrong_native','phase_sequence','bad_id'])
def test_record_schema_rejects_ambiguous_identity(record, change):
    _, _, original = record; value = deepcopy(original)
    if change=='unknown_key':value['unsafe_override']=True
    elif change=='boolean_sequence':value['sequence']=False
    elif change=='bad_owner':value['anchor']['run_owner']['pid']=True
    elif change=='wrong_native':value['anchor']['native_view']['pid_namespace_inode']+=1
    elif change=='phase_sequence':value['phase']='ready'
    else:value['anchor']['docker']['container_id']='short'
    with pytest.raises(ValueError):custody.validate(value)


def test_pending_and_native_mode_cannot_create_managed_authority(record, monkeypatch):
    store, usage, value = record
    custody.write(store,value,None)
    with store._connect() as db:
        import sqlite3
        db.row_factory=sqlite3.Row; row=db.execute('SELECT * FROM usage_runs').fetchone()
        assert usage._owner_state(row,db=db,current=True)=='unknown'
        usage.owner_config=SimpleNamespace(router_owner_backend='managed-container',router_owner_id='fixture')
        monkeypatch.setattr(usage,'_physical_owner',lambda owner:'live')
        assert usage._owner_state(row,db=db,current=True)=='unknown'
        assert usage._owner_state(row,db=db)=='live'  # LIVE always vetoes receipts.
        monkeypatch.setattr(usage,'_physical_owner',lambda owner:'unknown')
        assert usage._owner_state(row,db=db)=='unknown'


def test_native_host_after_failure_does_not_commit_pending_or_leave_runner(tmp_path):
    pending='a'*64
    source="import sys,json; print(json.dumps({'pending_sha256':'"+pending+"','run_id':'"+str(uuid.uuid4())+"'}),flush=True); line=sys.stdin.buffer.readline(); open(sys.argv[1],'wb').write(line)"
    path=tmp_path/'commit.txt'; observed=0
    def snapshot():
        nonlocal observed
        observed+=1
        return observed
    with pytest.raises(ValueError,match='custody_changed'):
        router_manage._native_owner_commit([sys.executable,'-c',source,str(path)],snapshot)
    assert not path.exists() or path.read_bytes()==b''


def test_cas_refuses_changed_record_without_overwrite(record):
    store,_,value=record
    custody.write(store,value,None)
    before=custody.location(store,value['anchor']['run_id']).read_bytes()
    with pytest.raises(KeyStoreError):custody.write(store,{**value,'phase':'live'},'0'*64)
    assert custody.location(store,value['anchor']['run_id']).read_bytes()==before


def test_ordinary_live_writer_does_not_impersonate_producer(record):
    store,_,_=record
    producer=Path(custody.binding(store)['state_path']+'.lock'); producer.touch(mode=0o600)
    with pytest.raises(KeyStoreError,match='producer unavailable'):
        with custody.live_writer(store):pytest.fail('fictitious producer')


@pytest.mark.parametrize('change', ['valid','root','parent','device','filesystem','source','writable','duplicate',
    'hidepid','subset','unknown_option','optional_field','duplicate_base','boot_descendant','stat_descendant','ns_descendant','pid1_mismatch'])
def test_managed_procfs_exception_is_exact_and_default_stays_strict(monkeypatch, change):
    from tests.router.test_usage_lifecycle import _ACTUAL_LINUX_OWNER
    actual_read=Path.read_text
    base='1 0 0:1 / /proc rw - proc proc rw'
    child='2 1 0:1 /sys /proc/sys ro - proc proc rw'
    modifications={'root':('/sys /proc/sys','/else /proc/sys'),'parent':('2 1','2 99'),
        'device':('0:1','0:2'),'filesystem':('- proc proc','- tmpfs proc'),
        'source':('- proc proc','- proc fixture'),'writable':(' ro ',' rw '),
        'hidepid':(' ro ',' ro,hidepid=2 '),'subset':(' ro ',' ro,subset=pid ')}
    if change in modifications:child=child.replace(*modifications[change])
    if change=='unknown_option':child=child.replace(' ro ',' ro,unknown=1 ')
    if change=='optional_field':base=base.replace(' rw - ',' rw unsupported - ')
    extra=''
    if change=='duplicate_base':extra='\n4 0 0:1 / /proc rw - proc proc rw'
    if change=='duplicate':extra='\n3 1 0:1 /sys /proc/sys ro - proc proc rw'
    elif change.endswith('descendant'):
        target={'boot_descendant':'/proc/sys/kernel/random/boot_id','stat_descendant':'/proc/456/stat',
                'ns_descendant':'/proc/1/ns/pid'}[change]
        extra='\n3 1 0:2 / '+target+' rw - tmpfs tmpfs rw'
    def read(path, **kwargs):
        text=str(path)
        if text=='/proc/self/mountinfo':return base+'\n'+child+extra
        if text.endswith('boot_id'):return '00000000-0000-4000-8000-000000000001'
        if text.endswith('/stat'):return '456 (fixture) '+' '.join(['S']+['0']*18+['42'])
        return actual_read(path,**kwargs)
    def stat(path, *args, **kwargs):
        text=str(path)
        if text=='/proc':return SimpleNamespace(st_dev=1,st_ino=1)
        if text=='/proc/sys':return SimpleNamespace(st_dev=1,st_ino=2)
        inode=3 if text.endswith('/user') else 2
        if change=='pid1_mismatch' and text=='/proc/1/ns/pid':inode=99
        return SimpleNamespace(st_dev=1,st_ino=inode,st_uid=1000)
    with monkeypatch.context() as patch:
        patch.setattr(ledger.os,'getpid',lambda:456);patch.setattr(ledger.os,'geteuid',lambda:1000)
        patch.setattr(ledger.os,'readlink',lambda p:'456');patch.setattr(ledger.os,'stat',stat)
        patch.setattr(Path,'read_text',read)
        if change=='valid':
            owner,view=_ACTUAL_LINUX_OWNER('fixture',_managed=True)
            assert owner.pid==456 and view[0][:5]==(1,0,'0:1','/','/proc')
        else:
            with pytest.raises(ValueError):_ACTUAL_LINUX_OWNER('fixture',_managed=True)
        with pytest.raises(ValueError):_ACTUAL_LINUX_OWNER('fixture')


def test_comparable_live_vetoes_even_finalized_retained_death(record, monkeypatch):
    store,usage,value=record
    stopped={**value['anchor']['docker'],'finished_at':'2026-01-01T00:01:00Z',
             'status':'exited','running':False,'paused':False,'restarting':False,'pid':0}
    ready={**value,'phase':'ready','sequence':1,'death':{'anchor_sha256':custody.digest(value['anchor']),
        'closure_sha256':custody.digest(custody.closure(store)),'closure_generation':None,'closure_consumed':None,
        'stopped':stopped,'implementation_sha256':'d'*64}}
    custody.write(store,ready,None)
    usage.owner_config=SimpleNamespace(router_owner_backend='managed-container',router_owner_id='fixture')
    with store._connect() as db:
        import sqlite3
        db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM usage_runs').fetchone()
        monkeypatch.setattr(usage,'_physical_owner',lambda owner:'live')
        assert usage._owner_state(row,db=db)=='live'
        monkeypatch.setattr(usage,'_physical_owner',lambda owner:'unknown')
        assert usage._owner_state(row,db=db)=='dead'
        usage.owner_config=None
        assert usage._owner_state(row,db=db)=='unknown'


@pytest.mark.parametrize('change', ['same', 'stopped', 'closure', 'live', 'transferred'])
def test_repeated_finalized_death_preserves_proof_and_refuses_changed_authority(record, monkeypatch, change):
    store,usage,value=record
    usage.owner_config=SimpleNamespace(usage_domain_id='fixture')
    monkeypatch.setattr(ledger,'observe_run',lambda *a,**k:'unknown')
    stopped={**value['anchor']['docker'],'finished_at':'2026-01-01T00:01:00Z',
             'status':'exited','running':False,'paused':False,'restarting':False,'pid':0}
    custody.write(store,{**value,'phase':'live'},None)
    proposed=custody.stopped_record(usage,stopped)
    ready={**proposed,'phase':'ready','sequence':1}
    # A historical implementation hash is immutable across native upgrades.
    ready['death']['implementation_sha256']='d'*64
    path=custody.location(store,value['anchor']['run_id'])
    custody.write(store,ready,custody.digest({**value,'phase':'live'}))
    before=path.read_bytes()
    if change=='stopped':stopped={**stopped,'finished_at':'2026-01-01T00:02:00Z'}
    elif change=='closure':
        current=custody.closure(store)
        changed={**current,'closure':{'configuration_revision':value['anchor']['configuration_revision'],
            'roster_revision':value['anchor']['roster_revision'],'generation':1,'consumed':False,
            'barrier_token':'e'*64,'policy_revision':'f'*64}}
        monkeypatch.setattr(custody,'closure',lambda store:changed)
    elif change=='live':monkeypatch.setattr(ledger,'observe_run',lambda *a,**k:'live')
    elif change=='transferred':
        moved={**ready,'phase':'transferred','sequence':2,'transfer':{
            'successor_run_id':str(uuid.uuid4()),'successor_roster_revision':'e'*64,
            'successor_configuration_revision':'f'*64,'successor_generation':1}}
        custody.write(store,moved,custody.digest(ready));before=path.read_bytes()
    if change=='same':
        assert custody.stopped_record(usage,stopped)==ready
        assert custody.validate(custody.stopped_record(usage,stopped))['sequence']==1
    else:
        with pytest.raises(ValueError):custody.stopped_record(usage,stopped)
    assert path.read_bytes()==before


@pytest.mark.parametrize('backend',['unknown',False,1,[]])
def test_managed_backend_configuration_is_explicit(config, backend):
    text=config.read_text()+'\nrouter_owner_backend='+json.dumps(backend)+'\n'
    config.write_text(text)
    with pytest.raises(ValueError):load_server_config(str(config))


def test_managed_mode_without_owner_cannot_fall_through_unmanaged(config):
    from dataclasses import replace
    from anvil_serving.router.admission import managed_router_admission
    settings=replace(load_server_config(str(config)),router_owner_backend='managed-container',
                     router_owner_id=None,router_owner_roster=())
    with pytest.raises(ValueError,match='roster_unsupported'):managed_router_admission(settings,'a'*64)


def test_actual_native_writer_fence_only_lends_reader_custody_to_same_store(record):
    import fcntl
    store,_,_=record
    producer=Path(custody.binding(store)['state_path']+'.lock')
    descriptor=os.open(producer,os.O_CREAT|os.O_RDWR,0o600)
    try:
        fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with custody.live_writer(store):
            with store._connect() as db:
                assert db.execute('SELECT 1').fetchone()==(1,)
            with pytest.raises(KeyStoreError,match='custody is busy'):
                KeyStore(store.path)  # Another instance cannot inherit custody.
            gate=Path(str(store.path)+'.router-writers.lock')
            held=gate.with_suffix('.held');gate.rename(held)
            gate.write_bytes(b'');gate.chmod(0o600)
            try:
                with pytest.raises(KeyStoreError):
                    with store._connect():pytest.fail('replacement inherited held custody')
            finally:
                gate.unlink();held.rename(gate)
        assert not hasattr(store._writer_context,'native_reader_custody')
        with KeyStore(store.path)._connect() as db:assert db.execute('SELECT 1').fetchone()==(1,)
    finally:os.close(descriptor)
