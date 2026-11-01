import http.client
import json
from pathlib import Path

import pytest

from anvil_serving.router.config import load
from anvil_serving.router.decision_log import DecisionLog, DecisionLogWriter
from anvil_serving.router.serve import RoutingBackend
from tests.router.helpers import http_get, server_context

_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "example.toml"
_TOKEN = "harness-contract-token"


class _Backend:
    def __init__(self): self.requests = []
    def generate(self, request):
        self.requests.append(request)
        yield "ok"


def _post(host, port, path, body, *, header, session_id):
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request("POST", path, json.dumps(body), {"Authorization": "Bearer " + _TOKEN, "Content-Type": "application/json", header: session_id})
        response = connection.getresponse()
        return response.status, response.read()
    finally: connection.close()


@pytest.mark.parametrize("harness,path,header,session_id,body", [("pi", "/v1/responses", "X-Session-Affinity", "pi-responses-1", {"model": "llm.primary", "store": False, "input": [{"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"}, {"type": "function_call_output", "call_id": "call_1", "output": "result"}], "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}]})])
def test_harness_protocols_retain_session_history_and_tool_continuations(tmp_path, harness, path, header, session_id, body):
    backend = _Backend()
    log = DecisionLog(sink=DecisionLogWriter(str(tmp_path / "decisions.jsonl")))
    routing = RoutingBackend(load(_CONFIG), {"primary-local": backend}, decision_log=log)
    with server_context(routing, token=_TOKEN) as (host, port):
        status, response = _post(host, port, path, body, header=header, session_id=session_id)
        history_status, _, history_body = http_get(host, port, f"/v1/requests?session_id={session_id}&history=1", token=_TOKEN)
    assert harness == "pi" and status == 200 and b"error" not in response
    assert backend.requests[0].raw["_anvil_correlation"]["session_id"] == session_id
    assert any(message.role == "tool" and message.content == "result" for message in backend.requests[0].messages)
    history = json.loads(history_body)
    assert history_status == 200 and history["session_id"] == session_id and len(history["records"]) == 1
