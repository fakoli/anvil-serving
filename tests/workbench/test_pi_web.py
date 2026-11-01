"""Managed Pi Web session UI lifecycle tests.

The tests inject every host boundary (subprocess, euid, platform, HTTP probe,
systemd root) so the reviewed behavior is proven without touching a real unit.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from anvil_serving.workbench_app import pi_web
from anvil_serving.workbench_app.pi_web import (
    PiWebConfig,
    PiWebError,
    PiWebInstaller,
    pi_web_config,
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


# --- unit rendering and planning -------------------------------------------


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
def test_concurrent_install_cannot_touch_active_promotion(installer_env, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl

    build, config, _ = installer_env
    installer, run, _ = build()
    root = Path(config.install_root)
    root.mkdir(mode=0o755)
    lock = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(PiWebError, match="another Pi Web installation"):
            installer.install(confirm=True)
        assert run.calls == []
    finally:
        os.close(lock)
    monkeypatch.setattr(fcntl, "flock", lambda *args: (_ for _ in ()).throw(OSError("lock unavailable")))
    with pytest.raises(PiWebError, match="cannot lock"):
        installer.install(confirm=True)
    assert run.calls == []


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


def _status_report(tmp_path: Path) -> dict[str, object]:
    import anvil_serving.workbench_app.pi_web as module

    config = pi_web_config(_config(tmp_path))
    run = FakeRun()
    original_service_state = module.service_state
    original_probe = module.probe

    def patched_state(**_: object) -> dict[str, object]:
        return original_service_state(run=run)

    def patched_probe(port: int, **_: object) -> dict[str, object]:
        return {"ready": False, "url": f"http://127.0.0.1:{port}/", "attempts": 1}

    module.service_state = patched_state  # type: ignore[assignment]
    module.probe = patched_probe  # type: ignore[assignment]
    try:
        return pi_web.status(config)
    finally:
        module.service_state = original_service_state  # type: ignore[assignment]
        module.probe = original_probe  # type: ignore[assignment]


def test_jiti_cache_is_still_detected_as_artifact_drift(tmp_path: Path) -> None:
    root = Path(str(pi_web_config(_config(tmp_path)).install_root))
    version_dir = root / "0.9.0"
    (version_dir / "bin").mkdir(parents=True)
    (version_dir / "bin/pi-web.js").write_text("// entry\n")
    recorded = pi_web._artifact_tree_sha256(version_dir)
    (root / "install-manifest.json").write_text(json.dumps({
        "version": "0.9.0",
        "bridge_install": {"artifact_sha256": recorded},
    }))
    cache = version_dir / "node_modules/.cache/jiti/image-budget-index.synthetic.cjs"
    cache.parent.mkdir(parents=True)
    cache.write_text("// runtime cache\n")
    assert _status_report(tmp_path)["drift"] == "drifted"


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


# --- bridge artifact promotion ----------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only managed unit")
def test_bridge_install_updates_origin_converges_and_preserves_old_artifact_on_build_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = tmp_path / "bridge-token.env"
    token.write_text("PI_WEB_WORKBENCH_BRIDGE_TOKEN=private\n", encoding="utf-8")
    token.chmod(0o600)
    source = tmp_path / "source"
    source.mkdir()
    config = pi_web_config(_config(
        tmp_path, bridge_source=str(source), bridge_parent_origin="https://workbench.example.test",
        bridge_token_env_file=str(token),
    ))
    run = _NpmResponder(config)
    calls: list[str] = []

    def staged(source_arg: Path, target: Path, **kwargs: object) -> dict[str, object]:
        calls.append(str(kwargs["parent_origin"]))
        entry = target / "bin" / "pi-web.js"
        entry.parent.mkdir(parents=True)
        entry.write_text("built=" + str(kwargs["parent_origin"]), encoding="utf-8")
        return pi_web._bridge_install_descriptor(
            parent_origin=str(kwargs["parent_origin"]), artifact_sha256=pi_web._artifact_tree_sha256(target),
            version=str(kwargs["version"]),
        )

    monkeypatch.setattr(pi_web, "_build_staged_bridge", staged)
    installer = PiWebInstaller(
        config, run=run, systemd_root=tmp_path / "systemd", geteuid=lambda: 0,
        probe_opener=lambda url: 200, node_path=_fake_node(tmp_path), platform="linux",
        chown=lambda target, uid, gid: None,
    )
    first = installer.install(confirm=True)
    version_dir = Path(str(config.install_root)) / config.version
    manifest_path = Path(str(config.install_root)) / "install-manifest.json"
    first_manifest = manifest_path.read_text(encoding="utf-8")
    assert first["package_installed"] is True and calls == ["https://workbench.example.test"]
    assert "built=https://workbench.example.test" in (version_dir / "bin" / "pi-web.js").read_text(encoding="utf-8")

    run.calls.clear()
    assert installer.install(confirm=True)["package_installed"] is False
    assert calls == ["https://workbench.example.test"]
    assert manifest_path.read_text(encoding="utf-8") == first_manifest

    changed = PiWebConfig(**{**config.__dict__, "bridge_parent_origin": "https://renewed.example.test"})
    changed_installer = PiWebInstaller(
        changed, run=run, systemd_root=tmp_path / "systemd", geteuid=lambda: 0,
        probe_opener=lambda url: 200, node_path=_fake_node(tmp_path), platform="linux",
        chown=lambda target, uid, gid: None,
    )
    remove_tree = pi_web.shutil.rmtree

    def blocked_cleanup(path, *args, **kwargs):
        if ".previous-" in Path(path).name:
            raise PermissionError("backup temporarily locked")
        return remove_tree(path, *args, **kwargs)

    with monkeypatch.context() as cleanup:
        cleanup.setattr(pi_web.shutil, "rmtree", blocked_cleanup)
        changed_result = changed_installer.install(confirm=True)
    assert changed_result["installed"] is True and changed_result["probe"]["ready"] is True
    assert changed_result["cleanup_pending"] is True
    backup, = version_dir.parent.glob("*.previous-*")
    assert backup.is_dir()
    assert changed_result["package_installed"] is True and changed_result["lifecycle"] == "restarted"
    assert calls[-1] == "https://renewed.example.test"
    assert "renewed" in (version_dir / "bin" / "pi-web.js").read_text(encoding="utf-8")
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["bridge_install"]["parent_origin"] == "https://renewed.example.test"

    with monkeypatch.context() as cleanup:
        cleanup.setattr(Path, "glob", lambda *args: (_ for _ in ()).throw(OSError("listing unavailable")))
        retry = changed_installer.install(confirm=True)
        assert retry["installed"] is True and retry["probe"]["ready"] is True
        assert retry["cleanup_pending"] is True
    run.calls.clear()
    retry = changed_installer.install(confirm=True)
    assert retry["cleanup_pending"] is False and not backup.exists()
    assert retry["package_installed"] is False and retry["lifecycle"] == "unchanged"
    assert calls == ["https://workbench.example.test", "https://renewed.example.test"]
    assert ["systemctl", "restart", pi_web.UNIT_NAME] not in run.calls

    old = (version_dir / "bin" / "pi-web.js").read_bytes()
    monkeypatch.setattr(pi_web, "_build_staged_bridge", lambda *args, **kwargs: (_ for _ in ()).throw(PiWebError("build failed")))
    failed = PiWebConfig(**{**changed.__dict__, "bridge_parent_origin": "https://failed.example.test"})
    with pytest.raises(PiWebError, match="build failed"):
        PiWebInstaller(
            failed, run=run, systemd_root=tmp_path / "systemd", geteuid=lambda: 0,
            probe_opener=lambda url: 200, node_path=_fake_node(tmp_path), platform="linux",
            chown=lambda target, uid, gid: None,
        ).install(confirm=True)
    assert (version_dir / "bin" / "pi-web.js").read_bytes() == old
    assert not list(Path(str(config.install_root)).glob("*.bridge-staging-*"))


def test_bridge_archive_uses_the_pinned_git_blob_not_a_mutated_checkout(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    tracked = source / "tracked.txt"
    tracked.write_text("reviewed\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.test"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "tracked.txt"],
        ["git", "commit", "-qm", "reviewed"],
    ):
        subprocess.run(command, cwd=source, check=True, capture_output=True, text=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=source, check=True, capture_output=True, text=True,
    ).stdout.strip()
    tracked.write_text("changed after review\n", encoding="utf-8")
    destination = tmp_path / "destination"
    pi_web._archive_tracked_bridge_source(
        source, destination, manifest={"source_commit": commit}, run=subprocess.run,
        chown=lambda target, uid, gid: None, uid=1000,
    )
    assert (destination / "tracked.txt").read_text(encoding="utf-8") == "reviewed\n"


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only managed install metadata")
def test_manifest_write_rejects_a_symlink_without_touching_its_target(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    root.chmod(0o755)
    victim = tmp_path / "preserve"
    victim.write_text("preserve", encoding="utf-8")
    (root / "install-manifest.json").symlink_to(victim)
    installer = PiWebInstaller(PiWebConfig(install_root=root, service_user="nobody"))
    with pytest.raises(PiWebError, match="regular file"):
        installer._record_manifest(root, node="/usr/bin/node", node_version=(22, 19, 0), bridge_install=None)
    assert victim.read_text(encoding="utf-8") == "preserve"


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only managed install metadata")
def test_manifest_write_is_atomic_when_promotion_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    root.chmod(0o755)
    manifest = root / "install-manifest.json"
    manifest.write_text("old manifest\n", encoding="utf-8")
    installer = PiWebInstaller(PiWebConfig(install_root=root, service_user="nobody"))
    monkeypatch.setattr(pi_web.os, "replace", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(PiWebError, match="write.*metadata"):
        installer._record_manifest(root, node="/usr/bin/node", node_version=(22, 19, 0), bridge_install=None)
    assert manifest.read_text(encoding="utf-8") == "old manifest\n"
    assert not list(root.glob(".install-manifest.json.*.tmp"))


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only managed unit")
def test_bridge_readiness_failure_restores_the_previous_artifact_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = tmp_path / "bridge-token.env"
    token.write_text("PI_WEB_WORKBENCH_BRIDGE_TOKEN=private\n", encoding="utf-8")
    token.chmod(0o600)
    source = tmp_path / "source"
    source.mkdir()
    config = pi_web_config(_config(
        tmp_path, bridge_source=str(source), bridge_parent_origin="https://workbench.example.test",
        bridge_token_env_file=str(token),
    ))
    run = _NpmResponder(config)

    def staged(source_arg: Path, target: Path, **kwargs: object) -> dict[str, object]:
        entry = target / "bin" / "pi-web.js"
        entry.parent.mkdir(parents=True)
        entry.write_text("built=" + str(kwargs["parent_origin"]), encoding="utf-8")
        return pi_web._bridge_install_descriptor(
            parent_origin=str(kwargs["parent_origin"]), artifact_sha256=pi_web._artifact_tree_sha256(target),
            version=str(kwargs["version"]),
        )

    monkeypatch.setattr(pi_web, "_build_staged_bridge", staged)
    first = PiWebInstaller(
        config, run=run, systemd_root=tmp_path / "systemd", geteuid=lambda: 0,
        probe_opener=lambda url: 200, node_path=_fake_node(tmp_path), platform="linux",
        chown=lambda target, uid, gid: None,
    )
    first.install(confirm=True)
    root = Path(str(config.install_root))
    version_dir = root / config.version
    old_entry = (version_dir / "bin" / "pi-web.js").read_bytes()
    old_manifest = (root / "install-manifest.json").read_bytes()
    changed = PiWebConfig(**{**config.__dict__, "bridge_parent_origin": "https://renewed.example.test"})
    monkeypatch.setattr(pi_web, "probe", lambda *args, **kwargs: {"ready": False, "url": "http://127.0.0.1:30141/", "attempts": 1})
    with pytest.raises(PiWebError, match="readiness probe"):
        PiWebInstaller(
            changed, run=run, systemd_root=tmp_path / "systemd", geteuid=lambda: 0,
            probe_opener=lambda url: 200, node_path=_fake_node(tmp_path), platform="linux",
            chown=lambda target, uid, gid: None,
        ).install(confirm=True)
    assert (version_dir / "bin" / "pi-web.js").read_bytes() == old_entry
    assert (root / "install-manifest.json").read_bytes() == old_manifest
    assert not list(root.glob("*.previous-*"))


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only managed unit")
@pytest.mark.parametrize("snapshot", ["state", "manifest", "unit"])
def test_bridge_snapshot_failure_prevents_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, snapshot: str,
) -> None:
    token = tmp_path / "bridge-token.env"
    token.write_text("PI_WEB_WORKBENCH_BRIDGE_TOKEN=private\n", encoding="utf-8")
    token.chmod(0o600)
    root = tmp_path / "pi-web-home"
    root.mkdir()
    root.chmod(0o755)
    version = root / "0.9.0"
    old_entry = version / "bin" / "pi-web.js"
    old_entry.parent.mkdir(parents=True)
    old_entry.write_text("old bridge", encoding="utf-8")
    old_manifest = b'{"old": true}\n'
    (root / "install-manifest.json").write_bytes(old_manifest)
    systemd = tmp_path / "systemd"
    systemd.mkdir()
    unit = systemd / pi_web.UNIT_NAME
    unit.write_text("old unit", encoding="utf-8")
    config = pi_web_config(_config(
        tmp_path, install_root=str(root), bridge_source=str(tmp_path / "source"),
        bridge_parent_origin="https://workbench.example.test", bridge_token_env_file=str(token),
    ))
    run = _NpmResponder(config)
    if snapshot == "state":
        monkeypatch.setattr(pi_web, "service_state", lambda **kwargs: (_ for _ in ()).throw(PiWebError("state unavailable")))
    elif snapshot == "manifest":
        monkeypatch.setattr(pi_web, "_read_regular_private_file", lambda *args, **kwargs: (_ for _ in ()).throw(PiWebError("manifest unavailable")))
    else:
        monkeypatch.setattr(PiWebInstaller, "_unit_snapshot", lambda self: (_ for _ in ()).throw(PiWebError("unit unavailable")))
    with pytest.raises(PiWebError, match="unavailable"):
        PiWebInstaller(
            config, run=run, systemd_root=systemd, geteuid=lambda: 0,
            probe_opener=lambda url: 200, node_path=_fake_node(tmp_path), platform="linux",
            chown=lambda target, uid, gid: None,
        ).install(confirm=True)
    assert old_entry.read_text(encoding="utf-8") == "old bridge"
    assert (root / "install-manifest.json").read_bytes() == old_manifest
    assert unit.read_text(encoding="utf-8") == "old unit"
    assert not list(root.glob("*.previous-*"))


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only installer uses patch(1)")
def test_bridge_source_verification_is_version_scoped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A source tree verifies only against the bridge version its pins describe."""
    source = tmp_path / "source"
    (source / "components").mkdir(parents=True)
    (source / "package.json").write_text('{"name": "@agegr/pi-web", "version": "0.9.2"}\n', encoding="utf-8")
    (source / "components" / "AppShell.tsx").write_text("export function AppShell() {}\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.test"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "reviewed"],
    ):
        subprocess.run(command, cwd=source, check=True, capture_output=True, text=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=source, check=True, capture_output=True, text=True,
    ).stdout.strip()
    real_manifest = pi_web.bridge_manifest
    pins = {
        "schema": "anvil-serving.pi-web-bridge/v1",
        "package": pi_web.PACKAGE,
        "version": "0.9.2",
        "source_commit": head,
        "package_json_sha256": __import__("hashlib").sha256((source / "package.json").read_bytes()).hexdigest(),
        "app_shell_sha256": __import__("hashlib").sha256((source / "components" / "AppShell.tsx").read_bytes()).hexdigest(),
        "patch_sha256": "0" * 64,
    }
    monkeypatch.setattr(
        pi_web, "bridge_manifest",
        lambda version=pi_web.DEFAULT_VERSION: pins if version == "0.9.2" else real_manifest(version),
    )
    verified = pi_web._verify_bridge_source(source, run=subprocess.run, version="0.9.2")
    assert verified["source_commit"] == head
    # The same tree must not verify against a different version's reviewed pins.
    with pytest.raises(pi_web.PiWebError, match="does not match the reviewed pin"):
        pi_web._verify_bridge_source(source, run=subprocess.run, version="0.9.0")
