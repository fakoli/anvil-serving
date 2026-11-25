"""Independent queue, custody and containment checks for the native front door."""
from contextlib import nullcontext
import dataclasses
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from anvil_serving.media import worker_pool as module, worker_runtime
from anvil_serving.media.comfyui import ComfyUIClient
from anvil_serving.media.contracts import RenderedWorkflow
from anvil_serving.media.errors import MediaError, MediaSubmissionRefused
from tests.media.test_cli import _workflow


class Native:
    def __init__(self):
        self.prompts = {}
        self.posts = 0
        self.uploads = []
        self.refuse = self.lose = False

    def request_json(self, method, path, payload=None, **_kwargs):
        if path == "/system_stats":
            return {"system": {"anvil_runtime": worker_runtime.runtime_status(),
                    "anvil_cuda_budget": {"limitBytes": 13312 * 1048576, "allocator": "native", "dynamicVram": False}}}
        if path == "/prompt":
            self.posts += 1
            if self.refuse:
                raise MediaSubmissionRefused("worker_busy", "definitely not queued")
            if self.lose:
                raise MediaError("backend_unavailable", "acceptance unknown")
            self.prompts["native"] = {"prompt": [0, "native", {}, {"client_id": payload["client_id"]}],
                "outputs": {}, "status": {"completed": False, "status_str": "running"}}
            return {"prompt_id": "native"}
        if path == "/queue":
            return {"queue_running": [r["prompt"] for r in self.prompts.values() if not r["status"]["completed"]],
                    "queue_pending": []}
        if path.startswith("/history/"):
            name = path.split("/")[-1]
            return {name: self.prompts[name]} if name in self.prompts else {}
        return {}

    def request_bytes(self, method, path, *, body, **kwargs):
        self.uploads.append(body)
        return b'{"name":"reference.png"}'

    def find_prompt(self, job_id):
        return next((k for k, r in self.prompts.items() if r["prompt"][3]["client_id"] == "anvil-" + job_id), None)

    def queue(self):
        return module.ComfyUIClient.queue(self)


@pytest.fixture
def pool(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *_: None))
    monkeypatch.setattr(module.ctypes, "CDLL", lambda *_args, **_kwargs: SimpleNamespace(prctl=lambda *_: 0))
    environment, _ = worker_runtime.child_environment({"timeout_seconds": 7200, "admission_seconds": 2700,
                                                     "admission_margin_seconds": 30})
    monkeypatch.setenv(worker_runtime.ENVIRONMENT, environment[worker_runtime.ENVIRONMENT])
    worker_runtime._runtime.cache_clear()
    monkeypatch.setattr(module, "gpu_memory", lambda: (32607, 30000))
    monkeypatch.setattr(module, "load_policy", lambda _: {"sha256": "test-policy", "min_commit_gib": 16, "max_age_seconds": 5})
    monkeypatch.setattr(module, "signal_reason", lambda _: "")
    monkeypatch.setattr(module, "_read_signal", lambda _: {"allow": True, "policy_sha256": "test-policy",
                                                           "sampled_at": time.time(), "commit_free_gib": 60})
    policy = {"root": str(tmp_path), "imageBudgetMiB": 13312, "deviceReserveMiB": 1024,
              "hostPolicy": "/policy", "hostSignal": "/signal", "imageCommitReserveGiB": 16,
              "workflows": [{"id": w, "version": "v1", "profile": "", "mode": m}
                            for w, m in (("image.a", "images"), ("video.a", "exclusive"))]}
    value = module.WorkerPool(policy)
    value.mode = "images"
    value.children = [{"id": str(i), "client": Native(), "ready": True,
                       "process": SimpleNamespace(poll=lambda: None)} for i in range(2)]
    yield value
    value.db.close()
    value._owner.close()
    worker_runtime._runtime.cache_clear()


def prepare(pool, n, workflow="image.a"):
    return pool.prepare({"jobId": "job_" + str(n), "sequence": n, "workflow": workflow, "version": "v1", "profile": ""})


def payload(n):
    return {"client_id": "anvil-job_" + str(n), "prompt": {"1": {"class_type": "Test", "inputs": {}}},
            "extra_data": {"anvil_runtime": {"runId": worker_runtime.runtime_status()["runId"], "requiredSeconds": 2700}}}


