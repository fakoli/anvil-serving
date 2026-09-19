"""Explicit client omissions preserve the router and shared compaction safety."""
import copy
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


def test_pi_explicit_omission_preserves_primary_and_compaction():
    source = catalog(); before = copy.deepcopy(source)
    with pytest.raises(sync.ClientCatalogError, match="smallest selected context"):
        sync._render_pi_documents(source, {}, settings(), base_url="http://127.0.0.1:8000/v1", api_key_env="KEY")
    models, policy = sync._render_pi_documents(source, {}, settings(), base_url="http://127.0.0.1:8000/v1", api_key_env="KEY", exclude_aliases="llm.secondary")
    assert [r["id"] for r in models["providers"]["anvil"]["models"]] == ["llm.primary"]
    assert models["providers"]["anvil"]["models"][0]["maxTokens"] == 65536
    assert policy["enabledModels"] == ["other/model", "anvil/llm.primary"]
    assert policy["compaction"] == settings()["compaction"]
    assert source == before
    assert sync._render_pi_documents(source, models, policy, base_url="http://127.0.0.1:8000/v1", api_key_env="KEY", exclude_aliases="llm.secondary") == (models, policy)


@pytest.mark.parametrize("excluded", ["llm.primary", "unknown", "llm.secondary,llm.secondary", [], None])
def test_invalid_alias_policy_fails(excluded):
    with pytest.raises(sync.ClientCatalogError):
        sync._excluded_aliases(excluded, catalog()["models"])


def test_pi_selected_model_cannot_be_removed():
    policy = settings(); policy["defaultModel"] = "llm.secondary"
    with pytest.raises(sync.ClientCatalogError, match="default Anvil model"):
        sync._render_pi_documents(catalog(), {}, policy, base_url="http://127.0.0.1:8000/v1", api_key_env="KEY", exclude_aliases="llm.secondary")


def openclaw():
    return {"models": {"providers": {"anvil": {"apiKey": "$KEY", "models": []}}},
            "agents": {"defaults": {"models": {"anvil/llm.secondary": {}, "other/model": {}},
                "model": {"primary": "anvil/llm.primary"},
                "compaction": {"mode": "safeguard", "reserveTokens": 65536, "keepRecentTokens": 20000}}}}


def test_openclaw_explicit_omission_keeps_primary_limits():
    doc = sync._render_openclaw_document(catalog(), openclaw(), exclude_aliases="llm.secondary")
    assert [r["id"] for r in doc["models"]["providers"]["anvil"]["models"]] == ["llm.primary"]
    assert doc["agents"]["defaults"]["models"] == {"other/model": {}, "anvil/llm.primary": {}}
    assert doc["agents"]["defaults"]["compaction"] == openclaw()["agents"]["defaults"]["compaction"]


def test_openclaw_omission_removes_allowlist_entry_without_changing_selection():
    source = openclaw()
    source["agents"]["defaults"]["modelPolicy"] = {"allow": ["other/model", "anvil/llm.primary", "anvil/llm.secondary"]}
    doc = sync._render_openclaw_document(catalog(), source, exclude_aliases="llm.secondary")
    assert doc["agents"]["defaults"]["modelPolicy"]["allow"] == ["other/model", "anvil/llm.primary"]
    assert doc["agents"]["defaults"]["model"] == source["agents"]["defaults"]["model"]
    assert source["agents"]["defaults"]["modelPolicy"]["allow"][-1] == "anvil/llm.secondary"
    source["agents"]["defaults"]["modelPolicy"]["allow"] = ["anvil/llm.secondary"]
    with pytest.raises(sync.ClientCatalogError, match="unrestricted"):
        sync._render_openclaw_document(catalog(), source, exclude_aliases="llm.secondary")


@pytest.mark.parametrize("location", ["default", "fallback", "agent", "talk"])
def test_openclaw_selected_references_cannot_be_removed(location):
    doc = openclaw()
    if location == "default": doc["agents"]["defaults"]["model"] = "anvil/llm.secondary"
    if location == "fallback": doc["agents"]["defaults"]["model"]["fallbacks"] = ["anvil/llm.secondary"]
    if location == "agent": doc["agents"]["list"] = [{"model": "anvil/llm.secondary"}]
    if location == "talk": doc["talk"] = {"consultModel": "anvil/llm.secondary"}
    with pytest.raises(sync.ClientCatalogError, match="references an excluded alias"):
        sync._render_openclaw_document(catalog(), doc, exclude_aliases="llm.secondary")


