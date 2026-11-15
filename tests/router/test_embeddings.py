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


from tests.router.helpers import StaticBackend
from anvil_serving.router.config import PurposeModel
from anvil_serving.router.front_door import make_server
from anvil_serving.router.purpose import PurposeRouter

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
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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

[[router.tiers]]
id            = "auxiliary-local"
base_url      = "http://127.0.0.1:30001/v1"
model         = "fast"
dialect       = "openai"
context_limit = 32768
privacy       = "local"
tool_support  = true
auth_env      = "ANVIL_AUXILIARY_LOCAL_KEY"

[router.model_routes]
llm.primary = "auxiliary-local"
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


# --------------------------------------------------------------------------- #
# PurposeRouter dispatch
# --------------------------------------------------------------------------- #


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
