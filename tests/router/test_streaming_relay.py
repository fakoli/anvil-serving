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
import urllib.error
from dataclasses import replace
from functools import partial

import pytest

from anvil_serving.router.backends.relay import RelayBackend, RelayBackendError
from anvil_serving.router.availability import AvailabilityResult
from anvil_serving.router.config import ReplicaIdentity, ReplicaMember, RouterConfig, Tier
from anvil_serving.router.front_door import make_server
from anvil_serving.router.serve import ReplicaRuntime, RoutingBackend
from anvil_serving.router.backends.sse import (
    AnthropicStreamAssembler,
    OpenAIStreamAssembler,
    iter_sse_events,
)
from anvil_serving.router.internal import InternalRequest, Message
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
def test_iter_sse_events_named_and_plain():
    raw = (b"event: message_start\ndata: {\"a\":1}\n\n"
           b": keep-alive comment\n\n"
           b"data: {\"b\":2}\n\n"
           b"data: [DONE]\n\n")
    events = list(iter_sse_events(io.BytesIO(raw)))
    assert events == [("message_start", '{"a":1}'), (None, '{"b":2}'),
                      (None, "[DONE]")]


def test_iter_sse_events_multiline_data_and_no_trailing_blank():
    raw = b"data: {\"x\":\ndata: 1}\n\ndata: tail"
    events = list(iter_sse_events(io.BytesIO(raw)))
    assert events == [(None, '{"x":\n1}'), (None, "tail")]


# --------------------------------------------------------------------------- #
# OpenAI streaming
# --------------------------------------------------------------------------- #
def test_openai_streaming_deltas_and_structured():
    payload = _openai_sse(
        {"choices": [{"index": 0, "delta": {"role": "assistant"}}]},
        {"choices": [{"index": 0, "delta": {"content": "Hel"}}]},
        {"choices": [{"index": 0, "delta": {"content": "lo"}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 2}},
    )
    transport = FakeStreamTransport(payload)
    backend = RelayBackend(_tier("openai"), env={"EXAMPLE_KEY": "k"},
                           stream_transport=transport)
    deltas = list(backend.generate(_request()))
    assert deltas == ["Hel", "lo"]  # REAL model chunks, not word-split fakes
    s = backend.get_last_structured()
    assert s is not None
    assert s.finish_reason == "stop"
    assert s.usage == {"input_tokens": 12, "output_tokens": 2}
    assert transport.response.closed
    # The upstream body asked to stream, with usage in the final chunk.
    body = transport.bodies[0]
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}


def test_openai_streaming_tool_calls_accumulate():
    payload = _openai_sse(
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "get_weather", "arguments": ""}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"city\": "}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "\"Oakland\"}"}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    )
    backend = RelayBackend(_tier("openai"), env={"EXAMPLE_KEY": "k"},
                           stream_transport=FakeStreamTransport(payload))
    assert list(backend.generate(_request())) == []
    s = backend.get_last_structured()
    (tc,) = s.tool_calls
    assert tc["id"] == "call_1" and tc["name"] == "get_weather"
    assert json.loads(tc["arguments"]) == {"city": "Oakland"}  # str, like buffered
    assert s.finish_reason == "tool_calls"


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


