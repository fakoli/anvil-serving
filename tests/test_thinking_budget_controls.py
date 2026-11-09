"""A requested thinking ceiling must survive the real Aider/router seams."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from anvil_serving.benchmarking import aider, aider_adapter, campaign_execution, harnesses, worker
from anvil_serving.benchmarking.jobs import BenchmarkJobError
from anvil_serving.benchmarking.preflight import run_benchmark_preflight
from anvil_serving.benchmarking.request_controls import resolve_job_request_controls
from anvil_serving.benchmarking.submission import validate_submission_spec
from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.dialects.openai import OpenAIDialect
from anvil_serving.router.internal import BackendClientError
from tests.router.helpers import make_tier
from tests.test_aider_adapter import offline_model_client
from tests.test_aider_benchmark import prepared_plan_inputs
from tests.test_aider_worker import spec
from tests.test_benchmark_preflight import _opener


def test_aider_budget_survives_admission_worker_plan_and_wire_guard(tmp_path, monkeypatch):
    profile, manifest, kwargs, _, rows, _ = prepared_plan_inputs(tmp_path, monkeypatch)
    job = spec()
    job["parameters"].update(instance_ids=[rows[0]["task_id"]], reasoning_effort="max",
                             thinking_budget_tokens=512)
    admitted = validate_submission_spec(job)
    assert admitted["request_controls"]["thinking_budget_tokens"] == 512
    monkeypatch.setenv("ANVIL_BENCHMARK_CACHE_ROOT", kwargs["cache_root"])
    monkeypatch.setattr(aider, "run_aider_benchmark", lambda plan: plan)
    plan = worker._run_suite(SimpleNamespace(run_root=kwargs["run_root"]),
                             {"spec": job}, profile, manifest)
    assert plan["request_controls"]["thinking_budget_tokens"] == 512
    job["profile_sha256"] = profile["content_sha256"]
    monkeypatch.setattr(harnesses, "prepare_harness_assets", lambda *_a, **_kw: manifest)
    prepared = campaign_execution.prepare_child(job, run_root=kwargs["run_root"], cache_root=kwargs["cache_root"])
    campaign_plan = json.loads((tmp_path / "runs/pilot/aider-run" / prepared["path"]).read_text())["plan"]
    assert campaign_plan["request_controls"]["thinking_budget_tokens"] == 512
    controls = aider_adapter.request_body_controls(plan["request_controls"])
    assert controls["thinking_budget"] == 512 and controls["reasoning_effort"] == "max"

    Model, Client, sent = offline_model_client()
    recorder = aider_adapter.EventRecorder(tmp_path / "events.jsonl")
    budget = aider_adapter.SuiteBudget(plan["budgets"], recorder)
    restore = aider_adapter.install_transport_guard(Client, Model, endpoint=plan["endpoint"],
        controls=plan["request_controls"], budget=budget, recorder=recorder)
    body = {"model": "llm.primary", "max_tokens": plan["budgets"]["max_completion_tokens"], **controls}
    try:
        with pytest.raises(aider_adapter.BudgetStopped, match="controls"):
            Model().send_completion({key: value for key, value in body.items() if key != "thinking_budget"})
        assert not sent
        Model().send_completion(body)
        assert len(sent) == 1
    finally:
        restore()
        recorder.close(outcome="completed")


@pytest.mark.parametrize("budget", [True, None, 0, -1, 1.5, 16384])
def test_aider_rejects_invalid_or_no_headroom_thinking_limits(budget):
    job = spec()
    job.update(profile="aider-diagnostic-16k-v1")
    job["parameters"]["thinking_budget_tokens"] = budget
    with pytest.raises(BenchmarkJobError):
        validate_submission_spec(job)


def test_preflight_records_request_without_claiming_engine_enforcement(tmp_path):
    job = spec()
    job.update(profile="aider-diagnostic-16k-v1")
    job["parameters"].update(thinking_budget_tokens=4096, model_host_id="model-host")
    artifact = run_benchmark_preflight(job, run_root=str(tmp_path), opener=_opener([{
        "id": "llm.primary", "max_output_tokens": 32768, "context_length": 131072}]))
    assert artifact["passed"]
    assert artifact["generation_budget"]["thinking_limit"] == {
        "requested_tokens": 4096, "wire_field": "thinking_budget",
        "endpoint_enforcement": "unverified", "answer_tokens_reserved": None,
    }
    assert artifact["generation_budget"]["reasoning_tokens_reserved"] is None
    assert "thinking_budget_tokens" not in resolve_job_request_controls({})


@pytest.mark.parametrize("suite", ["context", "performance"])
def test_other_suites_reject_an_unimplemented_control(suite):
    job = spec()
    job.update(suite=suite)
    job["parameters"]["thinking_budget_tokens"] = 512
    with pytest.raises(BenchmarkJobError) as error:
        validate_submission_spec(job)
    assert error.value.code == "suite_incompatible_parameter"


def test_router_preserves_validated_budget_and_explicit_tier_precedence():
    request = OpenAIDialect().parse_request({"model": "llm.primary",
        "messages": [{"role": "user", "content": "Solve the task."}],
        "max_completion_tokens": 16, "thinking_budget": 4})
    tier = replace(make_tier("openai"), extra_body_defaults={"thinking_budget": 8})
    backend = RelayBackend(tier, env={"EXAMPLE_KEY": "fixture"})
    assert backend._build_body(request)["thinking_budget"] == 4
    hard = RelayBackend(replace(tier, extra_body={"thinking_budget": 6}), env={"EXAMPLE_KEY": "fixture"})
    assert hard._build_body(request)["thinking_budget"] == 6
    invalid = RelayBackend(replace(tier, extra_body={"max_tokens": 4}), env={"EXAMPLE_KEY": "fixture"})
    with pytest.raises(BackendClientError):
        invalid._build_body(request)


@pytest.mark.parametrize("budget,cap,dialect", [(True, 16, "openai"), (None, 16, "openai"),
    (0, 16, "openai"), (16, 16, "openai"), (4, None, "openai"), (4, 16, "anthropic")])
def test_router_rejects_invalid_or_cross_dialect_budget_before_dispatch(budget, cap, dialect):
    sent = []
    backend = RelayBackend(make_tier(dialect), env={"EXAMPLE_KEY": "fixture"},
        transport=lambda *_a, **_kw: sent.append(True) or b"{}")
    request = OpenAIDialect().parse_request({"model": "llm.primary",
        "messages": [{"role": "user", "content": "Solve the task."}],
        "max_tokens": cap, "thinking_budget": budget})
    with pytest.raises(BackendClientError) as error:
        list(backend.generate(request))
    assert error.value.status == 400 and not sent
