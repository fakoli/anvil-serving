"""Bounded delivery worker and credential-derived client admission."""
from __future__ import annotations

import queue
import threading
import logging


class ClientAdmission:
    """Count active and waiting requests against a fixed configured client map."""

    def __init__(self, limits):
        self._limits = dict(limits)
        self._active = dict.fromkeys(limits, 0)
        self._lock = threading.Lock()

    def acquire(self, client_id):
        with self._lock:
            limit = self._limits.get(client_id)
            if limit is None:
                return True
            if self._active[client_id] >= limit:
                return False
            self._active[client_id] += 1
            return True

    def release(self, client_id):
        with self._lock:
            if client_id in self._active and self._active[client_id]:
                self._active[client_id] -= 1


class DeliveryWorker:
    """Keep generators and their thread-local structured result on one worker.

    At most two frames are buffered. Only the worker closes generators; the
    handler interrupts transport through RequestControl, never generator.close.
    The existing front-door admission pool bounds the number of workers.
    """

    def __init__(self, operation, control):
        self.control = control
        self.items = queue.Queue(maxsize=2)
        self.finished = threading.Event()
        self._lock = threading.Lock()
        self._cleanup = []
        self.thread = threading.Thread(target=self._run, args=(operation,), daemon=True)
        self.thread.start()

    def send(self, kind, value=None):
        while not self.control.cancelled():
            try:
                self.items.put((kind, value), timeout=0.1)
                return
            except queue.Full:
                self.control.check()
        self.control.check()

    def _run(self, operation):
        try:
            operation(self.send)
        except BaseException as exc:
            try:
                self.send("error", exc)
            except BaseException:
                pass
        finally:
            with self._lock:
                self.finished.set()
                cleanup, self._cleanup = self._cleanup, []
            for callback in cleanup:
                self._finish(callback)

    def when_finished(self, callback):
        with self._lock:
            if not self.finished.is_set():
                self._cleanup.append(callback)
                return
        self._finish(callback)

    @staticmethod
    def _finish(callback):
        try:
            callback()
        except Exception:
            # One failed observer must not prevent the remaining permit releases.
            logging.getLogger(__name__).error("request cleanup failed")

    def poll(self, timeout=0.1):
        try:
            return self.items.get(timeout=timeout)
        except queue.Empty:
            return ("done", None) if self.finished.is_set() else ("idle", None)

    def close(self):
        if not self.finished.is_set():
            self.control.cancel()
        self.thread.join(timeout=1)
