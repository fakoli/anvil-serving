from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import re
import os
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from anvil_serving.benchmarking.artifacts import REQUIRED_MEASURED_IDENTITIES
from anvil_serving.benchmarking.jobs import BenchmarkJobError
from anvil_serving.benchmarking.jobs import JOB_SPEC_SCHEMA
from anvil_serving.benchmarking import swe as swe_benchmark
from anvil_serving.benchmarking import worker as benchmark_worker
from anvil_serving.benchmarking.profiles import load_profile
from anvil_serving.benchmarking.worker import cancel_benchmark_job, cleanup_benchmark_job, execute_benchmark_job, launch_benchmark_job
from anvil_serving.control_plane.controller.store import BenchmarkJobStore


def declared_identities(**overrides):
    values = {name: {} for name in REQUIRED_MEASURED_IDENTITIES}
    values.update(overrides)
    return {"identities": values}


def spec():
    return {
        "schema": JOB_SPEC_SCHEMA,
        "run_id": "worker-run",
        "ownership_id": "campaign",
        "suite": "context",
        "profile": "smoke",
        "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "deepseek"},
        "worker": {"id": "worker"},
        "submitted_at": "2026-08-03T12:00:00Z",
        "timeout_s": 600,
        "parameters": {
            "case_limit": 1,
            "model_host_id": "model-host",
            "identities": {"model": {"alias": "candidate"}},
        },
    }


def test_worker_claims_once_and_retains_cross_suite_evidence(monkeypatch, tmp_path):
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(spec())
    monkeypatch.setattr(
        "anvil_serving.benchmarking.worker.prepare_harness_assets",
        lambda *_args, **_kwargs: {
            "schema": "anvil-serving.benchmark-harness-assets/v1",
            "profile_sha256": "profile",
            "suite": "context",
            "assets": {},
        },
    )
    monkeypatch.setattr(
        "anvil_serving.benchmarking.worker.run_benchmark_preflight",
        lambda *_args, **_kwargs: {
            "schema": "anvil-serving.benchmark-preflight/v1",
            "passed": True,
            "observed": {
                "worker": {"id": "worker", "architecture": "x86_64"},
                "endpoint": {"configured_context": 650000},
            },
            "checks": [],
        },
    )
    monkeypatch.setattr(
        "anvil_serving.benchmarking.worker.run_context_suite",
        lambda *_args, **_kwargs: {
            "schema": "anvil-serving.context-suite-run/v1",
            "curve": {"attempted_buckets": [8192], "effective_context": 8192},
            "passed": True,
        },
    )
    record = execute_benchmark_job(store, "worker-run")
    assert record["state"] == "completed"
    artifact = store.artifact("worker-run")
    evidence = artifact["results"]["evidence"]
    assert evidence["evidence_kind"] == "measured"
    assert evidence["completeness"] == "completed"
    assert [stage["name"] for stage in evidence["stages"]] == [
        "asset_preparation", "preflight", "context"
    ]
    assert evidence["promotion"]["authorized"] is False
    assert evidence["identities"]["model"] == {"alias": "candidate"}


def test_incomplete_swe_budget_exhaustion_retains_outer_resource_cause(monkeypatch, tmp_path):
    value = spec()
    value["suite"] = "swe"
    value["parameters"] = {"instance_ids": ["astropy__astropy-12907"]}
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(value)
    monkeypatch.setattr(
        benchmark_worker,
        "prepare_harness_assets",
        lambda *_args, **_kwargs: {"assets": {}},
    )
    monkeypatch.setattr(
        benchmark_worker,
        "run_benchmark_preflight",
        lambda *_args, **_kwargs: {
            "schema": "anvil-serving.benchmark-preflight/v1",
            "passed": True,
            "observed": {"worker": {"id": "worker"}, "endpoint": {}},
            "checks": [],
        },
    )
    monkeypatch.setattr(
        benchmark_worker,
        "_run_suite",
        lambda *_args, **_kwargs: {
            "state": "incomplete",
            "failure": {"class": "budget_exhausted", "code": "swe_limits_exceeded"},
            "summary": {"attempted": 1, "completed": False},
        },
    )

    record = execute_benchmark_job(store, "worker-run")
    evidence = store.artifact("worker-run")["results"]["evidence"]

    assert record["state"] == "failed"
    assert record["failure"]["class"] == "resource_exhaustion"
    assert record["failure"]["code"] == "budget_exhausted"
    assert evidence["completeness"] == "failed"
    assert evidence["failure"]["class"] == "resource_exhaustion"
    assert evidence["failure"]["code"] == "budget_exhausted"
    suite_stage = next(stage for stage in evidence["stages"] if stage["name"] == "swe")
    suite_result = json.loads((tmp_path / "runs" / "campaign" / "worker-run" / suite_stage["evidence"][0]["path"]).read_text())
    assert suite_result["state"] == "incomplete"
    assert suite_result["failure"] == {"class": "budget_exhausted", "code": "swe_limits_exceeded"}


