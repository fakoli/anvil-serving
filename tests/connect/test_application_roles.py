from __future__ import annotations

import copy
import hashlib
import json
import os

import pytest

from anvil_serving.connect import application_roles as roles
from anvil_serving.connect import config, manage, users
from tests.connect.test_render import isolated_manifest


def binding():
    return {'resource': 'dashboard', 'adapter_path': '/opt/connect-adapters/webui.py',
            'adapter_sha256': 'a' * 64, 'configuration_path': '/etc/anvil-connect/adapters/webui.json',
            'configuration_sha256': 'b' * 64}


def declaration():
    data = isolated_manifest()
    assert data['gateway']['gateway']['resources'][0]['rule']['native_auth'] == 'passthrough'
    data['application_role_bindings'] = [binding()]
    return data


def authority_fixture(monkeypatch):
    data = declaration()
    subject = '33333333-3333-4333-8333-333333333333'
    issuer = data['gateway']['oidc']['issuer']
    principal = users._principal(issuer, subject)
    accounts = {'tester': {'displayname': 'Tester', 'password': '$argon2id$synthetic-hash', 'email': 'tester@example.test', 'disabled': False, 'groups': ['admins', 'anvil-connect-operators']}}
    human = {'id': principal, 'username': 'tester', 'generation': 4, 'disabled': False,
             'resources': ['dashboard'], 'application_roles': {'dashboard': 'member'}}
    identifiers = [{'service': 'openid', 'sector_id': '', 'username': 'tester', 'identifier': subject}]
    monkeypatch.setattr(users, '_read_users', lambda _: (json.dumps({'users': accounts}).encode(), None))
    monkeypatch.setattr(users, '_oidc_identifiers', lambda *_a, **_kw: copy.deepcopy(identifiers))
    monkeypatch.setattr(users, '_human_admin_read', lambda _data, _manifest, p, _runner:
                        {'operation': p['operation'], 'found': human is not None,
                         **({'human': copy.deepcopy(human)} if human is not None else {})})
    return data, accounts, human, identifiers, principal


def test_roster_accepts_native_omitted_username_with_exact_subject_authority(monkeypatch):
    data, accounts, human, identifiers, principal = authority_fixture(monkeypatch)
    # Legacy/browser-created native humans omit the optional username field.
    human.pop('username')
    assert roles.roster(data, '/declaration.json', 'dashboard', None) == [
        {'subject': identifiers[0]['identifier'], 'principal': principal,
         'generation': '4', 'role': 'member'}]
    accounts['tester']['disabled'] = True
    assert roles.roster(data, '/declaration.json', 'dashboard', None)[0]['role'] == 'none'
    human['id'] = 'human:' + 'a' * 64
    with pytest.raises(manage.ManageError):
        roles.roster(data, '/declaration.json', 'dashboard', None)


@pytest.mark.parametrize('mutation', [
    lambda a,h,i: h.update(username='other'),
    lambda a,h,i: h.update(username=None),
    lambda a,h,i: h.update(username=''),
    lambda a,h,i: h.update(id='human:' + 'a' * 64),
    lambda a,h,i: h.update(generation=True),
    lambda a,h,i: i.append(copy.deepcopy(i[0])),
    lambda a,h,i: h.update(application_roles={'dashboard': 'owner'}),
    lambda a,h,i: h.update(application_roles=[]),
    lambda a,h,i: h.update(application_roles=''),
    lambda a,h,i: h.update(application_roles=None),
])
def test_roster_rejects_ambiguous_or_inconsistent_authority(monkeypatch, mutation):
    data, accounts, human, identifiers, _ = authority_fixture(monkeypatch)
    mutation(accounts, human, identifiers)
    with pytest.raises(manage.ManageError):
        roles.roster(data, '/declaration.json', 'dashboard', None)


def test_transport_bounds_output_and_timeout_and_passes_stdin(tmp_path):
    adapter = tmp_path / 'adapter.py'
    adapter.write_text('import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n')
    assert roles.invoke(str(adapter), b'bounded', timeout=5) == b'bounded'
    adapter.write_text('import sys\nsys.stdout.buffer.write(b"x"*1000000)\n')
    with pytest.raises(manage.ManageError, match='bound'):
        roles.invoke(str(adapter), b'bounded', timeout=5)
    adapter.write_text('import time\ntime.sleep(5)\n')
    with pytest.raises(manage.ManageError, match='bound'):
        roles.invoke(str(adapter), b'', timeout=.1)


def sync_fixture(monkeypatch):
    from contextlib import nullcontext
    data, accounts, human, identifiers, principal = authority_fixture(monkeypatch)
    monkeypatch.setattr(users, '_require_root', lambda: None)
    monkeypatch.setattr(roles, '_read_declaration', lambda _: copy.deepcopy(data))
    monkeypatch.setattr(manage, '_deployment_lock', lambda *_a, **_kw: nullcontext())
    monkeypatch.setattr(manage, '_require_no_authelia_upgrade', lambda _: None)
    monkeypatch.setattr(manage, '_verify_owned_tree', lambda _: None)
    monkeypatch.setattr(manage, '_read_regular', lambda *_a: roles._authelia(config.validate_manifest(data)).encode())
    monkeypatch.setattr(manage, '_digest', lambda _: 'pinned')
    monkeypatch.setattr(manage, '_component_lock', lambda: {'authelia': 'pinned'})
    monkeypatch.setattr(roles, 'verify_binding', lambda _: None)
    # Runtime functions normally consume normalized manifest data.
    normalized = config.validate_manifest(data)
    monkeypatch.setattr(roles, '_read_declaration', lambda _: copy.deepcopy(normalized))
    return normalized, human, principal


