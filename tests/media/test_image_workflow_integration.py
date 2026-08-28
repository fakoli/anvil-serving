from __future__ import annotations

import json
import subprocess

import pytest

from anvil_serving.media.artifacts import ArtifactStore
from anvil_serving.media.backends import BackendOutput, BackendStatus
from anvil_serving.media.comfyui import WorkflowCompatibility
from anvil_serving.media.errors import MediaError
from anvil_serving.media.jobs import MediaJobStore
from anvil_serving.media.qualification import qualify
from anvil_serving.media.workflows import WorkflowRegistry, canonical_digest


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (512).to_bytes(4, "big") + (384).to_bytes(4, "big") + b"payload"


def _registry(
    tmp_path,
    *,
    kind="image",
    mime="image/png",
    profiled=False,
    profile_dimensions=(512, 384),
):
    graph_inputs = {"prompt": ""}
    parameters = {"prompt": {"kind": "string", "max_length": 100}}
    bindings = [{"parameter": "prompt", "node": "1", "input": "prompt"}]
    profiles = {}
    if profiled:
        profile_width, profile_height = profile_dimensions
        graph_inputs.update({"width": 1, "height": 1})
        parameters.update(
            {
                "width": {"kind": "integer", "minimum": 1, "maximum": 1024},
                "height": {"kind": "integer", "minimum": 1, "maximum": 1024},
            }
        )
        bindings.extend(
            [
                {"parameter": "width", "node": "1", "input": "width"},
                {"parameter": "height", "node": "1", "input": "height"},
            ]
        )
        profiles = {
            "default_quality_profile": "high",
            "quality_profiles": {
                "high": {
                    "description": "Exact qualification dimensions.",
                    "parameters": {
                        "width": profile_width,
                        "height": profile_height,
                    },
                }
            },
        }
    graph = {"1": {"class_type": "Test", "inputs": graph_inputs}}
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(graph), encoding="utf-8")
    descriptor = {
        "schema": "anvil-serving.media-workflow/v1",
        "id": f"{kind}.test",
        "version": "v1",
        "kind": kind,
        "service_target": "media-worker",
        "graph": "graph.json",
        "graph_digest": canonical_digest(graph),
        "parameters": parameters,
        "bindings": bindings,
        "output_nodes": ["1"],
        "output_mime_types": [mime],
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
        **profiles,
    }
    (tmp_path / "descriptor.json").write_text(json.dumps(descriptor), encoding="utf-8")
    (tmp_path / "registry.json").write_text(
        json.dumps({"schema": "anvil-serving.media-workflow-registry/v1", "workflows": ["descriptor.json"]}),
        encoding="utf-8",
    )
    lock = {
        "runtime": {"comfyui_release": "v-test"},
        "workflows": [{
            "id": f"{kind}.test",
            "version": "v1",
            "models": [{
                "repository": "org/repo",
                "revision": "a" * 40,
                "path": "test.safetensors",
                "size": 7,
                "sha256": "b" * 64,
            }],
        }],
    }
    lock_path = tmp_path / "bundle.lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    return WorkflowRegistry(tmp_path / "registry.json"), lock_path


class _Backend:
    def __init__(self, expected_dimensions=None):
        self.expected_dimensions = expected_dimensions

    def compatibility(self, workflow, *, qualification=False):
        assert qualification is True
        return WorkflowCompatibility(workflow.id, workflow.version, True, True)

    def queue(self):
        return {"running": 0, "pending": 0}

    def submit(self, workflow, *, job_id):
        assert workflow.descriptor.available is True
        assert workflow.graph["1"]["inputs"]["prompt"] == "test prompt"
        if self.expected_dimensions is not None:
            width, height = self.expected_dimensions
            assert workflow.graph["1"]["inputs"]["width"] == width
            assert workflow.graph["1"]["inputs"]["height"] == height
        return "prompt_test"

    def history(self, prompt_id):
        return BackendStatus(prompt_id, "completed", outputs=(BackendOutput("1", "private.png"),))

    def fetch_output(self, output, *, max_bytes):
        assert output.filename == "private.png"
        assert len(PNG) <= max_bytes
        return PNG


def _gpu_runner(argv, **_kwargs):
    return subprocess.CompletedProcess(argv, 0, "0, 2048, 32607\n", "")


def test_managed_image_qualification_is_durable_decodable_and_not_promoted(tmp_path, monkeypatch):
    registry, lock_path = _registry(tmp_path, profiled=True)
    monkeypatch.setattr(
        "anvil_serving.media.qualification.bundle_inventory",
        lambda *_args, **_kwargs: {"ready": True, "assets": [{"state": "exact"}]},
    )
    ticks = iter([0.0, 0.01, 1.5])
    result = qualify(
        "image.test",
        "v1",
        {"prompt": "test prompt"},
        registry=registry,
        jobs=MediaJobStore(tmp_path / "jobs.sqlite3"),
        artifacts=ArtifactStore(tmp_path / "artifacts"),
        backend=_Backend(expected_dimensions=(512, 384)),
        principal="qualifier",
        quality_profile="high",
        lock_path=lock_path,
        models_volume="media-models",
        monotonic=lambda: next(ticks),
        sleep=lambda _seconds: None,
        gpu_runner=_gpu_runner,
    )
    assert result["passed"] is True
    assert result["promoted"] is False
    assert result["job"]["submittedState"] == "queued"
    assert result["job"]["finalState"] == "completed"
    assert result["workflow"]["qualityProfile"] == "high"
    assert result["workflow"]["qualityProfileSettings"] == {
        "width": 512,
        "height": 384,
    }
    assert result["decoding"][0] == {
        "artifactId": result["artifacts"][0]["id"],
        "decodable": True,
        "format": "png",
        "width": 512,
        "height": 384,
    }
    assert result["artifacts"][0]["sha256"]
    assert result["quality"]["status"] == "human_required"


def test_managed_image_qualification_rejects_profile_dimension_drift(
    tmp_path,
    monkeypatch,
):
    registry, lock_path = _registry(
        tmp_path,
        profiled=True,
        profile_dimensions=(1024, 1024),
    )
    monkeypatch.setattr(
        "anvil_serving.media.qualification.bundle_inventory",
        lambda *_args, **_kwargs: {
            "ready": True,
            "assets": [{"state": "exact"}],
        },
    )
    ticks = iter([0.0, 0.01, 1.5])
    with pytest.raises(MediaError) as error:
        qualify(
            "image.test",
            "v1",
            {"prompt": "test prompt"},
            registry=registry,
            jobs=MediaJobStore(tmp_path / "jobs.sqlite3"),
            artifacts=ArtifactStore(tmp_path / "artifacts"),
            backend=_Backend(expected_dimensions=(1024, 1024)),
            principal="qualifier",
            quality_profile="high",
            lock_path=lock_path,
            models_volume="media-models",
            monotonic=lambda: next(ticks),
            sleep=lambda _seconds: None,
            gpu_runner=_gpu_runner,
        )
    assert error.value.code == "media_qualification_output_dimensions"


__all__ = ["_Backend", "_gpu_runner", "_registry"]
