"""Shared deterministic router test doubles."""

from __future__ import annotations

import http.client
import threading
import time
import types
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, Optional

from anvil_serving.router.config import Tier
from anvil_serving.router.front_door import make_server
from anvil_serving.router.internal import InternalRequest


class SlowRelease:
    """One-permit slot whose release lags, like a late-scheduled handler thread."""

    def __init__(self, _permits: int = 1):
        self._inner = threading.BoundedSemaphore(1)

    def acquire(self, *args, **kwargs):
        return self._inner.acquire(*args, **kwargs)

    def release(self):
        time.sleep(0.05)
        self._inner.release()


def slow_per_server_slots(monkeypatch) -> None:
    """Make the key-store, owner-check and Connect slots of servers built next one-permit SlowRelease."""
    from anvil_serving.router import front_door

    monkeypatch.setattr(front_door, "threading", types.SimpleNamespace(BoundedSemaphore=SlowRelease))


class StaticBackend:
    """Yield a fixed caller-supplied sequence of text deltas."""

    def __init__(self, tokens: Sequence[str]):
        self._tokens = list(tokens)

    def generate(self, request: InternalRequest) -> Iterator[str]:
        yield from self._tokens


def make_tier(dialect: str, privacy: str = "local", extra_body: Optional[dict] = None) -> Tier:
    """A minimal but complete :class:`Tier` for wire-fidelity/relay tests."""
    return Tier(
        id=f"{dialect}-tier",
        base_url="https://api.example.test",
        dialect=dialect,
        context_limit=200_000,
        privacy=privacy,
        tool_support=True,
        auth_env="EXAMPLE_KEY",
        model="concrete-model",
        extra_body=extra_body,
    )


@contextmanager
def server_context(routing: Any, *, token: str):
    """Start ``make_server`` on a background thread, yielding ``(host, port)``."""
    httpd = make_server("127.0.0.1", 0, routing, auth_token=token)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[:2]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def http_get(host, port, path, *, token: Optional[str]):
    """One authenticated (or deliberately unauthenticated) GET, fully read."""
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()