def test_fifo_isolation_cached_custody_and_exact_native_refusal(pool):
    first = prepare(pool, 1)
    assert first["ready"] and not prepare(pool, 2)["ready"]  # An earlier unsent lease fences starts.
    pool.upload(first["lease"], b"owned-one", "multipart/form-data; boundary=test")
    a = pool.submit(first["lease"], payload(1))["prompt_id"]
    assert pool.submit(first["lease"], payload(1))["prompt_id"] == a
    second = prepare(pool, 2)
    pool.upload(second["lease"], b"owned-two", "multipart/form-data; boundary=test")
    b = pool.submit(second["lease"], payload(2))["prompt_id"]
    assert a != b and [c["client"].posts for c in pool.children] == [1, 1]
    assert [c["client"].uploads for c in pool.children] == [[b"owned-one"], [b"owned-two"]]
    assert not prepare(pool, 3, "video.a")["ready"] and not prepare(pool, 4)["ready"]
    for child in pool.children:
        child["client"].prompts["native"].update(outputs={"1": {"images": [{"filename": "frame.png", "type": "output"}]}},
                                               status={"completed": True, "status_str": "success"})
        directory = pool.root / "children" / child["id"] / "output"
        directory.mkdir(parents=True)
        (directory / "frame.png").write_bytes(child["id"].encode())
    pool.refresh()
    assert not pool._active() and pool._queues_idle()
    assert pool.history(a)[a]["prompt"][3]["client_id"] == "anvil-job_1"
    pool.release({"jobId": "job_3"})
    third = prepare(pool, 4)
    pool.children[0]["client"].refuse = True
    c = pool.submit(third["lease"], payload(4))["prompt_id"]
    assert pool.history(c)[c]["status"]["status_str"] == "error" and not pool._active()
    pool.children = []  # The old native HTTP servers no longer exist.
    assert pool.view(f"filename=frame.png&subfolder={a}%2F&type=output").read_bytes() == b"0"
    with pytest.raises(MediaError):
        pool.view("filename=owner.lock&subfolder=0%2F..%2F..%2F&type=output")


def test_unknown_submission_never_expires_or_replays_on_another_child(pool):
    lease = prepare(pool, 1)["lease"]
    pool.children[0]["client"].lose = True
    with pytest.raises(MediaError):
        pool.submit(lease, payload(1))
    pool.db.execute("UPDATE prompts SET expires=0")
    pool.refresh()
    finder = ComfyUIClient('http://127.0.0.1:8188')
    finder.request_json = lambda _method, path: pool.queue() if path == '/queue' else pool.history()
    assert finder.find_prompt('job_1') is None  # Preparation/intent is not native acceptance proof.
    assert pool._active()[0]["phase"] == "submitting"
    pool.release({"jobId": "job_1"})
    assert not prepare(pool, 2)["ready"]
    with pytest.raises(MediaError):
        pool.submit(lease, payload(1))
    assert [c["client"].posts for c in pool.children] == [1, 0]


def test_retired_lease_cannot_be_revived_on_a_replacement_child(pool):
    old = prepare(pool, 1)["lease"]
    pool.release({"jobId": "job_1"})
    pool.children[0]["id"] = "replacement"
    current = prepare(pool, 1)["lease"]
    assert current != old
    with pytest.raises(MediaError):
        pool.upload(old, b"stale", "multipart/form-data; boundary=test")
    assert pool.submit(current, payload(1))["prompt_id"]
    native = pool.children[1]["client"].request_json
    pool.children[1]["client"].request_json = lambda method, path: ({"queue_running": False, "queue_pending": None}
                                                                 if path == "/queue" else native(method, path))
    with pytest.raises(MediaError):
        prepare(pool, 2)


