"""Runtime admission is fresh; only proven no-enqueue outcomes are terminal."""
import dataclasses
import io
import json
import urllib.error
from urllib.parse import urlsplit

import pytest

from anvil_serving.media.artifacts import ArtifactStore
from anvil_serving.media.comfyui import ComfyUIClient
from anvil_serving.media.contracts import JobState
from anvil_serving.media.errors import MediaError, MediaSubmissionRefused
from anvil_serving.media.jobs import MediaJobStore
from anvil_serving.media.operations import MediaOperations
from anvil_serving.media.worker_runtime import FEATURE, REFUSAL_SCHEMA, SCHEMA
from test_cli import _Registry
from test_comfyui_adapter import Response


RUN_ID = "a" * 32


def runtime(**changes):
    return {"schema": SCHEMA, "runId": RUN_ID, "totalSeconds": 7200,
            "reservationSeconds": 900, "marginSeconds": 30, "remainingSeconds": 7000,
            "acceptingNewWork": True, "reason": "ready", **changes}


def stats(value):
    return {"system": {"anvil_runtime": value}}


def registry(*, required=True):
    result = _Registry()
    result.descriptor = dataclasses.replace(
        result.descriptor, required_features=(FEATURE,) if required else (), timeout_seconds=600
    )
    return result


class Harness:
    def __init__(self, status=None, *, refusal=None):
        self.status = stats(runtime()) if status is None else status
        self.refusal = refusal
        self.calls = []
        self.posts = []
        self.client = ComfyUIClient("http://127.0.0.1:8188", opener=self.open)

    def open(self, request, timeout):
        path = urlsplit(request.full_url).path
        self.calls.append((request.method, path))
        if path == "/system_stats":
            if isinstance(self.status, Exception):
                raise self.status
            return Response(self.status)
        if path == "/features":
            return Response({FEATURE: True})
        if path.startswith("/models/"):
            return Response([])
        if path == "/prompt" and request.method == "POST":
            payload = json.loads(request.data)
            self.posts.append(payload)
            if self.refusal is not None:
                raise self.refusal(payload)
            return Response({"prompt_id": "prompt_one"})
        if path == "/queue":
            return Response({"queue_running": [], "queue_pending": [
                [0, "prompt_one", {}, {"client_id": self.posts[0]["client_id"]}, []]
            ] if self.posts else []})
        raise AssertionError((request.method, path))


def operations(tmp_path, configured):
    return MediaOperations(configured, MediaJobStore(tmp_path / "jobs.sqlite3"),
                           ArtifactStore(tmp_path / "artifacts"))


def run(operations, client):
    return operations.workflow_run("image.test", "v1", {"prompt": "example"},
                                   principal="owner", idempotency_key="request", backend=client)


@pytest.mark.parametrize("value,reason", [
    (runtime(remainingSeconds=930), "worker_budget_insufficient"),
    (runtime(remainingSeconds=929, acceptingNewWork=False, reason="worker_budget_insufficient"), "worker_budget_insufficient"),
    (runtime(acceptingNewWork=False, reason="worker_busy"), "worker_busy"),
    (runtime(reservationSeconds=599), "worker_reservation_invalid"),
])
def test_reservation_limits_are_checked_before_job_creation(tmp_path, value, reason):
    configured = registry()
    harness = Harness(stats(value))
    assert harness.client.compatibility(configured.descriptor).reasons == (reason,)
    service = operations(tmp_path, configured)
    with pytest.raises(MediaError):
        run(service, harness.client)
    assert service.jobs.nonterminal() == [] and not harness.posts


def test_early_pass_is_rechecked_after_staging_and_late_refusal_is_terminal(tmp_path, monkeypatch):
    harness = Harness()
    service = operations(tmp_path, registry())
    staged = []

    def stage(rendered, **_kwargs):
        staged.append(True)
        harness.status = stats(runtime(acceptingNewWork=False, reason="worker_busy"))
        return rendered

    monkeypatch.setattr(service, "_stage_artifact_inputs", stage)
    with pytest.raises(MediaSubmissionRefused) as error:
        run(service, harness.client)
    assert error.value.code == "worker_busy"
    assert staged == [True] and not harness.posts
    result = run(service, harness.client)
    failed = service.jobs.get(result["job"]["id"], principal="owner")
    assert failed.state == JobState.FAILED and failed.events[-1].reason == "worker_busy"
    assert result["job"]["state"] == "failed"
    assert harness.calls.count(("GET", "/system_stats")) == 2


def refusal(payload, **changes):
    return {"schema": REFUSAL_SCHEMA, "accepted": False, "reason": "worker_budget_insufficient",
            "requestRunId": payload["extra_data"]["anvil_runtime"]["runId"],
            "clientId": payload["client_id"], **changes}


def http_error(body, *, status=409):
    return urllib.error.HTTPError("http://127.0.0.1:8188/prompt", status, "refused", {},
                                  io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode()))


@pytest.mark.parametrize("reason", [
    "worker_budget_insufficient", "worker_busy", "worker_budget_unknown",
    "worker_instance_changed", "worker_reservation_invalid",
])
def test_correlated_native_refusal_is_failed_without_resubmission(tmp_path, reason):
    harness = Harness(refusal=lambda payload: http_error(refusal(payload, reason=reason)))
    service = operations(tmp_path, registry())
    with pytest.raises(MediaSubmissionRefused) as error:
        run(service, harness.client)
    assert error.value.code == reason
    assert service.jobs.nonterminal() == []
    result = run(service, harness.client)
    assert result["job"]["state"] == "failed" and result["job"]["terminalReason"] == reason
    assert len(harness.posts) == 1


@pytest.mark.parametrize("change", [
    {"schema": "other"}, {"accepted": True}, {"accepted": 0}, {"reason": "other"},
    {"reason": []}, {"requestRunId": "b" * 32}, {"clientId": "anvil-other"}, {"extra": 1},
    "malformed", "nested", "oversize", "duplicate", "wrong_status", "timeout",
])
def test_uncorrelated_or_ambiguous_post_outcomes_remain_submitting_and_recover(tmp_path, change):
    def fail(payload):
        if change == "timeout":
            return urllib.error.URLError("timeout")
        body = refusal(payload, **change) if isinstance(change, dict) else refusal(payload)
        if change == "malformed":
            body = b"{"
        elif change == "nested":
            body = b"[" * 1050 + b"0" + b"]" * 1050
        elif change == "oversize":
            body = json.dumps(body).encode() + b" " * 4096
        elif change == "duplicate":
            body = json.dumps(body).replace('"accepted": false', '"accepted": true, "accepted": false').encode()
        return http_error(body, status=500 if change == "wrong_status" else 409)

    harness = Harness(refusal=fail)
    service = operations(tmp_path, registry())
    with pytest.raises(MediaError) as error:
        run(service, harness.client)
    assert not isinstance(error.value, MediaSubmissionRefused)
    assert service.jobs.nonterminal()[0].state == JobState.SUBMITTING
    harness.status = stats(runtime(remainingSeconds=0, acceptingNewWork=False, reason="worker_budget_insufficient"))
    recovered = run(service, harness.client)
    assert recovered["job"]["state"] == "queued"
    assert len(harness.posts) == 1
