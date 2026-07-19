"""Tests for `anvil-serving harness` — render the OpenClaw harness config from the router config.

The RouterConfig loader is injected (`_load`) and ssh via `_run`, so these run with no config
file, no network, and no ssh.
"""
import json
import subprocess
import types

import pytest

from anvil_serving import harness


def _proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class _FakeSCP:
    """Fake `_run` for scp (portable transport). `remote` = the remote file's content (None =
    absent). READ (`scp host:path localtmp`) materializes `remote` to the local dest; WRITE
    (`scp localtmp host:path`) captures the payload; a `.bak` dest records a backup. `read_err`
    non-"no such file" simulates an unreachable host."""
    def __init__(self, host="mini", remote=None, read_err="", write_rc=0,
                 plugin_manifest=None, model_available=True, plugin_loaded=True):
        self.host, self.remote, self.read_err, self.write_rc = host, remote, read_err, write_rc
        self.plugin_manifest = plugin_manifest or json.dumps({
            "id": "openclaw-anvil-intent-router",
        })
        self.model_available = model_available
        self.plugin_loaded = plugin_loaded
        self.calls, self.written, self.backed_up, self.restarted = [], None, False, False
        self.kwargs = []

    def _is_remote(self, arg):
        return arg.startswith(self.host + ":")

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        self.kwargs.append(kw)
        if argv[0] == "ssh" and argv[-1] == harness._REMOTE_RESTART_COMMAND:
            self.restarted = True
            return _proc(0)
        if argv[0] == "ssh" and argv[-1] == harness._REMOTE_MODELS_VALIDATE_COMMAND:
            payload = json.loads(self.written)
            native_ref = payload["agents"]["defaults"]["model"]["primary"]
            return _proc(0, json.dumps({
                "models": [{"key": native_ref, "available": self.model_available}],
            }))
        if argv[0] == "ssh" and argv[-1] == harness._REMOTE_PLUGIN_VALIDATE_COMMAND:
            return _proc(0, json.dumps({
                "plugin": {
                    "id": "openclaw-anvil-intent-router",
                    "enabled": True,
                    "imported": self.plugin_loaded,
                    "status": "loaded" if self.plugin_loaded else "error",
                    "error": None if self.plugin_loaded else "load failed",
                    "hookCount": 1 if self.plugin_loaded else 0,
                },
            }))
        if argv[0] == "sftp":
            assert kw["input"] == "rm .openclaw/openclaw.json\n"
            self.written = None
            return _proc(0)
        src, dst = argv[-2], argv[-1]
        if self._is_remote(dst):                      # WRITE or BACKUP (dest is remote)
            if dst.endswith(".bak"):
                self.backed_up = True
                return _proc(0)
            with open(src, "r", encoding="utf-8") as f:
                self.written = f.read()
            return _proc(self.write_rc, "", "" if self.write_rc == 0 else "write failed")
        # READ (source is remote) -> materialize the remote content to the local dest
        if src.endswith(("/openclaw.plugin.json", "\\openclaw.plugin.json")):
            with open(dst, "w", encoding="utf-8") as f:
                f.write(self.plugin_manifest)
            return _proc(0)
        if self.remote is None:
            return _proc(1, "", self.read_err or "scp: %s: No such file or directory" % src)
        with open(dst, "w", encoding="utf-8") as f:
            f.write(self.remote)
        return _proc(0)


class _Tier:
    def __init__(self, context_limit):
        self.context_limit = context_limit


class _Config:
    """Minimal stand-in for RouterConfig: `.presets` + `.tier(id).context_limit`."""
    def __init__(self, presets, tiers):
        self.presets = presets
        self._tiers = tiers

    def tier(self, tid):
        return self._tiers[tid]


def _cfg():
    return _Config(
        presets={"planning": ("heavy",), "chat": ("heavy", "fast"),
                 "quick-edit": ("heavy", "fast"), "review": ("heavy",),
                 "chat-fast": ("fast", "heavy")},
        tiers={"heavy": _Tier(131072), "fast": _Tier(32768)},
    )


def _local_plugin_dir(tmp_path):
    plugin_dir = tmp_path / "openclaw-anvil-intent-router"
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "openclaw.plugin.json").write_text(
        json.dumps({"id": "openclaw-anvil-intent-router"}),
        encoding="utf-8",
    )
    return str(plugin_dir)


# ---- rendering ---------------------------------------------------------------

def test_render_one_model_per_preset_with_max_routed_context():
    prov = harness.render_openclaw_provider(_cfg(), base_url="http://x:8000/v1")
    models = {m["id"]: m for m in prov["models"]["providers"]["anvil"]["models"]}
    assert set(models) == {"planning", "chat", "quick-edit", "review", "chat-fast"}
    # contextWindow = the LARGEST tier the preset can route to (clamp gotcha)
    assert models["planning"]["contextWindow"] == 131072       # heavy only
    assert models["chat"]["contextWindow"] == 131072           # max(heavy, fast) -> heavy
    assert models["quick-edit"]["contextWindow"] == 131072
    # display name title-cases the preset id
    assert models["quick-edit"]["name"] == "Anvil · Quick Edit"
    # review advertises image input
    assert models["review"]["input"] == ["text", "image"]
    assert models["chat"]["input"] == ["text"]


def test_render_enables_reasoning_selector():
    # reasoning:true surfaces OpenClaw's per-message reasoning selector (heavy honors reasoning_effort).
    prov = harness.render_openclaw_provider(_cfg(), base_url="http://x/v1")
    assert all(m["reasoning"] is True for m in prov["models"]["providers"]["anvil"]["models"])


def test_allowlist_lists_every_preset_with_empty_params():
    # agents.defaults.models is OpenClaw's DROPDOWN ALLOWLIST — a preset shows only if listed here.
    # So every preset must appear, with EMPTY params (no stale thinking override — router owns that).
    prov = harness.render_openclaw_provider(_cfg(), base_url="http://x/v1")
    dm = prov["agents"]["defaults"]["models"]
    assert set(dm) == {
        "anvil/planning", "anvil/chat", "anvil/quick-edit", "anvil/review", "anvil/chat-fast",
    }
    assert all(v == {} for v in dm.values())               # allowlisted, no per-preset override


