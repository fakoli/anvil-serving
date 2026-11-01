import subprocess

from anvil_serving import harness


def _completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def test_restart_hermes_default_fails_closed_on_status_timeout():
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    assert harness._restart_hermes_default(
        hermes_bin="hermes",
        timeout_seconds=31,
        _run=run,
    ) == 1
