from pathlib import Path


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


def test_conflicting_deterministic_signatures_escalate():
    assert pilot.deterministic_category('401 followed by CUDA out of memory') == 'unknown'


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
