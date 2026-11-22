"""CPU integration of media dispatcher, transport, worker pool, and both stores."""
from contextlib import contextmanager
from email.message import Message
from io import BytesIO
import json
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from anvil_serving.media import worker_pool as poolmod
from anvil_serving.media import worker_runtime as runtime
from anvil_serving.media.artifacts import ArtifactStore
from anvil_serving.media.comfyui import ComfyUIClient
from anvil_serving.media.contracts import (
    JobState, ParameterBinding, ParameterSpec, RenderedWorkflow, WorkflowDescriptor,
)
from anvil_serving.media.errors import MediaError, MediaSubmissionRefused
from anvil_serving.media.jobs import MediaJobStore
from anvil_serving.media.operations import MediaOperations
from anvil_serving.media.workflows import canonical_digest

from tests.media import test_gpu_admission as pool_fixture


class SyntheticRegistry:
    descriptor = WorkflowDescriptor(
        id="video.a", version="v1", kind="video", service_target="media-worker",
        graph_digest=canonical_digest({"1": {"class_type": "Test", "inputs": {"prompt": ""}}}),
        parameters={"prompt": ParameterSpec("string", max_length=100)},
        bindings=(ParameterBinding("prompt", "1", "prompt"),),
        output_nodes=("1",), output_mime_types=("video/mp4",),
        available=True, unavailable_reasons=(),
    )

    def list(self):
        return [self.descriptor.as_public_dict()]

    def get(self, workflow_id, version):
        if (workflow_id, version) != (self.descriptor.id, self.descriptor.version):
            raise AssertionError("unexpected fixture workflow")
        return self.descriptor

    def render(self, workflow_id, version, parameters):
        descriptor = self.get(workflow_id, version)
        validated = descriptor.validate_parameters(parameters)
        graph = {"1": {"class_type": "Test", "inputs": {"prompt": validated["prompt"]}}}
        return RenderedWorkflow(descriptor, graph, canonical_digest(validated))


class InMemoryWorkerHTTP:
    """Dispatch a real urllib Request through the actual handler with no listening socket."""

    def __init__(self, pool):
        self.pool = pool
        self.post_mode = "normal"
        self.records = []

    def __call__(self, request, *, timeout):
        headers = Message()
        for k, v in request.header_items():
            headers[k] = v
        raw = request.data or b""
        if raw:
            headers["Content-Length"] = str(len(raw))
        replies = []
        handler = object.__new__(poolmod._Handler)
        handler.command = request.method
        handler.path = request.selector
        handler.headers = headers
        handler.rfile = BytesIO(raw)
        handler.server = SimpleNamespace(pool=self.pool)
        handler._reply = lambda status, body: replies.append((status, poolmod._json(body)))
        is_submit = request.selector == "/prompt" and request.method == "POST"
        if is_submit and self.post_mode == "kill_helper":
            helper = self.pool._nvml._process
            helper.kill()
            helper.wait(timeout=1)
        handler.do_POST()
        (status, data), = replies
        self.records.append((request.method, request.selector, status, json.loads(data)))
        if is_submit and self.post_mode == "drop_response":
            raise URLError("fixture dropped response after pool reply")
        if status >= 400:
            raise HTTPError(request.full_url, status, "", headers, BytesIO(data))
        return BytesIO(data)

    def by_path(self, path):
        return [record for record in self.records if record[1] == path]


