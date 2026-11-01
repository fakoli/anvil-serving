"""Tests for `anvil_serving.deploy` — render a tuned docker-compose for one
local model serve. `nvidia-smi` / docker are injected, so these run with no
GPU, no docker, and no network.
"""

import textwrap

import pytest

from anvil_serving import deploy

CSV = (
    "0, GPU-33333333-3333-3333-3333-333333333333, NVIDIA GeForce RTX 5090\n"
    "1, GPU-11111111-1111-1111-1111-111111111111, NVIDIA RTX PRO 6000 Blackwell\n"
)


def _run_ok(*a, **k):
    return CSV


def _run_missing(*a, **k):
    raise FileNotFoundError("nvidia-smi not found")


# ---- gpu_uuid / cuda_visible (genericity:T007) ---------------------------------


# ---- loopback default / --expose-lan (genericity:T008) -------------------------

def test_deploy_loopback_default_publishes_127_0_0_1():
    out = deploy.render("/w/model", gpu=0, port=30000, _run=_run_missing)
    assert 'ports: ["127.0.0.1:30000:30000"]' in out


# ---- emits serves.toml entry + router-tier stub (genericity:T009) --------------


# ---- vLLM engine branch (genericity:T010) ---------------------------------------


# ---- thinking-disable at generation time (genericity:T011) ---------------------


# ---- engine-enforced reservation budgets (gpu-reservations:T003, ADR-0017 §4) ---

def _roles_manifest(tmp_path):
    """A serves manifest declaring one [[gpu_roles]] capacity row:
    32768 MiB capacity - 2768 MiB reserve = 30000 MiB budget."""
    manifest = tmp_path / "serves.toml"
    manifest.write_text(
        textwrap.dedent("""
            [[gpu_roles]]
            id = "dark-fast"
            vram_mib = 32768
            reserve_mib = 2768
        """),
        encoding="utf-8",
    )
    return manifest


def test_deploy_reserved_serve_entry_carries_reservation_fields(tmp_path, monkeypatch):
    out_path = tmp_path / "compose.yml"
    manifest = _roles_manifest(tmp_path)
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    deploy.main([
        "--model", "/w/model", "--out", str(out_path), "--engine", "vllm",
        "--served-name", "auxiliary-local", "--port", "30001",
        "--manifest-out", str(manifest),
        "--gpu-role", "dark-fast", "--vram-mib", "24000", "--residency", "on-demand",
    ])
    # The appended [[serve]] entry parses back with the reservation attached,
    # so `serves up` admission (T002) sees exactly what the engine enforces.
    from anvil_serving import reservations
    parsed = deploy._serves.load_manifest(str(manifest))
    assert len(parsed) == 1
    assert parsed[0]["gpu_role"] == "dark-fast"
    assert parsed[0]["vram_mib"] == 24000
    assert parsed[0]["residency"] == "on-demand"
    reservation = reservations.reservation_of(parsed[0])
    assert reservation is not None
    assert reservation.vram_mib == 24000
    budgets = reservations.budgets_of(parsed)
    assert budgets["dark-fast"].budget_mib == 30000


def test_deploy_reservation_over_budget_fails_before_writing(tmp_path, monkeypatch):
    out_path = tmp_path / "compose.yml"
    manifest = _roles_manifest(tmp_path)
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    rc = deploy.main([
        "--model", "/w/model", "--out", str(out_path),
        "--manifest-out", str(manifest),
        "--gpu-role", "dark-fast", "--vram-mib", "30001",   # budget is 30000
    ])
    assert rc == 2
    assert not out_path.exists()
    # nothing appended either: the manifest still has only the capacity row.
    assert "[[serve]]" not in manifest.read_text(encoding="utf-8")


def test_deploy_reservation_flags_must_come_together(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    for partial in (["--gpu-role", "dark-fast"], ["--vram-mib", "1000"]):
        with pytest.raises(SystemExit) as exc:
            deploy.main(["--model", "/w/model", "--out", str(tmp_path / "c.yml"),
                        "--no-manifest"] + partial)
        assert exc.value.code == 2
