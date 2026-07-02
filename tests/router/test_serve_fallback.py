"""genericity:T004 — verify-on-local-allow: catch empty/truncated local 200s.

Before T004, a privacy=local tier under an "allow" profile verdict was streamed
as a raw, zero-verifier passthrough — identical treatment to a cloud tier under
"allow". That meant an empty-content (thinking-budget starvation) or truncated
local 200 was delivered to the harness looking like a successful reply.

T004 closes that gap: a LOCAL "allow" tier now runs a MINIMAL commit-window
(NonEmptyContent + NotTruncated only — deliberately cheaper than the full
allow-with-verify chain in test_serve_verify_fallback.py) before its bytes
reach the client. On a fail it escalates to the next bound candidate, or (if
none remain) the request exhausts to ``exhaustion_status``. A cloud/remote
"allow" tier is unaffected — this file proves both halves end-to-end through
the REAL front door (build_server + injected backends), and proves the
``[router].verify_local_min`` config gate.

Hermetic and stdlib-only; no real network.
"""
from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict

from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.profile_store import default_profile
from anvil_serving.router.serve import build_server

_CONFIGS = Path(__file__).resolve().parents[2] / "configs"
CONFIG = str(_CONFIGS / "example.toml")                    # local-only; chat -> [fast-local, heavy-local]
CONFIG_WITH_CLOUD = str(_CONFIGS / "example-with-cloud.toml")  # planning -> [cloud] (metered)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONFIG_SINGLE_TIER = str(_FIXTURES / "single-tier-local.toml")  # chat -> [fast-local] only

CLOUD_CONTENT = "cloud-would-have-served-this"


# --------------------------------------------------------------------------- #
# harness (self-contained; mirrors test_serve_cli.py / test_serve_verify_fallback.py)
# --------------------------------------------------------------------------- #
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


def _chat_request():
    return {"model": "chat", "messages": [{"role": "user", "content": "hi"}], "stream": True}


# --------------------------------------------------------------------------- #
# local_allow_empty: the core T004 catch-and-escalate behaviour
# --------------------------------------------------------------------------- #
def test_local_allow_empty_content_escalates_to_next_candidate():
    """An empty-content 200 from the first LOCAL 'allow' tier is caught (not
    delivered) and escalates to the next bound candidate."""
    backends: Dict[str, StaticBackend] = {
        "fast-local": StaticBackend([""]),                 # empty -> fails NonEmptyContent
        "heavy-local": StaticBackend(["heavy-local-served"]),  # escalation target
    }
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=backends,
                         profile=default_profile())
    with running(httpd) as (host, port):
        status, _headers, raw = _post(host, port, "/v1/chat/completions", _chat_request())

    assert status == 200, (status, raw)
    content = _parse_content(raw)
    assert content == "heavy-local-served", (
        f"expected escalation to heavy-local, got: {content!r}"
    )
    # The empty local reply must not appear anywhere (nothing to appear, but
    # pin that no stray empty-content delta was streamed before escalating).
    record = httpd.anvil_routing._decision_log.last
    assert record is not None
    assert record.fell_back, "expected the decision log to record an escalation"
    assert record.served_tier == "heavy-local"


def test_local_allow_empty_content_no_further_candidate_exhausts_503():
    """A single-tier local-only config: the only 'allow' candidate returns empty
    content -> no candidate remains -> the request exhausts to exhaustion_status
    (default 503), not a silently-served empty 200."""
    backends: Dict[str, StaticBackend] = {"fast-local": StaticBackend([""])}
    httpd = build_server(CONFIG_SINGLE_TIER, host="127.0.0.1", port=0,
                         backends=backends, profile=default_profile())
    with running(httpd) as (host, port):
        status, _headers, raw = _post(host, port, "/v1/chat/completions", _chat_request())

    assert status == 503, f"expected exhaustion status 503, got {status}: {raw!r}"
    body = json.loads(raw.decode("utf-8"))
    assert "error" in body, f"expected error envelope, got: {body}"


def test_local_allow_tool_call_only_not_escalated():
    """v0.6.1 hotfix: a LOCAL 'allow' tier whose reply has empty text content
    but a populated ``tool_calls`` (a real tool-call turn, e.g. a coding-agent
    reading a file) must be served from the FIRST candidate, not misread as
    thinking-budget starvation and escalated/exhausted. Regression pin for the
    live end-to-end bug: empty content + >=1 tool_call must PASS the T004
    minimal commit-window (NonEmptyContent honors tool_calls; NotTruncated
    does not flag finish_reason='tool_calls' as truncation)."""

    class _ToolCallBackend:
        """Empty text delta, but a populated tool_calls + finish_reason='tool_calls'."""

        def generate(self, request):
            yield ""

        def get_last_structured(self):
            from anvil_serving.router.internal import StructuredResult
            return StructuredResult(
                finish_reason="tool_calls",
                tool_calls=[{"name": "read_file", "arguments": '{"path": "a.py"}'}],
            )

    backends = {
        "fast-local": _ToolCallBackend(),
        "heavy-local": StaticBackend(["should-not-be-reached"]),
    }
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=backends,
                         profile=default_profile())
    with running(httpd) as (host, port):
        status, _headers, raw = _post(host, port, "/v1/chat/completions", _chat_request())

    assert status == 200, (status, raw)
    raw_text = raw.decode("utf-8")
    assert "read_file" in raw_text, f"expected the tool call to be delivered, got: {raw_text!r}"
    assert "should-not-be-reached" not in raw_text, "escalated when it should not have"

    record = httpd.anvil_routing._decision_log.last
    assert record is not None
    assert not record.fell_back, "tool-call-only local reply was wrongly escalated"
    assert record.served_tier == "fast-local"


