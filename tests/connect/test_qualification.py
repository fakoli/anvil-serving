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
import threading

import pytest

from anvil_serving.connect import qualification as subject
from anvil_serving.connect import _qualification_supervisor as supervisor


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="requires Linux qualification process controls")


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
    reset = "container-gated browser and CLI streams close on authority reset"
    restore = "container-gated browser and CLI credentials fail closed after restore"
    assert expiry in subject._STREAM_CLOSURE_TESTS
    assert expiry in supervisor._STREAM_CLOSURE_TESTS
    assert reset in subject._STREAM_CLOSURE_TESTS
    assert reset in supervisor._STREAM_CLOSURE_TESTS
    assert restore in subject._STREAM_CLOSURE_TESTS
    assert restore in supervisor._STREAM_CLOSURE_TESTS
    for case in ("container-gated CLI key revocation preserves other sessions", "container-gated browser grant removal persists after restart"):
        assert case in subject._STREAM_CLOSURE_TESTS
        assert case in supervisor._STREAM_CLOSURE_TESTS
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


def _wait_for(path: Path) -> None:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    pytest.fail("owned child did not become ready")


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


def test_closed_phase_annotation_identifies_timeout_before_helper_location():
    value = {"errors": [{"message": "private-sentinel", "location": {
        "file": "/private/source/browser_edge.spec.mjs", "line": 255,
    }}], "annotations": [{"type": "diagnostic-location", "description": "browser_edge.spec.mjs:580"}]}
    assert supervisor._fixture_marker(value) == "browser_edge.spec.mjs:580"
    value["annotations"][0]["description"] = "/private/secret"
    assert supervisor._fixture_marker(value) == "browser_edge.spec.mjs:255"
