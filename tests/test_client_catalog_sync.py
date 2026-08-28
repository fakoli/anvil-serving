from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving.client_catalog_sync import (
    ClientCatalogError,
    sync_clients,
    sync_hermes_media,
)


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


class _HermesRunner:
    def __init__(self, states):
        self.states = states
        self.sets = []

    @staticmethod
    def _completed(*, returncode=0, stdout="", stderr=""):
        return SimpleNamespace(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def __call__(self, argv, **_kwargs):
        profile = argv[2]
        command = argv[3:]
        if command[:2] == ["config", "get"]:
            key = command[2]
            if key not in self.states[profile]:
                return self._completed(returncode=1, stderr="missing")
            return self._completed(stdout=json.dumps(self.states[profile][key]))
        if command[:2] == ["config", "set"]:
            key, raw = command[2:4]
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                value = raw
            if key.startswith("providers."):
                target = self.states[profile].setdefault("providers", {})
                parts = key.split(".")[1:]
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                target[parts[-1]] = value
            else:
                section, _, field = key.rpartition(".")
                if section:
                    self.states[profile].setdefault(section, {})[field] = value
                else:
                    self.states[profile][key] = value
            self.sets.append((profile, key))
            return self._completed()
        if command[:2] == ["config", "unset"]:
            key = command[2]
            target = self.states[profile]
            parts = key.split(".")
            for part in parts[:-1]:
                target = target[part]
            target.pop(parts[-1], None)
            self.sets.append((profile, key))
            return self._completed()
        if command == ["config", "check"]:
            return self._completed(stdout="valid")
        raise AssertionError("unexpected Hermes command: %r" % argv)


class _HermesMediaRunner:
    def __init__(self, profiles):
        self.servers = {profile: None for profile in profiles}
        self.sets = []

    def __call__(self, argv, **_kwargs):
        profile = argv[2]
        command = argv[3:]
        if command[:3] == ["config", "get", "mcp_servers.anvil-media"]:
            value = self.servers[profile]
            if value is None:
                return SimpleNamespace(returncode=1, stdout="", stderr="missing")
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(value),
                stderr="",
            )
        if command[:3] == ["config", "set", "mcp_servers.anvil-media"]:
            self.servers[profile] = json.loads(command[3])
            self.sets.append(profile)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command == ["config", "check"]:
            return SimpleNamespace(returncode=0, stdout="valid", stderr="")
        raise AssertionError("unexpected Hermes media command: %r" % argv)


class _WritingHermesMediaRunner(_HermesMediaRunner):
    def __init__(self, profiles, config_paths, *, fail_profile=None):
        super().__init__(profiles)
        self.config_paths = config_paths
        self.fail_profile = fail_profile

    def __call__(self, argv, **kwargs):
        profile = argv[2]
        command = argv[3:]
        if command[:3] == ["config", "set", "mcp_servers.anvil-media"]:
            if profile == self.fail_profile:
                return SimpleNamespace(returncode=1, stdout="", stderr="failed")
            self.config_paths[profile].write_text(
                "profile: %s\nmedia: configured\n" % profile,
                encoding="utf-8",
            )
        return super().__call__(argv, **kwargs)


