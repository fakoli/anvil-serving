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

from anvil_serving.benchmarking.jobs import BenchmarkJobError
from anvil_serving.benchmarking.jobs import JOB_SPEC_SCHEMA
from anvil_serving.benchmarking import swe as swe_benchmark
from anvil_serving.benchmarking import worker as benchmark_worker
from anvil_serving.benchmarking.profiles import load_profile
from anvil_serving.benchmarking.worker import cancel_benchmark_job, execute_benchmark_job, launch_benchmark_job
from anvil_serving.control_plane.controller.store import BenchmarkJobStore


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
        "parameters": {"case_limit": 1, "model_host_id": "model-host"},
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
    BenchmarkJobStore(database, run_root=run_root).submit(spec())

    with pytest.raises(BenchmarkJobError) as exc:
        launch_benchmark_job(
            path=database,
            run_root=run_root,
            run_id="worker-run",
            popen=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("blocked")),
        )

    assert exc.value.code == "worker_launch_failed"


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