def test_provider_shape_and_token_by_reference():
    prov = harness.render_openclaw_provider(
        _cfg(),
        base_url="http://h:8000/v1",
        api_key_env="TOK",
        native_provider="openai",
        native_model="gpt-5.6-sol",
        plugin_dir="/opt/anvil/plugins/openclaw-anvil-intent-router",
        tool_profile="full",
        exec_mode="auto",
    )
    anvil = prov["models"]["providers"]["anvil"]
    assert anvil["baseUrl"] == "http://h:8000/v1"
    assert anvil["apiKey"] == "${TOK}"          # by name, never the secret
    assert anvil["api"] == "openai-completions"
    assert prov["models"]["mode"] == "merge"
    # the entries key MUST match the packaged plugin id, not the stale spec's "anvil-intent-router"
    entry = prov["plugins"]["entries"]["openclaw-anvil-intent-router"]
    assert entry["hooks"]["allowConversationAccess"] is True
    assert entry["enabled"] is True
    assert entry["config"] == {
        "nativeProvider": "openai",
        "nativeModel": "gpt-5.6-sol",
        "routeEndpoint": "http://h:8000/v1/route",
        "routeAuthEnv": "TOK",
        "routeTimeoutMs": 500,
    }
    assert prov["plugins"]["load"]["paths"] == [
        "/opt/anvil/plugins/openclaw-anvil-intent-router"
    ]
    assert prov["agents"]["defaults"]["model"]["primary"] == "openai/gpt-5.6-sol"
    assert prov["tools"]["profile"] == "full"
    assert prov["tools"]["exec"]["mode"] == "auto"


def test_render_without_native_route_is_a_safe_merge_fragment():
    prov = harness.render_openclaw_provider(_cfg(), base_url="http://127.0.0.1:8000/v1")
    assert "model" not in prov["agents"]["defaults"]
    config = prov["plugins"]["entries"]["openclaw-anvil-intent-router"]["config"]
    assert "nativeProvider" not in config
    assert "nativeModel" not in config
    assert config["routeEndpoint"] == "http://127.0.0.1:8000/v1/route"
    assert config["routeTimeoutMs"] == 30


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"native_provider": "openai"}, "native_provider and native_model"),
        ({"native_model": "gpt-5.6-sol"}, "native_provider and native_model"),
        ({"tool_profile": "everything"}, "tool_profile"),
        ({"exec_mode": "everything"}, "exec_mode"),
        ({"plugin_dir": "relative/plugin"}, "absolute"),
    ],
)
def test_render_rejects_incomplete_or_unsafe_setup_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        harness.render_openclaw_provider(_cfg(), base_url="http://h/v1", **kwargs)


@pytest.mark.parametrize(
    "plugin_dir",
    [
        "/opt/anvil/openclaw-anvil-intent-router",
        r"C:\\Anvil\\openclaw-anvil-intent-router",
        r"\\server\share\openclaw-anvil-intent-router",
    ],
)
def test_render_accepts_absolute_gateway_paths_independent_of_controller_os(plugin_dir):
    rendered = harness.render_openclaw_provider(
        _cfg(),
        base_url="http://h/v1",
        plugin_dir=plugin_dir,
    )

    assert rendered["plugins"]["load"]["paths"] == [plugin_dir]


def test_openclaw_voice_sync_defaults_to_chat_fast_preset():
    preview = harness.openclaw_sync_preview(
        "r.toml",
        base_url="http://100.87.34.66:8000/v1",
        voice=True,
        _load=lambda _p: _cfg(),
    )

    assert preview["voice"] is True
    assert preview["voice_model"] == "chat-fast"
    assert preview["voice_consult_model"] == "anvil/chat-fast"


def test_openclaw_voice_sync_falls_back_to_chat_model_without_chat_fast():
    cfg = _Config(
        presets={"chat": ("fast", "heavy")},
        tiers={"fast": _Tier(32768), "heavy": _Tier(131072)},
    )
    preview = harness.openclaw_sync_preview(
        "r.toml",
        base_url="http://100.87.34.66:8000/v1",
        voice=True,
        _load=lambda _p: cfg,
    )

    assert preview["voice_model"] == "chat"
    assert preview["voice_consult_model"] == "anvil/chat"


# ---- cmd_sync_openclaw -------------------------------------------------------

def test_sync_emits_valid_json_to_stdout(capsys):
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1",
                                   api_key_env="ANVIL_ROUTER_TOKEN", _load=lambda p: _cfg())
    assert rc == 0
    d = json.loads(capsys.readouterr().out)          # valid JSON
    assert len(d["models"]["providers"]["anvil"]["models"]) == 5


def test_sync_writes_out_file(tmp_path, capsys):
    p = tmp_path / "openclaw.json"
    rc = harness.cmd_sync_openclaw(
        "r.toml",
        out=str(p),
        base_url="http://h/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        native_provider="openai",
        native_model="gpt-5.6-sol",
        plugin_dir=_local_plugin_dir(tmp_path),
        tool_profile="full",
        exec_mode="auto",
        _load=lambda _p: _cfg(),
    )
    assert rc == 0
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert len(payload["models"]["providers"]["anvil"]["models"]) == 5
    assert payload["agents"]["defaults"]["model"]["primary"] == "openai/gpt-5.6-sol"
    assert payload["tools"]["profile"] == "full"
    assert payload["tools"]["exec"]["mode"] == "auto"
    assert "OpenClaw provider config" in capsys.readouterr().out


def test_sync_refuses_incomplete_fresh_gateway_config(tmp_path, capsys):
    p = tmp_path / "openclaw.json"
    rc = harness.cmd_sync_openclaw(
        "r.toml",
        out=str(p),
        base_url="http://h/v1",
        api_key_env="T",
        _load=lambda _p: _cfg(),
    )
    assert rc == 1
    assert not p.exists()
    err = capsys.readouterr().err
    assert "fresh OpenClaw setup" in err
    assert "native provider/model" in err
    assert "plugin directory" in err


def test_openclaw_sync_out_dash_emits_valid_json_to_stdout(capsys):
    rc = harness.cmd_sync_openclaw("r.toml", out="-", base_url="http://h/v1",
                                   api_key_env="ANVIL_ROUTER_TOKEN", _load=lambda p: _cfg())
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["models"]["providers"]["anvil"]["apiKey"] == "${ANVIL_ROUTER_TOKEN}"


