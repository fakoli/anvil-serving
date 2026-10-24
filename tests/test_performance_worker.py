"""Performance jobs share durable dispatch and preserve partial native evidence."""
import json
from types import SimpleNamespace

import pytest

from anvil_serving.benchmarking import performance, worker
from anvil_serving.benchmarking.jobs import BenchmarkJobError, JOB_SPEC_SCHEMA
from anvil_serving.benchmarking.profiles import load_profile
from anvil_serving.benchmarking.submission import validate_submission_spec
from anvil_serving.control_plane.controller.store import BenchmarkJobStore


def spec():
    return {
        "schema": JOB_SPEC_SCHEMA, "run_id": "perf-run", "ownership_id": "pilot",
        "suite": "performance", "profile": "pilot-v2", "timeout_s": 600,
        "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary"},
        "worker": {"id": "benchmark-worker"}, "submitted_at": "2026-10-24T12:00:00Z",
        "parameters": {"runner": "lil", "workload": {"input_tokens": 0, "output_tokens": 256},
                       "concurrency": 4, "request_count": 16, "temperature": 0, "top_p": 1},
    }


def test_admission_dispatch_and_configured_identity(monkeypatch, tmp_path):
    value = spec()
    value["parameters"]["offline"] = True
    admitted = validate_submission_spec(value)
    assert admitted["selection"]["concurrency"] == 4
    assert admitted["budgets"]["max_model_calls"] == 128
    observed = {}

    def plan(profile, assets, **kwargs):
        observed.update(kwargs)
        return {"selected": True}

    monkeypatch.setattr(performance, "build_performance_run_plan", plan)
    monkeypatch.setattr(performance, "run_performance_benchmark", lambda plan: {"state": "completed", **plan})
    monkeypatch.setattr(worker, "build_swe_run_plan", lambda *_a, **_k: pytest.fail("SWE dispatch"))
    profile = load_profile("pilot-v2")
    result = worker._run_suite(SimpleNamespace(run_root=str(tmp_path)), {"spec": value}, profile, {})
    assert result == {"state": "completed", "selected": True}
    assert observed["endpoint"] == value["endpoint"]
    assert observed["parameters"] == {k: v for k, v in value["parameters"].items() if k != "offline"}
    assert observed["ownership_id"] == "pilot" and observed["timeout_s"] == 600
    identities = worker._identities(value, profile, None, None)
    assert identities["concurrency"] == {"requests": 4, "workers": 1, "status": "configured"}
    assert identities["dataset"]["tokenizer_match"] == "unverified"


@pytest.mark.parametrize("stopped,corrupt", [(False, False), (True, True), (True, False)])
def test_cancel_retains_native_bytes_and_requires_verified_stop(monkeypatch, tmp_path, stopped, corrupt):
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(spec())
    store.claim("perf-run")
    run = tmp_path / "runs" / "pilot" / "perf-run"
    work = run / "work" / "performance"
    work.mkdir(parents=True)
    canonical = run / "performance"
    canonical.mkdir()
    events = canonical / "requests.jsonl"
    events.write_text('{"attempt":1}\n')
    if corrupt:
        (canonical / "unsafe").symlink_to(tmp_path)
    monkeypatch.setattr(worker, "_stop_owned_worker", lambda *_a, **_k: {"stopped": stopped})
    cancelled = worker.cancel_benchmark_job(store, "perf-run")
    assert cancelled["state"] == "cancelled"
    assert cancelled["cleanup_deferred"] is not (stopped and not corrupt)
    assert work.exists() is not (stopped and not corrupt)
    assert json.loads(events.read_text()) == {"attempt": 1}
    assert store.artifact("perf-run")["results"]["evidence"]["completeness"] == "cancelled"


def test_cleanup_refuses_changed_canonical_artifacts(monkeypatch, tmp_path):
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(spec())
    store.claim("perf-run")
    store.transition("perf-run", "failed", failure={"class": "worker_runtime", "code": "fixture"})
    run = tmp_path / "runs" / "pilot" / "perf-run"
    (run / "work").mkdir(parents=True)
    (run / "performance").mkdir()
    events = run / "performance" / "requests.jsonl"
    events.write_text('{"attempt":1}\n')
    assert performance.retain_performance_artifacts(run_path=str(run))["state"] == "incomplete"
    events.write_text('{"attempt":2}\n')
    monkeypatch.setattr(worker, "_worker_group_stopped", lambda *_a, **_k: True)
    with pytest.raises(BenchmarkJobError, match="could not be verified"):
        worker.cleanup_benchmark_job(store, "perf-run")
    assert (run / "work").exists()


@pytest.mark.parametrize("overrides", [
    {"duration_s": 30}, {"warmup_requests": 1}, {"cache_condition": "cold"},
    {"workload": {"input_tokens": 1024, "output_tokens": 256}}, {"request_count": 129},
])
def test_unsupported_or_over_budget_selection_rejected_before_submission(overrides):
    value = spec()
    value["parameters"].update(overrides)
    with pytest.raises(BenchmarkJobError):
        validate_submission_spec(value)


def test_preparation_uses_selected_suite_and_binds_environments(monkeypatch, tmp_path):
    from anvil_serving.benchmarking import harnesses, performance_prepare
    profile = load_profile("pilot-v2")
    observed = []
    monkeypatch.setattr(harnesses.platform, "system", lambda: "Linux")

    def repository(**kwargs):
        observed.append(kwargs["name"])
        return {"revision": kwargs["adapter"]["revision"]}, []

    def environments(pinned, assets, **kwargs):
        assert pinned["content_sha256"] == profile["content_sha256"]
        assert set(assets) == {"lil", "aiperf"}
        assert kwargs["runner"] is None  # Production preparation owns its bounded runner.
        return {"lil": {"fixture": 1}, "aiperf": {"fixture": 2}}

    monkeypatch.setattr(harnesses, "_prepare_repository", repository)
    monkeypatch.setattr(performance_prepare, "prepare_performance_environments", environments)
    manifest = harnesses.prepare_harness_assets(profile, suite="performance", run_root=str(tmp_path / "runs"),
        ownership_id="pilot", run_id="prep", cache_root=str(tmp_path / "cache"))
    assert observed == ["lil", "aiperf"]
    assert manifest["performance_environments"]["aiperf"] == {"fixture": 2}
    assert len(manifest["cache_identity"]) == 64
    monkeypatch.setattr(harnesses.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(harnesses, "_prepare_repository", lambda **_k: pytest.fail("download on unsupported platform"))
    with pytest.raises(BenchmarkJobError, match="requires Linux"):
        harnesses.prepare_harness_assets(profile, suite="performance", run_root=str(tmp_path / "other"),
            ownership_id="pilot", run_id="prep", cache_root=str(tmp_path / "cache"))
    assert not (tmp_path / "other").exists()
