"""flexibility:T023 — tier ``max_concurrency = "auto"`` config + runtime contract."""
from __future__ import annotations

import json
import threading
from typing import Dict


from anvil_serving.router.serve import (
    _AutoConcurrencyGate,
    _AutoConcurrencyRefresher,
)


# --- config parsing ---------------------------------------------------------


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


# --- dynamic gate -----------------------------------------------------------

class _FakeBackend:
    """Backend-shaped inner for gate tests; body runs lazily."""

    def __init__(self, body=None):
        self._body = body

    def generate(self, request):
        if self._body is None:
            return iter([])
        return self._body(request)


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

    def run_first():
        next(first)  # run the body so the release path is real

    first_thread = threading.Thread(target=run_first, daemon=True)
    first_thread.start()
    assert started.wait(timeout=2)

    second_acquired = threading.Event()

    def second():
        it = gate.generate(object())  # acquires only after the slot frees
        second_acquired.set()
        it.close()

    thread = threading.Thread(target=second, daemon=True)
    thread.start()
    assert not second_acquired.wait(timeout=0.5)  # held at the ceiling
    release.set()
    first_thread.join(timeout=2)
    assert not first_thread.is_alive()
    first.close()
    assert second_acquired.wait(timeout=2)
    thread.join(timeout=2)
    assert not thread.is_alive()
    gate.set_ceiling(None)


# --- refresher --------------------------------------------------------------


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
