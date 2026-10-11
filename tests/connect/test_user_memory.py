"""Interrupted provisioning retries safely, and shared banks are never adopted."""
import json
import os
import http.client
import http.server
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from anvil_serving import memory_access
from anvil_serving.connect import manage, memory_backend, user_delete, user_memory, users
from tests.router.key_fixtures import tmp_path as tmp_path


def test_creation_retries_protection_and_shared_default(tmp_path, monkeypatch):
    monkeypatch.setattr(manage, "_safe_root_ancestors", lambda *_: None)
    token = tmp_path / "token"; token.write_text("test-token"); token.chmod(0o600)
    data = {"gateway": {"oidc": {"issuer": "https://auth.example.test"}}, "memory": {
        "base_url": "http://127.0.0.1:8888", "auth_file": str(token),
        "access_file": str(tmp_path / "access" / "access.json"), "default_banks": {},
        "shared_banks": {}, "reader_gid": os.getegid()}}
    (tmp_path / "access").mkdir(mode=0o750)
    banks = {"fleet-imported": {"bank_id": "fleet-imported", "name": "shared"}}
    config = {}; calls = []; fail = [True]
    def transport(url, *, data, method, **_):
        target = urlsplit(url).path
        calls.append((target, method))
        if target == "/v1/default/banks":
            result = {"banks": list(banks.values()), "total": len(banks)}
        else:
            bank = target.split("/")[4]
            if method == "PUT":
                banks[bank] = {"bank_id": bank, **json.loads(data)}
                if fail.pop() if fail else False:
                    raise TimeoutError("lost successful create response")
                result = banks[bank]
            elif method == "PATCH":
                config[bank] = json.loads(data)["updates"]; result = {}
            else:
                result = {"config": config[bank]}
        return json.dumps(result).encode()
    monkeypatch.setattr(memory_backend, "request", transport)
    subject = "opaque-subject"
    principal = users._principal(data["gateway"]["oidc"]["issuer"], subject)
    with pytest.raises(manage.ManageError): user_memory.provision(data,"developer",subject,admin=False)
    assert not (tmp_path / "access" / "access.json").exists()
    result = user_memory.provision(data,"developer",subject,admin=False)
    assert result["ready"] and len([call for call in calls if call[1] == "PUT"]) == 1
    assert config[result["bank"]]["memory_defense"] == user_memory.DEFENSE
    assert memory_access.read(data["memory"]["access_file"])["users"][principal]["default_bank"] == result["bank"]
    data["memory"]["default_banks"] = {"administrator": "fleet-imported"}
    data["memory"]["shared_banks"] = {"administrator": {"fleet-imported": ["retain", "recall", "reflect"]}}
    before = len(calls)
    result = user_memory.provision(data,"administrator","admin-subject",admin=True)
    assert result["shared"] and result["default_bank"] == "fleet-imported"
    assert all("fleet-imported" not in target for target, method in calls[before:] if method != "GET")
    old_bank = result["bank"]
    declaration = tmp_path / "deployment.json"
    data = user_delete._withdraw_memory_defaults(data, str(declaration), "administrator")
    result = user_memory.provision(data,"administrator","new-subject",admin=False)
    assert not result["shared"] and result["default_bank"] == result["bank"] != old_bank
    replacement = users._principal(data["gateway"]["oidc"]["issuer"], "new-subject")
    assert set(memory_access.read(data["memory"]["access_file"])["users"][replacement]["banks"]) == {result["bank"]}
    assert old_bank in banks and "fleet-imported" in banks
    # An existing managed-name bank without the protected receipt is a conflict.
    subject = "collision"
    monkeypatch.setattr(user_memory.secrets, "token_hex", lambda _: "d" * 48)
    bank = "user-" + "d" * 48
    banks[bank] = {"bank_id": bank, "name": "somebody else's bank"}
    with pytest.raises(manage.ManageError, match="bank access remains closed"):
        user_memory.provision(data,"collision",subject,admin=False)


