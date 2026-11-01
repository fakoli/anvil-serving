from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from anvil_serving.connect import qualification as runner
from anvil_serving.connect import qualification_container as image
from anvil_serving.connect import qualification_container_run as subject
from tests.connect.test_qualification_container import _inputs


def _revocation_result() -> bytes:
    return json.dumps({"tests": [
        {"name": name, "status": "passed", "duration_seconds": 0.1, "closure_ms": 1}
        for name in subject._REVOCATION_TESTS
    ], "escalated": False}).encode()


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux process groups and pipe selectors")
def test_attached_output_is_bounded_and_return_code_matters():
    environment = {"PATH": "/usr/bin:/bin"}
    assert subject._attached([sys.executable, "-c", 'print("safe")'], environment, 2) == b"safe\n"
    for script in ('print("x"*9000)', 'raise SystemExit(1)', 'import time; time.sleep(5)'):
        with pytest.raises(runner.QualificationError):
            subject._attached([sys.executable, "-c", script], environment, 1)


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux container and artifact custody")
def test_revocation_lane_uses_the_pinned_container_and_retains_only_stream_measurements(tmp_path, monkeypatch):
    config, paths = _inputs(tmp_path)
    for filename in ("qualification.py", "_qualification_supervisor.py", "qualification_container_run.py"):
        destination = paths["source"] / "anvil_serving/connect" / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(Path(subject.__file__).with_name(filename).read_bytes())
    monkeypatch.setattr(runner, "_source_metadata", lambda _: {"revision": "a" * 40, "dirty": False})
    monkeypatch.setattr(runner, "_tracked_connect_files", lambda _: [Path("connect/go.mod")])
    monkeypatch.setattr(image, "_cached_receipt", lambda *args: "sha256:" + "c" * 64)
    monkeypatch.setattr(image, "_image", lambda *args, **kwargs: "sha256:" + "c" * 64)
    observed = {}
    def attached(args, environment, timeout):
        observed["args"] = args
        observed["environment"] = environment
        observed["timeout"] = timeout
        assert args[-1] == "revocation"
        assert args[args.index("--network") + 1] == "none"
        assert args[args.index("--pull") + 1] == "never"
        assert "--read-only" in args and "--publish" not in args and "--gpus" not in args
        assert set(environment) == {"PATH", "LANG", "LC_ALL", "DOCKER_CONFIG"}
        return _revocation_result()
    monkeypatch.setattr(subject, "_attached", attached)
    monkeypatch.setattr(image, "_docker", lambda *args, **kwargs: (0, b""))
    result = subject.qualify(config, lane="revocation")
    assert result["ok"] is True and result["counts"] == {"passed": 10, "failed": 0, "skipped": 0, "not_run": 0}
    evidence = json.loads((Path(result["artifact_dir"]) / "evidence.json").read_text())
    assert [case["name"] for case in evidence["tests"]] == list(subject._REVOCATION_TESTS)
    assert [case["closure_ms"] for case in evidence["tests"]] == [1] * 10
    assert "scope_limitations" not in evidence
    assert observed["args"][-1] == "revocation"


def test_revocation_registry_and_unknown_lane_are_closed():
    assert subject._REVOCATION_TESTS[:4] == subject._DEVICE_TESTS[6:]
    assert subject._REVOCATION_TESTS[-6:] == (subject._SESSION_EXPIRY_TEST, subject._RESTART_TEST, subject._AUTHORITY_RESET_TEST, subject._RESTORE_TEST, subject._KEY_REVOKE_TEST, subject._GRANT_REMOVAL_TEST)
    assert subject._SESSION_EXPIRY_TEST in subject._STREAM_CLOSURE_TESTS
    assert subject._RESTART_TEST in subject._STREAM_CLOSURE_TESTS
    assert subject._AUTHORITY_RESET_TEST in subject._STREAM_CLOSURE_TESTS
    assert subject._RESTORE_TEST in subject._STREAM_CLOSURE_TESTS
    assert subject._KEY_REVOKE_TEST in subject._STREAM_CLOSURE_TESTS
    assert subject._GRANT_REMOVAL_TEST in subject._STREAM_CLOSURE_TESTS
    assert len(subject._DEVICE_TESTS) == 10
    assert subject._lane_tests("revocation", runner._TESTS) == subject._REVOCATION_TESTS
    assert subject._fixture_flags("revocation") == {
        "ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE": "1",
        "ANVIL_CONNECT_BROWSER_EXPIRY_FIXTURE": "1",
        "ANVIL_CONNECT_BROWSER_RESTART_FIXTURE": "1",
        "ANVIL_CONNECT_BROWSER_GRANT_FIXTURE": "1",
    }
    assert subject._fixture_flags("device") == {
        "ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE": "1",
        "ANVIL_CONNECT_BROWSER_PASSKEY_FIXTURE": "1",
    }
    with pytest.raises(ValueError):
        subject._lane_tests("unknown", runner._TESTS)
    with pytest.raises(ValueError):
        subject._fixture_flags("unknown")
    with pytest.raises(runner.QualificationError):
        subject.qualify(lane="unknown")
