"""Tests for the T012 ``anvil-serving serve`` verb (config -> backends -> front door).

Hermetic and stdlib-only. The end-to-end tests start the REAL front door (built
by :func:`anvil_serving.router.serve.build_server` from ``configs/example.toml``)
on an ephemeral ``127.0.0.1`` port in a background thread, with **injected
per-tier backends** so NO real upstream network is touched, issue one request
over ``http.client``, assert the streamed SSE, and tear the server down.

Coverage:
  * (a) ``serve --help`` exits 0 and documents ``--config``.
  * (b) ``configs/example.toml`` -> a running front door; one request streams a
        correct response, and routing actually composes (a ``chat`` request lands
        on ``fast-local``; a ``planning`` request is gated to ``cloud``).
  * (c) a drop-in-time smoke: measure + record (print + assert finite) the
        elapsed time from server build/start to the first served response.
  * tier -> backend mapping (cloud -> CloudBackend, local -> RelayBackend) and
    the local RelayBackend relaying via an injected transport with NO creds.
"""

from __future__ import annotations

import http.client
import json
import math
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from anvil_serving import cli
from anvil_serving.router import serve as serve_mod
from anvil_serving.router.backends import CloudBackend, StaticBackend
from anvil_serving.router.config import Tier
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.serve import (
    RelayBackend,
    build_backend_for_tier,
    build_backends,
    build_server,
)

CONFIG = str(Path(__file__).resolve().parents[2] / "configs" / "example.toml")


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
@contextmanager
def running(httpd):
    """Run an already-built front door on a daemon thread; yield ``(host, port)``."""
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
        conn.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
        resp = conn.getresponse()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, resp.read()
    finally:
        conn.close()


