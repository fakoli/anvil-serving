"""Authorization type/expiry, private crash publication and typed frontier guards."""
import os
from pathlib import Path
import sys
import json
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


def test_registered_cli_maintenance_forwards_leaf_options_without_subcommand(monkeypatch, capsys):
    from anvil_serving import cli

    calls = []

    def native_run(config, *, confirm=False, preview_out=None):
        calls.append((config, confirm, preview_out))
        return {'schema': 'synthetic-maintenance-result', 'confirmed': confirm}

    monkeypatch.setattr(maintenance, 'run', native_run)
    arguments = ['router', 'maintenance', '--config', 'maintenance.json',
                 '--preview-out', 'preview.json']
    assert cli.main(arguments) == 0
    assert calls == [('maintenance.json', False, 'preview.json')]
    assert cli.main([*arguments, '--confirm']) == 0
    assert calls == [('maintenance.json', False, 'preview.json'),
                     ('maintenance.json', True, 'preview.json')]
    for malformed in (['router', 'maintenance'], [*arguments, '--unexpected'],
                      ['router', 'maintenance', 'maintenance', '--config', 'maintenance.json']):
        assert cli.main(malformed) == 2
        assert len(calls) == 2
    assert 'synthetic-maintenance-result' in capsys.readouterr().out


