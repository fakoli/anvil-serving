"""Tests for `anvil-serving init` (alias `onboard`) — generate a consistent
docker-compose.yml + serves.toml + router.toml + operator-topology.toml bring-up.
`nvidia-smi` is injected via `_run`, so these run with no GPU, no docker, and
no network.
"""
import json
import os

import pytest

from anvil_serving import init, deploy, serves
from anvil_serving.router import config as router_config
from anvil_serving.topology import load_topology


def _run_missing(*a, **k):
    raise FileNotFoundError("nvidia-smi not found")


CSV = (
    "0, GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1, NVIDIA GeForce RTX 5090\n"
    "1, GPU-d0f446cf-1771-414c-e116-a39138798a8c, NVIDIA RTX PRO 6000 Blackwell\n"
)


def _run_ok(*a, **k):
    return CSV


def _card(tmp_path, name, **fields):
    cards = tmp_path / "model-library" / "cards"
    cards.mkdir(parents=True, exist_ok=True)
    data = {"local_path": str(tmp_path / "models" / name), "id": name,
            "format": "safetensors", "sglang_loadable": True, "size_gb": 10.0}
    data.update(fields)
    (cards / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    return data


# ---- pick_model ------------------------------------------------------------------

def test_pick_model_explicit_wins(tmp_path):
    _card(tmp_path, "a")
    facts = init.pick_model([{"local_path": "/x/a", "sglang_loadable": True}],
                            explicit_model="/explicit/model")
    assert facts["local_path"] == "/explicit/model"


def test_pick_model_prefers_largest_loadable():
    catalog = [
        {"local_path": "/a", "sglang_loadable": True, "size_gb": 5.0, "format": "safetensors"},
        {"local_path": "/b", "sglang_loadable": True, "size_gb": 30.0, "format": "safetensors"},
        {"local_path": "/c", "sglang_loadable": False, "size_gb": 90.0, "format": "safetensors"},
    ]
    facts = init.pick_model(catalog)
    assert facts["local_path"] == "/b"


def test_pick_model_skips_sm120_hazardous():
    catalog = [
        {"local_path": "/hazard", "sglang_loadable": True, "sm120_caveat": "hangs", "size_gb": 90},
        {"local_path": "/safe", "sglang_loadable": True, "size_gb": 10, "format": "safetensors"},
    ]
    facts = init.pick_model(catalog)
    assert facts["local_path"] == "/safe"


def test_pick_model_none_when_catalog_empty():
    assert init.pick_model([]) is None


# ---- run(): writes four files, mutually consistent -----------------------------

def test_init_writes_all_four_files(tmp_path):
    out_dir = tmp_path / "onboard"
    result = init.run(model="/w/qwen35-awq", gpu="0", out_dir=str(out_dir), port=30000,
                      served_name="qwen35-awq-local", _run=_run_missing)
    assert os.path.isfile(result["compose"])
    assert os.path.isfile(result["manifest"])
    assert os.path.isfile(result["router"])
    assert os.path.isfile(result["topology"])


def test_init_topology_is_generic_offline_valid_and_consistent(tmp_path):
    out_dir = tmp_path / "onboard"
    result = init.run(
        model="/w/qwen35-awq", gpu="0", out_dir=str(out_dir), port=31111,
        catalog_dir="./catalog", served_name="qwen35-awq-local", _run=_run_missing,
    )
    topology = load_topology(result["topology"])
    assert topology.command_host == "local-host"
    assert topology.command_runtime == "local-native"
    assert topology.host("local-host").address == "127.0.0.1"
    assert topology.host("local-host").os is None
    assert topology.gpu_roles == ()
    assert topology.transports == ()
    assert topology.resource("local-model-serve").endpoint == "http://127.0.0.1:31111/v1"
    assert topology.resource("local-model-catalog").path is None
    text = (out_dir / "operator-topology.toml").read_text(encoding="utf-8")
    assert "deployment-specific" in text
    assert "hostname" not in text.lower()


def test_init_backs_up_existing_operator_topology(tmp_path):
    out_dir = tmp_path / "onboard"
    out_dir.mkdir()
    topology_path = out_dir / "operator-topology.toml"
    topology_path.write_text("operator edits\n", encoding="utf-8")
    init.run(model="/w/model", gpu="0", out_dir=str(out_dir), _run=_run_missing)
    backups = list(out_dir.glob("operator-topology.toml.anvil.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "operator edits\n"
    assert load_topology(topology_path).id == "local-starter"


def test_invalid_topology_input_cannot_partially_rewrite_existing_files(tmp_path):
    out_dir = tmp_path / "onboard"
    out_dir.mkdir()
    originals = {}
    for name in ("docker-compose.yml", "router.toml", "operator-topology.toml"):
        path = out_dir / name
        path.write_text(f"original {name}\n", encoding="utf-8")
        originals[name] = path.read_text(encoding="utf-8")
    with pytest.raises(init.InitError):
        init.run(model="/w/model", out_dir=str(out_dir), port=0, _run=_run_missing)
    for name, expected in originals.items():
        assert (out_dir / name).read_text(encoding="utf-8") == expected
    assert list(out_dir.glob("*.anvil.bak.*")) == []


def test_render_starter_topology_does_not_read_ambient_identity(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("ambient identity must not be inspected")

    monkeypatch.setattr("socket.gethostname", forbidden)
    monkeypatch.setattr("platform.system", forbidden)
    monkeypatch.setenv("ANVIL_COMMAND_HOST", "host:ambient-host")
    text = init.render_starter_topology(port=30000)
    assert "ambient-host" not in text
    assert 'command_host = "host:local-host"' in text


def test_init_tier_model_equals_served_name_and_ports_match(tmp_path):
    out_dir = tmp_path / "onboard"
    result = init.run(model="/w/qwen35-awq", gpu="0", out_dir=str(out_dir), port=30000,
                      served_name="qwen35-awq-local", _run=_run_missing)

    compose = open(result["compose"], encoding="utf-8").read()
    assert "qwen35-awq-local" in compose
    assert "30000:30000" in compose

    manifest = serves.load_manifest(result["manifest"])
    assert len(manifest) == 1
    assert manifest[0]["port"] == 30000
    assert manifest[0]["model"] == "qwen35-awq-local"
    assert manifest[0]["engine"] == "sglang"

    cfg = router_config.load(result["router"])
    tier = cfg.tiers[0]
    assert tier.model == "qwen35-awq-local"
    assert tier.base_url == "http://127.0.0.1:30000/v1"


def test_init_router_toml_loads_without_missing_model_warning(tmp_path, capsys):
    out_dir = tmp_path / "onboard"
    result = init.run(model="/w/model", gpu="0", out_dir=str(out_dir), served_name="local",
                      _run=_run_missing)
    capsys.readouterr()  # drain init's own nvidia-smi warning
    router_config.load(result["router"])
    err = capsys.readouterr().err
    assert "WARNING" not in err  # no T001 missing-`model` warning on load


def test_init_router_toml_has_all_presets():
    pass  # covered by the RouterConfig.load success + preset-lookup test below


def test_init_router_toml_every_preset_resolves(tmp_path):
    out_dir = tmp_path / "onboard"
    result = init.run(model="/w/model", gpu="0", out_dir=str(out_dir), served_name="local",
                      tier_id="local-tier", _run=_run_missing)
    cfg = router_config.load(result["router"])
    for preset in ("planning", "quick-edit", "review", "chat", "long-context"):
        cands = cfg.candidates(preset)
        assert cands and cands[0].id == "local-tier"


# ---- GPU pinning: UUID present / nvidia-smi absent (T007 wiring) ---------------

def test_init_gpu_uuid_present_pins_compose(tmp_path):
    out_dir = tmp_path / "onboard"
    result = init.run(model="/w/model", gpu=1, out_dir=str(out_dir), served_name="local",
                      _run=_run_ok)
    compose = open(result["compose"], encoding="utf-8").read()
    assert "CUDA_VISIBLE_DEVICES: GPU-d0f446cf-1771-414c-e116-a39138798a8c" in compose


def test_init_nvidia_smi_absent_falls_back_with_warning(tmp_path, capsys):
    out_dir = tmp_path / "onboard"
    init.run(model="/w/model", gpu=1, out_dir=str(out_dir), served_name="local", _run=_run_missing)
    err = capsys.readouterr().err
    assert "WARNING" in err and "nvidia-smi" in err


# ---- catalog-driven pick + thinking_default ------------------------------------

def test_init_picks_from_catalog_when_no_explicit_model(tmp_path):
    catalog_dir = tmp_path / "model-library"
    _card(tmp_path, "qwen35-awq", local_path=str(tmp_path / "weights" / "qwen35-awq"))
    out_dir = tmp_path / "onboard"
    result = init.run(catalog_dir=str(catalog_dir), out_dir=str(out_dir), gpu="0", _run=_run_missing)
    assert result["model_path"] == str(tmp_path / "weights" / "qwen35-awq")


def test_init_no_model_no_catalog_raises_init_error(tmp_path):
    with pytest.raises(init.InitError):
        init.run(catalog_dir=str(tmp_path / "nope"), out_dir=str(tmp_path / "onboard"), _run=_run_missing)


def test_init_catalog_thinking_default_disables_at_generation(tmp_path):
    catalog_dir = tmp_path / "model-library"
    _card(tmp_path, "thinky", local_path=str(tmp_path / "weights" / "thinky"), thinking_default=True)
    out_dir = tmp_path / "onboard"
    result = init.run(catalog_dir=str(catalog_dir), out_dir=str(out_dir), gpu="0", _run=_run_missing)
    compose = open(result["compose"], encoding="utf-8").read()
    assert "enable_thinking" in compose
    assert result["disable_thinking"] is True


# ---- CLI -------------------------------------------------------------------------

def test_init_cli_single_model_writes_files(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    out_dir = tmp_path / "onboard"
    rc = init.main(["--single-model", "--model", "/w/model", "--served-name", "local",
                    "--out-dir", str(out_dir)])
    assert rc == 0
    assert os.path.isfile(out_dir / "docker-compose.yml")
    assert os.path.isfile(out_dir / "serves.toml")
    assert os.path.isfile(out_dir / "router.toml")
    assert os.path.isfile(out_dir / "operator-topology.toml")


def test_init_cli_single_model_no_model_no_catalog_errors(tmp_path, capsys):
    rc = init.main(["--single-model", "--catalog-dir", str(tmp_path / "nope"),
                    "--out-dir", str(tmp_path / "onboard")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "models sync" in err or "--model" in err


def test_init_cli_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        init.main(["--help"])
    assert exc.value.code == 0


def test_cli_dispatches_init_and_refuses_removed_onboard(tmp_path, monkeypatch, capsys):
    from anvil_serving import cli
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    out1 = tmp_path / "a"
    assert cli.main(["init", "--single-model", "--model", "/w/model",
                     "--out-dir", str(out1)]) == 0
    assert os.path.isfile(out1 / "router.toml")
    assert cli.main(["onboard", "--single-model", "--model", "/w/model"]) == 2
    assert "was removed; use `init`" in capsys.readouterr().err


# ---- init --home: full operational config-set scaffold ---------------------------

# The reference-instance host values that MUST NOT ride onto a fresh machine.
_REAL_HOST_VALUES = (
    "GPU-d0f446cf-1771-414c-e116-a39138798a8c",
    "GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1",
    "100.87.34.66",
)
_EXPECTED_HOME_FILES = {
    "serves.toml", "serves.voice.toml", "serves.comfyui.toml",
    "docker-compose.yml", "docker-compose.voice-audio.yml", "docker-compose.comfyui.yml",
    "operator-topology.toml", ".env.example", "voice.toml", "edge.toml",
}


def test_scaffold_home_writes_the_full_set(tmp_path):
    result = init.scaffold_home(out_dir=str(tmp_path))
    written = {os.path.basename(p) for p in result["written"]}
    assert written == _EXPECTED_HOME_FILES
    for name in _EXPECTED_HOME_FILES:
        assert os.path.isfile(tmp_path / name), name


def test_scaffold_home_group_tags_resolve(tmp_path):
    init.scaffold_home(out_dir=str(tmp_path))
    serves_set = serves.load_manifest_set(str(tmp_path / "serves.toml"))
    summary = serves.groups_summary(serves_set)
    groups = {row["group"] for row in summary["groups"]}
    # The full operational group vocabulary must resolve from the scaffold alone.
    assert {"voice", "fast-only", "heavy-only", "embedding", "llm-stack", "comfy"} <= groups
    # `serves up --group voice` must resolve the STT/TTS serves with zero editing.
    voice_members = serves.resolve_group(serves_set, "voice")
    assert {s["name"] for s in voice_members} == {"stt", "tts"}


def test_scaffold_home_writes_placeholders_not_real_host_values(tmp_path):
    init.scaffold_home(out_dir=str(tmp_path))
    for name in _EXPECTED_HOME_FILES:
        text = (tmp_path / name).read_text(encoding="utf-8")
        for real in _REAL_HOST_VALUES:
            assert real not in text, f"{name} leaked reference host value {real}"
    # The placeholders the operator is told to edit are actually present.
    compose = (tmp_path / "docker-compose.yml").read_text(encoding="utf-8")
    assert "GPU-REPLACE-WITH-HEAVY-GPU-UUID" in compose
    assert "GPU-REPLACE-WITH-FAST-GPU-UUID" in compose


def test_scaffold_home_never_writes_secrets_only_env_example(tmp_path):
    init.scaffold_home(out_dir=str(tmp_path))
    # Ships the template, never a populated `.env`; and the template holds no
    # filled-in secret values (keys present, values empty).
    assert not os.path.exists(tmp_path / ".env")
    env = (tmp_path / ".env.example").read_text(encoding="utf-8")
    for line in env.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        assert value.strip() == "", f".env.example ships a non-empty secret: {line}"


def test_scaffold_home_backs_up_before_overwrite(tmp_path):
    # Pre-place an operator-edited file; the scaffold must back it up, not clobber.
    (tmp_path / "serves.toml").write_text("# operator hand edits\n", encoding="utf-8")
    result = init.scaffold_home(out_dir=str(tmp_path))
    backed = {os.path.basename(p) for p, _ in result["backed_up"]}
    assert "serves.toml" in backed
    bak = tmp_path / "serves.toml.anvil.bak.1"
    assert bak.is_file()
    assert bak.read_text(encoding="utf-8") == "# operator hand edits\n"
    # Second run stacks a new numbered backup rather than overwriting bak.1.
    init.scaffold_home(out_dir=str(tmp_path))
    assert (tmp_path / "serves.toml.anvil.bak.2").is_file()


def test_scaffold_home_edge_config_parses_and_matches_canonical_routes(tmp_path):
    from anvil_serving import edge
    init.scaffold_home(out_dir=str(tmp_path))
    cfg = edge.load_config(str(tmp_path / "edge.toml"))
    mounts = {route.mount for route in cfg.routes}
    assert mounts == {mount for mount, _ in edge.DEFAULT_ROUTES}


def test_scaffold_home_is_idempotent_no_backup_on_first_run(tmp_path):
    result = init.scaffold_home(out_dir=str(tmp_path))
    assert result["backed_up"] == []  # clean target: nothing to back up


def test_init_cli_no_flags_defaults_to_home_scaffold(tmp_path):
    # DEFAULT (no flags): scaffold the full operational config home.
    rc = init.main(["--out-dir", str(tmp_path)])
    assert rc == 0
    written = {p.name for p in tmp_path.iterdir()}
    assert _EXPECTED_HOME_FILES <= written
    # It must NOT emit the single-model router.toml — that is behind --single-model.
    assert not os.path.isfile(tmp_path / "router.toml")


def test_init_cli_home_alias_still_writes_set_with_deprecation_note(tmp_path, capsys):
    # `--home` is a deprecated alias for the new default; still works, warns.
    rc = init.main(["--home", "--out-dir", str(tmp_path)])
    assert rc == 0
    assert os.path.isfile(tmp_path / "serves.toml")
    assert os.path.isfile(tmp_path / "edge.toml")
    assert "deprecated" in capsys.readouterr().err


def test_init_home_missing_templates_fails_loud(tmp_path, monkeypatch):
    # A broken install whose packaged templates are absent must fail loud,
    # never write a partial set.
    monkeypatch.setattr(init, "_templates_root", lambda: tmp_path / "absent")
    with pytest.raises(init.InitError) as exc:
        init.scaffold_home(out_dir=str(tmp_path / "out"))
    assert "packaged reference templates" in str(exc.value)
    assert not os.path.exists(tmp_path / "out")


# ---- packaging: the set ships as package data and resolves like an installed tool ----

def test_scaffold_templates_resolve_from_installed_package_not_examples():
    """CRITICAL #252 regression guard: resolve templates the way an INSTALLED
    tool does — importlib.resources against the `anvil_serving` package — not a
    path relative to the repo's `examples/` checkout. Proves the set is packaged."""
    import importlib.resources as resources

    root = resources.files("anvil_serving._scaffold_templates")
    pkg_dir = os.path.dirname(os.path.abspath(init.__file__))
    # Every template `init` needs is present as a readable package resource, and
    # each resolves to a real file INSIDE the installed package dir (not examples/).
    for _dest, template_name, _src in init._SCAFFOLD_TEMPLATES:
        res = root.joinpath(template_name)
        assert res.is_file(), f"packaged template missing: {template_name}"
        assert res.read_text(encoding="utf-8")  # non-empty
        with resources.as_file(res) as fs_path:
            resolved = os.path.abspath(str(fs_path))
        assert resolved.startswith(pkg_dir), resolved
        assert os.sep + "examples" + os.sep not in resolved


def test_scaffold_home_works_without_examples_tree(tmp_path, monkeypatch):
    """The home scaffold must NOT depend on the source `examples/` tree — an
    installed wheel has no examples/. Point the (unused-at-runtime) source paths
    at a nonexistent tree and confirm scaffolding still produces the full set
    from package data."""
    monkeypatch.setattr(
        init, "_SCAFFOLD_TEMPLATES",
        tuple((dest, tmpl, "/nonexistent/examples/" + src.split("/")[-1])
              for dest, tmpl, src in init._SCAFFOLD_TEMPLATES))
    result = init.scaffold_home(out_dir=str(tmp_path))
    assert {os.path.basename(p) for p in result["written"]} == _EXPECTED_HOME_FILES


def test_scaffold_templates_match_examples():
    """Drift guard: the packaged `_scaffold_templates/` mirror must stay
    byte-identical to its canonical source under `examples/`. Run
    `python scripts/sync_scaffold_templates.py` if this fails."""
    from pathlib import Path

    repo_root = Path(init.__file__).resolve().parent.parent
    templates_dir = repo_root / "anvil_serving" / "_scaffold_templates"
    for _dest, template_name, source_rel in init._SCAFFOLD_TEMPLATES:
        source = (repo_root / source_rel).read_bytes()
        mirror = (templates_dir / template_name).read_bytes()
        assert mirror == source, (
            f"{template_name} is stale vs {source_rel} — "
            f"run scripts/sync_scaffold_templates.py")
