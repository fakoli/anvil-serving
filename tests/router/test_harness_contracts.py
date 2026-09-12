"""Router wire contracts declared by Hermes, OpenClaw, and Pi harnesses."""
from __future__ import annotations

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
    def __init__(self) -> None:
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        yield "ok"


def _post(host, port, path, body, *, header, session_id):
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request("POST", path, json.dumps(body), {
            "Authorization": "Bearer " + _TOKEN,
            "Content-Type": "application/json",
            header: session_id,
        })
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _openai_tool_continuation():
    return {
        "model": "llm.primary",
        "messages": [
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }]},
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        ],
        "tools": [{"type": "function", "function": {
            "name": "lookup", "parameters": {"type": "object"},
        }}],
    }


def _anthropic_tool_continuation():
    return {
        "model": "llm.primary",
        "max_tokens": 8,
        "messages": [
            {"role": "assistant", "content": [{
                "type": "tool_use", "id": "call_1", "name": "lookup", "input": {},
            }]},
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "call_1", "content": "result",
            }]},
        ],
        "tools": [{"name": "lookup", "input_schema": {"type": "object"}}],
    }


def _responses_tool_continuation():
    return {
        "model": "llm.primary",
        "store": False,
        "input": [
            {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "result"},
        ],
        "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
    }


@pytest.mark.parametrize(
    ("harness", "path", "header", "session_id", "body"),
    (
        ("hermes", "/v1/chat/completions", "X-Anvil-Session-Id", "hermes-session-1", _openai_tool_continuation()),
        ("openclaw", "/v1/messages", "X-Anvil-Session-Id", "openclaw-session-1", _anthropic_tool_continuation()),
        ("pi", "/v1/chat/completions", "X-Session-Affinity", "pi-session-1", _openai_tool_continuation()),
        ("pi", "/v1/responses", "X-Session-Affinity", "pi-responses-1", _responses_tool_continuation()),
    ),
)
def test_harness_protocols_retain_session_history_and_tool_continuations(
    tmp_path, harness, path, header, session_id, body,
):
    backend = _Backend()
    log = DecisionLog(sink=DecisionLogWriter(str(tmp_path / "decisions.jsonl")))
    routing = RoutingBackend(load(_CONFIG), {"primary-local": backend}, decision_log=log)
    with server_context(routing, token=_TOKEN) as (host, port):
        status, response = _post(
            host, port, path, body, header=header, session_id=session_id,
        )
        history_status, _, history_body = http_get(
            host, port, f"/v1/requests?session_id={session_id}&history=1", token=_TOKEN,
        )

    assert harness in {"hermes", "openclaw", "pi"}
    assert status == 200
    assert b"error" not in response
    assert backend.requests[0].raw["_anvil_correlation"]["session_id"] == session_id
    if path == "/v1/messages":
        # Anthropic tool-result blocks are deliberately retained in the raw
        # request because InternalRequest only flattens text blocks.
        assert backend.requests[0].messages[-1].role == "user"
        assert backend.requests[0].raw["messages"][-1]["content"][0]["type"] == "tool_result"
        assert backend.requests[0].raw["messages"][-1]["content"][0]["content"] == "result"
    else:
        assert any(
            message.role == "tool" and message.content == "result"
            for message in backend.requests[0].messages
        )
    history = json.loads(history_body)
    assert history_status == 200
    assert history["object"] == "router_request_history"
    assert history["session_id"] == session_id
    assert len(history["records"]) == 1
