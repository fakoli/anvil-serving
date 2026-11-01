import hashlib
import json
from dataclasses import replace

import pytest

from anvil_serving.native_agents.auth import AdmissionDenied, NativeAuthenticator


def test_provider_requests_identify_the_native_broker_to_https_edges(monkeypatch):
    from anvil_serving.native_agents.auth import provider_request
    sent = {}
    class Response:
        status = 200
        def read(self, maximum): return b'{"active":false}'
    class Connection:
        sock = None
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ('auth.example.test', 443, 5)
        def connect(self): pass
        def request(self, method, path, *, body, headers): sent.update(headers)
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr('anvil_serving.native_agents.auth.http.client.HTTPSConnection', Connection)
    headers = {'Authorization': 'Basic synthetic'}
    assert provider_request('https://auth.example.test/api/oidc/introspection', 'POST', headers, b'token=synthetic') == {'active': False}
    assert sent['User-Agent'] == 'AnvilConnect-NativeAgents/1.0'
    assert sent['Accept'] == 'application/json' and sent['Authorization'] == headers['Authorization']
    assert headers == {'Authorization': 'Basic synthetic'}


@pytest.fixture
def setup():
    config = {'issuer':'https://auth.example.test', 'introspection_endpoint':'https://auth.example.test/api/oidc/introspection',
              'userinfo_endpoint':'https://auth.example.test/api/oidc/userinfo', 'introspection_client_id':'native-broker',
              'secret_file':'/etc/example/broker-secret', 'native_client_ids':['example-ios'],
              'authority_file':'/etc/example/native-authority', 'resource':'native-agents', 'source_resource':'pi-web'}
    principal = 'human:' + hashlib.sha256(b'https://auth.example.test\0subject-a').hexdigest()
    state = {'now':1000, 'claims':{'active':True, 'client_id':'example-ios', 'exp':2000, 'iss':config['issuer'],
                                'scope':'openid profile', 'sub':'subject-a'}, 'user':{'sub':'subject-a'},
             'policy':{'schema':'anvil-native-agents-authority/v1', 'issuer':config['issuer'],
                       'resource':'native-agents', 'source_resource':'pi-web', 'issued_at':1000, 'expires_at':1030, 'principals':{principal:7}},
             'calls':[], 'on_request':None}
    def read(path, maximum):
        return b'synthetic-host-secret' if path == config['secret_file'] else json.dumps(state['policy']).encode()
    def request(url, method, headers, body=None):
        state['calls'].append((url, method))
        if state['on_request']:
            state['on_request']()
        return dict(state['claims'] if method == 'POST' else state['user'])
    auth = NativeAuthenticator(config, read=read, request=request, clock=lambda:state['now'])
    return auth, state, principal, config


@pytest.mark.parametrize('patch', [
    {'active':False}, {'active':1}, {'client_id':'other-client'}, {'client_id':None},
    {'iss':'https://other.example.test'}, {'iss':None}, {'exp':1000}, {'exp':True}, {'exp':1000000},
    {'scope':'profile'}, {'sub':None}, {'sub':''},
])
def test_untrusted_provider_claims_are_not_authority(setup, patch):
    auth, state, _, _ = setup
    state['claims'].update(patch)
    with pytest.raises(AdmissionDenied): auth.authorize('opaque-provider-token')
    assert not any(method == 'GET' for _, method in state['calls'])


def test_native_authority_is_required_even_for_provider_admin(setup):
    auth, state, _, _ = setup
    state['policy']['principals'] = {}
    state['user']['groups'] = ['admin']
    with pytest.raises(AdmissionDenied): auth.authorize('token')


def test_revocation_during_provider_round_trip_is_denied(setup):
    auth, state, _, _ = setup
    state['on_request'] = lambda:state['policy'].update(principals={})
    with pytest.raises(AdmissionDenied): auth.authorize('token')


def test_recheck_closes_generation_and_stale_authority(setup):
    auth, state, principal, _ = setup
    first = auth.authorize('token')
    state['policy']['principals'][principal] = 8
    assert not auth.recheck(first)
    second = auth.authorize('token')
    state['now'] = 1030
    assert not auth.recheck(second)


def test_provider_revocation_checked_during_active_stream(setup):
    auth, state, _, _ = setup
    first = auth.authorize('token')
    state['now'] = 1014
    state['claims']['active'] = False
    assert auth.recheck(first)
    state['now'] = 1015
    assert not auth.recheck(first)


def test_forged_or_restarted_admission_not_reusable(setup):
    auth, state, _, config = setup
    first = auth.authorize('token')
    assert not auth.recheck(replace(first, principal='human:' + 'b'*64))
    assert not auth.recheck(first)
    other = NativeAuthenticator(config, read=auth.read, request=auth.request, clock=auth.clock)
    assert not other.recheck(first)


def test_bounded_memory_token_custody(setup):
    auth, _, _, _ = setup
    first = auth.authorize('first')
    for index in range(256): auth.authorize('token-' + str(index))
    assert len(auth._held) == 8
    assert not auth.recheck(first)
    assert 'token-255' not in repr(next(reversed(auth._held.values())))


def test_repeated_requests_reuse_provider_window_but_recheck_authority(setup):
    auth, state, principal, _ = setup
    first = auth.authorize('token')
    for _ in range(8):
        assert auth.authorize('token') == first
        assert auth.recheck(first)
    assert len(state['calls']) == 2
    state['now'] = 1015
    assert auth.authorize('token') == first
    assert len(state['calls']) == 4
    state['policy']['principals'].pop(principal)
    assert not auth.recheck(first)


def test_one_principals_token_churn_does_not_evict_another(setup):
    auth, state, _, config = setup
    other = auth.authorize('other-user-token')
    principal_b = 'human:' + hashlib.sha256(b'https://auth.example.test\0subject-b').hexdigest()
    state['policy']['principals'][principal_b] = 1
    state['claims']['sub'] = state['user']['sub'] = 'subject-b'
    for index in range(256): auth.authorize('new-' + str(index))
    assert len(auth._held) == 9
    assert auth.recheck(other)


@pytest.mark.parametrize('token', ['', 'a\nb', 'a b', 'x'*8193, None])
def test_invalid_bearer_rejected_before_provider(setup, token):
    auth, state, _, _ = setup
    with pytest.raises(AdmissionDenied): auth.authorize(token)
    assert state['calls'] == []
