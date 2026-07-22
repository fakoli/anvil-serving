"""Contract tests for the bearer-authed per-tier/serve health snapshot (#292).

``GET /v1/health/tiers`` surfaces the router's ALREADY-TRACKED availability for
EVERY configured serve (chat ``llm`` tiers, purpose models, audio routes) — not
only recently-routed ones — behind the same bearer auth as the rest of the
router.  These tests are hermetic: no GPU, no live serve, no network.  A tracked
readiness result is injected; the real front door + real ``RoutingBackend`` do
the rest.
"""
from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from typing import Optional

import pytest

from anvil_serving.router.availability import (
    AlwaysAvailable,
    AvailabilityResult,
    HttpHealthAvailability,
)
from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.config import AudioRoute, PurposeModel, RouterConfig, Tier
from anvil_serving.router.front_door import TIER_HEALTH_ENDPOINT, make_server
from anvil_serving.router.serve import RoutingBackend
from anvil_serving.router.tier_health import build_tier_health

TOKEN = "s3cr3t-router-token"
HEALTH = TIER_HEALTH_ENDPOINT


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
class _Profile:
    def decision(self, tier_id, work_class, *, is_cloud=False):
        return "allow"

    def score(self, tier_id, work_class):
        return 1.0


class _KeyedAvailability:
    """Tracked readiness keyed by serve id — the state routing already keeps.

    ``check`` never touches the network, so a tier reports its tracked status
    whether or not it was routed to recently (the idle-vs-down distinction).
    """

    def __init__(self, results: dict[str, AvailabilityResult]) -> None:
        self._results = results

    def check(self, tier) -> AvailabilityResult:
        return self._results.get(
            tier.id,
            AvailabilityResult(True, "ready", "availability_not_configured"),
        )


def _tier(tier_id: str, port: int, *, host: str = "127.0.0.1") -> Tier:
    return Tier(
        id=tier_id,
        base_url=f"http://{host}:{port}/v1",
        model=tier_id,
        dialect="openai",
        context_limit=131072,
        privacy="local",
        tool_support=True,
        auth_env="ANVIL_TEST_KEY",
    )


def _config(
    *tiers: Tier,
    purpose_models: tuple = (),
    audio_routes: tuple = (),
) -> RouterConfig:
    return RouterConfig(
        tiers=tuple(tiers),
        presets={"chat": tuple(t.id for t in tiers)},
        mapping_version="tier-health-test",
        verify_local_min=False,
        purpose_models=tuple(purpose_models),
        audio_routes=tuple(audio_routes),
    )


def _routing(config: RouterConfig, availability) -> RoutingBackend:
    backends = {t.id: StaticBackend(["ok"]) for t in config.tiers}
    return RoutingBackend(config, backends, _Profile(), availability=availability)


@contextmanager
def _server(backend, *, token: Optional[str] = TOKEN):
    httpd = make_server("127.0.0.1", 0, backend, auth_token=token)
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _get(host, port, path, *, token: Optional[str] = TOKEN):
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        headers = {} if token is None else {"Authorization": "Bearer " + token}
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, raw
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def test_missing_token_is_401_and_healthz_stays_open():
    """The snapshot needs the router bearer; only GET /healthz stays open."""
    routing = _routing(_config(_tier("heavy-local", 30000)), AlwaysAvailable())
    with _server(routing) as (host, port):
        status_noauth, _ = _get(host, port, HEALTH, token=None)
        status_badauth, _ = _get(host, port, HEALTH, token="wrong")
        status_ok, _ = _get(host, port, HEALTH)
        healthz_open, _ = _get(host, port, "/healthz", token=None)
    assert status_noauth == 401
    assert status_badauth == 401
    assert status_ok == 200
    assert healthz_open == 200