def _write_hermes_profiles(root: Path):
    home = root / "hermes-home"
    (home / "profiles").mkdir(parents=True)
    profiles = (
        "default",
        "anvil-primary",
        "anvil-secondary",
        "ox-alpha",
        "work-profile",
    )
    for profile in profiles:
        path = (
            home / "config.yaml"
            if profile == "default"
            else home / "profiles" / profile / "config.yaml"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("profile: %s\n" % profile, encoding="utf-8")

    def anvil_state(alias, max_tokens):
        return {
            "model": {
                "provider": "anvil",
                "default": alias,
                "max_tokens": max_tokens,
            },
            "compression": {
                "enabled": True,
                "threshold": 0.5,
                "target_ratio": 0.2,
            },
            "auxiliary.vision": {
                "provider": "anvil",
                "model": "vision.general",
            },
            "auxiliary.compression": {"provider": "auto"},
            "providers": {
                "anvil": {
                    "default_model": "llm.primary",
                    "context_length": 393_216,
                    "extra_body": {
                        "chat_template_kwargs": {"enable_thinking": False}
                    },
                    "models": {
                        "llm.primary": {"context_length": 393_216},
                        "llm.secondary": {"context_length": 131_072},
                        "retired.alias": {"context_length": 1},
                    },
                }
            },
            "custom_providers": [
                {
                    "name": "anvil-primary",
                    "base_url": "https://router.example.ts.net/v1",
                    "key_env": "ANVIL_ROUTER_TOKEN",
                    "model": "llm.primary",
                    "models": {
                        "llm.primary": {"context_length": 393_216},
                        "retired.alias": {"context_length": 1},
                    },
                }
            ],
        }

    states = {
        "default": anvil_state("llm.primary", 8192),
        "anvil-primary": anvil_state("llm.primary", 5120),
        "anvil-secondary": anvil_state("llm.secondary", 5120),
        "work-profile": anvil_state("llm.secondary", 5120),
        "ox-alpha": {
            "model": {
                "provider": "openrouter",
                "default": "stealth/ox-alpha",
                "max_tokens": 4096,
            },
            "compression": {
                "enabled": True,
                "threshold": 0.5,
                "target_ratio": 0.2,
            },
            "providers": {"openrouter": {}},
        },
    }
    return home, states


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


def _write_hermes(root: Path):
    hermes = root / "hermes.yaml"
    hermes.write_text(
        """model:
  default: llm.primary
  provider: anvil
  max_tokens: 5120
custom_providers:
  - name: preserve-me
    model: llm.primary
    models:
      llm.primary:
        context_length: 393216
providers:
  anvil:
    key_env: ANVIL_ROUTER_TOKEN
    default_model: llm.primary
    context_length: 393216
    models:
      llm.primary:
        context_length: 393216
      llm.secondary:
        context_length: 131072
unrelated:
  enabled: true
""",
        encoding="utf-8",
    )
    return hermes


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
        hermes_config=str(root / "hermes.yaml"),
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


def test_hermes_only_repairs_selected_limits_and_preserves_other_yaml(tmp_path):
    _write_inputs(tmp_path)
    hermes_path = _write_hermes(tmp_path)

    result = _run(
        tmp_path,
        clients="hermes",
        opener=_Opener(*_catalog(primary_context=262_144)),
        confirm=True,
        dry_run=False,
    )

    assert result["clients"] == ["hermes"]
    assert result["changed"] == ["hermes"]
    rendered = hermes_path.read_text(encoding="utf-8")
    assert "  max_tokens: 8192\n" in rendered
    assert "    context_length: 262144\n" in rendered
    assert "        context_length: 262144\n" in rendered
    assert "      llm.secondary:\n        context_length: 131072\n" in rendered
    assert "  - name: preserve-me" in rendered
    assert "        context_length: 393216\nproviders:" in rendered
    assert "unrelated:\n  enabled: true\n" in rendered

    second = _run(
        tmp_path,
        clients="hermes",
        opener=_Opener(*_catalog(primary_context=262_144)),
        confirm=True,
        dry_run=False,
    )
    assert second["changed"] == []
    assert second["backup_created"] is False


def test_hermes_wrong_selected_provider_fails_before_write(tmp_path):
    _write_inputs(tmp_path)
    hermes_path = _write_hermes(tmp_path)
    source = hermes_path.read_text(encoding="utf-8").replace(
        "  provider: anvil\n", "  provider: other\n"
    )
    hermes_path.write_text(source, encoding="utf-8")

    with pytest.raises(ClientCatalogError, match="selected provider"):
        _run(
            tmp_path,
            clients="hermes",
            opener=_Opener(*_catalog(primary_context=262_144)),
            confirm=True,
            dry_run=False,
        )
    assert hermes_path.read_text(encoding="utf-8") == source


def test_hermes_profile_sync_repairs_each_anvil_contract_and_skips_external(tmp_path):
    _write_inputs(tmp_path)
    home, states = _write_hermes_profiles(tmp_path)
    runner = _HermesRunner(states)

    preview = sync_clients(
        base_url="https://router.example.ts.net/v1",
        clients="hermes",
        hermes_bin="hermes",
        hermes_home=str(home),
        hermes_profiles="all",
        state_path=str(tmp_path / "state.json"),
        backup_root=str(tmp_path / "backups"),
        environ={"ANVIL_ROUTER_TOKEN": "secret-never-returned"},
        opener=_Opener(*_catalog(primary_context=262_144)),
        hermes_run=runner,
    )

    assert preview["changed"] == [
        "hermes:default",
        "hermes:anvil-primary",
        "hermes:anvil-secondary",
        "hermes:work-profile",
    ]
    by_profile = {row["profile"]: row for row in preview["hermes_profiles"]}
    assert by_profile["default"]["context_window"] == 262_144
    assert by_profile["anvil-secondary"]["context_window"] == 131_072
    assert by_profile["ox-alpha"] == {
        "profile": "ox-alpha",
        "managed": False,
        "provider": "openrouter",
        "model": "stealth/ox-alpha",
        "changed_keys": [],
    }
    assert "secret-never-returned" not in json.dumps(preview)
    assert runner.sets == []

    restarts = []
    applied = sync_clients(
        base_url="https://router.example.ts.net/v1",
        clients="hermes",
        hermes_bin="hermes",
        hermes_home=str(home),
        hermes_profiles="all",
        state_path=str(tmp_path / "state.json"),
        backup_root=str(tmp_path / "backups"),
        restart_hermes_on_change=True,
        dry_run=False,
        confirm=True,
        environ={"ANVIL_ROUTER_TOKEN": "secret-never-returned"},
        opener=_Opener(*_catalog(primary_context=262_144)),
        hermes_run=runner,
        restart_hermes=lambda: restarts.append("default") or 0,
    )

    assert applied["hermes_restarted"] is True
    assert applied["backup_created"] is True
    assert restarts == ["default"]
    assert states["default"]["model"]["context_length"] == 262_144
    assert states["anvil-primary"]["model"]["max_tokens"] == 8192
    assert states["anvil-secondary"]["model"]["context_length"] == 131_072
    assert states["work-profile"]["auxiliary.compression"]["context_length"] == 131_072
    assert states["ox-alpha"]["model"]["max_tokens"] == 4096
    custom = states["anvil-secondary"]["custom_providers"][0]
    assert custom["model"] == "llm.secondary"
    assert custom["models"]["llm.primary"]["context_length"] == 262_144
    assert custom["models"]["vision.general"]["context_length"] == 131_072
    assert "retired.alias" not in custom["models"]
    provider = states["anvil-secondary"]["providers"]["anvil"]
    assert provider["default_model"] == "llm.secondary"
    assert provider["context_length"] == 131_072
    assert provider["models"] == {
        "llm.primary": {"context_length": 262_144},
        "llm.secondary": {"context_length": 131_072},
    }
    assert "extra_body" not in provider

    second = sync_clients(
        base_url="https://router.example.ts.net/v1",
        clients="hermes",
        hermes_bin="hermes",
        hermes_home=str(home),
        hermes_profiles="all",
        state_path=str(tmp_path / "state.json"),
        backup_root=str(tmp_path / "backups"),
        restart_hermes_on_change=True,
        dry_run=False,
        confirm=True,
        environ={"ANVIL_ROUTER_TOKEN": "secret-never-returned"},
        opener=_Opener(*_catalog(primary_context=262_144)),
        hermes_run=runner,
        restart_hermes=lambda: pytest.fail("idempotent sync restarted Hermes"),
    )
    assert second["changed"] == []
    assert second["backup_created"] is False
    assert second["hermes_restarted"] is False


def test_hermes_profile_sync_rejects_unsafe_compaction_before_write(tmp_path):
    _write_inputs(tmp_path)
    home, states = _write_hermes_profiles(tmp_path)
    states["anvil-primary"]["compression"]["target_ratio"] = 0.6
    runner = _HermesRunner(states)

    with pytest.raises(ClientCatalogError, match="target_ratio must remain below"):
        sync_clients(
            base_url="https://router.example.ts.net/v1",
            clients="hermes",
            hermes_bin="hermes",
            hermes_home=str(home),
            hermes_profiles="all",
            state_path=str(tmp_path / "state.json"),
            backup_root=str(tmp_path / "backups"),
            dry_run=False,
            confirm=True,
            environ={"ANVIL_ROUTER_TOKEN": "secret-never-returned"},
            opener=_Opener(*_catalog(primary_context=262_144)),
            hermes_run=runner,
        )
    assert runner.sets == []
    assert not (tmp_path / "state.json").exists()


def test_hermes_media_sync_installs_scoped_mcp_and_packaged_skill_idempotently(
    tmp_path,
):
    home, _ = _write_hermes_profiles(tmp_path)
    profiles = ("default", "anvil-primary")
    runner = _HermesMediaRunner(profiles)
    skill_path = home / "skills" / "anvil-media" / "SKILL.md"
    kwargs = {
        "hermes_bin": "hermes",
        "hermes_home": str(home),
        "hermes_profiles": ",".join(profiles),
        "skill_path": str(skill_path),
        "backup_root": str(tmp_path / "backups-media"),
        "run": runner,
    }

    preview = sync_hermes_media(**kwargs)
    assert preview["changed"] == [
        "skill",
        "hermes:default",
        "hermes:anvil-primary",
    ]
    assert preview["dryRun"] is True
    assert not skill_path.exists()
    assert runner.sets == []

    restarts = []
    applied = sync_hermes_media(
        **kwargs,
        dry_run=False,
        confirm=True,
        restart_hermes_on_change=True,
        restart_hermes=lambda: restarts.append("restart") or 0,
    )
    assert applied["backupCreated"] is True
    assert applied["hermesRestarted"] is True
    assert restarts == ["restart"]
    assert skill_path.read_bytes() == (
        Path(__file__).parents[1]
        / "examples"
        / "hermes"
        / "skills"
        / "anvil-media"
        / "SKILL.md"
    ).read_bytes()
    server = runner.servers["default"]
    assert server == runner.servers["anvil-primary"]
    assert server["args"][3] == "${ANVIL_MEDIA_MCP_URL}"
    assert server["env"] == {
        "ANVIL_CONTROLLER_TOKEN": "${ANVIL_CONTROLLER_TOKEN}"
    }
    assert server["tools"]["resources"] is False
    assert server["tools"]["prompts"] is False
    assert set(server["tools"]["include"]) == set(applied["tools"])

    second = sync_hermes_media(**kwargs, dry_run=False, confirm=True)
    assert second["changed"] == []
    assert second["backupCreated"] is False
    assert second["hermesRestarted"] is False


def test_hermes_media_sync_accepts_resolved_values_only_with_raw_env_references(
    tmp_path,
):
    home, _ = _write_hermes_profiles(tmp_path)
    config = home / "config.yaml"
    config.write_text(
        """profile: default
mcp_servers:
  anvil-media:
    command: anvil-serving
    args:
      - mcp
      - serve
      - --controller-url
      - ${ANVIL_MEDIA_MCP_URL}
      - --auth-env
      - ANVIL_ROUTER_TOKEN
    env:
      ANVIL_ROUTER_TOKEN: ${ANVIL_ROUTER_TOKEN}
    tools:
      include:
        - media_capabilities
        - media_workflow_list
        - media_workflow_show
        - media_workflow_validate
        - media_workflow_run
        - media_job_status
        - media_job_cancel
        - media_artifact_inspect
      resources: false
      prompts: false
""",
        encoding="utf-8",
    )
    runner = _HermesMediaRunner(("default",))
    runner.servers["default"] = {
        "command": "anvil-serving",
        "args": [
            "mcp",
            "serve",
            "--controller-url",
            "https://router.example.ts.net/mcp",
            "--auth-env",
            "ANVIL_ROUTER_TOKEN",
        ],
        "env": {"ANVIL_ROUTER_TOKEN": "resolved-secret-never-returned"},
        "tools": {
            "include": [
                "media_capabilities",
                "media_workflow_list",
                "media_workflow_show",
                "media_workflow_validate",
                "media_workflow_run",
                "media_job_status",
                "media_job_cancel",
                "media_artifact_inspect",
            ],
            "resources": False,
            "prompts": False,
        },
    }
    kwargs = {
        "hermes_bin": "hermes",
        "hermes_home": str(home),
        "hermes_profiles": "default",
        "skill_path": str(home / "skills" / "anvil-media" / "SKILL.md"),
        "backup_root": str(tmp_path / "backups-media"),
        "token_env": "ANVIL_ROUTER_TOKEN",
        "run": runner,
    }

    preview = sync_hermes_media(**kwargs)
    assert preview["profiles"] == [{"profile": "default", "changed": False}]
    assert "resolved-secret-never-returned" not in json.dumps(preview)

    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "ANVIL_ROUTER_TOKEN: ${ANVIL_ROUTER_TOKEN}",
            "ANVIL_ROUTER_TOKEN: literal-is-not-accepted",
        ),
        encoding="utf-8",
    )
    drift = sync_hermes_media(**kwargs)
    assert drift["profiles"] == [{"profile": "default", "changed": True}]


