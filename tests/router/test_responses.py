"""Contract tests for the stateless OpenAI Responses compatibility surface."""
from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager

import pytest

from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.dialects.responses import ResponsesDialect
from anvil_serving.router.internal import DialectError
from anvil_serving.router.front_door import make_server
from anvil_serving.router.internal import StructuredResult


@contextmanager
def running_server(backend):
    server = make_server("127.0.0.1", 0, backend)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[:2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def post(host, port, body, headers=None):
    connection = http.client.HTTPConnection(host, port, timeout=10)
    try:
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        connection.request("POST", "/v1/responses", json.dumps(body), request_headers)
        response = connection.getresponse()
        return response.status, {key.lower(): value for key, value in response.getheaders()}, response.read()
    finally:
        connection.close()


def parse_named_events(raw: bytes):
    events = []
    for block in raw.decode("utf-8").strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_responses_nonstreaming_and_function_result_continuation():
    class CaptureBackend:
        def __init__(self):
            self.request = None

        def generate(self, request):
            self.request = request
            yield "done"

    backend = CaptureBackend()
    body = {
        "model": "chat",
        "instructions": "be precise",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "find file"}]},
            {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{\"path\":\"x.py\"}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "contents"},
        ],
        "tools": [{"type": "function", "name": "read_file", "parameters": {"type": "object"}}],
        "text": {"format": {"type": "json_schema", "name": "result", "schema": {"type": "object"}}},
    }
    with running_server(backend) as (host, port):
        status, _headers, raw = post(host, port, body)
    response = json.loads(raw)
    assert status == 200
    assert response["object"] == "response"
    assert response["status"] == "completed"
    assert response["output"][0]["content"][0]["text"] == "done"
    assert backend.request.dialect == "openai"  # relay stays on the existing path
    assert backend.request.raw["tools"][0]["function"]["name"] == "read_file"
    assert backend.request.raw["messages"][-1] == {"role": "tool", "tool_call_id": "call_1", "content": "contents"}
    assert backend.request.raw["response_format"]["type"] == "json_schema"


def test_responses_stream_event_order_and_cancellation_close():
    with running_server(StaticBackend(["Hel", "lo"])) as (host, port):
        status, headers, raw = post(host, port, {"model": "chat", "input": "hello", "stream": True})
    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    events = parse_named_events(raw)
    names = [name for name, _ in events]
    assert names[0:2] == ["response.created", "response.in_progress"]
    assert names.index("response.output_item.added") < names.index("response.output_text.delta")
    assert names[-1] == "response.completed"
    assert "".join(data.get("delta", "") for name, data in events if name == "response.output_text.delta") == "Hello"
    done = next(data for name, data in events if name == "response.output_text.done")
    assert done["text"] == "Hello"
    part_done = next(data for name, data in events if name == "response.content_part.done")
    assert part_done["part"]["text"] == "Hello"
    item_done = next(data for name, data in events if name == "response.output_item.done")
    assert item_done["item"]["content"][0]["text"] == "Hello"
    completed = events[-1][1]["response"]
    assert completed["output"][0]["content"][0]["text"] == "Hello"

    stream = ResponsesDialect().stream(ResponsesDialect().parse_request({"model": "chat", "input": "x", "stream": True}), iter(["x"]))
    next(stream)
    stream.close()  # caller cancellation must be a clean generator close


def test_responses_structured_tool_call_and_unsupported_features_fail_explicitly():
    class ToolBackend:
        def generate(self, request):
            return iter(())

        def get_last_structured(self):
            return StructuredResult(finish_reason="tool_calls", tool_calls=[{"id": "call_1", "name": "list_files", "arguments": {"path": "."}}])

    with running_server(ToolBackend()) as (host, port):
        status, _headers, raw = post(host, port, {"model": "chat", "input": "list", "tools": [{"type": "function", "name": "list_files"}]})
        unsupported_status, _unsupported_headers, unsupported_raw = post(host, port, {"model": "chat", "input": "list", "previous_response_id": "resp_old"})
    output = json.loads(raw)["output"]
    assert status == 200
    assert output[0]["type"] == "function_call"
    assert output[0]["call_id"] == "call_1"
    error = json.loads(unsupported_raw)["error"]
    assert unsupported_status == 400
    assert error["type"] == "unsupported_feature"
    assert "previous_response_id" in error["message"]


