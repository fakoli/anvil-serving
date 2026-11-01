"""Receipt-bound successor bootstrap; no live Docker or ownership mutations."""
from copy import deepcopy
import ast
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
    candidate_config.write_text(raw); candidate_config.chmod(0o600)
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
        helper = ast.parse(code)
        custody_block = next(node for node in ast.walk(helper)
                             if isinstance(node, ast.With)
                             and ast.unparse(node.items[0].context_expr)
                             == 'store._offline_custody(settings, allow_retained=True)')
        start_call = next(node for node in ast.walk(helper)
                          if isinstance(node, ast.Call) and ast.unparse(node.func) == '_offline_router_start')
        assert start_call not in ast.walk(custody_block)
        calls.append('native-read')
        return deepcopy(proof)
    monkeypatch.setattr(router_manage, '_offline_compose_run', native)
    def candidate_offline(compose, service, *, container, **kwargs):
        assert (service, container) == ('router', 'anvil-router')
        probe = json.loads(Path(compose).read_bytes())
        expected = deepcopy(old); expected['services'][service]['image'] = candidate['services'][service]['image']
        assert probe == expected and Path(compose).stat().st_mode & 0o777 == 0o600
        calls.append('candidate-offline')
    monkeypatch.setattr(router_manage, 'require_router_offline', candidate_offline)
    monkeypatch.setattr(router_manage, '_managed_container_custody', lambda kind, *a, **k: calls.append(kind))
    return SimpleNamespace(path=receipt_path, receipt=receipt, stopped=stopped, old=old,
        candidate=candidate, config=candidate_config, compose=f['new'], proof=proof, calls=calls, run=run,
        old_compose=f['old'], router_config=f['router_config'], scope=scope, protected=f['protected'])


def invoke(f, **kwargs):
    return router_manage.cmd_up(str(f.compose), 'router', current_stop_receipt=str(f.path),
                               _run=f.run, **kwargs)


def prepare_running_successor(f, monkeypatch, *, phases=('absent', 'live')):
    authorization = f.receipt['authorization']
    original_run = f.run
    running = dict(f.stopped)
    running.update(container_id='8' * 64, image_id=authorization['successor']['image_id'],
                   started_at='2026-10-26T18:00:00Z', restart_count=0)
    calls = []

    def incarnation(container, *, _run, stopped=False):
        assert container == 'anvil-router'
        if stopped:
            raise ValueError('router_container_custody_unknown')
        return deepcopy(running)

    def roster(compose, service, container, **kwargs):
        assert service == 'router' and container == 'anvil-router'
        assert kwargs.get('live_target') is True
        if compose == str(f.old_compose):
            assert kwargs.get('_metadata_only') is True
            rendered = f.old
        else:
            assert compose == str(f.compose)
            assert kwargs.get('_metadata_only') is not True
            rendered = f.candidate
        return {'rendered': rendered, 'owner_storage': f.scope,
                'roster': [], 'target': deepcopy(running)}

    def current_successor(target, incarnation, *, _run, _read_config=None):
        return deepcopy(f.old if target['compose'] == str(f.old_compose) else f.candidate)

    def run(argv, **kwargs):
        if argv[:3] == ['docker', 'exec', running['container_id']]:
            code = argv[-1]
            assert 'with live_writer(store)' in code
            assert 'native_transaction' not in code and 'write(' not in code
            compile(code, '<running-successor-proof>', 'exec')
            calls.append('native-proof')
            phase = phases[min(calls.count('native-proof') - 1, len(phases) - 1)]
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                {'ok': True, 'phase': phase, 'run_id': '12345678-1234-4234-8234-123456789abc'}))
        return original_run(argv, **kwargs)

    monkeypatch.setattr(router_manage, '_container_incarnation', incarnation)
    monkeypatch.setattr(router_manage, '_offline_compose_roster', roster)
    monkeypatch.setattr(router_manage, '_owner_storage_scope', lambda *a, **k: f.scope)
    monkeypatch.setattr(bootstrap, '_current_successor', current_successor)
    f.run = run
    return running, calls


