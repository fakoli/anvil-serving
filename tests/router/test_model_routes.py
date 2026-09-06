"""Direct-only chat capability gateway coverage."""
from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from anvil_serving.router.availability import AvailabilityResult
from anvil_serving.router.config import ConfigError, load
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.serve import build_server
from anvil_serving.router.replica_scheduler import normalize_replica_pressure
from anvil_serving.router import serve as serve_module
from tests.router.test_backends import _CompletedPressure, _MemberBackend, _ReplicaAvailability, _ready
from tests.router.test_config import _REPLICA_TIER


_CONFIG = """\
[router]

[[router.tiers]]
id = "primary"
base_url = "http://127.0.0.1:31002/v1"
dialect = "openai"
context_limit = 16
max_output_tokens = 8
privacy = "local"
tool_support = true
auth_env = "ANVIL_PRIMARY_KEY"
model = "primary-model"

[router.model_routes]
llm.primary = "primary"
"""


class CountingBackend:
    def __init__(self, tokens):
        self.tokens = list(tokens)
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        yield from self.tokens


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


def _post(host, port, body, *, stream=False):
    connection = http.client.HTTPConnection(host, port, timeout=10)
    try:
        body = {**body, "stream": stream}
        connection.request("POST", "/v1/chat/completions", json.dumps(body), {"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.fixture
def config_path(tmp_path: Path) -> str:
    path = tmp_path / "direct.toml"
    path.write_text(_CONFIG, encoding="utf-8")
    return str(path)


def _server(config_path, backend, **kwargs):
    return build_server(config_path, host="127.0.0.1", port=0, backends={"primary": backend}, **kwargs)


def test_config_requires_closed_direct_aliases(config_path, tmp_path):
    config = load(config_path)
    assert config.model_routes == {"llm.primary": "primary"}
    assert config.route_tier(" LLM.PRIMARY ").id == "primary"

    for replacement in ("", "llm.primary = \"missing\""):
        path = tmp_path / "invalid.toml"
        table = "[router.model_routes]\n" + replacement
        path.write_text(_CONFIG.rsplit("[router.model_routes]", 1)[0] + table, encoding="utf-8")
        with pytest.raises(ConfigError):
            load(path)


def test_direct_alias_is_the_only_chat_route_and_is_logged(config_path):
    backend = CountingBackend(["served"])
    server = _server(config_path, backend)
    with _running(server) as (host, port):
        status, raw = _post(host, port, {
            "model": "LLM.PRIMARY",
            "messages": [{"role": "user", "content": "hello"}],
        })
    assert status == 200
    assert "served" in raw.decode()
    assert backend.calls == 1
    record = server.anvil_routing._decision_log.last
    assert record is not None
    assert record.route == "llm.primary"
    assert record.served_tier == "primary"


def test_unknown_chat_model_is_a_clean_404_without_dispatch(config_path):
    backend = CountingBackend(["must not run"])
    server = _server(config_path, backend)
    with _running(server) as (host, port):
        status, raw = _post(host, port, {
            "model": "chat",
            "messages": [{"role": "user", "content": "hello"}],
        })
    assert status == 404
    assert "unknown configured model" in raw.decode()
    assert backend.calls == 0


def test_direct_route_has_no_fallback_for_unavailable_context_or_quiesced(config_path):
    backend = CountingBackend(["must not run"])
    server = _server(config_path, backend, availability=Unavailable())
    with _running(server) as (host, port):
        status, _ = _post(host, port, {
            "model": "llm.primary",
            "messages": [{"role": "user", "content": "hello"}],
        })
    assert status == 503
    assert backend.calls == 0

    backend = CountingBackend(["must not run"])
    server = _server(config_path, backend)
    with _running(server) as (host, port):
        status, _ = _post(host, port, {
            "model": "llm.primary",
            "messages": [{"role": "user", "content": "word " * 20}],
        })
    assert status == 413
    assert backend.calls == 0

    server = _server(config_path, backend)
    server.anvil_routing.quiesce_tier("primary", "test")
    with _running(server) as (host, port):
        status, _ = _post(host, port, {
            "model": "llm.primary",
            "messages": [{"role": "user", "content": "hello"}],
        })
    assert status == 503
    assert backend.calls == 0


def test_models_lists_only_configured_aliases(config_path):
    server = _server(config_path, CountingBackend(["served"]))
    with _running(server) as (host, port):
        connection = http.client.HTTPConnection(host, port, timeout=10)
        try:
            connection.request("GET", "/v1/models")
            response = connection.getresponse()
            payload = json.loads(response.read())
        finally:
            connection.close()
    assert [entry["id"] for entry in payload["data"]] == ["llm.primary"]
    assert payload["data"][0]["context_window"] == 16
    assert payload["data"][0]["max_output_tokens"] == 8


def test_direct_backend_error_is_logged_without_an_alternate_attempt(config_path):
    class Raising:
        def generate(self, request):
            raise RuntimeError("backend detail")
            yield  # pragma: no cover

    routing = _server(config_path, Raising()).anvil_routing
    request = InternalRequest(model="llm.primary", messages=[Message("user", "hello")], stream=True)
    with pytest.raises(RuntimeError):
        list(routing.generate(request))
    record = routing._decision_log.last
    assert record is not None
    assert record.served_tier is None
    assert record.attempts[0].reason == "backend_error_RuntimeError"


@pytest.mark.parametrize("durable", [False, True])
def test_server_wires_capacity_strategy_ceilings_and_cache_close(tmp_path, monkeypatch, durable):
    monkeypatch.setattr(serve_module, "ReplicaPressureCache", _CompletedPressure)
    body = _REPLICA_TIER.replace(
        "model_identity = true", 'model_identity = true\nreplica_strategy = "capacity"\nmax_concurrency = 1',
    ).replace('qualification_ref = "qualification:primary-a"',
              'qualification_ref = "qualification:primary-a", max_concurrency = 2').replace(
        'qualification_ref = "qualification:primary-b"',
        'qualification_ref = "qualification:primary-b", max_concurrency = 3',
    )
    if durable:
        body += "\n[server]\nadmission_state_path = " + json.dumps(str(tmp_path / "intent.json")) + "\n"
    path = tmp_path / "capacity.toml"
    path.write_text(body, encoding="utf-8")
    members = {"member-a": _MemberBackend("a"), "member-b": _MemberBackend("b")}
    availability = _ReplicaAvailability({
        ("primary", key): _ready("primary-model") for key in members
    })
    server = build_server(
        str(path), host="127.0.0.1", port=0,
        backends={"primary": serve_module.ReplicaRuntime(members)}, availability=availability,
    )
    routing = server.anvil_routing
    cache = routing._replica_pressure
    # A strategy actually passed to the shared owner chooses B on a fresh tie;
    # an accidentally default round-robin owner would choose A.
    cache.values["primary"]["member-b"] = normalize_replica_pressure(
        observed_at=1, now_monotonic=1, successful=True,
        requests_running=0, requests_waiting=0, scheduler_capacity=3,
    )
    iterator = None
    try:
        snapshot = routing._admission.snapshot("primary")
        assert snapshot.max_concurrency == 1
        assert tuple(member.max_concurrency for member in snapshot.members) == (2, 3)
        iterator = routing.generate(InternalRequest(model="llm.primary", messages=[Message("user", "hi")]))
        assert next(iterator) == "b"
        assert members["member-a"].calls == 0
        assert cache.calls == ["primary"]
    finally:
        if iterator is not None:
            iterator.close()
        server.server_close()
        server.server_close()
    assert cache.closed == 1
    assert server.socket.fileno() == -1
    if durable:
        assert json.loads((tmp_path / "intent.json").read_text(encoding="utf-8"))["tiers"] == {}


def test_server_close_releases_socket_even_if_pressure_owner_close_fails(config_path, monkeypatch):
    server = _server(config_path, CountingBackend(["ok"]))

    def broken_close():
        raise RuntimeError("close failure")

    monkeypatch.setattr(server.anvil_routing._replica_pressure, "close", broken_close)
    with pytest.raises(RuntimeError, match="close failure"):
        server.server_close()
    assert server.socket.fileno() == -1
    server.server_close()
