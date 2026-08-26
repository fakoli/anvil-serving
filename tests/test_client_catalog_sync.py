from __future__ import annotations

import json
from pathlib import Path

import pytest

from anvil_serving.client_catalog_sync import ClientCatalogError, sync_clients


CONFIG_SHA = "a" * 64


class _Response:
    def __init__(self, payload):
        self.raw = json.dumps(payload).encode()
        self.headers = {"Content-Length": str(len(self.raw))}

    def read(self, size=-1):
        return self.raw[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Opener:
    def __init__(self, status, capabilities):
        self.payloads = [status, capabilities]
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return _Response(self.payloads.pop(0))


def _catalog(*, missing_output=False, config_sha=CONFIG_SHA, primary_context=1_048_576):
    models = [
        ("primary", ["llm.primary"], primary_context, ["text"], True),
        ("secondary", ["llm.secondary", "vision.general", "vision.ocr"], 131_072, ["text", "image"], True),
        ("voice", ["llm.voice"], 32_768, ["text"], False),
        ("aux", ["llm.auxiliary"], 65_536, ["text"], False),
    ]
    rows = []
    aliases = []
    for tier, routed, context, modalities, reasoning in models:
        aliases.extend(routed)
        rows.append({
            "object": "model_capabilities",
            "id": tier,
            "aliases": routed,
            "context_limit_tokens": context,
            "modalities": modalities,
            "thinking": {"supported": reasoning},
            "compat": {"supportsUsageInStreaming": True},
            "limits": {"max_output_tokens": None if missing_output else 8192},
        })
    return (
        {
            "object": "router_status",
            "package_version": "0.34.3",
            "config_sha256": config_sha,
            "model_aliases": sorted(aliases),
        },
        {"object": "list", "data": rows},
    )


def _write_inputs(root: Path, *, bad_pi_compaction=False):
    openclaw = root / "openclaw.json"
    pi_models = root / "models.json"
    pi_settings = root / "settings.json"
    openclaw.write_text(json.dumps({
        "models": {"mode": "merge", "providers": {
            "anvil": {
                "baseUrl": "https://router.example.ts.net/v1",
                "api": "openai-completions",
                "apiKey": {"source": "env", "id": "ROUTER_TOKEN"},
                "customProviderKey": "preserve",
                "models": [],
            },
            "other": {"models": [{"id": "other"}]},
        }},
        "agents": {"defaults": {
            "models": {"other/model": {}, "anvil/stale": {}},
            "model": {"primary": "anvil/llm.primary"},
            "imageModel": {"primary": "anvil/stale", "fallbacks": ["other/image"]},
            "compaction": {
                "mode": "safeguard",
                "reserveTokens": 50_000,
                "reserveTokensFloor": 50_000,
                "keepRecentTokens": 30_000,
                "memoryFlush": {"model": "anvil/llm.primary"},
            },
        }},
        "unrelated": {"secretRef": {"source": "file", "id": "/key"}},
    }), encoding="utf-8")
    pi_models.write_text(json.dumps({
        "providers": {
            "anvil": {
                "baseUrl": "https://router.example.ts.net/v1",
                "apiKey": "ROUTER_TOKEN",
                "authHeader": True,
                "compat": {"supportsUsageInStreaming": True},
                "models": [{"id": "llm.primary", "name": "Primary name", "custom": 1}],
            },
            "other": {"models": []},
        }
    }), encoding="utf-8")
    pi_settings.write_text(json.dumps({
        "defaultProvider": "anvil",
        "defaultModel": "llm.primary",
        "enabledModels": ["other/model", "anvil/stale"],
        "compaction": {
            "enabled": not bad_pi_compaction,
            "reserveTokens": 16_384,
            "keepRecentTokens": 20_000,
        },
        "theme": "dark",
    }), encoding="utf-8")
    return openclaw, pi_models, pi_settings


def _run(
    root: Path,
    *,
    opener,
    clients="openclaw,pi",
    confirm=False,
    dry_run=True,
    restart=None,
    restart_on_change=False,
):
    openclaw, pi_models, pi_settings = (
        root / "openclaw.json",
        root / "models.json",
        root / "settings.json",
    )
    return sync_clients(
        base_url="https://router.example.ts.net/v1",
        clients=clients,
        openclaw_config=str(openclaw),
        pi_models=str(pi_models),
        pi_settings=str(pi_settings),
        state_path=str(root / "state.json"),
        backup_root=str(root / "backups"),
        dry_run=dry_run,
        confirm=confirm,
        environ={"ANVIL_ROUTER_TOKEN": "secret-never-returned"},
        opener=opener,
        restart=restart,
        restart_openclaw_on_change=restart_on_change,
    )


def test_preview_is_sanitized_and_never_writes(tmp_path):
    paths = _write_inputs(tmp_path)
    before = [path.read_bytes() for path in paths]
    result = _run(tmp_path, opener=_Opener(*_catalog()))
    assert result["dry_run"] is True
    assert result["config_sha256"] == CONFIG_SHA
    assert {row["id"] for row in result["models"]} >= {"llm.primary", "llm.secondary"}
    assert "secret-never-returned" not in json.dumps(result)
    assert before == [path.read_bytes() for path in paths]
    assert not (tmp_path / "state.json").exists()


def test_apply_preserves_credentials_and_compaction_and_is_idempotent(tmp_path):
    openclaw_path, pi_models_path, pi_settings_path = _write_inputs(tmp_path)
    result = _run(
        tmp_path,
        opener=_Opener(*_catalog()),
        confirm=True,
        dry_run=False,
    )
    assert result["changed"] == ["openclaw", "pi_models", "pi_settings"]
    assert result["backup_created"] is True
    openclaw = json.loads(openclaw_path.read_text())
    provider = openclaw["models"]["providers"]["anvil"]
    assert provider["apiKey"] == {"source": "env", "id": "ROUTER_TOKEN"}
    assert provider["customProviderKey"] == "preserve"
    by_id = {row["id"]: row for row in provider["models"]}
    assert by_id["llm.primary"]["contextWindow"] == 1_048_576
    assert by_id["llm.secondary"]["contextWindow"] == 131_072
    assert by_id["llm.voice"]["contextWindow"] == 32_768
    assert "llm.auxiliary" not in by_id
    assert openclaw["agents"]["defaults"]["compaction"]["memoryFlush"] == {
        "model": "anvil/llm.primary"
    }
    assert openclaw["models"]["providers"]["other"]["models"] == [{"id": "other"}]
    pi_models = json.loads(pi_models_path.read_text())
    pi_provider = pi_models["providers"]["anvil"]
    assert pi_provider["apiKey"] == "ROUTER_TOKEN"
    assert pi_provider["authHeader"] is True
    pi_by_id = {row["id"]: row for row in pi_provider["models"]}
    assert pi_by_id["llm.primary"]["name"] == "Primary name"
    assert pi_by_id["llm.primary"]["custom"] == 1
    assert pi_by_id["llm.secondary"]["contextWindow"] == 131_072
    pi_settings = json.loads(pi_settings_path.read_text())
    assert pi_settings["theme"] == "dark"
    assert pi_settings["enabledModels"] == [
        "other/model",
        "anvil/llm.primary",
        "anvil/llm.secondary",
        "anvil/vision.general",
        "anvil/vision.ocr",
    ]

    second = _run(
        tmp_path,
        opener=_Opener(*_catalog()),
        confirm=True,
        dry_run=False,
    )
    assert second["changed"] == []
    assert second["backup_created"] is False
    assert len(list((tmp_path / "backups").iterdir())) == 1


def test_config_hash_restart_is_retried_once_and_drift_is_repaired(tmp_path):
    openclaw_path, _, _ = _write_inputs(tmp_path)
    restarts = []
    first = _run(
        tmp_path,
        opener=_Opener(*_catalog()),
        confirm=True,
        dry_run=False,
        restart=lambda: restarts.append("restart") or 0,
        restart_on_change=True,
    )
    assert first["openclaw_restarted"] is True
    second = _run(
        tmp_path,
        opener=_Opener(*_catalog()),
        confirm=True,
        dry_run=False,
        restart=lambda: restarts.append("restart") or 0,
        restart_on_change=True,
    )
    assert second["openclaw_restarted"] is False
    assert restarts == ["restart"]

    payload = json.loads(openclaw_path.read_text())
    payload["models"]["providers"]["anvil"]["models"][0]["contextWindow"] = 1
    openclaw_path.write_text(json.dumps(payload), encoding="utf-8")
    repaired = _run(
        tmp_path,
        opener=_Opener(*_catalog()),
        confirm=True,
        dry_run=False,
        restart=lambda: restarts.append("restart") or 0,
        restart_on_change=True,
    )
    assert repaired["changed"] == ["openclaw"]
    assert restarts == ["restart"]


def test_openclaw_only_accepts_current_safeguard_schema_without_pi(tmp_path):
    openclaw_path, pi_models_path, pi_settings_path = _write_inputs(tmp_path)
    payload = json.loads(openclaw_path.read_text())
    payload["agents"]["defaults"]["compaction"] = {
        "mode": "safeguard",
        "keepRecentTokens": 30_000,
        "maxActiveTranscriptBytes": "20mb",
        "memoryFlush": {
            "forceFlushTranscriptBytes": "15mb",
            "model": "anvil/llm.primary",
        },
        "notifyUser": True,
    }
    openclaw_path.write_text(json.dumps(payload), encoding="utf-8")
    pi_models_path.unlink()
    pi_settings_path.unlink()

    result = _run(
        tmp_path,
        clients="openclaw",
        opener=_Opener(*_catalog(primary_context=262_144)),
        confirm=True,
        dry_run=False,
    )

    assert result["clients"] == ["openclaw"]
    assert result["changed"] == ["openclaw"]
    rendered = json.loads(openclaw_path.read_text())
    primary = next(
        row
        for row in rendered["models"]["providers"]["anvil"]["models"]
        if row["id"] == "llm.primary"
    )
    assert primary["contextWindow"] == 262_144
    assert rendered["agents"]["defaults"]["compaction"] == payload["agents"]["defaults"]["compaction"]


def test_pi_only_does_not_require_or_restart_openclaw(tmp_path):
    openclaw_path, _, _ = _write_inputs(tmp_path)
    openclaw_path.unlink()
    restarts = []

    result = _run(
        tmp_path,
        clients="pi",
        opener=_Opener(*_catalog()),
        confirm=True,
        dry_run=False,
        restart=lambda: restarts.append("restart") or 0,
        restart_on_change=True,
    )

    assert result["clients"] == ["pi"]
    assert result["changed"] == ["pi_models", "pi_settings"]
    assert result["openclaw_restarted"] is False
    assert restarts == []


def test_pi_only_seeds_missing_anvil_provider_from_router_contract(tmp_path):
    _, pi_models_path, _ = _write_inputs(tmp_path)
    pi_models = json.loads(pi_models_path.read_text())
    pi_models["providers"].pop("anvil")
    pi_models_path.write_text(json.dumps(pi_models), encoding="utf-8")

    result = _run(
        tmp_path,
        clients="pi",
        opener=_Opener(*_catalog(primary_context=262_144)),
        confirm=True,
        dry_run=False,
    )

    assert result["changed"] == ["pi_models", "pi_settings"]
    rendered = json.loads(pi_models_path.read_text())
    provider = rendered["providers"]["anvil"]
    assert provider["baseUrl"] == "https://router.example.ts.net/v1"
    assert provider["apiKey"] == "$ANVIL_ROUTER_TOKEN"
    assert provider["authHeader"] is True
    assert provider["api"] == "openai-completions"
    assert provider["compat"]["maxTokensField"] == "max_tokens"
    assert provider["compat"]["supportsUsageInStreaming"] is True
    by_id = {row["id"]: row for row in provider["models"]}
    assert by_id["llm.primary"]["contextWindow"] == 262_144
    assert by_id["llm.primary"]["maxTokens"] == 8192


def test_pi_only_repairs_bare_api_key_environment_name(tmp_path):
    _, pi_models_path, _ = _write_inputs(tmp_path)
    pi_models = json.loads(pi_models_path.read_text())
    pi_models["providers"]["anvil"]["apiKey"] = "ANVIL_ROUTER_TOKEN"
    pi_models_path.write_text(json.dumps(pi_models), encoding="utf-8")

    _run(
        tmp_path,
        clients="pi",
        opener=_Opener(*_catalog()),
        confirm=True,
        dry_run=False,
    )

    rendered = json.loads(pi_models_path.read_text())
    assert rendered["providers"]["anvil"]["apiKey"] == "$ANVIL_ROUTER_TOKEN"


def test_invalid_client_selection_fails_before_metadata_request(tmp_path):
    opener = _Opener(*_catalog())
    with pytest.raises(ClientCatalogError, match="clients must select"):
        _run(tmp_path, clients="openclaw,unknown", opener=opener)
    assert opener.requests == []


def test_pi_still_requires_explicit_compaction_reserve(tmp_path):
    _, _, pi_settings_path = _write_inputs(tmp_path)
    payload = json.loads(pi_settings_path.read_text())
    payload["compaction"].pop("reserveTokens")
    pi_settings_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ClientCatalogError, match="Pi compaction reserveTokens"):
        _run(tmp_path, clients="pi", opener=_Opener(*_catalog()))


