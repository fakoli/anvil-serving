"""Bounded request lifetime and cancellation at the router HTTP boundary."""

from __future__ import annotations

import math
import threading
import time
from typing import Callable, Mapping, Optional


DEFAULT_ADMISSION_TIMEOUT_S = 30.0
DEFAULT_STARTUP_TIMEOUT_S = 300.0
DEFAULT_IDLE_TIMEOUT_S = 60.0
DEFAULT_TOTAL_TIMEOUT_S = 900.0
_PHASES = frozenset(("checking", "queued", "admitted", "dispatched", "streaming"))
_COUNTS = frozenset((
    "estimated_input_tokens", "input_tokens", "output_tokens",
    "cache_read_input_tokens", "context_limit_tokens",
))


class RequestControlError(RuntimeError):
    """A safe terminal request-control error for the HTTP boundary."""


class RequestCancelledError(RequestControlError):
    code = "client_cancelled"

    def __init__(self) -> None:
        super().__init__("request cancelled")


class RequestDeadlineExceeded(RequestControlError):
    """A named deadline.  The front door owns its HTTP rendering."""

    def __init__(self, kind: str) -> None:
        if kind not in {"admission_timeout", "startup_timeout", "idle_timeout", "request_timeout"}:
            raise ValueError("unknown request deadline")
        self.kind = kind
        self.code = kind
        self.status = 503 if kind == "admission_timeout" else 504
        super().__init__(kind.replace("_", " "))