def test_http_client_lease_is_request_scoped_and_pool_failure_is_definite(pool):
    server = module._Server(("127.0.0.1", 0), pool)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    backend = ComfyUIClient("http://127.0.0.1:" + str(server.server_port))
    workflow = RenderedWorkflow(dataclasses.replace(_workflow(), id="image.a"), {"1": {"inputs": {}}}, "a" * 64)
    try:
        prepared = backend.prepare_execution(workflow, job_id="job_1", sequence=1)
        assert prepared is not backend and not backend._execution_headers
        assert prepared.upload_input("reference.png", b"abc") == "reference.png"
        assert len(pool.children[0]["client"].uploads) == 1 and not pool.children[1]["client"].uploads
        pool.release({"jobId": "job_1"})
        pool.fault = "reserve_unavailable"
        with pytest.raises(MediaError) as failure:
            backend.prepare_execution(workflow, job_id="job_2", sequence=2)
        assert failure.value.code == "worker_pool_preparation_failed"
        assert not any(c["client"].posts for c in pool.children)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_history_timeouts_cannot_disable_the_physical_memory_guard(pool, monkeypatch):
    ticks = iter((False, True))
    pool.stop = SimpleNamespace(wait=lambda _: next(ticks))
    pool._start_children = lambda _: None
    pool._stop_children = lambda: None
    def unavailable():
        raise MediaError("backend_unavailable", "history timed out")
    pool.refresh = unavailable
    monkeypatch.setattr(module, "gpu_memory", lambda: (32607, 0))
    pool.drive()
    assert pool.fault == "worker_pool_refused"


def test_retention_prunes_terminal_custody_without_releasing_unknown_work(pool):
    first = prepare(pool, 1)["lease"]
    pool.submit(first, payload(1))
    pool.children[0]["client"].prompts["native"]["status"].update(completed=True, status_str="success")
    pool.refresh()
    directory = pool.root / "children" / "0" / "output"
    directory.mkdir(parents=True)
    (directory / "frame.png").write_bytes(b"retained")
    os.utime(directory.parent, (0, 0))
    pool.children.pop(0)
    second = prepare(pool, 2)["lease"]
    pool.children[0]["client"].lose = True
    with pytest.raises(MediaError):
        pool.submit(second, payload(2))
    pool.db.execute("UPDATE prompts SET retained_until=0")
    pool.children = []
    pool._prune(force=True)
    assert not directory.parent.exists()
    assert len(pool._rows()) == 1 and pool._rows()[0]["phase"] == "submitting"


def test_commit_admission_waits_for_headroom_and_rejects_stale_signals(pool, monkeypatch):
    signal_value = {"allow": True, "policy_sha256": "test-policy", "sampled_at": time.time(), "commit_free_gib": 40}
    monkeypatch.setattr(module, "_read_signal", lambda _: signal_value)
    first = prepare(pool, 1)["lease"]
    pool.submit(first, payload(1))
    assert not prepare(pool, 2)["ready"]  # An occupied image child requires the larger paired reserve.
    signal_value['commit_free_gib'] = 60
    signal_value['sampled_at'] = time.time() - 10
    assert not prepare(pool, 2)["ready"]
    signal_value['sampled_at'] = time.time()
    assert prepare(pool, 2)["ready"]


def test_idle_children_reclaim_memory_once_before_a_new_batch(pool):
    pool.generation_used = True
    pool._host_ready = lambda active: active == 0
    assert not prepare(pool, 1)["ready"]
    calls = []
    pool._start_children = lambda mode: calls.append(mode)
    pool._stop_children = lambda: None
    ticks = iter((False, True))
    pool.stop = SimpleNamespace(wait=lambda _: next(ticks))
    pool.drive()
    assert calls == ["images", "images"] and not pool.fault


def test_config_keeps_qualified_host_commit_reservation_calibratable(tmp_path):
    value = {"schema": module.FEATURE, "root": str(tmp_path), "command": [sys.executable, "main.py"],
             "imageBudgetMiB": 13312, "contextReserveMiB": 1024, "deviceReserveMiB": 1024,
             "hostPolicy": str(tmp_path / "policy.toml"), "hostSignal": str(tmp_path / "signal.json"),
             "imageCommitReserveGiB": 15, "workflows": [{"id": "image.a", "version": "v1", "profile": "", "mode": "images"}]}
    path = tmp_path / "pool.json"
    path.write_text(json.dumps(value))
    assert module.load_config(path)["imageCommitReserveGiB"] == 15
    value["imageCommitReserveGiB"] = 0
    path.write_text(json.dumps(value))
    with pytest.raises(MediaError):
        module.load_config(path)


