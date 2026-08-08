"""ADR-0033 observability hardening: mid-stream honesty, background prober,
restart-detectable metrics, and the honest token estimator."""
from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from anvil_serving.router.availability import (
    AvailabilityResult,
    BackgroundAvailabilityProber,
    HttpHealthAvailability,
)
from anvil_serving.router.config import ConfigError, load
from anvil_serving.router.internal import estimate_tokens
from anvil_serving.router.serve import build_server


_CONFIG = """\
[router]
{router_keys}

[[router.tiers]]
id = "primary"
base_url = "http://127.0.0.1:31002/v1"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "ANVIL_PRIMARY_KEY"
model = "primary-model"
health_path = "/health"

[router.model_routes]
llm.primary = "primary"
"""


def _config(tmp_path: Path, **router_keys) -> str:
    lines = []
    for key, value in router_keys.items():
        rendered = f'"{value}"' if isinstance(value, str) else value
        lines.append(f"{key} = {rendered}")
    path = tmp_path / "router.toml"
    path.write_text(_CONFIG.format(router_keys="\n".join(lines)), encoding="utf-8")
    return str(path)


# --- 4B: mid-stream failure honesty ----------------------------------------


class ExplodingBackend:
    def generate(self, request):
        yield "first "
        yield "second "
        raise RuntimeError("upstream exploded with secret detail")