def enable_predecessor_runtime(f, monkeypatch):
    f.compose = f.old_compose
    original = f.run
    def run(argv, **kwargs):
        if 'config' in argv and '-f' in argv and argv[argv.index('-f') + 1] == str(f.old_compose):
            return SimpleNamespace(returncode=0, stdout=json.dumps(f.old), stderr='')
        if argv[:3] == ['docker', 'image', 'inspect'] and argv[-1] == f.old['services']['router']['image']:
            return SimpleNamespace(returncode=0, stdout=f.stopped['image_id'], stderr='')
        return original(argv, **kwargs)
    f.run = run
    offline = []
    def require_offline(compose, service, **kwargs):
        assert compose == str(f.old_compose)
        assert service == 'router'
        offline.append((compose, kwargs))
        return {'schema':'router-offline-start/v1','offline':True,'schema_version':3}
    monkeypatch.setattr(router_manage, 'require_router_offline', require_offline)
    return offline


def test_explicit_rollback_starts_only_frozen_predecessor_closed(successor, monkeypatch):
    f = successor
    offline = enable_predecessor_runtime(f, monkeypatch)
    assert invoke(f, rollback_current_stop=True, dry_run=True) == 0
    assert offline and 'native-read' in f.calls
    assert not any(isinstance(call, list) and 'up' in call for call in f.calls)
    assert 'live' not in f.calls

    assert invoke(f, rollback_current_stop=True) == 0
    up_calls = [call for call in f.calls if isinstance(call, list) and 'up' in call]
    assert len(up_calls) == 1 and str(f.old_compose) in up_calls[0]
    assert f.calls[-1] == 'live'


@pytest.mark.parametrize('damage', ['wrong-compose', 'stale-compose', 'wrong-image', 'wrong-config',
                                    'wrong-environment', 'consumed-proof'])
