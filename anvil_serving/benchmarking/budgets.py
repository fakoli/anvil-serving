"""Suite-scoped accounting around the native, non-retrying chat transport."""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from .jobs import BenchmarkJobError


class BudgetExhausted(BenchmarkJobError):
    def __init__(self, limit: str):
        super().__init__("budget_exhausted", f"suite budget exhausted: {limit}", {"budget": limit})


class NativeBudget:
    """Bound sends and socket timeouts; detect overruns without discarding replies.

    A socket timeout is not a hard process deadline. The owning worker retains
    responsibility for cancellation of an in-flight request.
    """

    def __init__(
        self, limits: Mapping[str, Any], caller: Callable[..., dict[str, Any]],
        *, timeout_s: float, clock: Callable[[], float] | None = None,
    ):
        self.limits = dict(limits)
        self.caller = caller
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self.deadline_s = min(float(limits["deadline_s"]), float(timeout_s))
        self.model_calls = 0
        self.completion_tokens = 0
        self.usage_complete = True
        self.exhaustion_reason: str | None = None
        self.token_cap_violation: dict[str, int] | None = None

    def check_deadline(self) -> float:
        remaining = self.deadline_s - (self.clock() - self.started)
        if remaining <= 0 and self.exhaustion_reason is None:
            self.exhaustion_reason = "deadline_s"
        return remaining

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        remaining = self.check_deadline()
        if self.exhaustion_reason is None and self.model_calls >= self.limits["max_model_calls"]:
            self.exhaustion_reason = "max_model_calls"
        if self.exhaustion_reason:
            raise BudgetExhausted(self.exhaustion_reason)
        kwargs["max_tokens"] = min(kwargs["max_tokens"], self.limits["max_completion_tokens"])
        kwargs["timeout"] = min(float(kwargs["timeout"]), remaining)
        self.model_calls += 1
        try:
            result = self.caller(*args, **kwargs)
        except Exception:
            self.usage_complete = False
            self.check_deadline()
            raise
        response = result.get("response")
        usage = response.get("usage") if isinstance(response, Mapping) else None
        tokens = usage.get("completion_tokens") if isinstance(usage, Mapping) else None
        if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
            self.completion_tokens += tokens
            if tokens > self.limits["max_completion_tokens"]:
                self.exhaustion_reason = "max_completion_tokens"
                self.token_cap_violation = {
                    "model_call": self.model_calls,
                    "observed_completion_tokens": tokens,
                    "max_completion_tokens": self.limits["max_completion_tokens"],
                }
        else:
            self.usage_complete = False
        self.check_deadline()
        return result

    def finish(self, result: dict[str, Any]) -> dict[str, Any]:
        self.check_deadline()
        result["budget"] = {
            "limits": dict(self.limits),
            "effective_deadline_s": self.deadline_s,
            "model_calls": self.model_calls,
            "retries": 0,
            "observed_completion_tokens": (
                self.completion_tokens if self.model_calls and self.usage_complete else None
            ),
            "theoretical_completion_tokens": (
                self.limits["max_model_calls"] * self.limits["max_completion_tokens"]
            ),
            "elapsed_s": self.clock() - self.started,
            "exhausted": self.exhaustion_reason is not None,
            "exhaustion_reason": self.exhaustion_reason,
            "completion_token_cap_violation": self.token_cap_violation,
            "deadline_enforcement": "before-send, socket-timeout, and return checks; no hard process cutoff",
        }
        if self.exhaustion_reason:
            result.update({
                "state": "incomplete", "status": "budget_exhausted", "passed": False,
                "truncated": True,
                "failure": {
                    "class": "budget_exhausted", "code": "budget_exhausted",
                    "message": f"suite budget exhausted: {self.exhaustion_reason}",
                    "budget": self.exhaustion_reason,
                },
            })
            if self.token_cap_violation:
                result["failure"].update({
                    "code": "completion_token_cap_exceeded",
                    "message": "endpoint reported completion tokens above the declared per-request cap",
                })
        return result
