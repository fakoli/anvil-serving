"""Conservative per-request context gate on the serve path.

Proves the router refuses a GROSS over-context request up front instead of
forwarding it to a tier too small to hold it (which would 400 at the model with
"Input length exceeds maximum context length" + an ASGI traceback):

* ``serve._needs_for`` wires ``Needs.min_context`` from
  ``internal.estimate_tokens`` (a whitespace WORD count — a strict lower bound on
  real tokens), with NO extra discount, so the gate fires only on a clear
  over-context and never on a request merely near a tier's limit.
* When the context filter drops EVERY candidate tier, ``RoutingBackend.generate``
  raises ``NoAvailableTierError(kind="over_context")`` and the front door renders
  a clean **413 Payload Too Large** — never a forwarded model 400, never a 500.
* A comfortably-fitting request is unaffected (routes + serves 200 as before).

Hermetic and stdlib-only: injected in-process backends, no GPU serve.
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
from anvil_serving.router.internal import (
    InternalRequest,
    Message,
    NoAvailableTierError,
    estimate_tokens,
)
from anvil_serving.router.serve import _needs_for, build_server

# Default local-only config: fast-local (context_limit 32768) + heavy-local
# (131072); chat -> [fast-local, heavy-local].
CONFIG = str(Path(__file__).resolve().parents[2] / "configs" / "example.toml")
# Single small local tier (fast-local, 32768) — an over-context request drops
# the ONLY tier, so the whole request is unroutable -> the 413 path.
CONFIG_SINGLE_TIER = str(
    Path(__file__).resolve().parent / "fixtures" / "single-tier-local.toml"
)

FAST_CTX = 32768  # fast-local context_limit in both configs above


class _NeverCalledBackend:
    """A backend that fails loudly if its ``generate`` is ever entered.

    Injected for the too-small tier so a test proves the router NEVER forwards
    an over-context request to it (the old bug: forward -> model 400).
    """

    def generate(self, request):  # noqa: D401 - test double
        raise AssertionError(
            "too-small tier was called for an over-context request "
            "(the router forwarded instead of refusing with 413)"
        )
        yield  # pragma: no cover - unreachable; makes this a generator fn


def _big_text(words: int) -> str:
    """A prompt of exactly ``words`` whitespace-separated words."""
    return "word " * words


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
        conn.request(
            "POST", path, json.dumps(body), {"Content-Type": "application/json"}
        )
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# _needs_for: the exact, conservative margin
# --------------------------------------------------------------------------- #
def test_needs_for_sets_min_context_to_raw_word_count():
    # min_context == estimate_tokens(system + message bodies), with NO discount:
    # estimate_tokens is a WORD count (a strict lower bound on real tokens), so
    # the raw value already carries a ~1.3x+ cushion. A discount would push the
    # drop threshold past the incident this gate exists to catch.
    req = InternalRequest(
        model="chat",
        messages=[Message("user", "one two three"), Message("assistant", "four")],
        system="sys words here",
    )
    expected = estimate_tokens(["sys words here", "one two three", "four"])
    assert _needs_for(req).min_context == expected == 7


def test_needs_for_small_request_is_effectively_unconstrained():
    req = InternalRequest(model="chat", messages=[Message("user", "hello there")])
    assert _needs_for(req).min_context == 2  # nowhere near any real tier limit


# --------------------------------------------------------------------------- #
# generate(): over-context of every tier -> kind="over_context"
# --------------------------------------------------------------------------- #
def test_generate_raises_over_context_when_request_exceeds_only_tier():
    # Single small tier; a request bigger than its context_limit drops it, so
    # NOTHING can hold the request -> over_context (not unbound/exhausted), and
    # the too-small tier's backend is never entered.
    routing = build_server(
        CONFIG_SINGLE_TIER, host="127.0.0.1", port=0,
        backends={"fast-local": _NeverCalledBackend()},
    ).anvil_routing

    request = InternalRequest(
        model="chat", messages=[Message("user", _big_text(FAST_CTX + 200))]
    )
    with pytest.raises(NoAvailableTierError) as excinfo:
        list(routing.generate(request))
    assert excinfo.value.kind == "over_context"
    assert "fast-local" in excinfo.value.candidates


def test_generate_normal_request_is_unaffected():
    # A comfortably-fitting request still routes + serves (regression guard).
    routing = build_server(
        CONFIG_SINGLE_TIER, host="127.0.0.1", port=0,
        backends={"fast-local": StaticBackend(["served-ok"])},
    ).anvil_routing
    request = InternalRequest(model="chat", messages=[Message("user", "hi there")])
    assert "".join(routing.generate(request)) == "served-ok"


# --------------------------------------------------------------------------- #
# front door: over-context -> a clean 413, never a forwarded 400 / a 500
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("stream", [True, False])
def test_over_context_returns_413_through_front_door(stream):
    httpd = build_server(
        CONFIG_SINGLE_TIER, host="127.0.0.1", port=0,
        backends={"fast-local": _NeverCalledBackend()},
    )
    with running(httpd) as (host, port):
        status, raw = _post(
            host, port, "/v1/chat/completions",
            {
                "model": "chat",
                "messages": [{"role": "user", "content": _big_text(FAST_CTX + 200)}],
                "stream": stream,
            },
        )
    assert status == 413, (status, raw[:200])
    body = json.loads(raw)
    assert "error" in body
    assert "context" in json.dumps(body).lower()
    # The too-small tier's content must not appear (it was never called).
    assert "served-ok" not in raw.decode("utf-8")


def test_mid_size_request_drops_small_tier_routes_to_large():
    # A request between fast-local (32768) and heavy-local (131072): fast is
    # dropped by context, heavy holds it and serves. Proves a partial
    # over-context still routes (to the larger tier) rather than 413-ing.
    backends: Dict[str, object] = {
        "fast-local": _NeverCalledBackend(),         # too small -> must be skipped
        "heavy-local": StaticBackend(["heavy-served"]),
    }
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=backends)
    with running(httpd) as (host, port):
        status, raw = _post(
            host, port, "/v1/chat/completions",
            {
                "model": "chat",
                "messages": [{"role": "user", "content": _big_text(FAST_CTX + 8000)}],
                "stream": False,
            },
        )
    assert status == 200, (status, raw[:200])
    assert "heavy-served" in raw.decode("utf-8")


def test_normal_size_request_routes_to_fast_tier_200():
    # Comfortably-fitting request: fast-local serves; heavy is not reached.
    backends: Dict[str, object] = {
        "fast-local": StaticBackend(["fast-served"]),
        "heavy-local": _NeverCalledBackend(),
    }
    httpd = build_server(CONFIG, host="127.0.0.1", port=0, backends=backends)
    with running(httpd) as (host, port):
        status, raw = _post(
            host, port, "/v1/chat/completions",
            {
                "model": "chat",
                "messages": [{"role": "user", "content": "just a short prompt"}],
                "stream": False,
            },
        )
    assert status == 200, (status, raw[:200])
    assert "fast-served" in raw.decode("utf-8")
