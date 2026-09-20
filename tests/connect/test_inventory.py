"""Discovery must be useful without acquiring lifecycle or credential authority."""
import json
import subprocess
import sys

import pytest

from anvil_serving import cli
from anvil_serving.connect import inventory, users
from anvil_serving.connect.cli import USER_OPERATIONS, dispatch
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


def test_account_inventory_uses_safe_file_reader_without_writes(environment):
    _, db, state, _ = environment
    database = json.loads(db.read_text())
    database["users"]["owner"].update(disabled=True, extra={"private": "PRIVATE_SENTINEL"})
    db.write_text(json.dumps(database))
    before = db.read_bytes()
    expected = [{"username": "owner", "email": "owner@example.test", "groups": ["owners"], "disabled": True}]
    for username in (None, "owner"):
        result = inventory.accounts("/deployment.json", username)
        assert result["users"] == expected
        assert result["read_only"] is True
        assert "PRIVATE_SENTINEL" not in json.dumps(result) and "$argon2" not in json.dumps(result)
        assert "do not establish Connect grants" in result["note"]
    with pytest.raises(UsageError, match="Account not found"):
        inventory.accounts("/deployment.json", "missing")
    assert db.read_bytes() == before and state["calls"] == []
    db.chmod(0o644)
    with pytest.raises(RuntimeError):
        inventory.accounts("/deployment.json")


def test_account_inventory_requires_root_before_reading(monkeypatch):
    monkeypatch.setattr(users.os, "geteuid", lambda: 12345)
    monkeypatch.setattr(users, "read_manifest", lambda *_: pytest.fail("read before root check"))
    with pytest.raises(UsageError, match="sudo"):
        inventory.accounts("/deployment.json")


def test_empty_and_per_operation_help_never_discovers(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("help started a process"))
    for args in ([], ["users"], ["users", "create", "--help"], ["users", "access", "--help"]):
        with pytest.raises(SystemExit) as exc:
            dispatch(args, prog="anvil-connect-ctl")
        assert exc.value.code == 0
        help_text = capsys.readouterr().out
        assert "anvil-connect-ctl" in help_text
        if args[:2] == ["users", "create"]:
            assert "--email" in help_text and "--destination" not in help_text
        if args[:2] == ["users", "access"]:
            assert "ALL browser grants" in help_text and "--email" not in help_text
    for operation in USER_OPERATIONS:
        assert cli.main(["connect", "users", operation, "--help"]) == 0
        text = capsys.readouterr().out
        assert f"users {operation}" in text
        if operation in {"create", "access"}:
            assert "username" in text and "Example (preview)" in text
            assert "--destination" not in text


def test_invalid_arguments_do_not_execute_or_echo_operands(monkeypatch):
    monkeypatch.setattr(users, "operate", lambda *_a, **_k: pytest.fail("invalid operation executed"))
    for args in (
        ["users", "create", "dev"],
        ["users", "access", "dev"],
        ["users", "list", "--grant", "PRIVATE_SENTINEL:admin"],
        ["users", "show", "dev", "--confirm"],
        ["users", "reset-password", "dev", "--email", "PRIVATE_SENTINEL"],
        ["users", "create", "dev", "--email", "a@example.test", "--email", "PRIVATE_SENTINEL"],
    ):
        result = dispatch(args)
        assert result.error is not None
        assert "PRIVATE_SENTINEL" not in str(result.error)
        assert "--help" in str(result.error)


def test_deletion_lifecycle_commands_remain_available(monkeypatch):
    from anvil_serving.connect import user_delete, user_schedule

    calls = []
    monkeypatch.setattr(user_schedule, "schedule", lambda *args, **kwargs: calls.append(("schedule", args, kwargs)) or {})
    monkeypatch.setattr(user_delete, "process_pending", lambda *args, **kwargs: calls.append(("process", args, kwargs)) or {})

    assert dispatch(["users", "deletion-schedule", "--confirm"]).error is None
    assert dispatch(["users", "process-deletions", "--confirm"]).error is None
    assert calls == [
        ("schedule", (users.DEFAULT_MANIFEST,), {"apply": True, "deletions": True}),
        ("process", (users.DEFAULT_MANIFEST,), {"apply": True}),
    ]


def test_product_discovery_is_read_only_and_machine_readable(environment, capsys):
    for args in (["list"], ["show", "owner"]):
        assert cli.main(["connect", "users", *args, "--json"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["ok"] and result["data"]["users"][0]["username"] == "owner"


def test_product_unknown_user_operation_does_not_echo_private_operand(capsys):
    for flags in ([], ["--json"]):
        assert cli.main(["connect", "users", "PRIVATE_SENTINEL", *flags]) == 2
        output = capsys.readouterr()
        assert "PRIVATE_SENTINEL" not in output.out + output.err


@pytest.mark.parametrize("flags,applied", [([], False), (["--confirm"], True), (["--confirm", "--dry-run"], False)])
def test_legacy_option_first_users_preserve_gates(monkeypatch, capsys, flags, applied):
    calls = []
    monkeypatch.setattr(users, "operate", lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    argv = ["users", "--manifest", "/deployment.json", *flags, "--grant", "chat:member",
            "create", "dev", "--email", "dev@example.test"]
    assert dispatch(argv).error is None
    assert cli.main(["connect", *argv, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    assert len(calls) == 2
    assert all(args == ("/deployment.json", "create", "dev") and kwargs["apply"] is applied
               and kwargs["grants"] == ["chat:member"] for args, kwargs in calls)
    assert dispatch(["users", "--manifest", "/one", "create", "dev", "--manifest", "/two", "--email", "dev@example.test"]).error
    assert len(calls) == 2


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
