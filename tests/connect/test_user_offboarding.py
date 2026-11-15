"""Offboarding fails closed without deleting another account or its factors."""
import json
import sys

import pytest

from anvil_serving.connect import manage, users
from anvil_serving.connect.cli import dispatch
from anvil_serving.operator_output import UsageError
from tests.connect import test_users
from tests.connect.test_users import record

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="local Linux account administration")
_SUBJECT = "00000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def _deny_real_subprocesses(monkeypatch):
    """Deletion tests must supply their own lifecycle runner."""
    monkeypatch.setattr(
        manage, "_bounded_run",
        lambda *_args, **_kwargs: pytest.fail("unexpected real subprocess from test"),
    )


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


@pytest.mark.parametrize("operation", ["suspend"])
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
