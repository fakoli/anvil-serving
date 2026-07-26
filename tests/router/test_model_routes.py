"""Exact direct model-route coverage for the chat router."""
from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from anvil_serving.router.availability import AvailabilityResult
from anvil_serving.router.config import ConfigError, load
from anvil_serving.router.discovery import models_payload
from anvil_serving.router.intent import PRESETS, resolve
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.profile_store import ProfileEntry, ProfileStore
from anvil_serving.router.serve import build_server


_CONFIG = """\
[router]
mapping_version = "model-routes-test"
transparent_response_model = true

[[router.tiers]]
id = "fast"
base_url = "http://127.0.0.1:31001/v1"
dialect = "openai"
context_limit = 4
privacy = "local"
tool_support = true
auth_env = "ANVIL_FAST_KEY"
model = "fast-model"

[[router.tiers]]
id = "heavy"
base_url = "http://127.0.0.1:31002/v1"
dialect = "openai"
context_limit = 128
privacy = "local"
tool_support = true
auth_env = "ANVIL_HEAVY_KEY"
model = "heavy-model"

[router.presets]
chat = ["heavy"]

[router.model_routes]
direct = "fast"
"""


class CountingBackend:
    def __init__(self, tokens):
        self.tokens = list(tokens)
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        yield from self.tokens


class RaisingBackend:
    def generate(self, request):
        raise RuntimeError("private backend detail")
        yield  # pragma: no cover - keeps this method an iterator


class Unavailable:
    def check(self, tier):
        return AvailabilityResult(False, "unavailable", "test_unavailable")


