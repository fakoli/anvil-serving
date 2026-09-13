"""Bounded Connect entitlement provisioning through the native authority."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from anvil_serving.connect import manage, users
from anvil_serving.operator_output import UsageError
from tests.connect.test_render import isolated_manifest
from tests.connect import test_users


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="local Linux account administration")


def _data(tmp_path: Path) -> dict:
    auth_state = tmp_path / "idp"
    gateway_state = tmp_path / "gateway"
    auth_state.mkdir(mode=0o700)
    gateway_state.mkdir(mode=0o700)
    return {
        "config_root": str(tmp_path / "rendered"),
        "components": {"authelia": "/synthetic/authelia"},
        "authelia": {"state_directory": str(auth_state), "users_file": str(tmp_path / "users.yml")},
        "gateway": {
            "state_directory": str(gateway_state), "oidc": {"issuer": "https://auth.example.test"},
            "gateway": {"resources": [
                {"rule": {"id": "dash", "access": "browser"}},
                {"rule": {"id": "api", "access": "api"}},
            ]},
        },
        "service_identities": {
            "idp": {"uid": os.geteuid(), "gid": os.getegid()},
            "gateway": {"uid": os.geteuid(), "gid": os.getegid()},
        },
        "service_limits": {},
    }


def test_access_uses_exact_complete_role_map_and_does_not_touch_user_file(tmp_path, monkeypatch):
    data = _data(tmp_path)
    users_file = tmp_path / "users.yml"
    users_file.write_text(json.dumps({"users": {"dev": {
        "displayname": "dev", "password": "$argon2id$v=19$m=1,t=1,p=1$A$B", "email": "dev@example.test",
    }}}))
    monkeypatch.setattr(users, "_require_root", lambda: None)
    monkeypatch.setattr(users, "read_manifest", lambda _: data)
    monkeypatch.setattr(users, "_read_users", lambda *_: (users_file.read_bytes(), users_file.stat()))
    monkeypatch.setattr(manage, "_verify_owned_tree", lambda *_: None)
    monkeypatch.setattr(users, "_authelia", lambda _: "test configuration")
    monkeypatch.setattr(manage, "_read_regular", lambda *_: b"test configuration")
    monkeypatch.setattr(manage, "_digest", lambda *_: "pinned")
    monkeypatch.setattr(manage, "_component_lock", lambda: {"authelia": "pinned"})
    monkeypatch.setattr(users, "_oidc_subject", lambda *_: "00000000-0000-4000-8000-000000000001")
    requests = []
    def admin(_manifest, *, request_path, apply, runner):
        assert apply
        requests.append(json.loads(Path(request_path).read_text()))
        return {"schema": "anvil-connect.manage/v1", "applied": True}
    monkeypatch.setattr(manage, "_safe_private_runtime_directory", lambda *_: None)
    monkeypatch.setattr(manage, "admin", admin)

    preview = users.operate("/private/deployment.json", "access", "dev", grants=["dash:admin"])
    assert preview["applied"] is False and preview["application_roles"] == {"dash": "admin"}
    applied = users.operate("/private/deployment.json", "access", "dev", grants=["dash:admin"], apply=True)
    assert applied["applied"] and applied["existing_connect_sessions_revoked"]
    assert applied["principal"] == users._principal("https://auth.example.test", "00000000-0000-4000-8000-000000000001")
    assert users_file.exists()
    assert requests[-1]["application_roles"] == {"dash": "admin"}
    for invalid in ([], ["api:member"], ["dash:member", "dash:admin"], ["dash:owner"]):
        with pytest.raises(UsageError):
            users.operate("/private/deployment.json", "access", "dev", grants=invalid)
    assert len(requests) == 1


def test_identifier_export_is_private_disposed_and_matches_blank_openid_sector(tmp_path, monkeypatch):
    data = _data(tmp_path)
    subject = "00000000-0000-4000-8000-000000000001"
    monkeypatch.setattr(manage, "_safe_private_runtime_directory", lambda *_: None)
    identity = object()
    monkeypatch.setattr(manage, "_role_service_identity", lambda *_: identity)

    def runner(argv, timeout, service_identity):
        assert argv[1:5] == ("storage", "user", "identifiers", "export") and timeout == 15 and service_identity is identity
        export = Path(argv[argv.index("--file") + 1])
        export.write_text("identifiers:\n  - service: openid\n    sector_id: \"\"\n    username: dev\n    identifier: " + subject + "\n")
        export.chmod(0o600)
        return manage.RunResult(0)

    assert users._oidc_subject(data, tmp_path / "rendered/authelia/configuration.yml", "dev", runner) == subject
    assert not list(Path(data["authelia"]["state_directory"]).iterdir())
    for raw in (
        b"identifiers:\n  - service: openid\n    sector_id: 'other'\n    username: dev\n    identifier: 00000000-0000-4000-8000-000000000001\n",
        b"identifiers:\n  - service: openid\n    sector_id: \"\"\n    username: dev\n    identifier: 00000000-0000-4000-8000-000000000001\n  - service: openid\n    sector_id: \"\"\n    username: dev\n    identifier: 00000000-0000-4000-8000-000000000001\n",
    ):
        with pytest.raises(UsageError):
            users._identifier_subject(raw, "dev")


def test_create_grant_preseeds_subject_and_keeps_service_home_in_handoff(tmp_path, monkeypatch):
    run, _, state, _ = test_users.environment.__wrapped__(tmp_path, monkeypatch)
    data = users.read_manifest("unused")
    data["gateway"] = {"oidc": {"issuer": "https://auth.example.test"}, "gateway": {"resources": [
        {"rule": {"id": "dash", "access": "browser", "host": "dash.example.test", "path_prefix": "/app/"}},
    ]}}
    calls = []
    monkeypatch.setattr(users, "_human_set", lambda *args: calls.append(args) or {"schema": "anvil-connect.manage/v1", "applied": True})

    result = run("create", email="dev@example.test", grants=["dash:member"], apply=True)
    handoff = Path(result["handoff_file"]).read_text()
    identifiers = [argv for argv in state["calls"] if argv[1:5] == ("storage", "user", "identifiers", "add")]
    assert result["grants_changed"] and "Your services: https://dash.example.test/app/_anvil-connect/home" in handoff
    assert len(identifiers) == 1 and identifiers[0][identifiers[0].index("--sector") + 1] == ""
    assert calls[-1][3] == {"dash": "member"}


def test_rendered_oidc_client_uses_the_blank_sector_subject_type():
    assert "sector_identifier_uri" not in users._authelia(isolated_manifest())


def test_pinned_authelia_add_export_subject_smoke_is_isolated(tmp_path):
    binary = os.environ.get("ANVIL_CONNECT_TEST_AUTHELIA")
    if not binary:
        pytest.skip("set ANVIL_CONNECT_TEST_AUTHELIA to exercise the pinned native identifier commands")
    subject = str(uuid.uuid4())
    database = tmp_path / "authelia.sqlite3"
    export = tmp_path / "identifiers.yml"
    config = tmp_path / "configuration.yml"
    config.write_text("storage:\n  encryption_key: '01234567890123456789012345678901'\n  local:\n    path: '" + str(database) + "'\n")
    base = [binary, "--config", str(config), "storage", "user", "identifiers"]
    migrated = subprocess.run([binary, "--config", str(config), "storage", "migrate", "up"], capture_output=True, timeout=15)
    assert migrated.returncode == 0
    added = subprocess.run([*base, "add", "dev", "--identifier", subject, "--service", "openid", "--sector", ""], capture_output=True, timeout=15)
    assert added.returncode == 0
    written = subprocess.run([*base, "export", "--file", str(export)], capture_output=True, timeout=15)
    assert written.returncode == 0
    assert users._identifier_subject(export.read_bytes(), "dev") == subject
