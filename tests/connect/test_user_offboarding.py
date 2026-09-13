"""Offboarding fails closed without deleting another account or its factors."""
import json
import sys
from contextlib import contextmanager

import pytest

from anvil_serving.connect import manage, users
from anvil_serving.connect.cli import dispatch
from anvil_serving.operator_output import UsageError
from tests.connect import test_users
from tests.connect.test_users import record

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="local Linux account administration")
_SUBJECT = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def offboarding(tmp_path, monkeypatch):
    run, db, state, private = test_users.environment.__wrapped__(tmp_path, monkeypatch)
    accounts = json.loads(db.read_text())
    accounts["users"]["dev"] = record("dev@example.test")
    db.write_text(json.dumps(accounts))
    data = users.read_manifest("unused")
    data["gateway"] = {"oidc": {"issuer": "https://auth.example.test"}, "gateway": {"resources": [
        {"rule": {"id": "pi", "access": "browser"}},
    ]}}
    monkeypatch.setattr(users, "_oidc_subject", lambda *args, **kwargs: _SUBJECT)
    requests = []

    def human_admin(data, manifest, payload, runner):
        assert not state["active"]
        assert list((private.parent / "backups").glob("auth-*.zip"))
        requests.append(payload)
        if state.get("fail_authority"):
            raise manage.ManageError("synthetic authority rejection")
        return {"applied": True}

    monkeypatch.setattr(users, "_human_admin", human_admin)
    return run, db, state, requests


def test_suspend_preserves_password_groups_and_factors_then_access_resumes(offboarding):
    run, db, state, requests = offboarding
    before = json.loads(db.read_text())["users"]
    preview = run("suspend")
    assert not preview["applied"] and not state["calls"] and not requests
    result = run("suspend", apply=True)
    suspended = json.loads(db.read_text())["users"]
    assert suspended == {**before, "dev": {**before["dev"], "disabled": True}}
    assert requests == [{"operation": "human-suspend", "subject": _SUBJECT}]
    assert result["existing_connect_sessions_revoked"] and result["account_disabled"]
    assert not result["account_deleted"] and not result["grants_changed"]
    assert state["active"] and not any(c[1:3] == ("storage", "user") for c in state["calls"])
    run("suspend", apply=True)
    resumed = run("access", grants=["pi:member"], apply=True)
    assert not json.loads(db.read_text())["users"]["dev"]["disabled"]
    assert requests[-1] == {"operation": "human-set", "subject": _SUBJECT,
                            "resources": ["pi"], "application_roles": {"pi": "member"}}
    assert resumed["grants_changed"] and resumed["existing_connect_sessions_revoked"]


def test_password_reset_replaces_hash_before_revoking_existing_connect_sessions(offboarding, monkeypatch):
    run, db, state, requests = offboarding
    accounts = json.loads(db.read_text())
    accounts["users"]["dev"]["password"] = test_users._HASH.replace("B" * 43, "C" * 43)
    db.write_text(json.dumps(accounts))
    before = json.loads(db.read_text())["users"]["dev"]
    original = users._human_admin

    def revoke(*args):
        assert json.loads(db.read_text())["users"]["dev"]["password"] == test_users._HASH
        return original(*args)

    monkeypatch.setattr(users, "_human_admin", revoke)
    result = run("reset-password", "dev", apply=True)
    after = json.loads(db.read_text())["users"]["dev"]
    assert after == {**before, "password": test_users._HASH}
    assert requests == [{"operation": "human-revoke-sessions", "subject": _SUBJECT}]
    assert result["existing_connect_sessions_revoked"] and result["password_setup_required"]
    assert not result["initial_password_saved"] and state["active"]


def test_delete_removes_only_selected_account_and_factors(offboarding):
    run, db, state, requests = offboarding
    result = run("delete", apply=True)
    assert json.loads(db.read_text())["users"] == {"owner": record()}
    assert result["account_deleted"] and result["existing_connect_sessions_revoked"]
    assert requests == [{"operation": "human-suspend", "subject": _SUBJECT}]
    calls = [c for c in state["calls"] if c[1:3] == ("storage", "user")]
    assert len(calls) == 2
    assert calls[0][3:7] == ("webauthn", "delete", "dev", "--all")
    assert calls[1][3:6] == ("totp", "delete", "dev")
    assert "handoff_file" not in result and state["active"]
    with pytest.raises(UsageError, match="Account does not exist"):
        run("delete", apply=True)


