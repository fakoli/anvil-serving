"""Explicit client omissions preserve the router and shared compaction safety."""
import json
import pytest
from anvil_serving import client_catalog_sync as sync


def catalog():
    return {"config_sha256": "a" * 64, "models": {
        name: {"id": name, "context_window": context, "max_output_tokens": output,
               "input": ["text"], "reasoning": False}
        for name, context, output in [
            ("llm.primary", 327680, 65536), ("llm.secondary", 32768, 2048)]
    }}


def settings():
    return {"defaultProvider": "anvil", "defaultModel": "llm.primary",
            "enabledModels": ["other/model", "anvil/llm.secondary"],
            "compaction": {"enabled": True, "reserveTokens": 65536, "keepRecentTokens": 20000}}


def openclaw():
    return {"models": {"providers": {"anvil": {"apiKey": "$KEY", "models": []}}},
            "agents": {"defaults": {"models": {"anvil/llm.secondary": {}, "other/model": {}},
                "model": {"primary": "anvil/llm.primary"},
                "compaction": {"mode": "safeguard", "reserveTokens": 65536, "keepRecentTokens": 20000}}}}


def test_cli_and_mcp_forward_explicit_policy(monkeypatch):
    from anvil_serving import harness
    from anvil_serving.control_plane.mcp.tools import openclaw as tool
    received = []
    monkeypatch.setattr(sync, "sync_clients", lambda **kw: received.append(kw) or {})
    assert harness.main(["sync", "clients", "--base-url", "http://127.0.0.1:8000/v1",
        "--pi-exclude-aliases", "llm.secondary", "--openclaw-exclude-aliases", "llm.secondary",
        "--openclaw-allow-aliases", "llm.primary",
        "--dry-run"]) == 0
    tool.tool_client_catalog_sync({"base_url": "http://127.0.0.1:8000/v1",
        "pi_exclude_aliases": "llm.secondary", "openclaw_exclude_aliases": "llm.secondary",
        "openclaw_allow_aliases": "llm.primary",
        "dry_run": True})
    assert len(received) == 2
    for row in received:
        assert row["pi_exclude_aliases"] == row["openclaw_exclude_aliases"] == "llm.secondary"
        assert row["openclaw_allow_aliases"] == "llm.primary"


@pytest.mark.parametrize("file_already_matches", [False, True])
@pytest.mark.parametrize("restore_allow", [False, True])
def test_policy_restart_remains_pending_across_apply_and_other_client(tmp_path, monkeypatch, file_already_matches, restore_allow):
    source = catalog()
    source["models"]["llm.secondary"]["context_window"] = 131072
    monkeypatch.setattr(sync, "fetch_client_catalog", lambda **kw: source)
    path = tmp_path / "openclaw.json"
    doc = openclaw()
    doc["agents"]["defaults"]["modelPolicy"] = {"allow": ["anvil/llm.primary"]}
    policy_args = {"allow_aliases": "llm.secondary"} if restore_allow else {"exclude_aliases": "llm.secondary"}
    if file_already_matches:
        doc = sync._render_openclaw_document(source, doc, **policy_args)
    path.write_text(json.dumps(doc))
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"client_excluded_aliases": {"openclaw": []},
        "openclaw_restarted_sha256": source["config_sha256"],
        "openclaw_service_restarted_sha256": source["config_sha256"]}))
    args = dict(base_url="http://127.0.0.1:8000/v1", clients="openclaw",
        openclaw_config=str(path), state_path=str(state), backup_root=str(tmp_path / "backups"),
        environ={"ANVIL_ROUTER_TOKEN": "test-only"}, confirm=True, dry_run=False,
        openclaw_exclude_aliases="" if restore_allow else "llm.secondary",
        openclaw_allow_aliases="llm.secondary" if restore_allow else "")
    sync.sync_clients(**args)
    assert json.loads(state.read_text())["openclaw_restarted_sha256"] is None
    pi_models = tmp_path / "models.json"; pi_models.write_text("{}")
    pi_settings = tmp_path / "settings.json"; pi_settings.write_text(json.dumps(settings()))
    sync.sync_clients(**{**args, "clients": "pi", "pi_models": str(pi_models),
        "pi_settings": str(pi_settings), "pi_exclude_aliases": "llm.secondary"})
    assert json.loads(state.read_text())["client_excluded_aliases"] == {
        "openclaw": [] if restore_allow else ["llm.secondary"], "pi": ["llm.secondary"]}
    assert json.loads(state.read_text())["openclaw_allowed_aliases"] == (["llm.secondary"] if restore_allow else [])
    restarts = []
    args.update(restart_openclaw_on_change=True, restart=lambda: restarts.append(1) or 0,
        refresh_openclaw_service=lambda: 0)
    assert sync.sync_clients(**args)["openclaw_restarted"] is True
    assert sync.sync_clients(**args)["openclaw_restarted"] is False
    assert restarts == [1]
