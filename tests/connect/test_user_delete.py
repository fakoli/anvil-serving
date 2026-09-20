"""Forward-only native account deletion worker boundaries."""
from __future__ import annotations

from contextlib import contextmanager
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


def test_worker_holds_deployment_lock_across_native_intent_and_finalize(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "_require_root", lambda: None)
    monkeypatch.setattr(user_delete, "_path", lambda _: None)
    root = tmp_path / "current"
    root.mkdir(mode=0o700)
    phase_root = tmp_path / "phases"
    phase_root.mkdir(mode=0o700)
    data = {"config_root": str(root)}
    monkeypatch.setattr(user_delete, "read_manifest", lambda _: data)
    monkeypatch.setattr(user_delete, "_phase_root", lambda _: phase_root)
    locked = {"value": False}

    @contextmanager
    def lock(path):
        assert path == root and not locked["value"]
        locked["value"] = True
        try:
            yield
        finally:
            locked["value"] = False

    request_id = _request()
    intent = _intent(request_id)
    monkeypatch.setattr(manage, "_deployment_lock", lock)
    monkeypatch.setattr(user_delete, "_intents", lambda *_: (assert_locked(locked), [intent])[1])
    monkeypatch.setattr(user_delete, "_new_phase", lambda *_: (assert_locked(locked), _phase(request_id))[1])
    monkeypatch.setattr(user_delete, "_run_phase", lambda *_args, **_kwargs: (assert_locked(locked), {"finalized": True})[1])

    result = user_delete.process_pending(str(tmp_path / "deployment.json"), apply=True)

    assert result["processed"] == 1 and not locked["value"]


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
        return {"schema_version": 24, "validated_only": validate_only, "applied": not validate_only,
                "username_records": 0, "opaque_identifiers": 1 if validate_only else 0,
                "oauth_sessions": 0, "consent_sessions": 0, "consent_preconfigurations": 0,
                "expected_subject_found": True if validate_only else False}

    monkeypatch.setattr(user_delete, "_purge_as_idp", purge)
    monkeypatch.setattr(user_delete, "_human_response", lambda *_args: {"operation": "human-delete-finalize"})

    result = user_delete._run_phase(data, "manifest", path, phase, None, tmp_path, intent=_intent(request_id))

    assert result["finalized"] and state["replaced"] and state["purges"] == [True, False] and state["active"]
    assert not path.exists()


def test_connect_only_intent_finalizes_without_idp_or_service_mutation(tmp_path, monkeypatch):
    root = tmp_path / "phases"
    root.mkdir(mode=0o700)
    request_id = _request()
    phase = _phase(request_id, idp_delete=False)
    path = user_delete._write_phase(root, phase)
    monkeypatch.setattr(user_delete, "_preflight", lambda *_: pytest.fail("Connect-only intent must not inspect IdP"))
    monkeypatch.setattr(users, "_read_users", lambda *_: pytest.fail("Connect-only intent must not read users"))
    monkeypatch.setattr(user_delete, "_human_response", lambda *_args: {"operation": "human-delete-finalize"})

    assert user_delete._run_phase({}, "manifest", path, phase, None, tmp_path, intent=_intent(request_id))["finalized"]
    assert not path.exists()


def test_mapped_subject_refuses_username_or_principal_mismatch(monkeypatch):
    issuer = "https://auth.example.test"
    principal = users._principal(issuer, _SUBJECT)
    monkeypatch.setattr(users, "_oidc_identifiers", lambda *_args, **_kwargs: [
        {"service": "openid", "sector_id": "", "username": "other", "identifier": _SUBJECT},
    ])
    with pytest.raises(UsageError):
        user_delete._mapped_subject({"gateway": {"oidc": {"issuer": issuer}}}, Path("/unused"), principal, "owner", None)


