"""Whole-call lifecycle checks for the offline systemd-guest qualifier.

These tests replace every external boundary while retaining the runner's real
stage tracking, error handling, artifact publication, and private cleanup.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import subprocess
import sys
from typing import Callable
import xml.etree.ElementTree as ET

import pytest

from anvil_serving.connect import qualification_vm_run as subject
from anvil_serving.connect._qualification_vm_process import ProcessResult
from anvil_serving.connect.qualification import QualificationConfig, QualificationError, _error


_ARTIFACTS = {"SHA256SUMS", "evidence.json", "junit.xml", "result.json"}
_PRIVATE = "private-lifecycle-sentinel"


def _config(tmp_path: Path) -> QualificationConfig:
    source = tmp_path / "source"
    artifacts = tmp_path / "artifacts"
    cache = tmp_path / "gomod"
    playwright = tmp_path / "playwright"
    tools = tmp_path / "tools"
    for directory in (source, artifacts, cache, playwright, tools):
        directory.mkdir(mode=0o700)
    values = {}
    for name in ("go", "node", "chromium", "certutil", "caddy", "authelia", "wstunnel"):
        path = tools / name
        path.write_bytes(name.encode("ascii"))
        path.chmod(0o700)
        values[name] = path
    return QualificationConfig(source, artifacts, playwright, cache, values, timeout_seconds=600)


def _guest_result() -> bytes:
    return json.dumps(
        {
            "schema": "anvil-connect.isolation-guest/v1",
            "ok": True,
            "cases": [{"name": name, "status": "passed"} for name in subject._CASES],
        },
        separators=(",", ":"),
    ).encode("ascii")


def _artifact_root(config: QualificationConfig) -> Path:
    roots = list(config.artifact_root.glob("isolation-*"))
    assert len(roots) == 1
    return roots[0]


def _closed(root: Path) -> tuple[dict, dict]:
    assert {entry.name for entry in root.iterdir()} == _ARTIFACTS
    raw = b"".join(entry.read_bytes() for entry in root.iterdir())
    assert _PRIVATE.encode() not in raw
    return (
        json.loads((root / "evidence.json").read_text(encoding="utf-8")),
        json.loads((root / "result.json").read_text(encoding="utf-8")),
    )


def _assert_junit_nonpass_parity(root: Path, status: str, infrastructure_error: str) -> None:
    suite = ET.parse(root / "junit.xml").getroot()
    cases = [case for case in suite.findall("testcase") if case.get("classname") == "connect.isolation"]
    assert len(cases) == len(subject._CASES)
    for case in cases:
        outcome = next(iter(case), None)
        assert outcome is not None
        assert outcome.get("type") == status
    infrastructure = [case for case in suite.findall("testcase") if case.get("classname") == "connect.isolation.infrastructure"]
    assert len(infrastructure) == 1
    outcome = next(iter(infrastructure[0]), None)
    assert outcome is not None and outcome.tag == "error" and outcome.get("type") == infrastructure_error
    assert suite.get("tests") == str(len(subject._CASES) + 1)
    assert suite.get("errors") == str((len(subject._CASES) if status == "unavailable" else 0) + 1)
    assert suite.get("skipped") == str(len(subject._CASES) if status == "not-run" else 0)


def _install_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    config: QualificationConfig,
    *,
    guest: Callable[..., ProcessResult] | None = None,
    payload_error: QualificationError | None = None,
) -> dict[str, object]:
    """Install inert exact-boundary doubles while preserving ``qualify`` itself."""
    cache = config.artifact_root / ".vm-images"
    cache.mkdir(mode=0o700)
    trace: dict[str, object] = {"iso": [], "payload_source": None, "qemu": None}
    tool_hashes = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in config.tools.items()
        if name in {"caddy", "authelia", "wstunnel"}
    }
    source = {"revision": "a" * 40, "dirty": False}
    pins = {"keyring_sha256": "b" * 64, "image_sha256": "c" * 64, "max_image_bytes": 8192}

    monkeypatch.setattr(subject, "_read_config", lambda _: config)
    monkeypatch.setattr(subject, "_source_metadata", lambda _: source)
    monkeypatch.setattr(subject, "_locks", lambda _: {"files": {}, "binaries": tool_hashes})
    monkeypatch.setattr(subject, "_capacity", lambda _: (0, 1))
    monkeypatch.setattr(subject, "_private_cache", lambda _: cache)
    monkeypatch.setattr(subject, "_pins", lambda _: pins)
    monkeypatch.setattr(subject, "_valid_cached_image", lambda *args, **kwargs: ("c" * 64, 4096, 8192))
    monkeypatch.setattr(
        subject,
        "_read_receipt",
        lambda _: {
            "image": subject._CACHE_NAME,
            "image_sha256": "c" * 64,
            "image_bytes": 4096,
            "virtual_size": 8192,
            "keyring_sha256": pins["keyring_sha256"],
        },
    )
    monkeypatch.setattr(
        subject,
        "_system_metadata",
        lambda: {"qemu": {"sha256": "d" * 64}, "firmware": {"sha256": "e" * 64}},
    )

    def stage(_source: Path, run_dir: Path, _metadata: dict, **_kwargs: object) -> Path:
        target = run_dir / "source"
        (target / "connect").mkdir(parents=True)
        return target

    def payload(
        _config: QualificationConfig,
        run_dir: Path,
        _source: dict,
        *,
        source_root: Path,
        cpus: tuple[int, ...],
        deadline: float,
    ) -> tuple[Path, dict[str, str], dict[str, object]]:
        del _config, _source, cpus, deadline
        trace["payload_source"] = source_root
        if payload_error is not None:
            raise payload_error
        target = run_dir / "payload"
        target.mkdir(mode=0o700)
        (target / "payload.json").write_text("{}", encoding="ascii")
        return target, {"payload.json": "e" * 64}, {"schema": subject._BUILD_SCHEMA, "platform": "linux/amd64"}

    def iso(_root: Path, output: Path, label: str, **_kwargs: object) -> int:
        trace["iso"].append(label)
        output.write_bytes(label.encode("ascii"))
        output.chmod(0o400)
        return os.open("/dev/null", os.O_RDONLY)

    def overlay(_base: int, output: Path, **_kwargs: object) -> int:
        output.write_bytes(b"overlay")
        output.chmod(0o600)
        return os.open("/dev/null", os.O_RDWR)

    def pinned(_path: Path, *, writable: bool = False) -> int:
        return os.open("/dev/null", os.O_RDWR if writable else os.O_RDONLY)

    def execute(argv: list[str], **kwargs: object) -> ProcessResult:
        trace["qemu"] = (argv, kwargs)
        if guest is not None:
            return guest(argv, kwargs)
        return ProcessResult(0, _guest_result(), 7, 1024, len(_guest_result()))

    monkeypatch.setattr(subject, "_stage_source", stage)
    monkeypatch.setattr(subject, "_payload", payload)
    monkeypatch.setattr(subject, "_iso", iso)
    monkeypatch.setattr(subject, "_overlay", overlay)
    monkeypatch.setattr(subject, "_open_pinned", pinned)
    monkeypatch.setattr(
        subject,
        "_verify_fd",
        lambda _fd, *, expected=None, **_kwargs: (expected or "f" * 64, 4096 if expected == "c" * 64 else 1),
    )
    monkeypatch.setattr(subject, "_image_fd_info", lambda *args, **kwargs: 8192)
    monkeypatch.setattr(subject, "execute", execute)
    return trace


def test_qualify_whole_lifecycle_publishes_exact_closed_artifacts(tmp_path, monkeypatch):
    config = _config(tmp_path)
    trace = _install_boundaries(monkeypatch, config)

    result = subject.qualify(tmp_path / "ignored.toml")

    root = Path(result["artifact_dir"])
    evidence, summary = _closed(root)
    assert result == {
        "schema": subject._SCHEMA,
        "ok": True,
        "artifact_dir": str(root),
        "counts": {"passed": 8, "failed": 0, "skipped": 0, "not_run": 0, "unavailable": 0},
    }
    assert evidence["counts"] == result["counts"] == summary["counts"]
    assert evidence["state"] == summary["state"] == "passed"
    assert trace["payload_source"] == root / "source"
    assert trace["iso"] == ["CIDATA", "ANVILTEST"]
    argv, _kwargs = trace["qemu"]
    assert argv[argv.index("-nic") + 1] == "none"
    assert argv.count("-drive") == 2
    for line in (root / "SHA256SUMS").read_text(encoding="ascii").splitlines():
        digest, name = line.split("  ", 1)
        assert digest == hashlib.sha256((root / name).read_bytes()).hexdigest()


def test_qualify_build_failure_is_closed_not_run_without_child_output(tmp_path, monkeypatch):
    config = _config(tmp_path)
    _install_boundaries(
        monkeypatch,
        config,
        payload_error=_error("staging-failed", _PRIVATE, execution_started=False, stage="staging"),
    )

    with pytest.raises(QualificationError) as caught:
        subject.qualify()

    assert caught.value.code == "staging-failed"
    evidence, summary = _closed(_artifact_root(config))
    assert evidence["state"] == "failed" and summary["ok"] is False
    assert evidence["error_code"] == summary["error_code"] == "staging-failed"
    assert evidence["counts"] == {"passed": 0, "failed": 0, "skipped": 0, "not_run": 8, "unavailable": 0}
    assert {case["status"] for case in evidence["cases"]} == {"not-run"}
    _assert_junit_nonpass_parity(_artifact_root(config), "not-run", "staging-failed")


@pytest.mark.parametrize("kind", ["timeout", "interrupt"])
def test_qualify_guest_without_final_report_is_closed_unavailable_and_cleans(tmp_path, monkeypatch, kind):
    config = _config(tmp_path)

    def guest(_argv: list[str], _kwargs: object) -> ProcessResult:
        if kind == "interrupt":
            raise KeyboardInterrupt
        raise _error("runner-timeout", _PRIVATE, execution_started=True, stage="execution")

    _install_boundaries(monkeypatch, config, guest=guest)

    if kind == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            subject.qualify()
        expected_code = "runner-interrupted"
    else:
        with pytest.raises(QualificationError) as caught:
            subject.qualify()
        expected_code = caught.value.code

    assert expected_code == ("runner-interrupted" if kind == "interrupt" else "runner-timeout")
    evidence, summary = _closed(_artifact_root(config))
    assert evidence["error_code"] == summary["error_code"] == expected_code
    assert evidence["counts"] == {"passed": 0, "failed": 0, "skipped": 0, "not_run": 0, "unavailable": 8}
    assert {case["status"] for case in evidence["cases"]} == {"unavailable"}
    _assert_junit_nonpass_parity(_artifact_root(config), "unavailable", expected_code)


def test_qualify_residual_private_file_becomes_closed_cleanup_failure(tmp_path, monkeypatch):
    config = _config(tmp_path)

    def guest(_argv: list[str], kwargs: object) -> ProcessResult:
        cwd = kwargs["cwd"]
        assert isinstance(cwd, Path)
        (cwd / "unexpected-private-residual").write_text(_PRIVATE, encoding="ascii")
        return ProcessResult(0, _guest_result(), 3, 0, len(_guest_result()))

    _install_boundaries(monkeypatch, config, guest=guest)

    with pytest.raises(QualificationError) as caught:
        subject.qualify()

    assert caught.value.code == "staging-failed"
    evidence, summary = _closed(_artifact_root(config))
    assert evidence["error_code"] == summary["error_code"] == "staging-failed"
    assert evidence["counts"]["unavailable"] == 0
    suite = ET.parse(_artifact_root(config) / "junit.xml").getroot()
    infrastructure = [case for case in suite.findall("testcase") if case.get("classname") == "connect.isolation.infrastructure"]
    assert suite.get("tests") == "9" and suite.get("errors") == "1"
    assert len(infrastructure) == 1 and next(iter(infrastructure[0])).get("type") == "staging-failed"


def test_qualify_removes_residual_link_without_following_its_target(tmp_path, monkeypatch):
    config = _config(tmp_path)
    outside = tmp_path / "outside-private-target"
    outside.write_text(_PRIVATE, encoding="ascii")

    def guest(_argv: list[str], kwargs: object) -> ProcessResult:
        cwd = kwargs["cwd"]
        assert isinstance(cwd, Path)
        (cwd / "unexpected-private-link").symlink_to(outside)
        return ProcessResult(0, _guest_result(), 3, 0, len(_guest_result()))

    _install_boundaries(monkeypatch, config, guest=guest)

    with pytest.raises(QualificationError) as caught:
        subject.qualify()

    assert caught.value.code == "staging-failed"
    evidence, summary = _closed(_artifact_root(config))
    assert evidence["error_code"] == summary["error_code"] == "staging-failed"
    assert outside.read_text(encoding="ascii") == _PRIVATE


def test_qualify_rejects_locked_component_before_qemu(tmp_path, monkeypatch):
    config = _config(tmp_path)
    trace = _install_boundaries(monkeypatch, config)
    monkeypatch.setattr(
        subject,
        "_locks",
        lambda _: {"files": {}, "binaries": {"caddy": "0" * 64, "authelia": "0" * 64, "wstunnel": "0" * 64}},
    )

    with pytest.raises(QualificationError) as caught:
        subject.qualify()

    assert caught.value.code == "tool-invalid"
    assert trace["qemu"] is None
    assert not list(config.artifact_root.glob("isolation-*"))


def test_stage_source_accepts_regular_git_archive_directory_entries(tmp_path):
    source = tmp_path / "source"
    guest = source / "connect/test/vm/guest.py"
    go_module = source / "connect/go.mod"
    package = source / "anvil_serving/connect/__init__.py"
    guest.parent.mkdir(parents=True)
    package.parent.mkdir(parents=True)
    guest.write_text("def build_manifest():\n    return {}\n", encoding="utf-8")
    go_module.write_text("module synthetic\n", encoding="ascii")
    package.write_text("", encoding="ascii")
    for args in (
        ["init", "--quiet"],
        ["config", "user.email", "fixture@example.test"],
        ["config", "user.name", "fixture"],
        ["add", "connect", "anvil_serving"],
        ["commit", "--quiet", "-m", "fixture"],
    ):
        subprocess.run(["/usr/bin/git", "-C", str(source), *args], check=True, stdout=subprocess.DEVNULL)
    revision = subprocess.check_output(["/usr/bin/git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()

    staged = subject._stage_source(source, tmp_path / "run", {"revision": revision})

    assert (staged / "connect/test/vm/guest.py").read_text(encoding="utf-8").startswith("def build_manifest")
    assert (staged / "connect/go.mod").read_text(encoding="ascii") == "module synthetic\n"


def test_private_payload_copies_the_managed_component_lock(tmp_path):
    source = tmp_path / "source"
    package = source / "anvil_serving/connect"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# fixture\n", encoding="ascii")
    (package / "components.lock.json").write_text('{"schema":"fixture"}\n', encoding="ascii")
    (package / "__init__.py").chmod(0o600)
    (package / "components.lock.json").chmod(0o600)
    payload = tmp_path / "payload"
    payload.mkdir()
    files: dict[str, str] = {}

    subject._copy_python(source, payload, files)

    copied = "python/anvil_serving/connect/components.lock.json"
    assert copied in files
    assert (payload / copied).read_text(encoding="ascii") == '{"schema":"fixture"}\n'


def test_static_declaration_render_does_not_execute_staged_guest_code(tmp_path):
    config = _config(tmp_path)
    source = config.source_root
    guest = source / "connect/test/vm/guest.py"
    declaration = source / "connect/test/vm/deployment.json"
    config_module = source / "anvil_serving/connect/config.py"
    render_module = source / "anvil_serving/connect/render.py"
    for path, content in (
        (guest, "while True:\n    pass\n"),
        (declaration, '{"schema":"fixture"}\n'),
        (config_module, "def validate_manifest(value):\n    return value\n"),
        (render_module, "def render_for_inspection(value):\n    return {'files': {}}\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="ascii")
        path.chmod(0o600)
    (source / "anvil_serving/__init__.py").write_text("# fixture\n", encoding="ascii")
    (source / "anvil_serving/connect/__init__.py").write_text("# fixture\n", encoding="ascii")
    for path in (source / "anvil_serving/__init__.py", source / "anvil_serving/connect/__init__.py"):
        path.chmod(0o600)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    manifest, rendered = subject._render_declaration(
        source,
        run_dir,
        cpus=tuple(sorted(os.sched_getaffinity(0))[:2]),
        deadline=time.monotonic() + 2,
    )

    assert manifest == {"schema": "fixture"}
    assert rendered == {}


@pytest.mark.skipif(not subject._XORRISO.is_file() or not subject._QEMU_IMG.is_file(), reason="pinned VM image tools are unavailable")
def test_retained_iso_and_qcow_descriptors_are_verified_without_booting(tmp_path):
    cpus = tuple(sorted(os.sched_getaffinity(0))[:2])
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    (source / "meta-data").write_text("instance-id: fixture\n", encoding="ascii")
    deadline = time.monotonic() + 10
    iso = tmp_path / "fixture.iso"

    iso_fd = subject._iso(source, iso, "FIXTURE", cpus=cpus, deadline=deadline)
    try:
        actual, size = subject._verify_fd(
            iso_fd,
            owner=os.geteuid(),
            mode=0o400,
            maximum=subject._MAX_PAYLOAD * 2,
            deadline=deadline,
        )
        assert actual == hashlib.sha256(iso.read_bytes()).hexdigest()
        assert size == iso.stat().st_size
    finally:
        os.close(iso_fd)

    image = tmp_path / "fixture.qcow2"
    created = subject.execute(
        [str(subject._QEMU_IMG), "create", "-f", "qcow2", str(image), "16M"],
        home=tmp_path,
        timeout=5,
        cpus=cpus,
        maximum_output=64 * 1024,
    )
    assert created.returncode == 0
    image_fd = subject._open_pinned(image)
    try:
        assert subject._image_fd_info(image_fd, home=tmp_path, cpus=cpus, deadline=deadline) == 16 * 1024 * 1024
        overlay = tmp_path / "fixture-overlay.qcow2"
        overlay_fd = subject._overlay(image_fd, overlay, cpus=cpus, deadline=deadline)
        try:
            assert subject._image_fd_info(
                overlay_fd,
                home=tmp_path,
                cpus=cpus,
                deadline=deadline,
                base_fd=image_fd,
            ) == 16 * 1024 * 1024
        finally:
            os.close(overlay_fd)
    finally:
        os.close(image_fd)


def test_open_pinned_fifo_returns_promptly_without_reading_it(tmp_path):
    fifo = tmp_path / "input.fifo"
    os.mkfifo(fifo, 0o600)
    program = """
