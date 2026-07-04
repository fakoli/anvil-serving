"""Tests for `anvil-serving router` — manage the DEPLOYED (ADR-0004) anvil-router.

Docker + HTTP + sleep are injected (`_run`/`_open`/`_sleep` seams), so these run with
no docker, no network, and no real waiting.
"""
import json
import types

import pytest

from anvil_serving import router_manage as rm


def proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class FakeRun:
    """Capture argv (and stdin `input`) for every docker call; dispatch state.

    `state_seq` (optional) feeds successive `docker inspect` results — so a promote
    can see a container go 'restarting' then 'running' (or stay crashed).
    """

    def __init__(self, state="running", state_seq=None, fail_prefixes=(), val_rc=0):
        self.calls = []
        self.inputs = []
        self._state = state
        self._state_seq = list(state_seq) if state_seq else None
        self._fail_prefixes = fail_prefixes  # list of argv-slices that should fail
        self._val_rc = val_rc

    def _next_state(self):
        if self._state_seq:
            return self._state_seq.pop(0) if len(self._state_seq) > 1 else self._state_seq[0]
        return self._state

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        self.inputs.append(kw.get("input"))
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            st = self._next_state()
            if st == "absent":
                return proc(1, "", "Error: No such object")
            if st == "error":
                return proc(1, "", "Cannot connect to the Docker daemon")
            return proc(0, st + "\n")
        # validator: `docker run ... --entrypoint python ...`
        if isinstance(argv, list) and "--entrypoint" in argv and "python" in argv:
            return proc(self._val_rc, "", "schema mismatch" if self._val_rc else "")
        for pref in self._fail_prefixes:
            if argv[:len(pref)] == pref:
                return proc(1, "", "boom")
        return proc(0)

    def argvs_matching(self, *needles):
        """argvs containing every needle token (in any position)."""
        return [a for a in self.calls
                if all(n in a for n in needles) and a[:2] != ["docker", "inspect"]]


def _find_index(calls, *needles):
    for i, a in enumerate(calls):
        if all(n in a for n in needles):
            return i
    return -1


# ---- lifecycle: up / down / restart / reload --------------------------------

def test_up_delegates_to_compose_up_d():
    run = FakeRun()
    rc = rm.cmd_up("/c.yml", "router", _run=run)
    assert rc == 0
    assert run.calls == [["docker", "compose", "-f", "/c.yml", "up", "-d", "router"]]


def test_down_delegates_to_compose_stop():
    run = FakeRun()
    rc = rm.cmd_down("/c.yml", "router", _run=run)
    assert rc == 0
    assert run.calls == [["docker", "compose", "-f", "/c.yml", "stop", "router"]]


def test_restart_uses_docker_restart_on_container():
    run = FakeRun()
    rc = rm.cmd_restart("anvil-router", _run=run)
    assert rc == 0
    assert run.calls == [["docker", "restart", "anvil-router"]]


def test_reload_is_a_restart_with_a_note(capsys):
    run = FakeRun()
    rc = rm.cmd_reload("anvil-router", _run=run)
    assert rc == 0
    assert run.calls == [["docker", "restart", "anvil-router"]]
    out = capsys.readouterr().out
    assert "STARTUP" in out and "restart" in out.lower()


def test_lifecycle_failure_returns_1():
    run = FakeRun(fail_prefixes=[["docker", "restart"]])
    assert rm.cmd_restart("anvil-router", _run=run) == 1


def test_dry_run_runs_nothing_for_lifecycle():
    run = FakeRun()
    assert rm.cmd_up("/c.yml", "router", dry_run=True, _run=run) == 0
    assert rm.cmd_restart("anvil-router", dry_run=True, _run=run) == 0
    assert run.calls == []  # nothing executed


# ---- status -----------------------------------------------------------------

class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_status_running_probes_health(capsys):
    run = FakeRun(state="running")
    opened = {}

    def fake_open(url, timeout=None):
        opened["url"] = url
        return _Resp()

    rc = rm.cmd_status("anvil-router", _run=run, _open=fake_open)
    assert rc == 0
    out = capsys.readouterr().out
    assert "running:          yes" in out
    assert "200" in out
    assert opened["url"] == "http://127.0.0.1:8000/"


def test_status_absent_reports_not_running(capsys):
    run = FakeRun(state="absent")
    rc = rm.cmd_status("anvil-router", _run=run, _open=None)  # _open unused when down
    assert rc == 0
    assert "running:          no" in capsys.readouterr().out


def test_status_docker_error_returns_1(capsys):
    run = FakeRun(state="error")
    assert rm.cmd_status("anvil-router", _run=run, _open=None) == 1
    assert "UNKNOWN" in capsys.readouterr().out


