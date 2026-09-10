from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import signal
import sys
import time
import _thread
import threading

import pytest

from anvil_serving.connect import qualification as subject
from anvil_serving.connect import _qualification_supervisor as supervisor


def _tool(path: Path, name: str) -> Path:
    target = path / name
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target.chmod(0o700)
    return target


def _config(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    source = tmp_path / "source"
    (source / "connect/lab").mkdir(parents=True)
    (source / "connect/test").mkdir(parents=True)
    (source / "connect/lab/edge-tools.json").write_text(json.dumps({"schema": "anvil-connect.edge-tools/v1"}))
    (source / "connect/transport.lock.json").write_text(json.dumps({"schema": "anvil-connect.transport-lock/v1"}))
    (source / "connect/test/browser_edge.spec.mjs").write_text("// fixture\n")
    playwright = tmp_path / "playwright"
    runner = playwright / "node_modules/playwright/cli.js"
    runner.parent.mkdir(parents=True)
    runner.write_text("// runner\n")
    package = playwright / "node_modules/playwright/package.json"
    package.write_text('{"version":"1.63.0"}')
    runner.chmod(0o600)
    cache = tmp_path / "go-cache"
    cache.mkdir(mode=0o700)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(mode=0o700)
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    tools = {name: _tool(tool_dir, name) for name in sorted(subject._TOOL_NAMES)}
    config = tmp_path / "qualification.toml"
    config.write_text(
        "\n".join([
            'schema = "anvil-connect.qualification-config/v1"',
            f'source_root = "{source}"', f'artifact_root = "{artifacts}"',
            f'playwright_root = "{playwright}"', f'go_module_cache = "{cache}"',
            "timeout_seconds = 60", "[tools]",
            *[f'{name} = "{path}"' for name, path in sorted(tools.items())],
            "",
        ]), encoding="utf-8",
    )
    return config, {"source": source, "artifacts": artifacts, **tools}


def test_closed_config_rejects_unknown_and_unowned_artifact_root(tmp_path: Path) -> None:
    config, paths = _config(tmp_path)
    raw = config.read_text(encoding="utf-8")
    config.write_text(raw.replace('timeout_seconds = 60', 'unexpected = "no"\ntimeout_seconds = 60'), encoding="utf-8")
    with pytest.raises(subject.QualificationError, match="unknown") as caught:
        subject._read_config(config)
    assert caught.value.code == "config-invalid"
    config, paths = _config(tmp_path / "second")
    paths["artifacts"].chmod(0o755)
    with pytest.raises(subject.QualificationError, match="owned private") as caught:
        subject._private_directory(paths["artifacts"])
    assert caught.value.code == "artifact-root-invalid"


def test_private_stage_omits_untracked_source_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, paths = _config(tmp_path)
    (paths["source"] / "connect/untracked-private-backup").write_text("not staged", encoding="utf-8")
    tracked = [Path("connect/lab/edge-tools.json"), Path("connect/transport.lock.json"), Path("connect/test/browser_edge.spec.mjs")]
    monkeypatch.setattr(subject, "_tracked_connect_files", lambda root: tracked)
    run_dir = paths["artifacts"] / "run-test"
    run_dir.mkdir(mode=0o700)
    stage = subject._copy_stage(subject._read_config(config), run_dir)
    assert (stage / "connect/test/browser_edge.spec.mjs").is_file()
    assert not (stage / "connect/untracked-private-backup").exists()
    shutil.rmtree(run_dir)


def test_qualify_stages_privately_and_records_only_safe_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, paths = _config(tmp_path)
    monkeypatch.setattr(subject, "_source_metadata", lambda _: {"revision": "d01d36ae" + "0" * 32, "dirty": True})
    monkeypatch.setattr(subject, "_tracked_connect_files", lambda root: [Path("connect/lab/edge-tools.json"), Path("connect/transport.lock.json"), Path("connect/test/browser_edge.spec.mjs"), Path("connect/package-lock.json")])
    (paths["source"] / "connect/package-lock.json").write_text('{"packages":{"node_modules/playwright":{"version":"1.63.0"}}}')
    monkeypatch.setattr(subject, "_locks", lambda _: {"files": {"edge_tools": "a" * 64, "transport": "b" * 64}, "binaries": {name: "a" * 64 for name in ("caddy", "authelia", "wstunnel")}})
    monkeypatch.setattr(subject, "_sha256", lambda _: "a" * 64)
    monkeypatch.setattr(subject, "_tool_metadata", lambda _: {"go": {"sha256": "c" * 64, "version": "go1"}})
    seen: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float, expected_name: str, supervisor: Path | None = None) -> tuple[str, float, bool, int | None]:
        seen.append((argv, env))
        return "passed", 0.01, False, None

    monkeypatch.setattr(subject, "_run_test", fake_run)
    result = subject.qualify(config)
    assert result["schema"] == "anvil-connect.qualification/v1"
    assert result["ok"] is True and result["counts"] == {"passed": 2, "failed": 0, "skipped": 0, "not_run": 0}
    evidence = json.loads((Path(result["artifact_dir"]) / "evidence.json").read_text())
    assert evidence["network"] == {"fixture_loopback": True, "network_isolation": "not_enforced"}
    assert [case["name"] for case in evidence["tests"]] == list(subject._TESTS)
    assert "synthetic-password" not in json.dumps(evidence)
    staged = Path(result["artifact_dir"]) / "source"
    assert not staged.exists()
    assert not (Path(result["artifact_dir"]) / "supervisor.py").exists()
    assert evidence["cleanup"]["private_stage_removed"] is True
    assert all("SECRET" not in env and "HOME" in env for _, env in seen)
    assert all(env["GOPROXY"] == "off" and env["GOFLAGS"] == "-p=2" for _, env in seen)
    fixture_roots = {Path(env["TMPDIR"]) for _, env in seen}
    assert len(fixture_roots) == 1
    fixture_root = fixture_roots.pop()
    assert fixture_root.parent == Path("/tmp") and fixture_root.name.startswith("acq-")
    assert not fixture_root.exists()


