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
from pathlib import Path as _Path
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
            except (ConnectionResetError, ConnectionAbortedError):
                # Windows sends an RST when the server closes with unread bytes
                # still in its receive buffer; that still means "closed" -> EOF.
                hit_eof = True
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
    """The Anthropic Messages API requires max_tokens; omitting it is a 400 in
    Anthropic's NATIVE error envelope (`{"type":"error","error":{...}}`)."""
    with running_server(StaticBackend(["x"])) as (host, port):
        status, _headers, raw = _post(host, port, "/v1/messages", {
            "model": "claude",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
    assert status == 400
    body = json.loads(raw)
    assert body["type"] == "error"               # native Anthropic shape
    assert body["error"]["type"] == "invalid_request_error"
    assert "max_tokens" in body["error"]["message"]


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


def test_dialect_error_envelopes_are_native():
    """OpenAI errors are `{"error":{...}}`; Anthropic errors are top-level
    `{"type":"error","error":{...}}`. Locks the per-dialect shapes directly."""
    from anvil_serving.router.dialects.anthropic import AnthropicDialect
    from anvil_serving.router.dialects.openai import OpenAIDialect

    assert OpenAIDialect().render_error(400, "invalid_request_error", "x") == {
        "error": {"type": "invalid_request_error", "message": "x"},
    }
    assert AnthropicDialect().render_error(400, "invalid_request_error", "x") == {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "x"},
    }


# Each value is an HTTP/1.1 request that triggers a pre-body early-return error
# path on a POOLED keep-alive socket (no `Connection: close`). These bodies are
# UNDRAINABLE (chunked / unparseable length) -> the server cannot realign the
# stream, so it MUST close (RFC 7230 3.3.3/6.6).
_UNDRAINABLE_ERROR_REQUESTS = {
    "chunked_body": (              # 411 — chunked upload we don't decode
        b"POST /v1/chat/completions HTTP/1.1\r\n"
        b"Host: x\r\n"
        b"Content-Type: application/json\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"\r\n"
        b"5\r\nhello\r\n0\r\n\r\n"
    ),
    "bad_content_length": (        # 400 — can't know how many body bytes follow
        b"POST /v1/chat/completions HTTP/1.1\r\n"
        b"Host: x\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: notanumber\r\n"
        b"\r\n"
        b'{"x":"y"}'
    ),
}


def _pipelined_follow() -> bytes:
    """A valid streaming POST to pipeline behind a bad request on one socket."""
    follow_body = json.dumps({
        "model": "chat",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }).encode("utf-8")
    return (
        b"POST /v1/chat/completions HTTP/1.1\r\n"
        b"Host: x\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(follow_body)}\r\n".encode("ascii")
        + b"\r\n" + follow_body
    )


@pytest.mark.parametrize("name", sorted(_UNDRAINABLE_ERROR_REQUESTS))
def test_undrainable_framing_error_closes_socket(name):
    """An undrainable framing error must close the keep-alive socket — otherwise
    the undecodable body desyncs the stream and a pipelined follow-up is
    mis-parsed (RFC 7230 3.3.3/6.6)."""
    bad = _UNDRAINABLE_ERROR_REQUESTS[name]
    with running_server(StaticBackend(["a", "b"])) as (host, port):
        raw, hit_eof = _raw_roundtrip(host, port, bad + _pipelined_follow(), timeout=3.0)

    head = raw.split(b"\r\n\r\n", 1)[0].decode("latin-1").lower()
    assert "connection: close" in head, head
    assert hit_eof, "server left the keep-alive socket open after a framing error"


def test_unknown_route_drains_body_and_keeps_socket_in_sync():
    """A 404 on a WELL-FRAMED request (valid Content-Length) drains the body and
    keeps the keep-alive socket in sync, so a pipelined follow-up POST is still
    served correctly — no desync, no forced close (which would RST-truncate on
    Windows)."""
    bad = (
        b"POST /v1/nope HTTP/1.1\r\n"
        b"Host: x\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 12\r\n"
        b"\r\n"
        b'{"x":"yyyy"}'
    )
    with running_server(StaticBackend(["a", "b"])) as (host, port):
        raw, _eof = _raw_roundtrip(host, port, bad + _pipelined_follow(), timeout=3.0)

    # Two responses came back on ONE socket: the 404, then the streamed 200.
    assert b" 404 " in raw, raw[:80]
    assert b"text/event-stream" in raw, "follow-up POST was not served -> desync"
    assert b"data: [DONE]" in raw


def test_http10_streaming_is_close_delimited():
    """An HTTP/1.0 streaming client (no chunked support) must get raw,
    close-delimited SSE frames — NO hex chunk-size framing leaking into body."""
    body = json.dumps({
        "model": "chat",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }).encode("utf-8")
    with running_server(StaticBackend(["a", "b", "c"])) as (host, port):
        req = (
            b"POST /v1/chat/completions HTTP/1.0\r\n"
            + f"Host: {host}:{port}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"\r\n" + body
        )
        raw, hit_eof = _raw_roundtrip(host, port, req, timeout=3.0)

    head, _, sse = raw.partition(b"\r\n\r\n")
    headl = head.lower()
    assert b"transfer-encoding" not in headl, head
    assert b"connection: close" in headl, head
    # Close-delimited body starts directly with an SSE frame, not a hex size.
    assert sse.startswith(b"data: "), sse[:48]
    assert b"data: [DONE]" in sse
    assert hit_eof

    payloads = [
        ln[len(b"data: "):]
        for ln in sse.split(b"\n\n")
        if ln.startswith(b"data: ") and not ln.startswith(b"data: [DONE]")
    ]
    content = "".join(
        json.loads(p)["choices"][0]["delta"].get("content", "")
        for p in payloads
    )
    assert content == "abc"


def test_handler_has_finite_idle_timeout():
    """The handler must carry a finite idle read timeout so abandoned keep-alive
    connections can't pin daemon threads forever. Tunable via make_server."""
    server = make_server("127.0.0.1", 0, StaticBackend(["x"]))
    try:
        assert server.RequestHandlerClass.timeout == 120
    finally:
        server.server_close()
    server2 = make_server("127.0.0.1", 0, StaticBackend(["x"]), timeout=5)
    try:
        assert server2.RequestHandlerClass.timeout == 5
    finally:
        server2.server_close()


# --------------------------------------------------------------------------- #
# Harden: resource caps (DoS)
# --------------------------------------------------------------------------- #

def test_oversized_content_length_413():
    """A Content-Length > MAX_BODY_BYTES is rejected with 413 without reading the
    body — no huge allocation, connection closed (body is too large to drain)."""
    import anvil_serving.router.front_door as fd_mod

    huge_n = fd_mod.MAX_BODY_BYTES + 1
    with running_server(StaticBackend(["x"])) as (host, port):
        req = (
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            + f"Host: {host}:{port}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {huge_n}\r\n".encode("ascii")
            + b"Connection: close\r\n"
            + b"\r\n"
            + b"{}"  # tiny actual body — server must NOT read huge_n bytes
        )
        raw, hit_eof = _raw_roundtrip(host, port, req, timeout=5.0)

    status_line = raw.split(b"\r\n", 1)[0].decode("latin-1")
    assert " 413 " in status_line, status_line
    assert hit_eof, "server must close the connection (too large to drain)"


def test_concurrency_cap_503(monkeypatch):
    """When the concurrency semaphore is exhausted, the next request gets 503."""
    import threading as th
    import anvil_serving.router.front_door as fd_mod

    exhausted = th.BoundedSemaphore(1)
    exhausted.acquire()  # drain the only slot
    monkeypatch.setattr(fd_mod, "_CONCURRENCY_LIMIT", exhausted)

    with running_server(StaticBackend(["x"])) as (host, port):
        status, _, raw = _post(host, port, "/v1/chat/completions", {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        })

    assert status == 503
    body = json.loads(raw)
    assert body["error"]["type"] == "server_busy"


# --------------------------------------------------------------------------- #
# Harden: request smuggling / strict framing
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("bad_cl", ["1_000", "+5", " 5 "])
def test_non_digit_content_length_400(bad_cl):
    """Non-ASCII-digit Content-Length values must be rejected with 400.
    Python's int() is too permissive (accepts underscores, signs, unicode);
    we require a strict decimal digit string."""
    with running_server(StaticBackend(["x"])) as (host, port):
        req = (
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            + f"Host: {host}:{port}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {bad_cl}\r\n".encode("ascii")
            + b"Connection: close\r\n"
            + b"\r\n"
            + b'{"model":"chat","messages":[]}'
        )
        raw, _ = _raw_roundtrip(host, port, req, timeout=3.0)

    status_line = raw.split(b"\r\n", 1)[0].decode("latin-1")
    assert " 400 " in status_line, f"expected 400 for CL={bad_cl!r}: {status_line}"


def test_duplicate_content_length_400():
    """Multiple Content-Length headers must be rejected with 400 (request
    smuggling prevention, RFC 7230 3.3.2)."""
    body = b'{"model":"chat","messages":[]}'
    with running_server(StaticBackend(["x"])) as (host, port):
        req = (
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            + f"Host: {host}:{port}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + f"Content-Length: {len(body)}\r\n".encode("ascii")  # duplicate
            + b"Connection: close\r\n"
            + b"\r\n" + body
        )
        raw, _ = _raw_roundtrip(host, port, req, timeout=3.0)

    status_line = raw.split(b"\r\n", 1)[0].decode("latin-1")
    assert " 400 " in status_line, status_line


def test_transfer_encoding_header_rejected_411():
    """Any Transfer-Encoding header — not just 'chunked' — must be rejected
    with 411. Catches obfuscated/second TE headers used in smuggling."""
    with running_server(StaticBackend(["x"])) as (host, port):
        req = (
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            + f"Host: {host}:{port}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + b"Transfer-Encoding: identity\r\n"  # non-chunked TE still rejected
            + b"Connection: close\r\n"
            + b"\r\n"
        )
        raw, _ = _raw_roundtrip(host, port, req, timeout=3.0)

    status_line = raw.split(b"\r\n", 1)[0].decode("latin-1")
    assert " 411 " in status_line, status_line


def test_get_with_body_drains_and_keeps_socket_in_sync():
    """A GET request with a Content-Length body must drain it and keep the
    keep-alive socket in sync so a pipelined follow-up POST is served
    correctly — no desync, no forced close."""
    get_body = b'{"surprise":"body"}'
    get_req = (
        b"GET /healthz HTTP/1.1\r\n"
        b"Host: x\r\n"
        + f"Content-Length: {len(get_body)}\r\n".encode("ascii")
        + b"\r\n" + get_body
    )
    with running_server(StaticBackend(["a", "b"])) as (host, port):
        raw, _ = _raw_roundtrip(host, port, get_req + _pipelined_follow(), timeout=3.0)

    # Both responses came back on ONE socket: healthz 200, then streaming 200.
    assert b'"status"' in raw, "healthz response missing"
    assert b"text/event-stream" in raw, "follow-up POST was not served -> desync"
    assert b"data: [DONE]" in raw


# --------------------------------------------------------------------------- #
# Harden: information leakage
# --------------------------------------------------------------------------- #

def test_503_no_tier_leaks_no_internal_names():
    """503 from NoAvailableTierError must not expose internal tier names,
    work-class identifiers, or remediation details to the client."""
    from anvil_serving.router.internal import NoAvailableTierError

    class TierFailBackend:
        def generate(self, request):
            raise NoAvailableTierError(
                "secret_work_class", ["super-secret-tier-1", "confidential-tier-2"]
            )

    with running_server(TierFailBackend()) as (host, port):
        status, _, raw = _post(host, port, "/v1/chat/completions", {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        })

    assert status == 503
    body_text = raw.decode("utf-8")
    # Internal tier names and work-class must NOT appear in the response.
    assert "super-secret-tier-1" not in body_text
    assert "confidential-tier-2" not in body_text
    assert "secret_work_class" not in body_text
    # Must still be a valid 503 with an error body.
    assert json.loads(raw)["error"]["type"] == "service_unavailable"


def test_500_backend_error_leaks_no_exception_text():
    """500 from an unexpected backend exception must not expose the exception
    message or traceback to the client."""

    class BoomBackend:
        def generate(self, request):
            raise RuntimeError("supersecret-internal-error-XYZ-9876")

    with running_server(BoomBackend()) as (host, port):
        status, _, raw = _post(host, port, "/v1/chat/completions", {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        })

    assert status == 500
    body_text = raw.decode("utf-8")
    assert "supersecret-internal-error-XYZ-9876" not in body_text
    assert json.loads(raw)["error"]["type"] == "internal_error"


def test_server_header_is_generic():
    """The Server: response header must not disclose software name or version."""
    with running_server(StaticBackend(["x"])) as (host, port):
        _, headers, _ = _post(host, port, "/v1/chat/completions", {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        })

    server_hdr = headers.get("server", "")
    assert "front-door" not in server_hdr.lower(), server_hdr
    assert "0.1" not in server_hdr, server_hdr


# --------------------------------------------------------------------------- #
# Harden: dialect-aware errors + backend-crash → clean 500
# --------------------------------------------------------------------------- #

def test_concurrency_cap_503_anthropic_dialect(monkeypatch):
    """A pre-acquire concurrency 503 on /v1/messages must use the native
    Anthropic error envelope {type:error, error:{...}}, not the generic shape."""
    import threading as th
    import anvil_serving.router.front_door as fd_mod

    exhausted = th.BoundedSemaphore(1)
    exhausted.acquire()  # drain the only slot
    monkeypatch.setattr(fd_mod, "_CONCURRENCY_LIMIT", exhausted)

    with running_server(StaticBackend(["x"])) as (host, port):
        status, _, raw = _post(host, port, "/v1/messages", {
            "model": "claude",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        })

    assert status == 503
    body = json.loads(raw)
    # Native Anthropic envelope: top-level "type":"error", not {"error": {...}}
    assert body.get("type") == "error", f"expected Anthropic envelope, got: {body}"
    assert body["error"]["type"] == "server_busy"


def test_anthropic_framing_error_uses_native_envelope():
    """A framing error (411 Transfer-Encoding) on /v1/messages must use the
    native Anthropic error envelope {type:error, error:{...}}, not the generic
    OpenAI-shaped one."""
    with running_server(StaticBackend(["x"])) as (host, port):
        req = (
            b"POST /v1/messages HTTP/1.1\r\n"
            + f"Host: {host}:{port}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + b"Transfer-Encoding: chunked\r\n"
            + b"Connection: close\r\n"
            + b"\r\n"
            + b"0\r\n\r\n"
        )
        raw, _ = _raw_roundtrip(host, port, req, timeout=3.0)

    status_line = raw.split(b"\r\n", 1)[0].decode("latin-1")
    assert " 411 " in status_line, status_line
    _, _, body_bytes = raw.partition(b"\r\n\r\n")
    body = json.loads(body_bytes)
    assert body.get("type") == "error", f"expected native Anthropic envelope: {body}"
    assert "error" in body


def test_anthropic_bad_json_uses_native_envelope():
    """A bad-JSON body on /v1/messages must return the native Anthropic error
    envelope {type:error, error:{...}}, not the generic shape."""
    with running_server(StaticBackend(["x"])) as (host, port):
        conn = http.client.HTTPConnection(host, port, timeout=10)
        try:
            conn.request("POST", "/v1/messages", b"{not valid json}",
                         {"Content-Type": "application/json"})
            resp = conn.getresponse()
            status = resp.status
            raw = resp.read()
        finally:
            conn.close()

    assert status == 400
    body = json.loads(raw)
    assert body.get("type") == "error", f"expected native Anthropic envelope: {body}"
    assert body["error"]["type"] == "invalid_request"


def test_streaming_backend_eager_exception_gives_clean_500():
    """A backend whose generate() raises (before yielding any deltas) must
    produce a clean 500 — NOT a bare TCP close — and the exception text must
    not appear in the response body."""

    class EagerFailBackend:
        def generate(self, request):
            raise RuntimeError("top-secret-generate-failure-ABC")

    with running_server(EagerFailBackend()) as (host, port):
        status, _, raw = _post(host, port, "/v1/chat/completions", {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })

    assert status == 500
    body_text = raw.decode("utf-8")
    assert "top-secret-generate-failure-ABC" not in body_text
    assert json.loads(raw)["error"]["type"] == "internal_error"


# --------------------------------------------------------------------------- #
# POST /v1/route — routing-brain decision endpoint (advise-and-defer:T007)
# --------------------------------------------------------------------------- #

_CONFIGS_DIR = _Path(__file__).resolve().parents[2] / "configs"
_CONFIG_LOCAL_ONLY = str(_CONFIGS_DIR / "example.toml")
_CONFIG_WITH_CLOUD = str(_CONFIGS_DIR / "example-with-cloud.toml")


class _NeverCallBackend:
    """Tier-backend stub: generate() raises if accidentally called.

    Injected into RoutingBackend._backends for /v1/route tests to assert
    that the decision endpoint never calls any tier backend.
    """

    def generate(self, request):
        raise AssertionError(
            "POST /v1/route must NOT call backend.generate() — "
            "it is a decision endpoint, not a serve path"
        )


def _make_routing_backend(config_path, tier_ids, profile=None):
    """Build a RoutingBackend with NeverCallBackend stubs for the given tier ids."""
    from anvil_serving.router.config import load as _load
    from anvil_serving.router.profile_store import default_profile
    from anvil_serving.router.serve import RoutingBackend

    config = _load(config_path)
    backends = {tid: _NeverCallBackend() for tid in tier_ids}
    return RoutingBackend(config, backends, profile or default_profile())


@pytest.fixture
def route_local_server():
    """Server backed by a RoutingBackend using the local-only example config."""
    routing = _make_routing_backend(
        _CONFIG_LOCAL_ONLY,
        tier_ids=["fast-local", "heavy-local"],
    )
    httpd = make_server("127.0.0.1", 0, routing)
    host, port = httpd.server_address[:2]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield host, port
    httpd.shutdown()
    httpd.server_close()
    t.join(timeout=5)


@pytest.fixture
def route_cloud_server():
    """Server backed by a RoutingBackend using the cloud-opt-in config.

    Injects a NeverCallBackend for the cloud tier so the decision endpoint
    can select it without making any real network calls.
    """
    routing = _make_routing_backend(
        _CONFIG_WITH_CLOUD,
        tier_ids=["fast-local", "heavy-local", "cloud"],
    )
    httpd = make_server("127.0.0.1", 0, routing)
    host, port = httpd.server_address[:2]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield host, port
    httpd.shutdown()
    httpd.server_close()
    t.join(timeout=5)


def _assert_route_response(result: dict) -> None:
    """Assert all required T007 contract fields are present and well-formed."""
    assert "tier" in result, f"missing 'tier' in {result}"
    assert result["tier"] in ("local", "cloud"), f"invalid tier {result['tier']!r}"
    assert "model" in result, f"missing 'model' in {result}"
    assert isinstance(result["model"], str) and result["model"], "model must be non-empty str"
    assert "provider" in result, f"missing 'provider' in {result}"
    assert isinstance(result["provider"], str), "provider must be str"
    assert "work_class" in result, f"missing 'work_class' in {result}"
    assert "reason" in result, f"missing 'reason' in {result}"
    assert isinstance(result["reason"], str), "reason must be str"
    assert "confidence" in result, f"missing 'confidence' in {result}"
    assert isinstance(result["confidence"], float), "confidence must be float"
    assert 0.0 <= result["confidence"] <= 1.0, f"confidence out of range: {result['confidence']}"
    assert "session_id" in result, f"missing 'session_id' in {result}"
    assert result["session_id"].startswith("rte_"), (
        f"session_id must start with 'rte_', got {result['session_id']!r}"
    )


def test_route_allow_preset_returns_local(route_local_server):
    """POST /v1/route for 'chat' (allow preset) returns a well-formed local decision.

    chat -> fast-local (allow by default profile); NeverCallBackend stubs
    confirm generate() is never triggered.
    """
    host, port = route_local_server
    status, _, raw = _post(host, port, "/v1/route", {
        "model": "chat",
        "messages": [{"role": "user", "content": "hello"}],
    })

    assert status == 200
    result = json.loads(raw)
    _assert_route_response(result)
    assert result["tier"] == "local"
    assert result["work_class"] == "chat"
    assert result["confidence"] == 1.0, "declared-preset must have confidence 1.0"
    assert "preset='chat'" in result["reason"], result["reason"]


def test_route_allow_with_verify_returns_local(route_local_server):
    """POST /v1/route for a review class (allow-with-verify profile override)
    returns a local decision; generate() is never called.

    Uses a custom profile that marks heavy-local as allow-with-verify for
    review (the default marks it allow; this confirms the endpoint works for
    the allow-with-verify tier class without any backend call).
    """
    from anvil_serving.router.profile_store import ProfileEntry, ProfileStore

    avw_profile = ProfileStore({
        ("heavy-local", "review"): ProfileEntry("allow-with-verify", 0.7, 10, None)
    })
    routing = _make_routing_backend(
        _CONFIG_LOCAL_ONLY,
        tier_ids=["fast-local", "heavy-local"],
        profile=avw_profile,
    )
    httpd = make_server("127.0.0.1", 0, routing)
    host, port = httpd.server_address[:2]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        status, _, raw = _post(host, port, "/v1/route", {
            "model": "review",
            "messages": [{"role": "user", "content": "review this diff"}],
        })
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)

    assert status == 200
    result = json.loads(raw)
    _assert_route_response(result)
    assert result["tier"] == "local"
    # review -> heavy-local (the only candidate in example.toml for review)
    assert result["provider"] == "heavy-local"
    assert result["confidence"] == 1.0, "declared-preset must have confidence 1.0"