def test_hermes_media_sync_restores_skill_and_profiles_after_partial_write(tmp_path):
    home, _ = _write_hermes_profiles(tmp_path)
    profiles = ("default", "anvil-primary")
    config_paths = {
        "default": home / "config.yaml",
        "anvil-primary": home / "profiles" / "anvil-primary" / "config.yaml",
    }
    originals = {profile: path.read_bytes() for profile, path in config_paths.items()}
    skill_path = home / "skills" / "anvil-media" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_bytes(b"previous skill\n")
    runner = _WritingHermesMediaRunner(
        profiles,
        config_paths,
        fail_profile="anvil-primary",
    )

    with pytest.raises(ClientCatalogError, match="update failed for anvil-primary"):
        sync_hermes_media(
            hermes_bin="hermes",
            hermes_home=str(home),
            hermes_profiles=",".join(profiles),
            skill_path=str(skill_path),
            backup_root=str(tmp_path / "backups-media"),
            dry_run=False,
            confirm=True,
            run=runner,
        )

    assert skill_path.read_bytes() == b"previous skill\n"
    assert {
        profile: path.read_bytes() for profile, path in config_paths.items()
    } == originals


def test_hermes_media_sync_restores_files_and_restarts_after_restart_failure(tmp_path):
    home, _ = _write_hermes_profiles(tmp_path)
    profiles = ("default", "anvil-primary")
    config_paths = {
        "default": home / "config.yaml",
        "anvil-primary": home / "profiles" / "anvil-primary" / "config.yaml",
    }
    originals = {profile: path.read_bytes() for profile, path in config_paths.items()}
    skill_path = home / "skills" / "anvil-media" / "SKILL.md"
    runner = _WritingHermesMediaRunner(profiles, config_paths)
    restart_results = iter((1, 0))
    restart_calls = []

    def restart():
        restart_calls.append("restart")
        return next(restart_results)

    with pytest.raises(ClientCatalogError, match="restored after gateway restart failed"):
        sync_hermes_media(
            hermes_bin="hermes",
            hermes_home=str(home),
            hermes_profiles=",".join(profiles),
            skill_path=str(skill_path),
            backup_root=str(tmp_path / "backups-media"),
            restart_hermes_on_change=True,
            dry_run=False,
            confirm=True,
            run=runner,
            restart_hermes=restart,
        )

    assert restart_calls == ["restart", "restart"]
    assert not skill_path.exists()
    assert {
        profile: path.read_bytes() for profile, path in config_paths.items()
    } == originals


