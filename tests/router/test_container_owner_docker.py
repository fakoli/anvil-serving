"""Opt-in, owned Docker qualification. No ports/GPU/network or real data mounts.

These tests run the production procfs oracle and native stdin commit helpers.
They deliberately retain old stopped incarnations until their proofs transfer.
"""
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import uuid

import pytest

from anvil_serving import router_manage
from tests.router.key_fixtures import tmp_path as tmp_path

pytestmark = pytest.mark.skipif(
    os.environ.get('ANVIL_ROUTER_DOCKER_TESTS') != '1', reason='explicit owned Docker qualification required')

MAIN = '''
import json,os,time
from anvil_serving.router.config import load_server_config
from anvil_serving.router.keys import KeyStore
from anvil_serving.router.usage_store import UsageStore
from anvil_serving.router.serve import build_server
from fixture_helpers import StaticBackend
class FixtureBackend(StaticBackend):
    def generate(self,request):
        if request.raw.get('messages',[{}])[0].get('content')=='fixture-interrupt':
            while True:time.sleep(.1)
        u=UsageStore(KeyStore(s.api_keys_path));u.owner_config=s
        owner=server.anvil_router_admission;u.key_store._router_admission=owner
        scope=owner.usage_scope()
        u.coverage_transition(owner.usage_run_id,True,scope.configuration_revision,authority_scope=scope)
        yield from super().generate(request)
s=load_server_config('/etc/anvil/config.toml')
if not os.path.exists(s.api_keys_path):
    UsageStore(KeyStore.initialize(s.api_keys_path)).migrate()
if os.environ.get('FIXTURE_LEGACY_SCOPE')=='1':
    original_scope=UsageStore.managed_owner_scope
    def legacy_scope(self,*a,**k):
        from dataclasses import replace
        current=original_scope(self,*a,**k)
        with self.key_store._connect() as db:
            rows=db.execute('SELECT run_id,started_at FROM usage_runs WHERE domain_id=?',(current.domain_id,)).fetchall()
        return replace(current,run_ids=tuple(r[0] for r in rows),from_utc=min(r[1] for r in rows))
    UsageStore.managed_owner_scope=legacy_scope
fault=os.environ.get('FIXTURE_FAILURE')
if fault=='sqlite':
    UsageStore.recover=lambda *a,**k: (_ for _ in ()).throw(ValueError('synthetic SQLite failure'))
elif fault=='register':
    original=UsageStore.register_run
    def interrupted_register(*a,**k):
        original(*a,**k)
        raise ValueError('synthetic post-register failure')
    UsageStore.register_run=interrupted_register
elif fault=='closure':
    original=os.replace
    def interrupted_replace(src,dst):
        if str(dst).endswith('intent.json.router'):raise OSError('synthetic closure failure')
        return original(src,dst)
    os.replace=interrupted_replace
elif fault=='transfer':
    from anvil_serving.router import container_owner
    container_owner.finish_transfers=lambda *a,**k: (_ for _ in ()).throw(ValueError('synthetic CAS failure'))
server=build_server('/etc/anvil/config.toml',host='127.0.0.1',port=8000,
    backends={'primary-local':FixtureBackend([]),'omni-local':FixtureBackend([])},env=os.environ)
server.serve_forever()
'''


