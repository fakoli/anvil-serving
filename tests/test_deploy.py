"""Tests for `anvil_serving.deploy` — render a tuned docker-compose for one
local model serve. `nvidia-smi` / docker are injected, so these run with no
GPU, no docker, and no network.
"""
import os

import pytest

from anvil_serving import deploy

CSV = (
    "0, GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1, NVIDIA GeForce RTX 5090\n"
    "1, GPU-d0f446cf-1771-414c-e116-a39138798a8c, NVIDIA RTX PRO 6000 Blackwell\n"
)


def _run_ok(*a, **k):
    return CSV


def _run_missing(*a, **k):
    raise FileNotFoundError("nvidia-smi not found")


# ---- gpu_uuid / cuda_visible (genericity:T007) ---------------------------------

def test_deploy_gpu_index_resolves_to_uuid_env_block():
    out = deploy.render("/w/model", gpu=1, _run=_run_ok)
    assert "CUDA_VISIBLE_DEVICES: GPU-d0f446cf-1771-414c-e116-a39138798a8c" in out
    assert "CUDA_DEVICE_ORDER: PCI_BUS_ID" in out
    assert 'device_ids: ["GPU-d0f446cf-1771-414c-e116-a39138798a8c"]' in out


def test_deploy_gpu_uuid_spec_passthrough():
    uuid = "GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1"
    out = deploy.render("/w/model", gpu=uuid, _run=_run_ok)
    assert f"CUDA_VISIBLE_DEVICES: {uuid}" in out
    assert f'device_ids: ["{uuid}"]' in out


def test_deploy_nvidia_smi_absent_falls_back_to_integer_no_crash(capsys):
    out = deploy.render("/w/model", gpu=1, _run=_run_missing)
    assert 'device_ids: ["1"]' in out
    assert "CUDA_VISIBLE_DEVICES" not in out  # no unresolved env pin emitted
    err = capsys.readouterr().err
    assert "WARNING" in err and "nvidia-smi" in err


def test_deploy_cli_writes_compose(tmp_path, monkeypatch):
    out_path = tmp_path / "compose.yml"
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    rc = deploy.main(["--model", "/w/model", "--gpu", "0", "--out", str(out_path)])
    assert out_path.exists()
    assert "sglang.launch_server" in out_path.read_text(encoding="utf-8")