# ---- token ------------------------------------------------------------------

def test_token_prints_value(capsys):
    def run(argv, **kw):
        assert argv == ["docker", "exec", "anvil-router", "printenv", "ANVIL_ROUTER_TOKEN"]
        return proc(0, "s3cr3t\n")
    out_rc = rm.cmd_token("anvil-router", _run=run)
    assert out_rc == 0
    assert capsys.readouterr().out.strip() == "s3cr3t"


def test_token_unset_reports_auth_off(capsys):
    run = lambda argv, **kw: proc(1, "", "")  # printenv exits 1 when unset
    assert rm.cmd_token("anvil-router", _run=run) == 0
    assert "UNSET" in capsys.readouterr().out


# ---- promote: happy path ----------------------------------------------------

def _profile(tmp_path):
    p = tmp_path / "profile.json"
    p.write_text(json.dumps({"schema": "v2", "rows": []}), encoding="utf-8")
    return str(p)


def test_promote_happy_path_orders_validate_backup_write_restart(tmp_path):
    run = FakeRun(state="running")
    rc = rm.cmd_promote(_profile(tmp_path), container="anvil-router",
                        cfg_volume="anvil-router-cfg", image="anvil-serving:0.7.1",
                        _run=run, _sleep=lambda *a: None)
    assert rc == 0

    calls = run.calls
    # validate (python entrypoint) fed the profile on stdin
    i_val = next(i for i, a in enumerate(calls)
                 if "--entrypoint" in a and "python" in a)
    assert run.inputs[i_val] is not None and "schema" in run.inputs[i_val]
    # backup uses cp to the .bak inside the volume-mounted side container
    i_bak = next(i for i, a in enumerate(calls)
                 if any("cp /cfg/profile.json /cfg/profile.json.bak" in tok for tok in a))
    # atomic write: cat > .new && mv .new profile.json
    i_write = next(i for i, a in enumerate(calls)
                   if any("mv /cfg/profile.json.new /cfg/profile.json" in tok for tok in a))
    i_restart = _find_index(calls, "docker", "restart", "anvil-router")
    # strict ordering: validate -> backup -> write -> restart
    assert i_val < i_bak < i_write < i_restart

    # the write side-container mounts the cfg volume as root and receives the profile
    write_argv = calls[i_write]
    assert "--user" in write_argv and "0" in write_argv
    assert "anvil-router-cfg:/cfg" in write_argv
    assert run.inputs[i_write] is not None  # profile piped on stdin
    # MUST override the router image's default `anvil-serving serve` entrypoint with `sh`,
    # else the side-container tries to start the router instead of writing (dogfooded 2026-07-04).
    assert write_argv[write_argv.index("--entrypoint") + 1] == "sh"
    # every file-op side-container (backup/write/rollback) overrides the entrypoint
    for a in calls:
        if a[:3] == ["docker", "run", "--rm"] and "-v" in a and "--entrypoint" not in a:
            # the only entrypoint-less `docker run` is... none: validate uses python, ops use sh
            assert False, "file-op side-container missing --entrypoint override: %r" % a


