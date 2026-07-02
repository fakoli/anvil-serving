"""In-flight request draining across multiplexer swaps (ADR-0006).

Hermetic: MockBackend only — no GPU, no docker, no sockets. Mirrors and extends
the drain section of the multiplexer's --self-check so the CI matrix pins it.
"""
from __future__ import annotations

import threading
import time

import pytest

from anvil_serving.multiplexer import (
    BackendError,
    LoadError,
    MockBackend,
    Multiplexer,
    UnknownModel,
)

REG = [
    {"name": "a", "model_path": "/m/a", "est_weight_gb": 20, "gpu": 0, "port": 30000},
    {"name": "b", "model_path": "/m/b", "est_weight_gb": 34, "gpu": 0, "port": 30001},
]


def _mux(drain_timeout: float = 5.0, ram: float = 100.0) -> Multiplexer:
    return Multiplexer(REG, MockBackend(), ram_probe=lambda: ram,
                       drain_timeout=drain_timeout)


def test_lease_serves_and_releases():
    mux = _mux()
    with mux.lease("a") as base:
        assert base == "http://mock/a"
        assert mux.inflight("a") == 1
    assert mux.inflight("a") == 0
    # Same-model leases stack (no serialization, no restart).
    with mux.lease("a"), mux.lease("a"):
        assert mux.inflight("a") == 2
    assert mux.backend.started == ["a"] and mux.backend.stops == 0


def test_lease_acquire_failures_register_nothing():
    mux = _mux()
    with pytest.raises(UnknownModel):
        with mux.lease("nope"):
            pass
    tight = Multiplexer(REG, MockBackend(), ram_probe=lambda: 16.0)
    with pytest.raises(LoadError):
        with tight.lease("a"):
            pass
    failing = Multiplexer(REG, MockBackend(fail_on={"a"}), ram_probe=lambda: 100.0)
    with pytest.raises(BackendError):
        with failing.lease("a"):
            pass
    for m in (mux, tight, failing):
        assert m.inflight("a") == 0 and m.inflight("nope") == 0


def test_swap_waits_for_live_lease_then_proceeds():
    mux = _mux()
    lease = mux.lease("a")
    lease.__enter__()
    swapped = threading.Event()
    t = threading.Thread(target=lambda: (mux.ensure_loaded("b"), swapped.set()),
                         daemon=True)
    t.start()
    # While the lease is live, the resident must NOT be stopped.
    assert not swapped.wait(timeout=0.3)
    assert mux.backend.current == "a" and mux.backend.stops == 0
    lease.__exit__(None, None, None)
    assert swapped.wait(timeout=5.0)
    t.join(timeout=5.0)
    assert mux.resident == "b" and mux.backend.current == "b"
    assert mux.backend.stops == 1


def test_drain_timeout_severs_laggard():
    mux = _mux(drain_timeout=0.2)
    hung = mux.lease("a")
    hung.__enter__()  # never released within the window
    t0 = time.monotonic()
    mux.ensure_loaded("b")
    waited = time.monotonic() - t0
    assert waited >= 0.15, f"swap did not honor the drain window ({waited:.3f}s)"
    assert mux.resident == "b" and mux.backend.current == "b"
    hung.__exit__(None, None, None)  # late release is harmless
    assert mux.inflight("a") == 0


def test_zero_drain_timeout_swaps_immediately():
    mux = _mux(drain_timeout=0.0)
    lease = mux.lease("a")
    lease.__enter__()
    t0 = time.monotonic()
    mux.ensure_loaded("b")
    assert time.monotonic() - t0 < 1.0
    assert mux.resident == "b"
    lease.__exit__(None, None, None)


def test_new_old_model_request_queues_behind_swap():
    """A request for the OLD model arriving mid-drain must queue behind the
    swap (not extend the drain), then be served by a swap back."""
    mux = _mux()
    first = mux.lease("a")
    first.__enter__()
    b_done = threading.Event()
    threading.Thread(target=lambda: (mux.ensure_loaded("b"), b_done.set()),
                     daemon=True).start()
    time.sleep(0.2)  # let the swapper enter its drain wait

    a_base = {}

    def _late_a():
        with mux.lease("a") as base:
            a_base["url"] = base

    late = threading.Thread(target=_late_a, daemon=True)
    late.start()
    # The late "a" request must not have extended the drain: releasing the
    # ORIGINAL lease is what lets the swap proceed.
    assert not b_done.wait(timeout=0.3)
    first.__exit__(None, None, None)
    assert b_done.wait(timeout=5.0)
    late.join(timeout=10.0)
    assert a_base.get("url") == "http://mock/a"
    assert mux.resident == "a"  # the queued request swapped back


def test_concurrent_same_model_leases_never_restart():
    mux = _mux()
    errors = []

    def worker():
        try:
            for _ in range(20):
                with mux.lease("a") as base:
                    assert base == "http://mock/a"
        except Exception as e:  # pragma: no cover - failure detail for the assert
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors
    assert mux.backend.started == ["a"] and mux.backend.stops == 0
    assert mux.inflight("a") == 0
