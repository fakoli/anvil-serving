"""A failed prefixture binding can retire without erasing its high-water mark."""
from dataclasses import replace
import json
import os

import pytest

from anvil_serving.propagation_fencing import NativeMutationFence, PropagationFenceError
from tests.test_propagation_fencing import _CONTRACT, _CONTRACT2, _fence, _grant


def bound(tmp_path, *, revoked=lambda *_: True, quiet=lambda *_: 'c'*64):
    if os.name != "nt":
        tmp_path.chmod(0o700)
    original, target = _fence(tmp_path)
    fence = NativeMutationFence(replace(original.owner, cancelled_authority=revoked,
        bound_cancellation_proof=quiet), tmp_path)
    grant = _grant(fence, target)
    args = dict(canonical_contract=_CONTRACT, target_paths=(target,), job_id="job-1", job_digest="b"*64)
    fence.bind_job(grant, **args)
    return fence, grant, target, args


def test_exact_bound_cancellation_preserves_custody_and_refuses_old_replay(tmp_path):
    fence, grant, target, args = bound(tmp_path)
    with pytest.raises(PropagationFenceError, match="native_effects_present"):
        fence.inspect_cancelled(grant, **{k: v for k, v in args.items() if k != "job_digest"})
    receipt = fence.cancel_bound(grant, **args, quiescence_digest="c"*64)
    before = {p.name: p.read_bytes() for p in fence.journal_root.glob('*.json')}
    assert receipt['historical_preservation_claim'] is False
    assert receipt['attempted_fixture_effects_retained'] is True and receipt['erasure_performed'] is False
    assert target.read_bytes() == b"before"
    assert fence.cancel_bound(grant, **args, quiescence_digest="c"*64) == receipt
    observed = fence.inspect_bound_cancelled(grant, **args)
    assert observed['quiescent'] is True and observed['native_effect_count'] == 0
    assert {p.name: p.read_bytes() for p in fence.journal_root.glob('*.json')} == before
    index = json.loads(fence._index_path.read_bytes())
    assert index['high_water_generation'] == 1 and index['latest_reservation_id'] in index['operations']
    assert index['operations'][index['latest_reservation_id']]['status'] == 'cancelled'
    assert fence.journal()['status'] == fence.lookup(grant)['status'] == 'cancelled'
    with pytest.raises(PropagationFenceError, match="already_cancelled"):
        fence.bind_job(grant, **args)
    with pytest.raises(PropagationFenceError):
        with fence.transaction(grant, canonical_contract=_CONTRACT, target_paths=(target,)):
            pytest.fail('cancelled binding reopened')
    newer = _grant(fence, target, generation=2)
    fence.bind_job(newer, **{**args, 'canonical_contract': _CONTRACT2, 'job_id': 'job-2'})
    assert json.loads(fence._index_path.read_bytes())['high_water_generation'] == 2
    assert fence.lookup(grant)['status'] == 'cancelled'


@pytest.mark.parametrize('failure', ['unrevoked', 'active', 'wrong-job', 'wrong-digest', 'orphan'])
def test_bound_cancel_refuses_ambiguous_or_unrevoked_work_without_writes(tmp_path, failure):
    fence, grant, target, args = bound(tmp_path, revoked=lambda *_: failure != 'unrevoked',
                                     quiet=lambda *_: None if failure == 'active' else 'c'*64)
    if failure == 'orphan':
        path = fence._operation_path(fence._reservation_id(grant))
        path.write_text('{}')
        if os.name != 'nt':
            path.chmod(0o600)
    before = {p.name: p.read_bytes() for p in fence.journal_root.glob('*.json')}
    changed = {**args, **({'job_id': 'wrong'} if failure == 'wrong-job' else
                        {'job_digest': 'd'*64} if failure == 'wrong-digest' else {})}
    with pytest.raises(PropagationFenceError):
        fence.cancel_bound(grant, **changed, quiescence_digest='c'*64)
    assert {p.name: p.read_bytes() for p in fence.journal_root.glob('*.json')} == before
    assert target.read_bytes() == b'before'


