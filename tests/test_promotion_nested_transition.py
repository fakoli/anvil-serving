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


def test_readmit_identity_refusal_is_failure(monkeypatch):
    monkeypatch.setattr(router_manage, 'transition_request', lambda *a, **kw: {'result': {'readmitted': False}})
    assert serves._transition_cli('http://127.0.0.1:8000', 'readmit', 'primary-local') == 1


def test_bind_mount_install_uses_deployed_parent_and_preserves_mode(tmp_path):
    import subprocess
    config = tmp_path/'candidate.toml'; config.write_text('[router]\n')
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        output = ''
        if argv[:4] == ['docker','inspect','-f','{{.Config.Image}}']:
            output = 'test-image'
        elif argv[:3] == ['docker','inspect','-f']:
            output = json.dumps({'cmd':['--config','/etc/anvil/config.toml'], 'mounts':[{'Type':'bind','Source':'/srv/operator/router.toml','Destination':'/etc/anvil/config.toml'}]})
        return subprocess.CompletedProcess(argv,0,output,'')
    assert serves._install_router_config(str(config),_run=run) == 0
    writes = [(a,k) for a,k in calls if a[:3] == ['docker','run','--rm'] and 'cat >' in a[-1]]
    assert len(writes) == 1
    argv,kwargs = writes[0]
    assert '/srv/operator:/cfg' in argv and 'cp -p /cfg/router.toml' in argv[-1]
    assert kwargs['input'] == b'[router]\n'
    assert calls[-1][0] == ['docker','restart','anvil-router']


def test_canonical_volume_without_cmd_and_failed_restart_restores_metadata(tmp_path):
    import subprocess
    config = tmp_path/'candidate.toml'; config.write_text('[router]\n')
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        output = ''; rc = 0
        if argv[:4] == ['docker','inspect','-f','{{.Config.Image}}']:
            output = 'test-image'
        elif argv[:3] == ['docker','inspect','-f']:
            output = json.dumps({'cmd':None,'mounts':[{'Type':'volume','Name':'anvil-router-cfg','Destination':'/etc/anvil'}]})
        if argv == ['docker','restart','anvil-router']:
            rc = int(sum(a == argv for a in calls) == 1)
        return subprocess.CompletedProcess(argv,rc,output,'')
    assert serves._install_router_config(str(config),_run=run) == 1
    assert any('cp -p /cfg/config.toml /cfg/config.toml.bak' in a[-1] for a in calls)
    assert any('mv /cfg/config.toml.bak /cfg/config.toml' in a[-1] for a in calls)
    assert sum(a == ['docker','restart','anvil-router'] for a in calls) == 2


@pytest.mark.skipif(__import__('sys').platform == 'win32', reason='executes Linux container shell')
@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('restart_fails', [False, True])
def test_install_shell_handles_absent_config_and_restores_state(tmp_path, existing, restart_fails):
    import subprocess
    config = tmp_path / 'candidate.toml'
    config.write_bytes(b'[router]\n')
    mounted = tmp_path / 'mount'
    mounted.mkdir()
    deployed = mounted / 'config.toml'
    if existing:
        deployed.write_bytes(b'old config\n')
        deployed.chmod(0o640)
    restarts = 0

    def run(argv, **kwargs):
        nonlocal restarts
        if argv[:3] == ['docker', 'run', '--rm']:
            script = argv[-1].replace('/cfg', str(mounted))
            return subprocess.run(['sh', '-c', script], **kwargs)
        if argv == ['docker', 'restart', 'anvil-router']:
            restarts += 1
            return subprocess.CompletedProcess(argv, int(restart_fails and restarts == 1), '', '')
        output = ''
        if argv[:4] == ['docker', 'inspect', '-f', '{{.Config.Image}}']:
            output = 'test-image'
        elif argv[:3] == ['docker', 'inspect', '-f']:
            output = json.dumps({'cmd': None, 'mounts': [{'Type': 'volume', 'Name': 'anvil-router-cfg', 'Destination': '/etc/anvil'}]})
        return subprocess.CompletedProcess(argv, 0, output, '')

    assert serves._install_router_config(str(config), _run=run) == int(restart_fails)
    assert restarts == (2 if restart_fails else 1)
    if restart_fails and not existing:
        assert not deployed.exists()
    else:
        assert deployed.read_bytes() == (b'old config\n' if restart_fails else config.read_bytes())
        if existing:
            assert deployed.stat().st_mode & 0o777 == 0o640
