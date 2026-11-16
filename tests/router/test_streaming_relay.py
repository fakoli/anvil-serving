"""True upstream streaming in RelayBackend/RelayBackend.

Hermetic: a fake stream_transport returns canned SSE bytes; no sockets. Pins:

* real per-chunk deltas come out AS THEY ARRIVE (not one buffered blob);
* the assembled StructuredResult (finish_reason / tool_calls / usage) matches
  the buffered path's shapes for both dialects;
* non-SSE responses (upstream ignored stream:true) fall back to the buffered
  parse; buffered custom transports never engage streaming;
* the request body carries stream:true (+ include_usage on OpenAI), with
  extra_body precedence preserved.
"""
from __future__ import annotations

import io
import json
import socket
import threading
from dataclasses import replace
from functools import partial

import pytest

from anvil_serving.router.backends.relay import (
    RelayBackend,
    RelayBackendError,
    RelayTimeoutError,
)
from anvil_serving.router.availability import AvailabilityResult
from anvil_serving.router.config import ReplicaIdentity, ReplicaMember, RouterConfig, Tier
from anvil_serving.router.front_door import make_server
from anvil_serving.router.serve import ReplicaRuntime, RoutingBackend
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.request_control import RequestControl
from tests.router.helpers import make_tier as _tier
from tests.router.test_backends import _CompletedPressure


def _request(stream: bool = True) -> InternalRequest:
    return InternalRequest(
        model="chat", messages=[Message("user", "hi")], max_tokens=64,
        stream=stream, dialect="openai",
    )


class FakeStreamResponse:
    """Line-iterable fake of an open urllib response."""

    def __init__(self, payload: bytes, ctype: str = "text/event-stream"):
        self._fp = io.BytesIO(payload)
        self.headers = {"Content-Type": ctype}
        self.closed = False

    def __iter__(self):
        return iter(self._fp)

    def read(self, n: int = -1) -> bytes:
        return self._fp.read(n)

    def close(self) -> None:
        self.closed = True


class FakeStreamTransport:
    def __init__(self, payload: bytes, ctype: str = "text/event-stream"):
        self.payload = payload
        self.ctype = ctype
        self.bodies = []
        self.response = None

    def __call__(self, url, *, data, headers, timeout):
        self.bodies.append(json.loads(data))
        self.response = FakeStreamResponse(self.payload, self.ctype)
        return self.response


def _openai_sse(*chunks: dict, done: bool = True) -> bytes:
    out = b"".join(
        b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks
    )
    if done:
        out += b"data: [DONE]\n\n"
    return out


# --------------------------------------------------------------------------- #
# SSE parser
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# OpenAI streaming
# --------------------------------------------------------------------------- #


def test_streaming_cancel_interrupts_the_open_socket_without_closing_generator():
    class Socket:
        def __init__(self):
            self.shutdowns = []

        def shutdown(self, how):
            self.shutdowns.append(how)

    class Response(FakeStreamResponse):
        def __init__(self):
            super().__init__(b"")
            self.socket = Socket()
            self.fp = type("FP", (), {"raw": type("Raw", (), {"_sock": self.socket})()})()

    response = Response()
    backend = RelayBackend(
        _tier("openai"), env={"EXAMPLE_KEY": "k"},
        stream_transport=lambda *args, **kwargs: response,
    )
    request = _request()
    control = RequestControl()
    request.raw["_anvil_control"] = control
    stream = backend.generate(request)
    control.cancel()
    assert response.socket.shutdowns == [socket.SHUT_RDWR]
    assert not response.closed
    stream.close()
    assert response.closed