@pytest.mark.parametrize("local", [False, True])
def test_permanent_deletion_withdraws_username_grants_before_idp_work(tmp_path, monkeypatch, local):
    from tests.connect.test_user_delete import _phase, _local_phase, _request, _intent
    root = tmp_path / "phases"; root.mkdir(mode=0o700)
    request = _request()
    phase = _local_phase(request, {}) if local else _phase(request)
    path = user_delete._write_phase(root, phase)
    declaration = tmp_path / "deployment.json"
    data = {"memory": {
        "default_banks": {"owner": "fleet-imported", "other": "shared"},
        "shared_banks": {"owner": {"fleet-imported": ["recall"]}, "other": {"shared": ["recall"]}}}}
    declaration.write_text(json.dumps(data)); declaration.chmod(0o600)
    receipt = tmp_path / ".creation.json"; receipt.write_text("preserve")
    monkeypatch.setattr(manage, "_safe_root_ancestors", lambda *_: None)
    def held(current, *_):
        assert current == json.loads(declaration.read_text())
        assert current["memory"]["default_banks"] == {"other": "shared"}
        assert current["memory"]["shared_banks"] == {"other": {"shared": ["recall"]}}
        raise manage.ManageError("IdP unavailable")
    monkeypatch.setattr(user_delete, "_preflight", held)
    def advance(current):
        if local:
            return user_delete._run_local_phase(current, str(declaration), path, phase, None, tmp_path)
        return user_delete._run_phase(current, str(declaration), path, phase, None, tmp_path, intent=_intent(request))
    with pytest.raises(manage.ManageError, match="IdP unavailable"):
        advance(data)
    assert path.exists() and receipt.read_text() == "preserve"
    assert declaration.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(manage, "_write_atomic", lambda *_: pytest.fail("retry must not rewrite revoked defaults"))
    with pytest.raises(manage.ManageError, match="IdP unavailable"):
        advance(json.loads(declaration.read_text()))


def test_deletion_refuses_a_writable_manifest_parent(monkeypatch):
    import stat
    destination = Path("/private/unsafe/deployment.json")
    data = {"memory": {"default_banks": {"owner": "shared"}, "shared_banks": {}}}
    monkeypatch.setattr(Path, "lstat", lambda path: SimpleNamespace(
        st_uid=0, st_mode=stat.S_IFDIR | (0o777 if path == destination.parent else 0o755)))
    monkeypatch.setattr(manage, "_write_atomic", lambda *_: pytest.fail("must not publish through an unsafe directory"))
    with pytest.raises(manage.ManageError, match="directory is unsafe"):
        user_delete._withdraw_memory_defaults(data, str(destination), "owner")
    assert data["memory"]["default_banks"] == {"owner": "shared"}


def test_private_installation_resolves_email_and_cli_previews(tmp_path, monkeypatch):
    from anvil_serving.connect import config
    from anvil_serving.connect.cli import dispatch
    from tests.connect.test_render import manifest
    from tests.connect.test_users import record
    source = tmp_path / "input.json"
    destination = tmp_path / "deployment.json"
    data = config.validate_manifest(manifest())
    desired = {"base_url": "http://127.0.0.1:8888", "auth_file": str(tmp_path / "token"),
        "access_file": str(tmp_path / "access" / "access.json"), "default_banks": {},
        "shared_banks": {}, "reader_gid": os.getegid()}
    declaration = {"memory": desired, "user_defaults": [{"email": "OWNER@example.test",
        "bank": "shared", "operations": ["recall"]}]}
    source.write_text(json.dumps(declaration)); source.chmod(0o600)
    monkeypatch.setattr(users, "_require_root", lambda: None)
    monkeypatch.setattr(config, "read_manifest", lambda _: data)
    monkeypatch.setattr(users, "_read_users", lambda _: (json.dumps({"users": {
        "administrator": record("owner@example.test")}}).encode(), None))
    monkeypatch.setattr(manage, "_safe_root_ancestors", lambda *_: None)
    monkeypatch.setattr(manage, "_deployment_lock", lambda _: nullcontext())
    monkeypatch.setattr(manage, "_require_no_authelia_upgrade", lambda _: None)
    args = ["users", "configure-memory", "--manifest", str(destination), "--input", str(source)]
    preview = dispatch(args)
    assert preview.error is None and preview.data["account_defaults"] == 1
    assert not destination.exists() and not (tmp_path / "access").exists()
    prior_umask = os.umask(0o077)
    try:
        applied = dispatch(args + ["--confirm"])
    finally:
        os.umask(prior_umask)
    assert applied.error is None and applied.data["applied"]
    saved = json.loads(destination.read_text())["memory"]
    assert saved["default_banks"] == {"administrator": "shared"}
    assert saved["shared_banks"] == {"administrator": {"shared": ["recall"]}}
    assert memory_access.read(saved["access_file"])["users"] == {}
    assert (tmp_path / "access").stat().st_mode & 0o777 == 0o750
    assert (tmp_path / "access/access.json").stat().st_mode & 0o777 == 0o640
    (tmp_path / "access/access.json").chmod(0o600)
    rejected = dispatch(args + ["--confirm"])
    assert rejected.error is not None
    assert (tmp_path / "access/access.json").stat().st_mode & 0o777 == 0o600
    (tmp_path / "access/access.json").chmod(0o640)
    declaration["user_defaults"][0]["email"] = "missing@example.test"
    source.write_text(json.dumps(declaration))
    assert dispatch(args).error is not None
    declaration["memory"] = []
    source.write_text(json.dumps(declaration))
    assert dispatch(args).error is not None