def test_missing_output_or_incompatible_compaction_fails_before_write(tmp_path):
    paths = _write_inputs(tmp_path)
    before = [path.read_bytes() for path in paths]
    with pytest.raises(ClientCatalogError, match="max_output_tokens"):
        _run(
            tmp_path,
            opener=_Opener(*_catalog(missing_output=True)),
            confirm=True,
            dry_run=False,
        )
    assert before == [path.read_bytes() for path in paths]

    _write_inputs(tmp_path, bad_pi_compaction=True)
    with pytest.raises(ClientCatalogError, match="Pi compaction"):
        _run(
            tmp_path,
            opener=_Opener(*_catalog()),
            confirm=True,
            dry_run=False,
        )


def test_public_https_hostname_is_refused_before_credential_dispatch(tmp_path):
    _write_inputs(tmp_path)
    opener = _Opener(*_catalog())
    with pytest.raises(ClientCatalogError, match="tailnet"):
        sync_clients(
            base_url="https://public.example/v1",
            openclaw_config=str(tmp_path / "openclaw.json"),
            pi_models=str(tmp_path / "models.json"),
            pi_settings=str(tmp_path / "settings.json"),
            state_path=str(tmp_path / "state.json"),
            backup_root=str(tmp_path / "backups"),
            environ={"ANVIL_ROUTER_TOKEN": "never-dispatched"},
            opener=opener,
        )
    assert opener.requests == []
