import copy
from pathlib import Path

import pytest

from anvil_serving import jev
from anvil_serving.benchmarking import jev_pilot as pilot


def packet():
    return {"schema": pilot.SCHEMA, "id": "case1", "intent": "dependency failure",
            "observation": "The import did not load", "optional_limit": 1,
            "evidence": [{"id": name, "text": text, "kind": kind, "artifact": name + '.json',
                          "sha256": None if kind == 'missing_capture' else 'a' * 64}
                         for name, text, kind in [
                             ('required', 'all gates required', 'mandatory'),
                             ('opposing', 'one gate failed', 'contradiction'),
                             ('lost', 'raw capture missing', 'missing_capture'),
                             ('a', 'dependency failure', 'optional'),
                             ('b', 'startup import resolution', 'optional')]]}


def settings():
    return jev.validate_policy({"enabled": True, "capabilities": ['context_ranking', 'incident_triage'],
                               "allow_api": True, "allow_export": True,
                               "anvil_binary": str(Path(__file__).absolute())})


def advice(capability, value, **kwargs):
    if capability == 'context_ranking':
        answers = {item['id']: {'score': i} for i, item in enumerate(value['candidates'])}
    else:
        answers = {'category': {'choice': 'missing_dependency'}}
    return jev.report(capability, 'completed', 'completed', started=True) | {'used': True, 'answers': answers}


def test_shadow_preserves_required_opposing_missing_and_every_reference():
    value = packet()
    result = pilot.replay(value, policy_reader=settings, advise=advice)
    assert result['views']['jev']['selected_ids'] == ['required', 'opposing', 'lost', 'b']
    assert result['views']['deterministic']['selected_ids'] == ['required', 'opposing', 'lost', 'a']
    assert all(set(result['pinned_ids']) <= set(view['selected_ids']) for view in result['views'].values())
    assert len(result['original_artifacts']) == 5
    assert result['qualification_or_lifecycle_actions'] == []
    assert result['automatic_consumption'] is False
    assert result['views']['jev']['escalate'] is True
    assert value == packet()


def test_stale_or_revoked_returns_all_context_and_retains_attempts():
    reads = iter([True, True, False])
    result = pilot.replay(packet(), policy_reader=settings, advise=advice, current=lambda: next(reads))
    assert result['views']['jev']['selected_ids'] == [r['id'] for r in packet()['evidence']]
    assert result['fallbacks'] == ['input_or_policy_changed']
    assert len(result['annotations']) == 2


def test_off_switch_does_not_start_provider_and_uses_baseline():
    result = pilot.replay(packet(), policy_reader=settings, disabled=True)
    assert all(not a['request_started'] for a in result['annotations'])
    assert result['views']['jev'] == result['views']['deterministic']


def test_known_category_avoids_triage_call_and_ties_preserve_coverage():
    value = packet()
    value['observation'] = 'ModuleNotFoundError: selected package'
    calls = []
    def tied(capability, value, **kwargs):
        calls.append(capability)
        return jev.report(capability, 'completed', 'completed', started=True) | {
            'used': True, 'answers': {r['id']: {'score': 1} for r in value['candidates']}}
    result = pilot.replay(value, policy_reader=settings, advise=tied)
    assert calls == ['context_ranking']
    assert result['views']['jev']['category'] == 'missing_dependency'
    assert result['views']['jev']['selected_ids'] == result['views']['current']['selected_ids']
    assert result['fallbacks'] == ['ambiguous_ranking_boundary']


@pytest.mark.parametrize('mutation', [
    lambda p: p['evidence'].append(copy.deepcopy(p['evidence'][0])),
    lambda p: p['evidence'][0].update(sha256=None),
    lambda p: p.update(optional_limit=True),
    lambda p: p['evidence'][0].update(kind='hidden'),
])
def test_invalid_packets_fail_before_calls(mutation):
    value = packet()
    mutation(value)
    with pytest.raises(ValueError):
        pilot.replay(value, advise=lambda *a, **kw: pytest.fail('invalid packet exported'))


def test_conflicting_deterministic_signatures_escalate():
    assert pilot.deterministic_category('401 followed by CUDA out of memory') == 'unknown'


def test_failed_call_receipt_and_policy_read_failure_remain_observable():
    reads = [0]
    def reader():
        reads[0] += 1
        if reads[0] > 2:
            raise ValueError('revoked config')
        return settings()
    def failed(capability, value, **kwargs):
        return jev.report(capability, 'invalid_response', 'invalid_answer', started=True) | {
            'usage_receipt': {'state': 'validated', 'input_tokens': 80, 'output_tokens': 6}}
    result = pilot.replay(packet(), policy_reader=reader, advise=failed)
    assert result['annotations'][0]['usage_receipt']['input_tokens'] == 80
    assert result['views']['jev']['selected_ids'] == result['views']['current']['selected_ids']
    assert 'input_or_policy_changed' in result['fallbacks']


def test_provider_failure_restores_full_view_and_unfamiliar_triage_stays_escalated():
    value = packet()
    value['evidence'] = [r for r in value['evidence'] if r['kind'] != 'missing_capture']
    def unavailable(capability, value, **kwargs):
        return jev.report(capability, 'unavailable', 'provider_timeout', started=True)
    result = pilot.replay(value, policy_reader=settings, advise=unavailable)
    assert result['views']['jev']['selected_ids'] == result['views']['current']['selected_ids']
    assert result['views']['jev']['escalate']
    result = pilot.replay(value, policy_reader=settings, advise=advice)
    assert result['views']['jev']['category'] == 'missing_dependency'
    assert result['views']['jev']['escalate']


def test_disabled_jev_keeps_deterministic_comparator():
    value = packet()
    value['observation'] = '401 authentication failed'
    value['evidence'] = [r for r in value['evidence'] if r['kind'] != 'missing_capture']
    result = pilot.replay(value, policy_reader=settings, disabled=True)
    assert result['views']['jev'] == result['views']['deterministic']
