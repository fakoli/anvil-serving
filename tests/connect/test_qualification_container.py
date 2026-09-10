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


def test_prepare_copies_only_closed_context_and_writes_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, paths = _inputs(tmp_path)
    (paths["source"] / "private-sentinel.env").write_text("SENTINEL", encoding="utf-8")
    calls: list[tuple[list[str], dict[str, str]]] = []
    built = False
    observed: set[str] = set()

    def docker(args: list[str], *, env: dict[str, str], capture: bool) -> tuple[int, bytes]:
        nonlocal built
        calls.append((args, env))
        assert set(env) == {"PATH", "LANG", "LC_ALL", "DOCKER_CONFIG"}
        assert "SENTINEL" not in json.dumps(env)
        if args[:2] == ["image", "inspect"]:
            digest = args[2].removeprefix("anvil-connect-qualification:sha256-")
            return (0, _image(digest)) if built else (1, b"")
        assert args[0] == "build" and capture is False
        context = Path(args[-1])
        observed.update(path.name for path in context.iterdir())
        assert all("SENTINEL" not in path.read_text(encoding="utf-8") for path in context.iterdir())
        built = True
        return 0, b""

    monkeypatch.setattr(subject, "_docker", docker)
    result = subject.prepare(config)
    assert result["schema"] == "anvil-connect.qualification-container/v1"
    assert result["reused"] is False and result["image_id"] == "sha256:" + "c" * 64
    assert observed == {destination for _, destination in subject._CONTEXT}
    receipt = Path(result["receipt"])
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    assert json.loads(receipt.read_text()) == {key: result[key] for key in ("schema", "image_id", "input_digest", "base_image", "platform")}
    assert not any(path.name.startswith(("container-context-", "docker-config-")) for path in paths["artifacts"].iterdir())
    assert any(args[0] == "build" for args, _ in calls)


def test_prepare_reuses_only_verified_labeled_image_and_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, paths = _inputs(tmp_path)
    loaded = subject._context_inputs(subject._read_config(config))
    base, platform, _ = subject._pins(dict(loaded))
    digest = subject._input_digest(loaded)
    subject._receipt(paths["artifacts"], {"schema": subject._SCHEMA, "image_id": "sha256:" + "c" * 64, "input_digest": digest, "base_image": base, "platform": platform})
    calls: list[list[str]] = []

    def docker(args: list[str], *, env: dict[str, str], capture: bool) -> tuple[int, bytes]:
        calls.append(args)
        if args[:2] == ["image", "inspect"]:
            return 0, _image(args[2].removeprefix("anvil-connect-qualification:sha256-"))
        pytest.fail("cached image must not build")

    monkeypatch.setattr(subject, "_docker", docker)
    result = subject.prepare(config)
    assert result["reused"] is True
    assert calls and all(args[:2] == ["image", "inspect"] for args in calls)


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


def test_prepare_build_failure_is_safe_and_does_not_write_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, paths = _inputs(tmp_path)
    seen_env: list[dict[str, str]] = []

    def docker(args: list[str], *, env: dict[str, str], capture: bool) -> tuple[int, bytes]:
        seen_env.append(env)
        return (1, b"private-build-sentinel")

    monkeypatch.setattr(subject, "_docker", docker)
    with pytest.raises(QualificationError) as caught:
        subject.prepare(config)
    assert caught.value.code == "runner-failed"
    assert seen_env and all("HOME" not in env and all("SECRET" not in value for value in env.values()) for env in seen_env)
    assert not (paths["artifacts"] / "container-image.json").exists()

@pytest.mark.parametrize("relative,content", [
    ("connect/test/container/Dockerfile", "FROM mcr.example.test/playwright@sha256:" + "a" * 64 + "\nRUN apt-get install -y libnss3-tools=wrong\n"),
    ("connect/package.json", '{"devDependencies":{"@playwright/test":"9.9.9"}}'),
    ("connect/package-lock.json", '{"packages":{"node_modules/playwright":{"version":"9.9.9"}}}'),
])
def test_prepare_rejects_nss_and_playwright_pin_drift(tmp_path: Path, relative: str, content: str) -> None:
    config, paths = _inputs(tmp_path)
    (paths["source"] / relative).write_text(content, encoding="utf-8")
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


