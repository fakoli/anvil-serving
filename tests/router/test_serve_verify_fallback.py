"""C3-on-serve tests: verify + commit-window + fallback wired into RoutingBackend.generate.

These tests prove the SERVE PATH enforces the quality gate end-to-end:

  C3-allow-with-verify-FAIL: a fail-prone local tier whose output fails a
  structural verifier must NOT deliver any local token to the client; the
  response must come from the cloud fallback, and the decision log must record
  the fallback.

  C3-allow-with-verify-PASS: a fail-prone local tier whose output PASSES verify
  is committed and delivered; cloud is not reached.

  C3-allow: a trusted (allow) tier is streamed directly without running any
  verifier; even content that would fail verify is delivered.

  C3-non-streaming: the non-streaming path (stream=False) also buffers+verifies
  and falls back on a verify-failure.

All tests go through the REAL front door (build_server with injected backends
and a custom ProfileStore), so the full HTTP path is exercised.

Hermetic and stdlib-only.
"""
from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict


from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.profile_store import ProfileEntry, ProfileStore
from anvil_serving.router.serve import build_server

CONFIG = str(Path(__file__).resolve().parents[2] / "configs" / "example.toml")

# Single-tier local-only configs (no fallback tier): used for T004 keyless-
# exhaustion tests.  An allow-with-verify miss on these configs exhausts
# immediately; the front door must return exhaustion_status with nothing
# streamed from the failing local tier (C3 on the keyless path).
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONFIG_SINGLE_TIER = str(_FIXTURES / "single-tier-local.toml")
CONFIG_SINGLE_TIER_424 = str(_FIXTURES / "single-tier-local-424.toml")

# Local backend output that FAILS the structural verifier chain:
# a fenced Python block containing a syntax error trips CodeParses.
LOCAL_FAIL_CONTENT = "```python\ndef broken_local_fn(\n```"

# Local backend output that PASSES the default verifier chain.
LOCAL_PASS_CONTENT = "local-good-response"

# Cloud fallback output — distinct from both local variants.
CLOUD_CONTENT = "cloud-fallback-response"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _avw_profile() -> ProfileStore:
    """Custom profile: fast-local is allow-with-verify for chat work class.

    The default profile marks fast-local as allow for chat; this override
    makes it fail-prone so the commit-window path is exercised.
    """
    return ProfileStore(
        {("fast-local", "chat"): ProfileEntry("allow-with-verify", 0.6, 5, None)}
    )


@contextmanager
def running(httpd):
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _post(host, port, path, body):
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
        resp = conn.getresponse()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, resp.read()
    finally:
        conn.close()


def _parse_content(raw: bytes) -> str:
    """Reassemble assistant content from an OpenAI-SSE body."""
    text = raw.decode("utf-8")
    payloads = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if block:
            assert block.startswith("data: "), block
            payloads.append(block[len("data: "):])
    assert payloads[-1] == "[DONE]", payloads
    chunks = [json.loads(p) for p in payloads[:-1]]
    return "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)


