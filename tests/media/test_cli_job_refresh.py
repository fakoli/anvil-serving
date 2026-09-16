"""Standalone CLI jobs can retain their result without a gateway daemon."""

import json

from anvil_serving.media.artifacts import ArtifactStore
from anvil_serving.media.backends import BackendOutput, BackendStatus
from anvil_serving.media.cli import main
from anvil_serving.media.contracts import JobState
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


def test_refresh_captures_once_and_never_submits_again(tmp_path, monkeypatch, capsys):
    operations, backend, job_id = _setup(tmp_path, monkeypatch)
    calls = []

    def history(prompt_id):
        calls.append(prompt_id)
        return BackendStatus(prompt_id, "completed", outputs=(BackendOutput("1", "private.png"),))

    backend.history = history
    backend.fetch_output = lambda _output, **_kwargs: b"\x89PNG\r\n\x1a\nsample"
    monkeypatch.setattr("anvil_serving.media.cli.ComfyUIClient", lambda _url: backend)
    argv = ["job", "status", job_id, "--principal", "owner", "--backend-url", "http://127.0.0.1:8188"]
    assert main(argv) == 0
    result = json.loads(capsys.readouterr().out)["job"]
    assert result["state"] == "completed" and len(result["artifacts"]) == 1
    assert "private.png" not in json.dumps(result)
    assert main(argv) == 0
    repeated = json.loads(capsys.readouterr().out)["job"]
    assert repeated["artifacts"] == result["artifacts"]
    assert calls == ["prompt-one"] and backend.submissions == 1


def test_other_principal_cannot_refresh_or_contact_backend(tmp_path, monkeypatch, capsys):
    _operations, _backend, job_id = _setup(tmp_path, monkeypatch)

    def forbidden(_url):
        raise AssertionError("backend constructed before ownership check")

    monkeypatch.setattr("anvil_serving.media.cli.ComfyUIClient", forbidden)
    assert main(["job", "status", job_id, "--principal", "other", "--backend-url", "http://127.0.0.1:8188"]) == 1
    assert json.loads(capsys.readouterr().err)["code"] == "job_not_found"


def test_status_without_backend_keeps_snapshot_behavior(tmp_path, monkeypatch, capsys):
    _operations, backend, job_id = _setup(tmp_path, monkeypatch)
    assert main(["job", "status", job_id, "--principal", "owner"]) == 0
    assert json.loads(capsys.readouterr().out)["job"]["state"] == "queued"
    assert backend.submissions == 1


def test_different_backend_refused_before_contact_and_cannot_rebind(tmp_path, monkeypatch, capsys):
    import hashlib
    import pytest
    from anvil_serving.media.errors import MediaError

    operations, _backend, job_id = _setup(tmp_path, monkeypatch)

    def forbidden(_url):
        raise AssertionError("backend constructed before identity check")

    monkeypatch.setattr("anvil_serving.media.cli.ComfyUIClient", forbidden)
    assert main(["job", "status", job_id, "--principal", "owner", "--backend-url", "http://127.0.0.1:8189"]) == 1
    assert json.loads(capsys.readouterr().err)["code"] == "backend_identity_conflict"
    assert operations.jobs.get(job_id, principal="owner").state.value == "queued"
    with pytest.raises(MediaError) as error:
        operations.jobs.check_backend(job_id, hashlib.sha256(b"other").hexdigest(), principal="owner", bind=True)
    assert error.value.code == "backend_identity_conflict"


def test_legacy_unbound_job_refused_without_backend_contact(tmp_path, monkeypatch, capsys):
    operations, _backend, _job_id = _setup(tmp_path, monkeypatch)
    job, _ = operations.jobs.create(
        principal="owner",
        workflow_id="image.test",
        workflow_version="v1",
        input_digest="b" * 64,
        idempotency_key="legacy",
    )
    job = operations.jobs.transition(job.id, JobState.PREPARING, principal="owner")
    job = operations.jobs.transition(job.id, JobState.SUBMITTING, principal="owner")
    job = operations.jobs.set_backend_prompt(job.id, "legacy-prompt", principal="owner")

    def forbidden(_url):
        raise AssertionError("unbound job contacted a backend")

    monkeypatch.setattr("anvil_serving.media.cli.ComfyUIClient", forbidden)
    assert main(["job", "status", job.id, "--principal", "owner", "--backend-url", "http://127.0.0.1:8188"]) == 1
    assert json.loads(capsys.readouterr().err)["code"] == "backend_identity_unavailable"


