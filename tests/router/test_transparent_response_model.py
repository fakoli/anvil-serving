"""End-to-end wire proof for opt-in served-tier response models (issue #180)."""
from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.config import load
from anvil_serving.router.front_door import make_server
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.profile_store import default_profile
from anvil_serving.router.serve import RoutingBackend, build_server


CONFIG = str(Path(__file__).resolve().parents[2] / "configs" / "example.toml")


@contextmanager
def _running(*, transparent: bool, fallback: bool):
    config = replace(
        load(CONFIG),
        transparent_response_model=transparent,
        verify_local_min=fallback,
    )
    routing = RoutingBackend(
        config,
        {
            "fast-local": StaticBackend([""]),
            "heavy-local": StaticBackend(["served by heavy"]),
        },
        default_profile(),
    )
    server = make_server(
        "127.0.0.1",
        0,
        routing,
        response_model_resolver=routing.response_model,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield routing, server.server_address[:2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post(host, port, path, body):
    connection = http.client.HTTPConnection(host, port, timeout=10)
    try:
        connection.request(
            "POST",
            path,
            json.dumps(body),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _wire_models(dialect: str, streaming: bool, raw: bytes) -> list[str]:
    if not streaming:
        return [json.loads(raw)["model"]]
    blocks = [block for block in raw.decode("utf-8").strip().split("\n\n") if block]
    if dialect == "openai":
        return [
            json.loads(block.removeprefix("data: "))["model"]
            for block in blocks
            if block != "data: [DONE]"
        ]
    events = []
    for block in blocks:
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    if dialect == "anthropic":
        return [events[0][1]["message"]["model"]]
    return [
        data["response"]["model"]
        for _name, data in events
        if isinstance(data.get("response"), dict) and "model" in data["response"]
    ]


def _request(dialect: str, streaming: bool):
    if dialect == "openai":
        return "/v1/chat/completions", {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": streaming,
        }
    if dialect == "anthropic":
        return "/v1/messages", {
            "model": "chat",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": streaming,
        }
    return "/v1/responses", {
        "model": "chat",
        "input": "hi",
        "stream": streaming,
    }


@pytest.mark.parametrize("dialect", ["openai", "anthropic", "responses"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("transparent", [False, True])
@pytest.mark.parametrize("fallback", [False, True])
def test_wire_model_is_default_compatible_or_reports_served_tier(
    dialect, streaming, transparent, fallback,
):
    with _running(transparent=transparent, fallback=fallback) as (routing, (host, port)):
        path, body = _request(dialect, streaming)
        status, raw = _post(host, port, path, body)

    assert status == 200, raw
    served_tier = "heavy-local" if fallback else "fast-local"
    expected_model = served_tier if transparent else "chat"
    assert _wire_models(dialect, streaming, raw)
    assert set(_wire_models(dialect, streaming, raw)) == {expected_model}
    if fallback:
        assert routing._decision_log.last.served_tier == served_tier
    else:
        # Uncorrelated trusted direct streams intentionally omit decision-log
        # records; the wire assertion above is the observable served-tier proof.
        assert routing._decision_log.last is None


def test_direct_allow_sets_transparent_model_before_stream_consumption():
    config = replace(
        load(CONFIG),
        transparent_response_model=True,
        verify_local_min=False,
    )
    routing = RoutingBackend(
        config,
        {
            "fast-local": StaticBackend(["fast"]),
            "heavy-local": StaticBackend(["heavy"]),
        },
        default_profile(),
    )
    request = InternalRequest(model="chat", messages=[Message("user", "hi")], stream=True)

    deltas = routing.generate(request)

    assert request.model == "chat"
    assert routing.response_model(request.model) == "fast-local"
    assert "".join(deltas) == "fast"


def test_production_build_server_wires_transparent_response_model(tmp_path):
    source = Path(CONFIG).read_text(encoding="utf-8")
    source = source.replace(
        "# transparent_response_model = false",
        "transparent_response_model = true",
        1,
    )
    config_path = tmp_path / "transparent.toml"
    config_path.write_text(source, encoding="utf-8")
    server = build_server(
        str(config_path),
        host="127.0.0.1",
        port=0,
        backends={
            "fast-local": StaticBackend(["fast"]),
            "heavy-local": StaticBackend(["heavy"]),
        },
        profile=default_profile(),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        path, body = _request("responses", False)
        status, raw = _post(host, port, path, body)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200, raw
    assert _wire_models("responses", False, raw) == ["fast-local"]


class _MutatingBackend:
    def generate(self, request):
        request.model = "backend-controlled"
        return iter(["ok"])


@pytest.mark.parametrize("streaming", [False, True])
def test_plain_backend_cannot_control_wire_model_by_mutating_request(streaming):
    server = make_server("127.0.0.1", 0, _MutatingBackend())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        path, body = _request("openai", streaming)
        status, raw = _post(host, port, path, body)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200, raw
    assert set(_wire_models("openai", streaming, raw)) == {"chat"}


@pytest.mark.parametrize("streaming", [False, True])
def test_default_off_routing_backend_cannot_mutate_requested_wire_model(streaming):
    config = replace(
        load(CONFIG),
        transparent_response_model=False,
        verify_local_min=False,
    )
    routing = RoutingBackend(
        config,
        {
            "fast-local": _MutatingBackend(),
            "heavy-local": StaticBackend(["heavy"]),
        },
        default_profile(),
    )
    server = make_server(
        "127.0.0.1",
        0,
        routing,
        response_model_resolver=routing.response_model,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        path, body = _request("responses", streaming)
        status, raw = _post(host, port, path, body)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200, raw
    assert set(_wire_models("responses", streaming, raw)) == {"chat"}


@pytest.mark.parametrize("resolved", [None, "", 17, RuntimeError("boom")])
def test_invalid_or_failing_resolver_falls_back_to_requested_model(resolved):
    def resolver(_requested):
        if isinstance(resolved, Exception):
            raise resolved
        return resolved

    server = make_server(
        "127.0.0.1",
        0,
        StaticBackend(["ok"]),
        response_model_resolver=resolver,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        path, body = _request("responses", False)
        status, raw = _post(host, port, path, body)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200, raw
    assert _wire_models("responses", False, raw) == ["chat"]