def test_pi_sync_applies_policy_with_backup_and_repeat_convergence(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "fetch_client_catalog", lambda **kw: catalog())
    models = tmp_path / "models.json"; models.write_text("{}")
    config = tmp_path / "settings.json"; config.write_text(json.dumps(settings()))
    args = dict(base_url="http://127.0.0.1:8000/v1", clients="pi", pi_models=str(models),
                pi_settings=str(config), state_path=str(tmp_path / "state.json"),
                backup_root=str(tmp_path / "backups"), pi_exclude_aliases="llm.secondary")
    preview = sync.sync_clients(**args)
    assert preview["client_excluded_aliases"]["pi"] == ["llm.secondary"]
    assert json.loads(models.read_text()) == {}
    result = sync.sync_clients(**args, confirm=True, dry_run=False)
    assert result["backup_created"]
    assert sync.sync_clients(**args)["changed"] == []


def test_cli_and_mcp_forward_explicit_policy(monkeypatch):
    from anvil_serving import harness
    from anvil_serving.control_plane.mcp.tools import openclaw as tool
    received = []
    monkeypatch.setattr(sync, "sync_clients", lambda **kw: received.append(kw) or {})
    assert harness.main(["sync", "clients", "--base-url", "http://127.0.0.1:8000/v1",
        "--pi-exclude-aliases", "llm.secondary", "--openclaw-exclude-aliases", "llm.secondary",
        "--dry-run"]) == 0
    tool.tool_client_catalog_sync({"base_url": "http://127.0.0.1:8000/v1",
        "pi_exclude_aliases": "llm.secondary", "openclaw_exclude_aliases": "llm.secondary",
        "dry_run": True})
    assert len(received) == 2
    for row in received:
        assert row["pi_exclude_aliases"] == row["openclaw_exclude_aliases"] == "llm.secondary"


@pytest.mark.parametrize("file_already_matches", [False, True])
def test_policy_restart_remains_pending_across_apply_and_other_client(tmp_path, monkeypatch, file_already_matches):
    source = catalog()
    source["models"]["llm.secondary"]["context_window"] = 131072
    monkeypatch.setattr(sync, "fetch_client_catalog", lambda **kw: source)
    path = tmp_path / "openclaw.json"
    doc = openclaw()
    if file_already_matches:
        doc = sync._render_openclaw_document(source, doc, exclude_aliases="llm.secondary")
    path.write_text(json.dumps(doc))
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"client_excluded_aliases": {"openclaw": []},
        "openclaw_restarted_sha256": source["config_sha256"],
        "openclaw_service_restarted_sha256": source["config_sha256"]}))
    args = dict(base_url="http://127.0.0.1:8000/v1", clients="openclaw",
        openclaw_config=str(path), state_path=str(state), backup_root=str(tmp_path / "backups"),
        environ={"ANVIL_ROUTER_TOKEN": "test-only"}, confirm=True, dry_run=False,
        openclaw_exclude_aliases="llm.secondary")
    sync.sync_clients(**args)
    assert json.loads(state.read_text())["openclaw_restarted_sha256"] is None
    pi_models = tmp_path / "models.json"; pi_models.write_text("{}")
    pi_settings = tmp_path / "settings.json"; pi_settings.write_text(json.dumps(settings()))
    sync.sync_clients(**{**args, "clients": "pi", "pi_models": str(pi_models),
        "pi_settings": str(pi_settings), "pi_exclude_aliases": "llm.secondary"})
    assert json.loads(state.read_text())["client_excluded_aliases"] == {
        "openclaw": ["llm.secondary"], "pi": ["llm.secondary"]}
    restarts = []
    args.update(restart_openclaw_on_change=True, restart=lambda: restarts.append(1) or 0,
        refresh_openclaw_service=lambda: 0)
    assert sync.sync_clients(**args)["openclaw_restarted"] is True
    assert sync.sync_clients(**args)["openclaw_restarted"] is False
    assert restarts == [1]
