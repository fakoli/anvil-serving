"""A real promotion lock must survive its nested authenticated transition."""
import io
import json
import pytest
from anvil_serving import serves, router_manage

@pytest.mark.parametrize('action', ['quiesce', 'drain', 'readmit'])
def test_nested_transition_retains_lock_and_auth(action, tmp_path, monkeypatch):
    monkeypatch.setattr(serves, 'config_path', lambda name: str(tmp_path/name))
    monkeypatch.setenv('ANVIL_ROUTER_TOKEN', 'test-only-router-token')
    original = router_manage.transition_request
    seen = []
    def opened(request, timeout):
        assert 'promotion' in serves._SERVING_AUTHORITY_LOCAL.roles
        assert request.get_header('Authorization') == 'Bearer test-only-router-token'
        seen.append(json.loads(request.data))
        return io.BytesIO(b'{"result":{"drained":true}}')
    def transition(*args, **kwargs):
        return original(*args, **kwargs, _open=opened)
    monkeypatch.setattr(router_manage, 'transition_request', transition)
    with serves._switch_role_lock('promotion'):
        assert serves._transition_cli('http://127.0.0.1:8000', action, 'primary-local', timeout=1 if action == 'drain' else None) == 0
    assert seen[0]['action'] == action
    assert not serves._SERVING_AUTHORITY_LOCAL.roles
