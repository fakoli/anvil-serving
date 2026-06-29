"""End-to-end tests for the T001 protocol-standard front door.

Hermetic: each test starts the REAL server on an ephemeral ``127.0.0.1`` port in
a background thread with a deterministic ``StaticBackend``, POSTs over
``http.client``, reads the raw response bytes, and asserts the SSE framing. No
network, no real LLM; the server is torn down in teardown.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
from contextlib import contextmanager
from typing import Dict, List, Tuple

import pytest

from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.front_door import make_server


# --------------------------------------------------------------------------- #
# server harness
# --------------------------------------------------------------------------- #
@contextmanager
def running_server(backend):
    """Start the front door on an ephemeral port; yield ``(host, port)``."""
    httpd = make_server("127.0.0.1", 0, backend)
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _post(host: str, port: int, path: str, body: dict) -> Tuple[int, Dict[str, str], bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        payload = json.dumps(body)
        conn.request("POST", path, payload, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        data = resp.read()
        return resp.status, headers, data
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# SSE parsers (consumer side)
# --------------------------------------------------------------------------- #
def parse_openai_sse(raw: bytes) -> List[str]:
    """Return the ordered list of ``data:`` payload strings (incl. ``[DONE]``)."""
    text = raw.decode("utf-8")
    payloads: List[str] = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        assert block.startswith("data: "), f"non-data OpenAI frame: {block!r}"
        payloads.append(block[len("data: "):])
    return payloads


def parse_anthropic_sse(raw: bytes) -> List[Tuple[str, dict]]:
    """Return ordered ``(event_type, data_obj)`` tuples."""
    text = raw.decode("utf-8")
    events: List[Tuple[str, dict]] = []
    for block in text.split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln]
        if not lines:
            continue
        etype = None
        data = None
        for ln in lines:
            if ln.startswith("event: "):
                etype = ln[len("event: "):]
            elif ln.startswith("data: "):
                data = json.loads(ln[len("data: "):])
        assert etype is not None, f"Anthropic frame missing event: {block!r}"
        assert data is not None, f"Anthropic frame missing data: {block!r}"
        events.append((etype, data))
    return events


# --------------------------------------------------------------------------- #
# OpenAI dialect
# --------------------------------------------------------------------------- #
def test_openai_streaming():
    tokens = ["Hel", "lo", ", ", "wor", "ld"]
    with running_server(StaticBackend(tokens)) as (host, port):
        status, headers, raw = _post(host, port, "/v1/chat/completions", {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })

    assert status == 200
    assert headers.get("content-type") == "text/event-stream"
    assert headers.get("cache-control") == "no-cache"

    payloads = parse_openai_sse(raw)
    assert payloads[-1] == "[DONE]", payloads

    chunks = [json.loads(p) for p in payloads[:-1]]
    # First chunk announces the assistant role with no content.
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert chunks[0]["object"] == "chat.completion.chunk"
    assert chunks[0]["model"] == "chat"

    # Final chunk: empty delta + finish_reason == "stop".
    assert chunks[-1]["choices"][0]["delta"] == {}
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"

    # Reassembled content equals the backend's tokens, in order.
    content = "".join(
        c["choices"][0]["delta"].get("content", "")
        for c in chunks
    )
    assert content == "Hello, world"

    # Each interior token chunk carries finish_reason null.
    for c in chunks[1:-1]:
        assert c["choices"][0]["finish_reason"] is None
        assert "content" in c["choices"][0]["delta"]


def test_openai_non_streaming():
    tokens = ["one ", "two ", "three"]
    with running_server(StaticBackend(tokens)) as (host, port):
        status, headers, raw = _post(host, port, "/v1/chat/completions", {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        })

    assert status == 200
    assert headers.get("content-type") == "application/json"
    obj = json.loads(raw)
    assert obj["object"] == "chat.completion"
    assert obj["model"] == "chat"
    choice = obj["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert choice["message"]["content"] == "one two three"
    assert choice["finish_reason"] == "stop"
    assert "usage" in obj


# --------------------------------------------------------------------------- #
# Anthropic dialect
# --------------------------------------------------------------------------- #
def test_anthropic_streaming():
    tokens = ["Hel", "lo", " there"]
    with running_server(StaticBackend(tokens)) as (host, port):
        status, headers, raw = _post(host, port, "/v1/messages", {
            "model": "claude",
            "system": "be terse",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })

    assert status == 200
    assert headers.get("content-type") == "text/event-stream"

    events = parse_anthropic_sse(raw)
    types = [t for t, _ in events]

    # Required events appear, each in the correct relative order.
    def idx(name: str) -> int:
        assert name in types, f"missing event {name}: {types}"
        return types.index(name)

    assert idx("message_start") < idx("content_block_start")
    # the optional keep-alive ping is emitted, after the block opens and before
    # the first text delta.
    assert idx("content_block_start") < idx("ping") < idx("content_block_delta")
    assert idx("content_block_delta") < idx("content_block_stop")
    assert idx("content_block_stop") < idx("message_delta")
    assert idx("message_delta") < idx("message_stop")
    assert types[0] == "message_start"
    assert types[-1] == "message_stop"

    # message_start carries an assistant message skeleton with usage.
    start = dict(events)["message_start"]["message"]
    assert start["role"] == "assistant"
    assert start["model"] == "claude"
    assert start["content"] == []
    assert start["usage"]["output_tokens"] == 0
    assert isinstance(start["usage"]["input_tokens"], int)

    # Reassembled text_delta text equals the backend's tokens, in order.
    text = "".join(
        data["delta"]["text"]
        for t, data in events
        if t == "content_block_delta"
    )
    assert text == "Hello there"

    # All deltas are text_delta on index 0.
    deltas = [data for t, data in events if t == "content_block_delta"]
    assert len(deltas) == len(tokens)
    for d in deltas:
        assert d["index"] == 0
        assert d["delta"]["type"] == "text_delta"

    # message_delta reports a stop reason and an output-token count.
    msg_delta = dict(events)["message_delta"]
    assert msg_delta["delta"]["stop_reason"] == "end_turn"
    assert msg_delta["usage"]["output_tokens"] == len(tokens)


def test_anthropic_non_streaming():
    tokens = ["a", "b", "c"]
    with running_server(StaticBackend(tokens)) as (host, port):
        status, headers, raw = _post(host, port, "/v1/messages", {
            "model": "claude",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        })

    assert status == 200
    assert headers.get("content-type") == "application/json"
    obj = json.loads(raw)
    assert obj["type"] == "message"
    assert obj["role"] == "assistant"
    assert obj["model"] == "claude"
    assert obj["content"] == [{"type": "text", "text": "abc"}]
    assert obj["stop_reason"] == "end_turn"
    assert "input_tokens" in obj["usage"]


# --------------------------------------------------------------------------- #
# routing / errors
# --------------------------------------------------------------------------- #
def test_unknown_route_404():
    with running_server(StaticBackend(["x"])) as (host, port):
        status, _headers, raw = _post(host, port, "/v1/nope", {"messages": []})
    assert status == 404
    assert json.loads(raw)["error"]["type"] == "not_found"


def test_bad_json_400():
    with running_server(StaticBackend(["x"])) as (host, port):
        conn = http.client.HTTPConnection(host, port, timeout=10)
        try:
            conn.request("POST", "/v1/chat/completions", b"{not json",
                         {"Content-Type": "application/json"})
            resp = conn.getresponse()
            status = resp.status
            raw = resp.read()
        finally:
            conn.close()
    assert status == 400
    assert json.loads(raw)["error"]["type"] == "invalid_request"


def _raw_roundtrip(host: str, port: int, request_bytes: bytes,
                   timeout: float = 3.0) -> Tuple[bytes, bool]:
    """Send raw HTTP bytes; read the response until the server closes the socket.

    Returns ``(raw_response, hit_eof)``. ``hit_eof`` is False when the read times
    out before EOF — exactly the regression signature of a server that fails to
    honor ``Connection: close`` (it leaves the socket open). The short timeout
    makes such a regression fail fast instead of hanging the suite.
    """
    s = socket.create_connection((host, port), timeout=timeout)
    s.settimeout(timeout)
    chunks: List[bytes] = []
    hit_eof = False
    try:
        s.sendall(request_bytes)
        while True:
            try:
                data = s.recv(4096)
            except socket.timeout:
                break
            if not data:
                hit_eof = True
                break
            chunks.append(data)
    finally:
        s.close()
    return b"".join(chunks), hit_eof


def test_streaming_honors_connection_close():
    """A `Connection: close` client must get `Connection: close` back AND the
    server must actually close the socket (read reaches EOF) — not force
    keep-alive and leave a read-to-EOF client hanging (RFC 7230 6.1)."""
    body = json.dumps({
        "model": "chat",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }).encode("utf-8")
    with running_server(StaticBackend(["a", "b", "c"])) as (host, port):
        req = (
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            + f"Host: {host}:{port}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + b"Connection: close\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"\r\n" + body
        )
        raw, hit_eof = _raw_roundtrip(host, port, req, timeout=3.0)

    head = raw.split(b"\r\n\r\n", 1)[0].decode("latin-1").lower()
    assert "connection: close" in head, head
    assert "connection: keep-alive" not in head, head
    assert hit_eof, "server did not close the socket for a Connection: close client"
    assert b"[DONE]" in raw  # the full stream was still delivered


def test_anthropic_missing_max_tokens_400():
    """The Anthropic Messages API requires max_tokens; omitting it is a 400."""
    with running_server(StaticBackend(["x"])) as (host, port):
        status, _headers, raw = _post(host, port, "/v1/messages", {
            "model": "claude",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
    assert status == 400
    err = json.loads(raw)["error"]
    assert err["type"] == "invalid_request_error"
    assert "max_tokens" in err["message"]


def test_openai_max_tokens_optional():
    """OpenAI keeps max_tokens optional — omitting it still streams a 200."""
    with running_server(StaticBackend(["hi"])) as (host, port):
        status, headers, raw = _post(host, port, "/v1/chat/completions", {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
    assert status == 200
    assert headers.get("content-type") == "text/event-stream"
    assert parse_openai_sse(raw)[-1] == "[DONE]"


def test_malformed_content_length_400():
    """A non-numeric Content-Length is a clean 400, not a 500/dropped request."""
    with running_server(StaticBackend(["x"])) as (host, port):
        body = b'{"model":"chat","messages":[]}'
        req = (
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            + f"Host: {host}:{port}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + b"Content-Length: notanumber\r\n"
            + b"Connection: close\r\n"
            + b"\r\n" + body
        )
        raw, _eof = _raw_roundtrip(host, port, req, timeout=3.0)
    status_line = raw.split(b"\r\n", 1)[0].decode("latin-1")
    assert " 400 " in status_line, status_line


def test_chunked_request_body_rejected():
    """A chunked request body (no Content-Length) is rejected, not read as {}."""
    with running_server(StaticBackend(["x"])) as (host, port):
        req = (
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            + f"Host: {host}:{port}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + b"Transfer-Encoding: chunked\r\n"
            + b"Connection: close\r\n"
            + b"\r\n"
            + b"0\r\n\r\n"
        )
        raw, _eof = _raw_roundtrip(host, port, req, timeout=3.0)
    status_line = raw.split(b"\r\n", 1)[0].decode("latin-1")
    assert " 411 " in status_line, status_line


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