@pytest.fixture
def current_stop(tmp_path, monkeypatch):
    """Real protected publication + custody vocabulary; synthetic Docker only."""
    if not sys.platform.startswith('linux'):
        pytest.skip('native managed maintenance requires Linux custody')
    import hashlib
    import json
    from contextlib import contextmanager
    from copy import deepcopy
    from dataclasses import asdict
    from types import SimpleNamespace
    from anvil_serving import router_manage
    from anvil_serving.router import container_owner as custody
    from anvil_serving.router.usage_store import RunOwner

    def protected(path, value):
        path.write_text(json.dumps(value)); path.chmod(0o600); return path
    monkeypatch.setenv('ANVIL_SERVING_HOME', str(tmp_path/'operator-home'))
    old = protected(tmp_path/'old.json', {'services': {'router': {'image': 'fixture:old'}}})
    new = protected(tmp_path/'new.json', {'services': {'router': {'image': 'fixture:new'}}})
    router_config = protected(tmp_path/'router.json', {'synthetic': True})
    revision = hashlib.sha256(router_config.read_bytes()).hexdigest()
    owner = RunOwner('fixture', '12345678-1234-1234-1234-123456789abc', 1, 2, 1, 2, 1000, 3, 4, 20, 30)
    incarnation = {'daemon_id': 'fixture-daemon', 'container_id': 'c'*64, 'image_id': 'sha256:'+'d'*64,
                   'started_at': '2026-01-01T00:00:00Z', 'restart_count': 0,
                   'compose_project': 'fixture', 'compose_service': 'router'}
    binding = {'synthetic_store_binding': True}
    state = {'barrier_token': '1'*64, 'configuration_revision': revision, 'roster_revision': custody.roster(owner),
             'policy_revision': '2'*64, 'generation': 2, 'consumed': False}
    native_view = {k:v for k,v in asdict(owner).items() if k not in {'host_domain_id', 'pid', 'start_ticks'}}
    native_view['container_init_start_ticks'] = 1
    anchor = {'owner_backend': 'managed-container', 'owner_id': 'fixture', 'domain_id': 'fixture',
              'run_id': '12345678-1234-1234-1234-123456789abc', 'run_owner': asdict(owner),
              'store_binding_sha256': custody.digest(binding), 'configuration_revision': revision,
              'roster_revision': custody.roster(owner), 'docker': incarnation, 'native_view': native_view}
    record = {'schema': 'router-managed-incarnation/v1', 'phase': 'live', 'sequence': 0,
              'anchor': anchor, 'death': None, 'transfer': None}
    observed = {'schema': 'router-maintenance-preview/v1', 'legacy_receipt_sha256': '3'*64,
                'run_id': anchor['run_id'], 'owner_id': 'fixture', 'domain_id': 'fixture',
                'store_binding': binding, 'anchor_sha256': custody.digest(anchor), 'closure': state,
                'counts': {k:0 for k in ('chat','purpose','audio','memory','media','internal','delivery','maintenance')},
                'eligible_uncertainty': 'remote-memory-terminal-unknown',
                'remote_frontier': 'UNKNOWN; backend supplies no terminal operation readback',
                'nonce': '4'*64, 'expires_at': int(time.time())+60, 'observation_revision': '5'*64}
    auth = authorization(); auth.update(schema='router-current-stop-authorization/v1', phase='current-instrumented-stop',
        expected_configuration_revision=revision, legacy_receipt_sha256='3'*64,
        acknowledge_uncertainty='remote-memory-terminal-only', ingress_barrier='closed-generation-exact-container-stop',
        successor={'image_id': 'sha256:'+'f'*64, 'configuration_revision': revision,
                   'compose': str(new), 'compose_sha256': hashlib.sha256(new.read_bytes()).hexdigest()})
    auth_path = protected(tmp_path/'authorization.json', auth)
    values = {'schema':'router-maintenance-config/v1', 'compose': str(old),
              'compose_sha256': hashlib.sha256(old.read_bytes()).hexdigest(),
              'authorization': str(auth_path), 'receipt_out': str(tmp_path/'receipt.json')}
    declaration = protected(tmp_path/'declaration.json', values)
    f = {'auth': auth, 'values': values, 'config': declaration, 'native': {'record':record, 'observation':observed},
         'incarnation':incarnation, 'stopped':False, 'stops':0, 'fenced':False, 'stage':None,
         'protected':protected, 'router_config':router_config, 'old':old, 'new':new, 'proofs':0,
         'marker':tmp_path/'canonical-marker.json', 'roster': {'complete': True}, 'checks':0}
    def incarnation_read(*args, stopped=False, **kwargs):
        if stopped and not f['stopped']: raise ValueError('not physically stopped')
        return ({**f['incarnation'], 'finished_at':'2026-01-01T01:00:00Z', 'status':'exited',
                 'running':False, 'paused':False, 'restarting':False, 'pid':0} if stopped else deepcopy(f['incarnation']))
    def roster(*a, **kw):
        f['checks'] += 1
        return {**deepcopy(f['roster']), 'owner_storage': {
            'configuration_revision': hashlib.sha256(router_config.read_bytes()).hexdigest()}}
    monkeypatch.setattr(router_manage, '_container_incarnation', incarnation_read)
    monkeypatch.setattr(router_manage, '_offline_compose_roster', roster)
    monkeypatch.setattr(router_manage, '_restart_custody', lambda *a: {'mounts':[
        {'destination':router_manage.DEFAULT_INSTALLED_CONFIG, 'source':str(router_config), 'type':'bind','read_only':True}]})
    @contextmanager
    def session(*a, **kw):
        f['fenced'] = True
        def commit(marker):
            if f['stage'] == 'commit': raise ValueError('writer unavailable')
            maintenance._publish(f['marker'], marker)
            if f['stage'] == 'after_commit': raise OSError('crash after canonical permission')
            if f['stage'] == 'swap_after_commit': f['roster']['complete'] = False
        try: yield deepcopy(f['native']), commit, lambda: maintenance.require(f['fenced'])
        finally: f['fenced'] = False
    monkeypatch.setattr(maintenance, '_current_stop_session', session)
    def stop(argv, **kw):
        if 'config' in argv:
            assert '--no-env-resolution' in argv and '--no-interpolate' in argv
            return SimpleNamespace(returncode=0, stdout=json.dumps({'name':'fixture','services':{
                'router':{'container_name':router_manage.DEFAULT_CONTAINER,'image':'fixture:new',
                    'user':f'{os.geteuid()}:{os.getegid()}', 'volumes':[
                    {'target':router_manage.DEFAULT_INSTALLED_CONFIG,'source':str(router_config),'type':'bind','read_only':True}]}}}))
        if argv[:3] == ['docker','image','inspect']:
            return SimpleNamespace(returncode=0, stdout='sha256:'+'f'*64)
        if argv[:2] == ['docker','inspect']:
            return SimpleNamespace(returncode=0, stdout=json.dumps(str(old)))
        assert argv == ['docker','stop',incarnation['container_id']]
        assert f['fenced'] and f['marker'].exists()
        f['stops'] += 1
        if f['stage'] == 'stop_failure': return SimpleNamespace(returncode=1, stdout='')
        f['stopped'] = True
        if f['stage'] == 'after_stop': raise OSError('crash after physical stop')
        return SimpleNamespace(returncode=0, stdout='')
    def proof(*a, **kw):
        assert f['stopped'] and not f['fenced']
        f['proofs'] += 1
        if f['stage'] == 'proof': raise ValueError('offline fence unavailable')
        death = {'anchor_sha256': custody.digest(anchor), 'closure_sha256': '6'*64,
                 'closure_generation':2, 'closure_consumed':False,
                 'stopped':incarnation_read(stopped=True), 'implementation_sha256':'7'*64}
        return {**record, 'phase':'ready', 'sequence':1, 'death':death}
    f['native_proof'] = maintenance._current_stop_proof
    f['proof'] = proof
    monkeypatch.setattr(maintenance, '_current_stop_proof', proof)
    f['run'] = lambda **kw: maintenance.run(declaration, _run=stop, **kw)
    def approve():
        preview=f['run']();f['auth']['preview_sha256']=custody.digest(preview)
        protected(auth_path,f['auth']);return preview
    f['approve']=approve
    return f


