"""Tests for the purpose-model surfaces (gpu-reservations:T010, ADR-0017 §7).

Covers the three layers the task adds:

* config: ``[[router.purpose_models]]`` parsing + validation;
* :class:`~anvil_serving.router.purpose.PurposeRouter`: model-name dispatch,
  the unknown-model 404 (never a fallthrough to chat), upstream failure
  sanitization, and decision logging;
* the front door: ``POST /v1/embeddings`` / ``POST /v1/rerank`` end-to-end on
  a real ephemeral server, under the existing token auth.

Hermetic: transports are injected fakes; no network, no GPU.
"""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

import pytest

from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.config import ConfigError, PurposeModel, load
from anvil_serving.router.decision_log import DecisionLog
from anvil_serving.router.front_door import make_server
from anvil_serving.router.purpose import PurposeError, PurposeRouter

# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
EMBED_PM = PurposeModel(
    id="embeddings-local",
    kind="embedding",
    model="qwen3-embedding-0.6b",
    base_url="http://127.0.0.1:30005/v1",
)
RERANK_PM = PurposeModel(
    id="reranker-local",
    kind="rerank",
    model="qwen3-reranker-0.6b",
    base_url="http://127.0.0.1:30006/v1",
)

EMBED_RESPONSE = {
    "object": "list",
    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
    "model": "qwen3-embedding-0.6b",
    "usage": {"prompt_tokens": 7, "total_tokens": 7},
}
RERANK_RESPONSE = {
    "id": "rerank-1",
    "model": "qwen3-reranker-0.6b",
    "results": [{"index": 0, "relevance_score": 0.93}],
    "usage": {"total_tokens": 11},
}


class FakeTransport:
    """Canned-response transport recording every call (hermetic seam)."""

    def __init__(self, payload: Optional[dict] = None, error: Optional[Exception] = None):
        self.payload = payload
        self.error = error
        self.calls: List[dict] = []

    def __call__(self, url, *, data, headers, timeout, max_bytes=None) -> bytes:
        self.calls.append({
            "url": url,
            "body": json.loads(data.decode("utf-8")),
            "headers": dict(headers),
            "timeout": timeout,
        })
        if self.error is not None:
            raise self.error
        return json.dumps(self.payload).encode("utf-8")


