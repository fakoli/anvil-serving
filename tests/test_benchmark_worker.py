from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import re
import threading
import time
from types import SimpleNamespace

from anvil_serving.benchmarking.jobs import JOB_SPEC_SCHEMA
from anvil_serving.benchmarking.worker import execute_benchmark_job, launch_benchmark_job
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
        "parameters": {"case_limit": 1},
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
