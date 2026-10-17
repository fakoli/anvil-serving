"""The capacity artifact distinguishes observed client overlap from configured C."""
import json
import threading

import pytest

from anvil_serving.benchmarking.cli import main


@pytest.mark.parametrize("fail", [False, True])
def test_client_overlap_is_observed_and_released_after_failed_calls(tmp_path, fail):
    barrier = threading.Barrier(2)

    def stream(*args, **kwargs):
        barrier.wait(timeout=5)
        if fail:
            raise OSError("controlled transport failure")
        return {
            "ttft": 0.1, "e2e": 0.2, "out_toks": 8,
            "usage": {"prompt_tokens": 100, "completion_tokens": 8},
            "finish_reasons": ["stop"], "stream_terminal_observed": True,
        }

    output = tmp_path / "capacity.json"
    code = main([
        "capacity", "--base-url", "http://127.0.0.1:30002/v1", "--model", "test",
        "--requests", "4", "--concurrency", "2", "--ctx-tokens", "1024",
        "--max-model-len", "8192", "--output", str(output),
    ], stream_request=stream)
    artifact = json.loads(output.read_text())
    assert code == (1 if fail else 0)
    assert artifact["client_concurrency_observed_max"] == 2
    assert artifact["failed"] == (4 if fail else 0)
    assert "does not prove" in artifact["timing_methodology"]["client_concurrency"]

    # Asking for four workers with one request must report one observed call.
    output = tmp_path / "single-request.json"
    main([
        "capacity", "--base-url", "http://127.0.0.1:30002/v1", "--model", "test",
        "--requests", "1", "--concurrency", "4", "--ctx-tokens", "1024",
        "--max-model-len", "8192", "--output", str(output),
    ], stream_request=lambda *a, **k: {"ttft": 0.1, "e2e": 0.2, "out_toks": 2})
    assert json.loads(output.read_text())["client_concurrency_observed_max"] == 1