@pytest.mark.parametrize("path,accept", [("/v1/chat/completions", "openai"), ("/v1/messages", "anthropic")])
def test_mid_stream_failure_emits_terminal_error_and_valid_chunked_close(tmp_path, path, accept):
    config_path = _config(tmp_path)
    server = build_server(
        config_path, host="127.0.0.1", port=0, backends={"primary": ExplodingBackend()}
    )
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(host, port, timeout=10)
        body = {
            "model": "llm.primary",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        if path == "/v1/messages":
            body["max_tokens"] = 128
        connection.request(
            "POST", path, json.dumps(body), {"Content-Type": "application/json"}
        )
        response = connection.getresponse()
        raw = response.read()  # raises IncompleteRead if chunked framing is broken
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    text = raw.decode("utf-8", errors="replace")
    assert response.status == 200
    assert "first" in text
    if accept == "openai":
        assert '"error"' in text
        assert "[DONE]" not in text  # failure is distinguishable from completion
    else:
        assert "event: error" in text
    # Server-side detail never reaches the wire.
    assert "secret detail" not in text
    assert "RuntimeError" not in text


# --- 4A: background availability prober -------------------------------------


class _FakeTier:
    """Duck-typed tier for direct prober tests."""

    def __init__(self, tier_id="primary"):
        self.id = tier_id
        self.privacy = "local"
        self.health_path = "/health"
        self.base_url = "http://127.0.0.1:31002/v1"
        self.model_identity = False
        self.model = "primary-model"
        self.auth_env = "ANVIL_PRIMARY_KEY"


def _availability(tmp_path, opener, clock):
    config = load(_config(tmp_path))
    return HttpHealthAvailability(config, opener=opener, clock=clock, wall_clock=clock)


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def getcode(self):
        return self.status


def test_prober_backoff_grows_and_caps_then_resets():
    inner_calls = []

    class Inner:
        def probe_now(self, tier):
            inner_calls.append(tier.id)
            return AvailabilityResult(False, "unavailable", "health_transport_x")

        def cached(self, tier_id):
            return None

        def check(self, tier):
            return AvailabilityResult(False, "unavailable", "inline")

        def invalidate(self, tier_id=None):
            return None

    prober = BackgroundAvailabilityProber(
        Inner(), [_FakeTier()], interval=1.0, backoff_max=4.0, jitter=lambda: 1.0
    )
    delays = [prober._next_delay("primary", available=False) for _ in range(5)]
    assert delays == [1.0, 2.0, 4.0, 4.0, 4.0]  # doubles then caps
    assert prober._next_delay("primary", available=True) == 1.0  # success resets


def test_prober_serves_fresh_cache_and_falls_through_when_stale(tmp_path):
    clock = [0.0]
    probes = []

    def opener(request, timeout):
        probes.append(request.full_url)
        return _Response()

    inner = _availability(tmp_path, opener, lambda: clock[0])
    tier = load(_config(tmp_path)).tiers[0]
    prober = BackgroundAvailabilityProber(
        inner, [tier], interval=5.0, staleness=15.0, jitter=lambda: 1.0
    )

    inner.probe_now(tier)
    assert len(probes) == 1
    clock[0] = 10.0  # within staleness: served from cache, no new probe
    assert prober.check(tier).available is True
    assert len(probes) == 1
    clock[0] = 30.0  # stale: falls through to the inline single-flight probe
    assert prober.check(tier).available is True
    assert len(probes) == 2


def test_inline_single_flight_losers_get_last_known_or_fail_closed(tmp_path):
    clock = [0.0]
    release = threading.Event()
    started = threading.Event()

    def opener(request, timeout):
        started.set()
        release.wait(timeout=10)
        return _Response()

    inner = _availability(tmp_path, opener, lambda: clock[0])
    tier = load(_config(tmp_path)).tiers[0]

    winner_result = {}

    def winner():
        winner_result["result"] = inner.check(tier)

    thread = threading.Thread(target=winner, daemon=True)
    thread.start()
    assert started.wait(timeout=5)
    # No prior result: the concurrent caller fails closed instead of stacking
    # a duplicate probe behind the same struggling serve.
    loser = inner.check(tier)
    assert loser.available is False
    assert loser.reason == "probe_pending"
    release.set()
    thread.join(timeout=5)
    assert winner_result["result"].available is True


def test_background_mode_builds_and_stops(tmp_path):
    config_path = _config(tmp_path, availability_prober="background")
    server = build_server(config_path, host="127.0.0.1", port=0)
    try:
        availability = server.anvil_availability
        assert isinstance(availability, BackgroundAvailabilityProber)
    finally:
        server.anvil_availability.stop()
        server.server_close()


def test_prober_config_keys_are_validated(tmp_path):
    with pytest.raises(ConfigError, match="availability_prober"):
        load(_config(tmp_path, availability_prober="sometimes"))
    with pytest.raises(ConfigError, match="staleness"):
        load(
            _config(
                tmp_path,
                availability_probe_interval=10,
                availability_probe_staleness=5,
            )
        )


# --- 4C: restart-detectable metrics ------------------------------------------


def test_metrics_expose_process_start_time_and_buffer_capacity(tmp_path):
    config_path = _config(tmp_path)

    class OkBackend:
        def generate(self, request):
            yield "ok"

    server = build_server(
        config_path, host="127.0.0.1", port=0, backends={"primary": OkBackend()}
    )
    try:
        rendered = server.anvil_routing.prometheus_metrics({})
    finally:
        server.server_close()
    assert "anvil_router_process_start_time_seconds " in rendered
    assert "anvil_router_decision_buffer_capacity 10000" in rendered
    start_line = next(
        line
        for line in rendered.splitlines()
        if line.startswith("anvil_router_process_start_time_seconds ")
    )
    assert float(start_line.split()[-1]) <= time.time() + 1


# --- 4D: honest token estimator ----------------------------------------------


def test_estimate_tokens_floors_at_bytes_over_four():
    # Prose: the word count still dominates for ordinary English.
    assert estimate_tokens(["one two three"]) == 3
    # CJK: one "word" by whitespace, but bytes/4 reflects real token cost.
    cjk = "模型服务" * 8  # 32 chars, 96 utf-8 bytes
    assert estimate_tokens([cjk]) == 24
    # Base64-ish blobs: single word, large byte count.
    blob = "A" * 4000
    assert estimate_tokens([blob]) == 1000
    assert estimate_tokens([]) == 0
