"""Shared lower-layer contracts must retain old entrypoints and launch guards."""
import json

import pytest

from anvil_serving.benchmarking import (
    aider, aider_adapter, aider_containment, aider_prepare, harnesses, harness_runtime,
    performance, performance_adapter, performance_prepare,
)
from anvil_serving.benchmarking.jobs import BenchmarkJobError


def test_legacy_helpers_reexport_the_shared_implementations():
    for name in ("resolve_container_binary", "_default_runner", "_bounded_text", "_run",
                 "_directory_bytes", "_normalized_freeze"):
        assert getattr(harnesses, name) is getattr(harness_runtime, name)
    for name in ("SOURCE_HASHES", "task_snapshot", "validate_task_id", "load_test_callback",
                 "contained_test_callback"):
        assert getattr(aider_adapter, name) is getattr(aider_containment, name)
    for name in ("SOURCE_HASHES", "instrumentation", "validate_supported_parameters"):
        assert getattr(performance_adapter, name) is getattr(performance_prepare, name)


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
