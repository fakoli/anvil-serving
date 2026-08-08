"""Bounded graceful shutdown for stdlib ThreadingHTTPServer processes.

ADR-0033: ``docker restart`` is the only reload mechanism for the router and
the controller, so both servers must treat SIGTERM as "stop accepting, drain
briefly, flush evidence, exit" rather than immediate death. Compose sets
``stop_grace_period`` above the drain budget; SIGKILL remains the backstop.

Windows native runs rarely receive SIGTERM; the KeyboardInterrupt path stays
as the interactive backstop. Containers are Linux with the server as PID 1
via an exec-form entrypoint, so SIGTERM delivery is the real path there.
"""

from __future__ import annotations

import signal
import sys
import threading
import time
from typing import Callable, Optional

DEFAULT_DRAIN_SECONDS = 20.0


def _drain(threads: list[threading.Thread], deadline: float) -> tuple[float, int]:
    """Join ``threads`` until ``deadline`` (monotonic); return (waited, left)."""
    started = time.monotonic()
    for thread in threads:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=remaining)
    waited = time.monotonic() - started
    left = sum(
        1
        for thread in threads
        if thread.is_alive() and thread is not threading.current_thread()
    )
    return waited, left


def serve_until_signal(
    httpd,
    *,
    drain_seconds: float = DEFAULT_DRAIN_SECONDS,
    on_shutdown: Optional[Callable[[], None]] = None,
) -> None:
    """Run ``httpd.serve_forever()`` until SIGTERM/SIGINT, then drain and close.

    Signal handlers run on the main thread, which is also the thread inside
    ``serve_forever`` here — calling ``httpd.shutdown()`` inline would
    deadlock, so the handler hands it to a one-shot thread. Handlers are only
    installed when running on the main thread; otherwise the caller's existing
    KeyboardInterrupt handling is the sole stop path.
    """
    installed: dict[int, object] = {}

    def _request_shutdown(signum, frame):  # pragma: no cover - exercised via direct call
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                installed[signum] = signal.signal(signum, _request_shutdown)
            except (ValueError, OSError):
                continue

    # Track in-flight handler threads ourselves: ThreadingHTTPServer uses
    # daemon threads, which the stdlib deliberately leaves out of its own
    # ``_threads`` bookkeeping, so there is nothing built in to drain against.
    in_flight_lock = threading.Lock()
    in_flight: set[threading.Thread] = set()
    original_process = getattr(httpd, "process_request_thread", None)

    def _tracked_process(request, client_address):
        thread = threading.current_thread()
        with in_flight_lock:
            in_flight.add(thread)
        try:
            original_process(request, client_address)
        finally:
            with in_flight_lock:
                in_flight.discard(thread)

    if original_process is not None:
        httpd.process_request_thread = _tracked_process
    try:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        # Bounded drain: ThreadingMixIn.server_close() would join in-flight
        # handler threads without a timeout, letting one open SSE stream stall
        # shutdown until SIGKILL. Join manually against the budget instead.
        httpd.block_on_close = False
        with in_flight_lock:
            live = [thread for thread in in_flight if thread.is_alive()]
        waited, left = _drain(live, time.monotonic() + max(0.0, drain_seconds))
        print(
            "shutdown drain: waited=%.1fs remaining_threads=%d" % (waited, left),
            file=sys.stderr,
            flush=True,
        )
        if on_shutdown is not None:
            try:
                on_shutdown()
            except Exception:
                pass
        httpd.server_close()
    finally:
        for signum, previous in installed.items():
            try:
                signal.signal(signum, previous)
            except (ValueError, OSError):
                continue
