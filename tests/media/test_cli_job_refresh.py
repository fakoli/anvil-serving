"""Standalone CLI jobs can retain their result without a gateway daemon."""

import json

from anvil_serving.media.artifacts import ArtifactStore
from anvil_serving.media.cli import main
from anvil_serving.media.jobs import MediaJobStore
from anvil_serving.media.operations import MediaOperations
from tests.media.test_cli import _Backend, _Registry


def _setup(tmp_path, monkeypatch):
    operations = MediaOperations(_Registry(), MediaJobStore(tmp_path / "jobs.sqlite3"),
                                 ArtifactStore(tmp_path / "artifacts"))
    backend = _Backend()
    backend.base_url = "http://127.0.0.1:8188"
    job = operations.workflow_run("image.test", "v1", {"prompt": "mountain"},
                                  principal="owner", idempotency_key="one", backend=backend)["job"]
    monkeypatch.setattr("anvil_serving.media.cli._operations", lambda _args: operations)
    return operations, backend, job["id"]


def test_other_principal_cannot_refresh_or_contact_backend(tmp_path, monkeypatch, capsys):
    _operations, _backend, job_id = _setup(tmp_path, monkeypatch)

    def forbidden(_url):
        raise AssertionError("backend constructed before ownership check")

    monkeypatch.setattr("anvil_serving.media.cli.ComfyUIClient", forbidden)
    assert main(["job", "status", job_id, "--principal", "other", "--backend-url", "http://127.0.0.1:8188"]) == 1
    assert json.loads(capsys.readouterr().err)["code"] == "job_not_found"
