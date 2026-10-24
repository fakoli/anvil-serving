from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from anvil_serving.benchmarking import performance as perf
from anvil_serving.benchmarking.jobs import BenchmarkJobError
from anvil_serving.benchmarking.profiles import load_profile


def parameters(runner="aiperf"):
    return {"runner": runner, "workload": {"input_tokens": 0 if runner == "lil" else 128, "output_tokens": 16},
            "concurrency": 1, "request_count": 2}


def native_plan(runner="aiperf"):
    return {"runner": runner, "parameters": {**parameters(runner), "cache_condition": "basic"},
            "endpoint": {"model": "llm.primary"}}


def aiperf_native():
    return {"schema_version": "1.4", "is_complete": True, "was_cancelled": False,
            "request_count": {"avg": 2, "unit": "requests", "count": 999},
            "error_request_count": {"avg": 0, "unit": "requests"},
            "time_to_first_token": {"avg": 12, "p99": 20, "unit": "ms", "count": 2},
            "time_to_first_output_token": {"avg": 19, "unit": "ms", "count": 2},
            "output_token_throughput_per_user": {"avg": 42, "unit": "tokens/sec/user", "count": 2}}


def lil_native():
    sample = {"completed": True, "ttft": 0.02, "latency": 0.1, "chunk_inter_token_latency_avg": 0.01, "output_tokens": 5}
    return {"metadata": {"model": "llm.primary", "decode_mode": "request-count"}, "results": [{
        "concurrency": 1, "request_count": 2, "completed_request_count": 2, "num_errors": 0,
        "aggregate_source": "openai_completed_usage", "aggregate_tps": 100,
        "ttft_avg": 0.02, "request_latency_avg": 0.1, "chunk_inter_token_latency_avg": 0.01,
        "measurement_seconds": 0.2, "request_samples": [sample, dict(sample)]}]}


@pytest.mark.parametrize("updates", [
    {"runner": "unmanaged"}, {"runner": []}, {"cache_condition": []}, {"concurrency": True},
    {"concurrency": 257}, {"request_count": 0}, {"request_count": 100001},
    {"duration_s": 10}, {"warmup_requests": 10001}, {"command": "untrusted"},
    {"workload": {"input_tokens": 5, "output_tokens": 10, "extra": 1}},
    {"workload": {"input_tokens": 5, "output_tokens": 4097}}, {"request_count": 129},
])
def test_parameters_reject_unknown_invalid_and_over_budget(updates):
    with pytest.raises(BenchmarkJobError):
        perf.validate_performance_parameters(load_profile("pilot-v2"), {**parameters(), **updates})


def test_typed_parameters_preserve_controls_and_reject_unqualified_modes():
    value = perf.validate_performance_parameters(load_profile("pilot-v2"), {
        **parameters(), "thinking_mode": "disabled", "temperature": 0})
    assert value["request_controls"]["chat_template_kwargs"] == {"enable_thinking": False}
    for change in ({"warmup_requests": 1}, {"cache_condition": "cold"},
                   {"cache_condition": "warm"}, {"cache_condition": "competing-pressure"}):
        with pytest.raises(BenchmarkJobError, match="performance|cache"):
            perf.validate_performance_parameters(load_profile("pilot-v2"), {**parameters(), **change})
    raw = parameters()
    raw.pop("request_count")
    with pytest.raises(BenchmarkJobError, match="finite"):
        perf.validate_performance_parameters(load_profile("pilot-v2"), {**raw, "duration_s": 30})
    with pytest.raises(BenchmarkJobError, match="zero padding"):
        perf.validate_performance_parameters(load_profile("pilot-v2"), {**parameters("lil"), "workload": {"input_tokens": 128, "output_tokens": 16}})


def test_aiperf_top_level_scalar_and_explicit_units_preserve_ttft_ttfo():
    summary = perf.normalize_performance_metrics(native_plan(), aiperf_native())
    metrics = summary["metrics"]
    assert summary["native_complete"] is True
    assert metrics["request_count"]["value"] == 2  # Never use distribution count as total requests.
    assert metrics["time_to_first_token"]["value"] == 12
    assert metrics["time_to_first_output_token"]["value"] == 19
    assert metrics["time_to_first_token"]["unit"] == "ms"
    assert metrics["output_token_throughput_per_user"]["unit"] == "tokens/sec/user"
    assert metrics["request_latency"]["value"] is None
    assert metrics["request_latency"]["unavailable_reason"]
    assert summary["cache_condition"]["status"] == summary["kernel_timing"]["status"] == "unknown"


@pytest.mark.parametrize("updates", [{"is_complete": False}, {"was_cancelled": True},
    {"incomplete_reason": "stalled"}, {"error_summary": [{"count": 1}]},
    {"error_request_count": {"avg": 1, "unit": "requests"}}, {"request_count": {"avg": 1, "unit": "requests"}}])