def test_worker_rejects_previously_stored_invalid_controls_before_assets(monkeypatch, tmp_path):
    value = spec()
    value["parameters"] = {"thinking_mode": "bogus"}
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    with monkeypatch.context() as old_writer:
        old_writer.setattr("anvil_serving.benchmarking.submission.bind_submission_spec", lambda value: value)
        store.submit(value)
    monkeypatch.setattr(
        "anvil_serving.benchmarking.worker.prepare_harness_assets",
        lambda *_args, **_kwargs: pytest.fail("invalid job prepared assets"),
    )
    record = execute_benchmark_job(store, "worker-run")
    assert record["state"] == "failed"
    assert record["failure"]["code"] == "bad_thinking_mode"


def test_worker_forwards_swe_sampler_controls_into_mini_config_and_result(monkeypatch, tmp_path):
    value = spec()
    value["suite"] = "swe"
    value["parameters"] = {
        "instance_ids": ["astropy__astropy-12907"],
        "temperature": 1.0,
        "top_p": 0.95,
    }
    profile = load_profile("smoke")
    captured = {}

    def fake_plan(profile, assets, *, endpoint, request_controls, **_kwargs):
        controls = swe_benchmark._validate_request_controls(request_controls)
        captured["plan"] = {
            "request_controls": controls,
            "config_text": swe_benchmark._mini_config(
                endpoint, profile["suites"]["swe"], controls, "docker"
            ),
        }
        return captured["plan"]

    monkeypatch.setattr(benchmark_worker, "build_swe_run_plan", fake_plan)
    monkeypatch.setattr(
        benchmark_worker, "run_swe_benchmark", lambda plan: dict(plan)
    )
    result = benchmark_worker._run_suite(
        SimpleNamespace(run_root=str(tmp_path / "runs")),
        {"spec": value}, profile, {"assets": {}},
    )

    assert result["request_controls"]["sampling"] == {
        "temperature": {"requested": 1.0, "effective_request": 1.0, "sent": True},
        "top_p": {"requested": 0.95, "effective_request": 0.95, "sent": True},
    }
    assert "    temperature: 1.0\n    top_p: 0.95\n" in result["config_text"]


@pytest.mark.parametrize("selector", [
    {"service": "candidate", "tail": 100},
    {"recipe_container": "candidate", "tail": 100},
])
def test_worker_forwards_explicit_log_target_without_endpoint_inference(monkeypatch, tmp_path, selector):
    value = spec()
    value.update(suite="swe", parameters={"instance_ids": ["astropy__astropy-12907"], "server_log": selector})
    from anvil_serving.benchmarking.submission import validate_submission_spec
    assert validate_submission_spec(value)["parameters"]["server_log"] == selector
    def fake_plan(*_args, server_log, **_kwargs):
        assert server_log == selector
        return {"server_log": server_log}
    collector = object()
    monkeypatch.setattr(benchmark_worker, "build_swe_run_plan", fake_plan)
    def bind(target):
        assert target == selector
        return collector
    monkeypatch.setattr(benchmark_worker, "server_log_collector", bind)
    def run(_plan, *, log_collector):
        assert log_collector is collector
        return {"state": "fixture"}
    monkeypatch.setattr(benchmark_worker, "run_swe_benchmark", run)
    assert benchmark_worker._run_suite(SimpleNamespace(run_root=str(tmp_path)), {"spec": value},
                                       load_profile("smoke"), {}) == {"state": "fixture"}


@pytest.mark.parametrize("force_nonempty_content", [True, False])
def test_worker_forwards_swe_force_nonempty_content(monkeypatch, tmp_path, force_nonempty_content):
    value = spec()
    value["suite"] = "swe"
    value["parameters"] = {
        "instance_ids": ["astropy__astropy-12907"],
        "thinking_mode": "enabled",
        "force_nonempty_content": force_nonempty_content,
    }
    profile = load_profile("smoke")

    def fake_plan(profile, assets, *, endpoint, request_controls, **_kwargs):
        controls = swe_benchmark._validate_request_controls(request_controls)
        return {
            "request_controls": controls,
            "config_text": swe_benchmark._mini_config(
                endpoint, profile["suites"]["swe"], controls, "docker"
            ),
        }

    monkeypatch.setattr(benchmark_worker, "build_swe_run_plan", fake_plan)
    monkeypatch.setattr(benchmark_worker, "run_swe_benchmark", lambda plan: dict(plan))
    result = benchmark_worker._run_suite(
        SimpleNamespace(run_root=str(tmp_path / "runs")),
        {"spec": value}, profile, {"assets": {}},
    )

    assert result["request_controls"]["chat_template_kwargs"] == {
        "enable_thinking": True,
        "force_nonempty_content": force_nonempty_content,
    }