def test_hermes_media_sync_reports_failed_rollback_restart(tmp_path):
    home, _ = _write_hermes_profiles(tmp_path)
    profiles = ("default",)
    config_paths = {"default": home / "config.yaml"}
    original = config_paths["default"].read_bytes()
    runner = _WritingHermesMediaRunner(profiles, config_paths)

    with pytest.raises(ClientCatalogError, match="restored on disk but its restart failed"):
        sync_hermes_media(
            hermes_bin="hermes",
            hermes_home=str(home),
            hermes_profiles="default",
            skill_path=str(home / "skills" / "anvil-media" / "SKILL.md"),
            backup_root=str(tmp_path / "backups-media"),
            restart_hermes_on_change=True,
            dry_run=False,
            confirm=True,
            run=runner,
            restart_hermes=lambda: 1,
        )

    assert config_paths["default"].read_bytes() == original


def test_hermes_media_sync_rejects_paths_and_secret_reference_names_before_write(
    tmp_path,
):
    home, _ = _write_hermes_profiles(tmp_path)
    runner = _HermesMediaRunner(("default",))

    with pytest.raises(ClientCatalogError, match="under the Hermes skills directory"):
        sync_hermes_media(
            hermes_bin="hermes",
            hermes_home=str(home),
            hermes_profiles="default",
            skill_path=str(tmp_path / "outside" / "SKILL.md"),
            run=runner,
        )
    with pytest.raises(ClientCatalogError, match="environment reference is invalid"):
        sync_hermes_media(
            hermes_bin="hermes",
            hermes_home=str(home),
            hermes_profiles="default",
            skill_path=str(home / "skills" / "anvil-media" / "SKILL.md"),
            token_env="TOKEN=value",
            run=runner,
        )
    assert runner.sets == []


def test_hermes_profile_sync_requires_anvil_custom_provider_before_write(tmp_path):
    _write_inputs(tmp_path)
    home, states = _write_hermes_profiles(tmp_path)
    states["anvil-primary"]["custom_providers"] = []
    runner = _HermesRunner(states)

    with pytest.raises(ClientCatalogError, match="no Anvil custom provider"):
        sync_clients(
            base_url="https://router.example.ts.net/v1",
            clients="hermes",
            hermes_bin="hermes",
            hermes_home=str(home),
            hermes_profiles="all",
            state_path=str(tmp_path / "state.json"),
            backup_root=str(tmp_path / "backups"),
            dry_run=False,
            confirm=True,
            environ={"ANVIL_ROUTER_TOKEN": "secret-never-returned"},
            opener=_Opener(*_catalog(primary_context=262_144)),
            hermes_run=runner,
        )
    assert runner.sets == []


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