def test_cli_preview_cannot_start_children_before_confirmation(monkeypatch, capsys):
    monkeypatch.setattr(module.sys, 'platform', 'linux')
    monkeypatch.setattr(module, 'load_config', lambda _: {})
    def started(_config):
        raise RuntimeError('confirmed start reached')
    monkeypatch.setattr(module, 'WorkerPool', started)
    assert module.main(['serve','--config','policy.json']) == 0
    assert json.loads(capsys.readouterr().out)['gpuStarted'] is False
    assert module.main(['serve','--config','policy.json','--dry-run','--confirm']) == 0
    assert json.loads(capsys.readouterr().out)['gpuStarted'] is False
    with pytest.raises(RuntimeError, match='confirmed start reached'):
        module.main(['serve','--config','policy.json','--confirm'])


@pytest.mark.skipif(sys.platform != "linux", reason="Linux exec parent-death contract")
def test_launcher_kills_child_when_parent_dies():
    code = """import os,subprocess,sys,time
p=subprocess.Popen([sys.executable,'-m','anvil_serving.media.worker_pool','_child',str(os.getpid()),
                    sys.executable,'-c','import time;time.sleep(60)'])
print(p.pid,flush=True)
time.sleep(60)
"""
    parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    child = None
    try:
        child = int(parent.stdout.readline())
        time.sleep(.2)
        parent.kill()
        parent.wait(timeout=3)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                # The child can exit between checks; reading a vanished /proc
                # entry raises ESRCH (ProcessLookupError), which means it is gone.
                state = Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()[0]
            except (FileNotFoundError, ProcessLookupError):
                break
            if state == "Z":
                break
            time.sleep(.05)
        else:
            pytest.fail("native child survived pool death")
    finally:
        parent.kill()
        parent.wait(timeout=3)
        if child:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native TCP restart semantics")
def test_child_port_probe_accepts_closed_server_time_wait_but_refuses_live_owner():
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', 0))
        address = server.getsockname()
        server.listen()
        with socket.socket() as refused:
            refused.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError):
                refused.bind(address)
        with socket.create_connection(address) as client:
            connection, _ = server.accept()
            connection.close()
            assert client.recv(1) == b''
    with socket.socket() as restarted:
        restarted.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        restarted.bind(address)


def test_opt_in_gpu_guard_blocks_prepare_and_quarantines_active_assignment(pool, monkeypatch):
    from contextlib import contextmanager
    events = []
    fresh = True

    def sample():
        events.append("sample")
        if not fresh:
            raise MediaError("gpu_telemetry_unavailable", "physical GPU telemetry unavailable", status=503)
        return 32607, 30000

    @contextmanager
    def contain():
        events.append("fence")
        yield
        events.append("unfence")

    pool._nvml = SimpleNamespace(epoch=0, validate=lambda _: nullcontext(), sample=sample,
                                 sample_with_revision=lambda: (0, sample()), contain=contain, close=lambda: events.append("close"))
    monkeypatch.setattr(module, "gpu_memory", lambda: pytest.fail("opt-in silently fell back"))
    assert pool.diagnostics()["gpuFreeMiB"] == 30000
    first = prepare(pool, 1)
    alias = pool.submit(first["lease"], payload(1))["prompt_id"]
    fresh = False
    with pytest.raises(MediaError, match="physical GPU telemetry unavailable"):
        prepare(pool, 2)
    assert len(pool._active()) == 1 and pool._active()[0]["phase"] == "active"
    pool._start_children = lambda _: None
    pool._stop_render_children = lambda: events.append("sweep")
    pool.stop = SimpleNamespace(wait=lambda _: False)
    pool.drive()
    assert pool.fault == "gpu_telemetry_unavailable"
    assert pool.history(alias)[alias]["status"]["status_str"] == "error"
    assert events == ["sample", "sample", "sample", "sample", "fence", "sweep", "unfence",
                      "close", "fence", "sweep", "unfence"]


