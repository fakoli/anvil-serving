from __future__ import annotations

import copy
import hashlib
import json
import os
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


def test_aiperf_actual_clean_export_omits_error_counter_without_measuring_zero():
    # Pinned 1.4 export shape observed from a complete real native fake-SSE run.
    native = {"schema_version": "1.4", "is_complete": True, "was_cancelled": False,
              "error_summary": [], "request_count": {"avg": 2.0, "unit": "requests"},
              "time_to_first_token": {"avg": 12.0, "unit": "ms", "count": 2}}
    summary = perf.normalize_performance_metrics(native_plan(), native)
    assert summary["native_complete"] is True
    assert summary["metrics"]["error_request_count"]["value"] is None
    assert summary["metrics"]["error_request_count"]["status"] == "unknown"
    for update in ({"error_summary": None}, {"error_summary": {}}, {"error_summary": [{"count": 1}]},
                   {"error_request_count": None}, {"error_request_count": {"avg": 1, "unit": "requests"}},
                   {"is_complete": False}, {"was_cancelled": True}, {"request_count": {"avg": 1, "unit": "requests"}}):
        assert not perf.normalize_performance_metrics(native_plan(), {**native, **update})["native_complete"]
    native.pop("error_summary")
    assert not perf.normalize_performance_metrics(native_plan(), native)["native_complete"]


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
              {"seq": 2, "event": "performance_transport_started", "model_call": 1},
              {"seq": 3, "event": "performance_transport_started", "model_call": 2},
              {"seq": 4, "event": "performance_stream_finished", "model_call": 1, "outcome": "valid",
               "terminal_observed": True, "usage_observed": True},
              {"seq": 5, "event": "performance_stream_finished", "model_call": 2, "outcome": "valid",
               "terminal_observed": True, "usage_observed": True}]
    raw = b"".join((json.dumps(row) + "\n").encode() for row in events)
    final = {"seq": len(events) + 1, "event": "adapter_finished", "status": "complete", "event_count": len(events), "byte_count": len(raw)}
    Path(plan["paths"]["request_events"]).write_bytes(raw + (json.dumps(final) + "\n").encode())
    terminal = {"outcome": "completed", "plan_sha256": plan["plan_sha256"], "adapter_sha256": plan["adapter_sha256"],
        "events_complete": True,
        "stream_validation": {"admitted": 2, "observed": 2, "valid": 2, "protocol_failure": 0,
                              "cancelled": 0, "complete": True, "usage_observed": 2}, "budget": {"configured": plan["budgets"], "model_calls": 2, "exhausted": False, "reason": None}}
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
    assert result == json.loads(Path(plan["paths"]["result"]).read_text())
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


