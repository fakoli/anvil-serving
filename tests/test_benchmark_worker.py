from __future__ import annotations

from http.server import BaseHTTPRequestHandler
import io
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


@pytest.mark.parametrize('slow_stage', ['preflight', 'prepared_verification'])
def test_campaign_requires_full_timeout_after_preflight_and_prepared_verification(monkeypatch, tmp_path, slow_stage):
    from anvil_serving.benchmarking import campaign_execution as execution, aider
    from anvil_serving.benchmarking.submission import bind_submission_spec
    value = spec()
    value.update(profile='pilot-v2', suite='aider', timeout_s=30,
                 parameters={'instance_ids': ['rust/exercises/practice/accumulate']})
    value = execution.bind_campaign_child(bind_submission_spec(value), manifest_sha256='a' * 64,
        campaign_id='campaign-run', cell_id='cell', arm_id='arm', batch_id='batch')
    store = BenchmarkJobStore(str(tmp_path / 'jobs.db'), run_root=str(tmp_path / 'runs'))
    store.submit(value)
    monkeypatch.setattr(benchmark_worker.shutil, "disk_usage", lambda _path: SimpleNamespace(free=2**40))
    clock, calls = [0], []
    monkeypatch.setattr(execution, 'time', SimpleNamespace(monotonic=lambda: clock[0],
        perf_counter=time.perf_counter, sleep=time.sleep))
    context = {'spec': value, 'payload': {'assets': {}}, 'receipt': {'status': 'active'},
               'release': {'boot_id': execution._boot_id(), 'traffic_deadline_monotonic': 90}}
    monkeypatch.setattr(execution, 'enter_campaign', lambda *a, **k: context)
    def preflight(*args, **kwargs):
        calls.append('preflight')
        clock[0] = 61 if slow_stage == 'preflight' else 10
        return {'schema': 'anvil-serving.benchmark-preflight/v1', 'passed': True, 'observed': {}, 'checks': []}
    def verify(*args, **kwargs):
        calls.append('prepared_verification')
        if slow_stage == 'prepared_verification':
            clock[0] = 61
        return {}, {'plan': {}}
    monkeypatch.setattr(benchmark_worker, 'run_benchmark_preflight', preflight)
    monkeypatch.setattr(execution, 'verify_child', verify)
    monkeypatch.setattr(aider, 'run_aider_benchmark', lambda *a, **k: pytest.fail('short window started suite'))
    monkeypatch.setattr(execution, 'close_campaign', lambda *a, **k: {'status': 'closed'})
    monkeypatch.setattr(benchmark_worker, 'campaign_result_reference', lambda *a, **k: {})
    monkeypatch.setattr(benchmark_worker, '_retain_external_artifacts', lambda *a, **k: {'state': 'absent'})
    result = execute_benchmark_job(store, value['run_id'])
    assert calls == ['preflight', 'prepared_verification']
    assert result['state'] == 'failed' and result['failure']['code'] == 'campaign_budget_exceeded'
    assert 'requires 30s after entry; remaining 29s' in result['failure']['message']


