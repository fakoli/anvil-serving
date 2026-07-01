"""Tests for `anvil-serving doctor` — environment preflight (genericity:T015).
docker / nvidia-smi / HTTP are injected, so these run with no docker, no GPU,
and no network.
"""
import types

import pytest

from anvil_serving import doctor


def proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


CSV = "0, GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1, NVIDIA GeForce RTX 5090\n"


# ---- individual checks -----------------------------------------------------------

def test_check_python_passes_on_modern_python():
    c = doctor.check_python(min_version=(3, 8))
    assert c.ok and c.required and c.status == "PASS"


def test_check_python_fails_below_minimum():
    c = doctor.check_python(min_version=(99, 0))
    assert not c.ok and c.status == "FAIL"


def test_check_docker_present():
    c = doctor.check_docker(_run=lambda *a, **k: proc(0, "Docker version 29.0.0"))
    assert c.ok and c.status == "PASS"


def test_check_docker_missing_is_required_fail():
    def boom(*a, **k):
        raise FileNotFoundError("docker not found")
    c = doctor.check_docker(_run=boom)
    assert not c.ok and c.required and c.status == "FAIL"


def test_check_docker_compose_v2_present():
    c = doctor.check_docker_compose(_run=lambda *a, **k: proc(0, "Docker Compose version v2.29.0"))
    assert c.ok and c.status == "PASS"


def test_check_docker_compose_missing_is_fail():
    def boom(*a, **k):
        raise FileNotFoundError("no compose plugin")
    c = doctor.check_docker_compose(_run=boom)
    assert not c.ok and c.status == "FAIL"


def test_check_nvidia_runtime_detected():
    c = doctor.check_nvidia_runtime(
        _run=lambda *a, **k: proc(0, '{"nvidia":{"path":"nvidia-container-runtime"},"runc":{}}'))
    assert c.ok and not c.required and c.status == "PASS"


def test_check_nvidia_runtime_absent_is_warn_not_fail():
    c = doctor.check_nvidia_runtime(_run=lambda *a, **k: proc(0, '{"runc":{}}'))
    assert not c.ok and not c.required and c.status == "WARN"


def test_check_nvidia_runtime_docker_missing_is_warn():
    def boom(*a, **k):
        raise FileNotFoundError()
    c = doctor.check_nvidia_runtime(_run=boom)
    assert not c.ok and c.status == "WARN"


def test_check_gpu_visibility_present():
    c = doctor.check_gpu_visibility(_run=lambda *a, **k: CSV)
    assert c.ok and c.status == "PASS"
    assert "RTX 5090" in c.detail


def test_check_gpu_visibility_absent_is_warn_no_crash():
    def boom(*a, **k):
        raise FileNotFoundError("nvidia-smi not found")
    c = doctor.check_gpu_visibility(_run=boom)
    assert not c.ok and not c.required and c.status == "WARN"


def test_check_tier_health_reachable():
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    c = doctor.check_tier_health("fast-local", "http://127.0.0.1:30001/v1",
                                 _open=lambda url, timeout=3: Resp())
    assert c.ok and c.status == "PASS"


def test_check_tier_health_strips_trailing_v1_from_base_url():
    seen = {}
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def _open(url, timeout=3):
        seen["url"] = url
        return Resp()
    doctor.check_tier_health("fast-local", "http://127.0.0.1:30001/v1", _open=_open)
    assert seen["url"] == "http://127.0.0.1:30001/health"


def test_check_tier_health_unreachable_is_warn_not_fail():
    def boom(url, timeout=3):
        raise ConnectionRefusedError("refused")
    c = doctor.check_tier_health("fast-local", "http://127.0.0.1:30001/v1", _open=boom)
    assert not c.ok and not c.required and c.status == "WARN"


# ---- run_checks: config wiring ----------------------------------------------------

def _ok_run(*a, **k):
    return proc(0, "ok")


def test_run_checks_no_config_skips_tier_section():
    checks = doctor.run_checks(config_path=None, _run=_ok_run, _gpu_run=lambda *a, **k: CSV)
    names = [c.name for c in checks]
    assert not any("tier" in n or "router config" in n for n in names)


def test_run_checks_missing_default_config_skips_quietly():
    checks = doctor.run_checks(config_path="./does-not-exist.toml", config_explicit=False,
                               _run=_ok_run, _gpu_run=lambda *a, **k: CSV)
    assert not any(not c.ok and c.required for c in checks)  # no FAIL from the missing default


def test_run_checks_missing_explicit_config_is_required_fail():
    checks = doctor.run_checks(config_path="./does-not-exist.toml", config_explicit=True,
                               _run=_ok_run, _gpu_run=lambda *a, **k: CSV)
    failed = [c for c in checks if c.required and not c.ok]
    assert any("router config" in c.name for c in failed)


def test_run_checks_probes_each_tier_from_a_real_config(tmp_path):
    cfg_path = tmp_path / "router.toml"
    cfg_path.write_text(
        '[router]\nmapping_version = "1"\n'
        '[[router.tiers]]\n'
        'id = "fast-local"\nbase_url = "http://127.0.0.1:30001/v1"\nmodel = "m"\n'
        'dialect = "openai"\ncontext_limit = 1000\nprivacy = "local"\ntool_support = true\n'
        'auth_env = "ANVIL_FAST_LOCAL_KEY"\n'
        '[router.presets]\nchat = ["fast-local"]\n',
        encoding="utf-8",
    )
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    checks = doctor.run_checks(config_path=str(cfg_path), config_explicit=True,
                               _run=_ok_run, _gpu_run=lambda *a, **k: CSV,
                               _open=lambda url, timeout=3: Resp())
    tier_checks = [c for c in checks if "fast-local" in c.name]
    assert len(tier_checks) == 1 and tier_checks[0].ok


# ---- CLI -------------------------------------------------------------------------

def test_doctor_cli_all_pass_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_checks", lambda **k: [doctor.Check("x", True, "", required=True)])
    rc = doctor.main(["--no-config"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[PASS] x" in out and "OK" in out


def test_doctor_cli_required_failure_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_checks",
                        lambda **k: [doctor.Check("x", False, "boom", required=True)])
    rc = doctor.main(["--no-config"])
    assert rc == 1
    err_out = capsys.readouterr().out
    assert "[FAIL] x" in err_out
    assert "FAILED" in err_out


def test_doctor_cli_warn_only_still_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_checks",
                        lambda **k: [doctor.Check("x", False, "meh", required=False)])
    rc = doctor.main(["--no-config"])
    assert rc == 0
    assert "[WARN] x" in capsys.readouterr().out


def test_doctor_cli_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        doctor.main(["--help"])
    assert exc.value.code == 0


def test_cli_dispatches_doctor(monkeypatch):
    from anvil_serving import cli
    monkeypatch.setattr(doctor, "run_checks",
                        lambda **k: [doctor.Check("x", True, "", required=True)])
    assert cli.main(["doctor", "--no-config"]) == 0