def test_bound_cancellation_receipt_survives_interruption_and_detects_drift(tmp_path):
    calls = []
    def quiet(*_):
        calls.append(1)
        return None if len(calls) == 2 else 'c'*64
    fence, grant, target, args = bound(tmp_path, quiet=quiet)
    with pytest.raises(PropagationFenceError, match='quiescence_unproven'):
        fence.cancel_bound(grant, **args, quiescence_digest='c'*64)
    assert json.loads(fence._index_path.read_bytes())['operations'][fence._reservation_id(grant)]['status'] == 'bound'
    original = fence._cancellation_path(fence._reservation_id(grant)).read_bytes()
    fence.cancel_bound(grant, **args, quiescence_digest='c'*64)
    assert fence._cancellation_path(fence._reservation_id(grant)).read_bytes() == original
    with pytest.raises(PropagationFenceError, match='quiescence_unproven'):
        fence.cancel_bound(grant, **args, quiescence_digest='d'*64)
    target.write_bytes(b'unrelated later edit')
    with pytest.raises(PropagationFenceError, match='external_drift'):
        fence.inspect_bound_cancelled(grant, **args)


def test_bound_cancel_requires_the_trusted_proof_digest_and_valid_receipt(tmp_path):
    fence, grant, target, args = bound(tmp_path)
    with pytest.raises(PropagationFenceError, match='quiescence_unproven'):
        fence.cancel_bound(grant, **args, quiescence_digest='d'*64)
    assert not fence._cancellation_path(fence._reservation_id(grant)).exists()
    fence.cancel_bound(grant, **args, quiescence_digest='c'*64)
    path = fence._cancellation_path(fence._reservation_id(grant))
    original = path.read_bytes()
    for key, value in [('historical_preservation_claim', True), ('observed', []), ('generation', True), ('extra', None)]:
        receipt = json.loads(original)
        receipt[key] = value
        path.write_text(json.dumps(receipt))
        with pytest.raises(PropagationFenceError):
            fence.inspect_bound_cancelled(grant, **args)
    path.write_bytes(original)
    assert fence.inspect_bound_cancelled(grant, **args)['quiescent'] is True


def test_bound_cancel_survives_owner_approved_recovery_epoch(tmp_path):
    fence, grant, target, args = bound(tmp_path)
    fence.cancel_bound(grant, **args, quiescence_digest='c'*64)
    recovered = NativeMutationFence(replace(fence.owner, recovery_quiescent=lambda *_: True,
        recovery_authority=lambda *_: True), tmp_path)
    recovered.advance_recovery_epoch('epoch-2', 'd'*64, 'e'*64)
    index = json.loads(fence._index_path.read_bytes())
    assert index['epoch'] == 'epoch-2' and index['high_water_generation'] == 1
    assert index['operations'] == {}
    assert fence._cancellation_path(fence._reservation_id(grant)).exists()


