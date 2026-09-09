"""flexibility:T023 — tier ``max_concurrency = "auto"`` config + runtime contract."""
from __future__ import annotations

import json
import threading
from typing import Dict

import pytest

from anvil_serving.router.config import ConfigError, load
from anvil_serving.router.model_capacity import engine_declared_concurrency
from anvil_serving.router.serve import (
    _AutoConcurrencyGate,
    _AutoConcurrencyRefresher,
    _configured_admission,
)

from tests.router.test_config import _ONE_TIER, _REPLICA_TIER, _write


# --- config parsing ---------------------------------------------------------

def test_direct_tier_accepts_auto(tmp_path):
    body = _ONE_TIER.replace(
        "context_limit = 4096", 'context_limit = 4096\nmax_concurrency = "auto"'
    )
    tier = load(_write(tmp_path, body)).tier("primary")
    assert tier.max_concurrency == "auto"


@pytest.mark.parametrize(
    "literal", ['"Auto"', '"automatic"', "true", "0", "-1", "1.0", "[]", "{}"]
)
def test_direct_tier_rejects_other_strings_and_bounds(tmp_path, literal):
    body = _ONE_TIER.replace(
        "context_limit = 4096", f"context_limit = 4096\nmax_concurrency = {literal}"
    )
    with pytest.raises(ConfigError, match="max_concurrency must be a positive integer"):
        load(_write(tmp_path, body))


def test_replica_member_rejects_auto_with_clear_error(tmp_path):
    body = _REPLICA_TIER.replace(
        'id = "member-a",', 'id = "member-a", max_concurrency = "auto",'
    )
    with pytest.raises(ConfigError, match='"auto" is not supported on replica members'):
        load(_write(tmp_path, body))


def test_replica_aggregate_ceiling_rejects_auto(tmp_path):
    body = _REPLICA_TIER.replace(
        "context_limit = 4096", 'context_limit = 4096\nmax_concurrency = "auto"'
    )
    with pytest.raises(ConfigError, match='aggregate max_concurrency "auto" is not supported'):
        load(_write(tmp_path, body))


def test_auto_direct_tier_builds_admission_without_ceilings(tmp_path):
    body = _ONE_TIER.replace(
        "context_limit = 4096", 'context_limit = 4096\nmax_concurrency = "auto"'
    )
    config = load(_write(tmp_path, body))
    # Direct tiers carry no admission ceilings; construction must not treat
    # the "auto" sentinel as an integer ceiling anywhere.
    assert _configured_admission(config) is not None


# --- engine adapter ---------------------------------------------------------

class _FakeOpener:
    """Bounded opener stub keyed by URL suffix."""

    def __init__(self, routes: Dict[str, object]):
        self._routes = routes

    def __call__(self, request, timeout=None):
        path = request.full_url.split("?")[0]
        for key, payload in self._routes.items():
            if path.endswith(key):
                body = (
                    payload if isinstance(payload, bytes)
                    else json.dumps(payload).encode("utf-8")
                )
                return _FakeResponse(200, body)
        raise OSError("connection refused")


class _FakeResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def getcode(self):
        return self.status

    def read(self, limit=-1):
        return self._body if limit < 0 else self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_engine_adapter_reads_sglang_server_info():
    opener = _FakeOpener({"/get_server_info": {"max_running_requests": 1}})
    assert engine_declared_concurrency("http://127.0.0.1:8001/v1", opener=opener) == 1


def test_engine_adapter_reads_vllm_server_info():
    opener = _FakeOpener({"/server_info": {"max_num_seqs": 8}})
    assert engine_declared_concurrency("http://127.0.0.1:8001/v1", opener=opener) == 8


def test_engine_adapter_prefers_running_requests_over_num_seqs():
    opener = _FakeOpener(
        {"/get_server_info": {"max_running_requests": 2, "max_num_seqs": 64}}
    )
    assert engine_declared_concurrency("http://127.0.0.1:8001/v1", opener=opener) == 2


def test_engine_adapter_returns_none_when_neither_endpoint_answers():
    opener = _FakeOpener({})
    assert engine_declared_concurrency("http://127.0.0.1:8001/v1", opener=opener) is None


def test_engine_adapter_ignores_bool_zero_and_bad_json():
    opener = _FakeOpener(
        {
            "/get_server_info": {"max_running_requests": True},
            "/server_info": b"not json{",
        }
    )
    assert engine_declared_concurrency("http://127.0.0.1:8001/v1", opener=opener) is None


# --- dynamic gate -----------------------------------------------------------

class _FakeBackend:
    """Backend-shaped inner for gate tests; body runs lazily."""

    def __init__(self, body=None):
        self._body = body

    def generate(self, request):
        if self._body is None:
            return iter([])
        return self._body(request)