# --------------------------------------------------------------------------- #
# Shape / coverage
# --------------------------------------------------------------------------- #
def test_snapshot_has_a_row_per_configured_serve_with_roles():
    """One row per configured serve — llm tiers + purpose + audio — not only routed ones."""
    heavy = _tier("heavy-local", 30000)
    fast = _tier("fast-local", 30001)
    embed = PurposeModel(
        id="embed-serve", kind="embedding", model="bge-m3",
        base_url="http://127.0.0.1:30005/v1",
    )
    rerank = PurposeModel(
        id="rerank-serve", kind="rerank", model="bge-rerank",
        base_url="http://127.0.0.1:30006/v1",
    )
    stt = AudioRoute(
        id="dark-stt", purpose="stt", model="parakeet",
        base_url="http://127.0.0.1:30010/v1",
    )
    tts = AudioRoute(
        id="dark-tts", purpose="tts", model="kokoro",
        base_url="http://127.0.0.1:30011/v1", source_sample_rate=24_000,
    )
    config = _config(
        heavy, fast,
        purpose_models=(embed, rerank),
        audio_routes=(stt, tts),
    )
    routing = _routing(config, AlwaysAvailable())
    with _server(routing) as (host, port):
        status, raw = _get(host, port, HEALTH)
    assert status == 200
    body = json.loads(raw)
    rows = body["tiers"]
    # Every configured serve appears exactly once, in a stable order.
    by_id = {row["id"]: row for row in rows}
    assert set(by_id) == {
        "heavy-local", "fast-local", "embed-serve", "rerank-serve",
        "dark-stt", "dark-tts",
    }
    assert by_id["heavy-local"]["role"] == "llm"
    assert by_id["fast-local"]["role"] == "llm"
    assert by_id["embed-serve"]["role"] == "embeddings"
    assert by_id["rerank-serve"]["role"] == "rerank"
    assert by_id["dark-stt"]["role"] == "stt"
    assert by_id["dark-tts"]["role"] == "tts"
    # Every row carries the full, well-typed contract shape.
    for row in rows:
        assert set(row) == {"id", "role", "status", "last_check", "latency_ms", "reason"}
        assert row["status"] in ("up", "degraded", "down")


# --------------------------------------------------------------------------- #
# Tracked status: idle-but-down vs idle-but-up
# --------------------------------------------------------------------------- #
def test_tracked_unavailable_tier_reports_down_even_when_idle():
    """A tier the router tracks as unavailable is ``down`` though never routed to."""
    heavy = _tier("heavy-local", 30000)
    fast = _tier("fast-local", 30001)
    availability = _KeyedAvailability({
        "heavy-local": AvailabilityResult(
            False, "unavailable", "health_transport_URLError"
        ),
        "fast-local": AvailabilityResult(True, "ready", "health_passed"),
    })
    routing = _routing(_config(heavy, fast), availability)
    with _server(routing) as (host, port):
        status, raw = _get(host, port, HEALTH)
    assert status == 200
    by_id = {row["id"]: row for row in json.loads(raw)["tiers"]}
    assert by_id["heavy-local"]["status"] == "down"
    assert by_id["heavy-local"]["reason"] == "health_transport_URLError"


def test_idle_but_healthy_tier_reports_up_not_unknown():
    """A configured-but-idle healthy tier is ``up`` — never ``unknown``."""
    fast = _tier("fast-local", 30001)
    availability = _KeyedAvailability({
        "fast-local": AvailabilityResult(True, "ready", "health_passed"),
    })
    routing = _routing(_config(fast), availability)
    with _server(routing) as (host, port):
        status, raw = _get(host, port, HEALTH)
    assert status == 200
    (row,) = json.loads(raw)["tiers"]
    assert row["status"] == "up"
    assert row["status"] != "unknown"


def test_real_http_probe_stamps_latency_and_last_check_for_an_up_tier():
    """The freshness fields are real: an HTTP-probed healthy tier is up + timestamped."""
    tier = Tier(
        id="fast-local",
        base_url="http://127.0.0.1:30001/v1",
        model="fast-local",
        dialect="openai",
        context_limit=131072,
        privacy="local",
        tool_support=True,
        auth_env="ANVIL_TEST_KEY",
        health_path="/health",
    )
    config = _config(tier)

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def read(self, amount=-1):
            return b""

    availability = HttpHealthAvailability(
        config,
        opener=lambda request, timeout: _Resp(),
        clock=lambda: 100.0,
        wall_clock=lambda: 1_700_000_000.0,
    )
    snapshot = build_tier_health(config, availability)
    (row,) = snapshot["tiers"]
    assert row["status"] == "up"
    assert isinstance(row["latency_ms"], int) and row["latency_ms"] >= 0
    assert row["last_check"] == "2023-11-14T22:13:20.000Z"


