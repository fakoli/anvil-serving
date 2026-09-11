"""Managed Pi Web session UI lifecycle tests.

The tests inject every host boundary (subprocess, euid, platform, HTTP probe,
systemd root) so the reviewed behavior is proven without touching a real unit.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from anvil_serving.workbench_app import pi_web
from anvil_serving.workbench_app.pi_web import (
    PACKAGE,
    PiWebConfig,
    PiWebError,
    PiWebInstaller,
    pi_web_config,
    unit_content,
)


def _fake_node(tmp_path: Path, version: str = "v24.20.0") -> str:
    runtime = tmp_path / "runtime"
    node = runtime / "bin" / "node-fake"
    node.parent.mkdir(parents=True, exist_ok=True)
    node.write_text(f"#!/bin/sh\necho {version}\n", encoding="utf-8")
    node.chmod(0o755)
    npm_cli = runtime / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    npm_cli.parent.mkdir(parents=True, exist_ok=True)
    npm_cli.write_text("// fake npm cli\n", encoding="utf-8")
    return str(node)


def _config(tmp_path: Path, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "version": "0.9.0",
        "port": 30141,
        "hostname": "127.0.0.1",
        "allowed_hosts": ["pi.example.test"],
        "service_user": "nobody",
        "install_root": str(tmp_path / "pi-web-home"),
    }
    value.update(overrides)
    return value


class FakeRun:
    """Record argv and answer the commands the installer issues."""

    def __init__(self, node_version: str = "v24.20.0") -> None:
        self.calls: list[list[str]] = []
        self.node_version = node_version
        self.active_state = "inactive"

    def __call__(self, argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        if argv and argv[0].endswith("node-fake") and argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout=self.node_version, stderr="")
        if argv[:3] == ["systemctl", "show", pi_web.UNIT_NAME]:
            return subprocess.CompletedProcess(
                argv, 0,
                stdout=f"ActiveState={self.active_state}\nSubState=dead\nMainPID=0\nUnitFileState=enabled\nExecMainStartTimestamp=\n",
                stderr="",
            )
        if argv[:2] == ["runuser", "-u"]:
            assert argv[4:6] == ["npm", "install"]
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:2] == ["journalctl", "-u"]:
            return subprocess.CompletedProcess(argv, 0, stdout="-- Logs begin here --\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


@pytest.fixture()
def installer_env(tmp_path: Path):
    """Build one installer wired to fake host boundaries."""
    runtime = tmp_path / "runtime"
    node = runtime / "bin" / "node-fake"
    node.parent.mkdir(parents=True, exist_ok=True)
    node.write_text("#!/bin/sh\necho v24.20.0\n", encoding="utf-8")
    node.chmod(0o755)
    npm_cli = runtime / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    npm_cli.parent.mkdir(parents=True, exist_ok=True)
    npm_cli.write_text("// fake npm cli\n", encoding="utf-8")
    config = pi_web_config(_config(tmp_path, node_path=str(node)))

    def build(**overrides: object) -> tuple[PiWebInstaller, FakeRun, PiWebConfig]:
        run = FakeRun()
        defaults: dict[str, object] = {
            "run": run,
            "systemd_root": tmp_path / "systemd",
            "geteuid": (lambda: 0),
            "probe_opener": (lambda url: 200),
            "node_path": str(node),
            "platform": "linux",
            "chown": (lambda target, uid, gid: None),
        }
        defaults.update(overrides)
        installer = PiWebInstaller(config, **defaults)  # type: ignore[arg-type]
        return installer, run, config

    return build, config, tmp_path


# --- configuration parsing -------------------------------------------------


def test_config_defaults_are_usable_for_status_without_a_file() -> None:
    parsed = pi_web_config({})
    assert parsed.version == pi_web.DEFAULT_VERSION
    assert parsed.port == pi_web.DEFAULT_PORT
    assert parsed.hostname == "127.0.0.1"
    assert parsed.idle_timeout_ms == pi_web.DEFAULT_IDLE_TIMEOUT_MS


@pytest.mark.parametrize(
    "mutation",
    [
        {"version": "latest"},
        {"version": ">=0.9.0"},
        {"version": "0.9"},
        {"hostname": "localhost"},
        {"hostname": "0.0.0.0"},
        {"hostname": "example.test:8080"},
        {"hostname": "*.example.test"},
        {"allowed_hosts": ["*.wild.example.test"]},
        {"allowed_hosts": ["not a host"]},
        {"allowed_hosts": [f"h{i}.example.test" for i in range(9)]},
        {"port": 80},
        {"port": 70000},
        {"idle_timeout_ms": -1},
        {"idle_timeout_ms": 2147483648},
        {"service_user": "Bad Name"},
        {"unsupported": True},
    ],
)
def test_config_rejects_unsafe_or_undeclared_fields(tmp_path: Path, mutation: dict[str, object]) -> None:
    with pytest.raises(PiWebError):
        pi_web_config(_config(tmp_path, **mutation))


def test_config_requires_an_existing_declared_node_path(tmp_path: Path) -> None:
    with pytest.raises(PiWebError, match="node_path"):
        pi_web_config(_config(tmp_path, node_path=str(tmp_path / "missing" / "node")))


def test_config_accepts_ip_literal_and_exact_hosts(tmp_path: Path) -> None:
    parsed = pi_web_config(_config(tmp_path, hostname="127.0.0.1", allowed_hosts=["127.0.0.1", "pi.example.test"]))
    assert parsed.allowed_hosts == ("127.0.0.1", "pi.example.test")


def test_load_config_uses_the_conventional_operator_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    config_path = tmp_path / "workbench" / "pi-web.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"version": "1.2.3"}), encoding="utf-8")
    loaded = pi_web.load_config(required=True)
    assert loaded.version == "1.2.3"
    assert loaded.port == pi_web.DEFAULT_PORT


def test_load_config_missing_required_file_is_actionable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    with pytest.raises(PiWebError, match=r"workbench.pi-web\\.json"):
        pi_web.load_config(required=True)
    assert pi_web.load_config(required=False).version == pi_web.DEFAULT_VERSION


# --- unit rendering and planning -------------------------------------------


def test_unit_content_renders_the_reviewed_service(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("systemd unit rendering is Linux-only")
    config = pi_web_config(_config(tmp_path, password_env_file="/etc/anvil-pi-web/env"))
    content = unit_content(
        config,
        node_path="/usr/bin/node-fake",
        entry_script=tmp_path / "0.9.0" / pi_web.PACKAGE_BIN,
        user="operator",
        user_home=Path("/home/operator"),
        node_bin_dir=Path("/usr/bin"),
    )
    assert "User=operator" in content
    assert "ExecStart=/usr/bin/node-fake" in content and "--hostname 127.0.0.1 --no-open" in content
    assert "Environment=PI_WEB_ALLOWED_HOSTS=pi.example.test" in content
    assert "Environment=PI_WEB_IDLE_TIMEOUT_MS=600000" in content
    assert "EnvironmentFile=/etc/anvil-pi-web/env" in content
    assert "NoNewPrivileges=yes" in content and "ProtectSystem=full" in content
    assert "WantedBy=multi-user.target" in content
    assert "0.0.0.0" not in content


def test_unit_content_refuses_a_non_loopback_bind(tmp_path: Path) -> None:
    config = pi_web_config(_config(tmp_path))
    widened = PiWebConfig(**{**config.__dict__, "hostname": "0.0.0.0"})
    with pytest.raises(PiWebError, match="loopback"):
        unit_content(
            widened,
            node_path="/usr/bin/node-fake",
            entry_script=tmp_path / "entry.js",
            user="operator",
            user_home=Path("/home/operator"),
            node_bin_dir=Path("/usr/bin"),
        )


def test_plan_lists_exact_root_commands(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("the managed install runs Linux host commands")
    config = pi_web_config(_config(tmp_path))
    planned = pi_web.plan(config, node_path=_fake_node(tmp_path))
    assert planned["package"] == f"{PACKAGE}@0.9.0"
    assert planned["commands"][0][:4] == ["runuser", "-u", "nobody", "--"]  # type: ignore[index]
    assert planned["commands"][0][4] == "env"  # type: ignore[index]
    assert planned["commands"][0][-1] == f"{PACKAGE}@0.9.0"  # type: ignore[index]
    assert ["systemctl", "daemon-reload"] in planned["commands"]  # type: ignore[operator]
    assert str(planned["unit_path"]).endswith(pi_web.UNIT_NAME)


def test_plan_rejects_a_node_below_the_engine_floor(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("the managed install runs Linux host commands")
    config = pi_web_config(_config(tmp_path))
    with pytest.raises(PiWebError, match="22.19.0"):
        pi_web.plan(config, node_path=_fake_node(tmp_path, version="v20.11.0"))


def test_plan_requires_a_service_user(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("the managed install runs Linux host commands")
    config = pi_web_config(_config(tmp_path, service_user=None))
    with pytest.raises(PiWebError, match="service_user"):
        pi_web.plan(config, node_path=_fake_node(tmp_path))


# --- installer gates -------------------------------------------------------


def test_install_without_confirm_is_a_read_only_plan(installer_env) -> None:
    if sys.platform == "win32":
        pytest.skip("the plan probes the pinned Node.js runtime")
    build, _, _ = installer_env
    installer, run, _ = build()
    result = installer.install(confirm=False)
    assert result["dry_run"] is True
    assert run.calls == []


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only managed unit")
def test_install_requires_root(installer_env) -> None:
    build, _, _ = installer_env
    installer, run, _ = build(geteuid=(lambda: 1000))
    with pytest.raises(PiWebError, match="requires root"):
        installer.install(confirm=True)
    assert run.calls == []


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only managed unit")
def test_install_requires_linux(installer_env) -> None:
    build, _, _ = installer_env
    installer, run, _ = build(platform="win32")
    with pytest.raises(PiWebError, match="Linux"):
        installer.install(confirm=True)
    assert run.calls == []


def test_install_rejects_missing_password_env_file(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("the managed install runs Linux host commands")
    missing = tmp_path / "missing-env"
    config = pi_web_config(_config(tmp_path, password_env_file=str(missing)))
    installer = PiWebInstaller(
        config, run=FakeRun(), systemd_root=tmp_path / "systemd", geteuid=(lambda: 0),
        probe_opener=(lambda url: 200), node_path=_fake_node(tmp_path), platform="linux",
    )
    with pytest.raises(PiWebError, match="password_env_file"):
        installer.install(confirm=True)


def test_install_rejects_group_or_world_readable_password_env_file(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("the managed install runs Linux host commands")
    loose = tmp_path / "loose-env"
    loose.write_text("PI_WEB_PASSWORD=x\n", encoding="utf-8")
    loose.chmod(0o644)
    config = pi_web_config(_config(tmp_path, password_env_file=str(loose)))
    installer = PiWebInstaller(
        config, run=FakeRun(), systemd_root=tmp_path / "systemd", geteuid=(lambda: 0),
        probe_opener=(lambda url: 200), node_path=_fake_node(tmp_path), platform="linux",
    )
    with pytest.raises(PiWebError, match="readable"):
        installer.install(confirm=True)


# --- install converge behavior --------------------------------------------


class _NpmResponder:
    """Fake run that creates the pinned entry, tracks unit state, and records."""

    def __init__(self, config: PiWebConfig) -> None:
        self.config = config
        self.calls: list[list[str]] = []
        self.active_state = "inactive"

    def __call__(self, argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        if argv[:2] == ["runuser", "-u"]:
            prefix = Path(argv[argv.index("--prefix") + 1])
            entry = prefix / pi_web.PACKAGE_BIN
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text("#!/usr/bin/env node\n", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:2] == ["systemctl", "start"] or argv[:2] == ["systemctl", "restart"]:
            self.active_state = "active"
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:3] == ["systemctl", "show", pi_web.UNIT_NAME]:
            return subprocess.CompletedProcess(
                argv, 0,
                stdout=f"ActiveState={self.active_state}\nSubState=running\nMainPID=4242\nUnitFileState=enabled\nExecMainStartTimestamp=now\n",
                stderr="",
            )
        if argv and argv[0].endswith("node-fake") and argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="v24.20.0", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only managed unit")
def test_install_runs_pinned_commands_and_probes_readiness(installer_env, tmp_path: Path) -> None:
    build, config, root = installer_env
    run = _NpmResponder(config)
    installer = PiWebInstaller(
        config, run=run, systemd_root=root / "systemd",
        geteuid=(lambda: 0), probe_opener=(lambda url: 200),
        node_path=str(root / "runtime" / "bin" / "node-fake"), platform="linux",
        chown=(lambda target, uid, gid: None),
    )
    result = installer.install(confirm=True)
    assert result["installed"] is True and result["package_installed"] is True
    assert result["lifecycle"] == "started" and result["probe"]["ready"] is True
    verbs = [argv[:2] for argv in run.calls]
    assert ["runuser", "-u"] in verbs
    assert verbs.count(["systemctl", "daemon-reload"]) == 1
    assert verbs.count(["systemctl", "enable"]) == 1
    assert verbs.count(["systemctl", "start"]) == 1
    assert verbs.count(["systemctl", "restart"]) == 0
    manifest = json.loads((Path(str(config.install_root)) / "install-manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == config.version
    assert manifest["schema"] == "anvil-serving.pi-web-install/v1"
    assert (Path(str(config.install_root)) / config.version / pi_web.PACKAGE_BIN).is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only managed unit")
def test_repeated_install_converges_without_restart_or_npm(installer_env, tmp_path: Path) -> None:
    build, config, root = installer_env
    run = _NpmResponder(config)
    installer = PiWebInstaller(
        config, run=run, systemd_root=root / "systemd",
        geteuid=(lambda: 0), probe_opener=(lambda url: 200),
        node_path=str(root / "runtime" / "bin" / "node-fake"), platform="linux",
        chown=(lambda target, uid, gid: None),
    )
    installer.install(confirm=True)
    run.calls.clear()
    second = installer.install(confirm=True)
    assert second["package_installed"] is False
    assert second["unit_changed"] is False
    assert second["lifecycle"] == "unchanged"  # already active; left running
    verbs = [argv[:2] for argv in run.calls]
    assert ["runuser", "-u"] not in verbs
    assert ["systemctl", "restart"] not in verbs
    assert ["systemctl", "daemon-reload"] not in verbs


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only managed unit")
def test_install_with_changed_unit_reloads_and_restarts(installer_env, tmp_path: Path) -> None:
    build, config, root = installer_env
    run = _NpmResponder(config)
    installer = PiWebInstaller(
        config, run=run, systemd_root=root / "systemd",
        geteuid=(lambda: 0), probe_opener=(lambda url: 200),
        node_path=str(root / "runtime" / "bin" / "node-fake"), platform="linux",
        chown=(lambda target, uid, gid: None),
    )
    installer.install(confirm=True)
    run.calls.clear()
    changed = pi_web_config({
        **config.__dict__,
        "version": "1.0.0",
        "install_root": str(config.install_root),
    })
    changed_installer = PiWebInstaller(
        changed, run=run, systemd_root=tmp_path / "systemd",
        geteuid=(lambda: 0), probe_opener=(lambda url: 200),
        node_path=str(root / "runtime" / "bin" / "node-fake"), platform="linux",
        chown=(lambda target, uid, gid: None),
    )
    result = changed_installer.install(confirm=True)
    assert result["unit_changed"] is True and result["lifecycle"] == "restarted"
    verbs = [argv[:2] for argv in run.calls]
    assert ["systemctl", "daemon-reload"] in verbs
    assert ["systemctl", "restart"] in verbs
    assert (Path(str(changed.install_root)) / "1.0.0" / pi_web.PACKAGE_BIN).is_file()


# --- read surfaces ---------------------------------------------------------


def test_service_state_parses_systemd_properties(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("systemd is Linux-only")
    run = FakeRun()
    state = pi_web.service_state(run=run)
    assert state["unit"] == pi_web.UNIT_NAME
    assert state["active"] == "inactive"
    assert state["enabled"] == "enabled"


def test_status_reports_probe_and_installed_pin(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("systemd is Linux-only")
    config = pi_web_config(_config(tmp_path))
    root = Path(str(config.install_root))
    root.mkdir(parents=True)
    (root / "install-manifest.json").write_text(json.dumps({"version": "0.9.0"}), encoding="utf-8")
    run = FakeRun()
    import anvil_serving.workbench_app.pi_web as module

    original_service_state = module.service_state
    original_probe = module.probe

    def patched_state(**_: object) -> dict[str, object]:
        return original_service_state(run=run)

    def patched_probe(port: int, **_: object) -> dict[str, object]:
        return {"ready": False, "url": f"http://127.0.0.1:{port}/", "attempts": 1}

    module.service_state = patched_state  # type: ignore[assignment]
    module.probe = patched_probe  # type: ignore[assignment]
    try:
        report = pi_web.status(config)
    finally:
        module.service_state = original_service_state  # type: ignore[assignment]
        module.probe = original_probe  # type: ignore[assignment]
    assert report["configured_version"] == "0.9.0"
    assert report["installed_version"] == "0.9.0"
    assert report["probe"]["attempts"] == 1
    assert report["probe"]["ready"] is False


def test_logs_returns_bounded_journal_text(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("journalctl is Linux-only")
    config = pi_web_config(_config(tmp_path))
    run = FakeRun()
    import anvil_serving.workbench_app.pi_web as module

    original = module._bounded_run

    def fake_bounded_run(run_callable, argv: list[str]) -> subprocess.CompletedProcess[str]:
        return original(run, argv)  # type: ignore[arg-type]

    module._bounded_run = fake_bounded_run  # type: ignore[assignment]
    try:
        output = pi_web.logs(config, tail=25)
    finally:
        module._bounded_run = original  # type: ignore[assignment]
    assert "Logs begin" in output
    assert run.calls[0][0] == "journalctl"
    assert "-n" in run.calls[0] and "25" in run.calls[0]


# --- probe behavior --------------------------------------------------------


class _FlakyOpener:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def __call__(self, url: str) -> object:
        self.calls += 1
        if self.calls <= self.failures:
            raise OSError("connection refused")
        return 200


def test_probe_retries_then_reports_ready() -> None:
    opener = _FlakyOpener(failures=2)
    result = pi_web.probe(30141, opener=opener, attempts=4, retry_seconds=0.0)
    assert result["ready"] is True and result["attempts"] == 3


def test_probe_exhaustion_reports_the_last_error() -> None:
    opener = _FlakyOpener(failures=99)
    result = pi_web.probe(30141, opener=opener, attempts=3, retry_seconds=0.0)
    assert result["ready"] is False and result["attempts"] == 3
    assert "connection refused" in str(result["error"])  # type: ignore[operator]


def test_probe_url_is_loopback_only() -> None:
    seen: list[str] = []

    def opener(url: str) -> object:
        seen.append(url)
        return 200

    pi_web.probe(30444, opener=opener, attempts=1)
    assert seen == ["http://127.0.0.1:30444/"]