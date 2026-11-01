"""Member scope survives local CLI, remote parsing and authenticated HTTP."""

from __future__ import annotations

import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from anvil_serving import cli, router_manage
from anvil_serving.control_plane.mcp.arguments import validate_tool_arguments
from anvil_serving.control_plane.mcp.tools.router import FAMILY, tool_router_transition
from tests.router.test_front_door_auth import TOKEN, _member_transition_server, running_server
from tests.router.test_transition_integration import _TextBackend, _routing
from tests.test_cli_contract import _leaf


ACTIONS = ("status", "quiesce", "drain", "readmit")


@pytest.mark.parametrize("action", ACTIONS)
def test_request_carries_exact_member_scope_and_auth(action):
    seen = []

    def open_request(request, *, timeout):
        seen.append((request, timeout))
        return io.BytesIO(b'{}')

    router_manage.transition_request(
        action, tier_id="replica-primary", member_id="member-a", timeout=2,
        router_url="http://127.0.0.1:18000", confirm=True, dry_run=False,
        env={"ANVIL_ROUTER_TOKEN": TOKEN}, _open=open_request,
    )
    request, timeout = seen[0]
    assert request.get_header("Authorization") == "Bearer " + TOKEN
    assert timeout == (7 if action == "drain" else 5)
    if action == "status":
        assert request.get_method() == "GET" and request.data is None
        assert parse_qs(urlsplit(request.full_url).query) == {
            "tier_id": ["replica-primary"], "member_id": ["member-a"],
        }
    else:
        body = json.loads(request.data)
        assert body["member_id"] == "member-a" and body["tier_id"] == "replica-primary"
        assert body["action"] == action and body["dry_run"] is False


@pytest.mark.parametrize("action", ["quiesce", "readmit"])
@pytest.mark.parametrize("confirm,dry_run", [(False, False), (False, True), (True, True)])
def test_member_preview_is_authenticated_remote_dry_run_and_probe_free(action, confirm, dry_run):
    with _member_transition_server() as (tier, routing, host, port):
        endpoint = f"http://{host}:{port}"
        result = router_manage.transition_request(
            action, tier_id=tier.id, member_id="member-a", router_url=endpoint,
            confirm=confirm, dry_run=dry_run, env={"ANVIL_ROUTER_TOKEN": TOKEN},
        )
        assert result == {
            "applied": False, "dry_run": True, "action": action,
            "tier_id": tier.id, "member_id": "member-a",
        }
        assert endpoint not in json.dumps(result) and TOKEN not in json.dumps(result)
        assert routing._availability.calls == routing._availability.invalidated == []
        assert not routing._admission.member_snapshot(tier.id, "member-a").quiesced


def test_member_preview_refuses_missing_auth_and_legacy_or_extra_field_response(monkeypatch):
    monkeypatch.delenv("ANVIL_ROUTER_TOKEN", raising=False)
    with pytest.raises(ValueError, match="required"):
        router_manage.transition_request("quiesce", tier_id="tier", member_id="member-a")
    expected = {"applied": False, "dry_run": True, "action": "quiesce", "tier_id": "tier", "member_id": "member-a"}
    for malformed in (
        {key: value for key, value in expected.items() if key != "member_id"},
        {**expected, "router_url": "http://127.0.0.1:18000"},
        {**expected, "applied": 0}, {**expected, "dry_run": 1},
        {**expected, "member_id": "member-b"},
    ):
        with pytest.raises(ValueError, match="preview was malformed"):
            router_manage.transition_request(
                "quiesce", tier_id="tier", member_id="member-a",
                env={"ANVIL_ROUTER_TOKEN": TOKEN},
                _open=lambda request, timeout: io.BytesIO(json.dumps(malformed).encode()),
            )


def test_local_and_controller_commands_apply_only_selected_member(monkeypatch, capsys):
    monkeypatch.setenv("ANVIL_ROUTER_TOKEN", TOKEN)
    for remote in (False, True):
        with _member_transition_server() as (tier, routing, host, port):
            target = ["--tier", tier.id, "--member", "member-a", "--router-url", f"http://{host}:{port}"]
            for verb, suffix in (
                ("quiesce", []), ("transition-status", []), ("drain", ["--timeout", "1"]), ("readmit", []),
            ):
                if remote:
                    arguments = cli._remote_arguments(_leaf(("router", verb)), target + suffix, confirmed=True)
                    arguments = validate_tool_arguments("router_transition", arguments, FAMILY.tools)
                    result = tool_router_transition(arguments)["data"]
                else:
                    approval = ["--confirm"] if verb in {"quiesce", "readmit"} else []
                    assert cli.main(["router", verb, *target, *suffix, *approval]) == 0
                    result = json.loads(capsys.readouterr().out)
                row = result["tiers"][0] if verb == "transition-status" else result["result"]
                if verb == "readmit":
                    assert row["readmitted"] is True
                    row = row["status"]["tiers"][0]
                if verb == "drain":
                    assert row["drained"] is True
                    row = row["snapshot"]
                assert row["tier_id"] == tier.id and row["member_id"] == "member-a"
                assert not routing._admission.snapshot(tier.id).quiesced
                assert not routing._admission.member_snapshot(tier.id, "member-b").quiesced
                assert TOKEN not in json.dumps(result) and f"{host}:{port}" not in json.dumps(result)
            assert not routing._admission.member_snapshot(tier.id, "member-a").quiesced
            assert all(member == "member-a" for _, member in routing._availability.calls)


def test_direct_tier_member_previews_refuse_through_authenticated_transport():
    routing = _routing(_TextBackend("a"), _TextBackend("b"))
    try:
        with running_server(routing, TOKEN) as (host, port):
            for action in ACTIONS:
                with pytest.raises(ValueError, match="HTTP 400"):
                    router_manage.transition_request(
                        action, tier_id="primary-local", member_id="member-a", timeout=1,
                        router_url=f"http://{host}:{port}", env={"ANVIL_ROUTER_TOKEN": TOKEN},
                    )
            assert not routing._admission.snapshot("primary-local").quiesced
    finally:
        routing.close()