def test_json_report_requires_the_expected_non_skipped_test() -> None:
    good = {"errors": [], "suites": [{"specs": [{"title": subject._TESTS[0], "ok": True, "tests": [{"status": "expected", "results": [{"status": "passed"}]}]}]}]}
    assert supervisor._valid_report(json.dumps(good).encode(), subject._TESTS[0]) is True
    assert supervisor._closure_ms(good, subject._TESTS[0]) is None
    good["suites"][0]["specs"][0]["tests"][0]["status"] = "skipped"
    assert supervisor._valid_report(json.dumps(good).encode(), subject._TESTS[0]) is False
    assert supervisor._skipped_report(json.dumps(good).encode(), subject._TESTS[0]) is True
    assert supervisor._skipped_report(json.dumps({"errors": [], "suites": []}).encode(), subject._TESTS[0]) is False


def test_stream_closure_measurement_is_exact_and_closed() -> None:
    expiry = "container-gated browser and CLI streams close on session expiry"
    assert expiry in subject._STREAM_CLOSURE_TESTS
    assert expiry in supervisor._STREAM_CLOSURE_TESTS
    stream = next(iter(subject._STREAM_CLOSURE_TESTS))
    report = {"errors": [], "suites": [{"specs": [{"title": stream, "ok": True, "tests": [{
        "status": "expected", "results": [{"status": "passed"}],
        "annotations": [{"type": "closure_ms", "description": "17"}],
    }]}]}]}
    assert supervisor._valid_report(json.dumps(report).encode(), stream) is True
    assert supervisor._closure_ms(report, stream) == 17
    annotations = report["suites"][0]["specs"][0]["tests"][0]["annotations"]
    for description in (None, True, 17, "-1", "01", "1001", "9" * 10_000):
        annotations[0]["description"] = description
        assert supervisor._valid_report(json.dumps(report).encode(), stream) is False
    annotations[0]["description"] = "17"
    annotations.append({"type": "closure_ms", "description": "18"})
    assert supervisor._valid_report(json.dumps(report).encode(), stream) is False
    report["suites"][0]["specs"][0]["tests"][0]["annotations"] = []
    assert supervisor._valid_report(json.dumps(report).encode(), stream) is False
    nonstream = subject._TESTS[0]
    report["suites"][0]["specs"][0]["title"] = nonstream
    report["suites"][0]["specs"][0]["tests"][0]["annotations"] = [{"type": "closure_ms", "description": "17"}]
    assert supervisor._valid_report(json.dumps(report).encode(), nonstream) is False