def test_openclaw_skills_sync_render_adds_workbench_roles():
    rendered = harness.render_openclaw_skills(_cfg())
    defaults = rendered["agents"]["defaults"]
    roles = {r["name"]: r for r in rendered["agents"]["list"]}
    assert defaults["skills"] == ["anvil-serving-workbench"]
    assert roles["anvil-orchestrator"]["model"] == "anvil/planning"
    assert roles["anvil-inventory-scout"]["model"] == "anvil/chat-fast"
    assert roles["anvil-route-analyst"]["model"] == "anvil/chat-fast"
    assert roles["anvil-serve-operator"]["model"] == "anvil/chat-fast"
    assert roles["anvil-preflight-runner"]["model"] == "anvil/chat-fast"
    assert roles["anvil-benchmark-runner"]["model"] == "anvil/chat-fast"
    assert roles["anvil-evidence-reporter"]["model"] == "anvil/chat-fast"
    assert "anvil-quality-critic" not in roles
    assert "anvil-adversarial-reviewer" not in roles
    assert all(r["skills"] == ["anvil-serving-workbench"] for r in roles.values())
    assert "skills" not in rendered


def test_openclaw_strong_roles_do_not_fallback_to_small_only_preset():
    cfg = _Config(
        presets={"chat-fast": ("fast",)},
        tiers={"fast": _Tier(32768)},
    )
    rendered = harness.render_openclaw_skills(cfg)
    roles = {r["name"]: r for r in rendered["agents"]["list"]}
    assert roles["anvil-inventory-scout"]["model"] == "anvil/chat-fast"
    assert roles["anvil-orchestrator"]["model"] == "anvil/planning"
    assert "anvil-quality-critic" not in roles
    assert "anvil-adversarial-reviewer" not in roles


def test_openclaw_skills_sync_render_can_add_checkout_skill_dir():
    rendered = harness.render_openclaw_skills(_cfg(), skill_dir="/opt/anvil/openclaw/skills")
    assert rendered["skills"]["load"]["extraDirs"] == ["/opt/anvil/openclaw/skills"]


def test_openclaw_sync_skills_emits_provider_and_agent_config(capsys):
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   skills=True, _load=lambda p: _cfg())
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    roles = {r["name"]: r for r in payload["agents"]["list"]}
    assert payload["agents"]["defaults"]["skills"] == ["anvil-serving-workbench"]
    assert roles["anvil-inventory-scout"]["model"] == "anvil/chat-fast"
    assert "anvil-quality-critic" not in roles
    assert "anvil-adversarial-reviewer" not in roles
    assert payload["models"]["providers"]["anvil"]["apiKey"] == "${T}"


def test_openclaw_sync_skill_dir_requires_skills(capsys):
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   skill_dir="/opt/skills", _load=lambda p: _cfg())
    assert rc == 2
    assert "--skill-dir requires --skills" in capsys.readouterr().err


def test_sync_missing_config_errors():
    def boom(p):
        raise FileNotFoundError()
    rc = harness.cmd_sync_openclaw("nope.toml", base_url="http://h/v1", api_key_env="T", _load=boom)
    assert rc == 2


def test_sync_no_presets_errors(capsys):
    empty = _Config(presets={}, tiers={})
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   _load=lambda p: empty)
    assert rc == 1
    assert "no [router.presets]" in capsys.readouterr().err


# ---- ssh/scp sync (the OpenClaw gateway is remote; scp = portable on windows + linux) --------

def test_scp_merge_preserves_others_and_drops_stale_anvil_overrides():
    remote = json.dumps({
        "models": {"providers": {"openai": {"baseUrl": "https://api.openai.com/v1"}}},
        "agents": {"defaults": {
            "model": {"primary": "openai/gpt-5.6-sol"},
            "models": {"anvil/planning": {"params": {"x": 1}}, "openai/gpt": {"y": 2}},
        }},
        "plugins": {"entries": {"openclaw-anvil-intent-router": {"enabled": True}}},
    })
    scp = _FakeSCP(remote=remote)
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", tool_profile="full",
                                   exec_mode="auto", _load=lambda p: _cfg(), _run=scp)
    assert rc == 0
    merged = json.loads(scp.written)
    provs = merged["models"]["providers"]
    assert "openai" in provs                                   # other provider preserved
    assert provs["anvil"]["api"] == "openai-completions"       # anvil provider (re)written
    dm = merged["agents"]["defaults"]["models"]
    assert dm["anvil/planning"] == {}                          # allowlisted, stale params STRIPPED
    assert "openai/gpt" in dm                                  # other agent model preserved
    assert merged["models"]["mode"] == "merge"
    plugin_config = merged["plugins"]["entries"][
        "openclaw-anvil-intent-router"
    ]["config"]
    assert plugin_config["nativeProvider"] == "openai"
    assert plugin_config["nativeModel"] == "gpt-5.6-sol"
    assert plugin_config["routeEndpoint"] == "http://h/v1/route"
    assert scp.backed_up                                       # remote backed up first


def test_scp_merge_preserves_live_baseurl_and_apikey():
    # a working literal apiKey + baseUrl on the remote must SURVIVE the sync (not be clobbered by the
    # rendered ${ENV} placeholder / default host) — else re-syncing Mini would 401 every request.
    remote = json.dumps({"models": {"providers": {"anvil": {
        "baseUrl": "http://100.87.34.66:8000/v1", "apiKey": "LITERAL-TOKEN-xyz"}}}})
    scp = _FakeSCP(remote=remote)
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://default-host/v1",
                                   api_key_env="ANVIL_ROUTER_TOKEN", gateway_host="mini",
                                   native_provider="openai", native_model="gpt-5.6-sol",
                                   plugin_dir="/srv/openclaw-anvil-intent-router",
                                   tool_profile="full", exec_mode="auto",
                                   _load=lambda p: _cfg(), _run=scp)
    assert rc == 0
    anvil = json.loads(scp.written)["models"]["providers"]["anvil"]
    assert anvil["baseUrl"] == "http://100.87.34.66:8000/v1"   # live URL preserved
    assert anvil["apiKey"] == "LITERAL-TOKEN-xyz"              # live token preserved (not ${ENV})
    assert all(m["reasoning"] is True for m in anvil["models"])  # but models ARE updated


def test_merge_replaces_explicit_provider_and_route_settings():
    existing = {
        "models": {"providers": {"anvil": {
            "baseUrl": "http://old/v1",
            "apiKey": "OLD",
        }}},
        "plugins": {"entries": {"openclaw-anvil-intent-router": {
            "config": {
                "routeEndpoint": "http://old/v1/route",
                "routeAuthEnv": "OLD_TOKEN",
                "routeTimeoutMs": 30,
            },
        }}},
    }
    rendered = harness.render_openclaw_provider(
        _cfg(),
        base_url="http://new/v1",
        api_key_env="NEW_TOKEN",
        route_endpoint="http://route-new/v1/route",
        route_auth_env="NEW_ROUTE_TOKEN",
        route_timeout_ms=750,
    )

    merged = harness._merge_anvil_provider(
        existing,
        rendered,
        replace_provider_keys=("baseUrl", "apiKey"),
        replace_route_keys=("routeEndpoint", "routeAuthEnv", "routeTimeoutMs"),
    )

    anvil = merged["models"]["providers"]["anvil"]
    assert anvil["baseUrl"] == "http://new/v1"
    assert anvil["apiKey"] == "${NEW_TOKEN}"
    route = merged["plugins"]["entries"][
        "openclaw-anvil-intent-router"
    ]["config"]
    assert route["routeEndpoint"] == "http://route-new/v1/route"
    assert route["routeAuthEnv"] == "NEW_ROUTE_TOKEN"
    assert route["routeTimeoutMs"] == 750


