from __future__ import annotations

import concurrent.futures
import json
import threading

import pytest

from anvil_serving.voice import cli


def test_existing_evidence_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(tmp_path))
    target = tmp_path / "attempt.json"
    original = b'{"original":true}\n'
    target.write_bytes(original)
    with pytest.raises(FileExistsError):
        cli._write_benchmark_evidence(str(target), {"replacement": True})
    assert target.read_bytes() == original
    assert not list(tmp_path.glob(".attempt.json.*"))


def test_concurrent_evidence_publication_keeps_one_complete_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(tmp_path))
    target = tmp_path / "attempt.json"
    ready = threading.Barrier(2)

    def writer(attempt):
        ready.wait()
        try:
            cli._write_benchmark_evidence(str(target), {"attempt": attempt, "data": list(range(1000))})
            return attempt
        except FileExistsError:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(writer, attempt) for attempt in (1, 2)]
        outcomes = [future.result() for future in futures]
    successful = [attempt for attempt in outcomes if attempt is not None]
    assert len(successful) == 1
    retained = json.loads(target.read_text())
    assert retained["attempt"] == successful[0]
    assert retained["data"] == list(range(1000))
    assert not list(tmp_path.glob(".attempt.json.*"))
