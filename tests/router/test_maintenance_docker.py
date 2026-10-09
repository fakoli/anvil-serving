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


def test_supported_legacy_stop_import_successor_fenced_readmit_and_replay(docker_owner):
    import fcntl
    f=docker_owner
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
    preview=maintenance.run(config,_run=f['run'])
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

    # Physical crash/restart cannot inherit the one-time exception. Native
    # stopped-incarnation proof preserves the committed ledger; startup creates
    # a fresh CLOSED generation and the old authorization/preview cannot open it.
    generation=f['transition']('status')['generation']
    subprocess.run(['docker','kill',successor['container_id']],check=True,
                   stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=10)
    assert router_manage.cmd_up(str(f['compose']),service='router',container=f['name'],_run=diagnostic_run)==0
    f['start']()
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
