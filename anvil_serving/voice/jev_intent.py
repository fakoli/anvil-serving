"""One bounded optional side worker; it never controls the voice pipeline."""

import queue
import threading

from .. import jev


class IntentAdvisor:
    def __init__(self, cancel_scope, *, policy_reader=jev.load_policy, advise=jev.advise):
        self.scope, self.policy_reader, self.advise = cancel_scope, policy_reader, advise
        self.pending = queue.Queue(maxsize=1)
        self.results = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.enabled = False
        self.epoch = 0
        self.latest = None
        self.thread = None

    def _policy(self):
        policy = self.policy_reader().copy()
        policy["timeout_seconds"] = min(policy["timeout_seconds"], 1.0)
        return policy

    def permitted(self):
        try:
            return self.enabled and not self.stop.is_set() and jev.gate(self._policy(), "voice_intent", allow_export=True) is None
        except (OSError, ValueError, TypeError, RecursionError):
            return False

    def configure(self, enabled):
        self.epoch += 1
        self.enabled = enabled
        self.latest = None
        for channel in (self.pending, self.results):
            while True:
                try:
                    channel.get_nowait()
                except queue.Empty:
                    break

    def submit(self, item):
        if (not self.permitted() or not getattr(item, "is_final", True)
                or item.generation != self.scope.current()
                or type(item.text) is not str or not item.text.strip()
                or len(item.text) > 4096 or len(item.text.encode()) > 8192):
            return
        identity = (item.turn_id, item.turn_revision, item.generation)
        self.latest = identity
        try:
            self.pending.put_nowait((self.epoch, identity, item.text))
        except queue.Full:
            return  # Drop advice under load; the ordinary turn continues.
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, daemon=True, name="voice-jev-intent")
            self.thread.start()

    def _current(self, epoch, identity):
        return self.permitted() and epoch == self.epoch and identity == self.latest and identity[2] == self.scope.current()

    def _request_policy(self, epoch, identity, expected=None):
        policy = self._policy()
        if (not self.enabled or self.stop.is_set() or epoch != self.epoch
                or identity != self.latest or identity[2] != self.scope.current()
                or (expected is not None and policy != expected)):
            policy["enabled"] = False
        return policy

    def _run(self):
        while not self.stop.is_set():
            try:
                epoch, identity, text = self.pending.get(timeout=0.1)
            except queue.Empty:
                continue
            if not self._current(epoch, identity):
                continue
            try:
                expected = self._request_policy(epoch, identity)
                annotation = self.advise("voice_intent", {"text": text}, allow_export=True,
                    policy_reader=lambda: self._request_policy(epoch, identity, expected))
            except Exception:
                # A classifier cannot fail the voice owner or log its input.
                continue
            if self._current(epoch, identity):
                try:
                    self.results.put_nowait((epoch, identity, annotation, expected))
                except queue.Full:
                    pass

    def drain(self):
        try:
            epoch, identity, annotation, expected = self.results.get_nowait()
        except queue.Empty:
            return None
        if not self._current(epoch, identity):
            return None
        try:
            if self._request_policy(epoch, identity, expected) != expected:
                return None
        except (OSError, ValueError, TypeError, RecursionError):
            return None
        return {"type": "anvil.voice.intent", "turn_id": identity[0], "turn_revision": identity[1],
                "generation": identity[2], "annotation": annotation}

    def close(self):
        self.configure(False)
        self.stop.set()
        if self.thread:
            # Include the existing process-tree cleanup bound on Windows.
            # Cancellation never calls or waits on this teardown join.
            self.thread.join(timeout=20)
