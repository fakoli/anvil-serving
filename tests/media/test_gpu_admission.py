"""GPU-free process and custody regressions for the opt-in sampler."""
from contextlib import ExitStack, contextmanager
from email.message import Message
from io import BytesIO
import os
from pathlib import Path
from queue import Queue
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from anvil_serving.media import gpu_telemetry as gpu
from anvil_serving.media import worker_pool as poolmod
from anvil_serving.media import worker_runtime as runtime
from anvil_serving.media.comfyui import ComfyUIClient, _worker_refusal
from anvil_serving.media.errors import MediaError, MediaSubmissionRefused

_UUID = "GPU-11111111-1111-1111-1111-111111111111"
_CHILD = r"""
import pathlib, socket, struct, sys, time
s=socket.socket(fileno=int(sys.argv[1]))
p=pathlib.Path(sys.argv[2])
while s.recv(1) == b'?':
    if (p/'hold').exists():
        (p/'inside').touch()
        while (p/'hold').exists():
            time.sleep(.005)
    s.sendall(struct.pack('!40sQQ', sys.argv[3].encode(), 32768<<20, (1023 if (p/'low').exists() else 30000)<<20))
"""


@unittest.skipUnless(sys.platform == "linux", "GPU worker pool requires Linux")
class TestGpuAdmission(unittest.TestCase):
    def setUp(self):
        ctx = ExitStack()
        self.addCleanup(ctx.close)
        self.root = ctx.enter_context(tempfile.TemporaryDirectory())
        self.path = Path(self.root)
        self.addCleanup(runtime._runtime.cache_clear)
        env, _ = runtime.child_environment({"timeout_seconds": 7200, "admission_seconds": 2700,
                                            "admission_margin_seconds": 30})
        ctx.enter_context(patch.dict(os.environ, env))
        runtime._runtime.cache_clear()
        ctx.enter_context(patch.object(poolmod, "ctypes", SimpleNamespace(
            CDLL=lambda *_a, **_kw: SimpleNamespace(prctl=lambda *_: 0))))
        ctx.enter_context(patch.object(poolmod, "load_policy", lambda _: {}))
        ctx.enter_context(patch.object(gpu, "_helper_command", lambda fd: [
            sys.executable, "-c", _CHILD, str(fd), self.root, _UUID,
        ]))
        ctx.enter_context(patch.object(poolmod, "gpu_memory", side_effect=AssertionError("no stock GPU command")))
        self.p = poolmod.WorkerPool({
            "root": self.root, "hostPolicy": str(self.path / "policy.json"),
            "hostSignal": str(self.path / "signal.json"),
            "deviceReserveMiB": 1024, "gpuTelemetry": {"provider": "nvml-v1", "deviceUuid": _UUID},
            "workflows": [{"id": "video.a", "version": "v1", "profile": "", "mode": "exclusive"}],
        })
        ctx.callback(self.p._owner.close)
        ctx.callback(self.p.db.close)
        ctx.callback(self.p._nvml.close)
        self.p._nvml.start()
        self.p.mode = "exclusive"
        self.posts = 0
        self.p.children = [{"id": "test-child", "ready": True, "process": SimpleNamespace(poll=lambda: None),
                            "client": SimpleNamespace(request_json=self.requests,
                                       queue=lambda: {"running": [], "pending": []})}]

    def requests(self, method, path, *_args, **_kwargs):
        if (method, path) == ("GET", "/system_stats"):
            return {"system": {"anvil_runtime": runtime.runtime_status()}}
        if (method, path) == ("POST", "/prompt"):
            self.posts += 1
            return {"prompt_id": "one-native"}
        self.fail("unexpected dispatch")

    def prep(self):
        return self.p.prepare({"jobId": "cpu_fixture", "sequence": 1, "workflow": "video.a",
                               "version": "v1", "profile": ""})

    def payload(self):
        return {"client_id": "anvil-cpu_fixture", "prompt": {}, "extra_data": {
            "anvil_runtime": {"runId": self.p.run_id, "requiredSeconds": 2}}}

    def assert_definitely_refused(self, lease):
        body = self.payload()
        with self.assertRaises(runtime.WorkerRuntimeRefused) as ctx:
            self.p.submit(lease, body)
        self.assertEqual(_worker_refusal(poolmod._json(ctx.exception.response()), body), "worker_budget_unknown")
        self.assertEqual(self.posts, 0)
        row, = self.p._rows()
        self.assertEqual((row["phase"], row["digest"]), ("prepared", ""))

    def test_stale_last_good_does_not_send_or_commit_intent(self):
        response = self.prep()
        self.assertTrue(response["ready"])
        original = gpu.time.monotonic
        with patch.object(gpu, "time", SimpleNamespace(monotonic=lambda: original() + 5)):
            self.assert_definitely_refused(response["lease"])

    def test_dead_helper_does_not_send_even_with_recent_last_good(self):
        response = self.prep()
        self.assertTrue(response["ready"])
        helper = self.p._nvml._process
        helper.kill()
        helper.wait(timeout=1)
        self.assert_definitely_refused(response["lease"])

    def test_fresh_last_good_dispatches_while_recheck_in_progress(self):
        response = self.prep()
        self.assertTrue(response["ready"])
        hold = self.path / "hold"
        hold.touch()
        results = Queue()
        def sample():
            try:
                results.put(self.p._nvml.sample())
            except Exception as exc:
                results.put(exc)
        thread = threading.Thread(target=sample, daemon=True)
        thread.start()
        try:
            until = time.monotonic() + 1
            while not (self.path / "inside").exists() and time.monotonic() < until:
                time.sleep(.005)
            self.assertTrue((self.path / "inside").exists())
            accepted = self.p.submit(response["lease"], self.payload())
            self.assertEqual(self.posts, 1)
        finally:
            hold.unlink()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results.get_nowait(), (32768, 30000))
        helper = self.p._nvml._process
        helper.kill()
        helper.wait(timeout=1)
        self.assertEqual(self.p.submit(response["lease"], self.payload()), accepted)
        self.assertEqual(self.posts, 1)

    def test_low_latest_state_still_blocks_dispatch_before_drive_fault(self):
        response = self.prep()
        self.assertTrue(response["ready"])
        (self.path / "low").touch()
        self.assertEqual(self.p._nvml.sample()[1], 1023)
        self.assertFalse(self.p.fault)
        self.assert_definitely_refused(response["lease"])

    def test_expired_dispatch_is_409_on_wire_and_definite_at_actual_client(self):
        response = self.prep()
        self.assertTrue(response["ready"])
        captured = []

        def opener(request, timeout):
            headers = Message()
            headers["Content-Length"] = str(len(request.data))
            headers[poolmod.HEADER] = request.headers[poolmod.HEADER.capitalize()]
            handler = object.__new__(poolmod._Handler)
            handler.command = request.method
            handler.path = request.selector
            handler.headers = headers
            handler.rfile = BytesIO(request.data)
            handler.server = SimpleNamespace(pool=self.p)
            handler._reply = lambda status, body: captured.append((status, poolmod._json(body)))
            handler.do_POST()
            status, raw = captured[-1]
            raise HTTPError(request.full_url, status, "", None, BytesIO(raw))

        client = ComfyUIClient("http://127.0.0.1:8188", opener=opener)
        client._execution_headers = {poolmod.HEADER: response["lease"]}
        original = gpu.time.monotonic
        with patch.object(gpu, "time", SimpleNamespace(monotonic=lambda: original() + 5)):
            with self.assertRaises(MediaSubmissionRefused) as error:
                client.request_json("POST", "/prompt", self.payload())
        self.assertEqual((captured[0][0], error.exception.code, self.posts), (409, "worker_budget_unknown", 0))

    def test_quarantined_retired_exact_lease_refuses_but_unknown_cannot(self):
        response = self.prep()
        self.assertTrue(response["ready"])
        row, = self.p._rows()
        self.p.fault = "gpu_telemetry_unavailable"
        self.p.children.clear()
        self.p._finish(row, "failed")
        with self.assertRaises(runtime.WorkerRuntimeRefused) as error:
            self.p.submit(response["lease"], self.payload())
        self.assertEqual(_worker_refusal(poolmod._json(error.exception.response()), self.payload()),
                         "worker_budget_unknown")
        with self.assertRaises(MediaError):
            self.p.submit("unknown", self.payload())
        other = {**self.payload(), "client_id": "anvil-unrelated"}
        with self.assertRaises(MediaError):
            self.p.submit(response["lease"], other)
        self.assertEqual(self.posts, 0)

    def sample_during_pending_intent(self, *, provider_times_out, rollback_fails=False):
        response = self.prep()
        self.assertTrue(response["ready"])
        write_started = threading.Event()
        release_write = threading.Event()
        original = self.p.db

        def execute(query, args=()):
            if query.startswith("UPDATE prompts SET phase='submitting',digest="):
                write_started.set()
                if not release_write.wait(3):
                    raise AssertionError("fixture write was not released")
            if rollback_fails and query.startswith("UPDATE prompts SET phase='prepared',digest="):
                raise OSError("fixture rollback durability failure")
            return original.execute(query, args)

        self.p.db = SimpleNamespace(execute=execute)
        result = Queue()

        def run(call, queue):
            try:
                queue.put(call())
            except Exception as exc:
                queue.put(exc)

        submit = threading.Thread(target=run, args=(
            lambda: self.p.submit(response["lease"], self.payload()), result), daemon=True)
        sample = None
        submit.start()
        try:
            self.assertTrue(write_started.wait(1))
            if provider_times_out:
                (self.path / "hold").touch()
            else:
                (self.path / "low").touch()
            updates = Queue()
            self.p._nvml._timeout = .15
            sample = threading.Thread(target=run, args=(self.p._nvml.sample, updates), daemon=True)
            sample.start()
            sample.join(.8)
            self.assertFalse(sample.is_alive(), "write stall blocked provider publication/discard")
            reading = updates.get_nowait()
            if provider_times_out:
                self.assertIsInstance(reading, MediaError)
                self.assertEqual(reading.code, "gpu_telemetry_unavailable")
            else:
                self.assertEqual(reading, (32768, 1023))
        finally:
            release_write.set()
            submit.join(2)
            if sample is not None:
                sample.join(2)
        self.assertFalse(submit.is_alive())
        self.assertEqual(self.posts, 0)
        return response, result.get_nowait()

    def test_low_publishes_during_stalled_intent_and_blocks_native_dispatch(self):
        _, error = self.sample_during_pending_intent(provider_times_out=False)
        self.assertIsInstance(error, runtime.WorkerRuntimeRefused)
        row, = self.p._rows()
        self.assertEqual((row["phase"], row["digest"]), ("prepared", ""))

    def test_timeout_discards_during_stalled_intent_and_blocks_dispatch(self):
        _, error = self.sample_during_pending_intent(provider_times_out=True)
        self.assertIsInstance(error, runtime.WorkerRuntimeRefused)
        row, = self.p._rows()
        self.assertEqual((row["phase"], row["digest"]), ("prepared", ""))

    def test_unproved_intent_rollback_cannot_return_definite_no_enqueue(self):
        response, error = self.sample_during_pending_intent(provider_times_out=False, rollback_fails=True)
        self.assertIsInstance(error, OSError)
        with self.assertRaisesRegex(MediaError, "submission outcome is uncertain"):
            self.p.submit(response["lease"], self.payload())
        row, = self.p._rows()
        self.assertEqual((row["phase"], self.posts), ("submitting", 0))
        self.assertTrue(row["digest"])

    def test_lost_native_response_cannot_replay_even_if_provider_is_dead(self):
        response = self.prep()
        self.assertTrue(response["ready"])
        client = self.p.children[0]["client"]
        original = client.request_json
        def lost_after_enqueue(method, path, *args, **kwargs):
            result = original(method, path, *args, **kwargs)
            if (method, path) == ("POST", "/prompt"):
                raise MediaError("backend_unavailable", "fixture response lost after enqueue")
            return result
        client.request_json = lost_after_enqueue
        with self.assertRaisesRegex(MediaError, "fixture response lost"):
            self.p.submit(response["lease"], self.payload())
        helper = self.p._nvml._process
        helper.kill()
        helper.wait(timeout=1)
        with self.assertRaisesRegex(MediaError, "submission outcome is uncertain"):
            self.p.submit(response["lease"], self.payload())
        row, = self.p._rows()
        self.assertEqual((row["phase"], self.posts), ("submitting", 1))
        self.assertTrue(row["digest"])

    def test_default_path_keeps_single_stock_prepare_sample_and_existing_submit(self):
        # Simulate the untouched default provider without invoking NVIDIA.
        self.p._nvml.close()
        self.p._nvml = None
        calls = []
        with patch.object(poolmod, "gpu_memory", side_effect=lambda: calls.append("stock") or (32768, 30000)):
            reservation = self.prep()
            self.assertTrue(reservation["ready"])
            self.assertEqual(calls, ["stock"])
            accepted = self.p.submit(reservation["lease"], self.payload())
            self.assertTrue(accepted["prompt_id"])
            self.assertEqual(calls, ["stock"])
            self.assertEqual(self.posts, 1)

    def test_plain_io_turn_defers_prepare_without_provider_discard(self):
        def busy(*, timeout):
            return False
        previous = self.p._nvml._process
        with patch.object(self.p._nvml, "_lock", SimpleNamespace(acquire=busy)):
            self.assertEqual(self.prep(), {"ready": False})
        self.assertIs(self.p._nvml._process, previous)
        self.assertTrue(self.prep()["ready"])

    def test_healthy_publication_after_validate_defers_preparation_without_terminal_error(self):
        original = self.p._nvml.validate
        once = True

        @contextmanager
        def advance_after_validation(epoch):
            nonlocal once
            with original(epoch):
                if once:
                    once = False
                    self.assertEqual(self.p._nvml.sample(), (32768, 30000))
                yield

        with patch.object(self.p._nvml, "validate", advance_after_validation):
            self.assertEqual(self.prep(), {"ready": False})
        row, = self.p._rows()
        self.assertEqual((row["phase"], row["incarnation"]), ("waiting", ""))
        self.assertTrue(self.prep()["ready"])

    def start_planned_switch(self):
        with self.p.lock:
            self.p.switching = True
            self.p._mode_generation = getattr(self.p, "_mode_generation", 0) + 1

    def test_proven_completed_mode_switch_defers_the_fifo_head(self):
        sampler = self.p._nvml
        original = sampler.sample_with_revision

        def race_switch():
            self.start_planned_switch()
            old = sampler._process
            try:
                with sampler.contain():
                    return original()
            finally:
                old.wait(timeout=1)
                sampler.start()
                with self.p.lock:
                    self.p.switching = False

        with patch.object(sampler, "sample_with_revision", race_switch):
            self.assertEqual(self.prep(), {"ready": False})
        self.assertTrue(self.prep()["ready"])

    def test_real_timeout_is_not_reclassified_during_planned_switch(self):
        sampler = self.p._nvml
        original = sampler.sample_with_revision
        sampler._timeout = .15
        (self.path / "hold").touch()

        def race_switch():
            self.start_planned_switch()
            return original()

        with patch.object(sampler, "sample_with_revision", race_switch):
            with self.assertRaises(MediaError) as error:
                self.prep()
        self.assertEqual(error.exception.code, "gpu_telemetry_unavailable")

    def test_read_timeout_during_planned_containment_quarantines_pool_run(self):
        sampler = self.p._nvml
        self.p.mode = "images"
        self.assertFalse(self.prep()["ready"])  # Queue a switch to exclusive mode.
        helper = sampler._process
        request_read_started = threading.Event()
        request_errors = Queue()
        request_thread = None
        started_modes = []
        contained_epochs = []
        healthy_epoch = sampler.epoch

        def start_children(mode):
            if started_modes:
                sampler.start()  # The actual restart attempted by drive().
            started_modes.append(mode)

        def read_request():
            try:
                self.prep()
            except Exception as error:
                request_errors.put(error)

        def refresh_after_healthy_sample():
            nonlocal request_thread, healthy_epoch
            self.assertGreater(sampler.epoch, healthy_epoch)
            healthy_epoch = sampler.epoch
            sampler._timeout = .2
            (self.path / "hold").touch()
            original_socket = sampler._socket

            def recv(size):
                request_read_started.set()
                self.assertTrue(sampler._frozen.wait(2), "planned containment did not begin")
                return original_socket.recv(size)  # Real read timeout while contain() waits.

            sampler._socket = SimpleNamespace(
                settimeout=original_socket.settimeout, sendall=original_socket.sendall,
                recv=recv, close=original_socket.close)
            request_thread = threading.Thread(target=read_request, daemon=True)
            request_thread.start()
            self.assertTrue(request_read_started.wait(2))

        def reap_helper():
            self.assertTrue(sampler._evidence.contained)
            contained_epochs.append(sampler.epoch)
            helper.wait(timeout=1)
            self.p.children.clear()

        with patch.object(self.p, "_start_children", start_children), \
                patch.object(self.p, "refresh", refresh_after_healthy_sample), \
                patch.object(self.p, "_stop_render_children", reap_helper), \
                patch.object(self.p.stop, "wait", side_effect=[False, True]):
            try:
                self.p.drive()
            finally:
                if request_thread is not None:
                    request_thread.join(3)

        self.assertIsNotNone(request_thread)
        self.assertFalse(request_thread.is_alive())
        self.assertEqual(request_errors.get_nowait().code, "gpu_telemetry_unavailable")
        self.assertEqual(contained_epochs[0], healthy_epoch + 2)  # Failure, then containment.
        self.assertIsNotNone(helper.returncode)
        self.assertEqual(started_modes, ["images"], "provider failure must refuse the same-run restart")
        self.assertEqual(self.p.fault, "gpu_telemetry_unavailable")
        self.assertEqual(self.posts, 0)

    def test_mode_switch_containment_does_not_mask_sticky_low_reserve(self):
        sampler = self.p._nvml
        original = sampler.sample_with_revision
        (self.path / "low").touch()
        self.assertEqual(sampler.sample(), (32768, 1023))

        def race_switch():
            self.start_planned_switch()
            with sampler.contain():
                return original()

        with patch.object(sampler, "sample_with_revision", race_switch):
            with self.assertRaisesRegex(MediaError, "physical GPU reserve was breached"):
                self.prep()
        self.assertEqual(self.posts, 0)

    def test_frozen_helper_does_not_mask_quarantine(self):
        sampler = self.p._nvml
        original = sampler.sample_with_revision

        def race_fault():
            self.start_planned_switch()
            with self.p.lock:
                self.p.fault = "gpu_telemetry_unavailable"
            with sampler.contain():
                return original()

        with patch.object(sampler, "sample_with_revision", race_fault):
            with self.assertRaises(MediaError):
                self.prep()

    def test_new_read_during_preparation_write_restores_fifo_without_leaking_lease(self):
        original = self.p.db
        def execute(query, args=()):
            result = original.execute(query, args)
            if query.startswith("UPDATE prompts SET phase='prepared',incarnation="):
                self.p._nvml.sample()
            return result
        self.p.db = SimpleNamespace(execute=execute)
        self.assertEqual(self.prep(), {"ready": False})
        row, = self.p._rows()
        self.assertEqual((row["phase"], row["incarnation"]), ("waiting", ""))
        self.p.db = original
        self.assertTrue(self.prep()["ready"])

    def test_new_high_reading_defers_prepare_without_terminal_http_error(self):
        before = self.p._sample_for_request
        def superseded(**kwargs):
            result = before(**kwargs)
            self.p._nvml.sample()
            return result
        with patch.object(self.p, "_sample_for_request", superseded):
            self.assertEqual(self.prep(), {"ready": False})
        row, = self.p._rows()
        self.assertEqual(row["phase"], "waiting")
        self.assertTrue(self.prep()["ready"])


if __name__ == "__main__":
    unittest.main()