from pathlib import Path
from anvil_serving.connect.qualification import QualificationError
from anvil_serving.connect.qualification_vm_run import _open_pinned
try:
    fd = _open_pinned(Path(__import__('sys').argv[1]))
except QualificationError:
    raise SystemExit(0)
else:
    __import__('os').close(fd)
    raise SystemExit(1)
"""

    result = subprocess.run(
        [sys.executable, "-c", program, str(fifo)],
        cwd=Path(__file__).parents[2],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path(__file__).parents[2])},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=2,
        check=False,
    )

    assert result.returncode == 0


def test_verify_retained_descriptor_hashes_original_after_path_rename_and_replacement(tmp_path):
    original = tmp_path / "pinned"
    retained = tmp_path / "retained"
    replacement = tmp_path / "replacement"
    original.write_bytes(b"original")
    replacement.write_bytes(b"replacement")
    original.chmod(0o400)
    replacement.chmod(0o400)
    descriptor = subject._open_pinned(original)
    try:
        os.rename(original, retained)
        os.rename(replacement, original)
        digest, size = subject._verify_fd(
            descriptor,
            owner=os.geteuid(),
            mode=0o400,
            maximum=1024,
            deadline=time.monotonic() + 2,
            expected=hashlib.sha256(b"original").hexdigest(),
        )
    finally:
        os.close(descriptor)

    assert (digest, size) == (hashlib.sha256(b"original").hexdigest(), len(b"original"))
    assert original.read_bytes() == b"replacement"


def test_verify_rejects_unlinked_retained_descriptor_after_atomic_replace(tmp_path):
    original = tmp_path / "pinned"
    replacement = tmp_path / "replacement"
    original.write_bytes(b"original")
    replacement.write_bytes(b"replacement")
    original.chmod(0o400)
    replacement.chmod(0o400)
    descriptor = subject._open_pinned(original)
    try:
        os.replace(replacement, original)
        with pytest.raises(QualificationError):
            subject._verify_fd(
                descriptor,
                owner=os.geteuid(),
                mode=0o400,
                maximum=1024,
                deadline=time.monotonic() + 2,
                expected=hashlib.sha256(b"original").hexdigest(),
            )
    finally:
        os.close(descriptor)

    assert original.read_bytes() == b"replacement"


@pytest.mark.parametrize("kind", ["iso", "overlay"])
def test_failed_output_build_closes_its_retained_descriptor(tmp_path, monkeypatch, kind):
    closed: list[int] = []
    close = os.close

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        close(descriptor)

    monkeypatch.setattr(subject.os, "close", record_close)
    monkeypatch.setattr(
        subject,
        "execute",
        lambda *args, **kwargs: ProcessResult(1, b"", 1, 0, 0),
    )
    output = tmp_path / ("fixture.iso" if kind == "iso" else "fixture.qcow2")
    deadline = time.monotonic() + 2

    if kind == "iso":
        source = tmp_path / "source"
        source.mkdir()
        with pytest.raises(OSError):
            subject._iso(source, output, "FIXTURE", cpus=(0,), deadline=deadline)
    else:
        base = tmp_path / "base.qcow2"
        base.write_bytes(b"base")
        base.chmod(0o400)
        base_fd = subject._open_pinned(base)
        try:
            with pytest.raises(QualificationError):
                subject._overlay(base_fd, output, cpus=(0,), deadline=deadline)
        finally:
            close(base_fd)

    assert closed
