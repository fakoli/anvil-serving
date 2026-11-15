"""In-flight request draining across multiplexer swaps (ADR-0006).

Hermetic: MockBackend only — no GPU, no docker, no sockets. Mirrors and extends
the drain section of the multiplexer's --self-check so the CI matrix pins it.
"""
from __future__ import annotations

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