@pytest.mark.parametrize("runner_name", ["lil", "aiperf"])
@pytest.mark.parametrize("wire", ["valid", "malformed", "truncated", "oversized", "missing-usage", "crlf"])
def test_prepared_full_performance_run_fake_sse(tmp_path, runner_name, wire):
    """Opt-in complete public plan/runner probe against an owned fixture, never a model."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    receipt_path = os.environ.get("ANVIL_PERFORMANCE_SMOKE_RECEIPT")
    if not receipt_path:
        pytest.skip("set ANVIL_PERFORMANCE_SMOKE_RECEIPT for full prepared native runner verification")
    prepared = json.loads(Path(receipt_path).read_text())
    profile = load_profile("pilot-v2")
    seen = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args):
            pass
        def do_GET(self):
            seen.append(("GET", self.path, self.headers.get("Authorization")))
            body = json.dumps({"data": [{"id": "llm.primary"}]} if self.path == "/v1/models" else {}).encode()
            self.send_response(200 if self.path == "/v1/models" else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(("POST", self.path, self.headers.get("Authorization"), body))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            separator = "\r\n\r\n" if wire == "crlf" else "\n\n"
            frames = [("data: " + json.dumps({"id": "fixture", "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": delta}]}) + separator).encode()
                for delta in ({"reasoning_content": "reason"}, {"content": "two words"})]
            if wire == "malformed":
                frames.append(b"data: {broken json\n\n")
            if wire == "oversized":
                frames.append(b"data: " + b"x" * 65537 + b"\n\n")
            if wire not in {"missing-usage", "truncated"}:
                frames.append(("data: " + json.dumps({"id": "fixture", "object": "chat.completion.chunk", "choices": [],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 7, "total_tokens": 11}}) + separator).encode())
            if wire != "truncated":
                frames.append(("data: [DONE]" + separator).encode())
            try:
                for frame in frames:
                    self.wfile.write(frame)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    manifest = {"schema": perf.HARNESS_ASSETS_SCHEMA, "suite": "performance", "profile_sha256": profile["content_sha256"],
        "assets": {name: {**profile["adapters"][name], "dirty": False} for name in ("lil", "aiperf")},
        "performance_environments": prepared["environments"]}
    runs = tmp_path / "runs"
    runs.mkdir()
    try:
        plan = perf.build_performance_run_plan(profile, manifest,
            endpoint={"base_url": f"http://127.0.0.1:{server.server_port}/v1", "model": "llm.primary"},
            parameters={**parameters(runner_name), "request_count": 1},
            run_root=str(runs), cache_root=prepared["cache_root"], ownership_id="fixture", run_id=runner_name, timeout_s=60)
        result = perf.run_performance_benchmark(plan)
    finally:
        server.shutdown()
        server.server_close()
    assert result == json.loads(Path(plan["paths"]["result"]).read_text())
    valid = wire in {"valid", "missing-usage", "crlf"}
    assert result["state"] == ("completed" if valid else "incomplete"), result
    assert result["stream_validation"]["complete"] is valid
    if valid:
        assert result["retention"]["state"] == "retained"
        assert result["summary"]["native_complete"] is True
    else:
        assert result["failure"]["class"] == "protocol_failure"
    assert result["instrumentation"] == plan["instrumentation"]
    assert result["budget"]["model_calls"] == 1
    calls = [row for row in seen if row[0] == "POST"]
    assert len(calls) == 1 and calls[0][1] == "/v1/chat/completions"
    assert calls[0][3]["model"] == "llm.primary"
    assert all(row[2] is None for row in seen), "no-auth endpoint unexpectedly received credentials"
    if runner_name == "aiperf" and valid:
        assert result["summary"]["metrics"]["error_request_count"]["value"] is None
    if wire == "missing-usage":
        for name in ("input_sequence_length", "output_sequence_length", "output_token_throughput",
                     "output_token_throughput_per_user", "inter_token_latency"):
            assert result["summary"]["metrics"][name]["status"] == "unknown"
    retained = perf.retain_performance_artifacts(run_path=plan["run_path"])
    assert {key: retained[key] for key in ("state", "missing")} == result["retention"]
    result_row = next(row for row in retained["artifacts"] if row["path"] == "performance/result.json")
    assert result_row["sha256"] == hashlib.sha256(Path(plan["paths"]["result"]).read_bytes()).hexdigest()


@pytest.mark.parametrize("defect", ["files", "directories", "bytes", "symlink", "missing", "seal"])
def test_full_run_retention_failure_persists_same_terminal_result(tmp_path, monkeypatch, defect):
    profile, manifest, kwargs, _ = prepared_plan(tmp_path, monkeypatch)
    plan = perf.build_performance_run_plan(profile, manifest, **kwargs)

    def runner(command, cwd, timeout, env):
        Path(plan["paths"]["native_output"]).write_text(json.dumps(aiperf_native()))
        write_adapter_evidence(plan)
        if defect == "files":
            monkeypatch.setattr(perf, "MAX_SWE_RETAINED_FILES", 1)
        elif defect == "directories":
            monkeypatch.setattr(perf, "MAX_SWE_RETAINED_DIRECTORIES", 1)
        elif defect == "bytes":
            monkeypatch.setattr(perf, "MAX_SWE_RETAINED_BYTES", 1)
        elif defect == "symlink":
            (Path(plan["paths"]["output"]) / "link").symlink_to(tmp_path / "outside")
        elif defect == "missing":
            Path(plan["paths"]["plan"]).unlink()
        else:
            write = perf.atomic_write_json
            def fail_seal(path, value):
                if str(path).endswith("retention.json"):
                    raise OSError("fixture seal unavailable")
                return write(path, value)
            monkeypatch.setattr(perf, "atomic_write_json", fail_seal)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    result = perf.run_performance_benchmark(plan, command_runner=runner, environ={"TEST_KEY": "fixture-value"})
    assert result == json.loads(Path(plan["paths"]["result"]).read_text())
    assert result["state"] == "incomplete"
    assert result["failure"]["code"] == "performance_retention_incomplete"
    assert result["retention"]["state"] == ("incomplete" if defect == "missing" else "failed")
    if defect == "missing":
        retained = perf.retain_performance_artifacts(run_path=plan["run_path"])
        assert retained["state"] == "incomplete" and retained["missing"] == ["plan.json"]
        row = next(row for row in retained["artifacts"] if row["path"] == "performance/result.json")
        assert row["sha256"] == hashlib.sha256(Path(plan["paths"]["result"]).read_bytes()).hexdigest()
    else:
        assert not (Path(plan["paths"]["performance"]) / "retention.json").exists()


@pytest.mark.parametrize("defect", [None, "missing", "symlink"])
def test_durable_worker_publishes_canonical_performance_outcome(tmp_path, monkeypatch, defect):
    from anvil_serving.benchmarking import worker
    from anvil_serving.benchmarking.preflight import BENCHMARK_PREFLIGHT_SCHEMA
    from anvil_serving.benchmarking.jobs import JOB_SPEC_SCHEMA
    from anvil_serving.control_plane.controller.store import BenchmarkJobStore

    profile, manifest, kwargs, _ = prepared_plan(tmp_path, monkeypatch)
    spec = {"schema": JOB_SPEC_SCHEMA, "run_id": kwargs["run_id"], "ownership_id": kwargs["ownership_id"],
            "suite": "performance", "profile": profile["name"], "timeout_s": 60,
            "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary"},
            "worker": {"id": "fixture-worker"}, "submitted_at": "2026-10-24T12:00:00Z",
            "parameters": kwargs["parameters"]}
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=kwargs["run_root"])
    store.submit(spec)
    monkeypatch.setenv("ANVIL_BENCHMARK_CACHE_ROOT", kwargs["cache_root"])
    monkeypatch.setattr(worker, "prepare_harness_assets", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(worker, "run_benchmark_preflight", lambda *args, **kwargs: {"schema": BENCHMARK_PREFLIGHT_SCHEMA, "passed": True})
    observed = {}
    run = perf.run_performance_benchmark

    def execute(plan):
        def native(command, cwd, timeout, env):
            Path(plan["paths"]["native_output"]).write_text(json.dumps(aiperf_native()))
            write_adapter_evidence(plan)
            if defect == "missing":
                Path(plan["paths"]["plan"]).unlink()
            elif defect == "symlink":
                (Path(plan["paths"]["output"]) / "unsafe").symlink_to(tmp_path / "outside")
            return subprocess.CompletedProcess(command, 0, b"", b"")
        observed["result"] = run(plan, command_runner=native)
        observed["path"] = plan["paths"]["result"]
        return observed["result"]

    monkeypatch.setattr(perf, "run_performance_benchmark", execute)
    completed = worker.execute_benchmark_job(store, spec["run_id"])
    assert "path" in observed, completed
    canonical = json.loads(Path(observed["path"]).read_text())
    assert canonical == observed["result"]
    assert canonical["state"] == ("incomplete" if defect else "completed")
    assert completed["state"] == ("failed" if defect else "completed")
    assert store.status(spec["run_id"])["state"] == completed["state"]
    assert store.artifact(spec["run_id"])["results"]["evidence"]["completeness"] == completed["state"]


@pytest.mark.parametrize("defect", ["missing", "duplicate", "terminal", "report"])
def test_parent_refuses_inconsistent_stream_observations(tmp_path, monkeypatch, defect):
    profile, manifest, kwargs, _ = prepared_plan(tmp_path, monkeypatch)
    plan = perf.build_performance_run_plan(profile, manifest, **kwargs)

    def native(command, cwd, timeout, env):
        Path(plan["paths"]["native_output"]).write_text(json.dumps(aiperf_native()))
        write_adapter_evidence(plan)
        events_path = Path(plan["paths"]["request_events"])
        events = [json.loads(line) for line in events_path.read_text().splitlines()][:-1]
        if defect == "missing":
            events.pop()
        elif defect == "duplicate":
            events[-1]["model_call"] = 1
        elif defect == "terminal":
            events[-1]["terminal_observed"] = False
        else:
            terminal_path = Path(plan["paths"]["adapter_result"])
            terminal = json.loads(terminal_path.read_text())
            terminal["stream_validation"]["valid"] = 1
            terminal_path.write_text(json.dumps(terminal))
        raw = b"".join((json.dumps(row) + "\n").encode() for row in events)
        final = {"seq": len(events) + 1, "event": "adapter_finished", "status": "complete",
                 "event_count": len(events), "byte_count": len(raw)}
        events_path.write_bytes(raw + (json.dumps(final) + "\n").encode())
        return subprocess.CompletedProcess(command, 0, b"", b"")

    result = perf.run_performance_benchmark(plan, command_runner=native, environ={"TEST_KEY": "fixture-value"})
    assert result == json.loads(Path(plan["paths"]["result"]).read_text())
    assert result["state"] == "incomplete"
    assert result["failure"]["code"] == "missing_performance_observations"


def test_finalization_never_rewrites_previously_sealed_result(tmp_path):
    base = tmp_path / "performance"
    base.mkdir()
    result_path = base / "result.json"
    result_path.write_text('{"state":"incomplete"}')
    retained = perf.retain_performance_artifacts(run_path=str(tmp_path))
    result_bytes = result_path.read_bytes()
    manifest_bytes = (base / "retention.json").read_bytes()
    with pytest.raises(BenchmarkJobError, match="previously sealed"):
        perf._finalize_performance_result({"run_path": str(tmp_path), "paths": {"result": str(result_path)}},
                                          {"state": "completed", "failure": None})
    assert result_path.read_bytes() == result_bytes
    assert (base / "retention.json").read_bytes() == manifest_bytes
    assert perf.retain_performance_artifacts(run_path=str(tmp_path)) == retained