def test_detached_launcher_keeps_credentials_out_of_argv(tmp_path):
    observed = {}

    def popen(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return SimpleNamespace(pid=987)

    database = str(tmp_path / "jobs.db")
    run_root = str(tmp_path / "runs")
    BenchmarkJobStore(database, run_root=run_root).submit(spec())
    result = launch_benchmark_job(
        path=database,
        run_root=run_root,
        run_id="worker-run",
        popen=popen,
    )
    assert result["pid"] == 987
    assert "worker-run" in observed["argv"]
    assert all("TOKEN" not in item for item in observed["argv"])
    assert observed["kwargs"]["stdout"] is not None


def test_detached_launcher_reports_typed_process_failure(tmp_path):
    database = str(tmp_path / "jobs.db")
    run_root = str(tmp_path / "runs")
    store = BenchmarkJobStore(database, run_root=run_root)
    _, submitted = store.submit(spec())

    with pytest.raises(BenchmarkJobError) as exc:
        launch_benchmark_job(
            path=database,
            run_root=run_root,
            run_id="worker-run",
            popen=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("blocked")),
        )

    assert exc.value.code == "worker_launch_failed"
    assert store.status("worker-run") == submitted
    assert store.submit(spec()) == ("existing", submitted)
    assert not (tmp_path / "runs" / "campaign" / "worker-run" / "worker.json").exists()


