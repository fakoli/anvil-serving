"""Tests for `anvil-serving harness` — render the OpenClaw harness config from the router config.

The RouterConfig loader is injected (`_load`) and ssh via `_run`, so these run with no config
file, no network, and no ssh.
"""
import json
import types

from anvil_serving import harness


def _proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class _FakeSSH:
    """Fake `_run`: the READ ssh call (no stdin) returns `remote`; the WRITE call (payload piped
    on stdin) captures it. `read_rc`/`write_rc` simulate ssh failures."""
    def __init__(self, remote="", read_rc=0, write_rc=0):
        self.remote, self.read_rc, self.write_rc = remote, read_rc, write_rc
        self.calls, self.written = [], None

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if kw.get("input") is None:      # the READ (`cat ... || true`)
            return _proc(self.read_rc, self.remote, "")
        self.written = kw.get("input")   # the WRITE (payload on stdin)
        return _proc(self.write_rc, "", "")


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
                 "quick-edit": ("heavy", "fast"), "review": ("heavy",)},
        tiers={"heavy": _Tier(131072), "fast": _Tier(32768)},
    )


# ---- rendering ---------------------------------------------------------------

def test_render_one_model_per_preset_with_max_routed_context():
    prov = harness.render_openclaw_provider(_cfg(), base_url="http://x:8000/v1")
    models = {m["id"]: m for m in prov["models"]["providers"]["anvil"]["models"]}
    assert set(models) == {"planning", "chat", "quick-edit", "review"}
    # contextWindow = the LARGEST tier the preset can route to (clamp gotcha)
    assert models["planning"]["contextWindow"] == 131072       # heavy only
    assert models["chat"]["contextWindow"] == 131072           # max(heavy, fast) -> heavy
    assert models["quick-edit"]["contextWindow"] == 131072
    # display name title-cases the preset id
    assert models["quick-edit"]["name"] == "Anvil · Quick Edit"
    # review advertises image input
    assert models["review"]["input"] == ["text", "image"]
    assert models["chat"]["input"] == ["text"]


def test_no_stale_thinking_overrides():
    # the router owns reasoning/thinking per tier now, so the harness must NOT re-declare them.
    prov = harness.render_openclaw_provider(_cfg(), base_url="http://x/v1")
    assert prov["agents"]["defaults"]["models"] == {}


def test_provider_shape_and_token_by_reference():
    prov = harness.render_openclaw_provider(_cfg(), base_url="http://h:8000/v1", api_key_env="TOK")
    anvil = prov["models"]["providers"]["anvil"]
    assert anvil["baseUrl"] == "http://h:8000/v1"
    assert anvil["apiKey"] == "${TOK}"          # by name, never the secret
    assert anvil["api"] == "openai-completions"
    assert prov["models"]["mode"] == "merge"
    assert prov["plugins"]["entries"]["anvil-intent-router"]["hooks"]["allowConversationAccess"] is True


# ---- cmd_sync_openclaw -------------------------------------------------------

def test_sync_emits_valid_json_to_stdout(capsys):
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1",
                                   api_key_env="ANVIL_ROUTER_TOKEN", _load=lambda p: _cfg())
    assert rc == 0
    d = json.loads(capsys.readouterr().out)          # valid JSON
    assert len(d["models"]["providers"]["anvil"]["models"]) == 4


def test_sync_writes_out_file(tmp_path, capsys):
    p = tmp_path / "openclaw.json"
    rc = harness.cmd_sync_openclaw("r.toml", out=str(p), base_url="http://h/v1",
                                   api_key_env="ANVIL_ROUTER_TOKEN", _load=lambda _p: _cfg())
    assert rc == 0
    assert len(json.loads(p.read_text(encoding="utf-8"))["models"]["providers"]["anvil"]["models"]) == 4
    assert "OpenClaw provider config" in capsys.readouterr().out


def test_sync_skills_not_implemented_yet(capsys):
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   skills=True, _load=lambda p: _cfg())
    assert rc == 2
    assert "not implemented" in capsys.readouterr().err


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


# ---- ssh sync (the OpenClaw gateway is remote) -------------------------------

def test_ssh_merge_preserves_others_and_drops_stale_anvil_overrides():
    remote = json.dumps({
        "models": {"providers": {"openai": {"baseUrl": "https://api.openai.com/v1"}}},
        "agents": {"defaults": {"models": {"anvil/planning": {"params": {"x": 1}},
                                           "openai/gpt": {"y": 2}}}},
    })
    ssh = _FakeSSH(remote=remote)
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", _load=lambda p: _cfg(), _run=ssh)
    assert rc == 0
    merged = json.loads(ssh.written)
    provs = merged["models"]["providers"]
    assert "openai" in provs                                   # other provider preserved
    assert provs["anvil"]["api"] == "openai-completions"       # anvil provider (re)written
    dm = merged["agents"]["defaults"]["models"]
    assert "anvil/planning" not in dm                          # stale anvil/* override dropped
    assert "openai/gpt" in dm                                  # other agent model preserved
    assert merged["models"]["mode"] == "merge"


def test_ssh_overwrite_clobbers_other_providers():
    ssh = _FakeSSH(remote=json.dumps({"models": {"providers": {"openai": {}}}}))
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", overwrite=True,
                                   _load=lambda p: _cfg(), _run=ssh)
    assert rc == 0
    written = json.loads(ssh.written)
    assert "openai" not in written["models"]["providers"]      # clobbered by overwrite
    assert "anvil" in written["models"]["providers"]


def test_ssh_refuses_merge_on_json5_remote(capsys):
    ssh = _FakeSSH(remote="// json5 comment\n{ models: {} }")   # not plain JSON
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", _load=lambda p: _cfg(), _run=ssh)
    assert rc == 1
    assert ssh.written is None                                  # nothing written
    assert "refusing to merge" in capsys.readouterr().err


def test_ssh_created_when_remote_absent(capsys):
    ssh = _FakeSSH(remote="")
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", _load=lambda p: _cfg(), _run=ssh)
    assert rc == 0
    assert json.loads(ssh.written)["models"]["providers"]["anvil"]
    assert "created" in capsys.readouterr().out


def test_ssh_unreachable_errors(capsys):
    ssh = _FakeSSH(read_rc=255)                                 # ssh connection failed
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", _load=lambda p: _cfg(), _run=ssh)
    assert rc == 1
    assert ssh.written is None
    assert "cannot reach" in capsys.readouterr().err


def test_ssh_write_backs_up_and_is_atomic():
    ssh = _FakeSSH(remote="")
    harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                              gateway_host="mini", gateway_path="~/.openclaw/openclaw.json",
                              _load=lambda p: _cfg(), _run=ssh)
    write_cmd = next(c for c in ssh.calls if len(c) >= 3 and ".anvil-new" in c[2])
    assert ".bak." in write_cmd[2]                              # timestamped backup
    assert "mv" in write_cmd[2]                                 # atomic temp + mv


# ---- CLI dispatch ------------------------------------------------------------

def test_main_dispatches_sync_openclaw(monkeypatch):
    seen = {}
    def fake(cfg, **k):
        seen["cfg"], seen["k"] = cfg, k
        return 0
    monkeypatch.setattr(harness, "cmd_sync_openclaw", fake)
    rc = harness.main(["sync", "openclaw", "--config", "r.toml", "--base-url", "http://h:8000/v1"])
    assert rc == 0
    assert seen["cfg"] == "r.toml"
    assert seen["k"]["base_url"] == "http://h:8000/v1"
    assert seen["k"]["api_key_env"] == "ANVIL_ROUTER_TOKEN"
