"""Tests for `serves up` ensuring the DEPLOYED router is healthy first.

`serves up` reuses the `router` verb's own status-probe and bring-up code paths
to guarantee the router is up before starting serves — on hosts whose operator
topology owns a router. A topology that assigns the router role to another
host skips the step entirely; a topology that assigns it HERE makes a failed
bring-up gate the serves (non-zero return). Without a positive topology answer
(no file, invalid file) the historical co-located default applies and failures
stay non-gating. Docker + HTTP are injected (`_run`/`_open`), and every test
passes an explicit `topology_path` so no test reads the developer machine's
real operator config home.
"""
import pytest

from anvil_serving import serves
from tests.conftest import proc


class FakeRun:
    """Capture argv for every docker call; answer `docker inspect` with `state`."""

    def __init__(self, state="running", up_rc=0, compose_project="anvil-serving"):
        self.calls = []
        self._state = state
        self._up_rc = up_rc
        self._compose_project = compose_project

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            if self._state == "absent":
                return proc(1, "", "Error: No such object")
            if self._state == "error":
                return proc(1, "", "Cannot connect to the Docker daemon")
            if "com.docker.compose.project" in " ".join(argv):
                return proc(0, self._compose_project + "\n")
            return proc(0, self._state + "\n")
        # docker compose ... up ... -> the router bring-up
        if isinstance(argv, list) and "up" in argv:
            return proc(self._up_rc, "", "boom" if self._up_rc else "")
        return proc(0)

    def ran_up(self):
        return any(
            isinstance(a, list) and a[:2] == ["docker", "compose"] and "up" in a
            for a in self.calls
        )


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _open_ok(url, timeout=None):
    return _Resp()


def _open_down(url, timeout=None):
    raise OSError("connection refused")


_TOPOLOGY_ROUTER_ELSEWHERE = """\
schema_version = 1
id = "test-topology"
command_host = "host:alpha"
command_runtime = "runtime:alpha-docker"

[[hosts]]
id = "alpha"
roles = ["operator", "serve"]

[[hosts]]
id = "beta"
roles = ["router"]

[[runtimes]]
id = "alpha-docker"
host = "alpha"
role = "docker"
"""

_TOPOLOGY_ROUTER_HERE = """\
schema_version = 1
id = "test-topology"
command_host = "host:alpha"
command_runtime = "runtime:alpha-docker"

[[hosts]]
id = "alpha"
roles = ["operator", "serve", "router"]

[[runtimes]]
id = "alpha-docker"
host = "alpha"
role = "docker"
"""


@pytest.fixture
def no_topology(tmp_path):
    """A topology path that does not exist -> no ownership answer (legacy)."""
    return str(tmp_path / "missing-operator-topology.toml")


@pytest.fixture
def router_elsewhere(tmp_path, monkeypatch):
    monkeypatch.delenv("ANVIL_COMMAND_HOST", raising=False)
    monkeypatch.delenv("ANVIL_COMMAND_RUNTIME", raising=False)
    path = tmp_path / "operator-topology.toml"
    path.write_text(_TOPOLOGY_ROUTER_ELSEWHERE, encoding="utf-8")
    return str(path)


@pytest.fixture
def router_here(tmp_path, monkeypatch):
    monkeypatch.delenv("ANVIL_COMMAND_HOST", raising=False)
    monkeypatch.delenv("ANVIL_COMMAND_RUNTIME", raising=False)
    path = tmp_path / "operator-topology.toml"
    path.write_text(_TOPOLOGY_ROUTER_HERE, encoding="utf-8")
    return str(path)


def test_router_already_healthy_is_a_noop(capsys, no_topology):
    # running container + a front-door HTTP response == healthy -> do nothing,
    # and crucially do NOT restart it.
    run = FakeRun(state="running")
    rc = serves.ensure_router_healthy(
        topology_path=no_topology, _run=run, _open=_open_ok)
    assert rc == 0
    assert "router: already healthy" in capsys.readouterr().out
    assert not run.ran_up()  # idempotent: a healthy router is never brought up


def test_running_but_loopback_silent_is_healthy(capsys, no_topology):
    # The live tailnet-publish case: the router is running and docker-healthy but
    # published on a tailnet IP, so the loopback HTTP probe answers nothing. It
    # must still be "already healthy" and NOT restarted (requiring a loopback 200
    # would needlessly bounce every tailnet-published router on each `serves up`).
    run = FakeRun(state="running")
    rc = serves.ensure_router_healthy(
        topology_path=no_topology, _run=run, _open=_open_down)
    assert rc == 0
    assert "router: already healthy" in capsys.readouterr().out
    assert not run.ran_up()


def test_router_absent_gets_started(capsys, no_topology):
    # a non-existent container is "not healthy" -> bring it up.
    run = FakeRun(state="absent", up_rc=0)
    rc = serves.ensure_router_healthy(
        topology_path=no_topology, _run=run, _open=_open_down, env_file="")
    assert rc == 0
    assert "router: started" in capsys.readouterr().out
    assert run.ran_up()