@contextmanager
def _running(server):
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post(host, port, path, body):
    connection = http.client.HTTPConnection(host, port, timeout=10)
    try:
        connection.request(
            "POST", path, json.dumps(body), {"Content-Type": "application/json"}
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _get(host, port, path):
    connection = http.client.HTTPConnection(host, port, timeout=10)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.fixture
def config_path(tmp_path: Path) -> str:
    path = tmp_path / "model-routes.toml"
    path.write_text(_CONFIG, encoding="utf-8")
    return str(path)


def _server(config_path, fast, heavy, **kwargs):
    # The explicit deny would prevent a legacy profile-routed chat turn from
    # reaching fast. A direct model route must still use it.
    profile = ProfileStore({
        ("fast", None): ProfileEntry("deny", 0.0, 1, None),
    })
    return build_server(
        config_path,
        host="127.0.0.1",
        port=0,
        backends={"fast": fast, "heavy": heavy},
        profile=profile,
        **kwargs,
    )


def test_config_parses_model_routes_and_rejects_invalid_values(config_path, tmp_path):
    assert load(config_path).model_routes == {"direct": "fast"}

    for route_table in (
        '"" = "fast"',
        'CHAT = "fast"\nchat = "fast"',
        'chat = "fast"',
        'fast = "heavy"',
        'direct = "missing"',
    ):
        path = tmp_path / "invalid.toml"
        path.write_text(_CONFIG.rsplit("[router.model_routes]", 1)[0] + "[router.model_routes]\n" + route_table, encoding="utf-8")
        with pytest.raises(ConfigError):
            load(str(path))

    cloud_path = tmp_path / "cloud-direct.toml"
    cloud_path.write_text(
        _CONFIG.replace('privacy = "local"', 'privacy = "cloud"', 1),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="metered-cloud billing gate"):
        load(str(cloud_path))


def test_model_route_bypasses_profile_deny_without_shadowing_legacy_tokens(config_path):
    fast = CountingBackend(["fast response"])
    heavy = CountingBackend(["heavy response"])
    server = _server(config_path, fast, heavy)
    with _running(server) as (host, port):
        status, raw = _post(host, port, "/v1/chat/completions", {
            "model": " anvil:DIRECT ",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        })

    assert status == 200
    assert "fast response" in raw.decode("utf-8")
    assert fast.calls == 1
    assert heavy.calls == 0
    record = server.anvil_routing._decision_log.last
    assert record is not None
    assert record.intent == "model-route"
    assert record.served_tier == "fast"
    assert server.anvil_routing._residency() is None


def test_model_route_failure_never_calls_another_backend(config_path):
    fast = CountingBackend([""])
    heavy = CountingBackend(["must not run"])
    server = _server(config_path, fast, heavy)
    with _running(server) as (host, port):
        status, _raw = _post(host, port, "/v1/chat/completions", {
            "model": "direct",
            "messages": [{"role": "user", "content": "hello"}],
        })

    assert status == 503
    assert fast.calls == 1
    assert heavy.calls == 0


def test_model_route_context_and_readiness_fail_without_legacy_fallback(config_path):
    fast = CountingBackend(["fast response"])
    heavy = CountingBackend(["must not run"])
    server = _server(config_path, fast, heavy)
    with _running(server) as (host, port):
        status, _raw = _post(host, port, "/v1/chat/completions", {
            "model": "direct",
            "messages": [{"role": "user", "content": "one two three four five"}],
        })
    assert status == 413
    assert fast.calls == heavy.calls == 0

    fast = CountingBackend(["fast response"])
    heavy = CountingBackend(["must not run"])
    server = _server(config_path, fast, heavy, availability=Unavailable())
    with _running(server) as (host, port):
        status, _raw = _post(host, port, "/v1/chat/completions", {
            "model": "direct",
            "messages": [{"role": "user", "content": "hello"}],
        })
    assert status == 503
    assert fast.calls == heavy.calls == 0
    assert server.anvil_routing._decision_log.last.intent == "model-route"


def test_model_routes_advertise_once_and_stream_both_chat_dialects(config_path):
    fast = CountingBackend(["direct output"])
    heavy = CountingBackend(["must not run"])
    server = _server(config_path, fast, heavy)
    with _running(server) as (host, port):
        status, raw = _get(host, port, "/v1/models")
        assert status == 200
        ids = [entry["id"] for entry in json.loads(raw)["data"]]
        assert ids.count("chat") == 1
        assert "direct" in ids

        status, raw = _post(host, port, "/v1/chat/completions", {
            "model": "direct",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        })
        assert status == 200
        assert "data: " in raw.decode("utf-8")
        assert "direct output" in raw.decode("utf-8")

        status, raw = _post(host, port, "/v1/messages", {
            "model": "direct",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        })
        assert status == 200
        assert "event: content_block_delta" in raw.decode("utf-8")
        assert "direct output" in raw.decode("utf-8")

    assert heavy.calls == 0


def test_model_route_raw_stream_is_always_decision_logged(config_path):
    raw_path = Path(config_path)
    raw_path.write_text(
        raw_path.read_text(encoding="utf-8").replace(
            "transparent_response_model = true",
            "transparent_response_model = true\nverify_local_min = false",
        ),
        encoding="utf-8",
    )
    fast = CountingBackend(["direct output"])
    server = _server(config_path, fast, CountingBackend(["must not run"]))

    with _running(server) as (host, port):
        status, _raw = _post(host, port, "/v1/chat/completions", {
            "model": "direct",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        })

    assert status == 200
    record = server.anvil_routing._decision_log.last
    assert record is not None
    assert record.intent == "model-route"
    assert record.served_tier == "fast"


def test_model_route_raw_stream_failures_and_abandonment_are_logged(config_path):
    raw_path = Path(config_path)
    raw_path.write_text(
        raw_path.read_text(encoding="utf-8").replace(
            "transparent_response_model = true",
            "transparent_response_model = true\nverify_local_min = false",
        ),
        encoding="utf-8",
    )
    request = InternalRequest(
        model="direct",
        messages=[Message(role="user", content="hello")],
        stream=True,
    )

    failed = _server(
        config_path,
        RaisingBackend(),
        CountingBackend(["must not run"]),
    )
    try:
        with pytest.raises(RuntimeError, match="private backend detail"):
            list(failed.anvil_routing.generate(request))
        record = failed.anvil_routing._decision_log.last
        assert record is not None
        assert record.intent == "model-route"
        assert record.served_tier is None
        assert record.attempts[0].verify_reason == "backend_error_RuntimeError"
        assert "private backend detail" not in record.attempts[0].verify_reason
    finally:
        failed.server_close()

    abandoned = _server(
        config_path,
        CountingBackend(["partial", "unread"]),
        CountingBackend(["must not run"]),
    )
    try:
        iterator = abandoned.anvil_routing.generate(request)
        assert next(iterator) == "partial"
        iterator.close()
        record = abandoned.anvil_routing._decision_log.last
        assert record is not None
        assert record.intent == "model-route"
        assert record.served_tier is None
        assert record.attempts[0].verify_reason == "client_disconnected"
    finally:
        abandoned.server_close()


def test_model_route_quiesce_failure_keeps_route_identity(config_path):
    raw_path = Path(config_path)
    raw_path.write_text(
        raw_path.read_text(encoding="utf-8").replace(
            "transparent_response_model = true",
            "transparent_response_model = true\nverify_local_min = false",
        ),
        encoding="utf-8",
    )
    server = _server(
        config_path,
        CountingBackend(["must not run"]),
        CountingBackend(["must not run"]),
    )
    server.anvil_routing.quiesce_tier("fast", "test")

    with _running(server) as (host, port):
        status, _raw = _post(host, port, "/v1/chat/completions", {
            "model": "direct",
            "messages": [{"role": "user", "content": "hello"}],
        })

    assert status == 503
    record = server.anvil_routing._decision_log.last
    assert record is not None
    assert record.intent == "model-route"
    assert record.served_tier is None


def test_discovery_dedupes_model_routes_with_wire_normalization():
    ids = [
        entry["id"]
        for entry in models_payload(PRESETS, ["anvil/chat", "direct", "DIRECT"])["data"]
    ]
    assert ids.count("chat") == 1
    assert ids.count("direct") == 1


def test_unmatched_and_absent_model_routes_preserve_legacy_resolution(tmp_path):
    with_routes = tmp_path / "with-routes.toml"
    with_routes.write_text(_CONFIG, encoding="utf-8")
    without_routes = tmp_path / "without-routes.toml"
    without_routes.write_text(
        _CONFIG.rsplit("[router.model_routes]", 1)[0], encoding="utf-8"
    )
    direct = InternalRequest(
        model="direct", messages=(Message(role="user", content="hello"),)
    )
    legacy_request = InternalRequest(
        model="chat", messages=(Message(role="user", content="hello"),)
    )

    assert resolve(direct, load(with_routes)).source == "model-route"
    assert resolve(legacy_request, load(with_routes)).source == "declared-preset"
    legacy = resolve(legacy_request, load(without_routes))
    assert load(without_routes).model_routes == {}
    assert legacy.source == "declared-preset"
    assert legacy.candidate_tiers == ("heavy",)

    unmatched = InternalRequest(
        model="unconfigured-alias",
        messages=(Message(role="user", content="hello"),),
    )
    assert resolve(unmatched, load(with_routes)).source == "inferred"
