"""Discovery must be useful without acquiring lifecycle or credential authority."""
import json
import subprocess
import sys

import pytest

from anvil_serving import cli
from anvil_serving.connect import users
from anvil_serving.connect.cli import dispatch
from anvil_serving.operator_output import UsageError
from tests.connect import test_users


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="local Linux account administration")


@pytest.fixture
def environment(tmp_path, monkeypatch):
    return test_users.environment.__wrapped__(tmp_path, monkeypatch)


def test_resources_allowlist_omits_api_and_secret_references(monkeypatch):
    def resource(identifier, access, auth):
        return {"rule": {"id": identifier, "access": access, "host": identifier + ".example.test",
                         "path_prefix": "/app/", "native_auth": auth},
                "identity_key_env": "PRIVATE_SENTINEL"}
    data = {"gateway": {"gateway": {"resources": [
        resource("workbench", "browser", "signed-identity"),
        resource("api", "api", "delegate-bearer"),
        resource("chat", "browser", "passthrough"),
    ]}}}
    monkeypatch.setattr(users, "read_manifest", lambda _: data)
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("discovery started a process"))
    result = dispatch(["resources"])
    assert result.error is None
    assert result.data["resources"] == [
        {"id": "chat", "url": "https://chat.example.test/app/", "native_auth": "passthrough",
         "grants": ["chat:member", "chat:admin"]},
        {"id": "workbench", "url": "https://workbench.example.test/app/", "native_auth": "signed-identity",
         "grants": ["workbench:member", "workbench:admin"]},
    ]
    assert "PRIVATE_SENTINEL" not in json.dumps(result.data)
    assert result.data["read_only"] is True


def test_product_discovery_is_read_only_and_machine_readable(environment, capsys):
    for args in (["list"], ["show", "owner"]):
        assert cli.main(["connect", "users", *args, "--json"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["ok"] and result["data"]["users"][0]["username"] == "owner"


@pytest.mark.parametrize("partial", [False, True])
def test_legacy_option_first_users_preserve_error_and_recovery(monkeypatch, capsys, partial):
    def fail(*args, **kwargs):
        if partial:
            raise users._partial("Account exists; retry access provisioning.", {
                "operation": "create", "username": "dev", "backup": {"file": "/private/backup.zip"},
            })
        raise UsageError("Account already exists.", code="connect_users_invalid")
    monkeypatch.setattr(users, "operate", fail)
    results = []
    for argv in (
        ["users", "--manifest", "/deployment.json", "create", "dev"],
        ["users", "create", "dev", "--manifest", "/deployment.json"],
    ):
        assert cli.main(["connect", *argv, "--email", "dev@example.test", "--confirm", "--json"]) == (5 if partial else 2)
        results.append(json.loads(capsys.readouterr().out))
    assert results[0]["error"] == results[1]["error"]
    assert results[0]["data"] == results[1]["data"]
    if partial:
        assert results[0]["error"]["code"] == "connect_operation_partial"
        assert results[0]["data"]["recovery_hint"] == "Account exists; retry access provisioning."