def _duration(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite positive number")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return value


class RequestControl:
    """Shared cancellation/deadline state carried only in ``InternalRequest.raw``.

    This object is router-private: relay request construction selects explicit
    fields and therefore never serializes it upstream.  Socket close is the
    cancellation mechanism; callers must not close a running generator from a
    second thread.
    """

    def __init__(
        self,
        cancellation_event: Optional[threading.Event] = None,
        *,
        deadline_monotonic: Optional[float] = None,
        admission_timeout_s: float = DEFAULT_ADMISSION_TIMEOUT_S,
        startup_timeout_s: float = DEFAULT_STARTUP_TIMEOUT_S,
        idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S,
        total_timeout_s: float = DEFAULT_TOTAL_TIMEOUT_S,
        activity_callback: Optional[Callable[..., None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._started = clock()
        self._admission_timeout_s = _duration(admission_timeout_s, "admission_timeout_s")
        self._startup_timeout_s = _duration(startup_timeout_s, "startup_timeout_s")
        self._idle_timeout_s = _duration(idle_timeout_s, "idle_timeout_s")
        self._total_timeout_s = _duration(total_timeout_s, "total_timeout_s")
        if deadline_monotonic is not None:
            deadline_monotonic = _duration(deadline_monotonic, "deadline_monotonic")
        self._deadline = deadline_monotonic or self._started + self._total_timeout_s
        self._event = cancellation_event or threading.Event()
        self._callback = activity_callback
        self._lock = threading.RLock()
        self._upstream_close: Optional[Callable[[], None]] = None
        self._upstream_started: Optional[float] = None
        self._last_activity: Optional[float] = None
        self._admission_wait_started: Optional[float] = None
        self._admission_wait_ms = 0
        self._phase: Optional[str] = None
        self._counts: dict[str, int] = {}

    @property
    def admission_timeout_s(self) -> float:
        return self._admission_timeout_s

    @property
    def admission_wait_ms(self) -> int:
        with self._lock:
            value = self._admission_wait_ms
            if self._admission_wait_started is not None:
                value += max(0, int((self._clock() - self._admission_wait_started) * 1000))
            return value

    def elapsed_seconds(self) -> float:
        return max(0.0, self._clock() - self._started)

    def remaining_seconds(self) -> float:
        return max(0.0, self._deadline - self._clock())

    expires_in = remaining_seconds

    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            close = self._upstream_close
        if close is not None:
            try:
                close()
            except Exception:
                pass

    def set_upstream_close(self, close: Callable[[], None]) -> None:
        if not callable(close):
            raise TypeError("upstream close must be callable")
        with self._lock:
            self._upstream_close = close
            cancelled = self._event.is_set()
        if cancelled:
            try:
                close()
            except Exception:
                pass

    def clear_upstream_close(self, close: Optional[Callable[[], None]] = None) -> None:
        with self._lock:
            if close is None or self._upstream_close is close:
                self._upstream_close = None

    def _check_total(self) -> None:
        if self.cancelled():
            raise RequestCancelledError()
        if self.remaining_seconds() <= 0:
            raise RequestDeadlineExceeded("request_timeout")

    def check(self) -> None:
        """Check cancellation and the absolute request deadline."""
        self._check_total()

    def check_admission(self) -> None:
        self._check_total()
        with self._lock:
            waiting = self._admission_wait_started
        if waiting is not None and self._clock() - waiting >= self._admission_timeout_s:
            raise RequestDeadlineExceeded("admission_timeout")

    def begin_admission_wait(self) -> None:
        self.check_admission()
        with self._lock:
            if self._admission_wait_started is None:
                self._admission_wait_started = self._clock()
        self.note_activity("queued")

    def end_admission_wait(self) -> None:
        with self._lock:
            started = self._admission_wait_started
            if started is None:
                return
            self._admission_wait_ms += max(0, int((self._clock() - started) * 1000))
            self._admission_wait_started = None

    def admission_wait_seconds(self, poll_seconds: float = 0.2) -> float:
        self.check_admission()
        poll_seconds = _duration(poll_seconds, "poll_seconds")
        with self._lock:
            started = self._admission_wait_started
        admission_left = self._admission_timeout_s - (self._clock() - started) if started is not None else self._admission_timeout_s
        return max(0.0, min(poll_seconds, admission_left, self.remaining_seconds()))

    def start_upstream(self) -> None:
        self._check_total()
        with self._lock:
            if self._upstream_started is None:
                self._upstream_started = self._clock()

    def check_upstream(self) -> None:
        self._check_total()
        now = self._clock()
        with self._lock:
            upstream_started = self._upstream_started
            last_activity = self._last_activity
        if upstream_started is None:
            return
        if last_activity is None:
            if now - upstream_started >= self._startup_timeout_s:
                raise RequestDeadlineExceeded("startup_timeout")
        elif now - last_activity >= self._idle_timeout_s:
            raise RequestDeadlineExceeded("idle_timeout")

    def upstream_wait_seconds(self) -> float:
        return self.upstream_deadline()[0]

    def upstream_deadline(self) -> tuple[float, str]:
        """Return the next upstream deadline interval and its terminal code.

        The transport uses this to distinguish its deadline watchdog from an
        ordinary socket failure.  Keeping the selected code alongside the
        interval avoids a coarse platform clock turning a watchdog-triggered
        startup deadline into an unclassified transport error.
        """
        self.check_upstream()
        now = self._clock()
        with self._lock:
            upstream_started = self._upstream_started
            last_activity = self._last_activity
            deadline = self._deadline
        if upstream_started is None:
            phase_deadline = now + self._startup_timeout_s
            phase_kind = "startup_timeout"
        elif last_activity is None:
            phase_deadline = upstream_started + self._startup_timeout_s
            phase_kind = "startup_timeout"
        else:
            phase_deadline = last_activity + self._idle_timeout_s
            phase_kind = "idle_timeout"
        if deadline <= phase_deadline:
            return max(0.0, deadline - now), "request_timeout"
        return max(0.0, phase_deadline - now), phase_kind

    def note_activity(self, phase: Optional[str] = None, **counts: int) -> None:
        if phase is not None and phase not in _PHASES:
            raise ValueError("unknown request phase")
        if any(
            key not in _COUNTS or isinstance(value, bool) or not isinstance(value, int)
            or not 0 <= value <= 1_000_000_000_000_000
            for key, value in counts.items()
        ):
            raise ValueError("invalid request activity counts")
        now = self._clock()
        with self._lock:
            if phase is not None:
                self._phase = phase
            if phase == "streaming":
                self._last_activity = now
            self._counts.update(counts)
            current_phase = self._phase
            callback = self._callback
        if callback is not None:
            snapshot = self.snapshot()
            try:
                callback(current_phase, snapshot)
            except TypeError:
                callback(snapshot)

    def snapshot(self) -> Mapping[str, object]:
        with self._lock:
            return {
                "phase": self._phase,
                "elapsed_ms": max(0, int((self._clock() - self._started) * 1000)),
                "last_activity_ms": (
                    None if self._last_activity is None else max(0, int((self._clock() - self._last_activity) * 1000))
                ),
                "admission_wait_ms": self.admission_wait_ms,
                **self._counts,
            }


def request_control(request: object) -> Optional[RequestControl]:
    raw = getattr(request, "raw", None)
    if not isinstance(raw, Mapping):
        return None
    control = raw.get("_anvil_control")
    return control if isinstance(control, RequestControl) else None