def test_client_side_routing_removes_existing_authoritative_route_settings():
    existing = {
        "plugins": {"entries": {"openclaw-anvil-intent-router": {
            "config": {
                "nativeProvider": "openai",
                "nativeModel": "gpt-5.6-sol",
                "routeEndpoint": "http://old/v1/route",
                "routeAuthEnv": "OLD_TOKEN",
                "routeTimeoutMs": 30,
            },
        }}},
    }
    rendered = harness.render_openclaw_provider(
        _cfg(), base_url="http://new/v1", authoritative_route=False,
    )

    merged = harness._merge_anvil_provider(
        existing, rendered, remove_route_config=True,
    )

    config = merged["plugins"]["entries"][
        "openclaw-anvil-intent-router"
    ]["config"]
    assert config["nativeProvider"] == "openai"
    assert config["nativeModel"] == "gpt-5.6-sol"
    assert "routeEndpoint" not in config
    assert "routeAuthEnv" not in config
    assert "routeTimeoutMs" not in config


def test_scp_merge_preserves_existing_plugin_config():
    remote = json.dumps({
        "plugins": {
            "entries": {
                "openclaw-anvil-intent-router": {
                    "hooks": {"allowPromptInjection": False},
                    "config": {
                        "routeEndpoint": "http://127.0.0.1:8000/v1/route",
                        "routeAuthEnv": "ANVIL_ROUTER_TOKEN",
                        "cloudClasses": ["planning", "long-context"],
                    },
                }
            }
        }
    })
    scp = _FakeSCP(remote=remote)
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", native_provider="openai",
                                   native_model="gpt-5.6-sol", tool_profile="full",
                                   exec_mode="auto", _load=lambda p: _cfg(), _run=scp)
    assert rc == 0
    entry = json.loads(scp.written)["plugins"]["entries"]["openclaw-anvil-intent-router"]
    assert entry["hooks"]["allowConversationAccess"] is True
    assert entry["hooks"]["allowPromptInjection"] is False
    assert entry["config"]["routeAuthEnv"] == "ANVIL_ROUTER_TOKEN"
    assert entry["config"]["nativeProvider"] == "openai"
    assert entry["config"]["nativeModel"] == "gpt-5.6-sol"


def test_scp_merge_removes_legacy_generated_plugin_defaults():
    remote = json.dumps({
        "agents": {"defaults": {"model": {"primary": "openai/gpt-5.6-sol"}}},
        "plugins": {
            "entries": {
                "openclaw-anvil-intent-router": {
                    "hooks": {"allowPromptInjection": False},
                    "config": {
                        "nativeProvider": "anthropic",
                        "nativeModel": "claude-sonnet-4-5",
                        "routeTimeoutMs": 30,
                    },
                }
            }
        }
    })
    scp = _FakeSCP(remote=remote)
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", tool_profile="full",
                                   exec_mode="auto", _load=lambda p: _cfg(), _run=scp)
    assert rc == 0
    entry = json.loads(scp.written)["plugins"]["entries"]["openclaw-anvil-intent-router"]
    assert entry["hooks"]["allowConversationAccess"] is True
    assert entry["hooks"]["allowPromptInjection"] is False
    assert entry["config"]["nativeProvider"] == "openai"
    assert entry["config"]["nativeModel"] == "gpt-5.6-sol"
    assert entry["config"]["routeEndpoint"] == "http://h/v1/route"


def test_scp_merge_preserves_explicit_plugin_overrides():
    remote = json.dumps({
        "plugins": {
            "entries": {
                "openclaw-anvil-intent-router": {
                    "config": {
                        "nativeProvider": "openai",
                        "nativeModel": "gpt-5.5",
                        "routeTimeoutMs": 250,
                    },
                }
            }
        }
    })
    scp = _FakeSCP(remote=remote)
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", native_provider="openai",
                                   native_model="gpt-5.5", tool_profile="full",
                                   exec_mode="auto", _load=lambda p: _cfg(), _run=scp)
    assert rc == 0
    config = json.loads(scp.written)["plugins"]["entries"]["openclaw-anvil-intent-router"]["config"]
    assert config["nativeProvider"] == "openai"
    assert config["nativeModel"] == "gpt-5.5"
    assert config["routeTimeoutMs"] == 250


def test_scp_merge_derives_native_route_from_existing_gateway_primary():
    remote = json.dumps({
        "agents": {"defaults": {"model": {"primary": "openai/gpt-5.6-sol"}}},
        "plugins": {
            "entries": {
                "openclaw-anvil-intent-router": {
                    "hooks": {"allowPromptInjection": False},
                }
            }
        }
    })
    scp = _FakeSCP(remote=remote)
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", tool_profile="full",
                                   exec_mode="auto", _load=lambda p: _cfg(), _run=scp)
    assert rc == 0
    entry = json.loads(scp.written)["plugins"]["entries"]["openclaw-anvil-intent-router"]
    assert entry["hooks"]["allowConversationAccess"] is True
    assert entry["hooks"]["allowPromptInjection"] is False
    assert entry["enabled"] is True
    assert entry["config"]["nativeProvider"] == "openai"
    assert entry["config"]["nativeModel"] == "gpt-5.6-sol"


def test_scp_merge_replaces_stale_anvil_plugin_paths_and_preserves_others():
    remote = json.dumps({
        "agents": {"defaults": {"model": {"primary": "openai/gpt-5.6-sol"}}},
        "plugins": {
            "load": {"paths": [
                "/old/checkout/openclaw-anvil-intent-router",
                "/operator/other-plugin",
                "/another/stale/openclaw-anvil-intent-router",
            ]},
            "entries": {"openclaw-anvil-intent-router": {"enabled": False}},
        },
    })
    scp = _FakeSCP(remote=remote)
    rc = harness.cmd_sync_openclaw(
        "r.toml",
        base_url="http://h/v1",
        api_key_env="T",
        gateway_host="mini",
        plugin_dir="/srv/anvil/plugins/openclaw-anvil-intent-router",
        tool_profile="full",
        exec_mode="auto",
        _load=lambda p: _cfg(),
        _run=scp,
    )
    assert rc == 0
    merged = json.loads(scp.written)
    assert merged["plugins"]["load"]["paths"] == [
        "/operator/other-plugin",
        "/srv/anvil/plugins/openclaw-anvil-intent-router",
    ]
    assert merged["plugins"]["entries"]["openclaw-anvil-intent-router"]["enabled"] is True


