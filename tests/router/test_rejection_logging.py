"""Managed rejection logs must agree with the one HTTP response sent."""
import http.client
import json
import threading

import pytest

from anvil_serving.router.config import ServerConfig
from anvil_serving.router.front_door import make_server
from anvil_serving.router.internal import BackendClientError, NoAvailableTierError


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("path", ["/v1/messages", "/v1/chat/completions"])
@pytest.mark.parametrize("error,status", [
    (NoAvailableTierError("chat", ("tier",), kind="over_context"), 413),
    (NoAvailableTierError("chat", ("tier",), kind="unavailable"), 503),
    (BackendClientError(422, "invalid_request", "unsupported request"), 422),
    (RuntimeError("private upstream detail"), 500),
])
def test_managed_rejection_logs_once(capsys, stream, path, error, status):
    class RejectingBackend:
        def generate(self, request):
            raise error
            yield  # Keep the generator protocol; failure precedes any frame.

    server = make_server("127.0.0.1", 0, RejectingBackend(), server_config=ServerConfig())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address[:2], timeout=5)
    try:
        connection.request("POST", path, json.dumps({
            "model": "chat", "stream": stream, "max_tokens": 1,
            "messages": [{"role": "user", "content": "hello"}],
        }), {"Content-Type": "application/json"})
        response = connection.getresponse()
        body = response.read()
        assert response.status == status
        assert b"private upstream detail" not in body
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    events = [line for line in capsys.readouterr().err.splitlines() if line.startswith("[anvil]")]
    assert len(events) == 1
    assert events[0].startswith(f"[anvil] {status} ")
    assert "gateway_request_id=req_" in events[0]
    assert "private upstream detail" not in events[0]


def test_over_context_diagnostics_do_not_blame_the_upstream():
    from anvil_serving.router_diagnostics import diagnose_record

    result = diagnose_record({
        "workload_outcome": "rejected",
        "attempts": [{"succeeded": False, "reason": "over_context"}],
        "usage": {"prompt_tokens": 1125109, "prompt_source": "estimated"},
    })
    assert result["outcome"] == "rejected"
    assert result["upstream_outcome"] == "not_attempted"
    assert result["observations"] == ["request_rejected_over_context"]
    assert "compare_serialized_request_with_client_context_estimate" in result["next_checks"]
    assert result["usage"]["prompt_source"] == "estimated"