def test_supervisor_envelope_requires_integer_stream_measurement() -> None:
    stream = next(iter(subject._STREAM_CLOSURE_TESTS))
    envelope = {"status": "passed", "escalated": False, "failure_stage": None, "fixture_marker": None, "closure_ms": 17}
    assert subject._supervisor_status(json.dumps(envelope).encode(), stream) == ("passed", False, 17)
    for value in ("17", True, -1, 1001):
        envelope["closure_ms"] = value
        assert subject._supervisor_status(json.dumps(envelope).encode(), stream) == ("runner-failed-supervisor", True, None)


def test_supervisor_classifies_only_explicit_expected_skip() -> None:
    skipped = json.dumps({"errors": [], "suites": [{"specs": [{"title": subject._TESTS[0], "ok": True, "tests": [{"status": "skipped", "results": []}]}]}]})
    result, _, escalated, _ = subject._run_test(
        [sys.executable, "-c", "print(%r)" % skipped], cwd=Path.cwd(), env={"PATH": "/usr/bin:/bin"},
        timeout=2, expected_name=subject._TESTS[0],
    )
    assert result == "skipped" and escalated is False
    empty, _, _, _ = subject._run_test(
        [sys.executable, "-c", "print(%r)" % json.dumps({"errors": [], "suites": []})], cwd=Path.cwd(), env={"PATH": "/usr/bin:/bin"},
        timeout=2, expected_name=subject._TESTS[0],
    )
    assert empty == "runner-failed-report-parsing"


def _wait_for(path: Path) -> None:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    pytest.fail("owned child did not become ready")


def test_supervisor_accepts_only_a_verified_report() -> None:
    report = json.dumps({"errors": [], "suites": [{"specs": [{"title": subject._TESTS[0], "ok": True, "tests": [{"status": "expected", "results": [{"status": "passed"}]}]}]}]})
    code = "import sys; sys.stderr.write('synthetic-warning-secret'); print(%r)" % report
    result, _, escalated, _ = subject._run_test(
        [sys.executable, "-c", code], cwd=Path.cwd(), env={"PATH": "/usr/bin:/bin"},
        timeout=2, expected_name=subject._TESTS[0],
    )
    assert result == "passed" and escalated is False


def test_owned_timeout_kills_only_its_process_group(tmp_path: Path) -> None:
    # The leader exits after its descendant has bound the listener. A sibling
    # started by the caller after that point must never enter the supervisor.
    marker = tmp_path / "ready"
    unrelated: list[subprocess.Popen[bytes]] = []
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    child = "import socket,time; s=socket.socket(); s.bind(('127.0.0.1', %d)); open(%r,'w').write('ready'); time.sleep(5)" % (port, str(marker))
    code = "import subprocess,sys; subprocess.Popen([sys.executable,'-c',%r])" % child

    def concurrent_sibling() -> None:
        _wait_for(marker)
        unrelated.append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"], start_new_session=True))

    trigger = threading.Thread(target=concurrent_sibling, daemon=True)
    trigger.start()
    try:
        result, _, escalated, _ = subject._run_test([sys.executable, "-c", code], cwd=Path.cwd(), env={"PATH": "/usr/bin:/bin"}, timeout=0.2, expected_name=subject._TESTS[0])
        trigger.join(timeout=1)
        assert marker.exists() and result == "runner-timeout"
        assert escalated is False and len(unrelated) == 1 and unrelated[0].poll() is None
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    finally:
        if unrelated:
            os.killpg(unrelated[0].pid, 9)
            unrelated[0].wait(timeout=2)