def test_sync_only_changes_tool_policy_when_explicit():
    existing = {
        "agents": {"defaults": {"model": {"primary": "openai/gpt-5.6-sol"}}},
        "plugins": {"entries": {"openclaw-anvil-intent-router": {"enabled": True}}},
        "tools": {
            "profile": "coding",
            "exec": {"mode": "ask", "strictInlineEval": True},
            "web": {"fetch": {"enabled": True}},
        },
    }
    rendered = harness.render_openclaw_provider(_cfg(), base_url="http://h/v1")
    merged = harness._merge_anvil_provider(existing, rendered)
    assert merged["tools"]["profile"] == "coding"
    assert merged["tools"]["exec"] == {"mode": "ask", "strictInlineEval": True}
    assert merged["tools"]["web"]["fetch"]["enabled"] is True

    rendered = harness.render_openclaw_provider(
        _cfg(), base_url="http://h/v1", tool_profile="full", exec_mode="auto"
    )
    merged = harness._merge_anvil_provider(existing, rendered)
    assert merged["tools"]["profile"] == "full"
    assert merged["tools"]["exec"] == {"mode": "auto", "strictInlineEval": True}
    assert merged["tools"]["web"]["fetch"]["enabled"] is True


def test_openclaw_skills_sync_scp_merge_preserves_operator_owned_config():
    remote = json.dumps({
        "skills": {"load": {"extraDirs": ["/operator/skills"]}, "other": True},
        "agents": {
            "defaults": {"skills": ["operator-skill"], "model": {"primary": "openai/gpt"}},
            "list": [
                {"name": "operator-agent", "model": "openai/gpt", "skills": ["operator-skill"]},
                {"name": "anvil-probe-evidence-runner", "model": "anvil/chat", "skills": ["old"]},
                {"name": "anvil-quality-critic", "model": "anvil/review", "skills": ["old"]},
                {"name": "anvil-adversarial-reviewer", "model": "anvil/review", "skills": ["old"]},
                {"name": "anvil-quality-critic-independent", "model": "openai/gpt-5.4"},
                {"name": "anvil-inventory-scout", "model": "anvil/chat", "skills": ["old"]},
            ],
        },
        "plugins": {"entries": {"operator-plugin": {"enabled": True}}},
    })
    scp = _FakeSCP(remote=remote)
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", skills=True,
                                   skill_dir="/checkout/examples/openclaw/skills",
                                   native_provider="openai", native_model="gpt",
                                   plugin_dir="/srv/openclaw-anvil-intent-router",
                                   tool_profile="full", exec_mode="auto",
                                   _load=lambda p: _cfg(), _run=scp)
    assert rc == 0
    merged = json.loads(scp.written)
    assert merged["skills"]["load"]["extraDirs"] == [
        "/operator/skills", "/checkout/examples/openclaw/skills",
    ]
    assert merged["skills"]["other"] is True
    assert merged["agents"]["defaults"]["skills"] == [
        "operator-skill", "anvil-serving-workbench",
    ]
    roles = {r["name"]: r for r in merged["agents"]["list"]}
    assert roles["operator-agent"]["skills"] == ["operator-skill"]
    assert "anvil-probe-evidence-runner" not in roles
    assert "anvil-quality-critic" not in roles
    assert "anvil-adversarial-reviewer" not in roles
    assert roles["anvil-quality-critic-independent"]["model"] == "openai/gpt-5.4"
    assert roles["anvil-inventory-scout"]["model"] == "anvil/chat-fast"
    assert "operator-plugin" in merged["plugins"]["entries"]


def test_openclaw_skills_sync_preserves_same_name_independent_reviewers():
    remote = json.dumps({
        "agents": {"list": [
            {"name": "anvil-quality-critic", "model": "openai/gpt-5.4"},
            {"name": "anvil-adversarial-reviewer", "model": "anthropic/claude"},
        ]},
    })
    scp = _FakeSCP(remote=remote)
    rc = harness.cmd_sync_openclaw(
        "r.toml",
        base_url="http://h/v1",
        api_key_env="T",
        gateway_host="mini",
        skills=True,
        native_provider="openai",
        native_model="gpt-5.6-sol",
        plugin_dir="/srv/openclaw-anvil-intent-router",
        tool_profile="full",
        exec_mode="auto",
        _load=lambda p: _cfg(),
        _run=scp,
    )
    assert rc == 0
    roles = {r["name"]: r for r in json.loads(scp.written)["agents"]["list"]}
    assert roles["anvil-quality-critic"]["model"] == "openai/gpt-5.4"
    assert roles["anvil-adversarial-reviewer"]["model"] == "anthropic/claude"


def test_openclaw_skills_sync_local_out_merges_and_backs_up(tmp_path):
    out = tmp_path / "openclaw.json"
    out.write_text(json.dumps({
        "models": {"providers": {"openai": {"baseUrl": "https://api.openai.com/v1"}}},
        "skills": {"load": {"extraDirs": ["/operator/skills"]}},
        "agents": {
            "defaults": {"skills": ["operator-skill"]},
            "list": [{"name": "operator-agent", "model": "openai/gpt"}],
        },
    }), encoding="utf-8")
    rc = harness.cmd_sync_openclaw("r.toml", out=str(out), base_url="http://h/v1",
                                   api_key_env="T", skills=True,
                                   native_provider="openai", native_model="gpt",
                                   plugin_dir=_local_plugin_dir(tmp_path),
                                   tool_profile="full", exec_mode="auto",
                                   _load=lambda p: _cfg())
    assert rc == 0
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert "openai" in merged["models"]["providers"]
    assert "anvil" in merged["models"]["providers"]
    assert merged["skills"]["load"]["extraDirs"] == ["/operator/skills"]
    assert merged["agents"]["defaults"]["skills"] == [
        "operator-skill", "anvil-serving-workbench",
    ]
    roles = {r["name"]: r for r in merged["agents"]["list"]}
    assert "operator-agent" in roles
    assert roles["anvil-inventory-scout"]["model"] == "anvil/chat-fast"
    assert (tmp_path / "openclaw.json.bak").exists()


