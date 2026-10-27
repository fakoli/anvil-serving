"""Actual isolated Docker legacy stop / successor one-shot maintenance.

Only the remote terminal gap is synthetic. Container incarnation, POSIX fences,
SQLite migration, admission, protected publication and supported commands are real.
"""
import hashlib
import json
import os
import subprocess
import time

import pytest

from anvil_serving import router_manage
from anvil_serving.router import maintenance
from tests.router.key_fixtures import tmp_path as tmp_path
from tests.router.test_container_owner_docker import docker_owner as docker_owner, MAIN

pytestmark = pytest.mark.skipif(os.environ.get('ANVIL_ROUTER_DOCKER_TESTS')!='1',
                               reason='explicit owned Docker qualification required')


def protected(path, value):
    path.write_text(json.dumps(value));path.chmod(0o600)
    return path


def auth(phase, incarnation, revision, **changes):
    return {'schema':'router-maintenance-authorization/v1','operation_id':'d'*64,
        'authorization_sha256':'e'*64,'phase':phase,'expected_container_id':incarnation['container_id'],
        'expected_image_id':incarnation['image_id'],'expected_configuration_revision':revision,
        'preview_sha256':None,'expires_at':int(time.time())+300,'legacy_receipt_sha256':None,
        'acknowledge_uncertainty':'legacy-local-and-remote' if phase=='legacy-stop' else 'remote-memory-terminal-only',
        'ingress_barrier':'exact-container-stop' if phase=='legacy-stop' else 'closed-generation', **changes}