def test_valid_report_cannot_hide_detached_listener(tmp_path: Path) -> None:
    marker = tmp_path / "ready"
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    report = json.dumps({"errors": [], "suites": [{"specs": [{"title": subject._TESTS[0], "ok": True, "tests": [{"status": "expected", "results": [{"status": "passed"}]}]}]}]})
    child = "import os,signal,socket,time; os.setsid(); signal.signal(signal.SIGTERM, signal.SIG_IGN); s=socket.socket(); s.bind(('127.0.0.1', %d)); open(%r,'w').write('ready'); os.close(1); os.close(2); time.sleep(5)" % (port, str(marker))
    leader = "import subprocess,sys; subprocess.Popen([sys.executable,'-c',%r]); print(%r)" % (child, report)
    trigger = threading.Thread(target=lambda: _wait_for(marker), daemon=True)
    trigger.start()
    result, _, escalated, _ = subject._run_test([sys.executable, "-c", leader], cwd=Path.cwd(), env={"PATH": "/usr/bin:/bin"}, timeout=1, expected_name=subject._TESTS[0])
    trigger.join(timeout=1)
    assert marker.exists() and result.startswith("runner-failed-") and escalated is True
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def test_setsiddescendant_listener_is_reaped_after_leader_exit(tmp_path: Path) -> None:
    marker = tmp_path / "ready"
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    child = (
        "import os,signal,socket,time; os.setsid(); signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "s=socket.socket(); s.bind(('127.0.0.1', %d)); open(%r,'w').write('ready'); time.sleep(5)" % (port, str(marker))
    )
    leader = "import subprocess,sys; subprocess.Popen([sys.executable,'-c',%r])" % child
    trigger = threading.Thread(target=lambda: _wait_for(marker), daemon=True)
    trigger.start()
    result, _, escalated, _ = subject._run_test(
        [sys.executable, "-c", leader], cwd=Path.cwd(), env={"PATH": "/usr/bin:/bin"},
        timeout=0.2, expected_name=subject._TESTS[0],
    )
    trigger.join(timeout=1)
    assert marker.exists() and result.startswith("runner-failed-") and escalated is True
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def test_child_flood_is_bounded_and_fails_closed() -> None:
    code = "import subprocess,sys; c=\"import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(5)\"; [subprocess.Popen([sys.executable,'-c',c]) for _ in range(65)]"
    started = time.monotonic()
    result, _, escalated, _ = subject._run_test(
        [sys.executable, "-c", code], cwd=Path.cwd(), env={"PATH": "/usr/bin:/bin"},
        timeout=0.2, expected_name=subject._TESTS[0],
    )
    assert result.startswith("runner-failed-") and escalated is True
    assert time.monotonic() - started < 5


def test_keyboard_interrupt_cleans_owned_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "ready"
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    child = "import socket,time; s=socket.socket(); s.bind(('127.0.0.1', %d)); open(%r,'w').write('ready'); time.sleep(5)" % (port, str(marker))

    def interrupt_after_ready() -> None:
        _wait_for(marker)
        _thread.interrupt_main()

    trigger = threading.Thread(target=interrupt_after_ready, daemon=True)
    trigger.start()
    result, _, escalated, _ = subject._run_test(
        [sys.executable, "-c", child], cwd=Path.cwd(), env={"PATH": "/usr/bin:/bin"},
        timeout=2, expected_name=subject._TESTS[0],
    )
    trigger.join(timeout=1)
    assert marker.exists() and result == "runner-interrupted" and escalated is False
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def test_interrupted_or_failed_child_output_never_becomes_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _ = _config(tmp_path)
    monkeypatch.setattr(subject, "_source_metadata", lambda _: {"revision": "d01d36ae" + "0" * 32, "dirty": False})
    monkeypatch.setattr(subject, "_tracked_connect_files", lambda root: [Path("connect/lab/edge-tools.json"), Path("connect/transport.lock.json"), Path("connect/test/browser_edge.spec.mjs"), Path("connect/package-lock.json")])
    (config.parent / "source/connect/package-lock.json").write_text('{"packages":{"node_modules/playwright":{"version":"1.63.0"}}}')
    monkeypatch.setattr(subject, "_locks", lambda _: {"files": {"edge_tools": "a" * 64, "transport": "b" * 64}, "binaries": {name: "a" * 64 for name in ("caddy", "authelia", "wstunnel")}})
    monkeypatch.setattr(subject, "_sha256", lambda _: "a" * 64)
    monkeypatch.setattr(subject, "_tool_metadata", lambda _: {})
    monkeypatch.setattr(subject, "_run_test", lambda *args, **kwargs: ("runner-failed", 0.02, False, None))
    result = subject.qualify(config)
    assert result["ok"] is False and result["error_code"] == "runner-failed"
    evidence = (Path(result["artifact_dir"]) / "evidence.json").read_text(encoding="utf-8")
    assert "runner-failed" in evidence
    assert "stderr" not in evidence and "output" not in evidence


def test_pidfd_signaling_never_uses_a_numeric_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(supervisor.signal, "pidfd_send_signal", lambda fd, sig: sent.append((fd, sig)))
    monkeypatch.setattr(supervisor.os, "kill", lambda *args: pytest.fail("numeric pid signal"))
    supervisor.Children._signal([supervisor.Child(99999, "1", 37)], signal.SIGTERM)
    assert sent == [(37, signal.SIGTERM)]