# --------------------------------------------------------------------------- #
# C3-FAIL: allow-with-verify local FAILS verify -> fallback is served, no local tokens
# --------------------------------------------------------------------------- #
def test_c3_avw_fail_delivers_fallback_not_local_streaming():
    """CORE INVARIANT (streaming): allow-with-verify local that fails verify
    must deliver ZERO local tokens; the next-candidate fallback is served instead,
    and the decision log records the fallback.

    In the local-only default config chat -> [fast-local, heavy-local]; heavy-local
    is the fallback (not cloud — T001 ships no cloud tier by default).
    """
    backends: Dict[str, StaticBackend] = {
        "fast-local": StaticBackend([LOCAL_FAIL_CONTENT]),
        "heavy-local": StaticBackend([CLOUD_CONTENT]),  # the fallback candidate
    }
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=backends, profile=_avw_profile())
    with running(httpd) as (host, port):
        status, headers, raw = _post(
            host, port, "/v1/chat/completions",
            {"model": "chat", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        )

    assert status == 200, (status, raw)
    content = _parse_content(raw)

    # Fallback output must be delivered.
    assert content == CLOUD_CONTENT, f"expected fallback content, got: {content!r}"

    # No local token may appear anywhere in the delivered HTTP body.
    raw_text = raw.decode("utf-8")
    assert "broken_local_fn" not in raw_text, "local token leaked into response"
    assert LOCAL_FAIL_CONTENT not in raw_text, "local content leaked into response"

    # Decision log must record that a fallback occurred.
    record = httpd.anvil_routing._decision_log.last
    assert record is not None, "no decision record written"
    assert record.fell_back, "decision log did not record fell_back=True"
    assert record.served_tier == "heavy-local", (
        f"served_tier should be heavy-local, got {record.served_tier!r}"
    )


# --------------------------------------------------------------------------- #
# C3-PASS: allow-with-verify local PASSES verify -> local committed + delivered
# --------------------------------------------------------------------------- #
def test_c3_avw_pass_delivers_local_not_cloud_streaming():
    """PASS path: allow-with-verify local that PASSES verify is committed and
    delivered; the fallback backend is never reached."""
    backends: Dict[str, StaticBackend] = {
        "fast-local": StaticBackend([LOCAL_PASS_CONTENT]),
        "heavy-local": StaticBackend([CLOUD_CONTENT]),
    }
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=backends, profile=_avw_profile())
    with running(httpd) as (host, port):
        status, headers, raw = _post(
            host, port, "/v1/chat/completions",
            {"model": "chat", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        )

    assert status == 200, (status, raw)
    content = _parse_content(raw)

    # Local output must be committed and delivered.
    assert content == LOCAL_PASS_CONTENT, f"expected local content, got: {content!r}"

    # Cloud output must not appear.
    assert CLOUD_CONTENT not in raw.decode("utf-8"), "cloud content unexpectedly appeared"

    # Decision log: no fallback.
    record = httpd.anvil_routing._decision_log.last
    assert record is not None, "no decision record written"
    assert not record.fell_back, "unexpected fallback recorded"
    assert record.served_tier == "fast-local"


# --------------------------------------------------------------------------- #
# C3-ALLOW: allow tier -> streamed directly, no *full* verifier chain invoked
#
# genericity:T004 superseded the old "allow == zero verification" contract for
# a LOCAL tier specifically: a privacy=local tier under "allow" now runs a
# MINIMAL commit-window (NonEmptyContent/NotTruncated only) so an empty local
# 200 is caught and escalated instead of silently reaching the client (see
# test_serve_fallback.py for the dedicated T004 coverage). Good, well-formed
# content from the first "allow" candidate is still served from that tier —
# the minimal check does not change the common case, only the failure case.
# --------------------------------------------------------------------------- #
def test_c3_allow_good_content_streams_from_first_tier():
    """An 'allow' tier serving well-formed content is served from that FIRST
    tier, not escalated — the T004 minimal-verify safety net is a no-op on a
    normal response (only the empty/truncated failure mode is caught)."""
    backends: Dict[str, StaticBackend] = {
        "fast-local": StaticBackend(["fast-local-response"]),
        "heavy-local": StaticBackend([CLOUD_CONTENT]),  # must NOT be reached
    }
    from anvil_serving.router.profile_store import default_profile
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=backends,
                         profile=default_profile())
    with running(httpd) as (host, port):
        status, _headers, raw = _post(
            host, port, "/v1/chat/completions",
            {"model": "chat", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        )

    assert status == 200, (status, raw)
    content = _parse_content(raw)
    assert content == "fast-local-response"
    assert CLOUD_CONTENT not in raw.decode("utf-8"), "escalated when it should not have"


# --------------------------------------------------------------------------- #
# C3-NON-STREAMING: stream=False also verifies + falls back
# --------------------------------------------------------------------------- #
def test_c3_avw_fail_non_streaming_falls_back_to_next_candidate():
    """Non-streaming path (stream=False): allow-with-verify local that fails
    verify must still be discarded; the next-candidate response is delivered."""
    backends: Dict[str, StaticBackend] = {
        "fast-local": StaticBackend([LOCAL_FAIL_CONTENT]),
        "heavy-local": StaticBackend([CLOUD_CONTENT]),
    }
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=backends, profile=_avw_profile())
    with running(httpd) as (host, port):
        status, headers, raw = _post(
            host, port, "/v1/chat/completions",
            {"model": "chat", "messages": [{"role": "user", "content": "hi"}]},
            # no "stream" key -> defaults to non-streaming
        )

    assert status == 200, (status, raw)
    assert headers.get("content-type") == "application/json"
    body = json.loads(raw)
    content = body["choices"][0]["message"]["content"]

    assert content == CLOUD_CONTENT, f"expected fallback content, got: {content!r}"
    assert "broken_local_fn" not in raw.decode("utf-8"), "local token leaked"

    record = httpd.anvil_routing._decision_log.last
    assert record is not None
    assert record.fell_back
    assert record.served_tier == "heavy-local"