# --------------------------------------------------------------------------- #
# Anthropic streaming
# --------------------------------------------------------------------------- #
def test_anthropic_streaming_deltas_tools_and_usage():
    events = [
        ("message_start", {"type": "message_start", "message": {
            "usage": {"input_tokens": 7, "output_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "Hi"}}),
        ("content_block_start", {"type": "content_block_start", "index": 1,
                                 "content_block": {"type": "tool_use",
                                                   "id": "toolu_1",
                                                   "name": "get_weather",
                                                   "input": {}}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                 "delta": {"type": "input_json_delta",
                                           "partial_json": "{\"city\": \"Oa"}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                 "delta": {"type": "input_json_delta",
                                           "partial_json": "kland\"}"}}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "tool_use"},
                           "usage": {"output_tokens": 9}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    payload = b"".join(
        b"event: " + name.encode() + b"\ndata: " + json.dumps(data).encode() + b"\n\n"
        for name, data in events
    )
    request = InternalRequest(
        model="chat", messages=[Message("user", "hi")], max_tokens=64,
        stream=True, dialect="anthropic",
    )
    backend = RelayBackend(_tier("anthropic"), env={"EXAMPLE_KEY": "k"},
                           stream_transport=FakeStreamTransport(payload))
    assert list(backend.generate(request)) == ["Hi"]
    s = backend.get_last_structured()
    assert s.finish_reason == "tool_use"
    (tc,) = s.tool_calls
    assert tc["arguments"] == {"city": "Oakland"}  # parsed dict, like buffered
    assert s.usage == {"input_tokens": 7, "output_tokens": 9}


# --------------------------------------------------------------------------- #
# gating / fallbacks / caps
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", ["buffered", "json-stream-fallback"])
def test_buffered_and_json_fallback_preserve_code_indentation(mode):
    text = (
        "def encode(plaintext, a, b):\n"
        " if not isinstance(plaintext, str):\n"
        "  raise TypeError('plaintext must be a string')\n"
        " return plaintext\n"
    )
    body = json.dumps({
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }).encode()
    if mode == "buffered":
        backend = RelayBackend(
            _tier("openai"), env={"EXAMPLE_KEY": "k"},
            transport=lambda url, **kwargs: body,
        )
        request = _request(stream=False)
    else:
        backend = RelayBackend(
            _tier("openai"), env={"EXAMPLE_KEY": "k"},
            stream_transport=FakeStreamTransport(body, ctype="application/json"),
        )
        request = _request(stream=True)

    assert "".join(backend.generate(request)) == text


@pytest.mark.parametrize(
    ("failure", "expected"),
    ((ConnectionResetError, RelayBackendError), (TimeoutError, RelayTimeoutError)),
)
def test_buffered_transport_classifies_reset_separately_from_timeout(failure, expected):
    def buffered(url, *, data, headers, timeout):
        raise failure("private-transport-detail")

    backend = RelayBackend(
        _tier("openai"), env={"EXAMPLE_KEY": "k"}, transport=buffered,
    )
    with pytest.raises(expected) as caught:
        list(backend.generate(_request(stream=False)))
    assert type(caught.value) is expected
    assert str(caught.value) in {
        "model upstream request failed", "model upstream request timed out",
    }
    assert "private-" not in str(caught.value)


# --------------------------------------------------------------------------- #
# End-to-end: relay backend -> get_last_structured -> OpenAI dialect.stream
# --------------------------------------------------------------------------- #
def test_openai_streaming_usage_flows_to_dialect_usage_chunk():
    """A live streaming request with include_usage yields a real-usage chunk.

    Proves the full path the #345 fix relies on: ``relay._generate_streaming``
    sets ``stream_options.include_usage`` upstream, the SSE assembler turns the
    upstream's trailing usage into ``StructuredResult.usage``, and
    ``OpenAIDialect.stream`` (via ``get_last_structured``) renders it as a final
    ``chat.completion.chunk`` carrying the REAL counts.
    """
    from anvil_serving.router.dialects.openai import OpenAIDialect

    payload = _openai_sse(
        {"choices": [{"index": 0, "delta": {"content": "ok"}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 2}},
    )
    backend = RelayBackend(_tier("openai"), env={"EXAMPLE_KEY": "k"},
                           stream_transport=FakeStreamTransport(payload))
    request = _request(stream=True)
    request.raw = {"stream": True, "stream_options": {"include_usage": True}}
    deltas = list(backend.generate(request))
    assert deltas == ["ok"]

    d = OpenAIDialect()
    chunks = b"".join(d.stream(request, deltas, get_structured=backend.get_last_structured)).decode()
    # Parse into chunk dicts.
    parsed = []
    for line in chunks.split("data: "):
        line = line.strip()
        if not line or line == "[DONE]":
            continue
        parsed.append(json.loads(line))
    # The final content chunk before [DONE] must carry the real usage.
    usage_chunks = [c for c in parsed if c.get("usage") is not None]
    assert len(usage_chunks) == 1
    # padding chunk
    assert parsed[-1]["choices"] == []
    assert usage_chunks[0]["usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 2,
        "total_tokens": 14,
    }


# --------------------------------------------------------------------------- #
# Qualified replica sets T009 — actual relay SSE terminal ownership
# --------------------------------------------------------------------------- #
class _ReplicaReadiness:
    def __init__(self, results: dict[str, AvailabilityResult]) -> None:
        self.results = results
        self.calls: list[str] = []

    def check_member(self, _tier, member_id: str) -> AvailabilityResult:
        self.calls.append(member_id)
        return self.results[member_id]


def _replica_stream_tier(strategy="round_robin"):
    return Tier(
        id="replica-stream",
        base_url="",
        dialect="openai",
        context_limit=200_000,
        privacy="local",
        tool_support=True,
        auth_env="EXAMPLE_KEY",
        model="concrete-model",
        health_path="/health",
        model_identity=True,
        replica_strategy=strategy,
        replicas=(
            ReplicaMember(
                "member-a", "http://127.0.0.1:32001/v1", "node-a",
                "resource-a", "qualification:a", max_concurrency=2,
            ),
            ReplicaMember(
                "member-b", "http://127.0.0.1:32002/v1", "node-a",
                "resource-b", "qualification:b", max_concurrency=2,
            ),
        ),
        replica_identity=ReplicaIdentity(
            model_revision="revision-1",
            engine_version="engine-1",
            image_digest="sha256:" + "1" * 64,
            config_fingerprint="sha256:" + "2" * 64,
        ),
    )


class _ReplicaStreamTransport:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.opened: list[object] = []

    def __call__(self, url, *, data, headers, timeout):
        member_id = "member-a" if ":32001/" in url else "member-b"
        self.calls.append(member_id)
        result = self.responses[member_id]
        if callable(result):
            result = result()
        if isinstance(result, BaseException):
            raise result
        self.opened.append(result)
        return result


class _BlockedSSEResponse(FakeStreamResponse):
    def __init__(self, first: tuple[bytes, ...], final: tuple[bytes, ...]) -> None:
        super().__init__(b"")
        self.first = first
        self.final = final
        self.first_sent = threading.Event()
        self.release = threading.Event()

    def __iter__(self):
        self.first_sent.set()
        yield from self.first
        assert self.release.wait(timeout=5)
        yield from self.final


class _TerminalSSEResponse(FakeStreamResponse):
    """Yield one complete SSE delta, then model an in-flight terminal error."""

    def __init__(self, error: BaseException) -> None:
        super().__init__(b"")
        self.error = error

    def __iter__(self):
        yield b'data: {"choices":[{"index":0,"delta":{"content":"one"}}]}\n'
        yield b"\n"
        raise self.error


def _ready(model: str = "concrete-model") -> AvailabilityResult:
    return AvailabilityResult(True, "ready", "identity_passed", model, model)


def _unready() -> AvailabilityResult:
    return AvailabilityResult(False, "unavailable", "member_unavailable")


def _replica_stream_routing(
    transport, readiness: _ReplicaReadiness, *, strategy="round_robin", buffered=False,
):
    tier = _replica_stream_tier(strategy)
    members = {
        member.id: RelayBackend(
            replace(tier, base_url=member.base_url, replicas=()),
            env={},
            **({"transport": transport} if buffered else {"stream_transport": transport}),
        )
        for member in tier.replicas
    }
    return tier, RoutingBackend(
        RouterConfig(tiers=(tier,), model_routes={"replica.stream": tier.id}),
        {tier.id: ReplicaRuntime(members)},
        availability=readiness,
    )


@pytest.fixture(params=("round_robin", "capacity"))
def replica_routing(request, monkeypatch):
    """Run the real terminal owner against both strategies, without metrics I/O."""
    from anvil_serving.router import serve as serve_module

    monkeypatch.setattr(serve_module, "ReplicaPressureCache", _CompletedPressure)
    return partial(_replica_stream_routing, strategy=request.param)


def _assert_replica_idle(routing, tier):
    snapshot = routing._admission.snapshot(tier.id)
    assert snapshot.active_requests == 0
    assert snapshot.member_active_requests == (("member-a", 0), ("member-b", 0))
    assert snapshot.active_requests == sum(count for _, count in snapshot.member_active_requests)


def _replica_stream_request() -> InternalRequest:
    request = _request(stream=True)
    request.model = "replica.stream"
    return request


def _count_real_member_releases(monkeypatch, routing: RoutingBackend) -> list[str]:
    """Count owner calls while retaining TierAdmission's real counters."""
    calls: list[str] = []
    acquire_member = routing._admission.acquire_member

    def counted_acquire(tier_id, readiness, pressure=None):
        lease = acquire_member(tier_id, readiness, pressure)
        if lease is None:
            return None
        release = lease.release

        def counted_release() -> None:
            calls.append(lease.member_id)
            release()

        lease.release = counted_release
        return lease

    monkeypatch.setattr(routing._admission, "acquire_member", counted_acquire)
    return calls


@pytest.mark.parametrize("path", ("/v1/chat/completions", "/v1/responses"))
def test_replica_front_door_disconnect_closes_selected_stream_once(
    monkeypatch, replica_routing, path,
):
    transport = _ReplicaStreamTransport({
        "member-a": lambda: FakeStreamResponse(_openai_sse(
            {"choices": [{"index": 0, "delta": {"content": "one"}}]},
        )),
        "member-b": lambda: FakeStreamResponse(_openai_sse()),
    })
    readiness = _ReplicaReadiness({"member-a": _ready(), "member-b": _ready()})
    tier, routing = replica_routing(transport, readiness)
    releases = _count_real_member_releases(monkeypatch, routing)
    server = make_server(
        "127.0.0.1", 0, routing, model_routes=("replica.stream",)
    )
    handler = server.RequestHandlerClass
    original_end_headers = handler.end_headers

    class _DisconnectedWFile:
        def __init__(self, wrapped) -> None:
            self._wrapped = wrapped

        def write(self, _payload) -> None:
            raise BrokenPipeError("synthetic client disconnect")

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    def arm_disconnect(self) -> None:
        original_end_headers(self)
        self.wfile = _DisconnectedWFile(self.wfile)

    handler.end_headers = arm_disconnect
    server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    server_thread.start()
    try:
        host, port = server.server_address[:2]
        with socket.create_connection((host, port), timeout=5) as raw_socket:
            request_body = {
                "model": "replica.stream",
                "stream": True,
            }
            if path == "/v1/responses":
                request_body["input"] = "hi"
            else:
                request_body["messages"] = [{"role": "user", "content": "hi"}]
            body = json.dumps(request_body).encode("utf-8")
            raw_socket.sendall(
                f"POST {path} HTTP/1.1\r\n".encode("ascii")
                + b"Host: 127.0.0.1\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii")
                + body
            )
            while raw_socket.recv(4096):
                pass
    finally:
        handler.end_headers = original_end_headers
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    assert not server_thread.is_alive()
    assert transport.calls == ["member-a"]
    assert routing._admission.snapshot(tier.id).active_requests == 0
    assert releases == ["member-a"]
    _assert_replica_idle(routing, tier)