def test_procfs_permission_error_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> object:
        raise PermissionError("denied")

    monkeypatch.setattr(supervisor.Path, "open", denied)
    with pytest.raises(supervisor.ProcFailure):
        supervisor._children(os.getpid())


def test_finite_tree_over_two_small_batches_is_reaped(capsys: pytest.CaptureFixture[str]) -> None:
    code = "import subprocess,sys; c=\"import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(5)\"; [subprocess.Popen([sys.executable,'-c',c]) for _ in range(5)]"
    assert supervisor.run([sys.executable, "-c", code], 0.2, subject._TESTS[0], batch_size=2) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "runner-failed", "escalated": True, "failure_stage": "supervisor", "fixture_marker": None, "closure_ms": None}


def test_pidfd_preflight_refuses_unavailable_kernel_support(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject.os, "pidfd_open", lambda *args: (_ for _ in ()).throw(OSError("no pidfd")))
    with pytest.raises(subject.QualificationError) as caught:
        subject._require_linux_pidfds()
    assert caught.value.code == "runner-unavailable"


def test_static_failure_stage_redacts_report_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = "synthetic-credential-DO-NOT-PERSIST"
    failed = {"errors": [], "suites": [{"specs": [{"title": subject._TESTS[0], "ok": False, "tests": [{"status": "unexpected", "results": [{"status": "failed", "error": {"message": "expect failed " + sentinel}}]}]}]}]}
    assert supervisor._failure_stage(json.dumps(failed).encode(), subject._TESTS[0]) == "browser-assertion"
    config, _ = _config(tmp_path)
    monkeypatch.setattr(subject, "_source_metadata", lambda _: {"revision": "d01d36ae" + "0" * 32, "dirty": False})
    monkeypatch.setattr(subject, "_tracked_connect_files", lambda root: [Path("connect/lab/edge-tools.json"), Path("connect/transport.lock.json"), Path("connect/test/browser_edge.spec.mjs"), Path("connect/package-lock.json")])
    (config.parent / "source/connect/package-lock.json").write_text('{"packages":{"node_modules/playwright":{"version":"1.63.0"}}}')
    monkeypatch.setattr(subject, "_locks", lambda _: {"files": {"edge_tools": "a" * 64, "transport": "b" * 64}, "binaries": {name: "a" * 64 for name in ("caddy", "authelia", "wstunnel")}})
    monkeypatch.setattr(subject, "_sha256", lambda _: "a" * 64)
    monkeypatch.setattr(subject, "_tool_metadata", lambda _: {})
    monkeypatch.setattr(subject, "_run_test", lambda *args, **kwargs: ("runner-failed-browser-assertion", 0.01, False, None))
    result = subject.qualify(config)
    evidence = (Path(result["artifact_dir"]) / "evidence.json").read_text(encoding="utf-8")
    assert result["ok"] is False
    assert json.loads(evidence)["tests"][0]["failure_stage"] == "browser-assertion"
    assert sentinel not in evidence


@pytest.mark.parametrize("filename", ["browser_edge_fixture_test.go", "browser_runtime_fixture_test.go", "browser_edge.spec.mjs"])
def test_fixture_marker_is_closed_and_persisted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str) -> None:
    marker = filename + ":586"
    stage = "browser-assertion" if filename.endswith("mjs") else "fixture-startup"
    failed = {"errors": [{"message": marker + ": redacted"}], "suites": []}
    assert supervisor._failure_stage(json.dumps(failed).encode(), subject._TESTS[0]) == stage
    assert supervisor._fixture_marker(failed) == marker
    config, _ = _config(tmp_path)
    monkeypatch.setattr(subject, "_source_metadata", lambda _: {"revision": "d01d36ae" + "0" * 32, "dirty": False})
    monkeypatch.setattr(subject, "_tracked_connect_files", lambda root: [Path("connect/lab/edge-tools.json"), Path("connect/transport.lock.json"), Path("connect/test/browser_edge.spec.mjs"), Path("connect/package-lock.json")])
    (config.parent / "source/connect/package-lock.json").write_text('{"packages":{"node_modules/playwright":{"version":"1.63.0"}}}')
    monkeypatch.setattr(subject, "_locks", lambda _: {"files": {"edge_tools": "a" * 64, "transport": "b" * 64}, "binaries": {name: "a" * 64 for name in ("caddy", "authelia", "wstunnel")}})
    monkeypatch.setattr(subject, "_sha256", lambda _: "a" * 64)
    monkeypatch.setattr(subject, "_tool_metadata", lambda _: {})
    monkeypatch.setattr(subject, "_run_test", lambda *args, **kwargs: ("runner-failed-" + stage + "@" + marker, 0.01, False, None))
    result = subject.qualify(config)
    evidence = json.loads((Path(result["artifact_dir"]) / "evidence.json").read_text())
    assert evidence["tests"][0]["failure_stage"] == stage
    assert evidence["tests"][0]["fixture_marker"] == marker


