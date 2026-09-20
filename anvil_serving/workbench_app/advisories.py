"""Explicit selected-text advice; no retrieval, Pi command or state mutation."""

from collections import OrderedDict
from email.message import Message
import threading

from .. import jev
from ..observability.dashboard.contracts import ObservatoryError, fields, identifier


class Advisories:
    def __init__(self, environment):
        self.environment = environment
        self.slots = threading.BoundedSemaphore(2)
        self.lock = threading.Lock()
        self.generations = OrderedDict()

    @staticmethod
    def _authorize(session, resource, access):
        headers = Message()
        headers["Cookie"] = access.COOKIE + "=" + session.key
        fresh = access.session(headers)
        if fresh != session:
            raise ObservatoryError("advisory_stale", "The advisory belongs to an earlier session. Request it again.", 409)
        access.permit(fresh, resource)

    def request(self, capability, body, session, *, access):
        fields(body, required=("resource_id", "generation", "allow_export", "input"), optional=("disabled",))
        resource = identifier(body["resource_id"])
        self._authorize(session, resource, access)
        if capability not in jev.CAPABILITIES[:3]:
            raise ObservatoryError("not_found", "This advisory capability is unavailable.", 404)
        generation = identifier(body["generation"])
        if type(body["allow_export"]) is not bool or type(body.get("disabled", False)) is not bool:
            raise ObservatoryError("invalid_advisory", "Choose explicit cloud export permission.")
        try:
            policy = jev.load_policy()
            denied = jev.gate(policy, capability, allow_export=body["allow_export"], disabled=body.get("disabled", False))
        except (OSError, ValueError, RecursionError):
            denied = jev.report(capability, "unavailable", "invalid_configuration")
        if denied:
            return {"annotation": denied, "generation": generation}
        try:
            value = jev.validate_input(capability, body["input"])
        except (ValueError, TypeError):
            raise ObservatoryError("invalid_advisory", "Select bounded snippets with distinct opaque IDs.") from None
        key = (access.origin, session.key, resource, capability)
        if not self.slots.acquire(blocking=False):
            return {"annotation": jev.report(capability, "unavailable", "busy"), "generation": generation}
        try:
            with self.lock:
                self.generations[key] = generation
                self.generations.move_to_end(key)
                while len(self.generations) > 256:
                    self.generations.popitem(last=False)
            def current_policy():
                self._authorize(session, resource, access)
                current = jev.load_policy()
                with self.lock:
                    stale = self.generations.get(key) != generation
                if stale or current != policy:
                    current = current.copy()
                    current["enabled"] = False
                return current
            annotation = jev.advise(capability, value, allow_export=True, environment=self.environment,
                policy_reader=current_policy)
            self._authorize(session, resource, access)
            if current_policy() != policy:
                annotation = jev.report(capability, "blocked", "policy_changed", started=annotation["request_started"])
            with self.lock:
                if self.generations.get(key) != generation:
                    annotation = jev.report(capability, "blocked", "generation_changed", started=annotation["request_started"])
            result = jev.advice_view(annotation, value)
            result.update(generation=generation, binding_digest=jev.digest({"origin": access.origin, "session": session.key,
                "principal": session.principal.identity, "resource": resource, "generation": generation,
                "selected_input": value}), source_digests={row["id"]: jev.digest(row) for row in value.get("candidates", [])})
            return result
        finally:
            self.slots.release()