def _get(host: str, port: int, path: str) -> Tuple[int, bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def parse_openai_content(raw: bytes) -> str:
    """Reassemble the assistant content from an OpenAI streamed SSE body."""
    text = raw.decode("utf-8")
    payloads: List[str] = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if block:
            assert block.startswith("data: "), block
            payloads.append(block[len("data: ") :])
    assert payloads[-1] == "[DONE]", payloads
    chunks = [json.loads(p) for p in payloads[:-1]]
    return "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)


def _distinct_backends() -> Dict[str, StaticBackend]:
    """Inject a DISTINCT static backend per tier id so the test can tell which
    tier actually served (proving the routing composition, not just a passthrough)."""
    return {
        "fast-local": StaticBackend(["Hel", "lo"]),          # -> "Hello"
        "heavy-local": StaticBackend(["heavy-", "served"]),  # -> "heavy-served"
        "cloud": StaticBackend(["from-", "cloud"]),          # -> "from-cloud"
    }


# --------------------------------------------------------------------------- #
# (a) serve --help
# --------------------------------------------------------------------------- #
def test_serve_help_exits_zero_and_mentions_config(capsys):
    # argparse --help raises SystemExit(0) after printing usage.
    with pytest.raises(SystemExit) as exc:
        cli.main(["serve", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--config" in out
    assert "serve" in out


def test_serve_subcommand_listed_in_top_level_help(capsys):
    rc = cli.main(["--help"])
    assert rc == 0
    assert "serve" in capsys.readouterr().out


def test_serve_requires_config(capsys):
    # Missing the required --config: argparse exits non-zero (usage error).
    with pytest.raises(SystemExit) as exc:
        cli.main(["serve"])
    assert exc.value.code != 0


# --------------------------------------------------------------------------- #
# (b) config -> running front door + routing composition
# --------------------------------------------------------------------------- #
def test_serve_streams_chat_request_via_fast_local():
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=_distinct_backends())
    # The example config binds exactly its three tiers.
    assert set(httpd.anvil_tiers) == {"fast-local", "heavy-local", "cloud"}
    with running(httpd) as (host, port):
        status, headers, raw = _post(
            host, port, "/v1/chat/completions",
            {"model": "chat", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        )
    assert status == 200
    assert headers.get("content-type") == "text/event-stream"
    # "chat" preset -> candidates [fast-local, cloud]; first bound is fast-local.
    assert parse_openai_content(raw) == "Hello"


def test_serve_gates_planning_request_to_cloud():
    """The quality gate (T005) is wired: planning never routes to a local tier."""
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=_distinct_backends())
    with running(httpd) as (host, port):
        status, _headers, raw = _post(
            host, port, "/v1/chat/completions",
            {"model": "planning", "messages": [{"role": "user", "content": "plan it"}],
             "stream": True},
        )
    assert status == 200
    # planning -> ["cloud"] only (locals are denied) -> served by the cloud backend.
    assert parse_openai_content(raw) == "from-cloud"


def test_serve_non_streaming_request():
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=_distinct_backends())
    with running(httpd) as (host, port):
        status, headers, raw = _post(
            host, port, "/v1/chat/completions",
            {"model": "chat", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert status == 200
    assert headers.get("content-type") == "application/json"
    body = json.loads(raw)
    assert body["choices"][0]["message"]["content"] == "Hello"


def test_serve_advertises_presets_on_v1_models():
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=_distinct_backends())
    with running(httpd) as (host, port):
        status, raw = _get(host, port, "/v1/models")
    assert status == 200
    ids = {m["id"] for m in json.loads(raw)["data"]}
    # The canonical intent vocabulary is advertised (the presets ARE the models).
    assert {"chat", "planning", "quick-edit", "review", "long-context"} <= ids


def test_serve_skips_cloud_tier_without_creds_but_still_starts():
    """AC1 without secrets: an unkeyed cloud tier is skipped (not fatal); the
    local tiers still bind and the front door starts."""
    # env has NO ANTHROPIC_API_KEY -> CloudBackend construction fails -> skipped.
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, env={})
    try:
        assert set(httpd.anvil_tiers) == {"fast-local", "heavy-local"}
        # cloud is gone, so the safer default falls back to the first local tier.
        assert httpd.anvil_default_tier == "fast-local"
    finally:
        httpd.server_close()


# --------------------------------------------------------------------------- #
# (c) drop-in-time smoke
# --------------------------------------------------------------------------- #
def test_drop_in_time_smoke():
    """Measure + record the elapsed time from server build/start to the first
    served response — the pip-install-to-first-served-request 'drop-in' time."""
    backends = _distinct_backends()
    t0 = time.perf_counter()
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=backends)
    with running(httpd) as (host, port):
        status, _headers, raw = _post(
            host, port, "/v1/chat/completions",
            {"model": "chat", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        )
        elapsed = time.perf_counter() - t0
    assert status == 200
    assert parse_openai_content(raw) == "Hello"
    # Record it (visible under `pytest -s`) and assert it is a finite, sane number.
    print(f"\n[drop-in-time] build+start -> first served response: {elapsed * 1000:.1f} ms")
    assert math.isfinite(elapsed)
    assert 0.0 <= elapsed < 30.0


# --------------------------------------------------------------------------- #
# tier -> backend mapping + local relay (the new wiring)
# --------------------------------------------------------------------------- #
def _local_openai_tier() -> Tier:
    return Tier(
        id="fast-local", base_url="http://127.0.0.1:30001/v1", dialect="openai",
        context_limit=32768, privacy="local", tool_support=True,
        auth_env="ANVIL_FAST_LOCAL_KEY",
    )


def _cloud_anthropic_tier() -> Tier:
    return Tier(
        id="cloud", base_url="https://api.anthropic.com", dialect="anthropic",
        context_limit=200000, privacy="cloud", tool_support=True,
        auth_env="ANVIL_TEST_CLOUD_KEY",
    )


def test_build_backend_for_tier_maps_privacy_to_backend_kind():
    relay = build_backend_for_tier(_local_openai_tier(), env={})
    assert isinstance(relay, RelayBackend)
    cloud = build_backend_for_tier(
        _cloud_anthropic_tier(), env={"ANVIL_TEST_CLOUD_KEY": "sk-test-DEADBEEF"}
    )
    assert isinstance(cloud, CloudBackend) and not isinstance(cloud, RelayBackend)


def test_build_backends_skips_unkeyed_cloud_records_reason():
    from anvil_serving.router.config import load

    config = load(CONFIG)
    backends, skipped = build_backends(config, env={})
    assert set(backends) == {"fast-local", "heavy-local"}
    skipped_ids = {tid for tid, _reason in skipped}
    assert skipped_ids == {"cloud"}
    assert any("ANTHROPIC_API_KEY" in reason for _tid, reason in skipped)


def test_relay_backend_relays_without_creds():
    """A local relay needs no key: it POSTs to base_url and yields the reply,
    and sends NO Authorization header when the auth env var is unset."""
    captured: Dict[str, object] = {}

    def fake_transport(url, *, data, headers, timeout):
        captured["url"] = url
        captured["headers"] = dict(headers)
        captured["body"] = json.loads(data)
        return json.dumps(
            {"choices": [{"message": {"content": "relayed hello"}}]}
        ).encode("utf-8")

    relay = build_backend_for_tier(_local_openai_tier(), env={}, transport=fake_transport)
    req = InternalRequest(model="chat", messages=[Message("user", "hi")], stream=False)
    out = "".join(relay.generate(req))

    assert out == "relayed hello"
    assert captured["url"] == "http://127.0.0.1:30001/v1/chat/completions"
    assert "Authorization" not in captured["headers"]  # no key -> unauthenticated


def test_relay_backend_forwards_key_when_present():
    captured: Dict[str, object] = {}

    def fake_transport(url, *, data, headers, timeout):
        captured["headers"] = dict(headers)
        return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

    relay = build_backend_for_tier(
        _local_openai_tier(),
        env={"ANVIL_FAST_LOCAL_KEY": "  local-secret  "},  # trimmed like CloudBackend
        transport=fake_transport,
    )
    list(relay.generate(InternalRequest(model="chat", messages=[Message("user", "hi")])))
    assert captured["headers"].get("Authorization") == "Bearer local-secret"