def test_local_allow_truncated_content_escalates():
    """A LOCAL 'allow' tier whose structured result reports a truncated
    finish_reason is caught by NotTruncated and escalates, even though its text
    is non-empty."""

    class _TruncatedBackend:
        """Yields non-empty text but reports finish_reason='length' (truncated)."""

        def generate(self, request):
            yield "cut off mid-thou"

        def get_last_structured(self):
            from anvil_serving.router.internal import StructuredResult
            return StructuredResult(finish_reason="length")

    backends = {
        "fast-local": _TruncatedBackend(),
        "heavy-local": StaticBackend(["heavy-local-served"]),
    }
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=backends,
                         profile=default_profile())
    with running(httpd) as (host, port):
        status, _headers, raw = _post(host, port, "/v1/chat/completions", _chat_request())

    assert status == 200, (status, raw)
    content = _parse_content(raw)
    assert content == "heavy-local-served", f"expected escalation, got: {content!r}"
    assert "cut off mid-thou" not in raw.decode("utf-8"), "truncated local text leaked"


def test_local_allow_good_content_not_escalated():
    """Sanity: the minimal-verify safety net does not fire on well-formed
    content — a normal 'allow' local response is still served from the first
    tier, matching pre-T004 behaviour for the common case."""
    backends: Dict[str, StaticBackend] = {
        "fast-local": StaticBackend(["all-good"]),
        "heavy-local": StaticBackend(["should-not-be-reached"]),
    }
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=backends,
                         profile=default_profile())
    with running(httpd) as (host, port):
        status, _headers, raw = _post(host, port, "/v1/chat/completions", _chat_request())

    assert status == 200, (status, raw)
    assert _parse_content(raw) == "all-good"
    assert "should-not-be-reached" not in raw.decode("utf-8")


# --------------------------------------------------------------------------- #
# verify_local: the [router].verify_local_min gate + the cloud/remote exemption
# --------------------------------------------------------------------------- #
def test_verify_local_min_false_restores_raw_passthrough():
    """[router].verify_local_min = false opts a deployment back into the
    pre-T004 raw passthrough for a LOCAL 'allow' tier: empty content IS
    delivered, no escalation."""
    from anvil_serving.router.config import load
    from dataclasses import replace

    config = load(CONFIG)
    config = replace(config, verify_local_min=False)

    backends: Dict[str, StaticBackend] = {
        "fast-local": StaticBackend([""]),
        "heavy-local": StaticBackend(["heavy-local-served"]),  # must NOT be reached
    }
    from anvil_serving.router.serve import RoutingBackend
    from anvil_serving.router.front_door import make_server

    routing = RoutingBackend(config, backends, default_profile())
    httpd = make_server("127.0.0.1", 0, routing)
    httpd.anvil_tiers = tuple(backends.keys())  # type: ignore[attr-defined]
    httpd.anvil_routing = routing  # type: ignore[attr-defined]

    with running(httpd) as (host, port):
        status, _headers, raw = _post(host, port, "/v1/chat/completions", _chat_request())

    assert status == 200, (status, raw)
    content = _parse_content(raw)
    assert content == "", f"expected raw empty passthrough, got: {content!r}"
    assert "heavy-local-served" not in raw.decode("utf-8")


def test_verify_local_min_true_is_the_default():
    from anvil_serving.router.config import load

    config = load(CONFIG)
    assert config.verify_local_min is True


def test_verify_local_cloud_allow_tier_unaffected():
    """A cloud tier under 'allow' is NEVER routed through the minimal
    commit-window, regardless of verify_local_min: empty content IS delivered
    raw, exactly as before T004. Uses example-with-cloud.toml's `planning`
    preset (cloud-only candidate pool; the cloud backend is injected so no real
    credential or network is needed)."""
    backends: Dict[str, StaticBackend] = {"cloud": StaticBackend([""])}
    httpd = build_server(CONFIG_WITH_CLOUD, host="127.0.0.1", port=0,
                         backends=backends, profile=default_profile())
    with running(httpd) as (host, port):
        status, _headers, raw = _post(
            host, port, "/v1/chat/completions",
            {"model": "planning", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        )

    assert status == 200, (status, raw)
    content = _parse_content(raw)
    assert content == "", f"expected raw empty passthrough from the cloud tier, got: {content!r}"
