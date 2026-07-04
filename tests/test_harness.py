"""Tests for `anvil-serving harness` — render the OpenClaw harness config from the router config.

The RouterConfig loader is injected (`_load`) and ssh via `_run`, so these run with no config
file, no network, and no ssh.
"""
import json
import types

from anvil_serving import harness


def _proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class _FakeSCP:
    """Fake `_run` for scp (portable transport). `remote` = the remote file's content (None =
    absent). READ (`scp host:path localtmp`) materializes `remote` to the local dest; WRITE
    (`scp localtmp host:path`) captures the payload; a `.bak` dest records a backup. `read_err`
    non-"no such file" simulates an unreachable host."""
    def __init__(self, host="mini", remote=None, read_err="", write_rc=0):
        self.host, self.remote, self.read_err, self.write_rc = host, remote, read_err, write_rc
        self.calls, self.written, self.backed_up = [], None, False

    def _is_remote(self, arg):
        return arg.startswith(self.host + ":")

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        src, dst = argv[-2], argv[-1]
        if self._is_remote(dst):                      # WRITE or BACKUP (dest is remote)
            if dst.endswith(".bak"):
                self.backed_up = True
                return _proc(0)
            with open(src, "r", encoding="utf-8") as f:
                self.written = f.read()
            return _proc(self.write_rc, "", "" if self.write_rc == 0 else "write failed")
        # READ (source is remote) -> materialize the remote content to the local dest
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


# ---- ssh/scp sync (the OpenClaw gateway is remote; scp = portable on windows + linux) --------

def test_scp_merge_preserves_others_and_drops_stale_anvil_overrides():
    remote = json.dumps({
        "models": {"providers": {"openai": {"baseUrl": "https://api.openai.com/v1"}}},
        "agents": {"defaults": {"models": {"anvil/planning": {"params": {"x": 1}},
                                           "openai/gpt": {"y": 2}}}},
    })
    scp = _FakeSCP(remote=remote)
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", _load=lambda p: _cfg(), _run=scp)
    assert rc == 0
    merged = json.loads(scp.written)
    provs = merged["models"]["providers"]
    assert "openai" in provs                                   # other provider preserved
    assert provs["anvil"]["api"] == "openai-completions"       # anvil provider (re)written
    dm = merged["agents"]["defaults"]["models"]
    assert "anvil/planning" not in dm                          # stale anvil/* override dropped
    assert "openai/gpt" in dm                                  # other agent model preserved
    assert merged["models"]["mode"] == "merge"
    assert scp.backed_up                                       # remote backed up first


def test_scp_overwrite_clobbers_other_providers():
    scp = _FakeSCP(remote=json.dumps({"models": {"providers": {"openai": {}}}}))
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", overwrite=True,
                                   _load=lambda p: _cfg(), _run=scp)
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
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   gateway_host="mini", _load=lambda p: _cfg(), _run=scp)
    assert rc == 0
    assert json.loads(scp.written)["models"]["providers"]["anvil"]
    assert not scp.backed_up                                    # nothing to back up
    assert "created" in capsys.readouterr().out


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