@contextmanager
def purpose_server(purpose: Optional[PurposeRouter], auth_token: Optional[str] = None):
    httpd = make_server("127.0.0.1", 0, StaticBackend("chat reply"),
                        auth_token=auth_token, purpose=purpose)
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _post(host, port, path, body, headers=None) -> Tuple[int, bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        conn.request("POST", path, json.dumps(body), h)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _get(host, port, path, headers=None) -> Tuple[int, Dict[str, str], bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# config parsing
# --------------------------------------------------------------------------- #
BASE_TOML = """
[router]
mapping_version = "test"

[[router.tiers]]
id            = "fast-local"
base_url      = "http://127.0.0.1:30001/v1"
model         = "fast"
dialect       = "openai"
context_limit = 32768
privacy       = "local"
tool_support  = true
auth_env      = "ANVIL_FAST_LOCAL_KEY"

[router.presets]
chat = ["fast-local"]
"""

PURPOSE_TOML = BASE_TOML + """
[[router.purpose_models]]
id       = "embeddings-local"
kind     = "embedding"
model    = "qwen3-embedding-0.6b"
base_url = "http://127.0.0.1:30005/v1"

[[router.purpose_models]]
id       = "reranker-local"
kind     = "rerank"
model    = "qwen3-reranker-0.6b"
base_url = "http://127.0.0.1:30006/v1"
timeout  = 7.5
"""


def _write(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_config_without_purpose_models_parses_empty(tmp_path):
    cfg = load(_write(tmp_path, BASE_TOML))
    assert cfg.purpose_models == ()


def test_config_parses_purpose_models(tmp_path):
    cfg = load(_write(tmp_path, PURPOSE_TOML))
    assert len(cfg.purpose_models) == 2
    emb, rr = cfg.purpose_models
    assert emb.kind == "embedding" and emb.model == "qwen3-embedding-0.6b"
    assert emb.auth_env is None and emb.timeout is None
    assert rr.kind == "rerank" and rr.timeout == 7.5


@pytest.mark.parametrize("snippet,fragment", [
    ('id = "x"\nkind = "ocr"\nmodel = "m"\nbase_url = "http://127.0.0.1:1/v1"',
     "kind"),
    ('id = "x"\nkind = "embedding"\nmodel = ""\nbase_url = "http://127.0.0.1:1/v1"',
     "model"),
    ('id = "x"\nkind = "embedding"\nmodel = "m"\nbase_url = "file:///etc/passwd"',
     "base_url"),
    ('id = "x"\nkind = "embedding"\nmodel = "m"\nbase_url = "http://127.0.0.1:1/v1"\nauth_env = "not a name"',
     "auth_env"),
    ('id = "x"\nkind = "embedding"\nmodel = "m"\nbase_url = "http://127.0.0.1:1/v1"\ntimeout = -3',
     "timeout"),
])
def test_config_rejects_malformed_purpose_model(tmp_path, snippet, fragment):
    toml = BASE_TOML + "\n[[router.purpose_models]]\n" + snippet + "\n"
    with pytest.raises(ConfigError) as exc:
        load(_write(tmp_path, toml))
    assert fragment in str(exc.value)


def test_config_rejects_duplicate_purpose_id_and_routing_key(tmp_path):
    dup_id = PURPOSE_TOML + """
[[router.purpose_models]]
id       = "embeddings-local"
kind     = "rerank"
model    = "other"
base_url = "http://127.0.0.1:30007/v1"
"""
    with pytest.raises(ConfigError, match="duplicate purpose model id"):
        load(_write(tmp_path, dup_id))

    dup_key = PURPOSE_TOML + """
[[router.purpose_models]]
id       = "embeddings-local-2"
kind     = "embedding"
model    = "qwen3-embedding-0.6b"
base_url = "http://127.0.0.1:30007/v1"
"""
    with pytest.raises(ConfigError, match="duplicate purpose model routing key"):
        load(_write(tmp_path, dup_key))


def test_config_rejects_purpose_id_colliding_with_tier_id(tmp_path):
    toml = BASE_TOML + """
[[router.purpose_models]]
id       = "fast-local"
kind     = "embedding"
model    = "m"
base_url = "http://127.0.0.1:30005/v1"
"""
    with pytest.raises(ConfigError, match="duplicate purpose model id"):
        load(_write(tmp_path, toml))


# --------------------------------------------------------------------------- #
# PurposeRouter dispatch
# --------------------------------------------------------------------------- #
def test_dispatch_embeddings_relays_verbatim_and_logs_decision():
    transport = FakeTransport(EMBED_RESPONSE)
    log = DecisionLog()
    router = PurposeRouter([EMBED_PM], transport=transport, decision_log=log)

    body = {"model": "qwen3-embedding-0.6b", "input": ["hello world"],
            "encoding_format": "float"}
    payload = router.dispatch("embedding", body)

    assert payload == EMBED_RESPONSE
    (call,) = transport.calls
    assert call["url"] == "http://127.0.0.1:30005/v1/embeddings"
    assert call["body"] == body  # relayed verbatim, extra knobs included
    assert "Authorization" not in call["headers"]  # no auth_env -> no header

    record = log.last
    assert record is not None
    assert record.work_class == "embedding"
    assert record.served_tier == "embeddings-local"
    assert record.total_prompt_tokens == 7
    assert record.attempts[0].outcome == "served"


def test_dispatch_rerank_hits_rerank_path_and_honors_timeout():
    transport = FakeTransport(RERANK_RESPONSE)
    pm = PurposeModel(id="rr", kind="rerank", model="qwen3-reranker-0.6b",
                      base_url="http://127.0.0.1:30006/v1", timeout=7.5)
    router = PurposeRouter([pm], transport=transport, default_timeout=20.0)

    payload = router.dispatch("rerank", {
        "model": "qwen3-reranker-0.6b", "query": "q", "documents": ["a", "b"],
    })
    assert payload == RERANK_RESPONSE
    (call,) = transport.calls
    assert call["url"] == "http://127.0.0.1:30006/v1/rerank"
    assert call["timeout"] == 7.5  # per-model override beats the default


def test_dispatch_unknown_model_is_clean_404_and_never_calls_upstream():
    transport = FakeTransport(EMBED_RESPONSE)
    log = DecisionLog()
    router = PurposeRouter([EMBED_PM], transport=transport, decision_log=log)

    with pytest.raises(PurposeError) as exc:
        router.dispatch("embedding", {"model": "no-such-model", "input": "x"})

    err = exc.value
    assert err.status == 404
    assert err.etype == "model_not_found"
    assert "no-such-model" in err.message
    assert "qwen3-embedding-0.6b" in err.message  # names the configured models
    assert "never fall through to chat" in err.message
    assert transport.calls == []  # rejected BEFORE any upstream call
    assert log.last is None  # nothing was dispatched


def test_dispatch_kind_mismatch_is_unknown_model():
    # An embedding model name sent to /v1/rerank must not resolve.
    router = PurposeRouter([EMBED_PM, RERANK_PM], transport=FakeTransport({}))
    with pytest.raises(PurposeError) as exc:
        router.dispatch("rerank", {"model": "qwen3-embedding-0.6b",
                                   "query": "q", "documents": ["d"]})
    assert exc.value.status == 404


def test_dispatch_upstream_failure_is_sanitized_502_and_logged():
    from anvil_serving.router.backends.cloud import CloudBackendError

    transport = FakeTransport(error=CloudBackendError("cloud provider returned HTTP 500"))
    log = DecisionLog()
    router = PurposeRouter([EMBED_PM], transport=transport, decision_log=log)

    with pytest.raises(PurposeError) as exc:
        router.dispatch("embedding", {"model": "qwen3-embedding-0.6b", "input": "x"})
    assert exc.value.status == 502
    record = log.last
    assert record is not None
    assert record.served_tier is None
    assert record.attempts[0].outcome == "error"


def test_auth_env_resolves_to_bearer_header_and_unset_skips_model(capsys):
    pm = PurposeModel(id="e", kind="embedding", model="m",
                      base_url="http://127.0.0.1:30005/v1",
                      auth_env="PURPOSE_KEY")
    transport = FakeTransport(EMBED_RESPONSE)

    bound = PurposeRouter([pm], env={"PURPOSE_KEY": "sekret"}, transport=transport)
    bound.dispatch("embedding", {"model": "m", "input": "x"})
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer sekret"

    unbound = PurposeRouter([pm], env={}, transport=transport)
    assert len(unbound) == 0
    err = capsys.readouterr().err
    assert "not bound" in err and "PURPOSE_KEY" in err
    assert "sekret" not in err  # never the secret itself


# --------------------------------------------------------------------------- #
# front door end-to-end
# --------------------------------------------------------------------------- #
def test_front_door_embeddings_end_to_end():
    transport = FakeTransport(EMBED_RESPONSE)

    def by_url(url, **kw):
        transport.payload = (
            RERANK_RESPONSE if url.endswith("/rerank") else EMBED_RESPONSE
        )
        return transport(url, **kw)

    log = DecisionLog()
    router = PurposeRouter([EMBED_PM, RERANK_PM], transport=by_url,
                           decision_log=log)
    with purpose_server(router) as (host, port):
        status, data = _post(host, port, "/v1/embeddings", {
            "model": "qwen3-embedding-0.6b", "input": ["hello"],
        })
        assert status == 200
        assert json.loads(data) == EMBED_RESPONSE
        assert log.last.served_tier == "embeddings-local"

        status, data = _post(host, port, "/v1/rerank", {
            "model": "qwen3-reranker-0.6b", "query": "q", "documents": ["a"],
        })
        assert status == 200
        assert json.loads(data) == RERANK_RESPONSE


def test_front_door_unknown_embedding_model_is_404_not_chat():
    transport = FakeTransport(EMBED_RESPONSE)
    router = PurposeRouter([EMBED_PM], transport=transport)
    with purpose_server(router) as (host, port):
        status, data = _post(host, port, "/v1/embeddings", {
            "model": "planning", "input": "x",  # a CHAT preset name
        })
    assert status == 404
    err = json.loads(data)["error"]
    assert err["type"] == "model_not_found"
    assert "planning" in err["message"]
    assert transport.calls == []  # nothing upstream; StaticBackend untouched


def test_front_door_purpose_paths_require_auth_token():
    router = PurposeRouter([EMBED_PM], transport=FakeTransport(EMBED_RESPONSE))
    with purpose_server(router, auth_token="tok-123") as (host, port):
        status, data = _post(host, port, "/v1/embeddings", {
            "model": "qwen3-embedding-0.6b", "input": "x",
        })
        assert status == 401

        status, _ = _post(host, port, "/v1/embeddings", {
            "model": "qwen3-embedding-0.6b", "input": "x",
        }, headers={"Authorization": "Bearer tok-123"})
        assert status == 200


def test_front_door_without_purpose_router_keeps_404():
    with purpose_server(None) as (host, port):
        status, data = _post(host, port, "/v1/embeddings", {
            "model": "qwen3-embedding-0.6b", "input": "x",
        })
        assert status == 404
        status, _ = _post(host, port, "/v1/rerank", {
            "model": "m", "query": "q", "documents": ["d"],
        })
        assert status == 404
        # And GET /healthz does not advertise the purpose routes.
        status, _, data = _get(host, port, "/healthz")
        assert status == 200
        assert "/v1/embeddings" not in json.loads(data)["routes"]


def test_front_door_healthz_advertises_purpose_routes_when_bound():
    router = PurposeRouter([EMBED_PM], transport=FakeTransport(EMBED_RESPONSE))
    with purpose_server(router) as (host, port):
        status, _, data = _get(host, port, "/healthz")
    assert status == 200
    routes = json.loads(data)["routes"]
    assert "/v1/embeddings" in routes and "/v1/rerank" in routes


def test_front_door_get_on_purpose_path_is_405():
    router = PurposeRouter([EMBED_PM], transport=FakeTransport(EMBED_RESPONSE))
    with purpose_server(router) as (host, port):
        status, headers, data = _get(host, port, "/v1/embeddings")
    assert status == 405
    assert headers.get("allow") == "POST"
    assert json.loads(data)["error"]["type"] == "method_not_allowed"


@pytest.mark.parametrize("path,body,fragment", [
    ("/v1/embeddings", {"input": "x"}, "model"),
    ("/v1/embeddings", {"model": "qwen3-embedding-0.6b"}, "input"),
    ("/v1/embeddings", {"model": "qwen3-embedding-0.6b", "input": []}, "input"),
    ("/v1/rerank", {"model": "qwen3-reranker-0.6b", "documents": ["d"]}, "query"),
    ("/v1/rerank", {"model": "qwen3-reranker-0.6b", "query": "q"}, "documents"),
])
def test_front_door_malformed_purpose_body_is_400(path, body, fragment):
    transport = FakeTransport(EMBED_RESPONSE)
    router = PurposeRouter([EMBED_PM, RERANK_PM], transport=transport)
    with purpose_server(router) as (host, port):
        status, data = _post(host, port, path, body)
    assert status == 400
    err = json.loads(data)["error"]
    assert err["type"] == "invalid_request_error"
    assert fragment in err["message"]
    assert transport.calls == []  # rejected before any upstream call
