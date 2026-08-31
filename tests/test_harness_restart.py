import json
import plistlib
import subprocess

from anvil_serving import harness


def _write_launchd(path, *, label=harness.OPENCLAW_LAUNCHD_LABEL, openclaw=True):
    wrapper = "ai.openclaw.gateway-env-wrapper.sh" if openclaw else "other-wrapper.sh"
    path.write_bytes(plistlib.dumps({
        "Label": label,
        "ProgramArguments": ["/bin/sh", "/opt/service/" + wrapper, "/opt/bin/node"],
        "RunAtLoad": True,
    }))


def test_restart_uses_resolved_openclaw_executable():
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert harness.cmd_restart_openclaw(
        _which=lambda _name: "/opt/bin/openclaw",
        _run=run,
    ) == 0
    assert calls == [["/opt/bin/openclaw", "gateway", "restart"]]


def test_service_environment_refresh_uses_managed_openclaw_install():
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert harness.cmd_refresh_openclaw_service_environment(
        _which=lambda _name: "/opt/bin/openclaw",
        _run=run,
    ) == 0
    assert calls == [[
        "/opt/bin/openclaw",
        "gateway",
        "install",
        "--force",
        "--json",
    ]]


def test_service_environment_refresh_does_not_expose_failed_output(capsys):
    secret = "credential-that-must-not-be-printed"

    assert harness.cmd_refresh_openclaw_service_environment(
        _which=lambda _name: "/opt/bin/openclaw",
        _run=lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv, 17, secret, secret
        ),
    ) == 1
    captured = capsys.readouterr()
    assert "status 17" in captured.err
    assert secret not in captured.err
    assert secret not in captured.out


def test_restart_falls_back_to_verified_launchd_service(tmp_path, capsys):
    plist = tmp_path / "ai.openclaw.gateway.plist"
    _write_launchd(plist)
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert harness.cmd_restart_openclaw(
        timeout_seconds=2,
        _which=lambda _name: None,
        _platform="darwin",
        _launchd_path=str(plist),
        _launchd_health=lambda **_kwargs: 200,
        _run=run,
        _uid=lambda: 501,
    ) == 0
    assert calls == [[
        "launchctl", "kickstart", "-k", "gui/501/ai.openclaw.gateway",
    ]]
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["service_label"] == "ai.openclaw.gateway"
    assert result["health_http_status"] == 200
    assert len(result["definition_sha256"]) == 64


def test_restart_rejects_mismatched_launchd_definition(tmp_path, capsys):
    plist = tmp_path / "ai.openclaw.gateway.plist"
    _write_launchd(plist, openclaw=False)
    calls = []

    assert harness.cmd_restart_openclaw(
        _which=lambda _name: None,
        _platform="darwin",
        _launchd_path=str(plist),
        _run=lambda *args, **kwargs: calls.append((args, kwargs)),
        _uid=lambda: 501,
    ) == 1
    assert calls == []
    assert "does not identify an OpenClaw program" in capsys.readouterr().err


def test_restart_launchd_dry_run_is_non_mutating(tmp_path, capsys):
    plist = tmp_path / "ai.openclaw.gateway.plist"
    _write_launchd(plist)
    calls = []

    assert harness.cmd_restart_openclaw(
        dry_run=True,
        _which=lambda _name: None,
        _platform="darwin",
        _launchd_path=str(plist),
        _run=lambda *args, **kwargs: calls.append((args, kwargs)),
        _uid=lambda: 501,
    ) == 0
    assert calls == []
    assert capsys.readouterr().out.strip() == (
        "launchctl kickstart -k gui/501/ai.openclaw.gateway"
    )
