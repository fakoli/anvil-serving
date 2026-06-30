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
  * the QUALITY GATE is never bypassed by availability: when the only gated
    candidate is unbound (planning with cloud unkeyed), the request gets a
    503-style error envelope naming the work class + unbound candidates — NOT a
    response from an out-of-gate local tier.
  * tier -> backend mapping (cloud -> CloudBackend, local -> RelayBackend), the
    local RelayBackend relaying via an injected transport with NO creds, and the
    router-package namespace resolving as intended (no ``serve`` shadow).
"""

from __future__ import annotations

import http.client
import json
import math
import threading
import time
import types
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
    RoutingBackend,
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


def _local_only_backends() -> Dict[str, StaticBackend]:
    """Bind ONLY the local tiers — cloud is unbound (the default dev-machine
    state when ANTHROPIC_API_KEY is unset). Used to prove the quality gate is
    not bypassed when the only gated candidate (cloud, for planning) is missing."""
    return {
        "fast-local": StaticBackend(["Hel", "lo"]),          # -> "Hello"
        "heavy-local": StaticBackend(["heavy-", "served"]),  # -> "heavy-served"
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
    finally:
        httpd.server_close()


# --------------------------------------------------------------------------- #
# MUST-FIX: availability never bypasses the quality gate
# --------------------------------------------------------------------------- #
def test_planning_with_cloud_unbound_returns_503_not_local_streaming():
    """A streaming ``planning`` request whose only gated tier (cloud) is unbound
    must get a clean 503 error envelope — NOT a 200 served from the
    out-of-gate ``fast-local`` tier.

    The 503 message is intentionally generic (internal tier names and work-class
    identifiers are logged server-side, not disclosed to the caller).
    """
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=_local_only_backends())
    assert set(httpd.anvil_tiers) == {"fast-local", "heavy-local"}
    with running(httpd) as (host, port):
        status, headers, raw = _post(
            host, port, "/v1/chat/completions",
            {"model": "planning", "messages": [{"role": "user", "content": "plan"}],
             "stream": True},
        )
    assert status == 503, (status, raw)
    # An error envelope, NOT a streamed completion from a local tier.
    assert headers.get("content-type") == "application/json"
    body = json.loads(raw)
    assert body["error"]["type"] == "service_unavailable"
    # Internal tier names / work-class must NOT be disclosed to the client
    # (they are logged to stderr server-side instead).
    raw_text = raw.decode("utf-8")
    assert "cloud" not in raw_text        # no tier name in response
    assert "planning" not in raw_text     # no work-class in response
    assert "Hello" not in raw_text        # fast-local did NOT serve it


def test_planning_with_cloud_unbound_returns_503_non_streaming():
    """Non-streaming variant: same quality-gate enforcement, same generic message."""
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=_local_only_backends())
    with running(httpd) as (host, port):
        status, headers, raw = _post(
            host, port, "/v1/chat/completions",
            {"model": "planning", "messages": [{"role": "user", "content": "plan"}]},
        )
    assert status == 503, (status, raw)
    body = json.loads(raw)
    assert body["error"]["type"] == "service_unavailable"
    # Generic message — no internal names.
    raw_text = raw.decode("utf-8")
    assert "planning" not in raw_text
    assert "cloud" not in raw_text


def test_gate_allowed_bound_tier_still_serves_when_cloud_unbound():
    """A class whose gated candidate IS bound still serves normally: ``chat`` ->
    [fast-local, cloud]; fast-local is bound and gate-allowed -> it serves."""
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=_local_only_backends())
    with running(httpd) as (host, port):
        status, _headers, raw = _post(
            host, port, "/v1/chat/completions",
            {"model": "chat", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        )
    assert status == 200
    assert parse_openai_content(raw) == "Hello"



# --------------------------------------------------------------------------- #
# Fix #2: _tier_verdict stale-allow downgrade applies to custom presets too
# --------------------------------------------------------------------------- #
def test_tier_verdict_stale_allow_custom_preset_downgrades():
    """Unit-level: a stale 'allow' entry for (tier, None) — a custom preset key
    — must be downgraded to 'allow-with-verify' by _tier_verdict, consistent
    with profile_store.decision().  Before the fix, _tier_verdict short-circuited
    to 'allow' for work_class=None, bypassing the stale check.
    """
    from anvil_serving.router.config import load
    from anvil_serving.router.profile_store import ProfileEntry, ProfileStore

    config = load(CONFIG)
    stale_profile = ProfileStore({
        ("fast-local", None): ProfileEntry("allow", 0.8, 5, None, stale=True),
    })
    routing = RoutingBackend(config, {}, stale_profile)
    # A stale allow for (tier, None) must become allow-with-verify.
    verdict = routing._tier_verdict("fast-local", None)
    assert verdict == "allow-with-verify", (
        f"_tier_verdict returned {verdict!r} for stale allow on custom preset; "
        f"expected 'allow-with-verify'"
    )

    # Non-stale allow stays allow.
    fresh_profile = ProfileStore({
        ("fast-local", None): ProfileEntry("allow", 0.8, 5, None, stale=False),
    })
    routing2 = RoutingBackend(config, {}, fresh_profile)
    assert routing2._tier_verdict("fast-local", None) == "allow"

    # An unmeasured (tier, None) pair also returns allow (default behaviour).
    empty_profile = ProfileStore({})
    routing3 = RoutingBackend(config, {}, empty_profile)
    assert routing3._tier_verdict("fast-local", None) == "allow"


# --------------------------------------------------------------------------- #
# router-package namespace resolves (no `serve` shadow)
# --------------------------------------------------------------------------- #
def test_router_namespace_has_no_serve_shadow():
    import anvil_serving.router as r
    from anvil_serving.router import front_door

    # `router.serve` is the T012 SUBMODULE (not the front_door function).
    assert isinstance(serve_mod, types.ModuleType)
    assert r.serve is serve_mod
    # The T012 launcher is exported under a non-colliding name.
    assert callable(r.serve_config) and r.serve_config is serve_mod.serve
    # The T001 front-door launcher is still reachable + callable via its module.
    assert callable(front_door.serve)
    # make_server is still exported at the package level.
    assert callable(r.make_server)


def test_relay_backend_inherits_cloudbackend_attrs():
    """Issue-3 fix: RelayBackend delegates to super().__init__ so it inherits the
    CloudBackend attribute set instead of hand-copying it (future-proof)."""
    relay = build_backend_for_tier(_local_openai_tier(), env={})
    for attr in ("_tier", "_key", "_timeout", "_transport"):
        assert hasattr(relay, attr), attr
    assert relay._key == ""  # no creds resolved -> empty, auth-optional


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


# --------------------------------------------------------------------------- #
# Fix 1: public-bind warning (chore/harden-exposure)
# --------------------------------------------------------------------------- #
from anvil_serving.router.serve import _warn_if_public_bind  # noqa: E402


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",   # IPv4 loopback
        "::1",         # IPv6 loopback
    ],
)
def test_warn_if_public_bind_loopback_is_silent(host, capsys):
    """Loopback addresses must NOT emit a warning."""
    _warn_if_public_bind(host)
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",           # wildcard
        "::",                # IPv6 wildcard
        "",                  # empty string -> wildcard
        "192.168.1.10",      # LAN IP
        "10.0.0.1",          # private but non-loopback
        "example.com",       # DNS name — cannot confirm loopback
    ],
)
def test_warn_if_public_bind_non_loopback_emits_warning(host, capsys):
    """Non-loopback / wildcard / DNS hosts must emit a prominent stderr warning."""
    _warn_if_public_bind(host)
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "authentication" in err.lower()


def test_warn_if_public_bind_warning_names_the_host(capsys):
    """The warning should name the host so the operator knows what was bound."""
    _warn_if_public_bind("0.0.0.0")
    err = capsys.readouterr().err
    assert "0.0.0.0" in err


def test_warn_if_public_bind_mentions_credentials(capsys):
    """The warning must mention credential exposure so the risk is unambiguous."""
    _warn_if_public_bind("0.0.0.0")
    err = capsys.readouterr().err
    assert "credential" in err.lower() or "cloud" in err.lower()
