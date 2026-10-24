from __future__ import annotations

import json

import pytest

from anvil_serving.benchmarking.jobs import JOB_SPEC_SCHEMA
from anvil_serving.control_plane.mcp.errors import ToolError
from anvil_serving.control_plane.mcp.tools import benchmarks


def _spec():
    return {
        "schema": JOB_SPEC_SCHEMA,
        "run_id": "preflight-remote-001",
        "ownership_id": "campaign-001",
        "suite": "context",
        "profile": "context-smoke-v1",
        "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "deepseek"},
        "worker": {"id": "benchmark-worker"},
        "submitted_at": "2026-08-03T12:00:00Z",
        "timeout_s": 600,
        "parameters": {},
    }


def test_controller_preflight_preserves_typed_artifact(monkeypatch, tmp_path):
    monkeypatch.setenv("ANVIL_BENCHMARK_JOB_DB", str(tmp_path / "jobs.sqlite3"))
    monkeypatch.setenv("ANVIL_BENCHMARK_RUN_ROOT", str(tmp_path / "runs"))
    expected = {"schema": "anvil-serving.benchmark-preflight/v1", "passed": True}
    monkeypatch.setattr(benchmarks, "run_benchmark_preflight", lambda *_args, **_kwargs: expected)
    result = benchmarks.tool_benchmark_job_preflight(
        {
            "suite": "context",
            "spec_json": json.dumps(_spec()),
            "requirements_json": json.dumps({"min_free_disk_bytes": 1}),
        }
    )
    assert result == {"ok": True, "data": expected}


def test_controller_preflight_rejects_suite_mismatch():
    result = benchmarks.FAMILY.tools["benchmark_job_preflight"]["handler"]
    try:
        result({"suite": "swe", "spec_json": json.dumps(_spec())})
    except Exception as exc:
        assert getattr(exc, "code", None) == "suite_mismatch"
    else:
        raise AssertionError("suite mismatch was accepted")


def test_profile_and_suite_schemas_share_the_closed_registry():
    from anvil_serving.benchmarking.profiles import PROFILE_NAMES, SUITE_NAMES

    for tool in benchmarks.FAMILY.tools.values():
        properties = tool["inputSchema"]["properties"]
        if "suite" in properties:
            assert set(properties["suite"]["enum"]) == SUITE_NAMES
        if "profile" in properties:
            assert set(properties["profile"]["enum"]) == PROFILE_NAMES


def test_harness_prepare_accepts_v2_and_rejects_profile_paths(monkeypatch, tmp_path):
    import pytest

    from anvil_serving.control_plane.mcp.errors import ToolError

    monkeypatch.setenv("ANVIL_BENCHMARK_JOB_DB", str(tmp_path / "jobs.sqlite3"))
    monkeypatch.setenv("ANVIL_BENCHMARK_RUN_ROOT", str(tmp_path / "runs"))
    prepared = []

    def prepare(profile, **kwargs):
        prepared.append(profile)
        assert kwargs["suite"] == "context"
        return {"profile": profile["name"]}

    monkeypatch.setattr(benchmarks, "prepare_harness_assets", prepare)
    args = {"suite": "context", "profile": "pilot-v2", "run_id": "pilot-001", "ownership_id": "campaign-001", "confirm": True}
    assert benchmarks.tool_benchmark_harness_prepare(args)["data"] == {"profile": "pilot-v2"}
    assert prepared[0]["suites"]["context"]["budgets"]["max_model_calls"] == 128
    with pytest.raises(ToolError) as exc:
        benchmarks.tool_benchmark_harness_prepare({**args, "profile": "/tmp/pilot-v2.json"})
    assert exc.value.code == "profile_not_found"
    assert len(prepared) == 1


def _external_spec(suite, runner):
    parameters = {} if suite == "aider" else {
        "runner": runner, "workload": {"input_tokens": 0, "output_tokens": 32},
        "request_count": 1,
    }
    return {**_spec(), "suite": suite, "profile": "pilot-v2", "parameters": parameters}