def test_route_deny_planning_local_only_returns_503(route_local_server):
    """POST /v1/route for 'planning' with a local-only config returns 503.

    planning -> heavy-local in example.toml; heavy-local is 'deny' for
    planning in the default profile (eval-proven-weak local class). The
    quality gate drops it; no tiers remain; NoAvailableTierError → 503.
    """
    host, port = route_local_server
    status, _, raw = _post(host, port, "/v1/route", {
        "model": "planning",
        "messages": [{"role": "user", "content": "plan this project"}],
    })

    assert status == 503
    body = json.loads(raw)
    assert body["error"]["type"] == "service_unavailable"
    # Internal tier names must NOT leak to the client.
    body_text = raw.decode("utf-8")
    assert "heavy-local" not in body_text
    assert "fast-local" not in body_text


def test_route_metered_cloud_class_returns_cloud(route_cloud_server):
    """POST /v1/route for 'planning' with the cloud-opt-in config returns tier:'cloud'.

    example-with-cloud.toml maps planning to ["cloud"] with metered_cloud
    = ["planning"]; the default profile allows cloud for planning; the
    cloud tier's NeverCallBackend stub confirms generate() is never called.
    """
    host, port = route_cloud_server
    status, _, raw = _post(host, port, "/v1/route", {
        "model": "planning",
        "messages": [{"role": "user", "content": "plan this feature"}],
    })

    assert status == 200
    result = json.loads(raw)
    _assert_route_response(result)
    assert result["tier"] == "cloud"
    assert result["provider"] == "cloud"
    assert result["work_class"] == "planning"
    assert result["confidence"] == 1.0, "declared-preset must have confidence 1.0"


