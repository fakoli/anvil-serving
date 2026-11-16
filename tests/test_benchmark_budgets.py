from __future__ import annotations

import pytest

from anvil_serving.benchmarking.budgets import BudgetExhausted, NativeBudget


def limits(**overrides):
    return {
        "max_model_calls": 2, "max_completion_tokens": 16, "deadline_s": 10,
        "max_retries": 1, "min_free_disk_bytes": 1024, **overrides,
    }


def test_native_deadline_bounds_each_timeout_and_retains_late_reply():
    now = [0.0]
    timeouts = []

    def caller(**kwargs):
        timeouts.append(kwargs["timeout"])
        now[0] += 3
        return {"response": {"usage": {"completion_tokens": 2}}}

    budget = NativeBudget(limits(), caller, timeout_s=5, clock=lambda: now[0])
    budget(max_tokens=8, timeout=20)
    assert budget(max_tokens=8, timeout=20)["response"]["usage"]["completion_tokens"] == 2
    assert timeouts == [5, 2]
    with pytest.raises(BudgetExhausted):
        budget(max_tokens=8, timeout=20)
    result = budget.finish({"passed": True})
    assert result["failure"]["budget"] == "deadline_s"
    assert result["budget"]["effective_deadline_s"] == 5
    assert result["budget"]["observed_completion_tokens"] == 4
    assert result["budget"]["elapsed_s"] == 6


def test_native_deadline_can_expire_before_first_send():
    now = [0]
    budget = NativeBudget(limits(), lambda **_: pytest.fail("sent after deadline"),
                          timeout_s=10, clock=lambda: now[0])
    now[0] = 10
    with pytest.raises(BudgetExhausted):
        budget(max_tokens=8, timeout=10)
    assert budget.finish({})["budget"]["model_calls"] == 0
