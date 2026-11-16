from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys

import pytest

from anvil_serving.connect import qualification_container as subject
from anvil_serving.connect.qualification import QualificationError
from tests.connect.test_qualification import _config


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="requires Linux qualification container custody controls")


def _inputs(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    config, paths = _config(tmp_path)
    source = paths["source"]
    container = source / "connect/test/container"
    container.mkdir(parents=True)
    base = "mcr.example.test/playwright@sha256:" + "a" * 64
    pins = {
        "schema": "anvil-connect.qualification-container-pins/v1",
        "platform": "linux/amd64", "base_image": base,
        "playwright_version": "1.63.0", "nss_tools_version": "2:3.98-1ubuntu0.2",
        "go": {"version": "1.27.1", "url": "https://go.dev/example", "sha256": "b" * 64, "source": "https://go.dev"},
        "sources": ["https://example.test"],
    }
    (container / "pins.json").write_text(json.dumps(pins), encoding="utf-8")
    (container / "Dockerfile").write_text(
        f"FROM {base}\nRUN apt-get install -y libnss3-tools={pins['nss_tools_version']}\n",
        encoding="utf-8",
    )
    (container / "install_tools.py").write_text("# trusted build helper\n", encoding="utf-8")
    (source / "connect/go.mod").write_text("module example.test/connect\n\ngo 1.27\n", encoding="utf-8")
    (source / "connect/go.sum").write_text("example.test/module v1.0.0 h1:fixture\n", encoding="utf-8")
    (source / "connect/package.json").write_text('{"devDependencies":{"@playwright/test":"1.63.0"}}', encoding="utf-8")
    (source / "connect/package-lock.json").write_text('{"packages":{"node_modules/playwright":{"version":"1.63.0"}}}', encoding="utf-8")
    return config, paths


def _image(digest: str) -> bytes:
    return json.dumps([{
        "Id": "sha256:" + "c" * 64,
        "Config": {"Labels": {"io.fakoli.anvil-connect.qualification.input-digest": digest}},
    }]).encode("utf-8")


def test_prepare_rejects_pin_drift_and_symlinked_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, paths = _inputs(tmp_path)
    dockerfile = paths["source"] / "connect/test/container/Dockerfile"
    dockerfile.write_text("FROM mcr.example.test/other\n", encoding="utf-8")
    with pytest.raises(QualificationError) as caught:
        subject.prepare(config)
    assert caught.value.code == "source-invalid"
    config, paths = _inputs(tmp_path / "symlink")
    target = paths["source"] / "outside"
    target.write_text("module outside\n", encoding="utf-8")
    module = paths["source"] / "connect/go.mod"
    module.unlink()
    module.symlink_to(target)
    with pytest.raises(QualificationError) as caught:
        subject.prepare(config)
    assert caught.value.code == "source-invalid"


def test_missing_or_mismatched_receipt_forces_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, paths = _inputs(tmp_path)
    built = False
    builds = 0

    def docker(args: list[str], *, env: dict[str, str], capture: bool) -> tuple[int, bytes]:
        nonlocal built, builds
        if args[:2] == ["image", "inspect"]:
            digest = args[2].removeprefix("anvil-connect-qualification:sha256-")
            return (0, _image(digest))
        builds += 1
        built = True
        return 0, b""

    monkeypatch.setattr(subject, "_docker", docker)
    first = subject.prepare(config)
    assert first["reused"] is False and builds == 1
    receipt = Path(first["receipt"])
    saved = json.loads(receipt.read_text())
    saved["image_id"] = "sha256:" + "d" * 64
    receipt.write_text(json.dumps(saved), encoding="utf-8")
    receipt.chmod(0o600)
    second = subject.prepare(config)
    assert second["reused"] is False and builds == 2


def test_prepare_rejects_symlinked_context_ancestor(tmp_path: Path) -> None:
    config, paths = _inputs(tmp_path)
    source = paths["source"]
    original = source / "connect/test"
    relocated = source / "relocated-test"
    original.rename(relocated)
    original.symlink_to(relocated, target_is_directory=True)
    with pytest.raises(QualificationError) as caught:
        subject.prepare(config)
    assert caught.value.code == "source-invalid"


def _docker_test_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = tmp_path / "docker"
    binary.write_text("fixture", encoding="utf-8")
    binary.chmod(0o700)
    monkeypatch.setattr(subject, "_DOCKER", str(binary))
    original_stat = os.stat
    def socket_stat(path, *args, **kwargs):
        if os.fspath(path) == "/var/run/docker.sock":
            return type("Socket", (), {"st_mode": stat.S_IFSOCK})()
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(subject.os, "stat", socket_stat)


def test_docker_inspect_never_selects_with_negative_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _docker_test_binary(tmp_path, monkeypatch)
    selected: list[float] = []

    class Pipe:
        def fileno(self): return 0
        def close(self): pass

    class Process:
        stdout = Pipe()
        pid = 43
        def poll(self): return 0
        def wait(self, *, timeout): return 0

    class Selector:
        def register(self, *_args): pass
        def get_map(self): return {1: object()}
        def select(self, timeout):
            selected.append(timeout)
            return []
        def close(self): pass

    clock = iter((0.0, 16.0))
    monkeypatch.setattr(subject.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(subject.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(subject.time, "monotonic", lambda: next(clock))
    with pytest.raises(QualificationError) as caught:
        subject._docker(["image", "inspect", "tag"], env={"PATH": "/usr/bin:/bin"}, capture=True)
    assert caught.value.code == "runner-failed" and selected == []