def test_fixture_temp_root_is_removed_when_staging_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _ = _config(tmp_path)
    fixture_root = tmp_path / "acq-test"
    fixture_root.mkdir(mode=0o700)
    monkeypatch.setattr(subject, "_fixture_temp_root", lambda: fixture_root)
    monkeypatch.setattr(subject, "_source_metadata", lambda _: {"revision": "d01d36ae" + "0" * 32, "dirty": False})
    monkeypatch.setattr(subject, "_locks", lambda _: {"files": {"edge_tools": "a" * 64, "transport": "b" * 64}, "binaries": {name: "a" * 64 for name in ("caddy", "authelia", "wstunnel")}})
    monkeypatch.setattr(subject, "_sha256", lambda _: "a" * 64)
    monkeypatch.setattr(subject, "_tracked_connect_files", lambda _: [Path("connect/test/browser_edge.spec.mjs")])
    monkeypatch.setattr(subject, "_copy_stage", lambda *args: (_ for _ in ()).throw(OSError("staging")))
    with pytest.raises(subject.QualificationError) as caught:
        subject.qualify(config)
    assert caught.value.code == "staging-failed"
    assert not fixture_root.exists()


def test_interrupted_staging_removes_runner_and_fixture_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, paths = _config(tmp_path)
    fixture_root = tmp_path / "acq-test"
    fixture_root.mkdir(mode=0o700)
    monkeypatch.setattr(subject, "_fixture_temp_root", lambda: fixture_root)
    monkeypatch.setattr(subject, "_source_metadata", lambda _: {"revision": "d01d36ae" + "0" * 32, "dirty": False})
    monkeypatch.setattr(subject, "_locks", lambda _: {"files": {"edge_tools": "a" * 64, "transport": "b" * 64}, "binaries": {name: "a" * 64 for name in ("caddy", "authelia", "wstunnel")}})
    monkeypatch.setattr(subject, "_sha256", lambda _: "a" * 64)
    monkeypatch.setattr(subject, "_tracked_connect_files", lambda _: [Path("connect/test/browser_edge.spec.mjs")])
    monkeypatch.setattr(subject, "_copy_stage", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(subject.QualificationError) as caught:
        subject.qualify(config)
    assert caught.value.code == "runner-interrupted"
    assert not fixture_root.exists()
    assert not any(paths["artifacts"].iterdir())


@pytest.mark.parametrize("failure", [OSError("chmod"), KeyboardInterrupt()])
def test_fixture_temp_root_cleans_a_partial_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException) -> None:
    partial = tmp_path / "acq-partial"
    partial.mkdir(mode=0o700)
    monkeypatch.setattr(subject.tempfile, "mkdtemp", lambda **_kwargs: str(partial))
    monkeypatch.setattr(subject.os, "chmod", lambda *_args: (_ for _ in ()).throw(failure))
    expected = KeyboardInterrupt if isinstance(failure, KeyboardInterrupt) else subject.QualificationError
    with pytest.raises(expected) as caught:
        subject._fixture_temp_root()
    if expected is subject.QualificationError:
        assert caught.value.code == "staging-failed"
    assert not partial.exists()


def test_preflight_errors_are_not_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing.toml"
    with pytest.raises(subject.QualificationError) as caught:
        subject.qualify(missing)
    assert caught.value.code == "config-missing"
    assert caught.value.execution_started is False and caught.value.stage == "preflight"
    config, paths = _config(tmp_path / "binary")
    monkeypatch.setattr(subject, "_source_metadata", lambda _: {"revision": "d01d36ae" + "0" * 32, "dirty": False})
    monkeypatch.setattr(subject, "_locks", lambda _: {"files": {}, "binaries": {}})
    paths["node"].unlink()
    with pytest.raises(subject.QualificationError) as caught:
        subject.qualify(config)
    assert caught.value.code == "tool-invalid"
    assert caught.value.execution_started is False and caught.value.stage == "preflight"