@pytest.mark.parametrize("operation", ["suspend", "delete"])
def test_authority_rejection_preserves_account_and_factors(offboarding, operation):
    run, db, state, _ = offboarding
    before = db.read_bytes()
    state["fail_authority"] = True
    with pytest.raises(manage.ManageError):
        run(operation, apply=True)
    assert db.read_bytes() == before and state["active"]
    assert not any(c[1:3] == ("storage", "user") for c in state["calls"])


def test_password_reset_authority_rejection_restores_the_prior_hash_before_restart(offboarding):
    run, db, state, requests = offboarding
    before = db.read_bytes()
    state["fail_authority"] = True
    with pytest.raises(manage.ManageError):
        run("reset-password", apply=True)
    assert db.read_bytes() == before and state["active"]
    assert requests == [{"operation": "human-revoke-sessions", "subject": _SUBJECT}]


@pytest.mark.parametrize("failure", ["factor", "start"])
def test_failed_delete_never_reactivates_account(offboarding, failure):
    run, db, state, _ = offboarding
    state["fail_" + failure] = True
    with pytest.raises(manage.ManageError) as error:
        run("delete", apply=True)
    accounts = json.loads(db.read_text())["users"]
    assert "dev" not in accounts or accounts["dev"]["disabled"]
    assert accounts["owner"] == record() and state["active"]
    assert error.value.recovery["existing_connect_sessions_revoked"]
    assert error.value.recovery["backup"]["sha256"]


def test_never_signed_in_account_can_be_deleted(offboarding, monkeypatch):
    run, db, _, requests = offboarding
    monkeypatch.setattr(users, "_oidc_subject", lambda *args, **kwargs: None)
    assert run("delete", apply=True)["account_deleted"]
    assert "dev" not in json.loads(db.read_text())["users"] and not requests


def test_deleted_identity_cannot_be_silently_reused(offboarding):
    run, db, state, _ = offboarding
    run("delete", apply=True)
    before = db.read_bytes()
    state["calls"].clear()
    with pytest.raises(UsageError, match="retained OpenID identifier"):
        run("create", email="new@example.test", apply=True)
    assert db.read_bytes() == before and not state["calls"]


def test_access_refuses_stale_account_after_waiting_for_offboarding(offboarding, monkeypatch):
    run, db, _, requests = offboarding
    lock = manage._deployment_lock

    @contextmanager
    def suspended_before_lock(root):
        with lock(root):
            accounts = json.loads(db.read_text())
            accounts["users"]["dev"]["disabled"] = True
            db.write_text(json.dumps(accounts))
            yield

    monkeypatch.setattr(manage, "_deployment_lock", suspended_before_lock)
    with pytest.raises(UsageError, match="changed during preparation"):
        run("access", grants=["pi:member"], apply=True)
    assert not requests and json.loads(db.read_text())["users"]["dev"]["disabled"]


@pytest.mark.parametrize("operation", ["suspend", "delete"])
def test_offboarding_cli_preview_confirmation_and_invalid_operands(offboarding, monkeypatch, operation):
    run, db, state, _ = offboarding
    for kwargs in ({"email": "dev@example.test"}, {"role": "admin"}, {"grants": ["pi:member"]}, {"output": "/private/output"}):
        with pytest.raises(UsageError):
            run(operation, apply=True, **kwargs)
    assert not state["calls"]
    calls = []
    monkeypatch.setattr(users, "operate", lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    for flags, apply in (([], False), (["--confirm"], True), (["--confirm", "--dry-run"], False)):
        assert dispatch(["users", operation, "dev", *flags]).error is None
        assert calls[-1][0] == (users.DEFAULT_MANIFEST, operation, "dev")
        assert calls[-1][1]["apply"] is apply


def test_empty_identifier_export_is_distinct_from_malformed_export():
    assert users._identifier_subject(b"identifiers: []\n", "dev", missing_ok=True) is None
    with pytest.raises(UsageError):
        users._identifier_subject(b"garbage\n", "dev", missing_ok=True)
