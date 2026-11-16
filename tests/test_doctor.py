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


# ---- docker required only with managed serves ------------------------------------

def _no_docker(*a, **k):
    raise FileNotFoundError("docker")


def _docker_checks(checks):
    return [c for c in checks if c.name in ("docker", "docker compose v2")]


def test_docker_optional_on_gateway_only_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    checks = doctor.run_checks(_run=_no_docker, _gpu_run=lambda *a, **k: "")
    docker = _docker_checks(checks)
    assert len(docker) == 2
    assert all(not c.required and not c.ok for c in docker)
    assert all("optional" in c.detail for c in docker)
    assert all(c.required is False or c.ok for c in checks if "NVIDIA" in c.name)


def test_docker_required_with_managed_serves(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    (tmp_path / "serves.toml").write_text('[serves.a]\nimage = "x"\n', encoding="utf-8")
    docker = _docker_checks(doctor.run_checks(_run=_no_docker, _gpu_run=lambda *a, **k: ""))
    assert len(docker) == 2 and all(c.required and not c.ok for c in docker)


def test_docker_optional_with_empty_serves_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    (tmp_path / "serves.toml").write_text("  \n", encoding="utf-8")
    docker = _docker_checks(doctor.run_checks(_run=_no_docker, _gpu_run=lambda *a, **k: ""))
    assert all(not c.required for c in docker)


def test_doctor_cli_exits_zero_without_docker_on_gateway_only_home(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    monkeypatch.setattr(doctor.subprocess, "run", _no_docker)
    monkeypatch.setattr(doctor._gpus, "list_gpus", lambda **k: [])
    assert doctor.main(["--no-config"]) == 0


def test_docker_required_with_cwd_serves_manifest(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (work / "serves.toml").write_text('[serves.a]\nimage = "x"\n', encoding="utf-8")
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(home))
    monkeypatch.chdir(work)
    docker = _docker_checks(doctor.run_checks(_run=_no_docker, _gpu_run=lambda *a, **k: ""))
    assert len(docker) == 2 and all(c.required and not c.ok for c in docker)