# --------------------------------------------------------------------------- #
# No secrets in the body
# --------------------------------------------------------------------------- #
def test_no_serve_host_url_or_token_ever_appears_in_the_body():
    """A dirty reason (host:port + secret) is categorized; no host/URL/token leaks."""
    marker_host = "10.9.8.7"
    marker_port = "31337"
    secret = "SUPERSECRETUPSTREAMTOKEN"
    tier = _tier("heavy-local", 30000, host=marker_host)  # base_url carries the host
    availability = _KeyedAvailability({
        "heavy-local": AvailabilityResult(
            False,
            "unavailable",
            # A hostile/buggy availability impl leaking an endpoint + secret.
            f"connect to {marker_host}:{marker_port} failed token={secret}",
        ),
    })
    routing = _routing(_config(tier), availability)
    with _server(routing) as (host, port):
        status, raw = _get(host, port, HEALTH)
    assert status == 200
    text = raw.decode("utf-8")
    # The endpoint never echoes an upstream host, port, URL, or token.
    assert marker_host not in text
    assert marker_port not in text
    assert secret not in text
    assert "http://" not in text
    row = json.loads(raw)["tiers"][0]
    # Reason is replaced with a bounded, content-free category; status is down.
    assert row["reason"] == "unavailable"
    assert row["status"] == "down"


def test_identity_mismatch_reason_is_categorized_degraded():
    """A serving-but-wrong-model tier reads degraded; the safe category survives."""
    tier = _tier("heavy-local", 30000)
    availability = _KeyedAvailability({
        "heavy-local": AvailabilityResult(False, "unavailable", "identity_mismatch"),
    })
    routing = _routing(_config(tier), availability)
    with _server(routing) as (host, port):
        status, raw = _get(host, port, HEALTH)
    assert status == 200
    row = json.loads(raw)["tiers"][0]
    assert row["status"] == "degraded"
    assert row["reason"] == "identity_mismatch"


# --------------------------------------------------------------------------- #
# Additive: /health and /v1/decisions are unchanged
# --------------------------------------------------------------------------- #
def test_health_alias_advertises_the_new_route_and_decisions_still_work():
    routing = _routing(_config(_tier("heavy-local", 30000)), AlwaysAvailable())
    with _server(routing) as (host, port):
        h_status, h_raw = _get(host, port, "/health")
        d_status, d_raw = _get(host, port, "/v1/decisions?limit=1")
        hz_status, hz_raw = _get(host, port, "/healthz")
    assert h_status == 200
    routes = json.loads(h_raw)["routes"]
    assert HEALTH in routes
    # Pre-existing surface is intact.
    assert "/v1/decisions" in routes
    assert "/v1/messages" in routes
    assert json.loads(hz_raw)["status"] == "ok"
    # /v1/decisions still returns its own summary shape, unaffected.
    assert d_status == 200
    assert "records" in json.loads(d_raw)


def test_plain_backend_without_routing_returns_empty_tiers():
    """A non-routing backend has no configured serves -> a well-formed empty snapshot."""
    with _server(StaticBackend(["ok"])) as (host, port):
        status, raw = _get(host, port, HEALTH)
    assert status == 200
    assert json.loads(raw) == {"tiers": []}


# --------------------------------------------------------------------------- #
# Pure-unit: assembly + sanitization
# --------------------------------------------------------------------------- #
def test_build_tier_health_orders_tiers_then_purpose_then_audio():
    config = _config(
        _tier("heavy-local", 30000),
        purpose_models=(PurposeModel(
            id="embed", kind="embedding", model="m", base_url="http://127.0.0.1:30005/v1"
        ),),
        audio_routes=(AudioRoute(
            id="stt", purpose="stt", model="p", base_url="http://127.0.0.1:30010/v1"
        ),),
    )
    rows = build_tier_health(config, AlwaysAvailable())["tiers"]
    assert [r["id"] for r in rows] == ["heavy-local", "embed", "stt"]
    assert [r["role"] for r in rows] == ["llm", "embeddings", "stt"]
    # AlwaysAvailable performs no probe -> no freshness metadata is fabricated.
    for row in rows:
        assert row["status"] == "up"
        assert row["last_check"] is None
        assert row["latency_ms"] is None
