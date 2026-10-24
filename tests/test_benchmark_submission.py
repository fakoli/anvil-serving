from __future__ import annotations

import pytest

from anvil_serving.benchmarking.jobs import BenchmarkJobError, JOB_SPEC_SCHEMA
from anvil_serving.benchmarking.submission import validate_submission_spec


def _spec(parameters, *, suite="swe"):
    return {
        "schema": JOB_SPEC_SCHEMA,
        "run_id": "regrade-001",
        "ownership_id": "campaign-001",
        "suite": suite,
        "profile": "smoke",
        "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary"},
        "worker": {"id": "benchmark-worker"},
        "submitted_at": "2026-10-24T12:00:00Z",
        "timeout_s": 600,
        "parameters": parameters,
    }


def test_regrade_admission_binds_new_run_to_retained_source():
    assert validate_submission_spec(_spec({"operation": "regrade", "source_run_id": "source-001"})) == {
        "profile": "smoke", "operation": "regrade", "source_run_id": "source-001",
    }


@pytest.mark.parametrize("parameters,code", [
    ({"operation": []}, "bad_swe_operation"),
    ({"operation": "other"}, "bad_swe_operation"),
    ({"operation": "regrade"}, "bad_identifier"),
    ({"operation": "regrade", "source_run_id": "../source"}, "bad_identifier"),
    ({"operation": "regrade", "source_run_id": "regrade-001"}, "bad_regrade_source"),
    ({"operation": "regrade", "source_run_id": "source", "temperature": 1}, "bad_regrade_parameters"),
    ({"operation": "regrade", "source_run_id": "source", "prompt_contract": "disclosed/v2"}, "bad_regrade_parameters"),
    ({"source_run_id": "source"}, "bad_regrade_source"),
    ({"prompt_contract": {}}, "bad_prompt_contract"),
])
def test_regrade_and_prompt_controls_fail_closed(parameters, code):
    with pytest.raises(BenchmarkJobError) as exc:
        validate_submission_spec(_spec(parameters))
    assert exc.value.code == code


@pytest.mark.parametrize("suite", ["context", "agentic"])
def test_swe_controls_cannot_change_other_suites(suite):
    with pytest.raises(BenchmarkJobError, match="unsupported by this suite"):
        validate_submission_spec(_spec({"operation": "regrade", "source_run_id": "source"}, suite=suite))


@pytest.mark.parametrize("surface", ["cli", "mcp"])
def test_cleanup_surface_checks_durable_owner_before_dispatch(monkeypatch, surface):
    from anvil_serving.benchmarking import jobs_cli
    from anvil_serving.control_plane.mcp.tools import benchmarks
    from anvil_serving.control_plane.mcp.errors import ToolError

    class Store:
        def status(self, run_id):
            assert run_id == "source"
            return {"spec": {"suite": "swe", "ownership_id": "owner"}, "state": "completed"}

    calls = []
    module = jobs_cli if surface == "cli" else benchmarks
    monkeypatch.setattr(module, "_store" if surface == "cli" else "_benchmark_job_store", Store)
    monkeypatch.setattr(module, "cleanup_benchmark_job", lambda store, run: calls.append(run) or {"cleaned": True})

    def cleanup(owner):
        if surface == "cli":
            return jobs_cli.run(["swe", "cleanup", "--run-id", "source", "--ownership-id", owner, "--confirm"])
        return benchmarks.tool_benchmark_harness_cleanup({
            "suite": "swe", "run_id": "source", "ownership_id": owner, "confirm": True,
        })

    with pytest.raises((BenchmarkJobError, ToolError)) as exc:
        cleanup("other")
    assert exc.value.code == "ownership_mismatch"
    assert not calls
    cleanup("owner")
    assert calls == ["source"]


def test_unknown_suite_is_rejected_before_profile_lookup(monkeypatch):
    monkeypatch.setattr("anvil_serving.benchmarking.submission.load_profile", lambda _name: pytest.fail("profile lookup"))
    with pytest.raises(BenchmarkJobError) as exc:
        validate_submission_spec(_spec({}, suite="unknown"))
    assert exc.value.code == "unknown_suite"


def test_v2_admission_binds_profile_and_budgets_without_changing_v1():
    from anvil_serving.benchmarking.profiles import load_profile

    value = _spec({}, suite="context")
    original = validate_submission_spec(value)
    assert "budgets" not in original
    value["profile"] = "pilot-v2"
    result = validate_submission_spec(value)
    profile = load_profile("pilot-v2")
    assert result["profile_sha256"] == profile["content_sha256"]
    assert result["budgets"] == profile["suites"]["context"]["budgets"]
    assert result["effective_deadline_s"] == 600


@pytest.mark.parametrize("changes,code", [
    ({"timeout_s": 1801}, "profile_budget_exceeded"),
    ({"parameters": {"output_headroom_tokens": 2049}}, "profile_budget_exceeded"),
    ({"parameters": {"repetitions": 2}}, "profile_budget_exceeded"),
    ({"parameters": {"max_model_calls": 100000}}, "suite_incompatible_parameter"),
    ({"parameters": {"budgets": {"max_model_calls": 100000}}}, "suite_incompatible_parameter"),
])
def test_v2_submission_cannot_widen_or_silently_override_budgets(changes, code):
    value = _spec({}, suite="context")
    value.update(profile="pilot-v2", **changes)
    with pytest.raises(BenchmarkJobError) as exc:
        validate_submission_spec(value)
    assert exc.value.code == code
