"""Tests for `serves up` ensuring the DEPLOYED router is healthy first.

`serves up` reuses the `router` verb's own status-probe and bring-up code paths
to guarantee the router is up before starting serves (serves are only reachable
behind it). Docker + HTTP are injected (`_run`/`_open`), so these run with no
docker and no network.
"""
import types

from anvil_serving import serves


def proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class FakeRun:
    """Capture argv for every docker call; answer `docker inspect` with `state`."""

    def __init__(self, state="running", up_rc=0):
        self.calls = []
        self._state = state
        self._up_rc = up_rc

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            if self._state == "absent":
                return proc(1, "", "Error: No such object")
            if self._state == "error":
                return proc(1, "", "Cannot connect to the Docker daemon")
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


def test_router_already_healthy_is_a_noop(capsys):
    # running container + a front-door HTTP response == healthy -> do nothing,
    # and crucially do NOT restart it.
    run = FakeRun(state="running")
    rc = serves.ensure_router_healthy(_run=run, _open=_open_ok)
    assert rc == 0
    assert "router: already healthy" in capsys.readouterr().out
    assert not run.ran_up()  # idempotent: a healthy router is never brought up


def test_running_but_loopback_silent_is_healthy(capsys):
    # The live tailnet-publish case: the router is running and docker-healthy but
    # published on a tailnet IP, so the loopback HTTP probe answers nothing. It
    # must still be "already healthy" and NOT restarted (requiring a loopback 200
    # would needlessly bounce every tailnet-published router on each `serves up`).
    run = FakeRun(state="running")
    rc = serves.ensure_router_healthy(_run=run, _open=_open_down)
    assert rc == 0
    assert "router: already healthy" in capsys.readouterr().out
    assert not run.ran_up()


def test_router_absent_gets_started(capsys):
    # a non-existent container is "not healthy" -> bring it up.
    run = FakeRun(state="absent", up_rc=0)
    rc = serves.ensure_router_healthy(_run=run, _open=_open_down, env_file="")
    assert rc == 0
    assert "router: started" in capsys.readouterr().out
    assert run.ran_up()


def test_router_exited_gets_started(capsys):
    # a stopped (exited) container -> not running -> bring it up.
    run = FakeRun(state="exited", up_rc=0)
    rc = serves.ensure_router_healthy(_run=run, _open=_open_down, env_file="")
    assert rc == 0
    assert "router: started" in capsys.readouterr().out
    assert run.ran_up()


def test_docker_error_is_reported_non_gating(capsys):
    # docker unreachable -> cannot probe or start; report and return non-zero,
    # but do not attempt a bring-up.
    run = FakeRun(state="error")
    rc = serves.ensure_router_healthy(_run=run, _open=_open_down)
    assert rc == 1
    assert "cannot determine health" in capsys.readouterr().out
    assert not run.ran_up()


def test_no_router_skips_everything(capsys):
    # --no-router: no probe, no bring-up, no docker calls at all.
    run = FakeRun(state="absent")
    rc = serves.ensure_router_healthy(no_router=True, _run=run, _open=_open_down)
    assert rc == 0
    assert "router: skipped" in capsys.readouterr().out
    assert run.calls == []  # nothing probed, nothing started


def test_dry_run_reports_but_does_not_start(capsys):
    # router down + --dry-run: report the action, do NOT bring it up.
    run = FakeRun(state="absent")
    rc = serves.ensure_router_healthy(dry_run=True, _run=run, _open=_open_down)
    out = capsys.readouterr().out
    assert rc == 0
    assert "would start" in out
    assert "router: started" not in out
    assert not run.ran_up()  # dry-run touched no container


def test_dry_run_when_healthy_still_reports_healthy(capsys):
    # the live-box verify case: router up + --dry-run -> "already healthy".
    run = FakeRun(state="running")
    rc = serves.ensure_router_healthy(dry_run=True, _run=run, _open=_open_ok)
    assert rc == 0
    assert "router: already healthy" in capsys.readouterr().out
    assert not run.ran_up()


def test_failed_bring_up_is_non_gating(capsys):
    # a router that fails to come up is reported but returns the non-zero rc so
    # the caller can decide; the serves-up caller proceeds regardless.
    run = FakeRun(state="absent", up_rc=1)
    rc = serves.ensure_router_healthy(_run=run, _open=_open_down, env_file="")
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAILED to start" in out
    assert run.ran_up()