def test_openclaw_sync_local_out_refuses_json5_without_overwrite(tmp_path, capsys):
    out = tmp_path / "openclaw.json"
    out.write_text("// comment\n{ models: {} }\n", encoding="utf-8")
    rc = harness.cmd_sync_openclaw("r.toml", out=str(out), base_url="http://h/v1",
                                   api_key_env="T", skills=True,
                                   _load=lambda p: _cfg())
    assert rc == 1
    assert "not plain JSON" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8").startswith("// comment")


def test_scp_overwrite_clobbers_other_providers():
    scp = _FakeSCP(remote=json.dumps({"models": {"providers": {"openai": {}}}}))
    rc = harness.cmd_sync_openclaw(
        "r.toml",
        base_url="http://h/v1",
        api_key_env="T",
        gateway_host="mini",
        overwrite=True,
        native_provider="openai",
        native_model="gpt-5.6-sol",
        plugin_dir="/srv/anvil/plugins/openclaw-anvil-intent-router",
        tool_profile="full",
        exec_mode="auto",
        _load=lambda p: _cfg(),
        _run=scp,
    )
    assert rc == 0
    written = json.loads(scp.written)
    assert "openai" not in written["models"]["providers"]      # clobbered by overwrite
    assert "anvil" in written["models"]["providers"]


def test_scp_refuses_merge_on_json5_remote(capsys):
    scp = _FakeSCP(remote="// json5 comment\n{ models: {} }")   # not plain JSON
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", _load=lambda p: _cfg(), _run=scp)
    assert rc == 1
    assert scp.written is None                                  # nothing written
    assert "refusing to merge" in capsys.readouterr().err


def test_scp_created_when_remote_absent(capsys):
    scp = _FakeSCP(remote=None)                                # file absent -> create
    validation = {}
    rc = harness.cmd_sync_openclaw(
        "r.toml",
        base_url="http://h/v1",
        api_key_env="T",
        gateway_host="mini",
        native_provider="openai",
        native_model="gpt-5.6-sol",
        plugin_dir="/srv/anvil/plugins/openclaw-anvil-intent-router",
        tool_profile="full",
        exec_mode="auto",
        _load=lambda p: _cfg(),
        _run=scp,
        _applied_validation=validation,
    )
    assert rc == 0
    assert json.loads(scp.written)["models"]["providers"]["anvil"]
    assert not scp.backed_up                                    # nothing to back up
    assert "created" in capsys.readouterr().out
    assert validation == {
        "plugin_manifest_verified": True,
        "native_model_verified": True,
        "plugin_runtime_verified": True,
    }


def test_runtime_validation_rejects_unavailable_native_model_and_unloaded_plugin():
    payload = harness.render_openclaw_provider(
        _cfg(),
        base_url="http://h/v1",
        native_provider="openai",
        native_model="fabricated-model",
        plugin_dir="/srv/openclaw-anvil-intent-router",
        tool_profile="full",
        exec_mode="auto",
    )
    loaded_plugin = json.dumps({
        "plugin": {
            "id": "openclaw-anvil-intent-router",
            "enabled": True,
            "imported": True,
            "status": "loaded",
            "error": None,
            "hookCount": 1,
        },
    })

    with pytest.raises(ValueError, match="cannot use native fallback model"):
        harness._validate_openclaw_runtime_payload(
            payload,
            json.dumps({"models": [{
                "key": "openai/fabricated-model",
                "available": None,
            }]}),
            loaded_plugin,
        )

    with pytest.raises(ValueError, match="did not load the Anvil routing plugin"):
        harness._validate_openclaw_runtime_payload(
            payload,
            json.dumps({"models": [{
                "key": "openai/fabricated-model",
                "available": True,
            }]}),
            json.dumps({"plugin": {
                "id": "openclaw-anvil-intent-router",
                "enabled": True,
                "imported": False,
                "status": "error",
                "error": "module load failed",
                "hookCount": 0,
            }}),
        )


def test_remote_first_time_runtime_failure_restores_existing_config(capsys):
    original = json.dumps({
        "models": {"providers": {"openai": {"baseUrl": "https://api.openai.com/v1"}}},
    })
    scp = _FakeSCP(remote=original, model_available=False)

    rc = harness.cmd_sync_openclaw(
        "r.toml",
        base_url="http://h/v1",
        api_key_env="T",
        gateway_host="mini",
        native_provider="openai",
        native_model="fabricated-model",
        plugin_dir="/srv/openclaw-anvil-intent-router",
        tool_profile="full",
        exec_mode="auto",
        _load=lambda p: _cfg(),
        _run=scp,
    )

    assert rc == 1
    assert json.loads(scp.written) == json.loads(original)
    error = capsys.readouterr().err
    assert "cannot use native fallback model" in error
    assert "restored the previous OpenClaw config" in error


def test_remote_new_runtime_failure_removes_unvalidated_config(capsys):
    scp = _FakeSCP(remote=None, model_available=False)

    rc = harness.cmd_sync_openclaw(
        "r.toml",
        base_url="http://h/v1",
        api_key_env="T",
        gateway_host="mini",
        native_provider="openai",
        native_model="fabricated-model",
        plugin_dir="/srv/openclaw-anvil-intent-router",
        tool_profile="full",
        exec_mode="auto",
        _load=lambda p: _cfg(),
        _run=scp,
    )

    assert rc == 1
    assert scp.written is None
    assert "removed the unvalidated newly created OpenClaw config" in capsys.readouterr().err


def test_remote_fresh_setup_refuses_wrong_or_missing_plugin_manifest(capsys):
    scp = _FakeSCP(
        remote=None,
        plugin_manifest=json.dumps({"id": "different-plugin"}),
    )
    rc = harness.cmd_sync_openclaw(
        "r.toml",
        base_url="http://h/v1",
        api_key_env="T",
        gateway_host="mini",
        native_provider="openai",
        native_model="gpt-5.6-sol",
        plugin_dir="/srv/anvil/plugins/openclaw-anvil-intent-router",
        tool_profile="full",
        exec_mode="auto",
        _load=lambda p: _cfg(),
        _run=scp,
    )

    assert rc == 1
    assert scp.written is None
    assert "different-plugin" in capsys.readouterr().err