def test_delete_route_delegates_before_legacy_users_file_reads(monkeypatch):
    monkeypatch.setattr(users, "_require_root", lambda: None)
    captured = {}
    monkeypatch.setattr(user_delete, "delete", lambda manifest, username, **kwargs: captured.update({"manifest": manifest, "username": username, **kwargs}) or {"ok": True})
    assert users.operate("/private/deployment.json", "delete", "owner", apply=True, runner="runner", unit_root=Path("/units")) == {"ok": True}
    assert captured == {"manifest": "/private/deployment.json", "username": "owner", "apply": True, "runner": "runner", "unit_root": Path("/units")}


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


def test_retained_phase_recovers_without_users_file_or_second_prepare(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "_require_root", lambda: None)
    monkeypatch.setattr(user_delete, "_path", lambda _: None)
    root = tmp_path / "current"
    root.mkdir(mode=0o700)
    phase_root = tmp_path / "phases"
    phase_root.mkdir(mode=0o700)
    request_id = _request()
    phase = _phase(request_id)
    path = user_delete._write_phase(phase_root, phase)
    monkeypatch.setattr(user_delete, "read_manifest", lambda _: {"config_root": str(root)})
    monkeypatch.setattr(user_delete, "_phase_root", lambda _: phase_root)
    monkeypatch.setattr(user_delete, "_intents", lambda *_: [])
    monkeypatch.setattr(users, "_read_users", lambda *_: pytest.fail("retained recovery must not require users file"))
    called = {}
    monkeypatch.setattr(user_delete, "_run_phase", lambda _data, _manifest, got_path, got_phase, *_args, intent=None: called.update({"path": got_path, "phase": got_phase, "intent": intent}) or {"finalized": True})

    result = user_delete.delete(str(tmp_path / "deployment.json"), "owner", apply=True)

    assert result == {"finalized": True} and called == {"path": path, "phase": phase, "intent": None}


def test_orphan_phase_is_reconciled_once_after_native_completed_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "_require_root", lambda: None)
    monkeypatch.setattr(user_delete, "_path", lambda _: None)
    root = tmp_path / "current"
    root.mkdir(mode=0o700)
    phase_root = tmp_path / "phases"
    phase_root.mkdir(mode=0o700)
    request_id = _request()
    phase = _phase(request_id)
    path = user_delete._write_phase(phase_root, phase)
    monkeypatch.setattr(user_delete, "read_manifest", lambda _: {"config_root": str(root)})
    monkeypatch.setattr(user_delete, "_phase_root", lambda _: phase_root)
    monkeypatch.setattr(user_delete, "_intents", lambda *_: [])
    called = {}
    monkeypatch.setattr(user_delete, "_run_phase", lambda _data, _manifest, got_path, got_phase, *_args, intent=None: called.update({"path": got_path, "phase": got_phase, "intent": intent}) or {"finalized": True})

    result = user_delete.process_pending(str(tmp_path / "deployment.json"), apply=True)

    assert result["processed"] == 1 and result["result"] == {"finalized": True}
    assert called == {"path": path, "phase": phase, "intent": None}


def test_native_deletion_response_requires_zero_pending_timestamp():
    value = _intent(_request())
    assert user_delete._deletion(value) == value
    value["completed_at"] = "2026-09-20T00:00:00Z"
    with pytest.raises(UsageError):
        user_delete._deletion(value)


def test_delete_allows_previously_disabled_native_human(tmp_path, monkeypatch):
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
    monkeypatch.setattr(user_delete, "_human_response", lambda *_args: {"operation": "human-inspect", "human": {
        "id": _principal(), "username": "owner", "generation": 7, "disabled": True, "resources": [], "browser_transaction_floor": 0,
    }})
    intent = _intent(_request(), generation=8)
    monkeypatch.setattr(user_delete, "_intent", lambda *_args: intent)
    captured = {}
    monkeypatch.setattr(user_delete, "_run_phase", lambda _data, _manifest, _path, phase, *_args, intent=None: captured.update({"phase": phase, "intent": intent}) or {"finalized": True})

    assert user_delete.delete(str(tmp_path / "deployment.json"), "owner", apply=True) == {"finalized": True}
    assert captured["phase"]["generation"] == 8 and captured["intent"] == intent