def _declare_environment(f, path):
    import hashlib
    import json
    # Model the production service's required credential interpolation.
    f['old'].write_text(json.dumps({'services': {'router': {'image': 'fixture:old',
        'environment': {'ANVIL_MEDIA_CONTROLLER_TOKEN': '${ANVIL_MEDIA_CONTROLLER_TOKEN:?required}'}}}}))
    f['values']['compose_sha256'] = hashlib.sha256(f['old'].read_bytes()).hexdigest()
    f['values']['environment'] = {'file': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    f['protected'](f['config'], f['values'])


def test_current_stop_environment_is_private_pinned_and_forwarded_to_both_helpers(current_stop, tmp_path, monkeypatch):
    import json
    from anvil_serving import router_manage
    f = current_stop
    path = tmp_path/'credentials.env'; path.write_text('ANVIL_MEDIA_CONTROLLER_TOKEN="synthetic-secret"\nUNRELATED_TOKEN=omitted\n'); path.chmod(0o600)
    _declare_environment(f, path)
    monkeypatch.setenv('ANVIL_MEDIA_CONTROLLER_TOKEN', 'wrong-parent-secret')
    monkeypatch.setenv('DOCKER_HOST', 'untrusted-parent-daemon')
    monkeypatch.setenv('PYTHONPATH', '/untrusted-parent')
    preview = f['approve']()
    assert preview['environment'] == f['values']['environment']
    assert 'synthetic-secret' not in json.dumps(preview)
    calls = []
    def check(kwargs):
        snapshot = Path(kwargs['env_file'])
        assert snapshot != path and snapshot.read_bytes() == path.read_bytes()
        assert snapshot.stat().st_mode & 0o777 == 0o600
        assert snapshot.parent.stat().st_mode & 0o777 == 0o700
        assert kwargs['execution_env'] == {'PATH': os.defpath, 'ANVIL_MEDIA_CONTROLLER_TOKEN': 'synthetic-secret'}
        calls.append(snapshot)
    def custody(kind, *args, **kwargs):
        check(kwargs)
        assert kind == 'dead'
        return {'finalized': 'dead', 'run_id': f['native']['record']['anchor']['run_id']}
    def offline(*args, **kwargs):
        check(kwargs)
        assert kwargs['_owner_state_only'] is True
        return f['proof']()
    monkeypatch.setattr(router_manage, '_managed_container_custody', custody)
    monkeypatch.setattr(router_manage, '_offline_compose_run', offline)
    monkeypatch.setattr(maintenance, '_current_stop_proof', f['native_proof'])
    assert f['run'](confirm=True)['applied']
    assert len(calls) == 2 and calls[0] == calls[1]
    assert not calls[0].exists() and not calls[0].parent.exists()
    assert path.exists() and f['stops'] == 1
    assert 'synthetic-secret' not in Path(f['values']['receipt_out']).read_text()


@pytest.mark.parametrize('damage', ['missing', 'digest', 'symlink', 'hardlink', 'mode'])
def test_environment_preview_never_reads_secret_and_confirmation_refuses_unsafe_input(current_stop, tmp_path, damage):
    f = current_stop
    path = tmp_path/'credentials.env'; path.write_text('ANVIL_MEDIA_CONTROLLER_TOKEN=synthetic\n'); path.chmod(0o600)
    _declare_environment(f, path)
    if damage == 'missing': path.unlink()
    elif damage == 'digest': path.write_text('ANVIL_MEDIA_CONTROLLER_TOKEN=changed\n')
    elif damage == 'symlink': path.rename(tmp_path/'original'); path.symlink_to(tmp_path/'original')
    elif damage == 'hardlink': os.link(path, tmp_path/'extra')
    else: path.chmod(0o640)
    assert f['approve']()['environment'] == f['values']['environment']
    with pytest.raises((ValueError, OSError)): f['run'](confirm=True)
    assert f['stops'] == f['proofs'] == 0 and not f['marker'].exists()


def test_confirm_resolves_compose_environment_before_physical_stop(current_stop, tmp_path, monkeypatch):
    from anvil_serving import router_manage
    f = current_stop
    path = tmp_path/'credentials.env'; path.write_text('OTHER_TOKEN=synthetic\n'); path.chmod(0o600)
    _declare_environment(f, path)
    original = router_manage._offline_compose_roster
    calls = []
    def roster(*args, **kwargs):
        if kwargs.get('env_file'):
            calls.append(kwargs['env_file'])
            assert not kwargs.get('_metadata_only') and f['stops'] == 0
            raise ValueError('required credential is absent')
        return original(*args, **kwargs)
    monkeypatch.setattr(router_manage, '_offline_compose_roster', roster)
    f['approve']()
    assert calls == []
    with pytest.raises(ValueError): f['run'](confirm=True)
    assert len(calls) == 1 and not Path(calls[0]).exists()
    assert f['stops'] == f['proofs'] == 0 and not f['marker'].exists()


def test_environment_change_after_permission_cannot_cross_physical_stop(current_stop, tmp_path, monkeypatch):
    f = current_stop
    path = tmp_path/'credentials.env'; path.write_text('ANVIL_MEDIA_CONTROLLER_TOKEN=synthetic\n'); path.chmod(0o600)
    _declare_environment(f, path); f['approve']()
    publish = maintenance._publish
    def changed(target, *args, **kwargs):
        result = publish(target, *args, **kwargs)
        if target == f['marker']: path.write_text('ANVIL_MEDIA_CONTROLLER_TOKEN=changed\n')
        return result
    monkeypatch.setattr(maintenance, '_publish', changed)
    with pytest.raises(ValueError): f['run'](confirm=True)
    assert f['stops'] == f['proofs'] == 0


def test_pending_stop_binds_environment_reference_and_recovery_never_stops_again(current_stop, tmp_path):
    f = current_stop
    path = tmp_path/'credentials.env'; path.write_text('ANVIL_MEDIA_CONTROLLER_TOKEN=synthetic\n'); path.chmod(0o600)
    _declare_environment(f, path); f['approve']()
    f['stage'] = 'after_stop'
    with pytest.raises(OSError): f['run'](confirm=True)
    assert f['stops'] == 1 and f['proofs'] == 0
    other = tmp_path/'other.env'; other.write_bytes(path.read_bytes()); other.chmod(0o600)
    f['values']['environment']['file'] = str(other)
    f['protected'](f['config'], f['values'])
    f['stage'] = None
    with pytest.raises(ValueError): f['run'](confirm=True)
    assert f['stops'] == 1 and f['proofs'] == 0
    f['values']['environment']['file'] = str(path)
    f['protected'](f['config'], f['values'])
    assert f['run'](confirm=True)['applied']
    assert f['stops'] == 1 and f['proofs'] == 1


@pytest.mark.parametrize('change', ['extra', 'relative', 'digest', 'wrong_phase'])
def test_environment_declaration_is_closed_and_current_stop_only(current_stop, tmp_path, change):
    f = current_stop
    path = tmp_path/'credentials.env'; path.write_text('ANVIL_MEDIA_CONTROLLER_TOKEN=synthetic\n'); path.chmod(0o600)
    _declare_environment(f, path)
    if change == 'extra': f['values']['environment']['values'] = {}
    elif change == 'relative': f['values']['environment']['file'] = 'credentials.env'
    elif change == 'digest': f['values']['environment']['sha256'] = 'partial'
    else:
        auth = authorization(); f['protected'](Path(f['values']['authorization']), auth)
    f['protected'](f['config'], f['values'])
    with pytest.raises(ValueError): f['run']()
    assert f['stops'] == f['proofs'] == f['checks'] == 0


@pytest.mark.parametrize('mode', [0o640, 0o660, 0o666])
def test_current_stop_accepts_nonsecret_service_config_without_credential_exemption(current_stop, monkeypatch, mode):
    from anvil_serving.control_plane.mcp import auth_file

    f = current_stop
    original = auth_file.read_private_auth_file
    def credentials(path, **kwargs):
        if Path(path) == f['router_config']:
            raise auth_file.AuthFileError('service-owned nonsecret config')
        return original(path, **kwargs)
    monkeypatch.setattr(auth_file, 'read_private_auth_file', credentials)
    f['router_config'].chmod(mode)
    if mode != 0o640:
        with pytest.raises(ValueError): f['approve']()
        assert f['stops'] == 0
        return
    f['approve']()
    assert f['run'](confirm=True)['stopped'] is True
    assert f['router_config'].stat().st_mode & 0o777 == mode


def test_current_stop_native_preview_and_exact_physical_stop_never_claim_drain(current_stop):
    import json
    f=current_stop;preview=f['approve']()
    assert f['stops']==0 and not f['marker'].exists() and not f['fenced']
    assert preview['local_frontier']=='instrumented-quiesced' and preview['drained'] is False
    result=f['run'](confirm=True)
    assert result['stopped'] and not result['drained'] and result['unknown']==['memory']
    assert f['stops']==1 and f['proofs']==1 and not f['fenced']
    receipt=json.loads(open(f['values']['receipt_out']).read())
    assert receipt['legacy_receipt_sha256']=='3'*64 and receipt['death_sha256']
    with pytest.raises(ValueError):f['run'](confirm=True)
    assert f['stops']==1


@pytest.mark.parametrize('case',['local','boolean_zero','missing_family','extra_unknown','expired_native','consumed',
                                'anchor','store','run','foreign_container','wrong_revision','unanchored'])
def test_current_stop_refuses_every_unsupported_frontier_before_stop(current_stop,case):
    f=current_stop;v=f['native']['observation']
    if case=='local':v['counts']['delivery']=1
    elif case=='boolean_zero':v['counts']['memory']=False
    elif case=='missing_family':del v['counts']['audio']
    elif case=='extra_unknown':v['eligible_uncertainty']='owner-roster-unknown'
    elif case=='expired_native':v['expires_at']=int(time.time())
    elif case=='consumed':v['closure']['consumed']=True
    elif case=='anchor':v['anchor_sha256']='0'*64
    elif case=='store':v['store_binding']={'different':True}
    elif case=='run':v['run_id']='22345678-1234-1234-1234-123456789abc'
    elif case=='foreign_container':f['incarnation']['container_id']='0'*64
    elif case=='wrong_revision':v['closure']['configuration_revision']='0'*64
    else:f['native']['record']['phase']='live-pending'
    with pytest.raises(ValueError):f['run']()
    assert not f['marker'].exists() and f['stops']==0


@pytest.mark.parametrize('stage',['commit','after_commit','swap_after_commit','stop_failure','after_stop','proof'])
def test_current_stop_crash_recovery_never_retries_a_live_stop(current_stop,stage):
    from pathlib import Path
    f=current_stop;f['approve']();f['stage']=stage
    with pytest.raises((ValueError,OSError)):f['run'](confirm=True)
    stops=f['stops'];f['stage']=None
    if f['stopped']:
        result=f['run'](confirm=True)
        assert result['stopped'] and result['drained'] is False and f['stops']==stops
    else:
        # Canonical permission survives a crash even before host pending publication.
        if stage=='commit':
            assert not f['marker'].exists() and not Path(f['values']['receipt_out']+'.pending').exists()
        else:
            with pytest.raises(ValueError):f['run'](confirm=True)
            assert f['stops']==stops


def test_current_stop_pending_finalizes_after_original_authorization_expires(current_stop, monkeypatch):
    f=current_stop; f['approve'](); f['stage']='after_stop'
    with pytest.raises(OSError): f['run'](confirm=True)
    f['stage']=None
    monkeypatch.setattr(maintenance.time, 'time', lambda: f['auth']['expires_at']+3600)
    result=f['run'](confirm=True)
    assert result['stopped'] and f['stops']==1 and f['proofs']==1
    receipt=json.loads(Path(f['values']['receipt_out']).read_text())
    assert receipt['acknowledged_at'] < f['auth']['expires_at']
    assert receipt['authorization']==f['auth']


def test_current_stop_expired_without_pending_cannot_start(current_stop, monkeypatch):
    f=current_stop; f['approve']()
    monkeypatch.setattr(maintenance.time, 'time', lambda: f['auth']['expires_at']+1)
    with pytest.raises(ValueError): f['run'](confirm=True)
    assert f['stops']==0 and f['proofs']==0 and not f['marker'].exists()


@pytest.mark.parametrize('damage', ['authorization','preview','compose','marker','timestamp','environment'])
def test_current_stop_expired_recovery_rejects_unbound_pending(current_stop, monkeypatch, damage):
    f=current_stop; f['approve'](); f['stage']='after_stop'
    with pytest.raises(OSError): f['run'](confirm=True)
    f['stage']=None
    path=Path(f['values']['receipt_out']+'.pending'); pending=json.loads(path.read_text())
    if damage=='authorization': pending['authorization']['operation_id']='0'*64
    elif damage=='preview': pending['preview']['drained']=True
    elif damage=='compose':
        f['values']['compose_sha256']='0'*64; f['protected'](f['config'],f['values'])
    elif damage=='marker': pending['marker']['native_sha256']='0'*64
    elif damage=='timestamp': pending['acknowledged_at']=f['auth']['expires_at']
    else: pending['preview']['environment']={'file':'/protected/other.env','sha256':'0'*64}
    f['protected'](path,pending)
    monkeypatch.setattr(maintenance.time, 'time', lambda: f['auth']['expires_at']+1)
    with pytest.raises(ValueError): f['run'](confirm=True)
    assert f['stops']==1 and f['proofs']==0


def test_current_stop_pending_cannot_stop_restarted_incarnation_or_refresh_expiry(current_stop):
    f=current_stop;f['approve']();f['stage']='after_stop'
    with pytest.raises(OSError):f['run'](confirm=True)
    f['stage']=None;f['stopped']=False;f['incarnation']['restart_count']+=1
    with pytest.raises(ValueError):f['run'](confirm=True)
    assert f['stops']==1
    f['stopped']=True
    with pytest.raises(ValueError):f['run'](confirm=True)
    f['auth']['expires_at']=int(time.time())-1
    f['protected'](Path(f['values']['authorization']),f['auth'])
    with pytest.raises(ValueError):f['run'](confirm=True)
    assert f['stops']==1


@pytest.mark.parametrize('target',['old','new','router_config','auth'])
def test_current_stop_confirmation_rejects_changed_reviewed_inputs(current_stop,target):
    f=current_stop;f['approve']()
    if target=='auth':
        f['auth']['preview_sha256']='0'*64
        f['protected'](Path(f['values']['authorization']),f['auth'])
    else:f[target].write_text('{"changed":true}')
    with pytest.raises(ValueError):f['run'](confirm=True)
    assert f['stops']==0


@pytest.mark.parametrize('change',['legacy_schema','readmit_schema','missing_successor','unknown_successor',
                                   'relative_successor','bad_image','bad_config','missing_legacy'])
def test_current_stop_authority_has_closed_distinct_approval_vocabulary(current_stop,change):
    from copy import deepcopy
    value=deepcopy(current_stop['auth'])
    if change=='legacy_schema':value['schema']='router-maintenance-authorization/v1'
    elif change=='readmit_schema':value['phase']='successor-readmit'
    elif change=='missing_successor':del value['successor']
    elif change=='unknown_successor':value['successor']['force']=True
    elif change=='relative_successor':value['successor']['compose']='candidate.json'
    elif change=='bad_image':value['successor']['image_id']='latest'
    elif change=='bad_config':value['successor']['configuration_revision']=False
    else:value['legacy_receipt_sha256']=None
    with pytest.raises(ValueError):maintenance.authorization(value)


@pytest.mark.parametrize('boundary',['pending_before','pending_after','receipt_before','receipt_after'])
def test_current_stop_publication_crash_keeps_one_shot_and_truthful_physical_evidence(current_stop,monkeypatch,boundary):
    f=current_stop;f['approve']();publish=maintenance._publish
    receipt=Path(f['values']['receipt_out']);target=receipt if boundary.startswith('receipt') else Path(str(receipt)+'.pending')
    def fail(path,value,**kw):
        if Path(path)==target:
            if boundary.endswith('after'):publish(path,value,**kw)
            raise OSError('synthetic publication interruption')
        return publish(path,value,**kw)
    monkeypatch.setattr(maintenance,'_publish',fail)
    with pytest.raises(OSError):f['run'](confirm=True)
    monkeypatch.setattr(maintenance,'_publish',publish)
    assert f['marker'].exists()
    if boundary=='receipt_before':
        assert f['run'](confirm=True)['stopped']
    else:
        with pytest.raises(ValueError):f['run'](confirm=True)
    assert f['stops']==(1 if boundary.startswith('receipt') else 0)


def test_current_stop_canonical_operation_record_defeats_changed_output_path(current_stop):
    f=current_stop;f['approve']();f['stage']='after_commit'
    with pytest.raises(OSError):f['run'](confirm=True)
    f['stage']=None
    f['values']['receipt_out']+='-another'
    f['protected'](f['config'],f['values'])
    with pytest.raises(ValueError):f['run'](confirm=True)
    assert f['stops']==0


def test_ordinary_drain_and_consume_stay_strict_after_typed_remote_gap():
    persisted=[]
    owner=RouterAdmission('fixture',owner_scope=lambda:None,persist=persisted.append)
    owner.observe('memory',lambda:(0,maintenance.REMOTE_MEMORY_TERMINAL_UNKNOWN))
    closure=owner.quiesce_router(dry_run=False,confirm=True)
    result=owner.drain_router(closure['barrier_token'],timeout_s=1)
    assert result['drained'] is False and result['unknown']==['memory']
    with pytest.raises(ValueError):owner.consume(closure['barrier_token'])
    assert persisted and persisted[-1]['consumed'] is False


@pytest.mark.parametrize('change', ['none', 'config_before', 'config_during', 'mount_during', 'private_declaration'])
def test_successor_readmit_uses_mounted_roster_not_credential_reader(current_stop, monkeypatch, change):
    import hashlib
    import json
    from types import SimpleNamespace
    from anvil_serving import router_manage
    from anvil_serving.control_plane.mcp import auth_file

    f = current_stop
    auth = {**f['auth'], 'schema': 'router-maintenance-authorization/v1',
            'phase': 'successor-readmit', 'ingress_barrier': 'closed-generation'}
    del auth['successor']
    f['protected'](Path(f['values']['authorization']), auth)
    monkeypatch.setattr(router_manage, '_container_compose_project', lambda *a, **kw: ('running', 'fixture'))
    private = auth_file.read_private_auth_file
    def credentials(path, **kw):
        # Model root's credential policy refusing the service-owned config.
        if Path(path) == f['router_config']:
            raise auth_file.AuthFileError('different service owner')
        return private(path, **kw)
    monkeypatch.setattr(auth_file, 'read_private_auth_file', credentials)
    calls = []
    def roster(*a, **kw):
        assert kw['_metadata_only'] is True and kw['_owner_state_only'] is True
        calls.append('roster')
        return {'owner_storage': {'configuration_revision': hashlib.sha256(f['router_config'].read_bytes()).hexdigest()},
                'target': {'config_source': f.get('source', str(f['router_config']))}}
    monkeypatch.setattr(router_manage, '_offline_compose_roster', roster)
    def native(argv, **kw):
        assert argv[:3] == ['docker', 'exec', '-i']
        assert json.loads(kw['input']) == {'authorization': auth, 'confirm': False}
        calls.append('native')
        if change == 'config_during': f['router_config'].write_text('changed')
        if change == 'mount_during': f['source'] = '/synthetic/other.toml'
        return SimpleNamespace(returncode=0, stdout='{"preview":true}')
    if change == 'config_before': f['router_config'].write_text('changed')
    if change == 'private_declaration': f['config'].chmod(0o644)
    if change == 'none':
        assert maintenance.run(f['config'], _run=native) == {'preview': True}
        assert calls == ['roster', 'native', 'roster']
    else:
        with pytest.raises(ValueError): maintenance.run(f['config'], _run=native)
        assert ('native' in calls) == change.endswith('_during')


@pytest.mark.parametrize('kind',['service-0600','root-group-readable','foreign-owner','service-unreadable','group-writable','world-writable','symlink','hardlink','owner-race','oversized'])
def test_current_successor_binds_exact_service_config_owner_and_custody(tmp_path,monkeypatch,kind):
    """Root operator + production UID1000 service; real bounded no-follow read.

    Ownership metadata is injected so the same regression runs without chown or
    root privileges. File identity/read/mode/link and race checks remain real.
    """
    import hashlib
    import json
    from types import SimpleNamespace
    from anvil_serving import router_manage,operator_config
    config=tmp_path/'service.toml';config.write_bytes(b'synthetic = true\n');config.chmod(0o600)
    owner=[1000];group=1000
    if kind in ('root-group-readable','service-unreadable'):owner[0]=0
    if kind=='root-group-readable':config.chmod(0o640)
    if kind=='foreign-owner':owner[0]=2000;config.chmod(0o644)
    if kind=='group-writable':config.chmod(0o660)
    if kind=='world-writable':config.chmod(0o606)
    if kind=='hardlink':os.link(config,tmp_path/'hardlink')
    if kind=='oversized':config.write_bytes(b'x'*(2*1024**2+1))
    raw=config.read_bytes()
    if kind=='symlink':config.rename(tmp_path/'target');config.symlink_to(tmp_path/'target')
    original_lstat=Path.lstat
    class Metadata:
        def __init__(self,actual):self.actual=actual;self.st_uid=owner[0];self.st_gid=group
        def __getattr__(self,name):return getattr(self.actual,name)
    def metadata(path,*args,**kwargs):
        actual=original_lstat(path,*args,**kwargs)
        return Metadata(actual) if path==config else actual
    monkeypatch.setattr(Path,'lstat',metadata)
    monkeypatch.setattr(os,'geteuid',lambda:0)
    original_read=operator_config._read_bounded;reads=[]
    def bounded(path,**kwargs):
        reads.append(kwargs);value=original_read(path,**kwargs)
        if kind=='owner-race':owner[0]=2000
        return value
    monkeypatch.setattr(operator_config,'_read_bounded',bounded)
    def run(argv,**kwargs):
        if 'config' in argv:
            assert '--no-env-resolution' in argv and '--no-interpolate' in argv
            return SimpleNamespace(returncode=0,stdout=json.dumps({'name':'fixture','services':{'router':{
                'container_name':router_manage.DEFAULT_CONTAINER,'image':'fixture:current','user':'1000:1000',
                'volumes':[{'target':router_manage.DEFAULT_INSTALLED_CONFIG,'source':str(config),'type':'bind','read_only':True}]}}}))
        assert argv[:3]==['docker','image','inspect']
        return SimpleNamespace(returncode=0,stdout='sha256:'+'f'*64)
    successor={'compose':str(tmp_path/'compose.json'),'image_id':'sha256:'+'f'*64,'configuration_revision':hashlib.sha256(raw).hexdigest()}
    if kind in ('service-0600','root-group-readable'):
        maintenance._current_successor(successor,{'compose_project':'fixture'},_run=run)
        assert len(reads)==1 and reads[0]['max_bytes']==2*1024**2 and reads[0]['expected_identity']
    else:
        with pytest.raises(ValueError):maintenance._current_successor(successor,{'compose_project':'fixture'},_run=run)