def test_nonempty_first_time_integration_requires_complete_setup():
    existing = json.dumps({
        "models": {"providers": {"openai": {"baseUrl": "https://api.openai.com/v1"}}},
    })
    rendered = harness.render_openclaw_provider(_cfg(), base_url="http://h/v1")

    with pytest.raises(ValueError, match="incomplete first-time OpenClaw integration"):
        harness._payload_for_existing_config(
            existing,
            rendered,
            overwrite=False,
            path="openclaw.json",
        )


def test_partial_legacy_anvil_fragment_does_not_bypass_complete_setup():
    existing = json.dumps({
        "models": {"providers": {"anvil": {
            "baseUrl": "http://old/v1",
            "apiKey": "OLD",
        }}},
    })
    rendered = harness.render_openclaw_provider(_cfg(), base_url="http://h/v1")

    with pytest.raises(ValueError, match="incomplete partial OpenClaw integration"):
        harness._payload_for_existing_config(
            existing,
            rendered,
            overwrite=False,
            path="openclaw.json",
        )


def test_scp_unreachable_errors(capsys):
    scp = _FakeSCP(remote=None, read_err="ssh: connect to host mini port 22: Connection refused")
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", _load=lambda p: _cfg(), _run=scp)
    assert rc == 1
    assert scp.written is None
    assert "cannot reach" in capsys.readouterr().err


def test_transport_is_scp_only_no_remote_shell():
    # portability: a POSIX remote-shell script would break on a Windows gateway, so EVERY transport
    # call must be scp — never `ssh <host> <shell-command>`.
    scp = _FakeSCP(remote=None)
    harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                              gateway_host="mini", _load=lambda p: _cfg(), _run=scp)
    assert scp.calls and all(c[0] == "scp" for c in scp.calls)
    assert all("--" in c for c in scp.calls)
    assert all(c[c.index("--") - 2:c.index("--")] == ["-o", "ServerAliveCountMax=1"] for c in scp.calls)
    assert all(kw["timeout"] == harness.DEFAULT_TRANSPORT_TIMEOUT_SECONDS for kw in scp.kwargs)


# ---- gateway restart (pick up settings) --------------------------------------

def test_restart_local_runs_openclaw_gateway_restart(capsys):
    seen = {}
    def fake(argv, **kw):
        seen["argv"] = argv
        return _proc(0)
    rc = harness.cmd_restart_openclaw(_run=fake)
    assert rc == 0
    assert seen["argv"] == ["openclaw", "gateway", "restart"]
    assert "restarted" in capsys.readouterr().out


def test_restart_dry_run_never_launches_process(capsys):
    rc = harness.cmd_restart_openclaw(
        dry_run=True,
        _run=lambda *_args, **_kwargs: pytest.fail("dry-run launched OpenClaw"),
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {
        "command": ["openclaw", "gateway", "restart"],
        "dry_run": True,
    }


def test_openclaw_status_is_bounded_and_parses_json():
    def run(argv, *, stdout, stderr, timeout):
        stdout.write(b'{"status":"running"}\n')
        stderr.write(b"note\n")
        return subprocess.CompletedProcess(argv, 0)

    result = harness.openclaw_gateway_status(_run=run)
    assert result["ok"] is True
    assert result["status"] == {"status": "running"}
    assert result["stdout_truncated"] is False
    assert result["command"] == ["openclaw", "gateway", "status", "--json"]


def test_openclaw_status_truncates_output_and_classifies_timeout():
    def oversized(argv, *, stdout, stderr, timeout):
        stdout.write(b"x" * 1100)
        return subprocess.CompletedProcess(argv, 0)

    result = harness.openclaw_gateway_status(max_output_bytes=1024, _run=oversized)
    assert result["stdout_truncated"] is True
    assert len(result["stdout"].encode("utf-8")) == 1024
    assert "status" not in result

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["openclaw"], 1)

    result = harness.openclaw_gateway_status(timeout_seconds=1, _run=timeout)
    assert result["ok"] is False
    assert "timed out" in result["error"]


def test_harness_status_openclaw_dispatches(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        harness,
        "cmd_status_openclaw",
        lambda **kwargs: seen.update(kwargs) or 0,
    )
    assert harness.main([
        "status", "openclaw", "--timeout-seconds", "7", "--max-output-bytes", "2048"
    ]) == 0
    assert seen == {"timeout_seconds": 7, "max_output_bytes": 2048}


def test_restart_remote_over_ssh():
    seen = {}
    def fake(argv, **kw):
        seen["argv"] = argv
        return _proc(0)
    rc = harness.cmd_restart_openclaw(gateway_host="mini", gateway_user="sd", _run=fake)
    assert rc == 0
    assert seen["argv"] == [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=60",
        "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=1",
        "--", "sd@mini", harness._REMOTE_RESTART_COMMAND,
    ]


def test_gateway_target_rejects_ssh_options(capsys):
    rc = harness.cmd_restart_openclaw(gateway_host="-oProxyCommand=sh", _run=lambda a, **k: _proc(0))
    assert rc == 2
    assert "gateway host" in capsys.readouterr().err

    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="-oProxyCommand=sh",
                                   _load=lambda p: _cfg(), _run=lambda a, **k: _proc(0))
    assert rc == 2


def test_restart_failure_reported(capsys):
    rc = harness.cmd_restart_openclaw(gateway_host="mini",
                                      _run=lambda a, **k: _proc(1, "", "openclaw: command not found"))
    assert rc == 1
    assert "FAILED to restart" in capsys.readouterr().err


def test_restart_binary_missing(capsys):
    def boom(argv, **kw):
        raise FileNotFoundError()
    rc = harness.cmd_restart_openclaw(_run=boom)
    assert rc == 1
    assert "not available" in capsys.readouterr().err


def test_restart_timeout_reported(capsys):
    def timeout(argv, **kw):
        raise harness.subprocess.TimeoutExpired(argv, kw["timeout"])
    rc = harness.cmd_restart_openclaw(gateway_host="mini", _run=timeout, timeout_seconds=2)
    assert rc == 1
    assert "timed out restarting" in capsys.readouterr().err


def test_sync_gateway_restarts_after_success():
    scp = _FakeSCP(remote=None)  # absent -> created
    rc = harness.cmd_sync_openclaw(
        "r.toml",
        base_url="http://h/v1",
        api_key_env="T",
        gateway_host="mini",
        restart=True,
        native_provider="openai",
        native_model="gpt-5.6-sol",
        plugin_dir="/srv/anvil/plugins/openclaw-anvil-intent-router",
        tool_profile="full",
        exec_mode="auto",
        _load=lambda p: _cfg(),
        _run=scp,
    )
    assert rc == 0 and scp.restarted  # gateway restarted after the config landed


