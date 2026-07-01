"""Tests for `anvil-serving init` (alias `onboard`) — generate a consistent
docker-compose.yml + serves.toml + router.toml bring-up (genericity:T006).
`nvidia-smi` is injected via `_run`, so these run with no GPU, no docker, and
no network.
"""
import json
import os

import pytest

from anvil_serving import init, deploy, serves
from anvil_serving.router import config as router_config


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


# ---- run(): writes all three files, mutually consistent ------------------------

def test_init_writes_all_three_files(tmp_path):
    out_dir = tmp_path / "onboard"
    result = init.run(model="/w/qwen35-awq", gpu="0", out_dir=str(out_dir), port=30000,
                      served_name="qwen35-awq-local", _run=_run_missing)
    assert os.path.isfile(result["compose"])
    assert os.path.isfile(result["manifest"])
    assert os.path.isfile(result["router"])


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

    cfg = router_config.load(result["router"])
    tier = cfg.tiers[0]
    assert tier.model == "qwen35-awq-local"
    assert tier.base_url == "http://127.0.0.1:30000/v1"


def test_init_router_toml_loads_without_missing_model_warning(tmp_path, capsys):
    out_dir = tmp_path / "onboard"
    result = init.run(model="/w/model", gpu="0", out_dir=str(out_dir), served_name="local",
                      _run=_run_missing)
    capsys.readouterr()  # drain init's own nvidia-smi warning
    cfg = router_config.load(result["router"])
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

def test_init_cli_writes_files(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    out_dir = tmp_path / "onboard"
    rc = init.main(["--model", "/w/model", "--served-name", "local", "--out-dir", str(out_dir)])
    assert rc == 0
    assert os.path.isfile(out_dir / "docker-compose.yml")
    assert os.path.isfile(out_dir / "serves.toml")
    assert os.path.isfile(out_dir / "router.toml")


def test_init_cli_no_model_no_catalog_errors(tmp_path, capsys):
    rc = init.main(["--catalog-dir", str(tmp_path / "nope"), "--out-dir", str(tmp_path / "onboard")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "models sync" in err or "--model" in err


def test_init_cli_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        init.main(["--help"])
    assert exc.value.code == 0


def test_cli_dispatches_init_and_onboard(tmp_path, monkeypatch):
    from anvil_serving import cli
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    out1 = tmp_path / "a"
    out2 = tmp_path / "b"
    assert cli.main(["init", "--model", "/w/model", "--out-dir", str(out1)]) == 0
    assert cli.main(["onboard", "--model", "/w/model", "--out-dir", str(out2)]) == 0
    assert os.path.isfile(out1 / "router.toml")
    assert os.path.isfile(out2 / "router.toml")