@pytest.mark.parametrize("state", [None, "running", "completed", "failed", "cancelled"])
def test_detached_launcher_rejects_absent_or_nonqueued_jobs_before_spawn(tmp_path, state):
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    if state is not None:
        store.submit(spec())
        store.claim("worker-run")
        if state != "running":
            store.transition("worker-run", state, failure={"code": "fixture"} if state == "failed" else None)
    original = store.status("worker-run")

    with pytest.raises(BenchmarkJobError) as exc:
        launch_benchmark_job(
            path=store.path, run_root=store.run_root, run_id="worker-run",
            popen=lambda *_a, **_kw: pytest.fail("ineligible job spawned a worker"),
        )

    assert exc.value.code == ("job_not_found" if state is None else "job_already_claimed")
    assert store.status("worker-run") == original
    assert not (tmp_path / "runs" / "campaign" / "worker-run" / "worker.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="process-group cancellation is POSIX-specific")
def test_rejected_relaunch_preserves_original_worker_cancellation_identity(monkeypatch, tmp_path):
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(spec())
    monkeypatch.setattr(benchmark_worker, "_process_start_identity", lambda _pid: "original-start")
    launch_benchmark_job(
        path=store.path, run_root=store.run_root, run_id="worker-run",
        popen=lambda *_a, **_kw: SimpleNamespace(pid=987),
    )
    worker_path = tmp_path / "runs" / "campaign" / "worker-run" / "worker.json"
    original = worker_path.read_bytes()
    store.claim("worker-run")
    with pytest.raises(BenchmarkJobError, match="not queued"):
        launch_benchmark_job(
            path=store.path, run_root=store.run_root, run_id="worker-run",
            popen=lambda *_a, **_kw: pytest.fail("duplicate worker launched"),
        )
    assert worker_path.read_bytes() == original
    monkeypatch.setattr(benchmark_worker, "_process_arguments", lambda _pid: (
        sys.executable, "-m", "anvil_serving.benchmarking.worker",
        "--db", os.path.realpath(store.path), "--run-root", store.run_root,
        "--run-id", "worker-run",
    ))
    monkeypatch.setattr(benchmark_worker.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(benchmark_worker.os, "getsid", lambda pid: pid)
    monkeypatch.setattr(benchmark_worker, "_process_group_running", lambda _pid: False)
    signals = []
    monkeypatch.setattr(benchmark_worker.os, "killpg", lambda *args: signals.append(args))

    cancelled = cancel_benchmark_job(store, "worker-run")

    assert signals == [(987, signal.SIGTERM)]
    assert cancelled["state"] == "cancelled"
    assert cancelled["worker_terminated"] is True
    assert cancelled["cleanup_deferred"] is False


@pytest.mark.skipif(os.name == "nt", reason="process-group cancellation is POSIX-specific")
def test_cancellation_retains_native_output_and_stops_owned_child(monkeypatch, tmp_path):
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(spec())
    store.claim("worker-run")
    run_path = tmp_path / "runs" / "campaign" / "worker-run"
    output = run_path / "work" / "mini-output" / "case-1"
    output.mkdir(parents=True)
    trajectory = output / "trajectory.json"
    expected_trajectory = '{"patch":"retained"}\n'
    trajectory.write_text(expected_trajectory, encoding="utf-8")
    request_events = run_path / "swe-request-events.jsonl"
    request_events.write_text('{"partial":"flushed attempt metadata"}\n', encoding="utf-8")
    code = (
        "import subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
        "print(child.pid, flush=True);time.sleep(60)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, "anvil_serving.benchmarking.worker", "worker-run"],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        child_pid = int(process.stdout.readline().strip())
        database = os.path.realpath(store.path)
        (run_path / "worker.json").write_text(
            json.dumps({
                "run_id": "worker-run", "pid": process.pid, "db": database,
                "run_root": store.run_root, "process_start": "test-start",
                "process_group_id": process.pid, "session_id": process.pid,
            }), encoding="utf-8"
        )
        monkeypatch.setattr(benchmark_worker, "_process_start_identity", lambda _pid: "test-start")
        monkeypatch.setattr(
            benchmark_worker,
            "_process_arguments",
            lambda _pid: (sys.executable, "-m", "anvil_serving.benchmarking.worker", "--db", database, "--run-root", store.run_root, "--run-id", "worker-run"),
        )
        cancelled = cancel_benchmark_job(store, "worker-run")
        assert cancelled["state"] == "cancelled"
        assert cancelled["worker_terminated"] is True
        assert cancelled["cleanup_deferred"] is False
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and subprocess.run(
            ("ps", "-p", str(child_pid)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode == 0:
            time.sleep(0.05)
        assert subprocess.run(("ps", "-p", str(child_pid)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
        artifact = store.artifact("worker-run")
        evidence = artifact["results"]["evidence"]
        assert evidence["completeness"] == "cancelled"
        assert evidence["failure"]["class"] == "cancellation"
        assert (run_path / "evidence" / "native-partial" / "case-1" / "trajectory.json").read_text(encoding="utf-8") == expected_trajectory
        assert not (run_path / "work").exists()
        assert request_events.read_text() == '{"partial":"flushed attempt metadata"}\n'
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def test_cancellation_does_not_signal_same_run_from_another_store(monkeypatch, tmp_path):
    store = BenchmarkJobStore(str(tmp_path / "jobs-a.db"), run_root=str(tmp_path / "runs-a"))
    store.submit(spec())
    store.claim("worker-run")
    run_path = tmp_path / "runs-a" / "campaign" / "worker-run"
    work = run_path / "work"
    work.mkdir(parents=True)
    other_db = os.path.realpath(str(tmp_path / "jobs-b.db"))
    other_root = os.path.realpath(str(tmp_path / "runs-b"))
    (run_path / "worker.json").write_text(
        json.dumps({
            "run_id": "worker-run", "pid": 12345, "db": other_db,
            "run_root": other_root, "process_start": "other-start",
            "process_group_id": 12345, "session_id": 12345,
        }), encoding="utf-8"
    )
    monkeypatch.setattr(
        benchmark_worker,
        "_process_arguments",
        lambda _pid: (sys.executable, "-m", "anvil_serving.benchmarking.worker", "--db", other_db, "--run-root", other_root, "--run-id", "worker-run"),
    )
    monkeypatch.setattr(benchmark_worker, "_process_start_identity", lambda _pid: "other-start")
    monkeypatch.setattr(benchmark_worker.os, "getpgid", lambda _pid: 12345, raising=False)
    monkeypatch.setattr(benchmark_worker.os, "getsid", lambda _pid: 12345, raising=False)
    signals = []
    monkeypatch.setattr(benchmark_worker.os, "killpg", lambda *args: signals.append(args), raising=False)

    cancelled = cancel_benchmark_job(store, "worker-run")

    assert cancelled["state"] == "cancelled"
    assert cancelled["cleanup_deferred"] is True
    assert signals == []
    assert work.exists()


def test_cancellation_artifact_write_failure_is_terminal_and_preserves_work(monkeypatch, tmp_path):
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(spec())
    store.claim("worker-run")
    run_path = tmp_path / "runs" / "campaign" / "worker-run"
    work = run_path / "work" / "mini-output"
    work.mkdir(parents=True)
    (work / "trajectory.json").write_text("retained source\n", encoding="utf-8")
    monkeypatch.setattr(
        benchmark_worker,
        "_stop_owned_worker",
        lambda *_args, **_kwargs: {"stopped": True, "status": "terminated", "pid": 12},
    )
    monkeypatch.setattr(
        benchmark_worker,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("ENOSPC")),
    )

    cancelled = cancel_benchmark_job(store, "worker-run")

    assert cancelled["state"] == "cancelled"
    assert cancelled["cleanup_deferred"] is True
    assert store.status("worker-run")["failure"]["code"] == "cancellation_artifact_unavailable"
    assert (run_path / "work").exists()


def test_cancellation_store_artifact_failure_is_terminal_and_preserves_work(monkeypatch, tmp_path):
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(spec())
    store.claim("worker-run")
    run_path = tmp_path / "runs" / "campaign" / "worker-run"
    work = run_path / "work" / "mini-output"
    work.mkdir(parents=True)
    (work / "trajectory.json").write_text("retained source\n", encoding="utf-8")
    monkeypatch.setattr(
        benchmark_worker,
        "_stop_owned_worker",
        lambda *_args, **_kwargs: {"stopped": True, "status": "terminated", "pid": 12},
    )
    monkeypatch.setattr(
        store,
        "_write_artifact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("ENOSPC")),
    )

    cancelled = cancel_benchmark_job(store, "worker-run")

    assert cancelled["state"] == "cancelled"
    assert cancelled["cleanup_deferred"] is True
    assert cancelled["failure"]["code"] == "cancellation_artifact_unavailable"
    assert (run_path / "work").exists()


def test_cleanup_terminal_job_requires_stopped_owned_worker_and_retains(monkeypatch, tmp_path):
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(spec())
    store.claim("worker-run")
    store.transition("worker-run", "completed", results={"evidence": {}})
    run_path = tmp_path / "runs" / "campaign" / "worker-run"
    work = run_path / "work"
    work.mkdir(parents=True)
    (work / "predictions.jsonl").write_text('{"instance_id":"case"}\n', encoding="utf-8")
    database = os.path.realpath(store.path)
    (run_path / "worker.json").write_text(json.dumps({
        "run_id": "worker-run", "pid": 12345, "db": database,
        "run_root": store.run_root, "process_start": "started",
        "process_group_id": 12345, "session_id": 12345,
    }), encoding="utf-8")
    monkeypatch.setattr(benchmark_worker, "_process_arguments", lambda _pid: None)
    monkeypatch.setattr(benchmark_worker.os, "getpgid", lambda _pid: (_ for _ in ()).throw(ProcessLookupError()))
    monkeypatch.setattr(benchmark_worker, "_process_group_running", lambda _pid: False)

    cleaned = cleanup_benchmark_job(store, "worker-run")

    assert cleaned["cleaned"] is True
    assert cleaned["retention"]["state"] == "absent"
    assert not work.exists()
    assert not (run_path / "evidence" / "swe-canonical").exists()


class EndpointHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_GET(self):
        payload = {
            "data": [{"id": "deepseek", "max_model_len": 650000}],
        }
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size))
        prompt = body["messages"][-1]["content"]
        if prompt.startswith("token calibration"):
            answer = "ok"
        else:
            answer = re.search(r"access marker for ORCHID is (K\d+)\.", prompt).group(1)
        payload = {
            "choices": [{"message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": max(2, math.ceil(len(prompt) / 4)),
                "completion_tokens": 2,
            },
        }
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Request-Id", f"fake-{len(prompt)}")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_real_detached_worker_completes_against_routed_protocol(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), EndpointHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        database = str(tmp_path / "jobs.db")
        run_root = str(tmp_path / "runs")
        store = BenchmarkJobStore(database, run_root=run_root)
        value = spec()
        value["endpoint"]["base_url"] = f"http://127.0.0.1:{server.server_port}/v1"
        store.submit(value)
        launch_benchmark_job(path=database, run_root=run_root, run_id="worker-run")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            record = store.status("worker-run")
            if record["state"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.1)
        assert record["state"] == "completed", store.logs("worker-run")
        artifact = store.artifact("worker-run")
        evidence = artifact["results"]["evidence"]
        assert evidence["completeness"] == "completed"
        assert evidence["summary"]["effective_context"] == 8192
        assert evidence["promotion"]["authorized"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

@pytest.mark.parametrize(("suite", "parameters", "code"), [
    ("context", {"offline": "false"}, "bad_offline"),
    ("context", {"client_topology": "bogus"}, "bad_client_topology"),
    ("context", {"model_host_id": ["model-host"]}, "bad_model_host_id"),
    ("context", {"model_host_id": " model-host "}, "bad_model_host_id"),
    ("swe", {"instance_ids": ["astropy__astropy-12907"], "paired_image_repo_digests": []}, "bad_swe_image_policy"),
    ("context", declared_identities(), "bad_identities"),
    ("context", declared_identities(model="spoofed-scalar"), "bad_identities"),
])
def test_worker_rejects_previously_stored_mis_typed_parameters_before_assets(
    monkeypatch, tmp_path, suite, parameters, code,
):
    value = spec()
    value["suite"] = suite
    value["parameters"] = parameters
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    with monkeypatch.context() as old_writer:
        old_writer.setattr("anvil_serving.benchmarking.submission.bind_submission_spec", lambda value: value)
        store.submit(value)
    monkeypatch.setattr(
        "anvil_serving.benchmarking.worker.prepare_harness_assets",
        lambda *_args, **_kwargs: pytest.fail("invalid job prepared assets"),
    )
    record = execute_benchmark_job(store, "worker-run")
    assert record["state"] == "failed"
    assert record["failure"]["code"] == code


def _execute_successful_swe(monkeypatch, tmp_path, *, missing=None):
    from tests.test_swe_benchmark import SuccessfulRunner, plan

    value = plan(tmp_path, prompt_contract="disclosed/v2" if missing == "overlay" else "legacy/v1")
    submitted = spec()
    submitted.update(run_id=value["run_id"], suite="swe", endpoint=value["endpoint"], parameters={
        "instance_ids": value["selection"]["instance_ids"], "prompt_contract": value["prompt_contract"],
    })
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(submitted)
    monkeypatch.setattr(benchmark_worker, "prepare_harness_assets", lambda *_args, **_kwargs: {"assets": {}})
    monkeypatch.setattr(benchmark_worker, "run_benchmark_preflight", lambda *_args, **_kwargs: {
        "schema": "anvil-serving.benchmark-preflight/v1", "passed": True, "observed": {}, "checks": [],
    })
    monkeypatch.setattr(benchmark_worker, "build_swe_run_plan", lambda *_args, **_kwargs: value)

    def run(generated):
        result = swe_benchmark.run_swe_benchmark(generated, runner=SuccessfulRunner(generated),
                                               environ={"ANVIL_ROUTER_TOKEN": "fixture-only"})
        assert result["state"] == "completed"
        if missing == "grader":
            from pathlib import Path
            for report in Path(generated["paths"]["grader_work"]).glob("*.json"):
                report.unlink()
        return result

    monkeypatch.setattr(benchmark_worker, "run_swe_benchmark", run)
    record = execute_benchmark_job(store, value["run_id"])
    return store, record, tmp_path / "runs" / "campaign" / value["run_id"]


def test_successful_legacy_swe_executes_and_completes_with_retained_artifacts(monkeypatch, tmp_path):
    store, record, run = _execute_successful_swe(monkeypatch, tmp_path)
    assert record["state"] == "completed", record.get("failure")
    evidence = store.artifact(record["spec"]["run_id"])["results"]["evidence"]
    assert evidence["completeness"] == "completed"
    assert evidence["summary"]["resolve_rate"] == 1.0
    assert all(stage["status"] == "completed" for stage in evidence["stages"])
    manifest = json.loads((run / "evidence" / "swe-canonical" / "manifest.json").read_text())
    assert manifest["state"] == "retained"
    assert manifest["missing"] == []
    assert not (run / "work" / "swe-prompt-contract.yaml").exists()
    assert not (run / "work" / "paired-image-ids.json").exists()


@pytest.mark.parametrize("missing", ["overlay", "grader"])
def test_swe_execution_cannot_complete_with_missing_conditional_or_grader_evidence(monkeypatch, tmp_path, missing):
    store, record, run = _execute_successful_swe(monkeypatch, tmp_path, missing=missing)
    assert record["state"] == "failed"
    evidence = store.artifact(record["spec"]["run_id"])["results"]["evidence"]
    assert evidence["completeness"] == "failed"
    assert next(stage for stage in evidence["stages"] if stage["name"] == "swe_canonical_retention")["status"] == "incomplete"
    assert (run / "work").exists()


def _stopped_worker(monkeypatch, store, run, run_id):
    (run / "worker.json").write_text(json.dumps({
        "run_id": run_id, "pid": 12345, "db": os.path.realpath(store.path),
        "run_root": store.run_root, "process_start": "started", "process_group_id": 12345, "session_id": 12345,
    }))
    monkeypatch.setattr(benchmark_worker, "_process_arguments", lambda _pid: None)
    monkeypatch.setattr(benchmark_worker.os, "getpgid", lambda _pid: (_ for _ in ()).throw(ProcessLookupError()))
    monkeypatch.setattr(benchmark_worker, "_process_group_running", lambda _pid: False)


@pytest.mark.parametrize("damage", [None, "manifest", "retained-file", "manifest-missing"])
def test_swe_cleanup_requires_exact_terminal_bound_snapshot(monkeypatch, tmp_path, damage):
    store, record, run = _execute_successful_swe(monkeypatch, tmp_path)
    run_id = record["spec"]["run_id"]
    _stopped_worker(monkeypatch, store, run, run_id)
    original_artifact = store.artifact(run_id)
    original_record = store.status(run_id)
    manifest = run / "evidence" / "swe-canonical" / "manifest.json"
    if damage == "manifest":
        # Same valid content, different bytes: terminal digest is the authority.
        manifest.write_text(manifest.read_text() + "\n")
    elif damage == "retained-file":
        (manifest.parent / "predictions.jsonl").write_text("tampered")
    elif damage == "manifest-missing":
        manifest.unlink()
    if damage:
        with pytest.raises(BenchmarkJobError) as exc:
            cleanup_benchmark_job(store, run_id)
        assert exc.value.code == ("swe_canonical_evidence_mismatch" if damage == "manifest" else "swe_artifact_retention_failed")
        assert (run / "work").exists()
    else:
        cleaned = cleanup_benchmark_job(store, run_id)
        assert cleaned["cleaned"] is True
        assert cleaned["retention"]["reused"] is True
        assert not (run / "work").exists()
        assert (manifest.parent / "predictions.jsonl").is_file()
    assert store.status(run_id) == original_record
    assert store.artifact(run_id) == original_artifact


def test_swe_cleanup_refuses_unanchored_terminal_work_without_creating_evidence(monkeypatch, tmp_path):
    value = spec()
    value.update(suite="swe", parameters={"instance_ids": ["astropy__astropy-12907"]})
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(value)
    store.claim("worker-run")
    store.transition("worker-run", "completed", results={"evidence": {}})
    run = tmp_path / "runs" / "campaign" / "worker-run"
    work = run / "work"
    work.mkdir(parents=True)
    (work / "predictions.jsonl").write_text("source")
    _stopped_worker(monkeypatch, store, run, "worker-run")
    original = store.status("worker-run")
    with pytest.raises(BenchmarkJobError) as exc:
        cleanup_benchmark_job(store, "worker-run")
    assert exc.value.code == "missing_swe_canonical_evidence"
    assert work.exists()
    assert not (run / "evidence" / "swe-canonical").exists()
    assert store.status("worker-run") == original


def test_swe_cancel_artifact_failure_then_cleanup_preserves_unanchored_work(monkeypatch, tmp_path):
    from tests.test_benchmark_retention import _canonical_work
    from anvil_serving.benchmarking.swe_regrade import prepare_swe_regrade

    value = spec()
    value.update(suite="swe", parameters={"instance_ids": ["astropy__astropy-12907"]})
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(value)
    store.claim("worker-run")
    run = tmp_path / "runs" / "campaign" / "worker-run"
    work = _canonical_work(run)
    _stopped_worker(monkeypatch, store, run, "worker-run")
    monkeypatch.setattr(benchmark_worker, "_stop_owned_worker", lambda *_args, **_kwargs: {
        "stopped": True, "status": "terminated", "pid": 12345,
    })
    with monkeypatch.context() as fault:
        fault.setattr(store, "_write_artifact", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("ENOSPC")))
        cancelled = cancel_benchmark_job(store, "worker-run")
    assert cancelled["state"] == "cancelled"
    assert cancelled["cleanup_deferred"] is True
    assert store.artifact("worker-run") is None
    assert (run / "evidence" / "swe-canonical" / "manifest.json").is_file()
    original = store.status("worker-run")
    with pytest.raises(BenchmarkJobError) as exc:
        cleanup_benchmark_job(store, "worker-run")
    assert exc.value.code == "missing_swe_canonical_evidence"
    with pytest.raises(BenchmarkJobError) as exc:
        prepare_swe_regrade(store, {"spec": {**value, "run_id": "regrade", "parameters": {"operation": "regrade", "source_run_id": "worker-run"}}})
    assert exc.value.code == "missing_swe_canonical_evidence"
    assert work.exists()
    assert store.status("worker-run") == original


@pytest.mark.parametrize("suite", ["context", "agentic"])
def test_non_swe_cleanup_retains_native_output_without_swe_evidence(monkeypatch, tmp_path, suite):
    value = spec()
    value["suite"] = suite
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(value)
    store.claim("worker-run")
    store.transition("worker-run", "failed", failure={"class": "worker_runtime", "code": "fixture"})
    run = tmp_path / "runs" / "campaign" / "worker-run"
    output = run / "work" / "mini-output"
    output.mkdir(parents=True)
    (output / "trajectory.json").write_text("native partial output")
    _stopped_worker(monkeypatch, store, run, "worker-run")
    original = store.status("worker-run")
    cleaned = cleanup_benchmark_job(store, "worker-run")
    assert cleaned["cleaned"] is True
    assert cleaned["retention"]["state"] == "retained"
    assert (run / "evidence" / "native-partial" / "trajectory.json").read_text() == "native partial output"
    assert not (run / "evidence" / "swe-canonical").exists()
    assert not (run / "work").exists()
    assert store.status("worker-run") == original


def test_unknown_suite_cannot_fall_through_to_swe(monkeypatch):
    monkeypatch.setattr(benchmark_worker, "build_swe_run_plan", lambda *_a, **_kw: pytest.fail("SWE fallback"))
    value = spec()
    value["suite"] = "unknown"
    with pytest.raises(BenchmarkJobError) as exc:
        benchmark_worker._run_suite(None, {"spec": value}, load_profile("smoke"), {})
    assert exc.value.code == "unknown_suite"


@pytest.mark.parametrize("change,code", [({"suite": "historical-suite"}, "unknown_suite"), ({"profile": "missing-profile"}, "profile_not_found")])
def test_historical_unknown_contract_fails_with_durable_evidence(monkeypatch, tmp_path, change, code):
    from anvil_serving.benchmarking import profiles

    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    value = spec()
    value.update(change)
    # Model an older writer without changing the persisted record format.
    with monkeypatch.context() as old_writer:
        old_writer.setattr(profiles, "SUITE_NAMES", profiles.SUITE_NAMES | {value["suite"]})
        old_writer.setattr("anvil_serving.benchmarking.submission.bind_submission_spec", lambda value: value)
        store.submit(value)
    monkeypatch.setattr(benchmark_worker, "prepare_harness_assets", lambda *_a, **_kw: pytest.fail("asset preparation"))
    record = execute_benchmark_job(store, "worker-run")
    assert record["state"] == "failed"
    assert record["failure"]["code"] == code
    assert store.artifact("worker-run")["results"]["evidence"]["completeness"] == "failed"


def test_v2_worker_retains_budget_and_partial_result(monkeypatch, tmp_path):
    value = spec()
    value["profile"] = "pilot-v2"
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(value)
    monkeypatch.setattr(benchmark_worker, "prepare_harness_assets", lambda *_a, **_kw: {"assets": {}})
    monkeypatch.setattr(benchmark_worker, "run_benchmark_preflight", lambda *_a, **_kw: {"schema": "anvil-serving.benchmark-preflight/v1", "passed": True, "observed": {}, "checks": []})
    monkeypatch.setattr(benchmark_worker, "_run_suite", lambda *_a, **_kw: {
        "state": "incomplete", "passed": False,
        "budget": {"model_calls": 2, "exhausted": True},
        "observations": [{"request_id": "partial"}],
        "failure": {"class": "budget_exhausted", "message": "call cap reached"},
    })
    record = execute_benchmark_job(store, "worker-run")
    assert record["state"] == "failed"
    assert record["failure"]["class"] == "resource_exhaustion"
    evidence = store.artifact("worker-run")["results"]["evidence"]
    assert [s["sequence"] for s in evidence["stages"]] == list(range(4))
    run = tmp_path / "runs" / "campaign" / "worker-run"
    admission = json.loads((run / evidence["stages"][0]["evidence"][0]["path"]).read_text())
    assert admission["budgets"] == load_profile("pilot-v2")["suites"]["context"]["budgets"]
    partial = json.loads((run / evidence["stages"][-1]["evidence"][0]["path"]).read_text())
    assert partial["observations"] == [{"request_id": "partial"}]


def test_worker_rejects_replaced_valid_profile_before_preparation_or_traffic(monkeypatch, tmp_path):
    from anvil_serving.benchmarking import profiles

    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    value = spec()
    value.update(profile="pilot-v2", suite="aider", parameters={})
    _, submitted = store.submit(value)
    changed = load_profile("pilot-v2")
    changed["suites"]["aider"]["budgets"]["max_model_calls"] += 1
    changed["content_sha256"] = profiles.profile_content_sha256(changed)
    profiles.validate_profile(changed)
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    (profile_root / "pilot-v2.json").write_text(json.dumps(changed))
    monkeypatch.setattr(profiles, "PROFILE_ROOT", profile_root)
    for name in ("prepare_harness_assets", "run_benchmark_preflight", "_run_suite"):
        monkeypatch.setattr(benchmark_worker, name, lambda *_a, **_kw: pytest.fail("profile drift crossed admission"))
    monkeypatch.setattr(benchmark_worker, "_retain_stopped_aider", lambda *_a, **_kw: {"state": "absent"})

    record = execute_benchmark_job(store, value["run_id"])

    assert record["state"] == "failed"
    assert record["failure"]["code"] == "profile_binding_mismatch"
    assert record["spec"] == submitted["spec"]
    assert record["spec_sha256"] == submitted["spec_sha256"]
    assert record["spec"]["profile_sha256"] != changed["content_sha256"]
    assert store.artifact(value["run_id"])["failure"]["code"] == "profile_binding_mismatch"
    assert store.submit(value) == ("existing", record)


def test_worker_uses_one_profile_snapshot_for_admission_and_preparation(monkeypatch, tmp_path):
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    value = spec()
    value.update(profile="pilot-v2", parameters={})
    _, submitted = store.submit(value)
    profile = load_profile("pilot-v2")
    calls = []

    def load(name):
        calls.append(name)
        return profile

    def prepare(actual, **_kwargs):
        assert actual is profile
        assert actual["content_sha256"] == submitted["spec"]["profile_sha256"]
        raise BenchmarkJobError("test_stop", "stop after verified admission")

    monkeypatch.setattr(benchmark_worker, "load_profile", load)
    monkeypatch.setattr("anvil_serving.benchmarking.submission.load_profile", lambda *_a: pytest.fail("second profile load"))
    monkeypatch.setattr(benchmark_worker, "prepare_harness_assets", prepare)
    result = execute_benchmark_job(store, value["run_id"])
    assert calls == ["pilot-v2"]
    assert result["failure"]["code"] == "test_stop"