def test_router_exited_gets_started(capsys, no_topology):
    # a stopped (exited) container -> not running -> bring it up.
    run = FakeRun(state="exited", up_rc=0)
    rc = serves.ensure_router_healthy(
        topology_path=no_topology, _run=run, _open=_open_down, env_file="")
    assert rc == 0
    assert "router: started" in capsys.readouterr().out
    assert run.ran_up()


def test_router_exited_with_foreign_owner_is_not_silently_replaced(
        capsys, no_topology):
    run = FakeRun(state="exited", compose_project="fakoli-dark")

    rc = serves.ensure_router_healthy(
        topology_path=no_topology, _run=run, _open=_open_down, env_file="")

    # the refusal is reported but non-gating without a topology owner claim
    assert rc == 0
    assert not run.ran_up()
    assert "--recreate" in capsys.readouterr().err


def test_docker_error_is_reported_non_gating(capsys, no_topology):
    # docker unreachable -> cannot probe or start; report but let the serves'
    # own docker commands produce the authoritative failure.
    run = FakeRun(state="error")
    rc = serves.ensure_router_healthy(
        topology_path=no_topology, _run=run, _open=_open_down)
    assert rc == 0
    assert "cannot determine health" in capsys.readouterr().out
    assert not run.ran_up()


def test_no_router_skips_everything(capsys, no_topology):
    # --no-router: no probe, no bring-up, no docker calls at all.
    run = FakeRun(state="absent")
    rc = serves.ensure_router_healthy(
        no_router=True, topology_path=no_topology, _run=run, _open=_open_down)
    assert rc == 0
    assert "router: skipped" in capsys.readouterr().out
    assert run.calls == []  # nothing probed, nothing started


def test_dry_run_reports_but_does_not_start(capsys, no_topology):
    # router down + --dry-run: report the action, do NOT bring it up.
    run = FakeRun(state="absent")
    rc = serves.ensure_router_healthy(
        dry_run=True, topology_path=no_topology, _run=run, _open=_open_down)
    out = capsys.readouterr().out
    assert rc == 0
    assert "would start" in out
    assert "router: started" not in out
    assert not run.ran_up()  # dry-run touched no container


def test_dry_run_when_healthy_still_reports_healthy(capsys, no_topology):
    # the live-box verify case: router up + --dry-run -> "already healthy".
    run = FakeRun(state="running")
    rc = serves.ensure_router_healthy(
        dry_run=True, topology_path=no_topology, _run=run, _open=_open_ok)
    assert rc == 0
    assert "router: already healthy" in capsys.readouterr().out
    assert not run.ran_up()


def test_failed_bring_up_without_topology_answer_is_non_gating(
        capsys, no_topology):
    # the historical co-located default: a router that fails to come up is
    # reported but does not stop the serves.
    run = FakeRun(state="absent", up_rc=1)
    rc = serves.ensure_router_healthy(
        topology_path=no_topology, _run=run, _open=_open_down, env_file="")
    out = capsys.readouterr().out
    assert rc == 0
    assert "FAILED to start" in out
    assert "bringing serves up anyway" in out
    assert run.ran_up()


def test_topology_router_elsewhere_skips_without_touching_docker(capsys, router_elsewhere):
    # A topology that assigns the router role to another host: the ensure step
    # is not this host's concern -- no probe, no bring-up, no docker calls, and
    # no "FAILED" noise for operators to learn to ignore.
    run = FakeRun(state="absent")
    rc = serves.ensure_router_healthy(
        topology_path=router_elsewhere, _run=run, _open=_open_down)
    assert rc == 0
    assert "not this host's role" in capsys.readouterr().out
    assert run.calls == []


def test_topology_router_here_failed_bring_up_gates(capsys, router_here):
    # This host is the declared router owner: a failed bring-up must gate the
    # serves (proceeding past a failed dependency this host owns is worse than
    # stopping), with --no-router as the explicit override.
    run = FakeRun(state="absent", up_rc=1)
    rc = serves.ensure_router_healthy(
        topology_path=router_here, _run=run, _open=_open_down, env_file="")
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAILED to start" in out
    assert "not bringing serves up" in out
    assert "--no-router" in out
    assert run.ran_up()


def test_topology_router_here_healthy_is_still_a_noop(capsys, router_here):
    run = FakeRun(state="running")
    rc = serves.ensure_router_healthy(
        topology_path=router_here, _run=run, _open=_open_ok)
    assert rc == 0
    assert "router: already healthy" in capsys.readouterr().out
    assert not run.ran_up()


def test_invalid_topology_falls_back_to_the_co_located_default(
        capsys, tmp_path):
    # an unparseable topology gives no ownership answer -> legacy behavior
    # (probe and bring up), not a crash and not a silent skip.
    path = tmp_path / "operator-topology.toml"
    path.write_text("this is [not toml", encoding="utf-8")
    run = FakeRun(state="absent", up_rc=0)
    rc = serves.ensure_router_healthy(
        topology_path=str(path), _run=run, _open=_open_down, env_file="")
    assert rc == 0
    assert "router: started" in capsys.readouterr().out
    assert run.ran_up()
