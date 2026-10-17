"""Receipt-bound successor bootstrap; no live Docker or ownership mutations."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving import router_current_stop as bootstrap, router_manage
from anvil_serving.router import maintenance
from tests.router.test_maintenance import current_stop as current_stop


@pytest.fixture
def successor(current_stop, monkeypatch):
    f = current_stop
    root = f['old'].parent
    storage = root / 'storage'; storage.mkdir()
    raw = Path('configs/example.toml').read_text() + '''
[server]
auth_env="SYNTHETIC_AUTH"
api_keys_path="/fixture/state/keys.sqlite3"
admission_state_path="/fixture/state/admission.json"
router_owner_id="fixture"
router_owner_roster=["fixture"]
router_owner_backend="managed-container"
usage_domain_id="fixture"
'''
    f['router_config'].write_text(raw)
    candidate_config = root / 'successor.toml'
    candidate_config.write_text(raw + '\n# authorized successor configuration\n'); candidate_config.chmod(0o600)
    revision = hashlib.sha256(f['router_config'].read_bytes()).hexdigest()
    f['auth']['expected_configuration_revision'] = revision
    native = f['native']; native['record']['anchor']['configuration_revision'] = revision
    native['observation']['closure']['configuration_revision'] = revision
    native['observation']['anchor_sha256'] = maintenance.digest(native['record']['anchor'])
    # The stop fixture owns publication. Its simulated successor reader uses
    # the old config; switch to the declared successor after that publication.
    f['auth']['successor']['configuration_revision'] = revision
    f['protected'](Path(f['values']['authorization']), f['auth'])
    f['approve'](); f['run'](confirm=True)
    stopped = router_manage._container_incarnation('anvil-router', _run=None, stopped=True)
    receipt_path = Path(f['values']['receipt_out'])
    receipt = json.loads(receipt_path.read_text())
    def rendered(source):
        return {'name':'fixture', 'services':{'router':{
            'container_name':'anvil-router', 'image':'fixture:new', 'user':f'{os.geteuid()}:{os.getegid()}',
            'volumes':[
                {'type':'bind','source':str(source),'target':router_manage.DEFAULT_INSTALLED_CONFIG,'read_only':True},
                {'type':'bind','source':str(source),'target':'/fixture/consumer/router.toml','read_only':True},
                {'type':'bind','source':str(storage),'target':'/fixture/state'}]}}}
    old, candidate = rendered(f['router_config']), rendered(candidate_config)
    old['services']['router']['image'] = 'fixture:old'
    f['protected'](f['old'], old); f['protected'](f['new'], candidate)
    receipt['preview']['compose_sha256'] = hashlib.sha256(f['old'].read_bytes()).hexdigest()
    declared = receipt['authorization']['successor']
    declared['image_id'] = 'sha256:' + '7'*64
    assert declared['image_id'] != stopped['image_id']
    declared['compose_sha256'] = hashlib.sha256(f['new'].read_bytes()).hexdigest()
    declared['configuration_revision'] = hashlib.sha256(candidate_config.read_bytes()).hexdigest()
    receipt['preview']['successor'] = deepcopy(declared)
    receipt['authorization']['preview_sha256'] = maintenance.digest(receipt['preview'])
    f['protected'](receipt_path, receipt)
    scope = router_manage._owner_storage_scope(old, old['services']['router']['volumes'],
        old['services']['router']['volumes'][0], {}, None)
    calls = []
    def roster(*args, **kwargs):
        assert args[0] == str(f['old'])
        calls.append('old-roster')
        return {'rendered':old,'owner_storage':scope,'roster':[], 'target':{'container_id':stopped['container_id']}}
    monkeypatch.setattr(router_manage, '_offline_compose_roster', roster)
    def run(argv, **kwargs):
        calls.append(argv)
        if 'config' in argv:
            assert '-f' in argv and argv[argv.index('-f')+1] == str(f['new'])
            assert '--no-env-resolution' in argv
            return SimpleNamespace(returncode=0, stdout=json.dumps(candidate))
        if argv[:3] == ['docker','image','inspect']:
            return SimpleNamespace(returncode=0, stdout=declared['image_id'])
        if 'up' in argv:
            return SimpleNamespace(returncode=0, stdout='')
        pytest.fail(str(argv))
    proof = f['proof']()
    def native(compose, service, container, code, **kwargs):
        assert compose == str(f['old']) and kwargs['_owner_state_only']
        assert '_offline_router_start()' in code and 'native_transaction' not in code
        calls.append('native-read')
        return deepcopy(proof)
    monkeypatch.setattr(router_manage, '_offline_compose_run', native)
    def candidate_offline(compose, service, *, container, **kwargs):
        assert compose == str(f['new']) and (service, container) == ('router', 'anvil-router')
        calls.append('candidate-offline')
    monkeypatch.setattr(router_manage, 'require_router_offline', candidate_offline)
    monkeypatch.setattr(router_manage, '_managed_container_custody', lambda kind, *a, **k: calls.append(kind))
    return SimpleNamespace(path=receipt_path, receipt=receipt, stopped=stopped, old=old,
        candidate=candidate, config=candidate_config, compose=f['new'], proof=proof, calls=calls, run=run,
        scope=scope, protected=f['protected'])


def invoke(f, **kwargs):
    return router_manage.cmd_up(str(f.compose), 'router', current_stop_receipt=str(f.path),
                               _run=f.run, **kwargs)


def test_changed_source_and_configuration_require_exact_receipt_and_preview_is_read_only(successor, monkeypatch):
    f = successor
    monkeypatch.setenv('UNAPPROVED_COMPOSE_OVERRIDE', 'must-not-propagate')
    original=f.run
    def run(argv, **kwargs):
        if 'up' in argv:
            assert kwargs['env'] == {'PATH':os.defpath}
        return original(argv, **kwargs)
    f.run=run
    assert bootstrap.completed_receipt(f.path, f.stopped) == f.receipt
    bootstrap.require_successor(str(f.path), str(f.compose), 'router', 'anvil-router', f.stopped, _run=f.run)
    assert invoke(f, dry_run=True) == 0
    assert 'native-read' in f.calls and 'candidate-offline' in f.calls and 'live' not in f.calls
    assert not any(isinstance(c, list) and 'up' in c for c in f.calls)
    assert invoke(f) == 0
    assert f.calls[-1] == 'live'
    assert sum(isinstance(c, list) and 'up' in c for c in f.calls) == 1
    # Expiry after the completed physical stop does not invalidate its receipt.
    monkeypatch.setattr(bootstrap.time, 'time', lambda: f.receipt['authorization']['expires_at'] + 10)
    assert invoke(f, dry_run=True) == 0


@pytest.mark.parametrize('damage', ['ack', 'preview', 'death', 'stopped', 'image', 'compose', 'config',
    'owner', 'writable', 'user', 'command', 'environment', 'entrypoint', 'extra-service', 'candidate-offline', 'extra-alias', 'lineage', 'active', 'transferred', 'closure', 'receipt-race'])
def test_changed_authority_refuses_before_start(successor, monkeypatch, damage):
    f = successor
    if damage == 'ack': f.receipt['acknowledged_at'] = f.receipt['authorization']['expires_at']
    elif damage == 'preview': f.receipt['preview']['native']['observation']['counts']['chat'] = 1
    elif damage == 'death': f.proof['death']['implementation_sha256'] = '0'*64
    elif damage == 'stopped':
        f.stopped['restart_count'] += 1
        monkeypatch.setattr(router_manage, '_container_incarnation', lambda *a, **k: f.stopped)
    elif damage == 'image':
        original = f.run
        def run(argv, **kw):
            result = original(argv, **kw)
            return SimpleNamespace(returncode=0, stdout='sha256:'+'0'*64) if argv[:3] == ['docker','image','inspect'] else result
        f.run = run
    elif damage == 'compose': f.compose.write_text('{}')
    elif damage == 'config': f.config.write_text(f.config.read_text()+'\n# drift\n')
    elif damage == 'owner':
        f.config.write_text(f.config.read_text().replace('router_owner_id="fixture"','router_owner_id="other"'))
        declared=f.receipt['authorization']['successor']; declared['configuration_revision']=hashlib.sha256(f.config.read_bytes()).hexdigest()
        f.receipt['preview']['successor']=deepcopy(declared)
        f.receipt['authorization']['preview_sha256']=maintenance.digest(f.receipt['preview'])
    elif damage == 'writable': f.candidate['services']['router']['volumes'][-1]['target'] = '/different'
    elif damage == 'user': f.candidate['services']['router']['user'] = '123:123'
    elif damage == 'command': f.candidate['services']['router']['command'] = ['unexpected']
    elif damage == 'environment': f.candidate['services']['router']['environment'] = {'UNAPPROVED': 'value'}
    elif damage == 'entrypoint': f.candidate['services']['router']['entrypoint'] = ['unexpected']
    elif damage == 'extra-service': f.candidate['services']['unexpected'] = {}
    elif damage == 'candidate-offline':
        monkeypatch.setattr(router_manage, 'require_router_offline', lambda *a, **k: (_ for _ in ()).throw(ValueError('incompatible_candidate')))
    elif damage == 'extra-alias':
        for rendered in (f.old, f.candidate):
            mounts=rendered['services']['router']['volumes']
            mounts.append({**mounts[0], 'target':'/third/router.toml'})
    elif damage == 'lineage': f.scope['lineages'] = []
    elif damage == 'active':
        monkeypatch.setattr(router_manage, '_offline_compose_roster', lambda *a, **k: (_ for _ in ()).throw(ValueError('router_offline_storage_busy')))
    elif damage == 'transferred': f.proof['phase'] = 'transferred'
    elif damage == 'closure': f.proof['death']['closure_consumed'] = True
    elif damage == 'receipt-race':
        original = router_manage._offline_compose_run
        def native(*a, **kw):
            result = original(*a, **kw)
            f.path.write_text('{}'); f.path.chmod(0o600)
            return result
        monkeypatch.setattr(router_manage, '_offline_compose_run', native)
    f.protected(f.path, f.receipt)
    assert invoke(f) == 1
    assert 'live' not in f.calls
    assert not any(isinstance(c, list) and 'up' in c for c in f.calls)


@pytest.mark.parametrize('damage', ['valid', 'different-file', 'changed-bytes', 'undeclared'])
def test_environment_is_exactly_the_protected_stop_declaration(successor, damage):
    f=successor
    env=f.path.parent/'router.env';env.write_text('SYNTHETIC_AUTH=fixture\n');env.chmod(0o600)
    if damage != 'undeclared':
        f.receipt['preview']['environment']={'file':str(env),'sha256':hashlib.sha256(env.read_bytes()).hexdigest()}
        f.receipt['authorization']['preview_sha256']=maintenance.digest(f.receipt['preview'])
        f.protected(f.path,f.receipt)
    if damage == 'different-file':
        other=env.with_name('other.env');other.write_bytes(env.read_bytes());other.chmod(0o600);env=other
    elif damage == 'changed-bytes': env.write_text('SYNTHETIC_AUTH=changed\n')
    assert invoke(f, env_file=str(env), dry_run=True) == (0 if damage == 'valid' else 1)


@pytest.mark.parametrize('target', ['environment', 'compose'])
def test_unapproved_inputs_are_rejected_before_read(successor, monkeypatch, target):
    def forbidden(*a, **kw):
        pytest.fail('unapproved environment must never be read')
    monkeypatch.setattr(router_manage, '_compose_execution_env', forbidden)
    if target == 'environment':
        assert invoke(successor, env_file='/private/implicit.env', dry_run=True) == 1
    else:
        successor.compose=Path('/private/implicit.env')
        assert invoke(successor, dry_run=True) == 1


def test_recovery_flags_are_exclusive_and_registered_preview_validates(successor, monkeypatch):
    from anvil_serving import cli
    f = successor
    assert invoke(f, startup_abort_config='other.json') == 1
    calls=[]
    def cmd(*a, **kw): calls.append(kw); return 1
    monkeypatch.setattr(router_manage, 'cmd_up', cmd)
    assert cli.main(['router','up','--compose',str(f.compose),'--env-file','/dev/null',
                     '--current-stop-receipt',str(f.path),'--dry-run']) == 1
    assert calls[0]['dry_run'] is True and calls[0]['current_stop_receipt'] == str(f.path)
    assert cli.main(['router','up','--current-stop-receipt',str(f.path),
                     '--startup-abort-config','other.json','--dry-run']) == 2
    for action in ('down', 'restart', 'reload'):
        assert cli.main(['router', action, '--dry-run']) == 0
