"""Bounded controller replay for large benchmark job envelopes."""

import pytest

from anvil_serving.control_plane.controller.http import _BENCHMARK_JOB_RESULT_BYTES
from anvil_serving.control_plane.controller.store import (
    DEFAULT_IDEMPOTENCY_MAX_RESULT_BYTES,
    OperationStore,
)
from tests.test_controller import CONTEXT, _request, running_controller


@pytest.mark.parametrize("tool,configured_limit,payload_size,limit", [
    *[(tool, None, 70 * 1024, _BENCHMARK_JOB_RESULT_BYTES) for tool in (
        "benchmark_job_submit", "benchmark_job_status", "benchmark_job_logs", "benchmark_job_cancel",
    )],
    ("fake", None, 70 * 1024, DEFAULT_IDEMPOTENCY_MAX_RESULT_BYTES),
    ("benchmark_job_submit_extra", None, 70 * 1024, DEFAULT_IDEMPOTENCY_MAX_RESULT_BYTES),
    ("benchmark_job_artifact", None, 70 * 1024, DEFAULT_IDEMPOTENCY_MAX_RESULT_BYTES),
    ("benchmark_job_submit", 64 * 1024, 70 * 1024, 64 * 1024),
    ("benchmark_job_submit", 128 * 1024, 70 * 1024, 128 * 1024),
    ("benchmark_job_submit", None, _BENCHMARK_JOB_RESULT_BYTES + 1, _BENCHMARK_JOB_RESULT_BYTES),
])
def test_controller_result_allowance_is_bounded_scoped_and_respects_config(
    tool, configured_limit, payload_size, limit,
):
    calls = []

    def invoke(name, arguments):
        calls.append((name, arguments))
        return {"ok": True, "data": {"payload": "x" * payload_size}}

    options = {} if configured_limit is None else {"idempotency_max_result_bytes": configured_limit}
    body = {"name": tool, "arguments": {"confirm": True}, "context": CONTEXT}
    headers = {"X-Anvil-Idempotency-Key": "large-result"}
    with running_controller(call_tool_func=invoke, audit_logger=lambda _record: None, **options) as (host, port):
        status, _, first, _ = _request(host, port, "POST", "/tools/call", body, headers)
        assert status == 200
        status, _, record, raw_record = _request(host, port, "GET", "/operations/large-result")
        assert status == 200
        status, _, replay, _ = _request(host, port, "POST", "/tools/call", body, headers)
        assert status == 200
    assert calls == [(tool, body["arguments"])]
    assert replay == record["response"]
    if payload_size < limit:
        assert record["status"] == "succeeded"
        assert record["result"] == replay == first
        assert len(raw_record) < 4 * 1024 * 1024
    else:
        assert record["status"] == "failed"
        assert replay["error"]["code"] == "persisted_result_too_large"
        assert replay["error"]["details"]["max_result_bytes"] == limit


@pytest.mark.parametrize("limit", [None, True, "1024", 1.5, 0, -1])
def test_store_rejects_malformed_dispatch_default_before_writing(tmp_path, limit):
    store = OperationStore(str(tmp_path / "operations.sqlite3"))
    with pytest.raises(ValueError, match="positive integer"):
        store.complete("job", "succeeded", {"ok": True}, None, default_max_result_bytes=limit)
    assert not (tmp_path / "operations.sqlite3").exists()


@pytest.mark.parametrize("limit", [True, "1024", 1.5, 0, -1])
def test_store_rejects_malformed_configured_result_cap(tmp_path, limit):
    with pytest.raises(ValueError, match="positive integer"):
        OperationStore(str(tmp_path / "operations.sqlite3"), max_result_bytes=limit)
    assert not (tmp_path / "operations.sqlite3").exists()
