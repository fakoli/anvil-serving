"""Tests for `anvil-serving init` — generate a consistent
docker-compose.yml + serves.toml + router.toml + operator-topology.toml bring-up.
`nvidia-smi` is injected via `_run`, so these run with no GPU, no docker, and
no network.
"""
import json
import os

import pytest

from anvil_serving import init, deploy


def _run_missing(*a, **k):
    raise FileNotFoundError("nvidia-smi not found")


CSV = (
    "0, GPU-33333333-3333-3333-3333-333333333333, NVIDIA GeForce RTX 5090\n"
    "1, GPU-11111111-1111-1111-1111-111111111111, NVIDIA RTX PRO 6000 Blackwell\n"
)

CAPACITY_CSV = (
    "0, GPU-33333333-3333-3333-3333-333333333333, NVIDIA GeForce RTX 5090, 32607\n"
    "1, GPU-11111111-1111-1111-1111-111111111111, NVIDIA RTX PRO 6000 Blackwell, 97887\n"
)


def _run_ok(*a, **k):
    return CSV


def _run_capacity(*a, **k):
    return CAPACITY_CSV


def _run_tailnet(*a, **k):
    return "100.64.0.10\n"


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


# ---- run(): writes four files, mutually consistent -----------------------------


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


# ---- GPU pinning: UUID present / nvidia-smi absent (T007 wiring) ---------------


# ---- catalog-driven pick + thinking_default ------------------------------------


def test_init_catalog_thinking_default_disables_at_generation(tmp_path):
    catalog_dir = tmp_path / "model-library"
    _card(tmp_path, "thinky", local_path=str(tmp_path / "weights" / "thinky"), thinking_default=True)
    out_dir = tmp_path / "onboard"
    result = init.run(catalog_dir=str(catalog_dir), out_dir=str(out_dir), gpu="0", _run=_run_missing)
    compose = open(result["compose"], encoding="utf-8").read()
    assert "enable_thinking" in compose
    assert result["disable_thinking"] is True


# ---- CLI -------------------------------------------------------------------------


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
    "GPU-11111111-1111-1111-1111-111111111111",
    "GPU-33333333-3333-3333-3333-333333333333",
    "100.64.0.10",
)
_EXPECTED_HOME_FILES = {
    "router.toml", "example.toml", "example-docker.toml",
    "host.toml", "serve-recipes.toml", "image-builds.toml",
    "anvil-router.deepseek-pi.toml", "anvil-router.live.toml",
    "anvil-router.qwen35-rollback.toml",
    "serves.toml", "services.toml", "serves.voice.toml", "serves.comfyui.toml",
    "docker-compose.yml", "docker-compose.voice-audio.yml", "docker-compose.voice-proxy.yml",
    "docker-compose.comfyui.yml",
    "Dockerfile.omni-small", "Dockerfile.comfyui", "install-comfyui-node.sh",
    "operator-topology.toml", ".env.example", "voice.toml", "edge.toml",
}


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
        if key in {"COMPUTE_A_GPU_UUID", "COMPUTE_B_GPU_UUID"}:
            assert value.startswith("GPU-REPLACE-WITH-")
        else:
            assert value.strip() == "", f".env.example ships a non-empty secret: {line}"


def test_scaffold_home_is_idempotent_no_backup_on_first_run(tmp_path):
    result = _scaffold_home(out_dir=str(tmp_path))
    assert result["backed_up"] == []  # clean target: nothing to back up


def test_scaffold_home_injects_detected_host_values(tmp_path):
    result = init.scaffold_home(
        out_dir=str(tmp_path),
        _gpu_run=_run_capacity,
        _tailscale_run=_run_tailnet,
    )

    assert result["discovery"]["compute_a_gpu"]["source"] == "detected"
    compose = (tmp_path / "docker-compose.yml").read_text(encoding="utf-8")
    voice = (tmp_path / "docker-compose.voice-audio.yml").read_text(encoding="utf-8")
    assert _REAL_HOST_VALUES[0] in compose
    assert _REAL_HOST_VALUES[1] in compose
    assert _REAL_HOST_VALUES[2] in voice
    assert "GPU-REPLACE-WITH" not in compose
    assert "REPLACE-WITH-YOUR-TAILNET-IP" not in voice


def test_discover_host_rejects_non_tailnet_ip_override():
    with pytest.raises(init.InitError, match="100.64.0.0/10"):
        init.discover_host(tailnet_ip="192.168.1.10", probe=False)


def test_init_cli_config_home_backs_up_existing_operator_file(tmp_path, monkeypatch):
    home = tmp_path / "operator-home"
    home.mkdir()
    (home / "serves.toml").write_text("# operator hand edits\n", encoding="utf-8")
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(home))

    assert init.main(["--no-detect-host"]) == 0

    backup = home / "serves.toml.anvil.bak.1"
    assert backup.read_text(encoding="utf-8") == "# operator hand edits\n"


# ---- packaging: the set ships as package data and resolves like an installed tool ----
