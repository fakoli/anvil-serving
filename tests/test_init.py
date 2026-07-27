"""Tests for `anvil-serving init` — generate a consistent
docker-compose.yml + serves.toml + router.toml + operator-topology.toml bring-up.
`nvidia-smi` is injected via `_run`, so these run with no GPU, no docker, and
no network.
"""
import json
import os
import re

import pytest

from anvil_serving import host, init, deploy, serve_recipes, serves
from anvil_serving.router import config as router_config
from anvil_serving.topology import load_topology


def _run_missing(*a, **k):
    raise FileNotFoundError("nvidia-smi not found")


CSV = (
    "0, GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1, NVIDIA GeForce RTX 5090\n"
    "1, GPU-d0f446cf-1771-414c-e116-a39138798a8c, NVIDIA RTX PRO 6000 Blackwell\n"
)

CAPACITY_CSV = (
    "0, GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1, NVIDIA GeForce RTX 5090, 32607\n"
    "1, GPU-d0f446cf-1771-414c-e116-a39138798a8c, NVIDIA RTX PRO 6000 Blackwell, 97887\n"
)


def _run_ok(*a, **k):
    return CSV


def _run_capacity(*a, **k):
    return CAPACITY_CSV


def _run_tailnet(*a, **k):
    return "100.87.34.66\n"


def _scaffold_home(*args, **kwargs):
    return init.scaffold_home(*args, detect_host=False, **kwargs)


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


def test_init_router_toml_declares_the_requested_direct_alias(tmp_path):
    out_dir = tmp_path / "onboard"
    result = init.run(model="/w/model", gpu="0", out_dir=str(out_dir), served_name="local",
                      tier_id="local-tier", _run=_run_missing)
    cfg = router_config.load(result["router"])
    assert cfg.route_tier("llm.primary").id == "local-tier"


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


def test_cli_dispatches_init_and_rejects_unknown_onboard(tmp_path, monkeypatch, capsys):
    from anvil_serving import cli
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    out1 = tmp_path / "a"
    assert cli.main(["init", "--single-model", "--model", "/w/model",
                     "--out-dir", str(out1)]) == 0
    assert os.path.isfile(out1 / "router.toml")
    assert cli.main(["onboard", "--single-model", "--model", "/w/model"]) == 2
    assert "unknown command: onboard" in capsys.readouterr().err


# ---- init --home: full operational config-set scaffold ---------------------------

# The reference-instance host values that MUST NOT ride onto a fresh machine.
_REAL_HOST_VALUES = (
    "GPU-d0f446cf-1771-414c-e116-a39138798a8c",
    "GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1",
    "100.87.34.66",
)
_EXPECTED_HOME_FILES = {
    "router.toml", "example.toml", "example-docker.toml",
    "host.toml", "serve-recipes.toml",
    "serves.toml", "serves.voice.toml", "serves.comfyui.toml",
    "docker-compose.yml", "docker-compose.voice-audio.yml", "docker-compose.voice-proxy.yml",
    "docker-compose.comfyui.yml",
    "operator-topology.toml", ".env.example", "voice.toml", "edge.toml",
}


def test_scaffold_home_writes_the_full_set(tmp_path):
    result = _scaffold_home(out_dir=str(tmp_path))
    written = {os.path.basename(p) for p in result["written"]}
    assert written == _EXPECTED_HOME_FILES
    for name in _EXPECTED_HOME_FILES:
        assert os.path.isfile(tmp_path / name), name


def test_scaffold_home_group_tags_resolve(tmp_path):
    _scaffold_home(out_dir=str(tmp_path))
    serves_set = serves.load_manifest_set(str(tmp_path / "serves.toml"))
    summary = serves.groups_summary(serves_set)
    groups = {row["group"] for row in summary["groups"]}
    # The full operational group vocabulary must resolve from the scaffold alone.
    assert {
        "voice",
        "auxiliary-only",
        "primary-only",
        "embedding",
        "llm-stack",
        "comfy",
    } <= groups
    # `serves up --group voice` must resolve the whole voice stack with zero
    # editing: the STT/TTS audio serves plus the managed realtime proxy.
    voice_members = serves.resolve_group(serves_set, "voice")
    assert {s["name"] for s in voice_members} == {"stt", "tts", "realtime-proxy"}