def test_explicit_rollback_refuses_changed_or_consumed_predecessor(successor, monkeypatch, damage):
    f = successor
    enable_predecessor_runtime(f, monkeypatch)
    if damage == 'wrong-compose':
        f.compose = f.compose.parent / 'other.json'
    elif damage == 'stale-compose':
        f.old_compose.write_text('{}')
    elif damage == 'wrong-image':
        original = f.run
        def run(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[:3] == ['docker', 'image', 'inspect'] and argv[-1] == f.old['services']['router']['image']:
                return SimpleNamespace(returncode=0, stdout='sha256:'+'0'*64, stderr='')
            return result
        f.run = run
    elif damage == 'wrong-config':
        f.router_config.write_text(f.router_config.read_text() + '\n# drift\n')
    elif damage == 'wrong-environment':
        env_file = f.path.parent / 'declared.env'
        env_file.write_text('SYNTHETIC_AUTH=fixture\n'); env_file.chmod(0o600)
        f.receipt['preview']['environment'] = {
            'file':str(env_file), 'sha256':hashlib.sha256(env_file.read_bytes()).hexdigest()}
        f.receipt['authorization']['preview_sha256'] = maintenance.digest(f.receipt['preview'])
        f.protected(f.path, f.receipt)
        f.env_file = f.path.parent / 'other.env'
        f.env_file.write_bytes(env_file.read_bytes()); f.env_file.chmod(0o600)
    elif damage == 'consumed-proof':
        f.proof['phase'] = 'transferred'
    assert invoke(f, rollback_current_stop=True,
                  **({'env_file':str(f.env_file)} if damage == 'wrong-environment' else {})) == 1
    assert 'live' not in f.calls
    assert not any(isinstance(call, list) and 'up' in call for call in f.calls)


def test_rollback_mode_requires_completed_stop_receipt(capsys):
    calls = []
    assert router_manage.cmd_up('compose.json', 'router', rollback_current_stop=True,
        _run=lambda *args, **kwargs: calls.append(args) or pytest.fail('unexpected runner call')) == 1
    assert not calls
    assert 'HOLD' in capsys.readouterr().err


def test_changed_image_and_source_require_exact_receipt_and_preview_is_read_only(successor, monkeypatch):
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


def test_running_exact_successor_resumes_native_custody_without_compose_up(successor, monkeypatch):
    f = successor
    running, proof_calls = prepare_running_successor(f, monkeypatch)
    custody = []
    monkeypatch.setattr(router_manage, '_managed_container_custody',
        lambda kind, *a, **k: custody.append(kind) or
            {'finalized': 'live', 'run_id': '12345678-1234-4234-8234-123456789abc'})

    assert invoke(f) == 0
    assert custody == ['live'] and proof_calls == ['native-proof', 'native-proof']
    assert not any(isinstance(call, list) and 'up' in call for call in f.calls)
    assert running['image_id'] == f.receipt['authorization']['successor']['image_id']


def test_running_exact_successor_dry_run_only_validates(successor, monkeypatch):
    f = successor
    prepare_running_successor(f, monkeypatch, phases=('live-pending',))
    custody = []
    monkeypatch.setattr(router_manage, '_managed_container_custody',
        lambda *a, **k: custody.append(a) or pytest.fail('dry-run must not finalize custody'))

    assert invoke(f, dry_run=True) == 0
    assert not custody
    assert not any(isinstance(call, list) and 'up' in call for call in f.calls)


def test_exact_live_anchor_is_idempotent_without_native_helper(successor, monkeypatch):
    f = successor
    prepare_running_successor(f, monkeypatch, phases=('live',))
    monkeypatch.setattr(router_manage, '_managed_container_custody',
        lambda *a, **k: pytest.fail('exact live anchor must not run a helper'))

    assert invoke(f) == 0
    assert not any(isinstance(call, list) and 'up' in call for call in f.calls)


@pytest.mark.parametrize('damage', ['wrong-image', 'bad-anchor-phase', 'postcheck-pending'])
def test_running_successor_resume_fails_closed_without_restarting(successor, monkeypatch, capsys, damage):
    f = successor
    phases = ('other', 'live') if damage == 'bad-anchor-phase' else (
        ('absent', 'live-pending') if damage == 'postcheck-pending' else ('absent', 'live'))
    running, proof_calls = prepare_running_successor(f, monkeypatch, phases=phases)
    if damage == 'wrong-image':
        running['image_id'] = 'sha256:' + '0' * 64
        monkeypatch.setattr(router_manage, '_container_incarnation',
            lambda container, *, _run, stopped=False: (_ for _ in ()).throw(ValueError('router_container_custody_unknown'))
            if stopped else deepcopy(running))
    custody = []
    monkeypatch.setattr(router_manage, '_managed_container_custody',
        lambda *a, **k: custody.append(a) or
            {'finalized': 'live', 'run_id': '12345678-1234-4234-8234-123456789abc'})

    assert invoke(f) == 1
    assert not any(isinstance(call, list) and 'up' in call for call in f.calls)
    assert 'docker exec' not in ' '.join(str(call) for call in f.calls)
    if damage != 'wrong-image':
        assert bool(custody) is (damage == 'postcheck-pending')
        if custody:
            assert custody[0][0] == 'live'
    err = capsys.readouterr().err
    assert 'stage=' in err and 'code=' in err
    assert 'fixture' not in err and 'successor.toml' not in err


@pytest.mark.parametrize('damage', ['ack', 'preview', 'death', 'stopped', 'image', 'compose', 'config',
    'owner', 'writable', 'user', 'command', 'environment', 'entrypoint', 'extra-service', 'candidate-offline', 'image-and-config', 'extra-alias', 'lineage', 'active', 'transferred', 'closure', 'receipt-race'])
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
    elif damage == 'image-and-config':
        f.config.write_text(f.config.read_text()+'\n# simultaneous config change\n')
        declared=f.receipt['authorization']['successor']; declared['configuration_revision']=hashlib.sha256(f.config.read_bytes()).hexdigest()
        f.receipt['preview']['successor']=deepcopy(declared)
        f.receipt['authorization']['preview_sha256']=maintenance.digest(f.receipt['preview'])
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
    assert cli.main(['router','up','--compose',str(f.old_compose),
                     '--current-stop-receipt',str(f.path),'--rollback-current-stop','--dry-run']) == 1
    assert calls[-1]['rollback_current_stop'] is True
    assert cli.main(['router','up','--current-stop-receipt',str(f.path),
                     '--startup-abort-config','other.json','--dry-run']) == 2
    assert cli.main(['router','up','--rollback-current-stop','--dry-run']) == 1
    for action in ('down', 'restart', 'reload'):
        assert cli.main(['router', action, '--dry-run']) == 0
