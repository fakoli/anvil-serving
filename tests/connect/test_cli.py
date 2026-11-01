from __future__ import annotations

import importlib
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving import cli
from anvil_serving.commands import manifest_data
from anvil_serving.connect.cli import dispatch


ROOT = Path(__file__).parents[2]
LEAVES = {"resources", "validate", "render", "up", "down", "status", "doctor", "logs", "init", "identity", "admin", "keygen", "backup", "restore", "migration", "edge-status", "edge-apply", "extend", "qualify", "users", "native-agents", "native-authority", "native-memory-service"}


@pytest.fixture
def fake_manager(monkeypatch):
    calls = []
    def invoke(action):
        def call(*args, **kwargs):
            calls.append((action, args, kwargs))
            return {"schema": "anvil-connect.management/v1", "action": action, "state": "preview" if not kwargs.get("apply") else "applied"}
        return call
    def target(value):
        if value != "gateway" and not value.startswith(("connector:", "client:")):
            raise ValueError("private operand")
        return value
    value = SimpleNamespace(
        Target=SimpleNamespace(parse=target),
        supported_platform=lambda: True,
        **{name: invoke(name) for name in LEAVES | {"native_init", "up_many"}},
    )
    package = importlib.import_module("anvil_serving.connect")
    monkeypatch.setattr(package, "manage", value, raising=False)
    return calls


@pytest.mark.parametrize("flags,applied", [([], False), (["--confirm"], True), (["--confirm", "--dry-run"], False)])
def test_root_dispatch_preserves_preview_and_conditional_confirmation(fake_manager, capsys, flags, applied):
    assert cli.main(["connect", "up", "--manifest", "/private/deployment.json", "--service", "gateway", "--json", *flags]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert fake_manager == [("up", ("/private/deployment.json",), {"target": "gateway", "apply": applied})]


def test_cli_rejects_unknown_duplicate_or_credential_like_operands_before_manager(fake_manager):
    for args in (
        ["up", "--manifest", "/a", "--manifest", "/b", "--service", "gateway"],
        ["admin", "--manifest", "/a", "--request", "/b", "--secret", "private-sentinel"],
        ["logs", "--manifest", "/a", "--service", "gateway", "--tail", "201"],
        ["up", "--manifest", "/a"],
    ):
        result = dispatch(args)
        assert result.error is not None
        assert "private-sentinel" not in str(result.error)
    assert fake_manager == []


@pytest.mark.parametrize("partial,exit_code", [(False, 1), (True, 5)])
def test_execution_failures_are_redacted_and_distinguish_partial_results(fake_manager, monkeypatch, capsys, partial, exit_code):
    package = importlib.import_module("anvil_serving.connect")
    def fail(*args, **kwargs):
        error = RuntimeError("private-subprocess-output")
        error.may_have_executed = partial
        raise error
    monkeypatch.setattr(package.manage, "up", fail)
    assert cli.main(["connect", "up", "--manifest", "/a", "--service", "gateway", "--confirm", "--json"]) == exit_code
    output = capsys.readouterr().out
    assert "private-subprocess-output" not in output
    result = json.loads(output)
    assert result["error"]["details"]["may_have_executed"] is partial


def test_cli_emits_pending_authelia_upgrade_recovery(fake_manager, monkeypatch, capsys):
    package = importlib.import_module("anvil_serving.connect")
    def pending(*args, **kwargs):
        error = RuntimeError("pending")
        error.may_have_executed = True
        error.recovery = {
            "recovery_required": True, "backup_retained": True,
            "recovery_hint": "Authelia migration recovery is pending; rerun the identical connect up command with --upgrade --confirm",
        }
        raise error
    monkeypatch.setattr(package.manage, "up", pending)
    assert cli.main(["connect", "up", "--manifest", "/a", "--service", "gateway", "--confirm", "--json"]) == 5
    result = json.loads(capsys.readouterr().out)
    assert result["data"] == {
        "recovery_required": True, "backup_retained": True,
        "recovery_hint": "Authelia migration recovery is pending; rerun the identical connect up command with --upgrade --confirm",
    }
    assert cli.main(["connect", "up", "--manifest", "/a", "--service", "gateway", "--confirm"]) == 5
    assert "rerun the identical connect up command with --upgrade --confirm" in capsys.readouterr().err


def test_existing_edge_manifest_records_remain_unchanged():
    # Golden canonical digest captured before Connect registration. No Git
    # history or live Tailscale inspection is required by this regression.
    edges = [entry for entry in manifest_data()["commands"] if entry["path"] == "edge" or entry["path"].startswith("edge ")]
    digest = hashlib.sha256(json.dumps(edges, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert digest == "69eb1f26a60755fb425afc69dd53b9a2955599e7a9f192cf2823a5529e67fce6"