@pytest.fixture
def docker_owner(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    build = tmp_path / 'build'; build.mkdir()
    shutil.copytree(root / 'anvil_serving', build / 'anvil_serving', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copyfile(root / 'tests/router/helpers.py', build / 'fixture_helpers.py')
    tag = 'ruu-owner-test:' + uuid.uuid4().hex
    base=os.environ['ANVIL_ROUTER_DOCKER_BASE']
    expected=os.environ['ANVIL_ROUTER_DOCKER_BASE_ID']
    assert re.fullmatch(r'[A-Za-z0-9_./:-]+',base) and not base.startswith('-')
    assert re.fullmatch(r'sha256:[a-f0-9]{64}',expected)
    assert subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',base],text=True).strip()==expected
    (build / 'Dockerfile').write_text('FROM '+base+'\n'
        'COPY --chown=1000:1000 anvil_serving /fixture-source/anvil_serving\n'
        'COPY --chown=1000:1000 fixture_helpers.py /fixture-source/fixture_helpers.py\n'
        'ENV PYTHONPATH=/fixture-source\nUSER 1000:1000\n')
    subprocess.run(['docker', 'build', '--pull=false', '--network=none', '-t', tag, str(build)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=90)
    private = tmp_path / 'private'; private.mkdir(mode=0o700)
    main = tmp_path / 'main.py'; main.write_text(MAIN)
    config = tmp_path / 'router.toml'
    config.write_text((root / 'configs/example.toml').read_text() + '\n[server]\nauth_env="SYNTHETIC_AUTH"\n'
        'api_keys_path="/fixture/private/keys.sqlite3"\nadmission_state_path="/fixture/private/intent.json"\n'
        'router_owner_backend="managed-container"\nrouter_owner_id="fixture"\nrouter_owner_roster=["fixture"]\n'
        'usage_domain_id="fixture"\nusage_enabled=true\n')
    name = 'ruu-owner-' + uuid.uuid4().hex
    project = name
    monkeypatch.setattr(router_manage, 'DEFAULT_COMPOSE_PROJECT', project)
    monkeypatch.setattr(router_manage, 'DEFAULT_CONTAINER', name)
    compose = tmp_path / 'compose.json'
    compose.write_text(json.dumps({'name':project,'services':{'router':{'image':tag,'container_name':name,
        'user':'1000:1000','network_mode':'none','cpus':2,'mem_limit':'512m','pids_limit':64,
        'entrypoint':['python','/fixture/main.py'],'environment':{'SYNTHETIC_AUTH':'synthetic'},
        'volumes':[{'type':'bind','source':str(private),'target':'/fixture/private'},
                   {'type':'bind','source':str(main),'target':'/fixture/main.py','read_only':True},
                   {'type':'bind','source':str(config),'target':'/etc/anvil/config.toml','read_only':True}]}}}))
    env = {'PATH':os.environ.get('PATH','/usr/bin:/bin'), 'HOME':str(Path.home())}
    owned_ids, namespace_fds = [], []
    def run(argv, **kwargs):
        return subprocess.run(argv, **kwargs)
    def start(*, failed=False):
        subprocess.run(router_manage._compose_up_argv(str(compose), 'router'), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env, timeout=30)
        cid = subprocess.check_output(['docker','inspect','--format','{{.Id}}',name],text=True).strip()
        owned_ids.append(cid)
        # A read-only namespace handle prevents immediate inode reuse. It does
        # not enter/share the namespace or alter either container's PID mode.
        host_pid = subprocess.check_output(['docker','inspect','--format','{{.State.Pid}}',cid],text=True).strip()
        if int(host_pid)>0:
            try:
                namespace_fds.append(os.open('/proc/' + host_pid + '/ns/pid',os.O_RDONLY|os.O_CLOEXEC))
            except FileNotFoundError:
                if not failed:raise
        else:
            assert failed, 'unexpected stopped fixture before live qualification'
        ready = ('import json; from anvil_serving.router_manage import _transition_request; '
                 "print(json.dumps(_transition_request('status',scope='router',router_url='http://127.0.0.1:8000',env={'ANVIL_ROUTER_TOKEN':'synthetic'})))")
        for _ in range(100):
            result = subprocess.run(['docker','exec',cid,'python','-c',ready],capture_output=True,text=True,timeout=3)
            if result.returncode == 0:
                assert not failed, 'injected failure must never create a listener'
                return cid
            if failed and subprocess.check_output(['docker','inspect','--format','{{.State.Status}}',cid],text=True).strip()=='exited':
                return cid
            time.sleep(.1)
        logs = subprocess.check_output(['docker','logs',cid],text=True,stderr=subprocess.STDOUT)
        pytest.fail('owned fixture failed startup: ' + logs)
    def code(source):
        return json.loads(subprocess.check_output(['docker','exec',name,'python','-c',source],text=True,timeout=10))
    def custody(kind):
        return router_manage._managed_container_custody(kind,str(compose),'router',name,_run=run,execution_env=env)
    def transition(action, **kwargs):
        source = ('import json; from anvil_serving.router_manage import _transition_request; '
                  f"print(json.dumps(_transition_request({action!r},scope='router',router_url='http://127.0.0.1:8000',"
                  f"env={{'ANVIL_ROUTER_TOKEN':'synthetic'}},confirm=True,dry_run=False,**{kwargs!r})))")
        return code(source)['result']
    def rows(sql):
        with sqlite3.connect(private / 'keys.sqlite3') as db:
            return db.execute(sql).fetchall()
    try:
        yield {'start':start,'code':code,'custody':custody,'transition':transition,'rows':rows,
               'private':private,'config':config,'compose':compose,'name':name,'env':env,'run':run,'main':main}
    finally:
        # Only IDs returned by this fixture's own create operation are removed.
        for descriptor in namespace_fds:
            os.close(descriptor)
        for cid in dict.fromkeys(owned_ids):
            subprocess.run(['docker','rm','-f',cid],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
        subprocess.run(['docker','image','rm',tag],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)


def test_real_docker_consumed_restart_crash_and_second_successor(docker_owner):
    f = docker_owner
    f['start']()
    observe = ("import json; from dataclasses import asdict; from anvil_serving.router.usage_store import RunOwner,observe_run; "
               "o=RunOwner.observe('fixture',managed=True); print(json.dumps({'owner':asdict(o),'state':observe_run(o,managed=True)}))")
    first = f['code'](observe)
    assert first['state'] == 'live'
    strict = f['code']("import json; from anvil_serving.router.usage_store import RunOwner; "
                      "\ntry: RunOwner.observe('fixture'); result=False\nexcept ValueError: result=True\nprint(json.dumps(result))")
    assert strict is True
    status = f['transition']('status')
    assert status['state'] == 'quiesced' and status['durable']
    token = f['transition']('quiesce')['barrier_token']
    assert f['transition']('drain',barrier_token=token,timeout=1)['unknown'] == ['owner_roster_unknown']
    assert f['custody']('live')['finalized'] == 'live'
    assert f['transition']('readmit',barrier_token=token)['applied']
    chat = ("import json,urllib.request; r=urllib.request.Request('http://127.0.0.1:8000/v1/chat/completions',"
            "data=json.dumps({'model':'llm.primary','messages':[{'role':'user','content':'fixture'}]}).encode(),"
            "headers={'Authorization':'Bearer synthetic','Content-Type':'application/json'}); "
            "response=urllib.request.urlopen(r,timeout=5); response.read(); print(json.dumps({'status':response.status}))")
    assert f['code'](chat)['status'] == 200
    for _ in range(100):
        committed = f['rows']('SELECT * FROM usage_details')
        if committed:
            break
        time.sleep(.02)
    assert len(committed) == 1, {'starts': f['rows']('SELECT request_id FROM usage_starts'), 'segments': f['rows']('SELECT enabled FROM usage_coverage_segments')}
    previous_ns = first['owner']['pid_namespace_inode']
    for cycle in range(2):
        if cycle == 1:
            interrupted = chat.replace("'fixture'","'fixture-interrupt'")
            subprocess.run(['docker','exec','-d',f['name'],'python','-c',interrupted],check=True,timeout=5)
            for _ in range(100):
                if len(f['rows']('SELECT request_id FROM usage_starts'))==3:break
                time.sleep(.02)
            assert len(f['rows']('SELECT request_id FROM usage_starts'))==3
        token = f['transition']('quiesce')['barrier_token']
        if cycle == 0:
            receipt = router_manage.require_router_drain(f['name'],_run=f['run'],compose=str(f['compose']),service='router')
            assert receipt['owner_backend'] == 'managed-container'
            subprocess.run(['docker','stop',f['name']],check=True,stdout=subprocess.DEVNULL,timeout=15)
        else:
            subprocess.run(['docker','kill',f['name']],check=True,stdout=subprocess.DEVNULL,timeout=10)
        before = (f['private'] / 'intent.json.router').read_bytes()
        assert f['custody']('dead')['finalized'] == 'dead'
        assert (f['private'] / 'intent.json.router').read_bytes() == before
        # Different config registration must consume the same shared classifier.
        f['config'].write_text(f['config'].read_text() + f'\n# successor {cycle}\n')
        probe = ('import json; from anvil_serving.router_manage import _offline_router_start; '
                 'print(json.dumps(_offline_router_start()))')
        assert router_manage._offline_compose_run(str(f['compose']),'router',f['name'],probe,
            _run=f['run'],execution_env=f['env'])['offline']
        f['start']()
        current = f['code'](observe)
        print(json.dumps({'fixture_cycle':cycle,'previous_pid_namespace':previous_ns,'current_pid_namespace':current['owner']['pid_namespace_inode'],'live_state':current['state']}),flush=True)
        assert current['owner']['pid_namespace_inode'] != previous_ns
        previous_ns = current['owner']['pid_namespace_inode']
        assert f['transition']('status')['state'] == 'quiesced'
        assert f['custody']('live')['finalized'] == 'live'
        token = f['transition']('quiesce')['barrier_token']
        assert f['transition']('readmit',barrier_token=token)['applied']
        assert committed[0] in f['rows']('SELECT * FROM usage_details')
        if cycle==1:
            terminals=[json.loads(r[0]) for r in f['rows']('SELECT terminal_payload FROM usage_details')]
            assert len(terminals)==3 and any(t['outcome']=='interrupted' and 'recovered_partial' in t['coverage'] for t in terminals)
        assert len(f['rows']('SELECT run_id FROM usage_runs')) == cycle + 2
        assert len(f['rows']("SELECT run_id FROM usage_runs WHERE state='dead'")) == cycle + 1
        records = [json.loads(p.read_text()) for p in (f['private'] / 'keys.sqlite3.router-incarnations').glob('*.json')]
        assert sum(r['phase']=='transferred' and r['sequence']==2 for r in records) == cycle + 1
        scope_token=f['transition']('quiesce')['barrier_token']
        assert not f['transition']('drain',barrier_token=scope_token,timeout=1)['unknown']
        if cycle==0:
            assert f['transition']('readmit',barrier_token=scope_token)['applied']
            assert f['code'](chat)['status']==200
            for _ in range(100):
                later=f['rows']('SELECT * FROM usage_details')
                if len(later)==2:break
                time.sleep(.02)
            assert len(later)==2 and committed[0] in later
            committed=later


def test_real_native_after_check_failure_is_pending_and_retryable(docker_owner, monkeypatch):
    f=docker_owner; f['start']()
    actual=router_manage._offline_compose_roster
    def fail_after(*args, **kwargs):
        value=actual(*args,**kwargs)
        if list((f['private']/'keys.sqlite3.router-incarnations').glob('*.json')):
            raise ValueError('synthetic host after-check failure')
        return value
    with monkeypatch.context() as patch:
        patch.setattr(router_manage,'_offline_compose_roster',fail_after)
        with pytest.raises(ValueError,match='after-check failure'):f['custody']('live')
    [path]=list((f['private']/'keys.sqlite3.router-incarnations').glob('*.json'))
    assert json.loads(path.read_text())['phase']=='live-pending'
    token=f['transition']('quiesce')['barrier_token']
    assert f['transition']('drain',barrier_token=token,timeout=1)['unknown']==['owner_roster_unknown']
    assert f['custody']('live')['finalized']=='live'
    assert f['transition']('readmit',barrier_token=token)['applied']
    receipt=router_manage.require_router_drain(f['name'],_run=f['run'],compose=str(f['compose']),service='router')
    assert receipt['closed']
    subprocess.run(['docker','stop',f['name']],check=True,stdout=subprocess.DEVNULL,timeout=15)
    # Both actual storage fences must independently prevent proof minting.
    import fcntl
    for name in ('intent.json.router.lock','keys.sqlite3.router-writers.lock'):
        with (f['private']/name).open('r+') as held:
            fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with pytest.raises(ValueError):f['custody']('dead')
        assert json.loads(path.read_text())['phase']=='live'
    actual_identity=router_manage._container_incarnation
    def wrong_daemon(*args,**kwargs):
        return {**actual_identity(*args,**kwargs),'daemon_id':'different-daemon'}
    with monkeypatch.context() as patch:
        patch.setattr(router_manage,'_container_incarnation',wrong_daemon)
        with pytest.raises(ValueError):f['custody']('dead')
    assert json.loads(path.read_text())['phase']=='live'
    assert f['custody']('dead')['finalized']=='dead'


@pytest.mark.parametrize('phase',['sqlite','register','closure','transfer'])
def test_actual_docker_startup_partial_failure_remains_closed(docker_owner, phase):
    f=docker_owner;f['start']();f['custody']('live')
    token=f['transition']('quiesce')['barrier_token'];f['transition']('readmit',barrier_token=token)
    router_manage.require_router_drain(f['name'],_run=f['run'],compose=str(f['compose']),service='router')
    subprocess.run(['docker','stop',f['name']],check=True,stdout=subprocess.DEVNULL,timeout=15)
    f['custody']('dead')
    before=(f['private']/'intent.json.router').read_bytes()
    run_count=len(f['rows']('SELECT run_id FROM usage_runs'))
    compose=json.loads(f['compose'].read_text());compose['services']['router']['environment']['FIXTURE_FAILURE']=phase
    f['compose'].write_text(json.dumps(compose))
    f['start'](failed=True)
    if phase=='sqlite':
        assert len(f['rows']('SELECT run_id FROM usage_runs'))==run_count
    else:
        assert len(f['rows']('SELECT run_id FROM usage_runs'))==run_count+1
    if phase!='transfer':assert (f['private']/'intent.json.router').read_bytes()==before
    else:
        state=json.loads((f['private']/'intent.json.router').read_text())['closure']
        assert state['consumed'] is False and state['generation']==json.loads(before)['closure']['generation']+1
    assert all(json.loads(p.read_text())['phase']=='ready' for p in (f['private']/'keys.sqlite3.router-incarnations').glob('*.json'))


def test_actual_legacy_scope_refuses_http_after_config_change(docker_owner):
    f=docker_owner;f['start']();f['custody']('live')
    token=f['transition']('quiesce')['barrier_token'];f['transition']('readmit',barrier_token=token)
    router_manage.require_router_drain(f['name'],_run=f['run'],compose=str(f['compose']),service='router')
    subprocess.run(['docker','stop',f['name']],check=True,stdout=subprocess.DEVNULL,timeout=15)
    f['custody']('dead');f['config'].write_text(f['config'].read_text()+'\n# actual different source configuration\n')
    compose=json.loads(f['compose'].read_text());compose['services']['router']['environment']['FIXTURE_LEGACY_SCOPE']='1'
    f['compose'].write_text(json.dumps(compose));f['start']();f['custody']('live')
    token=f['transition']('quiesce')['barrier_token'];f['transition']('readmit',barrier_token=token)
    source="""
import json,urllib.request,urllib.error
r=urllib.request.Request('http://127.0.0.1:8000/v1/chat/completions',data=json.dumps({'model':'llm.primary','messages':[{'role':'user','content':'fixture'}]}).encode(),headers={'Authorization':'Bearer synthetic','Content-Type':'application/json'})
try:
 response=urllib.request.urlopen(r,timeout=5);result={'status':response.status}
except urllib.error.HTTPError as e:result={'status':e.code,'code':json.loads(e.read())['error']['type']}
print(json.dumps(result))
"""
    result=f['code'](source)
    print(json.dumps({'controlled_previous_scope_http':result}),flush=True)
    assert result=={'status':503,'code':'accounting_unavailable'}
    assert f['rows']('SELECT request_id FROM usage_starts')==[]