def test_opt_in_start_checks_same_two_image_budget_before_launch(pool, monkeypatch):
    original = pool.children.copy()
    pool.config["contextReserveMiB"] = 1024
    pool._nvml = SimpleNamespace(epoch=0, validate=lambda _: nullcontext(), start=lambda: None, sample=lambda: (32607, 25000))
    monkeypatch.setattr(module, "gpu_memory", lambda: pytest.fail("opt-in silently fell back"))
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("budget was denied"))
    with pytest.raises(MediaError, match="device cannot reserve two isolated image workers"):
        pool._start_children("images")
    assert pool.children == original


def test_prepared_before_fault_cannot_submit_but_known_replay_survives(pool, monkeypatch):
    pool._nvml = SimpleNamespace(epoch=0, validate=lambda _: nullcontext(), sample=lambda: (32607, 30000),
                                 sample_with_revision=lambda: (0, (32607, 30000)))
    monkeypatch.setattr(module, "gpu_memory", lambda: pytest.fail("opt-in silently fell back"))
    first = prepare(pool, 1)
    posted = pool.submit(first["lease"], payload(1))
    second = prepare(pool, 2)
    pool.fault = "gpu_telemetry_unavailable"
    assert pool.submit(first["lease"], payload(1)) == posted
    with pytest.raises(worker_runtime.WorkerRuntimeRefused, match="worker_budget_unknown"):
        pool.submit(second["lease"], payload(2))
    assert [c["client"].posts for c in pool.children] == [1, 0]


def test_opt_in_provider_never_waits_under_pool_lock_or_admits_stale_sample(pool, monkeypatch):
    clock = [100.]

    def read():
        assert not pool.lock._is_owned()
        return 32607, 30000

    pool._nvml = SimpleNamespace(epoch=0, validate=lambda _: nullcontext(), sample=read,
                                 sample_with_revision=lambda: (0, read()))
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    assert pool.diagnostics()["gpuFreeMiB"] == 30000
    first = prepare(pool, 1)
    assert pool.submit(first["lease"], payload(1))["prompt_id"]
    next_child = pool.children[1]["client"]
    original = next_child.queue

    def delayed_queue():
        clock[0] += 4
        return original()

    next_child.queue = delayed_queue
    with pytest.raises(MediaError, match="GPU telemetry sample is stale"):
        prepare(pool, 2)
    assert [c["client"].posts for c in pool.children] == [1, 0]


def test_provider_epoch_change_invalidates_prepared_decision(pool):
    def validate(expected):
        if expected is not None and expected != telemetry.epoch:
            raise MediaError("gpu_telemetry_unavailable", "physical GPU telemetry unavailable", status=503)
        return nullcontext()

    telemetry = SimpleNamespace(epoch=0, validate=validate, sample=lambda: (32607, 30000),
                                 sample_with_revision=lambda: (0, (32607, 30000)))
    pool._nvml = telemetry
    first = prepare(pool, 1)
    pool.submit(first["lease"], payload(1))
    next_child = pool.children[1]["client"]
    original = next_child.queue

    def provider_lost_between_sample_and_decision():
        telemetry.epoch += 1
        return original()

    next_child.queue = provider_lost_between_sample_and_decision
    with pytest.raises(MediaError, match="physical GPU telemetry unavailable"):
        prepare(pool, 2)
    assert not any(row["phase"] == "prepared" for row in pool._active())


@pytest.mark.parametrize("headroom", [1023, 1024])
def test_nvml_exclusive_start_checks_physical_floor_before_launch(pool, monkeypatch, headroom):
    pool.config["contextReserveMiB"] = 1024
    pool._nvml = SimpleNamespace(start=lambda: None, sample=lambda: (32607, headroom))
    # Avoid a real socket/child; the stub proves whether startup reached launch.
    sink = SimpleNamespace(setsockopt=lambda *_: None, bind=lambda *_: None)
    monkeypatch.setattr(module.socket, "socket", lambda: nullcontext(sink))

    class WouldLaunch(Exception):
        pass

    def launch(*_args, **_kwargs):
        raise WouldLaunch

    pool.config["command"] = [sys.executable, "fixture.py"]
    monkeypatch.setattr(module.subprocess, "Popen", launch)
    if headroom == 1024:
        with pytest.raises(WouldLaunch):
            pool._start_children("exclusive")
    else:
        with pytest.raises(MediaError, match="physical GPU reserve was breached"):
            pool._start_children("exclusive")