def test_scaffold_home_router_configs_and_recipes_parse(tmp_path):
    _scaffold_home(out_dir=str(tmp_path))

    for name in (
        "router.toml",
        "example.toml",
        "example-docker.toml",
    ):
        cfg = router_config.load(str(tmp_path / name))
        assert cfg.tiers, name

    registry = serve_recipes.load_registry(str(tmp_path / "serve-recipes.toml"))
    assert registry["schema"] == serve_recipes.REGISTRY_SCHEMA
    assert registry["recipe"]

    policy = host.load_cache_reclaim_policy(str(tmp_path / "host.toml"))
    assert policy["configured"] is True
    assert policy["enabled"] is False
    assert policy["distro"] == "docker-desktop"
    assert policy["threshold_gb"] == 16.0


def test_scaffold_home_default_router_matches_production_serve_ports(tmp_path):
    _scaffold_home(out_dir=str(tmp_path))
    router = (tmp_path / "router.toml").read_text(encoding="utf-8")

    for port in (30002, 30003, 30005, 30006, 30007, 30008, 30010, 30011):
        assert f"127.0.0.1:{port}" in router
    assert 'llm.primary = "primary-local"' in router
    assert 'llm.voice = "auxiliary-local"' in router
    assert 'vision.ocr = "ocr-local"' in router
    assert 'vision.general = "vision-local"' in router


def test_scaffold_home_writes_placeholders_not_real_host_values(tmp_path):
    _scaffold_home(out_dir=str(tmp_path))
    for name in _EXPECTED_HOME_FILES:
        text = (tmp_path / name).read_text(encoding="utf-8")
        for real in _REAL_HOST_VALUES:
            assert real not in text, f"{name} leaked reference host value {real}"
    # The placeholders the operator is told to edit are actually present.
    compose = (tmp_path / "docker-compose.yml").read_text(encoding="utf-8")
    assert "GPU-REPLACE-WITH-PRIMARY-GPU-UUID" in compose
    assert "GPU-REPLACE-WITH-AUXILIARY-GPU-UUID" in compose


def test_scaffold_home_contains_no_legacy_hardware_or_live_role_names(tmp_path):
    _scaffold_home(out_dir=str(tmp_path))
    legacy = (
        "HEAVY_GPU_UUID",
        "FAST_GPU_UUID",
        "heavy-local",
        "fast-local",
        "ANVIL_HEAVY_LOCAL_KEY",
        "ANVIL_FAST_LOCAL_KEY",
        '--heavy-gpu-uuid',
        '--fast-gpu-uuid',
    )
    for path in tmp_path.iterdir():
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for token in legacy:
            assert token not in text, (path.name, token)


def test_scaffold_home_never_writes_secrets_only_env_example(tmp_path):
    _scaffold_home(out_dir=str(tmp_path))
    # Ships the template, never a populated `.env`; and the template holds no
    # filled-in secret values (keys present, values empty).
    assert not os.path.exists(tmp_path / ".env")
    env = (tmp_path / ".env.example").read_text(encoding="utf-8")
    for line in env.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key in {"PRIMARY_GPU_UUID", "AUXILIARY_GPU_UUID"}:
            assert value.startswith("GPU-REPLACE-WITH-")
        else:
            assert value.strip() == "", f".env.example ships a non-empty secret: {line}"


def test_scaffold_home_backs_up_before_overwrite(tmp_path):
    # Pre-place an operator-edited file; the scaffold must back it up, not clobber.
    (tmp_path / "serves.toml").write_text("# operator hand edits\n", encoding="utf-8")
    result = _scaffold_home(out_dir=str(tmp_path))
    backed = {os.path.basename(p) for p, _ in result["backed_up"]}
    assert "serves.toml" in backed
    bak = tmp_path / "serves.toml.anvil.bak.1"
    assert bak.is_file()
    assert bak.read_text(encoding="utf-8") == "# operator hand edits\n"
    # An identical rerun leaves the generated file in place and creates no
    # redundant second backup.
    rerun = _scaffold_home(out_dir=str(tmp_path))
    assert not (tmp_path / "serves.toml.anvil.bak.2").exists()
    assert rerun["written"] == []
    assert rerun["backed_up"] == []
    assert {os.path.basename(path) for path in rerun["unchanged"]} == _EXPECTED_HOME_FILES


