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

import pytest

from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.profile_store import ProfileEntry, ProfileStore
from anvil_serving.router.serve import build_server

CONFIG = str(Path(__file__).resolve().parents[2] / "configs" / "example.toml")

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
# C3-FAIL: allow-with-verify local FAILS verify -> cloud is served, no local tokens
# --------------------------------------------------------------------------- #
def test_c3_avw_fail_delivers_cloud_not_local_streaming():
    """CORE INVARIANT (streaming): allow-with-verify local that fails verify
    must deliver ZERO local tokens; the cloud fallback is served instead, and
    the decision log records the fallback."""
    backends: Dict[str, StaticBackend] = {
        "fast-local": StaticBackend([LOCAL_FAIL_CONTENT]),
        "cloud": StaticBackend([CLOUD_CONTENT]),
    }
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=backends, profile=_avw_profile())
    with running(httpd) as (host, port):
        status, headers, raw = _post(
            host, port, "/v1/chat/completions",
            {"model": "chat", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        )

    assert status == 200, (status, raw)
    content = _parse_content(raw)

    # Cloud output must be delivered.
    assert content == CLOUD_CONTENT, f"expected cloud content, got: {content!r}"

    # No local token may appear anywhere in the delivered HTTP body.
    raw_text = raw.decode("utf-8")
    assert "broken_local_fn" not in raw_text, "local token leaked into response"
    assert LOCAL_FAIL_CONTENT not in raw_text, "local content leaked into response"

    # Decision log must record that a fallback occurred.
    record = httpd.anvil_routing._decision_log.last
    assert record is not None, "no decision record written"
    assert record.fell_back, "decision log did not record fell_back=True"
    assert record.served_tier == "cloud", f"served_tier should be cloud, got {record.served_tier!r}"


# --------------------------------------------------------------------------- #
# C3-PASS: allow-with-verify local PASSES verify -> local committed + delivered
# --------------------------------------------------------------------------- #
def test_c3_avw_pass_delivers_local_not_cloud_streaming():
    """PASS path: allow-with-verify local that PASSES verify is committed and
    delivered; the cloud backend is never reached."""
    backends: Dict[str, StaticBackend] = {
        "fast-local": StaticBackend([LOCAL_PASS_CONTENT]),
        "cloud": StaticBackend([CLOUD_CONTENT]),
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
# C3-ALLOW: allow tier -> streamed directly, no verify invoked
# --------------------------------------------------------------------------- #
def test_c3_allow_streams_directly_no_verify():
    """An 'allow' tier is streamed to the client WITHOUT running any verifier.

    Proof: we serve content that would fail NonEmptyContent if verify ran
    (an empty string), and assert that the empty content IS delivered — not
    fallen back to the cloud tier.  If verify had run, cloud would have served
    CLOUD_CONTENT instead.
    """
    backends: Dict[str, StaticBackend] = {
        "fast-local": StaticBackend([""]),   # empty -> would fail NonEmptyContent
        "cloud": StaticBackend([CLOUD_CONTENT]),
        "heavy-local": StaticBackend(["heavy-served"]),
    }
    # Default profile: fast-local is 'allow' for chat.
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

    # The empty local content was delivered directly — not fallen back to cloud.
    assert content == "", f"expected empty local content, got: {content!r}"
    # Cloud fallback was NOT invoked.
    assert CLOUD_CONTENT not in raw.decode("utf-8")


# --------------------------------------------------------------------------- #
# C3-NON-STREAMING: stream=False also verifies + falls back
# --------------------------------------------------------------------------- #
def test_c3_avw_fail_non_streaming_falls_back_to_cloud():
    """Non-streaming path (stream=False): allow-with-verify local that fails
    verify must still be discarded; the cloud response is delivered instead."""
    backends: Dict[str, StaticBackend] = {
        "fast-local": StaticBackend([LOCAL_FAIL_CONTENT]),
        "cloud": StaticBackend([CLOUD_CONTENT]),
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

    assert content == CLOUD_CONTENT, f"expected cloud content, got: {content!r}"
    assert "broken_local_fn" not in raw.decode("utf-8"), "local token leaked"

    record = httpd.anvil_routing._decision_log.last
    assert record is not None
    assert record.fell_back
    assert record.served_tier == "cloud"