def adapter_result(payload, **changes):
    request = json.loads(payload)
    value = {'schema': roles.RESULT_SCHEMA, 'resource': request['resource'], 'mode': request['mode'],
             'request_sha256': hashlib.sha256(payload).hexdigest(), 'checked': len(request['accounts']),
             'changed': 0, 'pending_first_login': 0, 'retired': 0, 'sessions_revoked': 0, 'failed': 0}
    value.update(changes)
    return json.dumps(value).encode()


def test_sync_authority_changes_before_mutation_prevent_call(monkeypatch):
    sync_fixture(monkeypatch)
    count = 0
    def snapshot(*_a):
        nonlocal count
        count += 1
        return [{'generation': str(count)}]
    monkeypatch.setattr(roles, 'roster', snapshot)
    with pytest.raises(manage.ManageError) as error:
        roles.sync_roles('/declaration.json', apply=True,
                         adapter_runner=lambda *_a, **_kw: pytest.fail('stale authority executed'))
    assert error.value.may_have_executed is False
    assert error.value.recovery['complete'] is False


def test_sync_authority_change_after_mutation_reports_partial_and_never_replays(monkeypatch):
    data, human, _ = sync_fixture(monkeypatch)
    calls = []
    def adapter(_path, payload, *, timeout):
        calls.append(1)
        human.update(generation=5, disabled=True)
        return adapter_result(payload, changed=1)
    with pytest.raises(manage.ManageError) as error:
        roles.sync_roles('/declaration.json', apply=True, adapter_runner=adapter)
    assert error.value.may_have_executed is True
    assert error.value.recovery['complete'] is False and len(calls) == 1


def test_file_custody_digest_mode_links_and_checkout(tmp_path, monkeypatch):
    # Test the real fd checks without requiring root-owned pytest directories.
    monkeypatch.setattr(manage, '_safe_root_ancestors', lambda _: None)
    monkeypatch.setattr(roles, 'ROOT_UID', os.getuid())
    path = tmp_path / 'adapter.py'
    raw = b'print("synthetic")\n'
    path.write_bytes(raw)
    path.chmod(0o600)
    digest = hashlib.sha256(raw).hexdigest()
    assert roles._protected_file(str(path), maximum=1024, digest=digest, configuration=True) == raw
    with pytest.raises(manage.ManageError):
        roles._protected_file(str(path), maximum=1024, digest='0' * 64)
    path.chmod(0o622)
    with pytest.raises(manage.ManageError):
        roles._protected_file(str(path), maximum=1024)
    path.chmod(0o600)
    hardlink = tmp_path / 'hardlink'
    os.link(path, hardlink)
    with pytest.raises(manage.ManageError):
        roles._protected_file(str(path), maximum=1024)
    hardlink.unlink()
    link = tmp_path / 'symlink'
    link.symlink_to(path)
    with pytest.raises(OSError):
        roles._protected_file(str(link), maximum=1024)
    (tmp_path / '.git').mkdir()
    with pytest.raises(manage.ManageError):
        roles._protected_file(str(path), maximum=1024)


def test_deployment_lock_wait_obeys_reconciliation_deadline(tmp_path, monkeypatch):
    import time
    import fcntl
    monkeypatch.setattr(manage, "fcntl", fcntl)
    root = tmp_path / 'rendered'
    with manage._deployment_lock(root):
        before = time.monotonic()
        with pytest.raises(manage.ManageError, match='deadline'):
            with manage._deployment_lock(root, deadline=before + .1):
                pytest.fail('acquired held lock')
        assert time.monotonic() - before < 1


def test_worker_role_failure_blocks_destructive_deletion_but_attempts_operator_projection(monkeypatch):
    from anvil_serving.connect import cli, user_delete
    monkeypatch.setattr(manage, 'supported_platform', lambda: True)
    calls = []
    def fail(*_a, **_kw):
        calls.append('roles')
        raise manage.ManageError('synthetic failure')
    monkeypatch.setattr(roles, 'sync_roles', fail)
    monkeypatch.setattr(users, 'sync_operators', lambda *_a, **_kw: calls.append('operators') or {})
    monkeypatch.setattr(user_delete, 'process_pending', lambda *_a, **_kw: calls.append('deletion') or {})
    result = cli.dispatch(['users', 'process-deletions', '--confirm'])
    assert calls == ['roles', 'operators']
    assert result.error is not None
    assert result.data == {'application_roles': 'failed', 'operator_group': 'completed', 'deletion': 'blocked'}


def test_subject_export_is_canonicalized_before_principal_derivation(monkeypatch):
    data, accounts, human, identifiers, _ = authority_fixture(monkeypatch)
    identifiers[0]['identifier'] = 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA'
    human['id'] = users._principal(data['gateway']['oidc']['issuer'], identifiers[0]['identifier'].lower())
    result = roles.roster(data, '/declaration.json', 'dashboard', None)
    assert result[0]['principal'] == human['id']
    assert result[0]['subject'] == identifiers[0]['identifier'].lower()


def test_confirmed_absent_native_authority_never_inherits_global_admin_group(monkeypatch):
    data, _, _, _, _ = authority_fixture(monkeypatch)
    monkeypatch.setattr(users, '_human_admin_read', lambda _d, _m, p, _r: {'operation': p['operation'], 'found': False})
    entry = roles.roster(data, '/declaration.json', 'dashboard', None)[0]
    assert entry['generation'] is None and entry['role'] == 'none'