def test_supported_legacy_stop_import_successor_fenced_readmit_and_replay(docker_owner, monkeypatch):
    import fcntl
    f=docker_owner
    monkeypatch.setenv('ANVIL_SERVING_HOME',str(f['private']/'operator-home'))
    # Legacy has no usage run and no complete all-path instrumentation. Its
    # actual process owns the same synthetic ledger and may still have work.
    f['main'].write_text("import time\nfrom anvil_serving.router.keys import KeyStore\n"
        "from anvil_serving.router.usage_store import UsageStore\n"
        "UsageStore(KeyStore.initialize('/var/lib/anvil-serving/router-keys/keys.sqlite3')).migrate()\n"
        "while True: time.sleep(.1)\n")
    f['config'].write_text(f['config'].read_text().replace('/fixture/private','/var/lib/anvil-serving/router-keys'))
    f['config'].chmod(0o664)  # Actual admitted non-secret legacy mode is preserved.
    composed=json.loads(f['compose'].read_text())
    composed['services']['router']['volumes'][0]['target']='/var/lib/anvil-serving/router-keys'
    composed['services']['router']['volumes'].append({'type':'bind','source':str(f['config']),
        'target':'/fixture/consumer/router.toml','read_only':True})
    # Optional exact-image compatibility lane: the router and every native
    # helper import the real predecessor package, not the copied test source.
    predecessor=os.environ.get('ANVIL_ROUTER_DOCKER_PREDECESSOR')
    successor_image=os.environ.get('ANVIL_ROUTER_DOCKER_SUCCESSOR')
    if predecessor:
        from pathlib import Path
        assert successor_image
        for reference, key in ((predecessor,'ANVIL_ROUTER_DOCKER_PREDECESSOR_ID'),
                               (successor_image,'ANVIL_ROUTER_DOCKER_SUCCESSOR_ID')):
            assert subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',reference],text=True).strip()==os.environ[key]
        composed['services']['router']['image']=predecessor
        composed['services']['router']['environment']['PYTHONPATH']='/fixture-only'
        composed['services']['router']['volumes'].append({'type':'bind',
            'source':str(Path(__file__).resolve().parent/'helpers.py'),
            'target':'/fixture-only/fixture_helpers.py','read_only':True})
    protected(f['compose'],composed)
    f['start'](legacy=True)
    for _ in range(100):
        if (f['private']/'keys.sqlite3').exists():break
        time.sleep(.02)
    incarnation=router_manage._container_incarnation(f['name'],_run=f['run'])
    namespace=f['code']("import json,os; s=os.stat('/proc/self/ns/pid');print(json.dumps([s.st_dev,s.st_ino]))")
    # Metadata-only legacy STOP cannot inspect implicit project dotenv or
    # resolve declared service env files, even when they are unreadable/absent.
    poison=f['compose'].parent/'.env';poison.mkdir()
    composed['services']['router']['env_file']=['/synthetic-env-must-not-be-read/absent.env']
    protected(f['compose'],composed)
    revision=hashlib.sha256(f['config'].read_bytes()).hexdigest()
    root=f['private']
    authorization=protected(root/'authorization.json',auth('legacy-stop',incarnation,revision))
    receipt=root/'legacy-receipt.json'
    config=protected(root/'maintenance.json',{'schema':'router-maintenance-config/v1',
        'compose':str(f['compose']),'compose_sha256':hashlib.sha256(f['compose'].read_bytes()).hexdigest(),
        'authorization':str(authorization),'receipt_out':str(receipt)})
    observed=maintenance.run(config,_run=f['run'])
    assert observed['drained'] is False and observed['remote_frontier']=='UNKNOWN'
    assert router_manage._container_incarnation(f['name'],_run=f['run'])==incarnation
    protected(authorization,auth('legacy-stop',incarnation,revision,preview_sha256=maintenance.digest(observed)))
    result=maintenance.run(config,confirm=True,_run=f['run'])
    assert result['stopped'] is True and result['drained'] is False
    assert f['config'].stat().st_mode&0o777==0o664
    raw=receipt.read_bytes()
    with pytest.raises(ValueError):maintenance.run(config,confirm=True,_run=f['run'])
    assert receipt.read_bytes()==raw
    # Offline migration consumes the independently verified stopped identity,
    # never fabricates a legacy run, closure, ZERO or completed remote memory.
    f['config'].chmod(0o600)
    poison.rmdir()
    del composed['services']['router']['env_file']
    protected(f['compose'],composed)
    values=json.loads(config.read_text());values['compose_sha256']=hashlib.sha256(f['compose'].read_bytes()).hexdigest()
    protected(config,values)
    def diagnostic_run(argv, **kwargs):
        result=subprocess.run(argv,**kwargs)
        if result.returncode and any('_migrate_offline(' in item for item in argv):
            pytest.fail('owned synthetic native helper: '+(result.stderr or '')[:4096])
        return result
    imported=router_manage.migrate_router_offline(str(f['compose']),
        '/var/lib/anvil-serving/router-keys/latest.sqlite3',maintenance_receipt=str(receipt),_run=diagnostic_run)
    assert imported['legacy_receipt_sha256']==result['receipt_sha256']
    assert f['rows']('SELECT count(*) FROM usage_runs')==[(0,)]
    # Runtime creates a new generation CLOSED. Fixture native memory observer
    # distinguishes unsupported remote terminal readback from all local work.
    controlled=MAIN.replace('server.serve_forever()', '''
from anvil_serving.router.maintenance import REMOTE_MEMORY_TERMINAL_UNKNOWN
from pathlib import Path
owner=server.anvil_router_admission
owner._assembling=True
control=Path('/var/lib/anvil-serving/router-keys/control.json')
def remote_gap():
    data=json.loads(control.read_text()) if control.exists() else {}
    if data.get('exception'):raise OSError('synthetic unreachable metadata')
    return data.get('local',0),REMOTE_MEMORY_TERMINAL_UNKNOWN
owner.observe('memory',remote_gap)
owner._assembling=False
server.serve_forever()
''')
    f['main'].write_text(controlled)
    # Supported cmd_up observes the exact stopped predecessor, performs the
    # native offline probe and starts closed; no manual lower-level admission.
    assert router_manage.cmd_up(str(f['compose']),service='router',container=f['name'],_run=diagnostic_run)==0
    f['start']()
    successor=router_manage._container_incarnation(f['name'],_run=f['run'])
    assert successor!=incarnation
    assert f['code']("import json,os; s=os.stat('/proc/self/ns/pid');print(json.dumps([s.st_dev,s.st_ino]))")!=namespace
    assert f['transition']('status')['state']=='quiesced'
    assert f['custody']('live')['finalized']=='live'
    token=f['transition']('quiesce')['barrier_token']
    assert f['transition']('drain',barrier_token=token,timeout=1)['unknown']==['memory']
    with pytest.raises(subprocess.CalledProcessError):f['transition']('readmit',barrier_token=token)
    protected(authorization,auth('successor-readmit',successor,revision,operation_id='f'*64,
                                legacy_receipt_sha256=result['receipt_sha256']))
    # Readmission observes the mounted owner without resolving Compose secrets.
    poison.mkdir()
    composed['services']['router']['env_file']=['/synthetic-env-must-not-be-read/absent.env']
    composed['services']['router']['environment']['SYNTHETIC_REQUIRED']='${SYNTHETIC_UNSET:?must-not-resolve}'
    protected(f['compose'],composed)
    values['compose_sha256']=hashlib.sha256(f['compose'].read_bytes()).hexdigest()
    protected(config,values)
    try:
        preview=maintenance.run(config,_run=f['run'])
    finally:
        poison.rmdir()
        del composed['services']['router']['env_file']
        del composed['services']['router']['environment']['SYNTHETIC_REQUIRED']
        protected(f['compose'],composed)
        values['compose_sha256']=hashlib.sha256(f['compose'].read_bytes()).hexdigest()
        protected(config,values)
    assert not any(preview['counts'].values()) and preview['eligible_uncertainty']=='remote-memory-terminal-unknown'
    # The external real writer gate remains authoritative during preview.
    gate=os.open(root/'keys.sqlite3.router-writers.lock',os.O_RDWR)
    try:
        fcntl.flock(gate,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError):maintenance.run(config,_run=f['run'])
    finally:os.close(gate)
    # Callback failure and a known local operation both retain the same closed
    # gate and refuse the exception; neither is a typed terminal-only gap.
    protected(root/'control.json',{'exception':True})
    with pytest.raises(ValueError):maintenance.run(config,_run=f['run'])
    protected(root/'control.json',{'local':1})
    with pytest.raises(ValueError):maintenance.run(config,_run=f['run'])
    protected(root/'control.json',{})
    assert maintenance.run(config,_run=f['run'])==preview
    changed={**preview,'nonce':'0'*64}
    protected(authorization,auth('successor-readmit',successor,revision,operation_id='f'*64,
        legacy_receipt_sha256=result['receipt_sha256'],preview_sha256=maintenance.digest(changed)))
    with pytest.raises(ValueError):maintenance.run(config,confirm=True,_run=f['run'])
    assert f['transition']('status')['state']=='quiesced'
    protected(authorization,auth('successor-readmit',successor,revision,operation_id='f'*64,
        legacy_receipt_sha256=result['receipt_sha256'],preview_sha256=maintenance.digest(preview)))
    applied=maintenance.run(config,confirm=True,_run=f['run'])
    assert applied['readmitted'] and not applied['drained'] and applied['unknown']==['memory']
    status=f['transition']('status')
    assert status['state']=='admitting' and status['maintenance']['remote_memory_terminal']=='UNKNOWN'
    chat=("import json,urllib.request; r=urllib.request.Request('http://127.0.0.1:8000/v1/chat/completions',"
        "data=json.dumps({'model':'llm.primary','messages':[{'role':'user','content':'fixture'}]}).encode(),"
        "headers={'Authorization':'Bearer synthetic','Content-Type':'application/json'}); "
        "response=urllib.request.urlopen(r,timeout=5); response.read(); print(json.dumps(response.status))")
    assert f['code'](chat)==200
    for _ in range(100):
        if f['rows']('SELECT count(*) FROM usage_details')==[(1,)]:break
        time.sleep(.02)
    assert f['rows']('SELECT count(*) FROM usage_details')==[(1,)]
    token=f['transition']('quiesce')['barrier_token']
    with pytest.raises(subprocess.CalledProcessError):f['transition']('readmit',barrier_token=token)
    with pytest.raises(ValueError):maintenance.run(config,confirm=True,_run=f['run'])
    assert f['transition']('status')['state']=='quiesced'

    # A current instrumented owner has a distinct approved stop vocabulary.
    # The native helper holds the real writer gate until this exact incarnation
    # stops; ordinary drain still refuses the remote terminal gap.
    candidate=dict(composed,services={'router':dict(composed['services']['router'])})
    candidate_config=root/'successor-router.toml'
    candidate_config.write_text(f['config'].read_text()+'\n# authorized successor config revision\n')
    candidate_config.chmod(0o600)
    candidate['services']['router']['volumes']=[dict(m, source=str(candidate_config))
        if m.get('source')==str(f['config']) else dict(m)
        for m in composed['services']['router']['volumes']]
    candidate_revision=hashlib.sha256(candidate_config.read_bytes()).hexdigest()
    if successor_image:candidate['services']['router']['image']=successor_image
    candidate_compose=protected(root/'candidate-compose.json',candidate)
    candidate_id=(os.environ['ANVIL_ROUTER_DOCKER_SUCCESSOR_ID'] if successor_image else successor['image_id'])
    if predecessor:
        assert successor['image_id']==os.environ['ANVIL_ROUTER_DOCKER_PREDECESSOR_ID']
        assert f['code']("import json,anvil_serving;print(json.dumps('/fixture-source/' not in anvil_serving.__file__))") is True
    current_auth=auth('current-instrumented-stop',successor,revision,
        schema='router-current-stop-authorization/v1',operation_id='8'*64,
        ingress_barrier='closed-generation-exact-container-stop',
        legacy_receipt_sha256=result['receipt_sha256'],
        successor={'image_id':candidate_id,'configuration_revision':candidate_revision,
                   'compose':str(candidate_compose),
                   'compose_sha256':hashlib.sha256(candidate_compose.read_bytes()).hexdigest()})
    protected(authorization,current_auth)
    values['receipt_out']=str(root/'current-stop.json');protected(config,values)
    current_preview=maintenance.run(config,_run=f['run'])
    assert current_preview['local_frontier']=='instrumented-quiesced'
    assert current_preview['drained'] is False
    assert router_manage._container_incarnation(f['name'],_run=f['run'])==successor
    current_auth['preview_sha256']=maintenance.digest(current_preview)
    protected(authorization,current_auth)
    current_result=maintenance.run(config,confirm=True,_run=f['run'])
    assert current_result['stopped'] and not current_result['drained']
    assert current_result['unknown']==['memory'] and current_result['death_sha256']
    assert f['rows']('SELECT count(*) FROM usage_details')==[(1,)]
    with pytest.raises(ValueError):maintenance.run(config,confirm=True,_run=f['run'])
    # Generic startup cannot swap the stopped owner's config mount. The exact
    # completed receipt binds that change without publishing new death custody.
    assert router_manage.cmd_up(str(candidate_compose),service='router',container=f['name'],_run=diagnostic_run)==1
    before_state=(root/'intent.json.router').read_bytes()
    before_runs=f['rows']('SELECT * FROM usage_runs')
    assert router_manage.cmd_up(str(candidate_compose),service='router',container=f['name'],
        current_stop_receipt=values['receipt_out'],dry_run=True,_run=diagnostic_run)==0
    assert (root/'intent.json.router').read_bytes()==before_state
    assert f['rows']('SELECT * FROM usage_runs')==before_runs
    original_custody=router_manage._managed_container_custody
    original_run_argv=router_manage._run_argv
    custody_calls=[]; compose_activations=[]
    def fail_first_live_custody(kind,*args,**kwargs):
        custody_calls.append(kind)
        if kind=='live' and custody_calls.count('live')==1:
            raise ValueError('synthetic interrupted native custody')
        return original_custody(kind,*args,**kwargs)
    def count_compose_activation(argv, runner, **kwargs):
        if 'up' in argv: compose_activations.append(tuple(argv))
        return original_run_argv(argv,runner,**kwargs)
    monkeypatch.setattr(router_manage,'_managed_container_custody',fail_first_live_custody)
    monkeypatch.setattr(router_manage,'_run_argv',count_compose_activation)
    # The first command starts the exact authorized successor but loses native
    # custody before any helper writes. Subsequent calls resume the same
    # running container and must never activate Compose again.
    assert router_manage.cmd_up(str(candidate_compose),service='router',container=f['name'],
        current_stop_receipt=values['receipt_out'],_run=diagnostic_run)==1
    for _ in range(100):
        try:
            status=f['transition']('status')
            if status['state']=='quiesced' and status['configuration_revision']==candidate_revision:break
        except (subprocess.CalledProcessError, KeyError):
            pass
        time.sleep(.05)
    else: pytest.fail('interrupted successor did not finish its closed startup')
    first_running=router_manage._container_incarnation(f['name'],_run=f['run'])
    first_namespace=f['code']("import json,os; s=os.stat('/proc/self/ns/pid');print(json.dumps([s.st_dev,s.st_ino]))")
    live_rows=f['rows']("SELECT run_id,state FROM usage_runs WHERE state!='dead'")
    assert len(live_rows)==1 and live_rows[0][1]=='live'
    successor_state=(root/'intent.json.router').read_bytes()
    assert len(compose_activations)==1 and custody_calls==['live']
    assert router_manage.cmd_up(str(candidate_compose),service='router',container=f['name'],
        current_stop_receipt=values['receipt_out'],dry_run=True,_run=diagnostic_run)==0
    assert len(compose_activations)==1 and custody_calls==['live']
    assert router_manage.cmd_up(str(candidate_compose),service='router',container=f['name'],
        current_stop_receipt=values['receipt_out'],_run=diagnostic_run)==0
    successor=router_manage._container_incarnation(f['name'],_run=f['run'])
    namespace_after=f['code']("import json,os; s=os.stat('/proc/self/ns/pid');print(json.dumps([s.st_dev,s.st_ino]))")
    assert successor==first_running and namespace_after==first_namespace
    assert f['rows']("SELECT run_id,state FROM usage_runs WHERE state!='dead'")==live_rows
    assert f['rows']('SELECT count(*) FROM usage_details')==[(1,)]
    assert (root/'intent.json.router').read_bytes()==successor_state
    assert len(compose_activations)==1 and custody_calls==['live','live']
    sidecar=root/'keys.sqlite3.router-incarnations'/(live_rows[0][0]+'.json')
    live_anchor= json.loads(sidecar.read_text())
    assert live_anchor['phase']=='live'
    live_anchor_bytes=sidecar.read_bytes()
    # A replay after exact live finalization is a read-only idempotent success.
    assert router_manage.cmd_up(str(candidate_compose),service='router',container=f['name'],
        current_stop_receipt=values['receipt_out'],_run=diagnostic_run)==0
    assert sidecar.read_bytes()==live_anchor_bytes
    assert len(compose_activations)==1 and custody_calls==['live','live']
    monkeypatch.setattr(router_manage,'_managed_container_custody',original_custody)
    monkeypatch.setattr(router_manage,'_run_argv',original_run_argv)
    status=f['transition']('status')
    assert status['state']=='quiesced' and status['configuration_revision']==candidate_revision
    assert status['generation']>current_preview['native']['observation']['closure']['generation']
    assert f['rows']('SELECT count(*) FROM usage_details')==[(1,)]
    with pytest.raises(ValueError):maintenance.run(config,confirm=True,_run=f['run'])
    successor=router_manage._container_incarnation(f['name'],_run=f['run'])
    if successor_image:assert successor['image_id']==candidate_id

    # Physical crash/restart cannot inherit the one-time exception. Native
    # stopped-incarnation proof preserves the committed ledger; startup creates
    # a fresh CLOSED generation and the old authorization/preview cannot open it.
    generation=f['transition']('status')['generation']
    subprocess.run(['docker','kill',successor['container_id']],check=True,
                   stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=10)
    assert router_manage.cmd_up(str(candidate_compose),service='router',container=f['name'],_run=diagnostic_run)==0
    f['start'](compose_path=candidate_compose)
    restored=f['transition']('status')
    assert restored['state']=='quiesced' and restored['generation']>generation
    assert restored['maintenance']['remote_memory_terminal']=='UNKNOWN'
    assert f['rows']('SELECT count(*) FROM usage_details')==[(1,)]
    with pytest.raises(ValueError):maintenance.run(config,confirm=True,_run=f['run'])
    health=f['code']("import json;from anvil_serving.router.keys import KeyStore;"
        "from anvil_serving.router.usage_store import UsageStore;"
        "print(json.dumps(UsageStore(KeyStore('/var/lib/anvil-serving/router-keys/keys.sqlite3')).health('fixture')))")
    assert health['coverage_complete'] is False
    assert health['maintenance']['remote_memory_terminal']=='UNKNOWN'
