from __future__ import annotations

import pytest

from anvil_serving.benchmarking.budgets import BudgetExhausted, NativeBudget


def limits(**overrides):
    return {
        "max_model_calls": 2, "max_completion_tokens": 16, "deadline_s": 10,
        "max_retries": 1, "min_free_disk_bytes": 1024, **overrides,
    }


def test_native_budget_counts_failed_transport_without_retry_and_stops_sends():
    calls = []

    def caller(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise OSError("transport failed")
        return {"response": {"usage": {"completion_tokens": 2}}}

    budget = NativeBudget(limits(), caller, timeout_s=10, clock=lambda: 0)
    with pytest.raises(OSError):
        budget(max_tokens=32, timeout=20)
    budget(max_tokens=8, timeout=20)
    with pytest.raises(BudgetExhausted):
        budget(max_tokens=8, timeout=20)
    assert calls == [{"max_tokens": 16, "timeout": 10}, {"max_tokens": 8, "timeout": 10}]
    result = budget.finish({"passed": True})
    assert result["passed"] is False
    assert result["state"] == "incomplete"
    assert result["budget"]["model_calls"] == 2
    assert result["budget"]["retries"] == 0
    assert result["budget"]["observed_completion_tokens"] is None
    assert result["budget"]["theoretical_completion_tokens"] == 32
    assert result["budget"]["limits"] == limits()


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


@pytest.mark.parametrize("usage", [None, {}, {"completion_tokens": None}, {"completion_tokens": True}])
def test_native_budget_unknown_usage_is_not_zero(usage):
    budget = NativeBudget(limits(), lambda **_: {"response": {"usage": usage}},
                          timeout_s=10, clock=lambda: 0)
    budget(max_tokens=8, timeout=10)
    assert budget.finish({})["budget"]["observed_completion_tokens"] is None


def test_native_exact_call_limit_can_complete_without_truncation():
    budget = NativeBudget(limits(max_model_calls=1),
                          lambda **_: {"response": {"usage": {"completion_tokens": 0}}},
                          timeout_s=10, clock=lambda: 0)
    budget(max_tokens=8, timeout=10)
    result = budget.finish({"passed": True})
    assert result["passed"] is True
    assert result["budget"]["exhausted"] is False
    assert result["budget"]["observed_completion_tokens"] == 0


def test_native_endpoint_token_cap_violation_retains_usage_and_blocks_more_sends():
    calls = []

    def caller(**kwargs):
        calls.append(kwargs)
        return {"response": {"usage": {"completion_tokens": 33}}}

    budget = NativeBudget(limits(), caller, timeout_s=10, clock=lambda: 0)
    response = budget(max_tokens=64, timeout=10)
    assert response["response"]["usage"]["completion_tokens"] == 33
    with pytest.raises(BudgetExhausted):
        budget(max_tokens=64, timeout=10)
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 16
    result = budget.finish({"passed": True})
    assert result["passed"] is False
    assert result["failure"]["code"] == "completion_token_cap_exceeded"
    assert result["budget"]["theoretical_completion_tokens"] == 32
    assert result["budget"]["observed_completion_tokens"] == 33
    assert result["budget"]["completion_token_cap_violation"] == {
        "model_call": 1, "observed_completion_tokens": 33, "max_completion_tokens": 16,
    }
