"""Evidence binds the actual staged bytes and every published result file."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from anvil_serving.connect import qualification as subject
from tests.connect.test_qualification import _config


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="requires Linux qualification artifact custody")


def test_go_version_preserves_major_component(tmp_path, monkeypatch):
    tool = tmp_path / "go"
    tool.write_bytes(b"fixture")
    tool.chmod(0o700)
    monkeypatch.setattr(subject.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, b"go version go1.27.1 linux/amd64\n"))
    assert subject._tool_metadata({"go": tool})["go"]["version"] == "1.27.1"


def test_artifacts_bind_one_source_snapshot_and_result_bytes(tmp_path, monkeypatch):
    config, paths = _config(tmp_path)
    lock = paths["source"] / "connect/package-lock.json"
    lock.write_text('{"packages":{"node_modules/playwright":{"version":"1.63.0"}}}')
    tracked = [Path("connect/lab/edge-tools.json"), Path("connect/transport.lock.json"),
               Path("connect/test/browser_edge.spec.mjs"), Path("connect/package-lock.json")]
    calls = []

    def changing_index(root):
        calls.append(root)
        return tracked if len(calls) == 1 else tracked[:-1]

    monkeypatch.setattr(subject, "_tracked_connect_files", changing_index)
    monkeypatch.setattr(subject, "_source_metadata", lambda _: {"revision": "a" * 40, "dirty": True})
    monkeypatch.setattr(subject, "_locks", lambda _: {"files": {}, "binaries": {
        name: hashlib.sha256(paths[name].read_bytes()).hexdigest()
        for name in ("caddy", "authelia", "wstunnel")}})
    monkeypatch.setattr(subject, "_tool_metadata", lambda _: {})
    monkeypatch.setattr(subject, "_run_test", lambda *args, **kwargs: ("passed", 0.01, False, None))
    result = subject.qualify(config)
    assert len(calls) == 1
    directory = Path(result["artifact_dir"])
    evidence = json.loads((directory / "evidence.json").read_text())
    expected = {path.as_posix(): hashlib.sha256((paths["source"] / path).read_bytes()).hexdigest()
                for path in tracked}
    digest = hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert evidence["source"]["staged_files_sha256"] == digest
    summary = json.loads((directory / "result.json").read_text())
    assert summary["ok"] is True and summary["state"] == "passed"
    assert summary["source"] == evidence["source"]
    assert summary["counts"] == result["counts"]
    entries = dict(line.split("  ", 1)[::-1] for line in (directory / "SHA256SUMS").read_text().splitlines())
    assert set(entries) == {"evidence.json", "junit.xml", "result.json"}
    for name, expected_hash in entries.items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected_hash
