from dataclasses import replace
from pathlib import Path
import json

import pytest

from anvil_serving import cli
from anvil_serving.native_agents import local_check
from anvil_serving.native_agents.types import Directory, Workspace


def test_local_http_check_needs_no_login_and_cleans_state(capsys):
    before = set(Path.home().glob(".anvil-agents-check-*"))
    assert cli.main(["connect", "native-agents-check", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert result["ok"] and result["mode"] == "synthetic-owner-check"
    assert {"other-session-denied", "other-operation-denied", "other-stop-denied",
            "missing-token-denied", "unknown-token-denied", "local-admission-revoked",
            "other-admission-preserved", "create", "own-stop"} <= set(result["checks"])
    assert not any(result[key] for key in ("oauth_tested", "runtime_isolation_tested", "model_tested"))
    assert set(Path.home().glob(".anvil-agents-check-*")) == before


def guest_workspaces(root):
    return [Workspace(f"guest-{index}", "human:" + str(index + 1) * 64, "Guest",
        (Directory("project", "Project", root, "/workspace/project"),),
        f"http://127.0.0.1:{9001+index}", root / f"token-{index}", "sandboxed") for index in range(2)]


def test_owner_preview_and_personal_refusal_before_owner_access(monkeypatch, tmp_path):
    workspaces = guest_workspaces(tmp_path)
    data = {"personal_principal": "human:" + "f" * 64}
    monkeypatch.setattr(local_check, "load_config", lambda path: (data, workspaces))
    monkeypatch.setattr(local_check, "PiOwner", lambda workspace: pytest.fail("preview contacted owner"))
    assert local_check.check("protected.json")["mode"] == "owner-preview"
    workspaces[0] = replace(workspaces[0], isolation="personal")
    with pytest.raises(ValueError, match="guest principals"):
        local_check.check("protected.json", apply=True)
    workspaces[0] = replace(workspaces[0], isolation="sandboxed", principal=data["personal_principal"])
    with pytest.raises(ValueError, match="guest principals"):
        local_check.check("protected.json", apply=True)


def test_failure_keeps_request_and_session_ids_without_error_text(monkeypatch, tmp_path):
    workspaces = guest_workspaces(tmp_path)
    monkeypatch.setattr(local_check, "load_config", lambda path: ({}, workspaces))

    class BrokenOwner(local_check._FixtureOwner):
        def state(self, session):
            raise ValueError("private-error-sentinel")

    monkeypatch.setattr(local_check, "PiOwner", lambda workspace: BrokenOwner())
    result = local_check.check("protected.json", apply=True)
    assert not result["ok"] and result["sessions"] and result["create_requests"]
    assert result["temporary_state_removed"]
    assert "private-error-sentinel" not in json.dumps(result)


def test_cli_confirm_and_dry_run(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(local_check, "check", lambda path, *, apply: calls.append((path, apply)) or {"ok": True})
    for flags in ([], ["--confirm"], ["--confirm", "--dry-run"]):
        assert cli.main(["connect", "native-agents-check", "--config", "/protected/guest.json", *flags]) == 0
    assert calls == [("/protected/guest.json", False), ("/protected/guest.json", True), ("/protected/guest.json", False)]


def test_configured_control_check_does_not_prompt(monkeypatch, tmp_path):
    workspaces = guest_workspaces(tmp_path)
    monkeypatch.setattr(local_check, "load_config", lambda path: ({}, workspaces))
    # Fixture owners deliberately have no invoke/prompt, provider, or memory API.
    owners = {w.id: local_check._FixtureOwner() for w in workspaces}
    monkeypatch.setattr(local_check, "PiOwner", lambda workspace: owners[workspace.id])
    result = local_check.check("protected.json", apply=True)
    assert result["ok"] and result["mode"] == "owner-control-check"
    assert all(len(owner.sessions_by_id) == 1 for owner in owners.values())


def test_failed_check_cli_preserves_safe_audit_and_nonzero_exit(monkeypatch, capsys):
    result = {"ok": False, "sessions": [{"session_id": "empty-test-session"}],
              "create_requests": [{"request_id": "audit-request"}]}
    monkeypatch.setattr(local_check, "check", lambda *args, **kwargs: result)
    assert cli.main(["connect", "native-agents-check", "--json"]) != 0
    output = json.loads(capsys.readouterr().out)
    assert output["data"] == result
    assert output["error"]["code"] == "native_agents_local_check_failed"