def test_docker_start_failure_is_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _docker_test_binary(tmp_path, monkeypatch)
    monkeypatch.setattr(subject.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("private")))
    with pytest.raises(QualificationError) as caught:
        subject._docker(["build"], env={"PATH": "/usr/bin:/bin"}, capture=False)
    assert caught.value.code == "runner-failed"


def test_docker_keyboard_interrupt_terminates_owned_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _docker_test_binary(tmp_path, monkeypatch)
    signals: list[int] = []

    class Process:
        pid = 42
        stdout = None
        def poll(self): return None
        def wait(self, *, timeout):
            if not signals:
                raise KeyboardInterrupt()
            return 0

    monkeypatch.setattr(subject.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(subject.os, "killpg", lambda _pid, signal: signals.append(signal))
    with pytest.raises(KeyboardInterrupt):
        subject._docker(["build"], env={"PATH": "/usr/bin:/bin"}, capture=False)
    assert signals == [subject.signal.SIGTERM, subject.signal.SIGKILL]


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


def test_receipt_uses_unique_temporary_and_preserves_other_writer_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "receipts"
    root.mkdir(mode=0o700)
    other = root / ".container-image.other.new"
    other.write_text("other", encoding="utf-8")
    value = {"schema": subject._SCHEMA, "image_id": "sha256:" + "c" * 64, "input_digest": "a" * 64, "base_image": "base", "platform": "linux/amd64"}
    receipt = subject._receipt(root, value)
    assert other.read_text(encoding="utf-8") == "other"
    assert json.loads(receipt.read_text(encoding="utf-8")) == value


@pytest.mark.parametrize("leader_exited", [False, True])
def test_docker_cleanup_kills_term_ignoring_descendant(leader_exited):
    # A dedicated child becomes the subreaper; pytest's process never does.
    import subprocess
    import sys
    code = r"""
import ctypes, os, signal, subprocess, sys, time
from anvil_serving.connect.qualification_container import _stop_docker
assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0
child_code = 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print("ready",flush=True); time.sleep(30)'
leader_code = 'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c",sys.argv[1]],stdout=subprocess.PIPE,text=True); p.stdout.readline(); print(p.pid,flush=True); '+('sys.exit(0)' if sys.argv[1]=='True' else 'time.sleep(30)')
leader = subprocess.Popen([sys.executable,'-c',leader_code,child_code],start_new_session=True,stdout=subprocess.PIPE,text=True)
child = int(leader.stdout.readline())
try:
    if sys.argv[1]=='True': leader.wait(timeout=3)
    _stop_docker(leader)
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        pid,status=os.waitpid(child,os.WNOHANG)
        if pid:
            assert os.WIFSIGNALED(status) and os.WTERMSIG(status)==signal.SIGKILL
            break
        time.sleep(.01)
    else: raise AssertionError('descendant survived cleanup')
finally:
    try: os.killpg(leader.pid,signal.SIGKILL)
    except ProcessLookupError: pass
    leader.wait(timeout=3)
    leader.stdout.close()
"""
    subprocess.run([sys.executable, "-c", code, str(leader_exited)], check=True, timeout=10)


def test_helper_cleanup_errors_are_safe(monkeypatch):
    class Process:
        pid = 42
        def wait(self, *, timeout):
            raise subject.subprocess.TimeoutExpired("private-sentinel", timeout)
    monkeypatch.setattr(subject.os, "killpg", lambda *args: None)
    with pytest.raises(QualificationError) as caught:
        subject._stop_docker(Process())
    assert caught.value.code == "runner-failed"
    assert "private-sentinel" not in str(caught.value)
