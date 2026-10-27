"""One supervisor-owned monotonic lifetime and native queue admission gate."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import math
import os
import re
import secrets
import time


FEATURE = "anvil.media.runtime-reservation.v1"
ENVIRONMENT = "ANVIL_MEDIA_WORKER_RUNTIME"
SCHEMA = "anvil-serving.worker-runtime/v1"
REFUSAL_SCHEMA = "anvil-serving.worker-refusal/v1"


def _number(value, minimum: float, maximum: float) -> bool:
    return (type(value) in (int, float) and minimum <= value <= maximum
            and math.isfinite(value))


def child_environment(policy: dict) -> tuple[dict[str, str], int]:
    """Override inherited runtime metadata once, before spawning the child."""
    started = time.monotonic_ns()
    deadline = started + int(policy["timeout_seconds"] * 1_000_000_000)
    value = {"runId": secrets.token_hex(16), "startedNs": started, "deadlineNs": deadline,
             "totalSeconds": policy["timeout_seconds"],
             "reservationSeconds": policy.get("admission_seconds", 0),
             "marginSeconds": policy.get("admission_margin_seconds", 0)}
    environment = dict(os.environ)
    environment[ENVIRONMENT] = json.dumps(value, allow_nan=False, separators=(",", ":"))
    return environment, deadline


@dataclass(frozen=True)
class _Runtime:
    run_id: str
    started: int
    deadline: int
    total: float
    reservation: float
    margin: float


@lru_cache(maxsize=1)
def _runtime() -> _Runtime | None:
    # Snapshot once: a request or later environment edit cannot renew the deadline.
    raw = os.environ.get(ENVIRONMENT, "")
    if not raw or len(raw) > 1024:
        return None
    try:
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {
                "runId", "startedNs", "deadlineNs", "totalSeconds", "reservationSeconds", "marginSeconds"}
                or not isinstance(value["runId"], str)
                or not re.fullmatch(r"[a-f0-9]{32}", value["runId"])
                or type(value["startedNs"]) is not int or type(value["deadlineNs"]) is not int
                or not 0 < value["startedNs"] < value["deadlineNs"] < 2**63
                or not _number(value["totalSeconds"], 30, 7200)
                or not _number(value["reservationSeconds"], .001, 7200)
                or not _number(value["marginSeconds"], 0, 300)
                or value["reservationSeconds"] + value["marginSeconds"] >= value["totalSeconds"]
                or value["deadlineNs"] - value["startedNs"] != int(value["totalSeconds"] * 1_000_000_000)):
            return None
        return _Runtime(value["runId"], value["startedNs"], value["deadlineNs"],
                        value["totalSeconds"], value["reservationSeconds"], value["marginSeconds"])
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def runtime_status(*, busy: bool = False) -> dict:
    runtime = _runtime()
    now = time.monotonic_ns()
    if runtime is None or now < runtime.started:
        return {"schema": SCHEMA, "runId": "", "totalSeconds": 0, "reservationSeconds": 0,
                "marginSeconds": 0, "remainingSeconds": 0, "acceptingNewWork": False,
                "reason": "worker_budget_unknown"}
    remaining = max(0, (runtime.deadline - now) / 1_000_000_000)
    reason = ("worker_busy" if busy else "worker_budget_insufficient"
              if remaining <= runtime.reservation + runtime.margin else "ready")
    return {"schema": SCHEMA, "runId": runtime.run_id, "totalSeconds": runtime.total,
            "reservationSeconds": runtime.reservation, "marginSeconds": runtime.margin,
            "remainingSeconds": remaining, "acceptingNewWork": reason == "ready", "reason": reason}


class WorkerRuntimeRefused(Exception):
    """Only raised before native queue insertion; safe to report as unaccepted."""

    def __init__(self, reason: str, request_run_id: str, client_id: str):
        super().__init__(reason)
        self.reason, self.request_run_id, self.client_id = reason, request_run_id, client_id

    def response(self) -> dict:
        return {"schema": REFUSAL_SCHEMA, "accepted": False, "reason": self.reason,
                "requestRunId": self.request_run_id, "clientId": self.client_id}


def require_admission(extra_data, *, busy: bool) -> None:
    """Call under PromptQueue.mutex immediately before heap insertion."""
    extra = extra_data if isinstance(extra_data, dict) else {}
    requested = extra.get("anvil_runtime")
    requested = requested if isinstance(requested, dict) else {}
    run_id, client_id = requested.get("runId", ""), extra.get("client_id", "")
    run_id = run_id if isinstance(run_id, str) and re.fullmatch(r"[a-f0-9]{32}", run_id) else ""
    client_id = client_id if isinstance(client_id, str) and re.fullmatch(r"anvil-[A-Za-z0-9_-]{1,128}", client_id) else ""
    status = runtime_status(busy=busy)
    reason = status["reason"]
    if reason != "worker_budget_unknown":
        if (not run_id or not client_id or set(requested) != {"runId", "requiredSeconds"}
                or not _number(requested.get("requiredSeconds"), .001, status["reservationSeconds"])):
            reason = "worker_reservation_invalid"
        elif run_id != status["runId"]:
            reason = "worker_instance_changed"
    if reason != "ready":
        raise WorkerRuntimeRefused(reason, run_id, client_id)
