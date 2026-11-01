"""Tests for `anvil-serving doctor` — environment preflight (genericity:T015).
docker / nvidia-smi / HTTP are injected, so these run with no docker, no GPU,
and no network.
"""

from anvil_serving import doctor
from tests.conftest import proc


CSV = "0, GPU-33333333-3333-3333-3333-333333333333, NVIDIA GeForce RTX 5090\n"


# ---- individual checks -----------------------------------------------------------


# ---- run_checks: config wiring ----------------------------------------------------

def _ok_run(*a, **k):
    return proc(0, "ok")


def test_run_checks_probes_each_tier_from_a_real_config(tmp_path):
    cfg_path = tmp_path / "router.toml"
    cfg_path.write_text(
        '[router]\n[router.model_routes]\n"llm.primary" = "auxiliary-local"\n'
        '[[router.tiers]]\n'
        'id = "auxiliary-local"\nbase_url = "http://127.0.0.1:30001/v1"\nmodel = "m"\n'
        'dialect = "openai"\ncontext_limit = 1000\nprivacy = "local"\ntool_support = true\n'
        'auth_env = "ANVIL_AUXILIARY_LOCAL_KEY"\n',
        encoding="utf-8",
    )
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    checks = doctor.run_checks(config_path=str(cfg_path), config_explicit=True,
                               _run=_ok_run, _gpu_run=lambda *a, **k: CSV,
                               _open=lambda url, timeout=3: Resp())
    tier_checks = [c for c in checks if "auxiliary-local" in c.name]
    assert len(tier_checks) == 1 and tier_checks[0].ok


# ---- CLI -------------------------------------------------------------------------


def test_doctor_cli_required_failure_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_checks",
                        lambda **k: [doctor.Check("x", False, "boom", required=True)])
    rc = doctor.main(["--no-config"])
    assert rc == 1
    err_out = capsys.readouterr().out
    assert "[FAIL] x" in err_out
    assert "FAILED" in err_out