def test_detached_launcher_keeps_credentials_out_of_argv(monkeypatch, tmp_path):
    observed = {}
    monkeypatch.setattr(benchmark_worker, "_process_start_identity", lambda _pid: "test-start")

    def popen(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return SimpleNamespace(pid=987, stdin=io.BytesIO())

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


@pytest.mark.skipif(os.name == "nt", reason="process-group custody is POSIX-specific")
@pytest.mark.parametrize("failure", ["identity", "write", "after-write"])
def test_failed_custody_handoff_stops_real_child_before_claim_or_preparation(monkeypatch, tmp_path, failure):
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    _, submitted = store.submit(spec())
    ready, prepared = tmp_path / "ready", tmp_path / "prepared"
    children = []
    write_json = benchmark_worker.atomic_write_json
    code = (
        "import sys; from pathlib import Path; "
        "from anvil_serving.benchmarking import worker; "
        f"worker.prepare_harness_assets = lambda *a, **kw: Path({str(prepared)!r}).write_text('unexpected'); "
        "worker.run_benchmark_preflight = lambda *a, **kw: {}; "
        f"Path({str(ready)!r}).touch(); "
        "raise SystemExit(worker.main(sys.argv[1:]))"
    )

    def popen(argv, **kwargs):
        process = subprocess.Popen([sys.executable, "-c", code, *argv[3:]], **kwargs)
        children.append(process)
        return process

    def fail_write(*_args, **_kwargs):
        if failure == "after-write":
            write_json(*_args, **_kwargs)
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "child failed to reach the custody gate"
        time.sleep(0.1)
        assert store.status("worker-run") == submitted
        assert not prepared.exists()
        raise OSError("disk full")

    try:
        with monkeypatch.context() as fault:
            fault.setattr(benchmark_worker, "atomic_write_json", fail_write)
            if failure == "identity":
                fault.setattr(benchmark_worker, "_process_start_identity", lambda _pid: None)
            with pytest.raises(BenchmarkJobError) as exc:
                launch_benchmark_job(
                    path=store.path, run_root=store.run_root, run_id="worker-run", popen=popen,
                )
        assert exc.value.code == "worker_launch_failed"
        assert len(children) == 1
        assert children[0].poll() is not None
        assert not benchmark_worker._process_group_running(children[0].pid)
        assert not prepared.exists()
        assert store.status("worker-run") == submitted
        assert store.submit(spec()) == ("existing", submitted)
        assert (tmp_path / "runs" / "campaign" / "worker-run" / "worker.json").exists() is (failure == "after-write")
        assert cancel_benchmark_job(store, "worker-run")["state"] == "cancelled"
        assert store.submit({**spec(), "run_id": "replacement-run"})[0] == "submitted"
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)