def test_responses_identifies_a_rejected_hosted_tool_kind_without_logging_payload():
    with pytest.raises(DialectError, match="web_search_preview"):
        ResponsesDialect().parse_request({
            "model": "chat",
            "input": "list",
            "tools": [{"type": "web_search_preview", "user_location": {"city": "secret-city"}}],
        })


def test_responses_suppresses_only_codex_internal_collaboration_namespace():
    request = ResponsesDialect().parse_request({
        "model": "chat",
        "input": "hi",
        "tools": [
            {"type": "namespace", "name": "collaboration", "description": "internal"},
            {"type": "function", "name": "list_files", "parameters": {"type": "object"}},
        ],
    })

    assert request.raw["tools"] == [{"type": "function", "function": {"name": "list_files", "parameters": {"type": "object"}}}]
    with pytest.raises(DialectError, match="namespace"):
        ResponsesDialect().parse_request({
            "model": "chat",
            "input": "hi",
            "tools": [{"type": "namespace", "name": "unreviewed_mcp"}],
        })


def test_responses_accepts_only_the_explicitly_stateless_store_flag():
    request = ResponsesDialect().parse_request({"model": "chat", "input": "hi", "store": False})

    assert request.model == "chat"
    with pytest.raises(DialectError, match="store: false"):
        ResponsesDialect().parse_request({"model": "chat", "input": "hi", "store": True})


def test_responses_accepts_only_the_legacy_stateless_reasoning_include():
    request = ResponsesDialect().parse_request({
        "model": "chat",
        "input": "hi",
        "include": ["reasoning.encrypted_content"],
    })

    assert request.model == "chat"
    with pytest.raises(DialectError, match="include"):
        ResponsesDialect().parse_request({"model": "chat", "input": "hi", "include": ["message.output_text.logprobs"]})


def test_responses_accepts_bounded_stateless_codex_controls_without_policy_override():
    request = ResponsesDialect().parse_request({
        "model": "chat",
        "input": "hi",
        "reasoning": {"effort": "high", "summary": "auto"},
        "parallel_tool_calls": False,
        "truncation": "disabled",
    })

    assert "reasoning_effort" not in request.raw
    with pytest.raises(DialectError, match="parallel_tool_calls"):
        ResponsesDialect().parse_request({"model": "chat", "input": "hi", "parallel_tool_calls": True})
    with pytest.raises(DialectError, match="truncation"):
        ResponsesDialect().parse_request({"model": "chat", "input": "hi", "truncation": "auto"})


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"model": "chat", "input": "hi", "user": "ignored-before"}, "user"),
        ({"model": {"unexpected": "object"}, "input": "hi"}, "model"),
        ({"model": "chat", "input": "hi", "stream": "false"}, "stream"),
        (
            {
                "model": "chat",
                "input": [{"role": "user", "content": [{"type": "input_image", "image_url": "https://example.invalid/image.png"}]}],
            },
            "input_image",
        ),
        ({"model": "chat", "input": "hi", "tools": ["not-an-object"]}, "tools"),
    ],
)
def test_responses_rejects_unknown_or_lossy_request_shapes(body, message):
    """Unsupported inputs must fail rather than silently changing agent intent."""
    with pytest.raises(DialectError, match=message):
        ResponsesDialect().parse_request(body)


def test_responses_preserves_safe_correlation_in_the_internal_request_only():
    class CaptureBackend:
        def __init__(self):
            self.request = None

        def generate(self, request):
            self.request = request
            yield "ok"

    backend = CaptureBackend()
    with running_server(backend) as (host, port):
        status, _headers, _raw = post(host, port, {"model": "chat", "input": "hi"}, {
            "X-Anvil-Workbench-Run-Id": "run_7f2a",
            "X-Anvil-Task-Id": "task_48",
            "X-Request-Id": "req_91ce",
        })
    assert status == 200
    assert backend.request.raw["_anvil_correlation"] == {"workbench_run_id": "run_7f2a", "task_id": "task_48", "request_id": "req_91ce"}