def test_scaffold_home_identical_rerun_does_not_backup_env_example(tmp_path):
    first = _scaffold_home(out_dir=str(tmp_path))
    env_before = (tmp_path / ".env.example").read_bytes()

    second = _scaffold_home(out_dir=str(tmp_path))

    assert len(first["written"]) == len(_EXPECTED_HOME_FILES)
    assert second["written"] == []
    assert second["backed_up"] == []
    assert {os.path.basename(path) for path in second["unchanged"]} == _EXPECTED_HOME_FILES
    assert list(tmp_path.glob(".env.example.anvil.bak.*")) == []
    assert (tmp_path / ".env.example").read_bytes() == env_before


def test_scaffold_home_rerun_backs_up_only_changed_files(tmp_path):
    _scaffold_home(out_dir=str(tmp_path))
    serves_path = tmp_path / "serves.toml"
    serves_path.write_text("# operator changes\n", encoding="utf-8")

    result = _scaffold_home(out_dir=str(tmp_path))

    assert [os.path.basename(path) for path in result["written"]] == ["serves.toml"]
    assert [
        (os.path.basename(path), os.path.basename(backup))
        for path, backup in result["backed_up"]
    ] == [("serves.toml", "serves.toml.anvil.bak.1")]
    assert len(result["unchanged"]) == len(_EXPECTED_HOME_FILES) - 1
    assert (tmp_path / "serves.toml.anvil.bak.1").read_text(
        encoding="utf-8"
    ) == "# operator changes\n"


def test_scaffold_home_edge_config_parses_and_matches_canonical_routes(tmp_path):
    from anvil_serving import edge
    _scaffold_home(out_dir=str(tmp_path))
    cfg = edge.load_config(str(tmp_path / "edge.toml"))
    mounts = {route.mount for route in cfg.routes}
    assert mounts == {mount for mount, _ in edge.DEFAULT_ROUTES}


def test_scaffold_home_is_idempotent_no_backup_on_first_run(tmp_path):
    result = _scaffold_home(out_dir=str(tmp_path))
    assert result["backed_up"] == []  # clean target: nothing to back up


def test_discover_host_assigns_primary_and_auxiliary_by_vram():
    discovery = init.discover_host(
        _gpu_run=_run_capacity,
        _tailscale_run=_run_tailnet,
    )

    assert discovery["primary_gpu"]["uuid"] == _REAL_HOST_VALUES[0]
    assert discovery["primary_gpu"]["memory_total_mib"] == 97887
    assert discovery["auxiliary_gpu"]["uuid"] == _REAL_HOST_VALUES[1]
    assert discovery["auxiliary_gpu"]["memory_total_mib"] == 32607
    assert discovery["tailnet_ip"] == _REAL_HOST_VALUES[2]