def test_early_result_marks_remaining_test_not_run_and_junit_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _ = _config(tmp_path)
    monkeypatch.setattr(subject, "_source_metadata", lambda _: {"revision": "d01d36ae" + "0" * 32, "dirty": False})
    monkeypatch.setattr(subject, "_tracked_connect_files", lambda _: [Path("connect/lab/edge-tools.json"), Path("connect/transport.lock.json"), Path("connect/test/browser_edge.spec.mjs"), Path("connect/package-lock.json")])
    (config.parent / "source/connect/package-lock.json").write_text('{"packages":{"node_modules/playwright":{"version":"1.63.0"}}}')
    monkeypatch.setattr(subject, "_locks", lambda _: {"files": {"edge_tools": "a" * 64, "transport": "b" * 64}, "binaries": {name: "a" * 64 for name in ("caddy", "authelia", "wstunnel")}})
    monkeypatch.setattr(subject, "_sha256", lambda _: "a" * 64)
    monkeypatch.setattr(subject, "_tool_metadata", lambda _: {})
    calls: list[str] = []
    def fail_first(_argv, *, expected_name, **_kwargs):
        calls.append(expected_name)
        return "runner-failed-browser-assertion", 0.01, False, None
    monkeypatch.setattr(subject, "_run_test", fail_first)
    result = subject.qualify(config)
    evidence_path = Path(result["artifact_dir"]) / "evidence.json"
    evidence = json.loads(evidence_path.read_text())
    assert calls == [subject._TESTS[0]]
    assert result["state"] == "failed" and result["error_code"] == "runner-failed"
    assert result["counts"] == {"passed": 0, "failed": 1, "skipped": 0, "not_run": 1}
    assert [item["status"] for item in evidence["tests"]] == ["runner-failed", "not-run"]
    root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).parse(Path(result["artifact_dir"]) / "junit.xml").getroot()
    assert root.attrib["failures"] == "1" and root.attrib["skipped"] == "1"
    assert root.findall("testcase")[1].find("skipped").attrib["type"] == "not-run"


def test_skipped_result_is_non_success_and_marks_remaining_not_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _ = _config(tmp_path)
    monkeypatch.setattr(subject, "_source_metadata", lambda _: {"revision": "d01d36ae" + "0" * 32, "dirty": False})
    monkeypatch.setattr(subject, "_tracked_connect_files", lambda _: [Path("connect/lab/edge-tools.json"), Path("connect/transport.lock.json"), Path("connect/test/browser_edge.spec.mjs"), Path("connect/package-lock.json")])
    (config.parent / "source/connect/package-lock.json").write_text('{"packages":{"node_modules/playwright":{"version":"1.63.0"}}}')
    monkeypatch.setattr(subject, "_locks", lambda _: {"files": {"edge_tools": "a" * 64, "transport": "b" * 64}, "binaries": {name: "a" * 64 for name in ("caddy", "authelia", "wstunnel")}})
    monkeypatch.setattr(subject, "_sha256", lambda _: "a" * 64)
    monkeypatch.setattr(subject, "_tool_metadata", lambda _: {})
    monkeypatch.setattr(subject, "_run_test", lambda *args, **kwargs: ("skipped", 0.01, False, None))
    result = subject.qualify(config)
    assert result["ok"] is False and result["state"] == "skipped" and result["error_code"] == "skipped"
    assert result["counts"] == {"passed": 0, "failed": 0, "skipped": 1, "not_run": 1}