@pytest.mark.skipif(os.name != 'posix', reason='installed catalog lock fixture is POSIX')
@pytest.mark.parametrize('prior_cutover', [False, True, 'current', 'unindexed'])
def test_catalog_bound_cancellation_recovery_with_retained_cutover(tmp_path, prior_cutover):
    from anvil_serving.control_plane.propagation import effect_scope_digest, parse_contract
    fence, _, target, _ = bound(tmp_path)
    # Separate resource namespace; the generic test reservation remains intact.
    fence = NativeMutationFence(replace(fence.owner, resource_id='client-catalog'), tmp_path)
    legacy = tmp_path/'.config/anvil-serving/pi'
    legacy.mkdir(mode=0o700)
    (legacy/'catalog.lock').write_bytes(b'')
    (legacy/'catalog.lock').chmod(0o600)
    def contract(generation):
        value=parse_contract(_CONTRACT).value
        value['generation']=generation
        value['targets'][0]['resource_keys']=['client-catalog']
        value['effect_set_digest']=effect_scope_digest(value)
        return parse_contract(value).canonical
    def grant(raw,generation):
        return fence.grant(canonical_contract=raw,generation=generation,effects=('catalog',),target_paths=(target,))
    if prior_cutover is True:
        raw=contract(1)
        with fence.transaction(grant(raw,1),canonical_contract=raw,target_paths=(target,)) as journal:
            journal.begin_effect('catalog',target,b'before',b'before')
            journal.observe_bytes('catalog',b'before')
    generation=2 if prior_cutover is True else 1
    raw=contract(generation)
    selected=grant(raw,generation)
    args=dict(canonical_contract=raw,target_paths=(target,),job_id='catalog-job',job_digest='b'*64)
    fence.bind_job(selected,**args)
    if prior_cutover in {'current', 'unindexed'}:
        # Activation precedes operation-journal creation. A crash in that gap
        # must not be retired as a generation with no fenced catalog effect.
        fence.activate_catalog_cutover(raw)
        if prior_cutover == 'unindexed':
            marker=json.loads(fence._catalog_cutover_path.read_bytes())
            marker['generation']=generation+1
            fence._catalog_cutover_path.write_text(json.dumps(marker))
        before={p.name:p.read_bytes() for p in fence.journal_root.glob('*.json')}
        with pytest.raises(PropagationFenceError,match='native_effects_present'):
            fence.cancel_bound(selected,**args,quiescence_digest='c'*64)
        assert before=={p.name:p.read_bytes() for p in fence.journal_root.glob('*.json')}
        return
    fence.cancel_bound(selected,**args,quiescence_digest='c'*64)
    recover=NativeMutationFence(replace(fence.owner,recovery_quiescent=lambda *_:True,
        recovery_authority=lambda *_:True),tmp_path)
    recover.advance_recovery_epoch('epoch-2','d'*64,'e'*64)
    index=json.loads(fence._index_path.read_bytes())
    assert index['epoch']=='epoch-2' and index['high_water_generation']==generation
    assert index['operations']=={}
    if prior_cutover is True:
        # Approved recovery clears the current operation rows but retains the
        # exact prior cutover in its recovery evidence. Later failed bindings
        # must remain cancellable without reviving a legacy writer.
        next_fence=NativeMutationFence(replace(recover.owner,epoch='epoch-2'),tmp_path)
        next_raw=contract(generation+1)
        next_grant=next_fence.grant(canonical_contract=next_raw,generation=generation+1,
            effects=('catalog',),target_paths=(target,))
        next_args=dict(canonical_contract=next_raw,target_paths=(target,),
            job_id='catalog-job-next',job_digest='d'*64)
        next_fence.bind_job(next_grant,**next_args)
        next_fence.cancel_bound(next_grant,**next_args,quiescence_digest='c'*64)
        assert next_fence.inspect_bound_cancelled(next_grant,**next_args)['native_effect_count']==0
        original_marker=next_fence._catalog_cutover_path.read_bytes()
        marker=json.loads(original_marker)
        marker['contract_digest']='f'*64
        next_fence._catalog_cutover_path.write_text(json.dumps(marker))
        with pytest.raises(PropagationFenceError,match='native_effects_present'):
            next_fence.inspect_bound_cancelled(next_grant,**next_args)
        next_fence._catalog_cutover_path.write_bytes(original_marker)
        next_fence.advance_recovery_epoch('epoch-3','d'*64,'e'*64)
        current=NativeMutationFence(replace(next_fence.owner,epoch='epoch-3'),tmp_path)
        assert current.journal() is None
        index=json.loads(current._index_path.read_bytes())
        retained=index['recovery']['retained_journals']
        assert len(retained)==3
        (current.journal_root/next(iter(retained))).write_text('{}')
        with pytest.raises(PropagationFenceError,match='unsafe_journal'):
            current.journal()