def test_route_signals_work_class_override(route_cloud_server):
    """POST /v1/route with signals.work_class overrides the model field.

    Passing signals={"work_class":"planning"} with an empty model should
    route identically to model="planning" (WORK_CLASS_TO_PRESET maps
    "planning" -> "planning" preset).
    """
    host, port = route_cloud_server
    # Body has no model but signals.work_class = "planning"
    status, _, raw = _post(host, port, "/v1/route", {
        "messages": [{"role": "user", "content": "plan this"}],
        "signals": {"work_class": "planning"},
    })

    assert status == 200
    result = json.loads(raw)
    _assert_route_response(result)
    assert result["tier"] == "cloud"
    assert result["work_class"] == "planning"


def test_route_never_calls_generate():
    """POST /v1/route must never invoke backend.generate().

    Uses a routing backend whose tier-stubs raise AssertionError on
    generate().  If the endpoint accidentally calls the serve path, the
    test thread will catch the error as a 500; it must be 200 instead.
    """
    routing = _make_routing_backend(
        _CONFIG_LOCAL_ONLY,
        tier_ids=["fast-local", "heavy-local"],
    )
    with running_server(routing) as (host, port):
        # Use the chat preset (allowed for local tier) — would stream if
        # the endpoint accidentally called generate().
        status, _, raw = _post(host, port, "/v1/route", {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
        })

    assert status == 200, f"expected 200, got {status}: {raw.decode()}"
    # The response must be a valid decision, not a 500 from generate().
    _assert_route_response(json.loads(raw))