@pytest.mark.parametrize("launch_fails", [False, True])
def test_execution_error_preserves_only_known_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launch_fails: bool) -> None:
    config, _ = _config(tmp_path)
    fixture_root = tmp_path / "acq-test"
    fixture_root.mkdir(mode=0o700)
    monkeypatch.setattr(subject, "_fixture_temp_root", lambda: fixture_root)
    monkeypatch.setattr(subject, "_source_metadata", lambda _: {"revision": "d01d36ae" + "0" * 32, "dirty": False})
    monkeypatch.setattr(subject, "_tracked_connect_files", lambda _: [Path("connect/lab/edge-tools.json"), Path("connect/transport.lock.json"), Path("connect/test/browser_edge.spec.mjs"), Path("connect/package-lock.json")])
    (config.parent / "source/connect/package-lock.json").write_text('{"packages":{"node_modules/playwright":{"version":"1.63.0"}}}')
    monkeypatch.setattr(subject, "_locks", lambda _: {"files": {"edge_tools": "a" * 64, "transport": "b" * 64}, "binaries": {name: "a" * 64 for name in ("caddy", "authelia", "wstunnel")}})
    monkeypatch.setattr(subject, "_sha256", lambda _: "a" * 64)
    monkeypatch.setattr(subject, "_tool_metadata", lambda _: {})
    def run_test(*args, **kwargs):
        if launch_fails:
            raise subject._error("runner-unavailable", "safe launch error")
        return ("passed", 0.01, False, None)
    monkeypatch.setattr(subject, "_run_test", run_test)
    calls = 0
    def fail_once(root: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1 and not launch_fails:
            raise subject._error("staging-failed", "safe cleanup error")
        shutil.rmtree(root)
    monkeypatch.setattr(subject, "_remove_fixture_temp_root", fail_once)
    from anvil_serving.connect.cli import dispatch
    result = dispatch(["qualify", "--config", str(config)])
    assert result.error is not None
    assert result.data["state"] == "failed"
    assert result.data["stage"] == ("execution" if launch_fails else "cleanup")
    assert result.data["counts"] == (None if launch_fails else {"passed": 2, "failed": 0, "skipped": 0, "not_run": 0})
    assert calls == (1 if launch_fails else 2) and not fixture_root.exists()


@pytest.mark.parametrize("code,stage", [("ERR_CONNECTION_REFUSED","browser-connection"),("ERR_NAME_NOT_RESOLVED","browser-dns"),("browser-navigation-failed","browser-navigation")])
def test_browser_network_errors_keep_static_diagnostics(code, stage):
    from anvil_serving.connect import _qualification_supervisor as supervisor
    assert supervisor._failure_stage(json.dumps({"errors":[{"message":code+" private-sentinel"}]}).encode(), subject._TESTS[0]) == stage
    output = json.dumps({"status":"runner-failed","escalated":False,"failure_stage":stage,"fixture_marker":None,"closure_ms":None}).encode()
    assert subject._supervisor_status(output, subject._TESTS[0]) == ("runner-failed-"+stage,False, None)


def test_browser_error_location_keeps_only_public_basename_and_line():
    value = {"suites":[{"tests":[{"results":[{"error":{"message":"private-sentinel","location":{"file":"/private/source/browser_edge.spec.mjs","line":551,"column":19}}}]}]}]}
    assert supervisor._fixture_marker(value) == "browser_edge.spec.mjs:551"
    assert supervisor._failure_stage(json.dumps(value).encode(),subject._TESTS[0]) == "browser-assertion"
    value["suites"][0]["tests"][0]["results"][0]["error"]["location"]["file"] = "/private/unknown.js"
    assert supervisor._fixture_marker(value) is None


@pytest.mark.parametrize("message,stage", [
    ("fixture did not become ready", "fixture-startup"),
    ("net::ERR_NAME_NOT_RESOLVED", "browser-dns"),
    ("net::ERR_CONNECTION_REFUSED", "browser-connection"),
    ("net::ERR_CERT_AUTHORITY_INVALID", "browser-launch-cert"),
    ("fixture build failed", "build"),
])
def test_browser_location_does_not_mask_failure_stage(message, stage):
    value = {"errors": [{"message": message, "location": {
        "file": "/private/source/browser_edge.spec.mjs", "line": 495,
    }}]}
    assert supervisor._fixture_marker(value) == "browser_edge.spec.mjs:495"
    assert supervisor._failure_stage(json.dumps(value).encode(), subject._TESTS[0]) == stage


def test_closed_phase_annotation_identifies_timeout_before_helper_location():
    value = {"errors": [{"message": "private-sentinel", "location": {
        "file": "/private/source/browser_edge.spec.mjs", "line": 255,
    }}], "annotations": [{"type": "diagnostic-location", "description": "browser_edge.spec.mjs:580"}]}
    assert supervisor._fixture_marker(value) == "browser_edge.spec.mjs:580"
    value["annotations"][0]["description"] = "/private/secret"
    assert supervisor._fixture_marker(value) == "browser_edge.spec.mjs:255"
