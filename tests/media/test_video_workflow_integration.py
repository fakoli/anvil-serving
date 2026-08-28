from __future__ import annotations

import json
import subprocess

from anvil_serving.media.artifacts import ArtifactStore
from anvil_serving.media.backends import BackendOutput, BackendStatus
from anvil_serving.media.comfyui import WorkflowCompatibility
from anvil_serving.media.jobs import MediaJobStore
from anvil_serving.media.qualification import qualify
from anvil_serving.media.workflows import WorkflowRegistry, canonical_digest


MP4 = b"\x00\x00\x00\x18ftypmp42" + b"bounded-video"


def _registry(tmp_path):
    graph = {"1": {"class_type": "Test", "inputs": {"prompt": ""}}}
    (tmp_path / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    descriptor = {
        "schema": "anvil-serving.media-workflow/v1",
        "id": "video.test",
        "version": "v1",
        "kind": "video",
        "service_target": "media-worker",
        "graph": "graph.json",
        "graph_digest": canonical_digest(graph),
        "parameters": {"prompt": {"kind": "string", "max_length": 100}},
        "bindings": [{"parameter": "prompt", "node": "1", "input": "prompt"}],
        "output_nodes": ["1"],
        "output_mime_types": ["video/mp4"],
        "required_features": ["test"],
        "required_nodes": ["Test"],
        "required_models": ["test.safetensors"],
        "available": False,
        "unavailable_reasons": ["quality_unreviewed"],
        "limits": {
            "request_bytes": 1024,
            "artifact_bytes": 1024,
            "timeout_seconds": 30,
            "retention_seconds": 60,
            "queue_depth": 2,
            "concurrency": 1,
        },
    }
    (tmp_path / "descriptor.json").write_text(json.dumps(descriptor), encoding="utf-8")
    (tmp_path / "registry.json").write_text(
        json.dumps({"schema": "anvil-serving.media-workflow-registry/v1", "workflows": ["descriptor.json"]}),
        encoding="utf-8",
    )
    lock = {
        "runtime": {"comfyui_release": "v-test"},
        "workflows": [{
            "id": "video.test",
            "version": "v1",
            "models": [{
                "repository": "org/repo", "revision": "a" * 40,
                "path": "test.safetensors", "size": 7, "sha256": "b" * 64,
            }],
        }],
    }
    lock_path = tmp_path / "bundle.lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    return WorkflowRegistry(tmp_path / "registry.json"), lock_path


def _gpu_runner(argv, **_kwargs):
    return subprocess.CompletedProcess(argv, 0, "0, 2048, 32607\n", "")


class _VideoBackend:
    def compatibility(self, workflow, *, qualification=False):
        assert qualification is True
        return WorkflowCompatibility(workflow.id, workflow.version, True, True)

    def queue(self):
        return {"running": 1, "pending": 0}

    def submit(self, workflow, *, job_id):
        assert workflow.descriptor.available is True
        return "prompt_video"

    def history(self, prompt_id):
        return BackendStatus(prompt_id, "completed", outputs=(BackendOutput("1", "private.mp4"),))

    def fetch_output(self, output, *, max_bytes):
        assert len(MP4) <= max_bytes
        return MP4


def _ffprobe(argv, **_kwargs):
    payload = {
        "streams": [{
            "codec_name": "h264",
            "width": 832,
            "height": 480,
            "avg_frame_rate": "16/1",
            "nb_read_frames": "17",
        }],
        "format": {"duration": "1.0625"},
    }
    return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")


def test_managed_video_qualification_returns_immediately_and_records_stream_metadata(
    tmp_path, monkeypatch
):
    registry, lock_path = _registry(tmp_path)
    monkeypatch.setattr(
        "anvil_serving.media.qualification.bundle_inventory",
        lambda *_args, **_kwargs: {"ready": True, "assets": [{"state": "exact"}]},
    )
    ticks = iter([0.0, 0.02, 8.0])
    result = qualify(
        "video.test",
        "v1",
        {"prompt": "test prompt"},
        registry=registry,
        jobs=MediaJobStore(tmp_path / "jobs.sqlite3"),
        artifacts=ArtifactStore(tmp_path / "artifacts"),
        backend=_VideoBackend(),
        principal="qualifier",
        lock_path=lock_path,
        models_volume="media-models",
        monotonic=lambda: next(ticks),
        sleep=lambda _seconds: None,
        gpu_runner=_gpu_runner,
        ffprobe_runner=_ffprobe,
    )
    assert result["job"]["immediateReturnSeconds"] == 0.02
    assert result["artifacts"][0]["resource"].startswith("/artifacts/")
    assert "data" not in result["artifacts"][0]
    assert result["decoding"][0]["codec"] == "h264"
    assert result["decoding"][0]["frames"] == 17
    assert result["decoding"][0]["frameRate"] == 16.0
    assert result["decoding"][0]["durationSeconds"] == 1.0625
    assert result["capacity"]["maxQueueRunning"] == 1