@unittest.skipUnless(sys.platform == "linux", "GPU worker pool requires Linux")
class TestGpuDispatcherIntegration(unittest.TestCase):
    principal = "fixture-owner"

    def setUp(self):
        # Reuse the maintained CPU fixture's real socket helper and temp pool;
        # do not inherit its test methods.
        fixture = pool_fixture.TestGpuAdmission()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.pool = fixture.p
        self.child_posts = []
        self.pool.children[0]["client"].request_json = self.child
        self.pool.metadata["/system_stats"] = self.child("GET", "/system_stats")
        self.http = InMemoryWorkerHTTP(self.pool)
        self.backend = ComfyUIClient("http://127.0.0.1:8188", opener=self.http)
        env = patch.dict(os.environ, {"ANVIL_MEDIA_EXECUTION_SLOTS": "1", "ANVIL_MEDIA_QUEUE_DEPTH": "8"})
        env.start()
        self.addCleanup(env.stop)
        self.ops = MediaOperations(
            SyntheticRegistry(), MediaJobStore(fixture.path / "gateway.sqlite3"),
            ArtifactStore(fixture.path / "artifacts"), background_dispatch=True,
        )

    def child(self, method, path, payload=None, **kwargs):
        if (method, path) == ("GET", "/system_stats"):
            return {"system": {"anvil_runtime": runtime.runtime_status()}}
        if (method, path) == ("GET", "/features"):
            return {poolmod.FEATURE: True}
        if method == "GET" and path.startswith("/models/"):
            return []
        if (method, path) == ("POST", "/prompt"):
            self.child_posts.append(payload["client_id"])
            return {"prompt_id": "fixture-native"}
        self.fail(f"unexpected native route: {method} {path}")

    def run_job(self, key):
        return self.ops.workflow_run("video.a", "v1", {"prompt": "fixture"},
                                     principal=self.principal, idempotency_key=key,
                                     backend=self.backend)

    def stored(self, job_id):
        return self.ops.jobs.get(job_id, principal=self.principal)

    def test_healthy_publication_defers_and_retries_original_fifo_head(self):
        first = self.run_job("fixture-first")["job"]["id"]
        second = self.run_job("fixture-second")["job"]["id"]
        before = self.pool._nvml.validate
        once = True

        @contextmanager
        def advance_after_validate(epoch):
            nonlocal once
            with before(epoch):
                if once:
                    once = False
                    self.pool._nvml.sample()
                yield

        with patch.object(self.pool._nvml, "validate", advance_after_validate):
            self.ops.dispatch_queue(backend=self.backend, requested_job=first)
        self.assertEqual(self.stored(first).state, JobState.ACCEPTED)
        self.assertEqual(self.stored(second).state, JobState.ACCEPTED)
        self.assertEqual(self.ops.jobs.queue_info(first)["position"], 1)
        self.assertEqual(self.ops.jobs.queue_info(second)["position"], 2)
        self.assertEqual([r[2:] for r in self.http.by_path("/anvil/prepare")], [(200, {"ready": False})])
        self.assertEqual(self.child_posts, [])
        self.ops.dispatch_queue(backend=self.backend, requested_job=first)
        self.assertEqual(self.stored(first).state, JobState.QUEUED)
        self.assertEqual(self.stored(second).state, JobState.ACCEPTED)
        self.assertEqual(self.child_posts, ["anvil-" + first])

    def test_exact_pre_enqueue_409_is_failed_not_ambiguous(self):
        job = self.run_job("fixture-refused")["job"]["id"]
        self.http.post_mode = "kill_helper"
        with self.assertRaises(MediaSubmissionRefused) as error:
            self.ops.dispatch_queue(backend=self.backend, requested_job=job)
        self.assertEqual(error.exception.code, "worker_budget_unknown")
        failed = self.stored(job)
        self.assertEqual((failed.state, failed.events[-1].reason), (JobState.FAILED, "worker_budget_unknown"))
        self.assertEqual(failed.backend_prompt_id, "")
        (record,) = self.http.by_path("/prompt")
        self.assertEqual((record[2], record[3]["accepted"], record[3]["clientId"]), (409, False, "anvil-" + job))
        (row,) = self.pool._rows()
        self.assertEqual((row["phase"], row["digest"], self.child_posts), ("prepared", "", []))

    def test_lost_post_stays_submitting_then_recovers_without_replay(self):
        job = self.run_job("fixture-lost")["job"]["id"]
        self.http.post_mode = "drop_response"
        with self.assertRaises(MediaError) as error:
            self.ops.dispatch_queue(backend=self.backend, requested_job=job)
        self.assertEqual(error.exception.code, "backend_unavailable")
        self.assertEqual(self.stored(job).state, JobState.SUBMITTING)
        self.assertEqual(self.stored(job).backend_prompt_id, "")
        self.assertEqual(len(self.http.by_path("/prompt")), 1)
        self.ops.dispatch_queue(backend=self.backend, requested_job=job)
        self.assertEqual(len(self.http.by_path("/prompt")), 1)
        self.ops.background_dispatch = False
        same = self.run_job("fixture-lost")
        self.assertFalse(same["created"])
        self.assertEqual(same["job"]["id"], job)
        recovered = self.stored(job)
        self.assertEqual((recovered.state, recovered.events[-1].reason),
                         (JobState.QUEUED, "backend_submission_recovered"))
        self.assertTrue(recovered.backend_prompt_id)
        self.assertEqual(self.child_posts, ["anvil-" + job])
        self.assertEqual(len(self.http.by_path("/prompt")), 1)
        self.assertEqual(len(self.http.by_path("/queue")), 1)


if __name__ == "__main__":
    unittest.main()
