"""Forward-only native account deletion worker boundaries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import uuid

import pytest

from anvil_serving.connect import manage, user_delete, users
from anvil_serving.operator_output import UsageError

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="local Linux account administration")
_HASH = "$argon2id$v=19$m=65536,t=3,p=4$" + "A" * 22 + "$" + "B" * 43
_SUBJECT = "11111111-1111-4111-8111-111111111111"
_GO_ABSENT_INSPECTION_WIRE = (
    b'{"operation":"human-inspect","epoch":"","secret":"","key_id":"","principal":"",'
    b'"grants":[],"invitation":"","installation":"","role":"","resources":[],"generation":0,'
    b'"fingerprint":"","found":false,"status":{"id":"","status":"","fingerprint":"",'
    b'"epoch":"","generation":0,"resources":[]}}'
)


@pytest.fixture(autouse=True)
def _deny_real_subprocesses(monkeypatch):
    """Every lifecycle subprocess in this module must use an explicit fake."""
    monkeypatch.setattr(
        manage, "_bounded_run",
        lambda *_args, **_kwargs: pytest.fail("unexpected real subprocess from test"),
    )


def _principal() -> str:
    return "human:" + "a" * 64


def _request() -> str:
    return str(uuid.uuid4())


def _intent(request_id: str, *, username: str = "owner", generation: int = 2) -> dict:
    return {"request_id": request_id, "principal": _principal(), "username": username,
            "generation": generation, "epoch": "b" * 64, "digest": "c" * 64,
            "complete": False, "completed_at": "0001-01-01T00:00:00Z"}


def _phase(request_id: str, *, idp_delete: bool = True, original_active: bool = True) -> dict:
    return {"schema": user_delete._PHASE_SCHEMA, "request_id": request_id, "principal": _principal(),
            "username": "owner", "subject": _SUBJECT if idp_delete else "", "generation": 2,
            "original_active": original_active, "idp_delete": idp_delete}


def _local_phase(request_id: str, entry: dict, *, original_active: bool = False) -> dict:
    return {"schema": user_delete._LOCAL_PHASE_SCHEMA, "request_id": request_id, "username": "owner",
            "entry_digest": user_delete._entry_digest(entry), "original_active": original_active}


def _native_inspection(*, found: bool, human: dict | None = None) -> dict:
    """Exact JSON shape from json.Marshal(admin.Response) for inspection."""
    value = {
        "operation": "human-inspect", "epoch": "", "secret": "", "key_id": "", "principal": "",
        "grants": [], "invitation": "", "installation": "", "role": "", "resources": [],
        "generation": 0, "fingerprint": "", "found": found,
        "status": {"id": "", "status": "", "fingerprint": "", "epoch": "", "generation": 0, "resources": []},
    }
    if human is not None:
        value["human"] = human
    return value


def assert_locked(state: dict) -> None:
    assert state["value"]


def test_phase_precedes_idp_user_replacement_and_retry_survives_completed_purge(tmp_path, monkeypatch):
    phase_root = tmp_path / "phases"
    phase_root.mkdir(mode=0o700)
    request_id = _request()
    phase = _phase(request_id)
    path = user_delete._write_phase(phase_root, phase)
    users_file = tmp_path / "users.yml"
    raw = json.dumps({"users": {"owner": {"displayname": "Owner", "password": _HASH,
                                             "email": "owner@example.test", "groups": []}}}).encode()
    users_file.write_bytes(raw)
    users_file.chmod(0o600)
    data = {"config_root": str(tmp_path / "rendered"), "components": {"authelia": str(tmp_path / "authelia")},
            "authelia": {"users_file": str(users_file), "state_directory": str(tmp_path / "idp")}}
    (tmp_path / "rendered" / "systemd").mkdir(parents=True)
    source = b"unit"
    (tmp_path / "rendered" / "systemd" / users._UNIT).write_bytes(source)
    state = {"active": True, "replaced": False, "purges": []}
    monkeypatch.setattr(user_delete, "_preflight", lambda *_: (tmp_path / "config", state["active"]))
    monkeypatch.setattr(manage, "_read_unit", lambda *_: source)
    monkeypatch.setattr(manage, "_unit_exec_path", lambda *_: Path(data["components"]["authelia"]))
    monkeypatch.setattr(manage, "_verify_unit", lambda *_: None)
    monkeypatch.setattr(manage, "_unit_metadata", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manage, "_unit_state", lambda *_: (state["active"], "enabled"))

    def action(_runner, argv, *_args):
        if argv[1] == "stop":
            state["active"] = False
        elif argv[1] == "start":
            state["active"] = True

    monkeypatch.setattr(manage, "_action", action)
    monkeypatch.setattr(users, "_read_users", lambda *_: (raw, users_file.stat()))
    monkeypatch.setattr(manage, "_safe_authelia_users_file", lambda *_args, **_kwargs: users_file)
    monkeypatch.setattr(user_delete, "role_identity", lambda *_: (1001, 1001))
    monkeypatch.setattr(__import__("anvil_serving.connect.user_backup", fromlist=["snapshot"]), "snapshot", lambda *_args, **_kwargs: {"ok": True})

    def replace(_path, _raw, _info):
        assert path.exists(), "phase must be durable before IdP data mutation"
        assert state["purges"] == [True], "validate-only purge must precede user-file replacement"
        state["replaced"] = True

    monkeypatch.setattr(users, "_replace_users", replace)

    def purge(_database, _username, _subject, *, validate_only, uid, gid):
        state["purges"].append(validate_only)
        return {"schema_version": 29, "validated_only": validate_only, "applied": not validate_only,
                "username_records": 0, "opaque_identifiers": 1 if validate_only else 0,
                "oauth_sessions": 0, "consent_sessions": 0, "consent_preconfigurations": 0,
                "expected_subject_found": True if validate_only else False}

    monkeypatch.setattr(user_delete, "_purge_as_idp", purge)
    monkeypatch.setattr(user_delete, "_human_response", lambda *_args: {"operation": "human-delete-finalize"})

    result = user_delete._run_phase(data, "manifest", path, phase, None, tmp_path, intent=_intent(request_id))

    assert result["finalized"] and state["replaced"] and state["purges"] == [True, False] and state["active"]
    assert not path.exists()


def test_mapped_subject_refuses_username_or_principal_mismatch(monkeypatch):
    issuer = "https://auth.example.test"
    principal = users._principal(issuer, _SUBJECT)
    monkeypatch.setattr(users, "_oidc_identifiers", lambda *_args, **_kwargs: [
        {"service": "openid", "sector_id": "", "username": "other", "identifier": _SUBJECT},
    ])
    with pytest.raises(UsageError):
        user_delete._mapped_subject({"gateway": {"oidc": {"issuer": issuer}}}, Path("/unused"), principal, "owner", None)


def test_native_read_request_omits_legacy_issuer(tmp_path, monkeypatch):
    state = tmp_path / "gateway"
    state.mkdir(mode=0o700)
    data = {"gateway": {"state_directory": str(state), "oidc": {"issuer": "https://auth.example.test"}},
            "service_identities": {"gateway": {"uid": os.geteuid(), "gid": os.getegid()}}}
    monkeypatch.setattr(manage, "_safe_private_runtime_directory", lambda *_: None)
    monkeypatch.setattr(users, "role_identity", lambda *_: (os.geteuid(), os.getegid()))

    def admin(_manifest, *, request_path, output_path, **_kwargs):
        request = json.loads(Path(request_path).read_text())
        assert request == {"operation": "human-deletions"}
        Path(output_path).write_text('{"operation":"human-deletions"}')
        Path(output_path).chmod(0o600)

    monkeypatch.setattr(manage, "admin", admin)
    assert users._human_admin_read(data, "manifest", {"operation": "human-deletions"}, None) == {"operation": "human-deletions"}


@pytest.mark.parametrize("incarnation", (None, "_" * 42 + "8"))
def test_delete_allows_previously_disabled_native_human(tmp_path, monkeypatch, incarnation):
    monkeypatch.setattr(users, "_require_root", lambda: None)
    monkeypatch.setattr(user_delete, "_path", lambda _: None)
    root = tmp_path / "current"
    root.mkdir(mode=0o700)
    phase_root = tmp_path / "phases"
    phase_root.mkdir(mode=0o700)
    data = {"config_root": str(root), "gateway": {"oidc": {"issuer": "https://auth.example.test"}}}
    raw = json.dumps({"users": {"owner": {"displayname": "Owner", "password": _HASH,
                                             "email": "owner@example.test", "groups": [], "disabled": True}}}).encode()
    monkeypatch.setattr(user_delete, "read_manifest", lambda _: data)
    monkeypatch.setattr(user_delete, "_phase_root", lambda _: phase_root)
    monkeypatch.setattr(user_delete, "_preflight", lambda *_: (tmp_path / "config", False))
    monkeypatch.setattr(users, "_read_users", lambda *_: (raw, (tmp_path / "users.yml").stat() if (tmp_path / "users.yml").exists() else None))
    monkeypatch.setattr(users, "_oidc_subject", lambda *_args, **_kwargs: _SUBJECT)
    monkeypatch.setattr(user_delete, "_human_response", lambda *_args: _native_inspection(found=True, human={
        "id": _principal(), "username": "owner", "generation": 7, "disabled": True, "resources": [], "browser_transaction_floor": 0,
        **({"router_incarnation": incarnation} if incarnation is not None else {}),
    }))
    intent = _intent(_request(), generation=8)
    monkeypatch.setattr(user_delete, "_intent", lambda *_args: intent)
    captured = {}
    monkeypatch.setattr(user_delete, "_run_phase", lambda _data, _manifest, _path, phase, *_args, intent=None: captured.update({"phase": phase, "intent": intent}) or {"finalized": True})

    assert user_delete.delete(str(tmp_path / "deployment.json"), "owner", apply=True) == {"finalized": True}
    assert captured["phase"]["generation"] == 8 and captured["intent"] == intent


def test_idp_only_account_uses_atomic_native_absent_prepare(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "_require_root", lambda: None)
    monkeypatch.setattr(user_delete, "_path", lambda _: None)
    root = tmp_path / "current"
    root.mkdir(mode=0o700)
    phases = tmp_path / "phases"
    phases.mkdir(mode=0o700)
    raw = json.dumps({"users": {"owner": {"displayname": "Owner", "password": _HASH,
                                             "email": "owner@example.test", "groups": []}}}).encode()
    data = {"config_root": str(root), "gateway": {"oidc": {"issuer": "https://auth.example.test"}}}
    monkeypatch.setattr(user_delete, "read_manifest", lambda _: data)
    monkeypatch.setattr(user_delete, "_phase_root", lambda _: phases)
    monkeypatch.setattr(user_delete, "_preflight", lambda *_: (tmp_path / "config", True))
    monkeypatch.setattr(users, "_read_users", lambda *_: (raw, None))
    monkeypatch.setattr(users, "_oidc_subject", lambda *_args, **_kwargs: _SUBJECT)
    calls = []
    intent = _intent(_request(), generation=2)
    intent["principal"] = users._principal(data["gateway"]["oidc"]["issuer"], _SUBJECT)

    def native(_data, _manifest, payload, _runner, operation):
        calls.append(payload)
        if operation == "human-inspect":
            return _native_inspection(found=False)
        assert operation == "human-delete-prepare-absent"
        intent["request_id"] = payload["request_id"]
        intent["principal"] = payload["principal"]
        return {"operation": operation, "deletion": intent}

    monkeypatch.setattr(user_delete, "_human_response", native)
    captured = {}
    monkeypatch.setattr(user_delete, "_run_phase", lambda _data, _manifest, _path, phase, *_args, intent=None: captured.update({"phase": phase, "intent": intent}) or {"finalized": True})

    assert user_delete.delete(str(tmp_path / "deployment.json"), "owner", apply=True) == {"finalized": True}
    assert calls[0] == {"operation": "human-inspect", "principal": users._principal(data["gateway"]["oidc"]["issuer"], _SUBJECT)}
    assert calls[1]["operation"] == "human-delete-prepare-absent"
    assert calls[1]["username"] == "owner" and calls[1]["principal"] == users._principal(data["gateway"]["oidc"]["issuer"], _SUBJECT)
    assert captured["phase"]["idp_delete"] and captured["intent"] == intent


def test_never_signed_in_phase_rechecks_zero_identity_state_then_purges_without_native(tmp_path, monkeypatch):
    phase_root = tmp_path / "phases"
    phase_root.mkdir(mode=0o700)
    users_file = tmp_path / "users.yml"
    entry = {"displayname": "Owner", "password": _HASH, "email": "owner@example.test", "groups": []}
    raw = json.dumps({"users": {"owner": entry}}).encode()
    users_file.write_bytes(raw)
    users_file.chmod(0o600)
    phase = _local_phase(_request(), entry)
    path = user_delete._write_phase(phase_root, phase)
    root = tmp_path / "rendered"
    (root / "systemd").mkdir(parents=True)
    source = b"unit"
    (root / "systemd" / users._UNIT).write_bytes(source)
    data = {"config_root": str(root), "components": {"authelia": str(tmp_path / "authelia")},
            "authelia": {"users_file": str(users_file), "state_directory": str(tmp_path / "idp")}}
    monkeypatch.setattr(user_delete, "_preflight", lambda *_: (root / "authelia/configuration.yml", False))
    monkeypatch.setattr(manage, "_read_unit", lambda *_: source)
    monkeypatch.setattr(manage, "_unit_exec_path", lambda *_: Path(data["components"]["authelia"]))
    monkeypatch.setattr(manage, "_verify_unit", lambda *_: None)
    monkeypatch.setattr(manage, "_unit_metadata", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manage, "_unit_state", lambda *_: (False, "enabled"))
    monkeypatch.setattr(users, "_read_users", lambda *_: (raw, users_file.stat()))
    monkeypatch.setattr(users, "_oidc_identifiers", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(user_delete, "role_identity", lambda *_: (1001, 1001))
    monkeypatch.setattr(manage, "_safe_authelia_users_file", lambda *_args, **_kwargs: users_file)
    monkeypatch.setattr(__import__("anvil_serving.connect.user_backup", fromlist=["snapshot"]), "snapshot", lambda *_args, **_kwargs: {"ok": True})
    replaced = []
    monkeypatch.setattr(users, "_replace_users", lambda _path, value, _info: replaced.append(json.loads(value)))
    native = []
    monkeypatch.setattr(user_delete, "_human_response", lambda *_args: pytest.fail("must not call native finalization"))

    def purge(_database, _username, _subject, *, validate_only, require_zero_opaque, uid, gid):
        native.append((validate_only, require_zero_opaque, uid, gid))
        return {"schema_version": 29, "validated_only": validate_only, "applied": not validate_only,
                "username_records": 1, "opaque_identifiers": 0, "oauth_sessions": 0,
                "consent_sessions": 0, "consent_preconfigurations": 0, "expected_subject_found": False}

    monkeypatch.setattr(user_delete, "_purge_as_idp", purge)
    result = user_delete._run_local_phase(data, "manifest", path, phase, None, tmp_path)

    assert result["finalized"] and not path.exists()
    assert native == [(True, True, 1001, 1001), (False, True, 1001, 1001)]
    assert replaced == [{"users": {}}]


def test_process_pending_dispatches_a_retained_local_phase(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "_require_root", lambda: None)
    monkeypatch.setattr(user_delete, "_path", lambda _: None)
    root = tmp_path / "current"
    root.mkdir(mode=0o700)
    phases = tmp_path / "phases"
    phases.mkdir(mode=0o700)
    entry = {"displayname": "Owner", "password": _HASH, "email": "owner@example.test", "groups": []}
    phase = _local_phase(_request(), entry)
    user_delete._write_phase(phases, phase)
    monkeypatch.setattr(user_delete, "read_manifest", lambda _: {"config_root": str(root)})
    monkeypatch.setattr(user_delete, "_phase_root", lambda _: phases)
    monkeypatch.setattr(user_delete, "_intents", lambda *_: [])
    calls = []
    monkeypatch.setattr(user_delete, "_run_local_phase", lambda *_args: calls.append(_args[3]) or {"finalized": True})

    result = user_delete.process_pending(str(tmp_path / "deployment.json"), apply=True)
    assert result["processed"] == 1 and result["result"]["finalized"] and calls == [phase]


def test_pending_authelia_upgrade_blocks_delete_worker_before_phase_or_native_actions(tmp_path, monkeypatch):
    root = tmp_path / "rendered"
    root.mkdir(mode=0o700)
    manage._authelia_upgrade_marker(root).write_text("pending\n")
    monkeypatch.setattr(users, "_require_root", lambda: None)
    monkeypatch.setattr(user_delete, "_path", lambda *_: None)
    monkeypatch.setattr(user_delete, "read_manifest", lambda *_: {"config_root": str(root)})
    monkeypatch.setattr(user_delete, "_phase_root", lambda *_: pytest.fail("must not inspect deletion phases"))
    monkeypatch.setattr(user_delete, "_intents", lambda *_: pytest.fail("must not contact native authority"))

    with pytest.raises(manage.ManageError, match="Authelia migration recovery is pending"):
        user_delete.delete(str(tmp_path / "deployment.json"), "owner", apply=True)
    with pytest.raises(manage.ManageError, match="Authelia migration recovery is pending"):
        user_delete.process_pending(str(tmp_path / "deployment.json"), apply=True)


def test_omitted_legacy_username_decodes_and_preserves_mapping_fence(monkeypatch):
    wire = {"request_id": "11111111-1111-4111-8111-111111111111", "principal": "human:" + "a" * 64,
            "generation": 2, "epoch": "b" * 64, "digest": "c" * 64, "complete": False,
            "completed_at": "0001-01-01T00:00:00Z"}
    decoded = user_delete._deletion(wire)
    assert decoded["username"] == "" and "username" not in wire
    human = user_delete._human({"id": wire["principal"], "generation": 2, "disabled": True})
    assert human["username"] == ""
    data = {"gateway": {"oidc": {"issuer": "https://auth.example.test"}}}
    monkeypatch.setattr(users, "_oidc_identifiers", lambda *a, **kw: [])
    assert user_delete._mapped_subject(data, None, wire["principal"], decoded["username"], None) == ""
    monkeypatch.setattr(users, "_principal", lambda *a: wire["principal"])
    monkeypatch.setattr(users, "_oidc_identifiers", lambda *a, **kw: [
        {"service": "openid", "sector_id": "", "identifier": "subject", "username": "owner"}])
    with pytest.raises(UsageError, match="incomplete"):
        user_delete._mapped_subject(data, None, wire["principal"], decoded["username"], None)
    for invalid in (None, 1, [], {}):
        with pytest.raises(UsageError):
            user_delete._deletion({**wire, "username": invalid})
