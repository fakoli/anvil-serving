"""Account isolation, rollback, secret boundaries and the public CLI contract."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

import pytest

from anvil_serving.connect import manage, users
from anvil_serving.connect.cli import dispatch
from anvil_serving.operator_output import UsageError

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="local Linux account administration")
_HASH = "$argon2id$v=19$m=65536,t=3,p=4$" + "A" * 22 + "$" + "B" * 43
_PASSWORD = "generated" + "X" * 31


def record(email="owner@example.test"):
    return {"displayname": "Existing owner", "password": _HASH, "email": email, "groups": ["owners"]}


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "_require_root", lambda: None)
    monkeypatch.setattr(manage, "_safe_root_ancestors", lambda *args: None)
    monkeypatch.setattr(manage, "_verify_owned_tree", lambda *_: None)
    root = tmp_path / "rendered"
    (root / "authelia").mkdir(parents=True)
    (root / "systemd").mkdir()
    unit_root = tmp_path / "units"
    unit_root.mkdir()
    private = tmp_path / "auth"
    private.mkdir(mode=0o700)
    db = private / "users.yml"
    db.write_text(json.dumps({"users": {"owner": record()}}))
    db.chmod(0o600)
    binary = tmp_path / "authelia"
    binary.write_bytes(b"synthetic executable")
    binary.chmod(0o755)
    source = f"[Service]\nExecStart={binary} --config {root}/authelia/configuration.yml\n".encode()
    (root / "systemd" / users._UNIT).write_bytes(source)
    (unit_root / users._UNIT).write_bytes(source)
    auth = {"users_file": str(db), "state_directory": str(private), "host": "auth.example.test"}
    data = {"authelia": auth, "config_root": str(root), "components": {"authelia": str(binary)},
            "service_identities": {"idp": {"uid": os.geteuid(), "gid": os.getegid()}}, "service_limits": {}}
    monkeypatch.setattr(users, "read_manifest", lambda _: data)
    monkeypatch.setattr(users, "_authelia", lambda _: "test configuration")
    (root / "authelia/configuration.yml").write_text("test configuration")
    monkeypatch.setattr(manage, "_component_lock", lambda: {"authelia": hashlib.sha256(binary.read_bytes()).hexdigest()})
    monkeypatch.setattr(manage, "_role_service_identity", lambda *args: None)
    state = {"active": True, "calls": [], "fail_start": False, "fail_factor": False}

    def runner(argv, timeout, identity):
        assert timeout <= 30
        assert _PASSWORD not in " ".join(argv)
        state["calls"].append(argv)
        if argv[:3] == (manage._SYSTEMCTL, "show", "--property=LoadState,FragmentPath,DropInPaths"):
            return manage.RunResult(0, f"LoadState=loaded\nFragmentPath={unit_root / users._UNIT}\nDropInPaths=\n".encode())
        if argv[:3] == (manage._SYSTEMCTL, "show", "--property=ActiveState,UnitFileState"):
            return manage.RunResult(0, f"ActiveState={'active' if state['active'] else 'inactive'}\nUnitFileState=enabled\n".encode())
        if argv[:2] == (manage._SYSTEMCTL, "stop"):
            state["active"] = False
        if argv[:2] == (manage._SYSTEMCTL, "start"):
            if state["fail_start"]:
                state["fail_start"] = False
                return manage.RunResult(1, b"sensitive-startup-output")
            state["active"] = True
        interrupt = state.get("interrupt")
        if interrupt and (argv[:2] == (manage._SYSTEMCTL, interrupt) or (interrupt == "factor" and argv[1:3] == ("storage", "user"))):
            state.pop("interrupt")
            raise KeyboardInterrupt
        if argv[1:3] == ("crypto", "hash"):
            return manage.RunResult(0, f"Random Password: {_PASSWORD}\nDigest: {_HASH}\n".encode())
        if argv[1:3] == ("storage", "user") and state["fail_factor"]:
            return manage.RunResult(1, b"sensitive-factor-output")
        return manage.RunResult(0)

    def run(operation, username="dev", **kwargs):
        return users.operate(str(tmp_path / "deployment.json"), operation, username,
                             runner=runner, unit_root=unit_root, **kwargs)
    return run, db, state, private


def test_create_reset_preserve_other_accounts_and_secrets(environment):
    run, db, state, _ = environment
    original = json.loads(db.read_text())["users"]["owner"]
    preview = run("create", email="dev@example.test")
    assert not preview["applied"] and not Path(preview["handoff_file"]).exists()
    assert not state["calls"]
    created = run("create", email="dev@example.test", apply=True)
    handoff = Path(created["handoff_file"])
    assert _PASSWORD in handoff.read_text()
    assert _PASSWORD not in json.dumps(created) and _PASSWORD not in db.read_text()
    assert stat.S_IMODE(handoff.stat().st_mode) == 0o600
    assert stat.S_IMODE(handoff.parent.stat().st_mode) == 0o700
    accounts = json.loads(db.read_text())["users"]
    assert accounts["owner"] == original and accounts["dev"]["groups"] == ["members"]
    assert state["active"]
    reset = run("reset-password", apply=True)
    assert reset["handoff_file"] != created["handoff_file"]
    assert json.loads(db.read_text())["users"]["owner"] == original
    before = db.read_bytes()
    with pytest.raises(UsageError):
        run("create", email="dev@example.test", apply=True)
    assert db.read_bytes() == before


def test_admin_role_is_explicit_and_resets_preserve_groups(environment):
    run, db, _, _ = environment
    result = run("create", email="dev@example.test", role="admin", apply=True)
    assert result["role"] == "admin" and not result["grants_changed"]
    assert json.loads(db.read_text())["users"]["dev"]["groups"] == ["admins"]
    run("reset-password", apply=True)
    assert json.loads(db.read_text())["users"]["dev"]["groups"] == ["admins"]
    with pytest.raises(UsageError):
        run("reset-password", role="member", apply=True)


def test_factor_reset_is_explicit_native_and_partial_failures_restart(environment):
    run, db, state, _ = environment
    before = db.read_bytes()
    result = run("reset-mfa", "owner", apply=True)
    calls = [c for c in state["calls"] if c[1:3] == ("storage", "user")]
    assert len(calls) == 2
    assert calls[0][3:7] == ("webauthn", "delete", "owner", "--all")
    assert calls[1][3:6] == ("totp", "delete", "owner")
    assert db.read_bytes() == before and "handoff_file" not in result
    assert not result["existing_connect_sessions_revoked"]
    state["fail_factor"] = True
    with pytest.raises(manage.ManageError) as failure:
        run("reset-mfa", "owner", apply=True)
    assert failure.value.may_have_executed and state["active"]


def test_restart_failure_rolls_back_and_inactive_service_stays_inactive(environment):
    run, db, state, _ = environment
    before = db.read_bytes()
    state["fail_start"] = True
    with pytest.raises(manage.ManageError) as failure:
        run("create", email="dev@example.test", apply=True)
    assert failure.value.may_have_executed and db.read_bytes() == before and state["active"]
    assert not list((db.parent.parent / "handoffs").glob("*.txt"))
    state["active"] = False
    state["calls"].clear()
    run("create", email="dev@example.test", apply=True)
    assert not state["active"]
    assert not any(c[:2] == (manage._SYSTEMCTL, "start") for c in state["calls"])


@pytest.mark.parametrize("stage", ["stop", "handoff", "replace", "factor", "start"])
def test_interruption_recovers_service_and_removes_rolled_back_handoff(environment, monkeypatch, stage):
    run, db, state, _ = environment
    before = db.read_bytes()
    if stage in {"handoff", "replace"}:
        original = users._replace_users
        def interrupted(*args):
            monkeypatch.setattr(users, "_replace_users", original)
            if stage == "replace":
                original(*args)
            raise KeyboardInterrupt
        monkeypatch.setattr(users, "_replace_users", interrupted)
    else:
        state["interrupt"] = stage
    with pytest.raises(manage.ManageError) as failure:
        if stage == "factor":
            run("reset-mfa", "owner", apply=True)
        else:
            run("create", email="dev@example.test", apply=True)
    assert failure.value.may_have_executed and state["active"]
    assert db.read_bytes() == before
    assert not list((db.parent.parent / "handoffs").glob("*.txt"))
    if stage != "stop":
        backup = failure.value.recovery["backup"]
        assert hashlib.sha256(Path(backup["file"]).read_bytes()).hexdigest() == backup["sha256"]


def test_symlink_duplicate_email_and_invalid_operands_do_not_mutate(environment, tmp_path):
    run, db, state, _ = environment
    for op, name, options in (
        ("create", "dev", {"email": "owner@example.test"}),
        ("create", "../owner", {"email": "dev@example.test"}),
        ("create", "dev", {"email": "a@example.test\nsecond@example.test"}),
        ("reset-password", "missing", {}),
    ):
        with pytest.raises(UsageError):
            run(op, name, apply=True, **options)
    original = db.read_bytes()
    db.rename(db.with_suffix(".original"))
    db.symlink_to(db.with_suffix(".original"))
    with pytest.raises(manage.ManageError):
        run("reset-password", "owner", apply=True)
    assert db.read_bytes() == original and not state["calls"]


def test_simple_yaml_adoption_and_ambiguous_yaml_rejected():
    raw = f'''users:
  owner:
    displayname: 'An existing owner'
    password: "{_HASH}"
    email: owner@example.test
    groups:
      - owners
'''.encode()
    assert users._database(raw)["users"]["owner"] == {**record(), "displayname": "An existing owner"}
    for bad in (raw + b"users:\n", raw.replace(b"  owner:", b"  owner: &alias"),
                raw.replace(b"    groups:", b"    email: second@example.test\n    groups:"),
                b'{"users":{},"users":{}}', b'{"users":[]}'):
        with pytest.raises((UsageError, ValueError)):
            users._database(bad)


def notice(email, when):
    return (f"Date: {when.strftime('%Y-%m-%d %H:%M:%S %z')} UTC\nRecipient: <{email}>\nSubject: Confirm your identity\n"
            "A ONE-TIME CODE HAS BEEN GENERATED TO COMPLETE A REQUESTED ACTION\n"
            "----------------------------------------\n\nABCDEFGH\n\n----------------------------------------\n").encode()


def test_code_is_recent_exact_recipient_and_never_returned(environment):
    run, _, state, private = environment
    now = datetime.now(timezone.utc)
    path = private / "notifications.txt"
    path.write_bytes(notice("owner@example.test", now))
    path.chmod(0o600)
    result = run("code", "owner", apply=True)
    assert Path(result["handoff_file"]).read_text().strip() == "ABCDEFGH"
    assert "ABCDEFGH" not in json.dumps(result) and not state["calls"]
    for email, when in (("other@example.test", now), ("owner@example.test", now - timedelta(minutes=6))):
        with pytest.raises(UsageError):
            users._notification(notice(email, when), "owner@example.test", now)


def test_cli_default_manifest_and_conditional_apply(monkeypatch):
    calls = []
    monkeypatch.setattr(users, "operate", lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    for flags, applied in (([], False), (["--confirm"], True), (["--confirm", "--dry-run"], False)):
        result = dispatch(["users", "create", "dev", "--email", "dev@example.test", *flags])
        assert result.error is None
        assert calls[-1][0] == (users.DEFAULT_MANIFEST, "create", "dev")
        assert calls[-1][1]["apply"] == applied
    count = len(calls)
    assert dispatch(["users", "reset-password", "dev", "--password", "must-not-echo"]).error
    assert len(calls) == count


def test_product_cli_forwards_users_operands_and_confirmation(monkeypatch, capsys):
    from anvil_serving import cli
    calls = []
    monkeypatch.setattr(users, "operate", lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    assert cli.main(["connect", "users", "create", "dev", "--email", "dev@example.test", "--role", "admin", "--grant", "dash:member", "--grant", "pi:admin", "--confirm", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    assert calls == [((users.DEFAULT_MANIFEST, "create", "dev"), {
        "email": "dev@example.test", "role": "admin", "grants": ["dash:member", "pi:admin"], "output": None, "apply": True})]


def test_schedule_cli_is_explicit_and_rejects_account_operands(monkeypatch):
    from anvil_serving.connect import user_schedule
    from anvil_serving.connect.cli import dispatch
    calls = []
    monkeypatch.setattr(user_schedule, "schedule", lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    assert dispatch(["users", "schedule"]).error is None
    assert dispatch(["users", "schedule", "--confirm"]).error is None
    assert [kwargs["apply"] for _, kwargs in calls] == [False, True]
    assert dispatch(["users", "schedule", "dev", "--confirm"]).error is not None
    assert len(calls) == 2


def test_backup_retention_and_verified_restore_without_activation(environment, monkeypatch):
    from anvil_serving.connect import user_backup
    run, db, state, private = environment
    key = private / "storage-key"
    key.write_bytes(b"synthetic-backup-key")
    key.chmod(0o600)
    users.read_manifest("unused")["authelia"]["storage_encryption_key_file"] = str(key)
    database = private / "authelia.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE example (value TEXT)")
        connection.execute("INSERT INTO example VALUES ('synthetic-personal-data')")
    database.chmod(0o600)
    for _ in range(8):
        result = run("backup", None, apply=True)
    archive = Path(result["backup"]["file"])
    assert result["backup"]["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert len(list(archive.parent.glob("auth-*.zip"))) == 7
    members = user_backup.read_snapshot(archive)
    assert members["users.yml"] == db.read_bytes()
    assert members["storage-key"] == key.read_bytes()
    assert result["backup"]["classification"] == "restricted-authentication" and not result["backup"]["encrypted"]
    recovered = private / "recovery"
    checksum = result["backup"]["sha256"]
    preview = user_backup.restore(str(archive), str(recovered), sha256=checksum)
    assert not preview["applied"] and not recovered.exists()
    restored = user_backup.restore(str(archive), str(recovered), sha256=checksum, apply=True)
    assert restored["applied"] and not restored["activated"]
    with sqlite3.connect(recovered / "authelia.sqlite3") as connection:
        assert connection.execute("SELECT value FROM example").fetchone() == ("synthetic-personal-data",)
    with pytest.raises(UsageError):
        user_backup.restore(str(archive), str(recovered), sha256=checksum, apply=True)
    assert state["active"]

    # Even a matching archive name is not sufficient authority to prune it.
    foreign = archive.parent / "auth-20000101T000000000000Z-000000000000.zip"
    foreign.write_bytes(b"not our archive")
    foreign.chmod(0o600)
    assert run("backup", None, apply=True)["backup"]["unrecognized_archives_preserved"] == 1
    assert foreign.exists()
    # Recomputed member checksums cannot disguise an invalid SQLite database.
    with zipfile.ZipFile(archive) as source:
        contents = {name: source.read(name) for name in source.namelist()}
    contents["authelia.sqlite3"] = b"invalid database"
    record = json.loads(contents["record.json"])
    record["files"]["authelia.sqlite3"] = hashlib.sha256(contents["authelia.sqlite3"]).hexdigest()
    contents["record.json"] = json.dumps(record).encode()
    with zipfile.ZipFile(foreign, "w") as target:
        for name, raw in contents.items():
            target.writestr(name, raw)
    with pytest.raises(UsageError):
        user_backup.restore(str(foreign), str(private / "invalid-recovery"), sha256=hashlib.sha256(foreign.read_bytes()).hexdigest())
    assert not (private / "invalid-recovery").exists()
    with pytest.raises(UsageError):
        user_backup.restore(str(archive), str(private / "wrong-digest"), sha256="0" * 64, apply=True)
    assert not (private / "wrong-digest").exists()
    original = users._exclusive
    def interrupt_extract(*args, **kwargs):
        monkeypatch.setattr(users, "_exclusive", original)
        raise KeyboardInterrupt
    monkeypatch.setattr(users, "_exclusive", interrupt_extract)
    with pytest.raises(manage.ManageError) as failure:
        user_backup.restore(str(archive), str(private / "interrupted-recovery"), sha256=checksum, apply=True)
    assert failure.value.may_have_executed
    assert failure.value.recovery["destination"] == str(private / "interrupted-recovery")


@pytest.mark.parametrize("fail_sync", [False, True])
def test_backup_directory_is_durable_before_account_replacement(environment, monkeypatch, fail_sync):
    run, db, state, _ = environment
    backup = db.parent.parent / "backups"
    original = db.read_bytes()
    synced = set()
    real_sync, real_replace = os.fsync, users._replace_users

    def sync(fd):
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            if fail_sync and info.st_ino == backup.stat().st_ino:
                raise OSError("synthetic backup directory sync failure")
            synced.add((info.st_dev, info.st_ino))
        real_sync(fd)

    def replace(*args):
        for directory in (backup, backup.parent):
            info = directory.stat()
            assert (info.st_dev, info.st_ino) in synced
        real_replace(*args)

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(users, "_replace_users", replace)
    if fail_sync:
        with pytest.raises(manage.ManageError):
            run("create", email="dev@example.test", apply=True)
        assert db.read_bytes() == original
    else:
        assert run("create", email="dev@example.test", apply=True)["applied"]
    assert state["active"]


def test_backup_leaves_wal_source_bytes_and_metadata_unchanged(environment):
    run, _, state, private = environment
    key = private / "storage-key"
    key.write_bytes(b"synthetic-key")
    key.chmod(0o600)
    users.read_manifest("unused")["authelia"]["storage_encryption_key_file"] = str(key)
    database = private / "authelia.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE example (value TEXT)")
        connection.execute("INSERT INTO example VALUES ('committed-in-wal')")
        connection.commit()
        def source_state():
            return {p.name: (p.read_bytes(), p.stat().st_mode, p.stat().st_uid, p.stat().st_gid) for p in private.iterdir()}
        before = source_state()
        result = run("backup", None, apply=True)
        assert source_state() == before and state["active"]
        from anvil_serving.connect import user_backup
        restored = user_backup.restore(result["backup"]["file"], str(private / "recovery"), sha256=result["backup"]["sha256"], apply=True)
        with sqlite3.connect(Path(restored["destination"]) / "authelia.sqlite3") as recovered:
            assert recovered.execute("SELECT value FROM example").fetchone() == ("committed-in-wal",)
    finally:
        connection.close()


def test_partial_backup_receipt_survives_cli_error(monkeypatch, capsys):
    from anvil_serving import cli
    backup = {"file": "/private/backup.zip", "sha256": "a" * 64}
    def fail(*args, **kwargs):
        raise users._partial("synthetic failure", {"backup": backup})
    monkeypatch.setattr(users, "operate", fail)
    assert cli.main(["connect", "users", "reset-mfa", "dev", "--confirm", "--json"]) != 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"]["backup"] == backup


def test_real_authelia_accepts_created_and_reset_passwords_and_factor_reset(environment, monkeypatch):
    """Opt-in real provider smoke, isolated files/loopback; no live systemd calls."""
    binary = os.environ.get("ANVIL_CONNECT_TEST_AUTHELIA")
    if not binary:
        pytest.skip("set ANVIL_CONNECT_TEST_AUTHELIA to the pinned executable")
    run, db, state, private = environment
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    data = users.read_manifest("unused")
    data["components"]["authelia"] = binary
    monkeypatch.setattr(manage, "_component_lock", lambda: {"authelia": hashlib.sha256(Path(binary).read_bytes()).hexdigest()})
    root = Path(data["config_root"])
    key = private / "storage-key"
    key.write_text("synthetic-storage-secret-for-isolated-test")
    key.chmod(0o600)
    data["authelia"]["storage_encryption_key_file"] = str(key)
    config = json.dumps({
        "server": {"address": f"tcp://127.0.0.1:{port}"},
        "authentication_backend": {"file": {"path": str(db)}},
        "access_control": {"default_policy": "deny", "rules": [{"domain": "auth.example.test", "policy": "one_factor"}]},
        "session": {"secret": "synthetic-session-secret-for-isolated-test", "cookies": [{"domain": "auth.example.test", "authelia_url": "https://auth.example.test"}]},
        "identity_validation": {"reset_password": {"jwt_secret": "synthetic-reset-secret-for-isolated-test"}},
        "storage": {"encryption_key": "synthetic-storage-secret-for-isolated-test", "local": {"path": str(private / "authelia.sqlite3")}},
        "notifier": {"filesystem": {"filename": str(private / "notifications.txt")}},
    })
    monkeypatch.setattr(users, "_authelia", lambda _: config)
    (root / "authelia/configuration.yml").write_text(config)
    unit_root = private.parent / "units"
    source = f"[Service]\nExecStart={binary} --config {root}/authelia/configuration.yml\n".encode()
    (root / "systemd" / users._UNIT).write_bytes(source)
    (unit_root / users._UNIT).write_bytes(source)
    original_run = manage._run

    def provider_run(runner, argv, timeout, identity=None):
        if argv[0] == binary:
            return original_run(None, argv, timeout)
        return original_run(runner, argv, timeout, identity)

    monkeypatch.setattr(manage, "_run", provider_run)
    created = run("create", email="dev@example.test", apply=True)
    first_password = Path(created["handoff_file"]).read_text().split("Password: ", 1)[1].splitlines()[0]

    def authenticate(password):
        payload = json.dumps({"username": "dev", "password": password, "keepMeLoggedIn": False,
                              "targetURL": "https://auth.example.test/", "requestMethod": "GET"}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/firstfactor", data=payload,
                                         headers={"Content-Type": "application/json", "Host": "auth.example.test"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    def check_passwords(allowed, denied=None):
        process = subprocess.Popen([binary, "--config", str(root / "authelia/configuration.yml")],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                assert process.poll() is None, "isolated Authelia startup failed"
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.2):
                        break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.05)
            if allowed is not None:
                assert authenticate(allowed) == 200
            if denied:
                assert authenticate(denied) == 401
        finally:
            process.terminate()
            process.wait(timeout=10)

    check_passwords(first_password)
    reset = run("reset-password", apply=True)
    second_password = Path(reset["handoff_file"]).read_text().split("Password: ", 1)[1].splitlines()[0]
    assert second_password != first_password
    check_passwords(second_password, first_password)
    # Native generation without --force succeeds only when the factor is absent.
    # Keep the generated TOTP secret out of assertion diagnostics and logs.
    def generate_totp():
        completed = subprocess.run([binary, "storage", "user", "totp", "generate", "dev", "--config", str(root / "authelia/configuration.yml")],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        assert completed.returncode == 0
    generate_totp()
    run("reset-mfa", "dev", apply=True)
    generate_totp()
    run("reset-mfa", "dev", apply=True)
    # Repeating the reset with missing factors is idempotent.
    run("reset-mfa", "dev", apply=True)
    check_passwords(second_password)
    # Exercise suspension, explicit access restoration and deletion against the
    # pinned provider. Only the gateway authority is replaced by a local stub;
    # its real generation fences are covered by the native session/admin tests.
    subject = "00000000-0000-4000-8000-000000000001"
    added = subprocess.run([binary, "storage", "user", "identifiers", "add", "dev", "--identifier", subject,
                            "--service", "openid", "--sector", "", "--config", str(root / "authelia/configuration.yml")],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    assert added.returncode == 0
    data["gateway"] = {"oidc": {"issuer": "https://auth.example.test"}, "gateway": {"resources": [
        {"rule": {"id": "pi", "access": "browser"}},
    ]}}
    requests = []
    monkeypatch.setattr(users, "_human_admin", lambda data, manifest, payload, runner: requests.append(payload) or {"applied": True})
    generate_totp()
    assert run("suspend", "dev", apply=True)["account_disabled"]
    check_passwords(None, second_password)
    retained = subprocess.run([binary, "storage", "user", "totp", "generate", "dev", "--config", str(root / "authelia/configuration.yml")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    assert retained.returncode != 0
    run("access", "dev", grants=["pi:member"], apply=True)
    check_passwords(second_password)
    assert run("delete", "dev", apply=True)["account_deleted"]
    check_passwords(None, second_password)
    assert [request["operation"] for request in requests] == ["human-suspend", "human-set", "human-suspend"]
    generate_totp()  # Deletion removed the former factor.
    with pytest.raises(UsageError, match="retained OpenID identifier"):
        run("create", email="new@example.test", apply=True)