def test_suspend_and_delete_close_policy_before_native_failure(tmp_path, monkeypatch):
    principal = "human:" + "a" * 64
    path = tmp_path / "access.json"
    receipt = tmp_path / ".creation.json"; receipt.write_text("retained receipt")
    data = {"gateway": {"oidc": {"issuer": "https://auth.example.test"}},
        "memory": {"access_file": str(path), "reader_gid": os.getegid()}}
    monkeypatch.setattr(users, "_principal", lambda *_: principal)
    def failed_native(*_):
        assert principal not in memory_access.read(str(path))["users"]
        raise manage.ManageError("native failure")
    monkeypatch.setattr(users, "role_identity", failed_native)
    for bridge, payload in ((users._human_admin, {"operation": "human-suspend", "subject": "subject"}),
        (users._human_admin_read, {"operation": "human-delete-prepare", "principal": principal}),
        (users._human_admin_read, {"operation": "human-delete-prepare-absent", "principal": principal})):
        data["gateway"]["state_directory"] = str(tmp_path)
        user_memory._publish(data, {"schema": memory_access.SCHEMA, "users": {principal: {
            "default_bank": "mine", "admin": True, "banks": {"mine": ["recall"]}}}})
        with pytest.raises(manage.ManageError, match="native failure"):
            bridge(data, "unused", payload, None)
        assert receipt.read_text() == "retained receipt"


def test_bank_transport_deadline_covers_dripping_body():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Length", "10000"); self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b"x"); self.wfile.flush(); time.sleep(0.03)
            except (OSError, ValueError):
                pass
        def log_message(self, *_): pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    started = time.monotonic()
    try:
        with pytest.raises((OSError, http.client.HTTPException)):
            memory_backend.request("http://127.0.0.1:" + str(server.server_port) + "/",
                method="GET", data=None, headers={}, timeout=0.15, max_bytes=32768)
        assert time.monotonic() - started < 1
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=1)


