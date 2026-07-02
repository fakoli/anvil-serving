"""Production-hardening pins (bug-bash round).

* DecisionLog is a bounded ring buffer (long-running-server memory leak fix).
* RouterConfig.tier() is O(1) and still raises on unknown ids.
* CircuitBreaker: an abandoned half-open probe expires instead of wedging the
  circuit OPEN forever.
* verify._has_unterminated_fence is linear (previously quadratic on
  adversarial many-fence input) and still correct.
* Front door: POST tolerates a trailing slash like GET does; a GET carrying
  Transfer-Encoding closes the connection instead of desyncing keep-alive.
"""
from __future__ import annotations

import json
import time

import pytest

from anvil_serving.router.config import ConfigError, RouterConfig, Tier
from anvil_serving.router.decision_log import DecisionLog, DecisionRecord
from anvil_serving.router.fallback import CircuitBreaker
from anvil_serving.router.verify import _has_unterminated_fence


def _record(i: int) -> DecisionRecord:
    return DecisionRecord(
        work_class="chat",
        requested_tiers=("t",),
        attempts=(),
        served_tier="t",
        total_prompt_tokens=i,
        total_completion_tokens=0,
        fell_back=False,
    )


# --------------------------------------------------------------------------- #
# DecisionLog ring buffer
# --------------------------------------------------------------------------- #
def test_decision_log_caps_memory():
    log = DecisionLog(max_records=3)
    for i in range(10):
        log.record(_record(i))
    assert len(log) == 3
    # Oldest evicted first; the newest three survive in order.
    assert [r.total_prompt_tokens for r in log.records] == [7, 8, 9]
    assert log.last.total_prompt_tokens == 9


def test_decision_log_default_is_bounded():
    log = DecisionLog()
    assert log._records.maxlen is not None and log._records.maxlen > 0


def test_decision_log_unbounded_opt_out():
    log = DecisionLog(max_records=None)
    assert log._records.maxlen is None


def test_decision_log_rejects_nonpositive_cap():
    with pytest.raises(ValueError):
        DecisionLog(max_records=0)


# --------------------------------------------------------------------------- #
# RouterConfig.tier() O(1) lookup
# --------------------------------------------------------------------------- #
def _config() -> RouterConfig:
    tiers = tuple(
        Tier(
            id=f"t{i}", base_url="http://127.0.0.1:1/v1", dialect="openai",
            context_limit=1024, privacy="local", tool_support=True,
            auth_env="K", model="m",
        )
        for i in range(5)
    )
    return RouterConfig(
        tiers=tiers, presets={"chat": ("t0",)}, mapping_version="x",
    )


def test_tier_lookup_hits_and_misses():
    cfg = _config()
    assert cfg.tier("t3").id == "t3"
    with pytest.raises(ConfigError):
        cfg.tier("nope")
    # The lazy index is built once and reused.
    assert cfg._tiers_by_id is cfg._tiers_by_id


# --------------------------------------------------------------------------- #
# CircuitBreaker probe expiry
# --------------------------------------------------------------------------- #
def test_abandoned_probe_expires_and_regrants(monkeypatch):
    cb = CircuitBreaker(cooldown=60.0)
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    cb.record_failure("t")
    cb.record_failure("t")            # threshold 2 -> OPEN at t=1000
    clock[0] += 61                     # cooldown expired -> half-open
    assert cb.is_open("t", 2) is False  # probe granted to this "thread"
    assert cb.is_open("t", 2) is True   # concurrent caller: probe in flight

    # The probing thread dies without reporting an outcome. After another
    # cooldown the stale probe must expire and a NEW probe be granted —
    # previously the tier stayed OPEN forever.
    clock[0] += 61
    assert cb.is_open("t", 2) is False
    cb.record_success("t")             # probe heals the circuit
    assert cb.is_open("t", 2) is False


# --------------------------------------------------------------------------- #
# linear fence scan: correctness pins + adversarial input completes fast
# --------------------------------------------------------------------------- #
def test_unterminated_fence_detection_still_correct():
    assert _has_unterminated_fence("```python\nx = 1\n")            # dangling opener
    assert not _has_unterminated_fence("```python\nx = 1\n```\n")   # complete block
    assert not _has_unterminated_fence("no fences at all")
    # complete block followed by a dangling opener
    assert _has_unterminated_fence("```py\na\n```\ntext\n```js\nb\n")


def test_many_bare_fences_scan_is_fast():
    # ~8000 fence delimiters (~4000 complete blocks). The old quadratic scan
    # took on the order of seconds-to-minutes here; linear is milliseconds.
    blob = "```\n" * 8000
    t0 = time.perf_counter()
    _has_unterminated_fence(blob)
    assert time.perf_counter() - t0 < 1.0


# --------------------------------------------------------------------------- #
# front-door framing
# --------------------------------------------------------------------------- #
def test_post_tolerates_trailing_slash():
    from anvil_serving.router.backends import StaticBackend
    from tests.router.test_front_door import _post, running_server

    with running_server(StaticBackend(["ok"])) as (host, port):
        status, _, raw = _post(host, port, "/v1/chat/completions/", {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        })
    assert status == 200
    assert json.loads(raw)["choices"][0]["message"]["content"] == "ok"


def test_get_with_transfer_encoding_closes_connection():
    import http.client

    from anvil_serving.router.backends import StaticBackend
    from tests.router.test_front_door import running_server

    with running_server(StaticBackend(["ok"])) as (host, port):
        conn = http.client.HTTPConnection(host, port, timeout=10)
        try:
            conn.putrequest("GET", "/healthz")
            conn.putheader("Transfer-Encoding", "chunked")
            conn.endheaders()
            # Terminate the (empty) chunked body so the response can be read.
            conn.send(b"0\r\n\r\n")
            resp = conn.getresponse()
            body = resp.read()
            # The response itself succeeds, but the server must not keep the
            # (now unframed) connection alive.
            assert resp.status == 200
            assert json.loads(body)["status"] == "ok"
            assert resp.getheader("Connection", "").lower() == "close"
        finally:
            conn.close()