# --------------------------------------------------------------------------- #
# SECURITY (end-to-end): a caller PIN cannot bypass the deny gate
# --------------------------------------------------------------------------- #
def test_pin_to_denied_tier_cannot_bypass_gate_via_http():
    """A caller pinning a tier the gate DENIES for the work-class must NOT be
    served by that tier over the real HTTP path.

    ``POST {"model":"fast-local", ...}`` is a caller-controlled PIN (the wire
    ``model`` naming a concrete tier id). For a multi-file-refactor request the
    profile DENIES fast-local, so the request must be routed via the work-class's
    gated pool to an ALLOWED tier (heavy-local) — the pinned local's content must
    never reach the client. This is the exact gate-bypass the router exists to
    prevent, proven blocked end-to-end through the front door.
    """
    from anvil_serving.router.profile_store import default_profile

    backends: Dict[str, StaticBackend] = {
        # The pinned-but-DENIED tier emits a distinctive poison string.
        "fast-local": StaticBackend(["POISON-PINNED-LOCAL-OUTPUT"]),
        "heavy-local": StaticBackend(["heavy-served-allowed"]),
        "cloud": StaticBackend([CLOUD_CONTENT]),
    }
    httpd = build_server(
        CONFIG, host="127.0.0.1", port=0, backends=backends, profile=default_profile()
    )
    with running(httpd) as (host, port):
        status, headers, raw = _post(
            host, port, "/v1/chat/completions",
            # "refactor" alone classifies as multi-file-refactor (confident);
            # model="fast-local" is the caller's PIN to a tier denied for it.
            {"model": "fast-local",
             "messages": [{"role": "user", "content": "refactor the auth module"}]},
        )

    assert status == 200, (status, raw)
    body = json.loads(raw)
    content = body["choices"][0]["message"]["content"]

    # Served by the ALLOWED tier (heavy-local), not the denied pin.
    assert content == "heavy-served-allowed", f"got {content!r}"
    # The pinned local's output must never appear anywhere in the HTTP body.
    assert "POISON-PINNED-LOCAL-OUTPUT" not in raw.decode("utf-8"), (
        "denied pinned tier's output leaked — the caller pin bypassed the gate"
    )

    record = httpd.anvil_routing._decision_log.last
    assert record is not None
    assert record.served_tier == "heavy-local"


# --------------------------------------------------------------------------- #
# Anthropic dialect: verify-fallback via /v1/messages
#
# These mirror the OpenAI tests above but exercise the Anthropic wire form
# end-to-end through the same RoutingBackend.generate() path.  The dialect
# difference is: POST /v1/messages with max_tokens; response is named-event SSE
# (streaming) or an Anthropic message object (non-streaming).
# --------------------------------------------------------------------------- #

def _parse_anthropic_content(raw: bytes) -> str:
    """Reassemble assistant text from an Anthropic named-event SSE body."""
    text = raw.decode("utf-8")
    pieces = []
    for block in text.split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln]
        etype = None
        data_str = None
        for ln in lines:
            if ln.startswith("event: "):
                etype = ln[len("event: "):]
            elif ln.startswith("data: "):
                data_str = ln[len("data: "):]
        if etype == "content_block_delta" and data_str:
            obj = json.loads(data_str)
            pieces.append(obj.get("delta", {}).get("text", ""))
    return "".join(pieces)