def test_anthropic_streaming_preserves_tool_arguments_without_gateway_rewrite():
    assembler = AnthropicStreamAssembler()
    assembler.feed("content_block_start", json.dumps({
        "type": "content_block_start",
        "index": 0,
        "content_block": {
            "type": "tool_use",
            "id": "toolu_bad",
            "name": "get_weather",
            "input": {},
        },
    }))
    assembler.feed("content_block_delta", json.dumps({
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": "{not json"},
    }))

    (tool_call,) = assembler.result().tool_calls
    assert tool_call["arguments"] == "{not json"


# --------------------------------------------------------------------------- #
# gating / fallbacks / caps
# --------------------------------------------------------------------------- #
def test_non_sse_response_falls_back_to_buffered_parse():
    body = json.dumps({
        "choices": [{"message": {"content": "one two"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }).encode()
    backend = RelayBackend(
        _tier("openai"), env={"EXAMPLE_KEY": "k"},
        stream_transport=FakeStreamTransport(body, ctype="application/json"))
    assert "".join(backend.generate(_request())) == "one two"
    s = backend.get_last_structured()
    assert s.finish_reason == "stop"
    assert s.usage == {"input_tokens": 3, "output_tokens": 2}


@pytest.mark.parametrize(
    ("dialect", "wire_usage", "expected"),
    [
        ("openai", {"prompt_tokens": 11}, {"input_tokens": 11}),
        ("anthropic", {"output_tokens": 7}, {"output_tokens": 7}),
    ],
)
def test_real_buffered_relay_preserves_each_partial_usage_field(
    dialect, wire_usage, expected
):
    def buffered(url, *, data, headers, timeout):
        if dialect == "anthropic":
            return json.dumps(
                {"content": [{"type": "text", "text": "ok"}], "usage": wire_usage}
            ).encode()
        return json.dumps(
            {"choices": [{"message": {"content": "ok"}}], "usage": wire_usage}
        ).encode()

    request = InternalRequest(
        model="chat",
        messages=[Message("user", "hi")],
        stream=False,
        dialect=dialect,
    )
    backend = RelayBackend(
        _tier(dialect), env={"EXAMPLE_KEY": "k"}, transport=buffered
    )
    assert list(backend.generate(request)) == ["ok"]
    assert backend.get_last_structured().usage == expected


def test_real_openai_sse_relay_preserves_partial_input_usage():
    payload = _openai_sse(
        {"choices": [{"index": 0, "delta": {"content": "ok"}}]},
        {"choices": [], "usage": {"prompt_tokens": 11}},
    )
    backend = RelayBackend(
        _tier("openai"),
        env={"EXAMPLE_KEY": "k"},
        stream_transport=FakeStreamTransport(payload),
    )
    assert list(backend.generate(_request())) == ["ok"]
    assert backend.get_last_structured().usage == {"input_tokens": 11}


def test_real_anthropic_sse_relay_preserves_partial_output_usage():
    payload = (
        b'event: content_block_delta\ndata: {"type":"content_block_delta",'
        b'"index":0,"delta":{"type":"text_delta","text":"ok"}}\n\n'
        b'event: message_delta\ndata: {"type":"message_delta",'
        b'"usage":{"output_tokens":7}}\n\n'
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    request = InternalRequest(
        model="chat",
        messages=[Message("user", "hi")],
        stream=True,
        dialect="anthropic",
    )
    backend = RelayBackend(
        _tier("anthropic"),
        env={"EXAMPLE_KEY": "k"},
        stream_transport=FakeStreamTransport(payload),
    )
    assert list(backend.generate(request)) == ["ok"]
    assert backend.get_last_structured().usage == {"output_tokens": 7}


def test_custom_buffered_transport_never_streams():
    """A hermetic buffered transport (no stream companion) keeps the old path —
    it must not attempt a network streaming call."""
    calls = []

    def buffered(url, *, data, headers, timeout):
        calls.append(json.loads(data))
        return json.dumps({
            "choices": [{"message": {"content": "buffered"},
                         "finish_reason": "stop"}],
        }).encode()

    backend = RelayBackend(_tier("openai"), env={"EXAMPLE_KEY": "k"},
                           transport=buffered)
    assert "".join(backend.generate(_request(stream=True))) == "buffered"
    assert calls[0]["stream"] is False  # buffered path body unchanged


def test_extra_body_stream_override_wins():
    tier = _tier("openai", extra_body={"stream": False})
    transport = FakeStreamTransport(_openai_sse(
        {"choices": [{"index": 0, "delta": {"content": "x"}}]}))
    backend = RelayBackend(tier, env={"EXAMPLE_KEY": "k"},
                           stream_transport=transport)
    list(backend.generate(_request()))
    body = transport.bodies[0]
    assert body["stream"] is False           # operator override respected
    assert "stream_options" not in body


def test_streaming_response_cap_enforced():
    payload = _openai_sse(
        {"choices": [{"index": 0, "delta": {"content": "x" * 64}}]},
        {"choices": [{"index": 0, "delta": {"content": "y" * 64}}]},
    )
    backend = RelayBackend(_tier("openai"), env={"EXAMPLE_KEY": "k"},
                           stream_transport=FakeStreamTransport(payload),
                           max_response_bytes=100)
    with pytest.raises(RelayBackendError):
        list(backend.generate(_request()))


def test_relay_backend_streams_too():
    payload = _openai_sse(
        {"choices": [{"index": 0, "delta": {"content": "local "}}]},
        {"choices": [{"index": 0, "delta": {"content": "stream"}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    )
    tier = _tier("openai", privacy="local")
    backend = RelayBackend(tier, env={},
                           stream_transport=FakeStreamTransport(payload))
    assert list(backend.generate(_request())) == ["local ", "stream"]


def test_assemblers_skip_malformed_events():
    oa = OpenAIStreamAssembler()
    assert oa.feed(None, "not json") is None
    assert oa.feed(None, '{"choices": "nope"}') is None
    an = AnthropicStreamAssembler()
    assert an.feed("content_block_delta", "not json") is None
    assert an.feed(None, '{"type": "content_block_delta", "delta": 5}') is None


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


def test_replica_actual_sse_normal_and_malformed_terminal_paths_release_once(
    monkeypatch, replica_routing,
):
    normal: list[FakeStreamResponse] = []
    malformed: list[FakeStreamResponse] = []

    def normal_response() -> FakeStreamResponse:
        response = FakeStreamResponse(_openai_sse(
            {"choices": [{"index": 0, "delta": {"content": "ok"}}]},
        ))
        normal.append(response)
        return response

    def malformed_response() -> FakeStreamResponse:
        response = FakeStreamResponse(b"data: not-json\n\ndata: [DONE]\n\n")
        malformed.append(response)
        return response

    transport = _ReplicaStreamTransport({
        "member-a": normal_response,
        "member-b": malformed_response,
    })
    readiness = _ReplicaReadiness({"member-a": _ready(), "member-b": _ready()})
    tier, routing = replica_routing(transport, readiness)
    releases = _count_real_member_releases(monkeypatch, routing)

    assert list(routing.generate(_replica_stream_request())) == ["ok"]
    assert normal[0].closed
    assert routing._admission.snapshot(tier.id).active_requests == 0
    assert list(routing.generate(_replica_stream_request())) == []
    assert malformed[0].closed
    snapshot = routing._admission.snapshot(tier.id)
    assert snapshot.active_requests == 0
    assert snapshot.member_active_requests == (("member-a", 0), ("member-b", 0))
    assert transport.calls == ["member-a", "member-b"]
    assert releases == ["member-a", "member-b"]


def test_replica_sse_terminal_iterator_errors_release_selected_member_without_retry(
    monkeypatch, replica_routing,
):
    transport = _ReplicaStreamTransport({
        "member-a": lambda: _TerminalSSEResponse(TimeoutError("synthetic timeout")),
        "member-b": lambda: FakeStreamResponse(_openai_sse()),
    })
    readiness = _ReplicaReadiness({"member-a": _ready(), "member-b": _ready()})
    tier, routing = replica_routing(transport, readiness)
    releases = _count_real_member_releases(monkeypatch, routing)

    stream = routing.generate(_replica_stream_request())
    assert next(stream) == "one"
    with pytest.raises(TimeoutError, match="synthetic timeout"):
        next(stream)
    snapshot = routing._admission.snapshot(tier.id)
    assert snapshot.active_requests == 0
    assert snapshot.member_active_requests == (("member-a", 0), ("member-b", 0))
    assert transport.calls == ["member-a"]
    assert transport.opened[0].closed
    assert releases == ["member-a"]


@pytest.mark.parametrize(
    ("terminal", "expected"),
    [
        (lambda: _TerminalSSEResponse(GeneratorExit()), GeneratorExit),
        (
            lambda: FakeStreamResponse(
                b'data: {"choices":[{"index":0,"delta":{"content":"one"}}]}\n\n'
                b'data: {"error":{}}\n\n',
            ),
            RelayBackendError,
        ),
    ],
)
def test_replica_sse_generator_exit_and_provider_error_release_once(
    monkeypatch, terminal, expected, replica_routing,
):
    transport = _ReplicaStreamTransport({
        "member-a": terminal,
        "member-b": lambda: FakeStreamResponse(_openai_sse()),
    })
    readiness = _ReplicaReadiness({"member-a": _ready(), "member-b": _ready()})
    tier, routing = replica_routing(transport, readiness)
    releases = _count_real_member_releases(monkeypatch, routing)

    stream = routing.generate(_replica_stream_request())
    assert next(stream) == "one"
    with pytest.raises(expected):
        next(stream)
    assert transport.opened[0].closed
    assert routing._admission.snapshot(tier.id).active_requests == 0
    assert transport.calls == ["member-a"]
    assert releases == ["member-a"]
    _assert_replica_idle(routing, tier)


def test_replica_sse_close_before_first_and_after_first_release_once(monkeypatch, replica_routing):
    transport = _ReplicaStreamTransport({
        "member-a": lambda: FakeStreamResponse(_openai_sse(
            {"choices": [{"index": 0, "delta": {"content": "one"}}]},
            {"choices": [{"index": 0, "delta": {"content": "two"}}]},
        )),
        "member-b": lambda: FakeStreamResponse(_openai_sse()),
    })
    readiness = _ReplicaReadiness({"member-a": _ready(), "member-b": _ready()})
    tier, routing = replica_routing(transport, readiness)
    releases = _count_real_member_releases(monkeypatch, routing)

    unadvanced = routing.generate(_replica_stream_request())
    assert routing._admission.snapshot(tier.id).active_requests == 1
    unadvanced.close()
    unadvanced.close()
    assert transport.opened[0].closed
    assert routing._admission.snapshot(tier.id).active_requests == 0

    # The next request rotates to b. Make b unavailable to pin a as the
    # selected member and model a client disconnect after its first delta.
    readiness.results["member-b"] = _unready()
    cancelling = routing.generate(_replica_stream_request())
    assert next(cancelling) == "one"
    cancelling.close()
    cancelling.close()
    assert routing._admission.snapshot(tier.id).active_requests == 0
    assert transport.calls == ["member-a", "member-a"]
    assert releases == ["member-a", "member-a"]
    _assert_replica_idle(routing, tier)


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
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
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


def test_replica_readiness_loss_after_dispatch_keeps_stream_and_excludes_next_request(
    replica_routing,
):
    blocked = _BlockedSSEResponse(
        (
            b'data: {"choices":[{"index":0,"delta":{"content":"one"}}]}\n',
            b"\n",
        ),
        (
            b'data: {"choices":[{"index":0,"delta":{"content":"two"}}]}\n',
            b"\n",
            b"data: [DONE]\n",
            b"\n",
        ),
    )
    next_member = FakeStreamResponse(_openai_sse(
        {"choices": [{"index": 0, "delta": {"content": "b"}}]},
    ))
    transport = _ReplicaStreamTransport({"member-a": blocked, "member-b": next_member})
    readiness = _ReplicaReadiness({"member-a": _ready(), "member-b": _ready()})
    tier, routing = replica_routing(transport, readiness)

    stream = routing.generate(_replica_stream_request())
    assert next(stream) == "one"
    assert blocked.first_sent.is_set()
    readiness.results["member-a"] = _unready()
    blocked.release.set()
    assert list(stream) == ["two"]
    assert routing._admission.snapshot(tier.id).active_requests == 0
    assert list(routing.generate(_replica_stream_request())) == ["b"]
    assert transport.calls == ["member-a", "member-b"]
    _assert_replica_idle(routing, tier)


@pytest.mark.parametrize("terminal", (
    "success", "http-error", "timeout", "malformed", "cancel",
    "close-before-first", "close-after-first",
))
def test_replica_real_buffered_terminal_paths_release_once(
    monkeypatch, replica_routing, terminal,
):
    response = b'{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}'
    expected_error = None
    if terminal == "http-error":
        response = urllib.error.HTTPError("http://127.0.0.1:32001/v1", 503, "synthetic", {}, None)
        expected_error = RelayBackendError
    elif terminal == "timeout":
        response = urllib.error.URLError(TimeoutError())
        expected_error = RelayBackendError
    elif terminal == "malformed":
        response = b"not-json"
        expected_error = RelayBackendError
    elif terminal == "cancel":
        response = GeneratorExit()
        expected_error = GeneratorExit
    transport = _ReplicaStreamTransport({"member-a": response, "member-b": b"unused"})
    tier, routing = replica_routing(
        transport, _ReplicaReadiness({"member-a": _ready(), "member-b": _ready()}), buffered=True,
    )
    releases = _count_real_member_releases(monkeypatch, routing)
    request = _replica_stream_request()
    request.stream = False
    if expected_error is not None:
        with pytest.raises(expected_error):
            list(routing.generate(request))
    else:
        stream = routing.generate(request)
        snapshot = routing._admission.snapshot(tier.id)
        assert snapshot.active_requests == 1
        assert snapshot.member_active_requests == (("member-a", 1), ("member-b", 0))
        if terminal == "success":
            assert list(stream) == ["ok"]
        elif terminal == "close-after-first":
            assert next(stream) == "ok"
            assert routing._admission.snapshot(tier.id).active_requests == 1
        stream.close()
        stream.close()
    _assert_replica_idle(routing, tier)
    # Buffered relay I/O is lazy; cancelling before iteration releases the
    # reservation without starting any transport. SSE opens eagerly instead.
    assert transport.calls == ([] if terminal == "close-before-first" else ["member-a"])
    assert releases == ["member-a"]


@pytest.mark.parametrize("terminal", ("http-error", "timeout"))
def test_replica_real_sse_open_failure_releases_once(monkeypatch, replica_routing, terminal):
    error = (
        urllib.error.HTTPError("http://127.0.0.1:32001/v1", 503, "synthetic", {}, None)
        if terminal == "http-error" else urllib.error.URLError(TimeoutError())
    )
    transport = _ReplicaStreamTransport({"member-a": error, "member-b": b"unused"})
    tier, routing = replica_routing(
        transport, _ReplicaReadiness({"member-a": _ready(), "member-b": _ready()}),
    )
    releases = _count_real_member_releases(monkeypatch, routing)
    with pytest.raises(RelayBackendError):
        list(routing.generate(_replica_stream_request()))
    _assert_replica_idle(routing, tier)
    assert transport.calls == ["member-a"]
    assert transport.opened == []
    assert releases == ["member-a"]
