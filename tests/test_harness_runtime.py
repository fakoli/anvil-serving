"""Shared lower-layer contracts must retain old entrypoints and launch guards."""
import json

import pytest

from anvil_serving.benchmarking import (
    aider, aider_adapter, aider_prepare, performance,
    performance_adapter, performance_prepare,
)
from anvil_serving.benchmarking.jobs import BenchmarkJobError


@pytest.mark.parametrize("planner,adapter,validator,launch,code", [
    (aider, aider_adapter, aider_prepare.verify_aider_plan, "run_adapter", "bad_aider_plan"),
    (performance, performance_adapter, performance_prepare.verify_performance_plan,
     "run_plan", "bad_performance_plan"),
])
def test_planner_and_adapter_reject_changed_plan_before_launch(
    tmp_path, monkeypatch, planner, adapter, validator, launch, code,
):
    # This checks the real validator at all three callers, not a stubbed success path.
    monkeypatch.setattr(performance_prepare.sys, "platform", "linux")
    plan = {"schema": getattr(planner, "AIDER_PLAN_SCHEMA", None)
            or planner.PERFORMANCE_PLAN_SCHEMA, "plan_sha256": "0" * 64}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(adapter, launch, lambda _: pytest.fail("unverified adapter launch"))
    for call in (lambda: planner.verify_plan(plan), lambda: validator(plan),
                 lambda: adapter.main(["--plan", str(path)])):
        with pytest.raises(BenchmarkJobError) as caught:
            call()
        assert caught.value.code == code