def test_aiperf_incomplete_flags_never_become_completed(updates):
    assert not perf.normalize_performance_metrics(native_plan(), {**aiperf_native(), **updates})["native_complete"]


def test_aiperf_unknown_unit_invalid_numbers_and_wrong_schema():
    native = aiperf_native()
    native["time_to_first_token"]["unit"] = "mystery"
    native["time_to_first_output_token"]["avg"] = float("nan")
    summary = perf.normalize_performance_metrics(native_plan(), native)
    assert summary["metrics"]["time_to_first_token"]["value"] is None
    assert summary["metrics"]["time_to_first_output_token"]["value"] is None
    native["schema_version"] = "1.3"
    with pytest.raises(ValueError, match="schema"):
        perf.normalize_performance_metrics(native_plan(), native)


def test_lil_usage_label_does_not_prove_tokens_and_native_defaults_are_unknown():
    native = lil_native()
    summary = perf.normalize_performance_metrics(native_plan("lil"), native)
    assert summary["native_complete"] is True
    assert summary["metrics"]["time_to_first_token"]["value"] == 0.02
    assert summary["metrics"]["inter_chunk_latency"]["value"] == 0.01
    for metric in ("output_token_throughput", "output_sequence_length", "inter_token_latency", "time_to_first_output_token"):
        assert summary["metrics"][metric]["value"] is None
    native["results"][0]["ttft_avg"] = 0
    assert perf.normalize_performance_metrics(native_plan("lil"), native)["metrics"]["time_to_first_token"]["value"] is None
    native["results"][0]["request_samples"][0]["completed"] = False
    assert not perf.normalize_performance_metrics(native_plan("lil"), native)["native_complete"]


def prepared_plan(tmp_path, monkeypatch, runner="aiperf"):
    from anvil_serving.benchmarking import performance_prepare as prep
    profile = load_profile("pilot-v2")
    cache = tmp_path / "cache"
    python = cache / "python"
    cache.mkdir()
    python.write_text("prepared-python-fixture")
    expected = hashlib.sha256(python.read_bytes()).hexdigest()
    def verify(receipt, *, cache_root, runner_name):
        assert cache_root == str(cache) and runner_name == runner
        if receipt != {"fixture": True} or hashlib.sha256(python.read_bytes()).hexdigest() != expected:
            raise BenchmarkJobError("performance_preparation_mismatch", "prepared bytes changed")
        return str(python)
    monkeypatch.setattr(prep, "verify_performance_environment", verify)
    manifest = {"schema": perf.HARNESS_ASSETS_SCHEMA, "suite": "performance", "profile_sha256": profile["content_sha256"],
        "assets": {name: {**profile["adapters"][name], "dirty": False} for name in ("lil", "aiperf")},
        "performance_environments": {runner: {"fixture": True}}}
    runs = tmp_path / "runs"
    runs.mkdir()
    kwargs = dict(endpoint={"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary", "auth_env": "TEST_KEY"},
        parameters=parameters(runner), run_root=str(runs), cache_root=str(cache), ownership_id="owner", run_id="run")
    return profile, manifest, kwargs, python


def test_plan_is_bound_to_prepared_interpreter_source_and_owned_artifacts(tmp_path, monkeypatch):
    profile, manifest, kwargs, python = prepared_plan(tmp_path, monkeypatch)
    plan = perf.build_performance_run_plan(profile, manifest, **kwargs)
    assert plan["command"][0] == str(python)
    assert plan["paths"]["work"].endswith("owner/run/work/performance")
    assert not Path(plan["run_path"]).exists()
    altered = copy.deepcopy(manifest)
    altered["assets"]["aiperf"]["revision"] = "0" * 40
    with pytest.raises(BenchmarkJobError, match="pinned"):
        perf.build_performance_run_plan(profile, altered, **kwargs)
    python.write_text("drift")
    with pytest.raises(BenchmarkJobError, match="bytes changed"):
        perf.run_performance_benchmark(plan, command_runner=lambda *_: pytest.fail("traffic before pin verification"))


def test_resigned_interpreter_substitution_and_plan_digest_fail_pretraffic(tmp_path, monkeypatch):
    profile, manifest, kwargs, _ = prepared_plan(tmp_path, monkeypatch)
    plan = perf.build_performance_run_plan(profile, manifest, **kwargs)
    plan["command"][0] = "/usr/bin/python3"
    with pytest.raises(BenchmarkJobError, match="identity"):
        perf.verify_plan(plan)
    plan["plan_sha256"] = perf._digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    with pytest.raises(BenchmarkJobError, match="prepared interpreter"):
        perf.verify_plan(plan)