@pytest.mark.parametrize("handoff", ["parent-eof", "timeout", "wrong-custody", "missing-start", "oversize", "fifo"])
def test_real_child_requires_bounded_matching_custody_before_execution(tmp_path, handoff):
    if handoff == "fifo" and os.name == "nt":
        pytest.skip("FIFO requires POSIX")
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    _, submitted = store.submit(spec())
    executed = tmp_path / "executed"
    code = (
        "import sys; from pathlib import Path; "
        "from anvil_serving.benchmarking import worker; "
        "worker._WORKER_HANDOFF_TIMEOUT_S = 0.1; "
        f"worker.execute_benchmark_job = lambda *a: Path({str(executed)!r}).touch(); "
        "raise SystemExit(worker.main(sys.argv[1:]))"
    )
    if handoff == "missing-start":
        code = code.replace("raise SystemExit", "worker._process_start_identity = lambda pid: None; raise SystemExit")
    process = subprocess.Popen(
        [sys.executable, "-c", code, "--db", store.path, "--run-root", store.run_root, "--run-id", "worker-run"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        start_new_session=os.name != "nt",
    )
    try:
        if handoff == "parent-eof":
            process.stdin.close()
        elif handoff not in {"timeout", "parent-eof"}:
            run = tmp_path / "runs" / "campaign" / "worker-run"
            run.mkdir(parents=True)
            (run / "worker.json").write_text(json.dumps({
                "run_id": "worker-run", "pid": process.pid + (handoff == "wrong-custody"), "db": store.path,
                "run_root": store.run_root, "process_start": None if handoff == "missing-start" else benchmark_worker._process_start_identity(process.pid),
                "process_group_id": process.pid, "session_id": process.pid,
            }))
            if handoff == "oversize":
                with (run / "worker.json").open("a") as handle:
                    handle.write(" " * 65536)
            if handoff == "fifo":
                (run / "worker.json").unlink()
                os.mkfifo(run / "worker.json")
            process.stdin.write(b"1")
            process.stdin.flush()
        assert process.wait(timeout=5) == 2, process.stderr.read().decode()
        assert not executed.exists()
        assert store.status("worker-run") == submitted
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdin.close()
        process.stderr.close()


@pytest.mark.skipif(os.name == "nt", reason="process-group cancellation is POSIX-specific")
def test_rejected_relaunch_preserves_original_worker_cancellation_identity(monkeypatch, tmp_path):
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(spec())
    monkeypatch.setattr(benchmark_worker, "_process_start_identity", lambda _pid: "original-start")
    launch_benchmark_job(
        path=store.path, run_root=store.run_root, run_id="worker-run",
        popen=lambda *_a, **_kw: SimpleNamespace(pid=987, stdin=io.BytesIO()),
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


@pytest.mark.skipif(os.name == "nt", reason="process-group custody is POSIX-specific")
def test_competing_deadline_watchdog_stop_is_idempotent_for_campaign_owner(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from anvil_serving.benchmarking import campaign_execution as execution
    from tests.test_campaign_batch_closure import active
    from tests.test_campaign_telemetry import setup

    manifest, store, specs, sampler, release, clock = setup(tmp_path, monkeypatch, count=4)
    contexts = active(store, specs, release)
    pids = {s["run_id"]: 20000 + i for i, s in enumerate(specs)}
    alive = dict.fromkeys(pids.values(), True)
    for value, context in zip(specs, contexts):
        pid = pids[value["run_id"]]
        (context["path"].parent / "worker.json").write_text(json.dumps({
            "run_id": value["run_id"], "pid": pid, "db": os.path.realpath(store.path),
            "run_root": store.run_root, "process_start": "started", "process_group_id": pid, "session_id": pid,
        }))
    def arguments(pid):
        if not alive[pid]:
            return None
        run_id = next(run for run, value in pids.items() if value == pid)
        return (sys.executable, "-m", "anvil_serving.benchmarking.worker", "--db",
                os.path.realpath(store.path), "--run-root", store.run_root, "--run-id", run_id)
    def group_id(pid):
        if alive[pid]:
            return pid
        raise ProcessLookupError()
    monkeypatch.setattr(benchmark_worker, "_process_arguments", arguments)
    monkeypatch.setattr(benchmark_worker, "_process_start_identity", lambda pid: "started")
    monkeypatch.setattr(benchmark_worker.os, "getpgid", group_id)
    monkeypatch.setattr(benchmark_worker.os, "getsid", group_id)
    monkeypatch.setattr(execution, "verify_child", lambda *a, **k: (manifest, {}))
    entered, resume = threading.Event(), threading.Event()
    signals = []
    def signal_group(pid, sig):
        assert alive[pid]
        signals.append((pid, sig))
        alive[pid] = False
        if pid == pids[specs[0]["run_id"]]:
            entered.set()
            assert resume.wait(5)
    monkeypatch.setattr(benchmark_worker.os, "killpg", signal_group)
    monkeypatch.setattr(benchmark_worker, "_process_group_running", lambda pid: alive[pid])
    drains = []
    def drain(*args, **kwargs):
        assert not any(alive.values())
        assert all(execution._read(c["path"])["clients_stopped"] for c in contexts)
        drains.append(True)
        clock[0] += 35
        return {"status": "verified"}
    monkeypatch.setattr(execution, "observe_drain", drain)
    clock[0] = release["traffic_deadline_monotonic"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(benchmark_worker.cancel_campaign_jobs, manifest, store)
        try:
            assert entered.wait(3)
            # The losing watchdog still performs its bounded safety stop. The
            # campaign owner must accept proof that this group is already gone.
            benchmark_worker._campaign_watchdog(store.path, store.run_root, specs[-1]["run_id"])
            assert not alive[pids[specs[-1]["run_id"]]]
        finally:
            resume.set()
        result = owner.result(5)
    assert result["phase"] == "completed"
    assert len(signals) == 4 and all(sig == signal.SIGTERM for _, sig in signals)
    assert len(drains) == 1
    assert all(store.status(s["run_id"])["state"] == "cancelled" for s in specs)
    last = result["children"][-1]
    assert last["worker_terminated"] is True and last["cleanup_deferred"] is False
    files = [c["path"] for c in contexts]
    files += [c["path"].parent / "artifact.json" for c in contexts]
    files += [c["path"].parent / "evidence/cancellation-native-output.json" for c in contexts]
    saved = {p: p.read_bytes() for p in files}
    assert benchmark_worker.cancel_campaign_jobs(manifest, store)["phase"] == "completed"
    assert all(p.read_bytes() == raw for p, raw in saved.items())
    assert len(signals) == 4 and len(drains) == 1