def test_c3_anthropic_avw_fail_delivers_fallback_not_local_streaming():
    """Anthropic streaming: allow-with-verify local that FAILS verify must
    deliver ZERO local tokens; the next-candidate fallback is served via Anthropic SSE."""
    backends: Dict[str, StaticBackend] = {
        "fast-local": StaticBackend([LOCAL_FAIL_CONTENT]),
        "heavy-local": StaticBackend([CLOUD_CONTENT]),  # fallback in local-only config
    }
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=backends, profile=_avw_profile())
    with running(httpd) as (host, port):
        status, headers, raw = _post(
            host, port, "/v1/messages",
            {
                "model": "chat",
                "max_tokens": 256,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )

    assert status == 200, (status, raw)
    assert "text/event-stream" in headers.get("content-type", ""), headers
    content = _parse_anthropic_content(raw)

    # Fallback output must be delivered.
    assert content == CLOUD_CONTENT, f"expected fallback content, got: {content!r}"

    # No local token may appear anywhere in the HTTP body.
    raw_text = raw.decode("utf-8")
    assert "broken_local_fn" not in raw_text, "local token leaked into Anthropic SSE response"
    assert LOCAL_FAIL_CONTENT not in raw_text, "local content leaked into Anthropic SSE response"

    # Decision log: fallback recorded.
    record = httpd.anvil_routing._decision_log.last
    assert record is not None, "no decision record written"
    assert record.fell_back, "decision log did not record fell_back=True"
    assert record.served_tier == "heavy-local"


def test_c3_anthropic_avw_pass_delivers_local_streaming():
    """Anthropic streaming: allow-with-verify local that PASSES verify is
    committed and delivered via Anthropic SSE; the fallback is not reached."""
    backends: Dict[str, StaticBackend] = {
        "fast-local": StaticBackend([LOCAL_PASS_CONTENT]),
        "heavy-local": StaticBackend([CLOUD_CONTENT]),
    }
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=backends, profile=_avw_profile())
    with running(httpd) as (host, port):
        status, headers, raw = _post(
            host, port, "/v1/messages",
            {
                "model": "chat",
                "max_tokens": 256,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )

    assert status == 200, (status, raw)
    content = _parse_anthropic_content(raw)

    # Local output must be committed and delivered.
    assert content == LOCAL_PASS_CONTENT, f"expected local content, got: {content!r}"

    # Cloud output must not appear.
    assert CLOUD_CONTENT not in raw.decode("utf-8"), "cloud content unexpectedly appeared"

    # Decision log: no fallback.
    record = httpd.anvil_routing._decision_log.last
    assert record is not None
    assert not record.fell_back, "unexpected fallback recorded"
    assert record.served_tier == "fast-local"


def test_c3_anthropic_avw_fail_non_streaming_falls_back_to_next_candidate():
    """Anthropic non-streaming: allow-with-verify local that fails verify must
    be discarded; the next-candidate response is delivered in the Anthropic message format."""
    backends: Dict[str, StaticBackend] = {
        "fast-local": StaticBackend([LOCAL_FAIL_CONTENT]),
        "heavy-local": StaticBackend([CLOUD_CONTENT]),
    }
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=backends, profile=_avw_profile())
    with running(httpd) as (host, port):
        status, headers, raw = _post(
            host, port, "/v1/messages",
            {
                "model": "chat",
                "max_tokens": 256,
                "messages": [{"role": "user", "content": "hi"}],
                # no "stream" key -> non-streaming Anthropic message response
            },
        )

    assert status == 200, (status, raw)
    assert headers.get("content-type") == "application/json"
    body = json.loads(raw)
    # Anthropic non-streaming message format: content is a list of blocks.
    content = body["content"][0]["text"]

    assert content == CLOUD_CONTENT, f"expected fallback content, got: {content!r}"
    assert "broken_local_fn" not in raw.decode("utf-8"), "local token leaked"

    record = httpd.anvil_routing._decision_log.last
    assert record is not None
    assert record.fell_back
    assert record.served_tier == "heavy-local"


# --------------------------------------------------------------------------- #
# T004: keyless exhaustion → exhaustion_status (C3 on the keyless path)
#
# In the local-only default an allow-with-verify miss MUST exhaust the local
# tier chain and the front door MUST return the exhaustion status (default 503)
# with NO partial local tokens in the body (C3).
#
# Architecture: CONFIG_SINGLE_TIER has only one tier (fast-local) in the chat
# pool — no fallback tier.  When fast-local fails verify, route_with_fallback
# exhausts, RoutingBackend.generate() raises NoAvailableTierError BEFORE
# returning any iterator (fully eager: the commit-window drained + verified the
# whole local response in memory first), and the front door converts it to the
# configured exhaustion status.  This is the keyless handoff signal: OpenClaw's
# transport failover treats it as "overloaded" and re-runs on the native
# subscription provider (ADR-0001 §Mechanism, advise-and-defer:T004).
# --------------------------------------------------------------------------- #