def test_route_malformed_body_400(route_local_server):
    """POST /v1/route with a non-JSON body returns 400."""
    host, port = route_local_server
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.request("POST", "/v1/route", b"{not json}",
                     {"Content-Type": "application/json"})
        resp = conn.getresponse()
        status = resp.status
        raw = resp.read()
    finally:
        conn.close()

    assert status == 400
    assert json.loads(raw)["error"]["type"] == "invalid_request"


def test_route_no_routing_backend_503():
    """POST /v1/route with a plain StaticBackend (no .decide) returns 503.

    A static/echo backend has no routing brain; the endpoint must return
    503 'routing brain not available', not 500 or a traceback.
    """
    with running_server(StaticBackend(["hello"])) as (host, port):
        status, _, raw = _post(host, port, "/v1/route", {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
        })

    assert status == 503
    body = json.loads(raw)
    assert body["error"]["type"] == "service_unavailable"


def test_route_session_ids_are_unique(route_local_server):
    """POST /v1/route session_ids must differ across requests."""
    host, port = route_local_server
    ids = set()
    for _ in range(5):
        status, _, raw = _post(host, port, "/v1/route", {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert status == 200
        ids.add(json.loads(raw)["session_id"])
    assert len(ids) == 5, f"session_ids are not unique: {ids}"


# --------------------------------------------------------------------------- #
# Issue #53 — Front-door HTTP polish
# --------------------------------------------------------------------------- #

def test_get_on_post_only_route_returns_405():
    """GET to /v1/chat/completions, /v1/messages, or /v1/route → 405 Method Not
    Allowed with an Allow: POST header (not 404).  GET to a genuinely unknown
    path still 404s.  The dialect-native error envelope is used for known
    dialect routes (/v1/messages → Anthropic shape)."""
    with running_server(StaticBackend(["x"])) as (host, port):
        # --- OpenAI dialect route ---
        conn = http.client.HTTPConnection(host, port, timeout=10)
        try:
            conn.request("GET", "/v1/chat/completions")
            resp = conn.getresponse()
            assert resp.status == 405, (
                f"GET /v1/chat/completions: expected 405, got {resp.status}"
            )
            hdrs = {k.lower(): v for k, v in resp.getheaders()}
            assert hdrs.get("allow") == "POST", (
                f"Allow header wrong: {hdrs.get('allow')!r}"
            )
            body = json.loads(resp.read())
            # OpenAI error envelope: {"error": {...}}
            assert "error" in body and "type" in body["error"], body
        finally:
            conn.close()

        # --- Anthropic dialect route ---
        conn = http.client.HTTPConnection(host, port, timeout=10)
        try:
            conn.request("GET", "/v1/messages")
            resp = conn.getresponse()
            assert resp.status == 405, (
                f"GET /v1/messages: expected 405, got {resp.status}"
            )
            hdrs = {k.lower(): v for k, v in resp.getheaders()}
            assert hdrs.get("allow") == "POST", (
                f"Allow header wrong: {hdrs.get('allow')!r}"
            )
            body = json.loads(resp.read())
            # Anthropic native error envelope: {"type": "error", "error": {...}}
            assert body.get("type") == "error", (
                f"expected Anthropic error envelope on /v1/messages 405: {body}"
            )
        finally:
            conn.close()

        # --- Route-decision endpoint ---
        conn = http.client.HTTPConnection(host, port, timeout=10)
        try:
            conn.request("GET", "/v1/route")
            resp = conn.getresponse()
            assert resp.status == 405, (
                f"GET /v1/route: expected 405, got {resp.status}"
            )
            hdrs = {k.lower(): v for k, v in resp.getheaders()}
            assert hdrs.get("allow") == "POST", (
                f"Allow header wrong on /v1/route: {hdrs.get('allow')!r}"
            )
            resp.read()
        finally:
            conn.close()

        # --- Unknown path still 404 ---
        conn = http.client.HTTPConnection(host, port, timeout=10)
        try:
            conn.request("GET", "/v1/nope")
            resp = conn.getresponse()
            assert resp.status == 404, (
                f"GET /v1/nope: expected 404, got {resp.status}"
            )
            resp.read()
        finally:
            conn.close()


def test_413_bounded_drain_no_hang():
    """After a 413, the bounded drain does not hang waiting for the full oversized
    body — it takes only what is already in the OS receive buffer (non-blocking).

    Sends a small actual body (256 bytes, far less than MAX_BODY_BYTES) with a
    huge claimed Content-Length.  The server must send 413, do the bounded drain
    (gets the 256 bytes immediately), close, and let the client read the 413 —
    all within the test timeout.  If the drain were unbounded (reading huge_n
    bytes), this test would hang past the 5-second timeout.
    """
    import anvil_serving.router.front_door as fd_mod

    huge_n = fd_mod.MAX_BODY_BYTES + 1
    partial_body = b"x" * 256  # small — immediately available in OS recv buffer

    with running_server(StaticBackend(["x"])) as (host, port):
        req = (
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            + f"Host: {host}:{port}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {huge_n}\r\n".encode("ascii")
            + b"\r\n"
            + partial_body  # send 256 bytes; server must NOT block on huge_n
        )
        raw, hit_eof = _raw_roundtrip(host, port, req, timeout=5.0)

    status_line = raw.split(b"\r\n", 1)[0].decode("latin-1")
    assert " 413 " in status_line, f"expected 413: {status_line!r}"
    # Server must have closed (bounded drain did not block).
    assert hit_eof, "server must close after 413 (bounded drain completed)"


def test_get_with_oversized_body_closes_cleanly():
    """GET with a body that exceeds MAX_BODY_BYTES must close cleanly — the
    bounded post-response drain gives TCP time to deliver the 200 before RST.

    Also regression-tests that the drain is bounded: if it read the full
    oversized body, the server would block on the partial_body and not close
    within the 5-second timeout, causing hit_eof=False.
    """
    import anvil_serving.router.front_door as fd_mod

    huge_n = fd_mod.MAX_BODY_BYTES + 1
    partial_body = b"x" * 256  # small — immediately available in OS recv buffer

    with running_server(StaticBackend(["x"])) as (host, port):
        req = (
            b"GET /healthz HTTP/1.1\r\n"
            + f"Host: {host}:{port}\r\n".encode("ascii")
            + f"Content-Length: {huge_n}\r\n".encode("ascii")
            + b"\r\n"
            + partial_body
        )
        raw, hit_eof = _raw_roundtrip(host, port, req, timeout=5.0)

    # The 200 health response must be received (not RST-truncated).
    assert b'"status"' in raw, (
        f"health response not received (possible RST-truncation): {raw[:80]!r}"
    )
    # Server must have closed (bounded drain completed, did not hang).
    assert hit_eof, "server must close after oversized GET body"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