def test_promote_writes_config_when_given(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[router]\nprofile_path = "/etc/anvil/profile.json"\n', encoding="utf-8")
    run = FakeRun(state="running")
    rc = rm.cmd_promote(_profile(tmp_path), config_path=str(cfg),
                        _run=run, _sleep=lambda *a: None)
    assert rc == 0
    assert any(any("mv /cfg/config.toml.new /cfg/config.toml" in tok for tok in a)
               for a in run.calls)


def test_promote_rejects_config_without_profile_path(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[router]\ntier = "x"\n', encoding="utf-8")  # no profile_path
    run = FakeRun(state="running")
    rc = rm.cmd_promote(_profile(tmp_path), config_path=str(cfg),
                        _run=run, _sleep=lambda *a: None)
    assert rc == 1
    assert run.calls == []  # aborted before touching docker


# ---- promote: validation failure aborts BEFORE any volume write -------------

def test_promote_validation_failure_aborts_before_write(tmp_path, capsys):
    run = FakeRun(state="running", val_rc=1)  # deployed loader rejects the profile
    rc = rm.cmd_promote(_profile(tmp_path), _run=run, _sleep=lambda *a: None)
    assert rc == 1
    # ONLY the validator ran; no volume-mounting side container, no restart.
    assert all("-v" not in a for a in run.calls)
    assert all(a[:2] != ["docker", "restart"] for a in run.calls)
    assert "ABORT" in capsys.readouterr().out


def test_promote_bad_json_aborts_without_docker(tmp_path):
    p = tmp_path / "profile.json"
    p.write_text("{not json", encoding="utf-8")
    run = FakeRun()
    assert rm.cmd_promote(str(p), _run=run, _sleep=lambda *a: None) == 1
    assert run.calls == []


# ---- promote: --no-reload skips the restart ---------------------------------

def test_promote_no_reload_skips_restart(tmp_path):
    run = FakeRun(state="running")
    rc = rm.cmd_promote(_profile(tmp_path), no_reload=True,
                        _run=run, _sleep=lambda *a: None)
    assert rc == 0
    assert all(a[:2] != ["docker", "restart"] for a in run.calls)
    # but the write DID happen
    assert any(any("mv /cfg/profile.json.new" in tok for tok in a) for a in run.calls)


# ---- promote: --dry-run runs nothing ----------------------------------------

def test_promote_dry_run_runs_no_docker(tmp_path, capsys):
    run = FakeRun()
    rc = rm.cmd_promote(_profile(tmp_path), dry_run=True,
                        _run=run, _sleep=lambda *a: None)
    assert rc == 0
    assert run.calls == []
    out = capsys.readouterr().out
    assert "validate" in out and "write profile" in out


# ---- promote: crash-loop triggers rollback ----------------------------------

def test_promote_crash_loop_rolls_back(tmp_path, capsys):
    # restart succeeds, but the container never reaches 'running' -> crash-loop.
    run = FakeRun(state="restarting")
    rc = rm.cmd_promote(_profile(tmp_path), _run=run, _sleep=lambda *a: None)
    assert rc == 1
    # rollback restores the .bak over the live profile, then restarts again.
    assert any(any("mv /cfg/profile.json.bak /cfg/profile.json" in tok for tok in a)
               for a in run.calls)
    restart_calls = [a for a in run.calls if a[:2] == ["docker", "restart"]]
    assert len(restart_calls) == 2  # initial reload + post-rollback restart
    assert "CRASH" in capsys.readouterr().out


def test_promote_crash_loop_detected_when_restart_count_grows(tmp_path, capsys):
    """The REALISTIC race the naive first-'running' check missed: a fail-fast router
    exits and `restart: unless-stopped` bounces it back to 'running' before we look, so
    docker_state reads 'running' — but its RestartCount grew since our manual restart.
    _await_running must treat that as a crash-loop and roll back."""
    counts = iter([0, 1, 1, 1, 1, 1])  # baseline 0, then the policy re-launched it (1)
    def run(argv, **kw):
        if argv[:2] == ["docker", "inspect"]:
            tmpl = argv[3] if len(argv) > 3 else ""
            if "RestartCount" in tmpl:
                return proc(0, "%d\n" % next(counts))
            return proc(0, "running\n")   # .State.Status always reads 'running'
        if "--entrypoint" in argv and "python" in argv:
            return proc(0)                # validator passes
        return proc(0)
    rc = rm.cmd_promote(_profile(tmp_path), _run=run, _sleep=lambda *a: None)
    assert rc == 1  # detected the bounce despite 'running' state -> rolled back
    assert "CRASH" in capsys.readouterr().out


def test_promote_first_ever_rollback_removes_the_bad_profile(tmp_path, capsys):
    """First-ever promote (no prior profile -> no .bak): a crash-loop rollback must
    REMOVE the promoted profile (restore the pre-promote 'no profile' state), not no-op."""
    run = FakeRun(state="restarting")  # never comes up -> crash
    rc = rm.cmd_promote(_profile(tmp_path), _run=run, _sleep=lambda *a: None)
    assert rc == 1
    # rollback command handles the no-.bak case with an `rm -f` of the live profile.
    assert any(any("rm -f /cfg/profile.json" in tok for tok in a) for a in run.calls)


# ---- CLI dispatch -----------------------------------------------------------

def test_main_promote_requires_profile(capsys):
    assert rm.main(["promote"]) == 2
    assert "--profile" in capsys.readouterr().err


def test_main_status_dispatches(monkeypatch):
    seen = {}
    def fake_status(c, **k):
        seen["c"] = c
        return 0
    monkeypatch.setattr(rm, "cmd_status", fake_status)
    assert rm.main(["status", "--container", "my-router"]) == 0
    assert seen["c"] == "my-router"


def test_defaults_match_deployed_compose():
    assert rm.DEFAULT_CONTAINER == "anvil-router"
    assert rm.DEFAULT_CFG_VOLUME == "anvil-router-cfg"
    assert rm.DEFAULT_COMPOSE.endswith("docker-compose.yml")
    assert "fakoli-dark" in rm.DEFAULT_COMPOSE