def test_sync_no_restart_without_flag():
    scp = _FakeSCP(remote=None)
    harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                              gateway_host="mini", _load=lambda p: _cfg(), _run=scp)
    assert not scp.restarted


def test_sync_no_restart_when_sync_fails():
    # remote is JSON5 -> merge refused -> rc 1 -> the gateway must NOT be restarted
    scp = _FakeSCP(remote="// json5\n{ }")
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", restart=True,
                                   _load=lambda p: _cfg(), _run=scp)
    assert rc == 1 and not scp.restarted


def test_sync_out_restart_rejects_preview_file(capsys):
    rc = harness.cmd_sync_openclaw("r.toml", out="cfg.json", base_url="http://h/v1",
                                   api_key_env="T", restart=True, _load=lambda p: _cfg())
    assert rc == 2
    assert "preview file" in capsys.readouterr().err


# ---- CLI dispatch ------------------------------------------------------------

def test_main_dispatches_sync_openclaw(monkeypatch):
    seen = {}
    def fake(cfg, **k):
        seen["cfg"], seen["k"] = cfg, k
        return 0
    monkeypatch.setattr(harness, "cmd_sync_openclaw", fake)
    rc = harness.main([
        "sync", "openclaw", "--config", "r.toml",
        "--base-url", "http://h:8000/v1",
        "--api-key-env", "ROUTER_TOKEN",
    ])
    assert rc == 0
    assert seen["cfg"] == "r.toml"
    assert seen["k"]["base_url"] == "http://h:8000/v1"
    assert seen["k"]["api_key_env"] == "ROUTER_TOKEN"
    assert seen["k"]["_replace_provider_keys"] == ("baseUrl", "apiKey")


def test_main_sync_forwards_fresh_gateway_setup_options(monkeypatch):
    seen = {}
    monkeypatch.setattr(harness, "cmd_sync_openclaw", lambda cfg, **k: seen.update(k) or 0)
    rc = harness.main([
        "sync", "openclaw",
        "--config", "r.toml",
        "--native-provider", "openai",
        "--native-model", "gpt-5.6-sol",
        "--plugin-dir", "/srv/anvil/plugins/openclaw-anvil-intent-router",
        "--tool-profile", "full",
        "--exec-mode", "auto",
    ])
    assert rc == 0
    assert seen["native_provider"] == "openai"
    assert seen["native_model"] == "gpt-5.6-sol"
    assert seen["plugin_dir"] == "/srv/anvil/plugins/openclaw-anvil-intent-router"
    assert seen["tool_profile"] == "full"
    assert seen["exec_mode"] == "auto"


def test_main_sync_forwards_restart_flag(monkeypatch):
    seen = {}
    monkeypatch.setattr(harness, "cmd_sync_openclaw", lambda cfg, **k: seen.update(k) or 0)
    harness.main(["sync", "openclaw", "--config", "r.toml", "--gateway-host", "mini", "--restart"])
    assert seen["restart"] is True and seen["gateway_host"] == "mini"


def test_openclaw_sync_main_forwards_skills_and_skill_dir(monkeypatch):
    seen = {}
    monkeypatch.setattr(harness, "cmd_sync_openclaw", lambda cfg, **k: seen.update(k) or 0)
    rc = harness.main(["sync", "openclaw", "--config", "r.toml", "--skills",
                       "--skill-dir", "/opt/anvil/openclaw/skills"])
    assert rc == 0
    assert seen["skills"] is True
    assert seen["skill_dir"] == "/opt/anvil/openclaw/skills"


def test_openclaw_sync_main_skill_dir_requires_skills(capsys):
    rc = harness.main(["sync", "openclaw", "--config", "r.toml",
                       "--skill-dir", "/opt/anvil/openclaw/skills"])
    assert rc == 2
    assert "--skill-dir requires --skills" in capsys.readouterr().err


def test_main_dispatches_restart_action(monkeypatch):
    seen = {}
    monkeypatch.setattr(harness, "cmd_restart_openclaw", lambda **k: seen.update(k) or 0)
    rc = harness.main(["restart", "openclaw", "--gateway-host", "mini", "--gateway-user", "sd"])
    assert rc == 0 and seen["gateway_host"] == "mini" and seen["gateway_user"] == "sd"


def test_main_sync_requires_config(capsys):
    # `sync` needs --config now that it's optional (so `restart` can omit it)
    rc = harness.main(["sync", "openclaw"])
    assert rc == 2
    assert "requires --config" in capsys.readouterr().err


def test_restart_action_rejects_sync_only_flags(capsys):
    # `restart openclaw --config r.toml` would silently discard --config; reject it instead.
    rc = harness.main(["restart", "openclaw", "--config", "r.toml"])
    assert rc == 2
    assert "does not sync" in capsys.readouterr().err


def test_restart_action_rejects_explicit_default_valued_sync_only_flags(capsys):
    rc = harness.main([
        "restart",
        "openclaw",
        "--voice-consult-thinking-level",
        "off",
        "--voice-consult-bootstrap-context-mode",
        "lightweight",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--voice-consult-thinking-level" in err
    assert "--voice-consult-bootstrap-context-mode" in err


def test_stdout_sync_with_restart_rejected(capsys):
    # a stdout-only sync isn't applied, so --restart would reload the OLD gateway config.
    rc = harness.main(["sync", "openclaw", "--config", "r.toml", "--restart"])
    assert rc == 2
    assert "stdout-only" in capsys.readouterr().err


def test_sync_restart_allowed_with_gateway_host(monkeypatch):
    seen = {}
    monkeypatch.setattr(harness, "cmd_sync_openclaw", lambda cfg, **k: seen.update(k) or 0)
    rc = harness.main(["sync", "openclaw", "--config", "r.toml",
                       "--gateway-host", "mini", "--restart"])
    assert rc == 0 and seen["restart"] is True


def test_sync_restart_rejects_arbitrary_out(capsys):
    rc = harness.main(["sync", "openclaw", "--config", "r.toml",
                       "--out", "cfg.json", "--restart"])
    assert rc == 2
    assert "preview file" in capsys.readouterr().err


def test_sync_restart_allowed_with_local_openclaw_config(monkeypatch):
    seen = {}
    monkeypatch.setattr(harness, "cmd_sync_openclaw", lambda cfg, **k: seen.update(k) or 0)
    rc = harness.main(["sync", "openclaw", "--config", "r.toml",
                       "--out", "~/.openclaw/openclaw.json", "--restart"])
    assert rc == 0 and seen["restart"] is True