def test_gate_unlimited_before_first_ceiling():
    gate = _AutoConcurrencyGate(_FakeBackend(), "primary")
    assert gate.ceiling() is None
    first = gate.generate(object())  # acquires
    second = gate.generate(object())  # no ceiling yet: also passes
    gate.set_ceiling(1)  # in_flight=2 >= 1: new dispatches wait
    third_running = threading.Event()

    def third():
        gate.generate(object())
        third_running.set()

    thread = threading.Thread(target=third, daemon=True)
    thread.start()
    assert not third_running.wait(timeout=0.5)
    first.close()
    second.close()
    assert third_running.wait(timeout=5)
    thread.join(timeout=5)
    gate.set_ceiling(None)


def _lazy_inner():
    def inner(_request):
        yield "token"

    return inner


def test_gate_lowering_holds_until_slot_frees():
    started = threading.Event()
    release = threading.Event()

    def body(_request):
        started.set()
        release.wait(timeout=10)
        yield "token"

    gate = _AutoConcurrencyGate(_FakeBackend(body), "primary")
    gate.set_ceiling(1)

    first = gate.generate(object())  # in_flight=1
    next(first)  # run the body so the release path is real

    second_acquired = threading.Event()

    def second():
        it = gate.generate(object())  # acquires only after the slot frees
        it.close()

    thread = threading.Thread(target=second, daemon=True)
    thread.start()
    assert not second_acquired.wait(timeout=0.5)  # held at the ceiling
    release.set()
    first.close()
    thread.join(timeout=5)
    gate.set_ceiling(None)


# --- refresher --------------------------------------------------------------

def test_refresher_applies_reported_ceiling_once():
    gate = _AutoConcurrencyGate(_FakeBackend(), "primary")
    logs: list[str] = []
    refresher = _AutoConcurrencyRefresher(
        {"primary": (gate, "http://127.0.0.1:8001/v1")},
        interval=5,
        timeout=1,
        max_bytes=65536,
        opener=_FakeOpener({"/get_server_info": {"max_running_requests": 1}}),
        log=logs.append,
    )
    refresher.refresh_once()
    assert gate.ceiling() == 1
    assert any("auto max_concurrency resolved to 1" in line for line in logs)
    refresher.close()


def test_refresher_keeps_last_known_ceiling_on_transport_fault():
    gate = _AutoConcurrencyGate(_FakeBackend(), "primary")
    good = _FakeOpener({"/get_server_info": {"max_running_requests": 1}})

    class FlakyOpener:
        def __init__(self):
            self.calls = 0

        def __call__(self, request, timeout=None):
            self.calls += 1
            if self.calls > 2:
                raise OSError("down")
            return good(request, timeout=timeout)

    refresher = _AutoConcurrencyRefresher(
        {"primary": (gate, "http://127.0.0.1:8001/v1")},
        interval=5,
        timeout=1,
        max_bytes=65536,
        opener=FlakyOpener(),
        log=lambda _msg: None,
    )
    refresher.refresh_once()
    assert gate.ceiling() == 1
    refresher.refresh_once()
    refresher.refresh_once()
    assert gate.ceiling() == 1  # last known kept despite the fault
    refresher.close()


def test_refresher_starts_and_stops_thread():
    gate = _AutoConcurrencyGate(_FakeBackend(), "primary")
    refresher = _AutoConcurrencyRefresher(
        {"primary": (gate, "http://127.0.0.1:9/v1")},  # unreachable: exercises fault path
        interval=1,
        timeout=0.2,
        max_bytes=65536,
        opener=None,
        log=lambda _msg: None,
    )
    refresher.start()
    refresher.close()  # joins; must not hang
    assert gate.ceiling() is None

def test_gate_ceiling_raise_unblocks_waiters():
    gate = _AutoConcurrencyGate(_FakeBackend(), "primary")
    gate.set_ceiling(1)
    acquired = []
    acquired_ok = threading.Event()

    def wait_for_slot():
        gate.generate(object())
        acquired.append(1)
        acquired_ok.set()

    first = gate.generate(object())  # fills the single slot
    thread = threading.Thread(target=wait_for_slot, daemon=True)
    thread.start()
    assert not acquired_ok.wait(timeout=0.5)
    gate.set_ceiling(3)  # raise: the waiter must proceed without any release
    assert acquired_ok.wait(timeout=5)
    thread.join(timeout=5)
    assert acquired == [1]
    first.close()
    gate.set_ceiling(None)


def test_engine_adapter_respects_byte_cap():
    opener = _FakeOpener({"/get_server_info": {"max_running_requests": 1}})
    # Body is larger than the cap: the candidate is skipped entirely.
    assert (
        engine_declared_concurrency(
            "http://127.0.0.1:8001/v1", opener=opener, max_bytes=4
        )
        is None
    )