@pytest.mark.parametrize("suite,runner", [("aider", None), ("performance", "lil"), ("performance", "aiperf")])
def test_external_submission_retries_original_pin_without_relaunch_and_retains_lifecycle(monkeypatch, tmp_path, suite, runner):
    from anvil_serving.benchmarking import submission
    from anvil_serving.control_plane.controller.store import BenchmarkJobStore

    store = BenchmarkJobStore(str(tmp_path / "jobs.sqlite3"), run_root=str(tmp_path / "runs"))
    monkeypatch.setattr(benchmarks, "_benchmark_job_store", lambda: store)
    launches = []
    monkeypatch.setattr(benchmarks, "launch_benchmark_job", lambda **kw: launches.append(kw) or {"launched": True})
    spec = _external_spec(suite, runner)
    args = {"suite": suite, "spec_json": json.dumps(spec), "confirm": True}
    submitted = benchmarks.tool_benchmark_job_submit(args)["data"]
    job = submitted["job"]
    assert submitted["disposition"] == "submitted"
    assert job["state"] == "queued" and "profile_sha256" in job["spec"]

    monkeypatch.setattr(submission, "load_profile", lambda *_a: pytest.fail("retry must retain original admission"))
    for retry in (spec, job["spec"]):
        result = benchmarks.tool_benchmark_job_submit({**args, "spec_json": json.dumps(retry)})["data"]
        assert result["disposition"] == "existing" and result["job"] == job
        assert result["worker"]["launched"] is False
    assert len(launches) == 1
    for change in ({"profile_sha256": "0" * 64}, {"timeout_s": 601}):
        with pytest.raises(ToolError) as exc:
            benchmarks.tool_benchmark_job_submit({**args, "spec_json": json.dumps({**spec, **change})})
        assert exc.value.code == "run_id_conflict"

    identity = {"suite": suite, "run_id": spec["run_id"]}
    assert benchmarks.tool_benchmark_job_status(identity)["data"] == job
    store.append_log(spec["run_id"], level="info", message="queued for test")
    assert benchmarks.tool_benchmark_job_logs(identity)["data"]["entries"][0]["message"] == "queued for test"
    assert benchmarks.tool_benchmark_job_cancel({**identity, "confirm": True})["data"]["state"] == "cancelled"
    assert benchmarks.tool_benchmark_job_artifact(identity)["data"]["status"] == "cancelled"
    terminal = benchmarks.tool_benchmark_job_submit(args)["data"]
    assert terminal["job"]["state"] == "cancelled" and terminal["worker"]["launched"] is False
    assert len(launches) == 1


@pytest.mark.parametrize("suite,parameters,code", [
    ("aider", {"runner": "lil"}, "suite_incompatible_parameter"),
    ("performance", {}, "bad_performance_parameters"),
])
def test_external_submission_rejects_invalid_parameters_before_job_creation(monkeypatch, tmp_path, suite, parameters, code):
    from anvil_serving.control_plane.controller.store import BenchmarkJobStore

    store = BenchmarkJobStore(str(tmp_path / "jobs.sqlite3"), run_root=str(tmp_path / "runs"))
    monkeypatch.setattr(benchmarks, "_benchmark_job_store", lambda: store)
    monkeypatch.setattr(benchmarks, "launch_benchmark_job", lambda **_kw: pytest.fail("invalid job launched"))
    spec = {**_external_spec(suite, "lil"), "parameters": parameters}
    with pytest.raises(ToolError) as exc:
        benchmarks.tool_benchmark_job_submit({"suite": suite, "spec_json": json.dumps(spec), "confirm": True})
    assert exc.value.code == code
    assert store.status(spec["run_id"]) is None


def test_submit_confirmation_and_suite_match_precede_store(monkeypatch):
    monkeypatch.setattr(benchmarks, "_benchmark_job_store", lambda: pytest.fail("store opened"))
    args = {"suite": "performance", "spec_json": json.dumps(_external_spec("aider", None))}
    for confirmed, code in ((False, "confirmation_required"), (True, "suite_mismatch")):
        with pytest.raises(ToolError) as exc:
            benchmarks.tool_benchmark_job_submit({**args, "confirm": confirmed})
        assert exc.value.code == code
