import subprocess

from anvil_serving import harness


def _completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def test_restart_hermes_default_restarts_current_service_definition():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _completed(argv)

    assert harness._restart_hermes_default(
        hermes_bin="hermes",
        timeout_seconds=17,
        _run=run,
    ) == 0
    assert [call[0][-1] for call in calls] == ["status", "restart"]
    assert all(call[1]["text"] is True for call in calls)
    assert all(call[1]["timeout"] == 17 for call in calls)


def test_restart_hermes_default_refreshes_stale_service_definition():
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[-1] == "status":
            return _completed(
                argv,
                returncode=1,
                stderr="Service definition is stale relative to the current Hermes install",
            )
        return _completed(argv)

    assert harness._restart_hermes_default(
        hermes_bin="~/bin/hermes",
        timeout_seconds=23,
        _run=run,
    ) == 0
    assert [argv[-1] for argv in calls] == ["status", "start", "restart"]


def test_restart_hermes_default_stops_when_stale_definition_refresh_fails():
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[-1] == "status":
            return _completed(argv, stderr="service definition IS STALE")
        return _completed(argv, returncode=4)

    assert harness._restart_hermes_default(
        hermes_bin="hermes",
        timeout_seconds=29,
        _run=run,
    ) == 4
    assert [argv[-1] for argv in calls] == ["status", "start"]


def test_restart_hermes_default_fails_closed_on_status_timeout():
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    assert harness._restart_hermes_default(
        hermes_bin="hermes",
        timeout_seconds=31,
        _run=run,
    ) == 1