def test_c3_keyless_exhaustion_streaming_default_503():
    """Streaming: avw miss on a local-only single-tier config → exhaustion_status
    (default 503); the broken local token is absent from the response body (C3).

    The commit-window buffers the entire local response before NoAvailableTierError
    is raised, so the front door still has not sent any headers when it catches
    the error — the 503 IS the first (and only) thing the client receives.
    """
    backends = {"fast-local": StaticBackend([LOCAL_FAIL_CONTENT])}
    httpd = build_server(
        CONFIG_SINGLE_TIER, host="127.0.0.1", port=0,
        backends=backends, profile=_avw_profile(),
    )
    with running(httpd) as (host, port):
        status, _headers, raw = _post(
            host, port, "/v1/chat/completions",
            {"model": "chat", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        )

    # Must be the exhaustion status (503 by default), not a 200.
    assert status == 503, f"expected exhaustion status 503, got {status}: {raw!r}"

    # C3: the broken local token must appear NOWHERE in the response body.
    raw_text = raw.decode("utf-8")
    assert "broken_local_fn" not in raw_text, (
        f"local token leaked into exhaustion response: {raw_text!r}"
    )
    assert LOCAL_FAIL_CONTENT not in raw_text, (
        f"local content leaked into exhaustion response: {raw_text!r}"
    )

    # Body must be a generic error envelope, not any local content.
    body = json.loads(raw_text)
    assert "error" in body, f"expected error envelope, got: {body}"


def test_c3_keyless_exhaustion_non_streaming_default_503():
    """Non-streaming: avw miss on a local-only single-tier config → exhaustion_status
    (default 503); the broken local token is absent from the response body (C3).

    Symmetric with the streaming test: the non-streaming path also catches
    NoAvailableTierError before committing any response bytes.
    """
    backends = {"fast-local": StaticBackend([LOCAL_FAIL_CONTENT])}
    httpd = build_server(
        CONFIG_SINGLE_TIER, host="127.0.0.1", port=0,
        backends=backends, profile=_avw_profile(),
    )
    with running(httpd) as (host, port):
        status, _headers, raw = _post(
            host, port, "/v1/chat/completions",
            {"model": "chat", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert status == 503, f"expected exhaustion status 503, got {status}: {raw!r}"

    raw_text = raw.decode("utf-8")
    assert "broken_local_fn" not in raw_text, (
        f"local token leaked into exhaustion response: {raw_text!r}"
    )
    assert LOCAL_FAIL_CONTENT not in raw_text, (
        f"local content leaked into exhaustion response: {raw_text!r}"
    )

    body = json.loads(raw_text)
    assert "error" in body, f"expected error envelope, got: {body}"


def test_c3_keyless_exhaustion_configurable_status_424():
    """exhaustion_status is configurable: a config with exhaustion_status=424 →
    the exhaustion response uses 424, not the default 503.

    Proves the operator can override exhaustion_status to match their gateway's
    transport-failover trigger (ADR-0001 §Mechanism, advise-and-defer:T004).
    C3 still holds: no local tokens in the body regardless of the status value.
    """
    backends = {"fast-local": StaticBackend([LOCAL_FAIL_CONTENT])}
    httpd = build_server(
        CONFIG_SINGLE_TIER_424, host="127.0.0.1", port=0,
        backends=backends, profile=_avw_profile(),
    )
    with running(httpd) as (host, port):
        status, _headers, raw = _post(
            host, port, "/v1/chat/completions",
            {"model": "chat", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        )

    # The configured exhaustion_status (424) must be returned, not the default 503.
    assert status == 424, f"expected configured exhaustion_status 424, got {status}: {raw!r}"

    # C3 still holds with the non-default status.
    raw_text = raw.decode("utf-8")
    assert "broken_local_fn" not in raw_text, (
        f"local token leaked into 424 exhaustion response: {raw_text!r}"
    )
    assert LOCAL_FAIL_CONTENT not in raw_text, (
        f"local content leaked into 424 exhaustion response: {raw_text!r}"
    )

    body = json.loads(raw_text)
    assert "error" in body, f"expected error envelope, got: {body}"
