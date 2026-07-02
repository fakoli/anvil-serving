"""Residency-aware routing wired into RoutingBackend (AC3 anti-thrash).

policy.route()'s residency reorder was implemented and tested (test_residency)
but never fed by the serve path — RoutingBackend had no tracking. These tests
pin the wiring: the last-served LOCAL tier is recorded (thread-safely), passed
to route() on both generate() and decide(), and a cloud serve never clobbers it.
"""
from __future__ import annotations

import textwrap

from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.serve import RoutingBackend, build_server
from anvil_serving.router.config import load
from anvil_serving.router.profile_store import ProfileStore


class _EchoTier:
    """Minimal Backend: records calls, yields a canned reply."""

    def __init__(self, reply="ok"):
        self.reply = reply
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        yield self.reply


def _config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(textwrap.dedent("""\
        [router]
        mapping_version = "test-1"

        [[router.tiers]]
        id            = "fast-local"
        base_url      = "http://127.0.0.1:30001/v1"
        model         = "m1"
        dialect       = "openai"
        context_limit = 131072
        privacy       = "local"
        tool_support  = true
        auth_env      = "K1"

        [[router.tiers]]
        id            = "heavy-local"
        base_url      = "http://127.0.0.1:30000/v1"
        model         = "m2"
        dialect       = "openai"
        context_limit = 131072
        privacy       = "local"
        tool_support  = true
        auth_env      = "K2"

        [router.presets]
        chat         = ["fast-local", "heavy-local"]
        quick-edit   = ["fast-local", "heavy-local"]
        review       = ["heavy-local", "fast-local"]
        planning     = ["heavy-local"]
        long-context = ["heavy-local"]
        """), encoding="utf-8")
    return load(str(cfg))


def _routing(tmp_path) -> RoutingBackend:
    config = _config(tmp_path)
    backends = {"fast-local": _EchoTier("fast"), "heavy-local": _EchoTier("heavy")}
    # allow everywhere: the residency reorder (not the deny gate) is under test.
    profile = ProfileStore({})
    for tid in ("fast-local", "heavy-local"):
        for wc in ("chat", "bounded-edit", "review", "planning",
                   "multi-file-refactor", "long-context"):
            profile.record_grade(tid, wc, score=0.9, decision="allow")
    return RoutingBackend(config, backends, profile)


def _request(model: str) -> InternalRequest:
    return InternalRequest(
        model=model, messages=[Message("user", "hello")], max_tokens=32,
        dialect="openai",
    )


def test_local_serve_records_residency(tmp_path):
    routing = _routing(tmp_path)
    assert routing._residency() is None
    list(routing.generate(_request("chat")))       # served by fast-local
    assert routing._residency() == "fast-local"


def test_residency_reorders_subsequent_routes(tmp_path):
    routing = _routing(tmp_path)
    # review's config order prefers heavy-local; serve it -> heavy resident.
    list(routing.generate(_request("review")))
    assert routing._residency() == "heavy-local"
    # A chat request (config order fast > heavy) must now prefer the RESIDENT
    # heavy-local: the non-resident fast-local is deferred (anti-thrash).
    d = routing.decide(_request("chat"))
    assert d["provider"] == "heavy-local"
    # And serving it keeps heavy resident, without ever calling fast-local.
    list(routing.generate(_request("chat")))
    assert routing._residency() == "heavy-local"
    assert routing._backends["fast-local"].calls == 0


def test_decide_reads_but_never_writes_residency(tmp_path):
    routing = _routing(tmp_path)
    routing.decide(_request("chat"))               # would pick fast-local
    assert routing._residency() is None            # decide() never serves


def test_note_selected_ignores_cloud_and_unknown(tmp_path):
    routing = _routing(tmp_path)
    routing._note_selected(None)
    routing._note_selected("not-a-tier")
    assert routing._residency() is None
    routing._note_selected("fast-local")
    assert routing._residency() == "fast-local"
    # An unknown/cloud id never clobbers the last-known local resident.
    routing._note_selected("not-a-tier")
    assert routing._residency() == "fast-local"


def test_end_to_end_build_server_residency(tmp_path):
    """build_server-produced routing backend carries the wiring too."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(textwrap.dedent("""\
        [router]
        mapping_version = "test-1"

        [[router.tiers]]
        id            = "fast-local"
        base_url      = "http://127.0.0.1:30001/v1"
        model         = "m1"
        dialect       = "openai"
        context_limit = 131072
        privacy       = "local"
        tool_support  = true
        auth_env      = "K1"

        [router.presets]
        chat = ["fast-local"]
        quick-edit = ["fast-local"]
        review = ["fast-local"]
        planning = ["fast-local"]
        long-context = ["fast-local"]
        """), encoding="utf-8")
    httpd = build_server(str(cfg), port=0,
                         backends={"fast-local": _EchoTier()})
    try:
        routing = httpd.anvil_routing
        assert routing._residency() is None
        list(routing.generate(_request("chat")))
        assert routing._residency() == "fast-local"
    finally:
        httpd.server_close()