def test_endpoint_binding_survives_reopen_and_is_not_public(tmp_path, monkeypatch):
    import hashlib

    operations, _backend, job_id = _setup(tmp_path, monkeypatch)
    reopened = MediaJobStore(operations.jobs.path)
    reopened.check_backend(job_id, hashlib.sha256(b"http://127.0.0.1:8188").hexdigest(), principal="owner")
    assert "127.0.0.1" not in json.dumps(reopened.get(job_id, principal="owner").as_public_dict())


def test_backend_binding_canonicalizes_new_jobs_but_allows_only_exact_legacy_spelling(tmp_path):
    import hashlib
    import pytest
    from anvil_serving.media.comfyui import ComfyUIClient
    from anvil_serving.media.errors import MediaError

    store = MediaJobStore(tmp_path / "jobs.sqlite3")

    def submitting(key):
        job, _ = store.create(
            principal="owner",
            workflow_id="image.test",
            workflow_version="v1",
            input_digest=(key * 64)[:64],
            idempotency_key=key,
        )
        job = store.transition(job.id, JobState.PREPARING, principal="owner")
        return store.transition(job.id, JobState.SUBMITTING, principal="owner")

    new = submitting("a")
    store.check_backend_endpoint(
        new.id,
        "HTTP://MediaHost:80/Comfy/",
        principal="owner",
        bind=True,
    )
    store.check_backend_endpoint(
        new.id,
        "http://mediahost/Comfy",
        principal="owner",
    )

    legacy = submitting("b")
    raw_legacy = "http://MediaHost"
    store.check_backend(
        legacy.id,
        hashlib.sha256(raw_legacy.encode("utf-8")).hexdigest(),
        principal="owner",
        bind=True,
    )
    store.check_backend_endpoint(
        legacy.id,
        ComfyUIClient(raw_legacy).base_url,
        principal="owner",
    )
    with pytest.raises(MediaError) as changed_spelling:
        store.check_backend_endpoint(
            legacy.id,
            "http://mediahost",
            principal="owner",
        )
    assert changed_spelling.value.code == "backend_identity_conflict"


def test_ambiguous_submission_retry_cannot_change_backend(tmp_path):
    import pytest
    from anvil_serving.media.errors import MediaError

    class AmbiguousBackend(_Backend):
        base_url = "http://127.0.0.1:8188"

        def submit(self, workflow, *, job_id):
            raise MediaError("backend_unavailable", "submission response lost")

        def find_prompt(self, job_id):
            raise AssertionError("wrong backend queried for ambiguous submission")

    operations = MediaOperations(_Registry(), MediaJobStore(tmp_path / "jobs.sqlite3"),
                                 ArtifactStore(tmp_path / "artifacts"))
    backend = AmbiguousBackend()
    kwargs = dict(principal="owner", idempotency_key="ambiguous", backend=backend)
    with pytest.raises(MediaError, match="submission response lost"):
        operations.workflow_run("image.test", "v1", {"prompt": "mountain"}, **kwargs)
    backend.base_url = "http://127.0.0.1:8189"
    with pytest.raises(MediaError) as error:
        operations.workflow_run("image.test", "v1", {"prompt": "mountain"}, **kwargs)
    assert error.value.code == "backend_identity_conflict"


def test_legacy_sidecar_cannot_override_actual_backend(tmp_path, monkeypatch):
    import pytest
    from anvil_serving.media.comfyui import ComfyUIClient
    from anvil_serving.media.errors import MediaError

    operations, _backend, job_id = _setup(tmp_path, monkeypatch)
    endpoint = ComfyUIClient("http://127.0.0.1:8189").base_url
    endpoint.legacy_endpoint = "http://127.0.0.1:8188"
    with pytest.raises(MediaError) as error:
        operations.jobs.check_backend_endpoint(job_id, endpoint, principal="owner")
    assert error.value.code == "invalid_backend"