def test_discover_host_equal_vram_uses_lower_index_as_primary():
    equal_capacity = (
        "1, GPU-11111111-2222-3333-4444-555555555555, Card B, 49140\n"
        "0, GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee, Card A, 49140\n"
    )

    discovery = init.discover_host(
        _gpu_run=lambda *a, **k: equal_capacity,
        _tailscale_run=_run_missing,
    )

    assert discovery["primary_gpu"]["uuid"] == (
        "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    )
    assert discovery["auxiliary_gpu"]["uuid"] == (
        "GPU-11111111-2222-3333-4444-555555555555"
    )


def test_discover_host_uses_smallest_of_three_gpus_as_auxiliary():
    capacities = (
        "0, GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee, Large, 97887\n"
        "1, GPU-11111111-2222-3333-4444-555555555555, Medium, 49140\n"
        "2, GPU-99999999-8888-7777-6666-555555555555, Small, 32607\n"
    )

    discovery = init.discover_host(
        _gpu_run=lambda *a, **k: capacities,
        _tailscale_run=_run_missing,
    )

    assert discovery["primary_gpu"]["name"] == "Large"
    assert discovery["auxiliary_gpu"]["name"] == "Small"


def test_discover_host_single_gpu_leaves_auxiliary_unresolved():
    discovery = init.discover_host(
        _gpu_run=lambda *a, **k: CAPACITY_CSV.splitlines()[1] + "\n",
        _tailscale_run=_run_missing,
    )

    assert discovery["primary_gpu"]["uuid"] == _REAL_HOST_VALUES[0]
    assert discovery["auxiliary_gpu"] is None
    assert discovery["tailnet_ip"] is None


def test_scaffold_home_injects_detected_host_values(tmp_path):
    result = init.scaffold_home(
        out_dir=str(tmp_path),
        _gpu_run=_run_capacity,
        _tailscale_run=_run_tailnet,
    )

    assert result["discovery"]["primary_gpu"]["source"] == "detected"
    compose = (tmp_path / "docker-compose.yml").read_text(encoding="utf-8")
    voice = (tmp_path / "docker-compose.voice-audio.yml").read_text(encoding="utf-8")
    assert _REAL_HOST_VALUES[0] in compose
    assert _REAL_HOST_VALUES[1] in compose
    assert _REAL_HOST_VALUES[2] in voice
    assert "GPU-REPLACE-WITH" not in compose
    assert "REPLACE-WITH-YOUR-TAILNET-IP" not in voice


def test_scaffold_maps_primary_llm_and_auxiliary_workloads_to_gpu_roles(tmp_path):
    init.scaffold_home(
        out_dir=str(tmp_path),
        _gpu_run=_run_capacity,
        _tailscale_run=_run_tailnet,
    )
    compose = (tmp_path / "docker-compose.yml").read_text(encoding="utf-8")
    voice = (tmp_path / "docker-compose.voice-audio.yml").read_text(
        encoding="utf-8"
    )
    comfy = (tmp_path / "docker-compose.comfyui.yml").read_text(encoding="utf-8")

    def service_block(text, name):
        match = re.search(
            rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:\n|^\S|\Z)",
            text,
        )
        assert match is not None
        return match.group(1)

    assert "PRIMARY_GPU_UUID" in service_block(compose, "primary")
    for service in ("auxiliary", "embeddings", "reranker", "ocr", "vision"):
        block = service_block(compose, service)
        assert "AUXILIARY_GPU_UUID" in block
        assert "PRIMARY_GPU_UUID" not in block
    for service in ("stt", "tts"):
        assert "AUXILIARY_GPU_UUID" in service_block(voice, service)
    assert "AUXILIARY_GPU_UUID" in service_block(comfy, "comfyui")

    topology = (tmp_path / "operator-topology.toml").read_text(encoding="utf-8")
    serves_manifest = (tmp_path / "serves.toml").read_text(encoding="utf-8")
    assert 'id = "dark-primary"' in topology
    assert 'id = "dark-auxiliary"' in topology
    assert 'gpu_role = "dark-auxiliary"' in serves_manifest


def test_discover_host_overrides_take_precedence():
    primary = "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    auxiliary = "GPU-11111111-2222-3333-4444-555555555555"
    discovery = init.discover_host(
        primary_gpu_uuid=primary,
        auxiliary_gpu_uuid=auxiliary,
        tailnet_ip="100.100.100.100",
        probe=False,
        _tailscale_run=_run_missing,
    )

    assert discovery["primary_gpu"] == {
        "uuid": primary,
        "name": None,
        "memory_total_mib": None,
        "source": "override",
    }
    assert discovery["auxiliary_gpu"]["uuid"] == auxiliary
    assert discovery["tailnet_ip"] == "100.100.100.100"
    assert discovery["tailnet_source"] == "override"


def test_no_detect_host_still_applies_explicit_overrides(tmp_path):
    primary = "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    result = init.scaffold_home(
        out_dir=str(tmp_path),
        detect_host=False,
        primary_gpu_uuid=primary,
        tailnet_ip="100.100.100.100",
    )

    assert result["discovery"]["primary_gpu"]["source"] == "override"
    assert result["discovery"]["auxiliary_gpu"] is None
    compose = (tmp_path / "docker-compose.yml").read_text(encoding="utf-8")
    assert primary in compose
    assert "GPU-REPLACE-WITH-AUXILIARY-GPU-UUID" in compose


