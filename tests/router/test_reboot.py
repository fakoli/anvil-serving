"""Previous-boot custody and fenced maintenance, using only owned synthetic state."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import time
import sys
from types import SimpleNamespace
import uuid

import pytest

from anvil_serving.router import container_owner as custody, maintenance, reboot, usage_store as ledger
from anvil_serving.router.config import load_server_config
from tests.router.test_container_owner import record as record
from tests.router.test_container_owner_docker import docker_owner as docker_owner
from tests.router.test_owner_recovery import config as config

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='native recovery requires Linux custody')


@pytest.fixture
def retained(record, config, monkeypatch):
    store, usage, value = record
    value = deepcopy(value)
    current = ledger.RunOwner.observe('fixture')
    old = replace(current, boot_id=str(uuid.uuid4()))
    config.write_text(config.read_text() + '\nrouter_owner_backend="managed-container"\n')
    settings = load_server_config(str(config)); revision = hashlib.sha256(config.read_bytes()).hexdigest()
    usage.owner_config = settings
    value.update(phase='live')
    value['anchor'].update(configuration_revision=revision, run_owner=asdict(old), roster_revision=custody.roster(old))
    value['anchor']['native_view']['boot_id'] = old.boot_id
    with store._connect() as db:
        db.execute('UPDATE usage_runs SET boot_id=?', (old.boot_id,))
        db.execute('UPDATE usage_domains SET configuration_revision=?', (revision,))
        db.execute('UPDATE usage_coverage_segments SET configuration_revision=?', (revision,))
    custody.write(store, value, None)
    monkeypatch.setattr(ledger.RunOwner, 'observe', classmethod(lambda cls, *a, **kw: current))
    monkeypatch.setattr(ledger, '_linux_managed_owner', lambda *a, **kw: (current, ()))
    monkeypatch.setattr(ledger, 'observe_run', lambda owner, **kw: 'live' if owner == current else 'unknown')
    monkeypatch.setattr(maintenance, 'legacy_frontier', lambda store: 'f'*64)
    from tests.router.test_maintenance import authorization
    auth = {**authorization(), 'schema': 'router-reboot-authorization/v1', 'phase': 'reboot-stop',
            'acknowledge_uncertainty': 'remote-memory-terminal-only',
            'ingress_barrier': 'previous-boot-exact-container-stop',
            'expected_container_id': value['anchor']['docker']['container_id'],
            'expected_image_id': value['anchor']['docker']['image_id'],
            'successor': {'image_id': 'sha256:'+'f'*64, 'configuration_revision': revision,
                          'compose': '/synthetic/router.json', 'compose_sha256': 'a'*64}, 'boot_id': current.boot_id, 'expected_configuration_revision': revision,
            'sidecar_snapshot_sha256': custody.digest({value['anchor']['run_id'] + '.json': custody.digest(value)}),
            'legacy_receipt_sha256': 'f'*64}
    incumbent = {k: v for k, v in value['anchor']['docker'].items() if k not in {'started_at', 'restart_count'}}
    stopped = {**value['anchor']['docker'], 'started_at': '2026-01-02T00:00:00Z', 'restart_count': 10,
               'finished_at': '2026-01-02T00:01:00Z', 'status': 'exited', 'running': False,
               'paused': False, 'restarting': False, 'pid': 0}
    return SimpleNamespace(store=store, usage=usage, value=value, settings=settings, auth=auth,
                           incumbent=incumbent, stopped=stopped, current=current, old=old, revision=revision, config=config)


def acknowledgment(f, observed):
    preview = {'native': deepcopy(observed), 'incumbent': f.incumbent, 'successor': f.auth['successor']}
    f.auth['preview_sha256'] = custody.digest(preview)
    return {'schema': 'router-reboot-pending/v1', 'authorization': deepcopy(f.auth),
            'preview': preview, 'acknowledged_at': int(time.time())}


@pytest.mark.parametrize('change', ['none', 'no_fence', 'same_boot', 'live_veto', 'snapshot', 'unanchored',
                                  'different_image', 'different_daemon', 'partial', 'coverage', 'legacy'])
def test_fenced_candidate_refuses_unknown_ownership_before_mutation(retained, monkeypatch, change):
    f = retained
    if change == 'same_boot':
        monkeypatch.setattr(ledger.RunOwner, 'observe', classmethod(lambda cls, *a, **kw: f.old))
    elif change == 'live_veto': monkeypatch.setattr(ledger, 'observe_run', lambda *a, **k: 'live')
    elif change == 'snapshot': f.auth['sidecar_snapshot_sha256'] = '0'*64
    elif change == 'different_image': f.incumbent['image_id'] = 'sha256:' + '0'*64
    elif change == 'different_daemon': f.incumbent['daemon_id'] = 'another-daemon'
    elif change == 'legacy': monkeypatch.setattr(maintenance, 'legacy_frontier', lambda store: None)
    elif change == 'partial':
        custody.write(f.store, {**f.value, 'phase': 'live-pending'}, custody.digest(f.value))
    elif change == 'coverage':
        with f.store._connect() as db: db.execute('DELETE FROM usage_coverage_segments')
    elif change == 'unanchored':
        with f.store._connect() as db:
            db.execute('INSERT INTO usage_runs SELECT ?,domain_id,started_at,ended_at,state,host_domain_id,boot_id,'
                'pid_namespace_device,pid_namespace_inode,procfs_pid_namespace_device,procfs_pid_namespace_inode,'
                'uid,user_namespace_device,user_namespace_inode,pid,start_ticks FROM usage_runs', (str(uuid.uuid4()),))
    before = f.store.path.read_bytes()
    path = custody.location(f.store, f.value['anchor']['run_id']); original = path.read_bytes()
    if change == 'no_fence':
        with pytest.raises(ValueError): reboot.fenced_candidate(f.usage, f.auth, f.incumbent)
    else:
        with f.store._offline_custody(f.settings, allow_retained=True):
            if change == 'none':
                observed, record, state = reboot.fenced_candidate(f.usage, f.auth, f.incumbent)
                assert observed['prior_record_sha256'] == custody.digest(f.value)
                assert record == f.value and state['closure'] is None
            else:
                with pytest.raises(ValueError): reboot.fenced_candidate(f.usage, f.auth, f.incumbent)
    assert f.store.path.read_bytes() == before and path.read_bytes() == original


@pytest.mark.parametrize('change', ['none', 'unknown', 'same_config_required', 'boot', 'snapshot', 'wrong_gap', 'confirm_without_history'])
def test_reboot_authorization_is_narrow(retained, change):
    from tests.router.test_maintenance import authorization
    f = retained
    auth = {**authorization(), **f.auth, 'schema': 'router-reboot-authorization/v1', 'phase': 'reboot-stop',
        'acknowledge_uncertainty': 'remote-memory-terminal-only', 'ingress_barrier': 'previous-boot-exact-container-stop',
        'successor': {'image_id': 'sha256:'+'f'*64, 'configuration_revision': f.revision,
                      'compose': '/synthetic/router.json', 'compose_sha256': 'a'*64}}
    if change == 'unknown': auth['force'] = True
    elif change == 'same_config_required': auth['successor']['configuration_revision'] = '0'*64
    elif change == 'boot': auth['boot_id'] = 'not-a-boot'
    elif change == 'snapshot': auth['sidecar_snapshot_sha256'] = False
    elif change == 'wrong_gap': auth['acknowledge_uncertainty'] = 'local-and-remote'
    elif change == 'confirm_without_history': auth.update(preview_sha256='0'*64, legacy_receipt_sha256=None)
    if change == 'none': assert maintenance.authorization(auth) == auth
    else:
        with pytest.raises(ValueError): maintenance.authorization(auth)


@pytest.mark.parametrize('break_at', ['none', 'release', 'after_stage', 'bad_commit', 'expired_after_ack'])
def test_native_protocol_holds_real_fences_and_never_finalizes_without_commit(retained, monkeypatch, break_at):
    import io
    import fcntl
    from anvil_serving import router_manage
    f = retained
    with f.store._offline_custody(f.settings, allow_retained=True):
        observed, _, _ = reboot.fenced_candidate(f.usage, f.auth, f.incumbent)
    pending = acknowledgment(f, observed)
    output = io.StringIO(); reads = 0
    def read(size):
        nonlocal reads
        reads += 1
        if reads == 1:
            value = {'authorization': f.auth, 'incumbent': f.incumbent, 'intent': None}
        else:
            # Independent descriptors cannot acquire either held kernel lock.
            for path in [str(f.store.path)+'.router-writers.lock', f.settings.admission_state_path+'.router.lock']:
                with open(path, 'rb') as lock:
                    with pytest.raises(BlockingIOError): fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if reads == 2:
                if break_at == 'release': value = {'release': True}
                else:
                    if break_at == 'expired_after_ack': monkeypatch.setattr(maintenance.time, 'time', lambda: f.auth['expires_at']+100)
                    value = {'stopped': f.stopped, 'fenced_sha256': custody.digest(observed), 'intent': pending}
            else:
                if break_at == 'after_stage': raise EOFError('interrupted peer')
                staged = json.loads(output.getvalue().splitlines()[-1])['pending_sha256']
                value = {'commit': '0'*64 if break_at == 'bad_commit' else staged}
        return json.dumps(value).encode()+b'\n'
    monkeypatch.setattr(router_manage, 'DEFAULT_INSTALLED_CONFIG', str(f.config))
    monkeypatch.setattr(reboot.signal, 'alarm', lambda *a: None)
    monkeypatch.setattr(reboot.select, 'select', lambda *a: ([True], [], []))
    monkeypatch.setattr(reboot.sys, 'stdin', SimpleNamespace(buffer=SimpleNamespace(readline=read)))
    monkeypatch.setattr(reboot.sys, 'stdout', output)
    if break_at in {'after_stage', 'bad_commit'}:
        with pytest.raises((EOFError, ValueError)): reboot.native_session()
    else: reboot.native_session()
    saved = custody.validate(json.loads(custody.location(f.store, f.value['anchor']['run_id']).read_text()))
    assert saved['phase'] == ('live' if break_at == 'release' else
                             'dead-pending' if break_at in {'after_stage', 'bad_commit'} else 'ready')
    if saved['death']:
        assert saved['death']['reboot']['intent_sha256'] == custody.digest(pending)
        assert saved['death']['reboot']['acknowledged_at'] == pending['acknowledged_at']
    with f.store._offline_custody(f.settings, allow_retained=True): pass


@pytest.fixture
def reboot_host(retained, monkeypatch):
    from contextlib import contextmanager
    from anvil_serving import router_manage, router_recovery_status
    f = retained; folder = f.config.parent / 'maintenance'; folder.mkdir(mode=0o700)
    f.stopped_now = False; f.stops = 0; f.stage = None; f.fenced = False
    def protected(path, value):
        path.write_text(json.dumps(value)); path.chmod(0o600); return path
    f.protected = protected
    monkeypatch.setattr(router_manage, 'DEFAULT_COMPOSE_PROJECT', 'fixture')
    mounted = {'type': 'bind', 'source': str(f.config), 'destination': router_manage.DEFAULT_INSTALLED_CONFIG, 'read_only': True}
    volume = {'type': 'volume', 'source': 'synthetic-store', 'destination': str(f.store.path.parent), 'read_only': False}
    compose = {'name': 'fixture', 'services': {'router': {'container_name': router_manage.DEFAULT_CONTAINER,
        'image': 'fixture:successor', 'volumes': [{**mounted, 'target': mounted['destination']}]}}}
    old = protected(folder/'old.json', compose); new = protected(folder/'new.json', compose)
    f.auth['successor'].update(compose=str(new), compose_sha256=hashlib.sha256(new.read_bytes()).hexdigest())
    auth_file = protected(folder/'authorization.json', f.auth)
    f.values = {'schema': 'router-maintenance-config/v1', 'compose': str(old),
        'compose_sha256': hashlib.sha256(old.read_bytes()).hexdigest(), 'authorization': str(auth_file),
        'receipt_out': str(folder/'receipt.json')}
    f.declaration = protected(folder/'declaration.json', f.values)
    def inspect(*a):
        return {'available': True, **{k: v for k, v in f.stopped.items() if k in f.value['anchor']['docker']},
                'mounts': [mounted, volume]}
    def incarnation(*a, stopped=False, **kw):
        custody.require(not stopped or f.stopped_now)
        return deepcopy(f.stopped)
    def roster(*a, live_target=False, **kw):
        custody.require(live_target or f.stopped_now)
        return {'rendered': compose, 'roster': [], 'owner_storage': {'synthetic': True}}
    monkeypatch.setattr(router_manage, '_restart_custody', inspect)
    monkeypatch.setattr(router_manage, '_container_incarnation', incarnation)
    monkeypatch.setattr(router_manage, '_offline_compose_roster', roster)
    @contextmanager
    def helper(*a, **kw):
        yield 'a'*64, 'synthetic-helper', lambda state: custody.require(state == 'exited')
    monkeypatch.setattr(router_recovery_status, '_native_container', helper)
    @contextmanager
    def session(identity, request):
        with f.store._offline_custody(f.settings, allow_retained=True):
            observed, record, state = reboot.fenced_candidate(f.usage, f.auth, f.incumbent)
            f.fenced = True
            proposed = None
            def exchange(value):
                nonlocal proposed
                assert f.fenced
                if 'release' in value: return {'released': True}
                if 'stopped' in value:
                    assert f.stopped_now
                    proposed = reboot.recovered_record(record, state, observed, value['stopped'], value['intent'])
                    if proposed != record: custody.write(f.store, proposed, custody.digest(record))
                    if f.stage == 'after_stage': raise OSError('interrupted after pending proof')
                    return {'pending_sha256': custody.digest(proposed)}
                assert value == {'commit': custody.digest(proposed)}
                ready = {**proposed, 'phase': 'ready', 'sequence': 1}
                if ready != proposed: custody.write(f.store, ready, custody.digest(proposed))
                if f.stage == 'after_ready': raise OSError('interrupted after finalized proof')
                return {'finalized': 'reboot-dead', 'death_sha256': custody.digest(ready['death'])}
            try:
                yield observed, record['death']['stopped'] if record['death'] else None, exchange, lambda: custody.require(f.fenced)
            finally: f.fenced = False
    monkeypatch.setattr(reboot, '_session', session)
    def run(argv, **kw):
        if argv[:2] == ['docker', 'info']: result = f.incumbent['daemon_id']
        elif argv[:3] == ['docker', 'image', 'inspect']: result = f.auth['successor']['image_id']
        elif 'config' in argv:
            assert '--no-env-resolution' in argv and '--no-interpolate' in argv
            result = json.dumps(compose)
        elif argv[:2] == ['docker', 'inspect']:
            result = json.dumps('1000:1000' if '.Config.User' in argv[3] else str(old))
        else:
            assert argv == ['docker', 'stop', f.auth['expected_container_id']]
            assert f.fenced and Path(f.values['receipt_out']+'.pending').exists()
            if f.stage == 'before_stop': raise OSError('interrupted before stop')
            f.stops += 1; f.stopped_now = True
            if f.stage == 'after_stop': raise OSError('interrupted after stop')
            result = ''
        return SimpleNamespace(returncode=0, stdout=result)
    f.run = lambda **kw: maintenance.run(f.declaration, _run=run, **kw)
    def approve():
        preview = f.run(); f.auth['preview_sha256'] = custody.digest(preview)
        protected(auth_file, f.auth)
        return preview
    f.approve = approve
    return f


@pytest.mark.parametrize('stage', ['none', 'before_stop', 'after_stop', 'after_stage', 'after_ready'])
def test_host_retry_after_interrupt_and_expiry_keeps_exact_intent(reboot_host, monkeypatch, stage):
    f = reboot_host; preview = f.approve()
    assert f.stops == 0 and preview['drained'] is False
    if stage != 'none':
        f.stage = stage
        with pytest.raises(OSError): f.run(confirm=True)
        pending = json.loads(Path(f.values['receipt_out']+'.pending').read_text())
        f.stage = None
        monkeypatch.setattr(maintenance.time, 'time', lambda: f.auth['expires_at']+100)
    result = f.run(confirm=True)
    assert result['stopped'] and not result['drained'] and result['remote_memory_terminal'] == 'UNKNOWN'
    assert not f.fenced and f.stopped_now
    receipt = json.loads(Path(f.values['receipt_out']).read_text())
    if stage != 'none': assert receipt['acknowledged_at'] == pending['acknowledged_at']
    with pytest.raises(ValueError): f.run(confirm=True)


@pytest.mark.skipif(os.environ.get('ANVIL_ROUTER_DOCKER_TESTS') != '1', reason='explicit owned Docker qualification required')
def test_owned_docker_previous_boot_crash_loop_stops_and_starts_closed(docker_owner, monkeypatch):
    """The old boot is synthetic; Docker incarnations, volume and fences are real."""
    import subprocess
    from anvil_serving import router_manage
    from tests.router.test_container_owner_docker import MAIN
    from tests.router.test_maintenance_docker import auth, protected
    original_session = reboot._session
    def diagnostic_popen(argv, **kw):
        kw['stderr'] = None
        return subprocess.Popen(argv, **kw)
    monkeypatch.setattr(reboot, '_session', lambda identity, request: original_session(identity, request, _popen=diagnostic_popen))
    f = docker_owner; volume = 'ruu-reboot-' + uuid.uuid4().hex
    consumers = []
    original_roster = router_manage._offline_compose_roster
    def owned_roster(*a, **kw):
        result = original_roster(*a, **kw)
        # Keep all real consumer validation; unrelated host services can restart.
        result['roster'] = [r for r in result['roster'] if any(
            m.get('Name') == volume for m in r['mounts'])]
        return result
    monkeypatch.setattr(router_manage, '_offline_compose_roster', owned_roster)
    compose = json.loads(f['compose'].read_text()); service = compose['services']['router']; image = service['image']
    service['volumes'][0] = {'type': 'volume', 'source': 'store', 'target': '/fixture/private'}
    media = f['private'].parent/'media'; media.mkdir()
    service['volumes'].append({'type': 'bind', 'source': str(media), 'target': '/fixture/media'})
    compose['volumes'] = {'store': {'name': volume, 'external': True}}
    protected(f['compose'], compose)
    subprocess.run(['docker', 'volume', 'create', volume], check=True, capture_output=True)
    def owned(source, user='1000:1000'):
        name = 'ruu-reboot-setup-' + uuid.uuid4().hex
        try:
            return subprocess.check_output(['docker', 'run', '--name', name, '--network=none', '--memory=256m', '--cpus=1',
                '--pids-limit=32', '--user', user, '--mount', 'type=volume,src='+volume+',dst=/fixture/private',
                '--entrypoint', 'python', image, '-c', source], text=True, timeout=20)
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=15)
            assert subprocess.run(['docker', 'inspect', name], capture_output=True, timeout=5).returncode != 0
    def consumer(mount):
        name = 'ruu-reboot-consumer-' + uuid.uuid4().hex
        consumers.append(name)
        subprocess.run(['docker', 'run', '--detach', '--name', name, '--network=none', '--memory=128m',
            '--cpus=1', '--pids-limit=32', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges:true',
            '--user=1000:1000', '--mount', mount, '--entrypoint=python', image,
            '-c', 'import time;time.sleep(600)'], check=True, capture_output=True, timeout=15)
        return name
    try:
        mountpoint = subprocess.check_output(['docker', 'volume', 'inspect', '--format', '{{.Mountpoint}}', volume],
                                             text=True, timeout=5).strip()
        try:
            Path(mountpoint).stat()
        except PermissionError:
            pytest.skip('operator privileges required for host Docker volume inode inspection')
        owned("import os;os.chown('/fixture/private',1000,1000);os.chmod('/fixture/private',0o700)", user='0:0')
        f['start'](); f['custody']('live')
        incarnation = router_manage._container_incarnation(f['name'], _run=f['run'])
        subprocess.run(['docker', 'stop', f['name']], check=True, capture_output=True)
        revision = hashlib.sha256(f['config'].read_bytes()).hexdigest()
        # Only owned synthetic qualification data gets historical boot metadata.
        oldboot = str(uuid.uuid4())
        setup = r'''
import json,uuid
from pathlib import Path
from dataclasses import replace
from anvil_serving.router.keys import KeyStore
from anvil_serving.router import container_owner as c, maintenance as m
from anvil_serving.router.usage_store import RunOwner
s=KeyStore('/fixture/private/keys.sqlite3',_defer_open=True)
p=next(Path(str(s.path)+'.router-incarnations').glob('*.json'));v=json.loads(p.read_text())
old=replace(RunOwner(**v['anchor']['run_owner']),boot_id=OLD_BOOT)
v['anchor']['run_owner']['boot_id']=OLD_BOOT;v['anchor']['native_view']['boot_id']=OLD_BOOT
v['anchor']['roster_revision']=c.roster(old)
state_path=Path(c.binding(s)['state_path']);state=json.loads(state_path.read_text())
if state['closure'] is not None:
    state['closure']['roster_revision']=c.roster(old);state_path.write_text(json.dumps(state))
with s._connect() as db: db.execute('UPDATE usage_runs SET boot_id=?',(OLD_BOOT,))
p.write_text(json.dumps(v))
a=AUTH
preview={'schema':'router-legacy-maintenance-preview/v1','incarnation':v['anchor']['docker'],
 'configuration_revision':REVISION,'local_frontier':'UNKNOWN; legacy lacks complete all-path instrumentation',
 'remote_frontier':'UNKNOWN','drained':False,'compose_path':'/fixture/legacy.json','compose_sha256':'a'*64}
a['preview_sha256']=c.digest(preview)
stopped={**v['anchor']['docker'],'finished_at':v['anchor']['docker']['started_at'],
 'status':'exited','running':False,'paused':False,'restarting':False,'pid':0}
r={'schema':'router-legacy-maintenance/v1','authorization':a,'preview':preview,'stopped':stopped,
 'acknowledged_at':ACK,'drained':False,'legacy_coverage':'uninstrumented UNKNOWN','remote_memory_terminal':'UNKNOWN'}
m.validate_legacy(r)
i=s.path.stat();m._publish(Path(str(s.path)+'.maintenance-legacy.json'),{'schema':'router-legacy-frontier/v1',
 'receipt':r,'store_identity':[i.st_dev,i.st_ino],'configuration_revision':REVISION})
print(json.dumps({'snapshot':c.digest({p.name:c.digest(v)}),'legacy':c.digest(r)}))
'''
        source = ('OLD_BOOT='+repr(oldboot)+'\nAUTH='+repr(auth('legacy-stop', incarnation, revision))+
                  '\nREVISION='+repr(revision)+'\nACK='+str(int(time.time()))+'\n'+setup)
        history = json.loads(owned(source))
        f['main'].write_text('import time\ntime.sleep(.2)\nraise SystemExit(1)\n')
        subprocess.run(['docker', 'update', '--restart=always', f['name']], check=True, capture_output=True)
        subprocess.run(['docker', 'start', f['name']], check=True, capture_output=True)
        for _ in range(100):
            count = int(subprocess.check_output(['docker', 'inspect', '--format', '{{.RestartCount}}', f['name']], text=True))
            if count > 0: break
            time.sleep(.05)
        assert count > 0
        candidate = protected(f['private']/'candidate.json', compose)
        image_id = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], text=True).strip()
        authorization = auth('reboot-stop', incarnation, revision, schema='router-reboot-authorization/v1',
            acknowledge_uncertainty='remote-memory-terminal-only', ingress_barrier='previous-boot-exact-container-stop',
            boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            sidecar_snapshot_sha256=history['snapshot'], legacy_receipt_sha256=history['legacy'],
            successor={'image_id': image_id, 'configuration_revision': revision, 'compose': str(candidate),
                       'compose_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest()})
        permission = protected(f['private']/'authorization.json', authorization)
        values = {'schema': 'router-maintenance-config/v1', 'compose': str(f['compose']),
            'compose_sha256': hashlib.sha256(f['compose'].read_bytes()).hexdigest(),
            'authorization': str(permission), 'receipt_out': str(f['private']/'reboot-receipt.json')}
        declaration = protected(f['private']/'reboot-config.json', values)
        media_consumer = consumer('type=bind,src='+str(media)+',dst=/media')
        writer = consumer('type=volume,src='+volume+',dst=/store')
        with pytest.raises(ValueError, match='router_offline_storage_busy'):
            maintenance.run(declaration, _run=f['run'])
        subprocess.run(['docker', 'rm', '-f', writer], check=True, capture_output=True, timeout=15)
        preview = maintenance.run(declaration, _run=f['run'])
        assert preview['local_frontier'] == 'previous-boot-owner'
        authorization['preview_sha256'] = custody.digest(preview); protected(permission, authorization)
        result = maintenance.run(declaration, confirm=True, _run=f['run'])
        assert result['stopped'] and not result['drained']
        f['main'].write_text(MAIN)
        assert router_manage.cmd_up(str(f['compose']), service='router', container=f['name'], _run=f['run']) == 0
        f['start']()
        assert f['transition']('status')['state'] == 'quiesced'
        records = f['code']("import json;from pathlib import Path;print(json.dumps([json.loads(p.read_text()) for p in Path('/fixture/private/keys.sqlite3.router-incarnations').glob('*.json')]))")
        assert sorted(r['phase'] for r in records) == ['live', 'transferred']
        previous = next(r for r in records if r['phase'] == 'transferred')
        assert previous['schema'] == 'router-managed-incarnation/v2'
        assert previous['anchor']['run_owner']['boot_id'] == oldboot
        assert subprocess.check_output(['docker', 'inspect', '--format', '{{.State.Running}}', media_consumer],
                                       text=True, timeout=5).strip() == 'true'
    finally:
        for name in consumers:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=15)
        subprocess.run(['docker', 'rm', '-f', f['name']], capture_output=True, timeout=15)
        subprocess.run(['docker', 'volume', 'rm', volume], check=True, capture_output=True, timeout=15)