def write_adapter_evidence(plan):
    events = [{"seq": 1, "event": "performance_adapter_ready", "adapter_sha256": plan["adapter_sha256"]},
              {"seq": 2, "event": "performance_transport_started"}, {"seq": 3, "event": "performance_transport_started"}]
    raw = b"".join((json.dumps(row) + "\n").encode() for row in events)
    final = {"seq": 4, "event": "adapter_finished", "status": "complete", "event_count": 3, "byte_count": len(raw)}
    Path(plan["paths"]["request_events"]).write_bytes(raw + (json.dumps(final) + "\n").encode())
    terminal = {"outcome": "completed", "plan_sha256": plan["plan_sha256"], "adapter_sha256": plan["adapter_sha256"],
        "events_complete": True, "budget": {"configured": plan["budgets"], "model_calls": 2, "exhausted": False, "reason": None}}
    Path(plan["paths"]["adapter_result"]).write_text(json.dumps(terminal))


@pytest.mark.parametrize("mode", ["success", "missing-observer", "timeout", "cancel"])
def test_execution_isolates_environment_and_retains_partial_native_outputs(tmp_path, monkeypatch, mode):
    profile, manifest, kwargs, _ = prepared_plan(tmp_path, monkeypatch)
    plan = perf.build_performance_run_plan(profile, manifest, **kwargs)
    def runner(command, cwd, timeout, env):
        assert cwd == plan["paths"]["work"] and not list(Path(cwd).iterdir())
        assert env["OPENAI_API_KEY"] == "fixture-value"
        assert "TEST_KEY" not in env and "UNRELATED_CLOUD_KEY" not in env and "AIPERF_HTTP_TRUST_ENV" not in env
        Path(plan["paths"]["native_output"]).write_text(json.dumps(aiperf_native()))
        if mode == "timeout":
            raise subprocess.TimeoutExpired(command, timeout, output="partial output")
        if mode == "cancel":
            raise KeyboardInterrupt
        if mode == "success":
            write_adapter_evidence(plan)
        return subprocess.CompletedProcess(command, 0, b"native output", b"")
    def invoke():
        return perf.run_performance_benchmark(plan, command_runner=runner,
            environ={"TEST_KEY": "fixture-value", "UNRELATED_CLOUD_KEY": "excluded", "AIPERF_HTTP_TRUST_ENV": "true"})
    if mode == "cancel":
        with pytest.raises(KeyboardInterrupt):
            invoke()
        result = json.loads(Path(plan["paths"]["result"]).read_text())
    else:
        result = invoke()
    assert result["state"] == ("completed" if mode == "success" else "incomplete")
    retained = perf.retain_performance_artifacts(run_path=plan["run_path"])
    assert retained["state"] == ("retained" if mode == "success" else "incomplete")
    assert any(row["path"].endswith("profile_export_aiperf.json") for row in retained["artifacts"])
    if mode == "timeout":
        assert result["stage"]["stdout"] == "partial output"


def test_retention_immutable_partial_and_symlink_rejection(tmp_path):
    base = tmp_path / "performance"
    base.mkdir()
    (base / "plan.json").write_text("{}")
    first = perf.retain_performance_artifacts(run_path=str(tmp_path))
    assert first["state"] == "incomplete"
    assert perf.retain_performance_artifacts(run_path=str(tmp_path)) == first
    (base / "plan.json").write_text("changed")
    assert perf.retain_performance_artifacts(run_path=str(tmp_path))["state"] == "failed"
    (base / "retention.json").unlink()
    (base / "plan.json").unlink()
    (base / "plan.json").symlink_to(tmp_path / "outside")
    assert perf.retain_performance_artifacts(run_path=str(tmp_path))["state"] == "failed"


def test_preparation_failure_has_partial_retention_without_creating_evidence(tmp_path):
    result = perf.retain_performance_artifacts(run_path=str(tmp_path))
    assert result["state"] == "incomplete" and result["missing"]
    assert not (tmp_path / "performance").exists()


@pytest.mark.parametrize("bound", ["files", "directories", "bytes"])
def test_retention_rejects_bounded_inventory_overflow(tmp_path, monkeypatch, bound):
    base = tmp_path / "performance"
    base.mkdir()
    if bound == "files":
        monkeypatch.setattr(perf, "MAX_SWE_RETAINED_FILES", 1)
        (base / "one").write_text("1")
        (base / "two").write_text("2")
    elif bound == "directories":
        monkeypatch.setattr(perf, "MAX_SWE_RETAINED_DIRECTORIES", 1)
        (base / "native").mkdir()
    else:
        monkeypatch.setattr(perf, "MAX_SWE_RETAINED_BYTES", 1)
        (base / "oversized").write_text("12")
    assert perf.retain_performance_artifacts(run_path=str(tmp_path))["state"] == "failed"


def test_native_symlink_and_oversized_document_are_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("{}")
    base = tmp_path / "run"
    base.mkdir()
    link = base / "native.json"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="unsafe"):
        perf._read_json(str(link), str(base))
    link.unlink()
    link.write_text("{} ")
    with pytest.raises(ValueError, match="oversized"):
        perf._read_json(str(link), str(base), limit=2)