def test_personal_key_receipt_recovers_lost_response_and_renews(tmp_path, monkeypatch):
    from anvil_serving.connect import user_memory_key as keys
    from anvil_serving.router.keys import KeyStore
    from anvil_serving.router.connect_keys import provision_memory_key
    monkeypatch.setattr(manage, '_safe_root_ancestors', lambda *_: None)
    subject = 'native-subject'
    issuer = 'https://id.example.test'
    principal = users._principal(issuer, subject)
    generation = [1]
    human = {'id': principal, 'username': 'developer', 'disabled': False, 'generation': 1}
    def native(data, manifest, payload, runner):
        if payload['operation'] == 'human-inspect':
            return {'operation': 'human-inspect', 'found': True, 'human': {**human, 'generation': generation[0]}}
        assert payload['expected_generation'] == generation[0]
        return {'operation': 'human-router-identity', 'principal': principal, 'generation': generation[0], 'epoch': 'f'*64}
    monkeypatch.setattr(users, '_human_admin_read', native)
    monkeypatch.setattr(memory_access, 'resolve', lambda *_: ('personal-bank', ['recall', 'retain']))
    store = KeyStore.initialize(tmp_path / 'router' / 'keys.sqlite3')
    from anvil_serving.router.connect_keys import migrate
    migrate(store)
    store.owner_check = lambda *actor: actor == (principal, str(generation[0]), 'f'*64)
    lost = [True]
    lost_retirement = []
    token_ids = {}
    monkeypatch.setattr(keys.key_container, '_container_id', lambda *_: 'c'*64)
    def invoke(container, payload):
        if payload['action'] == 'memory-key-retire':
            retired = store.revoke(token_ids[payload['secret']])
            if lost_retirement.pop() if lost_retirement else False:
                raise TimeoutError('lost retirement response')
            return {'data': {'key_id': payload['key_id'], 'revoked': retired}}
        actor = (payload['principal'], payload['generation'], payload['epoch'])
        data = provision_memory_key(store, actor, secret=payload['secret'], expected_key_id=payload['expected_key_id'], apply=payload['apply'])
        token_ids[payload['secret']] = data['key_id']
        if lost.pop() if lost else False:
            raise TimeoutError('lost successful pipe response')
        return {'data': data}
    monkeypatch.setattr(keys.key_container, '_invoke', invoke)
    data = {'gateway': {'oidc': {'issuer': issuer}, 'gateway': {}}, 'memory': {'access_file': '/unused',
        'router': {'container': 'router', 'store_path': '/private/keys.sqlite3', 'key_directory': str(tmp_path/'personal')}}}
    preview = keys.provision(data, '/unused', 'developer', subject, apply=False)
    assert preview['would_create'] and not (tmp_path/'personal').exists()
    with pytest.raises(TimeoutError): keys.provision(data, '/unused', 'developer', subject, apply=True)
    path = Path(preview['key_file'])
    pending = json.loads(path.read_text())
    assert pending['key_id'] is None and store.authenticate(pending['secret']).owner[0] == principal
    again = keys.provision(data, '/unused', 'developer', subject, apply=True)
    assert not again['created'] and len(store.list_keys()) == 1
    saved = path.read_bytes()
    assert not keys.provision(data, '/unused', 'developer', subject, apply=True)['created']
    assert path.read_bytes() == saved
    # The account owner invalidates old keys on access-generation changes.
    generation[0] = 2
    renewed = keys.provision(data, '/unused', 'developer', subject, apply=True)
    assert renewed['created'] and renewed['key_id'] != again['key_id']
    receipt = json.loads(path.read_text())
    assert receipt['previous']['retired'] and receipt['previous']['secret'] == pending['secret']
    assert store.authenticate(receipt['secret']).owner[1] == '2'
    assert next(k for k in store.list_keys() if k['key_id'] == again['key_id'])['revoked_at'] is not None
    assert path.stat().st_mode & 0o777 == 0o600

    # A second generation change while creation is pending must still retire
    # that exact token, including a lost successful retirement response.
    generation[0] = 3
    lost.append(True)
    with pytest.raises(TimeoutError): keys.provision(data, '/unused', 'developer', subject, apply=True)
    pending = json.loads(path.read_text())
    assert pending['key_id'] is None
    generation[0] = 4
    # First settle generation two, then lose the pending generation-three retire.
    lost_retirement.extend([True, False])
    with pytest.raises(TimeoutError): keys.provision(data, '/unused', 'developer', subject, apply=True)
    completed = json.loads(path.read_text())
    assert completed['key_id'] and not completed['previous']['retired']
    assert completed['previous']['key_id'] is None
    keys.provision(data, '/unused', 'developer', subject, apply=True)
    assert len(store.list_keys()) == 4
    receipt = json.loads(path.read_text())
    assert receipt['previous']['retired']
    assert store.authenticate(receipt['secret']).owner[1] == '4'
    assert all(k['revoked_at'] is not None for k in store.list_keys() if k['key_id'] != receipt['key_id'])