def test_discover_host_rejects_duplicate_role_overrides():
    uuid = "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    with pytest.raises(init.InitError, match="distinct GPUs"):
        init.discover_host(
            primary_gpu_uuid=uuid,
            auxiliary_gpu_uuid=uuid,
            probe=False,
        )


def test_discover_host_rejects_override_not_reported_by_detected_host():
    with pytest.raises(init.InitError, match="was not reported by nvidia-smi"):
        init.discover_host(
            primary_gpu_uuid="GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            _gpu_run=_run_capacity,
            _tailscale_run=_run_missing,
        )


def test_discover_host_rejects_inverted_detected_role_overrides():
    with pytest.raises(init.InitError, match="at least as much VRAM"):
        init.discover_host(
            primary_gpu_uuid=_REAL_HOST_VALUES[1],
            auxiliary_gpu_uuid=_REAL_HOST_VALUES[0],
            _gpu_run=_run_capacity,
            _tailscale_run=_run_missing,
        )


def test_discover_host_rejects_non_tailnet_ip_override():
    with pytest.raises(init.InitError, match="100.64.0.0/10"):
        init.discover_host(tailnet_ip="192.168.1.10", probe=False)


def test_copy_env_command_uses_powershell_on_windows():
    command = init._copy_env_command(r"C:\Operator Home", platform_name="nt")

    assert command == (
        "Copy-Item -LiteralPath 'C:\\Operator Home\\.env.example' "
        "-Destination 'C:\\Operator Home\\.env'"
    )


def test_copy_env_command_uses_posix_paths_on_posix():
    command = init._copy_env_command("/operator home", platform_name="posix")

    assert command == (
        "cp -- '/operator home/.env.example' '/operator home/.env'"
    )


def test_init_cli_no_flags_defaults_to_home_scaffold(tmp_path):
    # An explicit target always wins over the default config home.
    rc = init.main(["--no-detect-host", "--out-dir", str(tmp_path)])
    assert rc == 0
    written = {p.name for p in tmp_path.iterdir()}
    assert _EXPECTED_HOME_FILES <= written
    assert os.path.isfile(tmp_path / "router.toml")


def test_init_cli_no_flags_scaffolds_config_home(tmp_path, monkeypatch):
    home = tmp_path / "operator-home"
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(home))
    monkeypatch.setattr(init, "discover_host", lambda **kwargs: init._empty_host_discovery())
    rc = init.main([])
    assert rc == 0
    assert (home / "serves.toml").is_file()
    assert (home / "router.toml").is_file()
    assert (home / "serve-recipes.toml").is_file()


def test_init_cli_config_home_backs_up_existing_operator_file(tmp_path, monkeypatch):
    home = tmp_path / "operator-home"
    home.mkdir()
    (home / "serves.toml").write_text("# operator hand edits\n", encoding="utf-8")
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(home))

    assert init.main(["--no-detect-host"]) == 0

    backup = home / "serves.toml.anvil.bak.1"
    assert backup.read_text(encoding="utf-8") == "# operator hand edits\n"


def test_init_cli_identical_rerun_reports_up_to_date_without_backups(
    tmp_path, capsys
):
    args = ["--no-detect-host", "--out-dir", str(tmp_path)]
    assert init.main(args) == 0
    capsys.readouterr()

    assert init.main(args) == 0

    output = capsys.readouterr().out
    assert "configuration already up to date" in output
    assert "16 unchanged file(s)" in output
    assert "backed up existing operator files" not in output
    assert list(tmp_path.glob("*.anvil.bak.*")) == []


def test_init_home_missing_templates_fails_loud(tmp_path, monkeypatch):
    # A broken install whose packaged templates are absent must fail loud,
    # never write a partial set.
    monkeypatch.setattr(init, "_templates_root", lambda: tmp_path / "absent")
    with pytest.raises(init.InitError) as exc:
        _scaffold_home(out_dir=str(tmp_path / "out"))
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
    result = _scaffold_home(out_dir=str(tmp_path))
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
