"""End-to-end tests for the T004 ``GET /v1/models`` preset-discovery endpoint.

Hermetic: each test starts the REAL front-door server on an ephemeral
``127.0.0.1`` port in a background thread, GETs ``/v1/models`` over
``http.client``, and asserts the OpenAI model-list shape. No network, no real
LLM; the server is torn down in teardown. Stdlib-only.

The key invariant is *no drift*: the served ``id`` set must EQUAL the canonical
preset id set imported from the SAME source intent resolution uses
(:data:`anvil_serving.router.intent.PRESETS`). A future preset added there is
therefore automatically covered — the assertion needs no edit.
"""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from typing import Dict, Tuple

from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.front_door import make_server
from anvil_serving.router.intent import PRESETS


# --------------------------------------------------------------------------- #
# server harness (mirrors tests/router/test_front_door.py)
# --------------------------------------------------------------------------- #
@contextmanager
def running_server(backend):
    """Start the front door on an ephemeral port; yield ``(host, port)``."""
    httpd = make_server("127.0.0.1", 0, backend)
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _get(host: str, port: int, path: str) -> Tuple[int, Dict[str, str], bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, resp.read()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# shape
# --------------------------------------------------------------------------- #
def test_models_endpoint_shape():
    with running_server(StaticBackend(["x"])) as (host, port):
        status, headers, raw = _get(host, port, "/v1/models")

    assert status == 200
    assert headers.get("content-type") == "application/json"

    body = json.loads(raw)
    assert body["object"] == "list"
    assert isinstance(body["data"], list)
    assert body["data"], "model list is empty"

    for entry in body["data"]:
        # Each entry is an OpenAI-shaped Model with non-empty discovery metadata.
        assert entry["object"] == "model"
        assert isinstance(entry["id"], str) and entry["id"], entry
        assert isinstance(entry["name"], str) and entry["name"], entry
        assert isinstance(entry["description"], str) and entry["description"], entry
        # OpenAI-client compatibility fields.
        assert entry["owned_by"] == "anvil-serving"
        assert isinstance(entry["created"], int)


def test_no_duplicate_ids():
    with running_server(StaticBackend(["x"])) as (host, port):
        _status, _headers, raw = _get(host, port, "/v1/models")
    ids = [e["id"] for e in json.loads(raw)["data"]]
    assert len(ids) == len(set(ids)), f"duplicate preset ids served: {ids}"


# --------------------------------------------------------------------------- #
# no drift: served id set == canonical preset id set (same source as routing)
# --------------------------------------------------------------------------- #
def test_no_drift_from_canonical_presets():
    """The served ``id`` set must equal the canonical preset id set imported from
    the SAME source intent resolution uses. Adding/removing a preset at the
    canonical source is automatically covered — this assertion needs no edit."""
    expected_ids = {p.id for p in PRESETS}
    assert expected_ids, "canonical PRESETS is empty"

    with running_server(StaticBackend(["x"])) as (host, port):
        _status, _headers, raw = _get(host, port, "/v1/models")

    served_ids = {e["id"] for e in json.loads(raw)["data"]}
    assert served_ids == expected_ids

    # Names/descriptions are also served straight from the canonical source.
    by_id = {e["id"]: e for e in json.loads(raw)["data"]}
    for p in PRESETS:
        assert by_id[p.id]["name"] == p.name
        assert by_id[p.id]["description"] == p.description


def test_deterministic_payload():
    """No time.now() in the payload: two GETs return byte-identical bodies."""
    with running_server(StaticBackend(["x"])) as (host, port):
        _s1, _h1, raw1 = _get(host, port, "/v1/models")
        _s2, _h2, raw2 = _get(host, port, "/v1/models")
    assert raw1 == raw2


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
