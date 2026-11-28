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

from anvil_serving.router.availability import (
    AlwaysAvailable,
    AvailabilityResult,
)
from tests.router.helpers import StaticBackend
from anvil_serving.router.config import AudioRoute, PurposeModel, RealtimeRoute, RouterConfig, Tier
from anvil_serving.router.front_door import TIER_HEALTH_ENDPOINT, make_server
from anvil_serving.router.realtime import RealtimeGateway
from anvil_serving.router.serve import RoutingBackend

TOKEN = "s3cr3t-router-token"
HEALTH = TIER_HEALTH_ENDPOINT


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
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
    realtime_routes: tuple = (),
) -> RouterConfig:
    return RouterConfig(
        tiers=tuple(tiers),
        model_routes={f"llm.{t.id}": t.id for t in tiers},
        purpose_models=tuple(purpose_models),
        audio_routes=tuple(audio_routes),
        realtime_routes=tuple(realtime_routes),
    )


def _routing(config: RouterConfig, availability) -> RoutingBackend:
    backends = {t.id: StaticBackend(["ok"]) for t in config.tiers}
    return RoutingBackend(config, backends, availability=availability)


@contextmanager
def _server(backend, *, token: Optional[str] = TOKEN, realtime=None):
    httpd = make_server("127.0.0.1", 0, backend, auth_token=token, realtime=realtime)
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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


# --------------------------------------------------------------------------- #
# Shape / coverage
# --------------------------------------------------------------------------- #
def test_snapshot_has_a_row_per_configured_serve_with_roles():
    """One row per configured serve — llm tiers + purpose + audio — not only routed ones."""
    heavy = _tier("primary-local", 30000)
    fast = _tier("auxiliary-local", 30001)
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
        "primary-local", "auxiliary-local", "embed-serve", "rerank-serve",
        "dark-stt", "dark-tts",
    }
    assert by_id["primary-local"]["role"] == "llm"
    assert by_id["auxiliary-local"]["role"] == "llm"
    assert by_id["embed-serve"]["role"] == "embeddings"
    assert by_id["rerank-serve"]["role"] == "rerank"
    assert by_id["dark-stt"]["role"] == "stt"
    assert by_id["dark-tts"]["role"] == "tts"
    # Every row carries the full, well-typed contract shape.
    for row in rows:
        assert set(row) == {"id", "role", "status", "last_check", "latency_ms", "reason"}
        assert row["status"] in ("up", "degraded", "down")


def test_snapshot_uses_the_bound_realtime_gateway_health_for_every_route():
    ready = RealtimeRoute(
        id="voice-ready", base_url="http://127.0.0.1:30111/v1", health_path="/ready",
    )
    down = RealtimeRoute(
        id="voice-down", base_url="http://127.0.0.1:30112/v1", health_path="/down",
    )
    calls = []
    realtime = RealtimeGateway(
        (ready, down), max_concurrency=1,
        probe=lambda url, timeout: calls.append(url) or (200 if url.endswith("/ready") else 503),
    )
    routing = _routing(_config(_tier("primary-local", 30000), realtime_routes=(ready, down)), AlwaysAvailable())

    with _server(routing, realtime=realtime) as (host, port):
        status, raw = _get(host, port, HEALTH)

    assert status == 200
    rows = {row["id"]: row for row in json.loads(raw)["tiers"]}
    assert rows["voice-ready"]["role"] == "realtime"
    assert rows["voice-ready"]["status"] == "up"
    assert rows["voice-down"]["status"] == "down"
    assert calls == ["http://127.0.0.1:30111/ready", "http://127.0.0.1:30112/down"]


# --------------------------------------------------------------------------- #
# Tracked status: idle-but-down vs idle-but-up
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# No secrets in the body
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Additive: /health and /v1/decisions are unchanged
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Pure-unit: assembly + sanitization
# --------------------------------------------------------------------------- #
