import json
from pathlib import Path

import pytest

from anvil_serving.native_agents import authority
from anvil_serving.connect import application_roles, manage
from tests.connect.test_application_roles import sync_fixture


def setup_projection(monkeypatch, tmp_path):
    data, human, principal = sync_fixture(monkeypatch)
    monkeypatch.setattr(manage, '_safe_root_ancestors', lambda *_: None)
    monkeypatch.setattr(authority.os, 'chown', lambda *_: None)
    return data, human, principal, tmp_path / 'authority.json'


def test_projection_preview_never_writes_then_atomic_grant_publish(monkeypatch, tmp_path):
    data, human, principal, path = setup_projection(monkeypatch, tmp_path)
    options = dict(resource='dashboard', output=str(path), reader_gid=1001, clock=lambda:1000)
    result = authority.project('/manifest.json', **options)
    assert not result['applied'] and result['principals'] == 1 and not path.exists()
    result = authority.project('/manifest.json', apply=True, **options)
    value = json.loads(path.read_text())
    assert value == {'schema':'anvil-native-agents-authority/v1', 'issuer':data['gateway']['oidc']['issuer'],
                     'resource':'native-agents','source_resource':'dashboard','issued_at':1000,'expires_at':1030,'principals':{principal:4}}
    assert path.stat().st_mode & 0o777 == 0o640
    assert not list(tmp_path.glob('.native-*'))
    assert principal not in json.dumps(result)


def test_suspended_or_pending_roster_never_projects_grant(monkeypatch, tmp_path):
    _, human, _, path = setup_projection(monkeypatch, tmp_path)
    human['disabled'] = True
    authority.project('/manifest.json', resource='dashboard', output=str(path), reader_gid=1001, apply=True, clock=lambda:1000)
    assert json.loads(path.read_text())['principals'] == {}


def test_authority_change_between_reads_does_not_publish(monkeypatch, tmp_path):
    _, _, _, path = setup_projection(monkeypatch, tmp_path)
    calls = [0]
    def roster(*_):
        calls[0] += 1
        return [{'principal':'human:' + 'a'*64,'generation':str(calls[0]),'role':'member'}]
    monkeypatch.setattr(application_roles, 'roster', roster)
    with pytest.raises(manage.ManageError, match='changed'):
        authority.project('/manifest.json', resource='dashboard', output=str(path), reader_gid=1001, apply=True, clock=lambda:1000)
    assert not path.exists()


def test_projection_cannot_overwrite_unrelated_protected_file(monkeypatch, tmp_path):
    _, _, _, path = setup_projection(monkeypatch, tmp_path)
    path.write_text('{"important":"configuration"}')
    monkeypatch.setattr(application_roles, '_protected_file', lambda *_a, **_k:path.read_bytes())
    with pytest.raises(manage.ManageError, match='Existing output'):
        authority.project('/manifest.json', resource='dashboard', output=str(path), reader_gid=1001, apply=True, clock=lambda:1000)
    assert path.read_text() == '{"important":"configuration"}'


def test_native_cli_is_registered_with_preview_and_confirm_gates():
    from anvil_serving.commands.connect import commands
    from anvil_serving.connect.cli import _parser
    nodes = {node.name:node for node in commands.build()[0].children}
    assert nodes['native-authority'].mutation_class == 'mutate'
    options = _parser().parse_args(['native-authority','--manifest','/manifest.json','--resource','dashboard',
                                   '--output','/etc/example/authority','--reader-gid','1001'])
    assert options.confirm is False and options.reader_gid == 1001


def test_projection_capacity_matches_consumer_and_preserves_prior_file(monkeypatch, tmp_path):
    _, _, _, path = setup_projection(monkeypatch, tmp_path)
    roster = [{'principal':'human:' + format(index, '064x'), 'generation':'1', 'role':'member'} for index in range(64)]
    monkeypatch.setattr(application_roles, 'roster', lambda *_:roster)
    options = dict(resource='dashboard', output=str(path), reader_gid=1001, apply=True, clock=lambda:1000)
    assert authority.project('/manifest.json', **options)['principals'] == 64
    before = path.read_bytes()
    monkeypatch.setattr(application_roles, '_protected_file', lambda *_a, **_k:path.read_bytes())
    roster.append({'principal':'human:' + format(64, '064x'), 'generation':'1', 'role':'member'})
    with pytest.raises(manage.ManageError, match='64-principal capacity'):
        authority.project('/manifest.json', **options)
    assert path.read_bytes() == before


def test_standalone_archive_includes_native_authority_closure(tmp_path):
    import importlib.util
    import subprocess
    import sys
    import zipfile
    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location('native_bundle', root / 'connect/packaging/build_bundle.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    output = tmp_path / 'manager'; module.manager_archive(output)
    result = subprocess.run([sys.executable, str(output), 'native-authority', '--help'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and '--reader-gid' in result.stdout
    with zipfile.ZipFile(output) as archive:
        assert {'anvil_serving/native_agents/authority.py','anvil_serving/native_agents/auth.py','anvil_serving/native_agents/types.py'} <= set(archive.namelist())
    result = subprocess.run([sys.executable, '-I', '-c', 'import sys; sys.path.insert(0,sys.argv[1]); from anvil_serving.native_agents.authority import project', str(output)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
