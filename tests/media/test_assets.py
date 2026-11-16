import base64
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import http.client
import io
import json
import shutil

import pytest

from anvil_serving.media import assets
from anvil_serving.media.artifacts import ArtifactStore
from anvil_serving.media.contracts import ParameterSpec
from anvil_serving.media.errors import MediaError
from tests.media.test_artifact_inputs import setup, run
from tests.router.test_gateway_protocol_dispatch import gateway_server

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
INFO = {"width": 1, "height": 1, "frames": 1, "decodedBytes": 12}


def ingest(store, **kwargs):
    return store.ingest(None, io.BytesIO(PNG), principal="hermes", media_type="image/png",
                        max_bytes=1024, retention_seconds=60,
                        expected_sha256=hashlib.sha256(PNG).hexdigest(), validate=lambda p: dict(INFO), **kwargs)


def test_owned_input_has_no_fake_job_and_recipe_must_declare_limits(tmp_path):
    operations, backend = setup(tmp_path, media_types=["image/png"])
    artifact = ingest(operations.artifacts)
    public = artifact.as_public_dict()
    assert public["origin"] == "input" and "jobId" not in public and "workflow" not in public
    assert public["mediaInfo"] == INFO
    with pytest.raises(MediaError, match="no qualified"):
        run(operations, backend, artifact.id)
    workflow = operations.registry.get("video.test", "v1")
    operations.registry._descriptors[workflow.key] = replace(workflow, parameters={"source": ParameterSpec(
        "artifact", media_types=("image/png",), media_limits=INFO)})
    result = run(operations, backend, artifact.id)
    assert result["created"] and len(backend.submissions) == 1
    assert backend.uploads[0][0].endswith(".png")
    with pytest.raises(MediaError):
        operations.artifacts.metadata(artifact.id, principal="other")


def test_failed_digest_or_probe_leaves_no_input_bytes(tmp_path):
    store = ArtifactStore(tmp_path)
    for digest, validate in (("0" * 64, lambda p: INFO),
                             (hashlib.sha256(PNG).hexdigest(), lambda p: (_ for _ in ()).throw(MediaError("bad", "bad")))):
        with pytest.raises(MediaError):
            store.ingest(None, io.BytesIO(PNG), principal="hermes", media_type="image/png", max_bytes=1024,
                         retention_seconds=60, expected_sha256=digest, validate=validate)
        assert not list(tmp_path.iterdir())


def test_failed_metadata_promotion_leaves_no_orphan(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path)
    monkeypatch.setattr(store, "_write_metadata", lambda a: (_ for _ in ()).throw(OSError("unavailable")))
    with pytest.raises(OSError):
        ingest(store)
    assert not list(tmp_path.iterdir())


def test_limited_reader_stops_at_length_and_has_total_deadline(monkeypatch):
    source = io.BytesIO(b"abNEXT")
    reader = assets.LimitedReader(source, 2)
    assert reader.read(20) == b"ab" and reader.read(20) == b"" and source.read() == b"NEXT"
    reader = assets.LimitedReader(io.BytesIO(b"a"), 2)
    reader.read(2)
    with pytest.raises(MediaError, match="before Content-Length"):
        reader.read(2)
    reader = assets.LimitedReader(io.BytesIO(b"a"), 1)
    monkeypatch.setattr(assets.time, "monotonic", lambda: reader.deadline)
    with pytest.raises(MediaError, match="total deadline"):
        reader.read(1)


def test_authenticated_upload_framing_digest_owner_and_no_scope(tmp_path, monkeypatch):
    monkeypatch.setattr("anvil_serving.media.operations.inspect_media", lambda *a: dict(INFO))
    with gateway_server(tmp_path) as (address, tasks):
        def send(*, token="secret", extra=None, path=assets.ASSET_PATH, content=PNG):
            headers = {"Content-Length": str(len(content)), "Content-Type": "image/png",
                       "X-Content-SHA256": hashlib.sha256(content).hexdigest(), **(extra or {})}
            if token:
                headers["Authorization"] = "Bearer " + token
            connection = http.client.HTTPConnection(*address, timeout=5)
            try:
                connection.request("POST", path, body=content, headers=headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        assert send(token=None)[0] == 401
        assert send(extra={"Content-Length": str(assets.MAX_ASSET_BYTES + 1)})[0] == 413
        assert send(extra={"Content-Encoding": "gzip"})[0] == 400
        assert send(extra={"Transfer-Encoding": "chunked"})[0] == 400
        assert send(extra={"X-Content-SHA256": "0" * 64})[0] == 422
        assert send(path=assets.ASSET_PATH + "?signature=key")[0] == 400
        status, body = send()
        assert status == 201 and body["artifact"]["origin"] == "input"
        artifact_id = body["artifact"]["id"]
        assert len(list(tasks.operations.artifacts.root.glob("*.bin"))) == 1
        with pytest.raises(MediaError):
            tasks.operations.artifacts.metadata(artifact_id, principal="other")
    from tests.a2a.test_tasks import CALLER
    with gateway_server(tmp_path / "no-scope", caller={**CALLER, "scopes": ["media:read"]}) as (address, _):
        assert send()[0] == 403


def test_real_decoder_rejects_corrupt_and_accepts_single_still(tmp_path):
    if shutil.which("ffprobe") is None:
        pytest.skip("managed ffprobe not installed on this test host")
    path = tmp_path / "input.png"
    path.write_bytes(PNG)
    assert assets.inspect_media(path, "image/png") == INFO
    path.write_bytes(PNG[:24])
    with pytest.raises(MediaError):
        assets.inspect_media(path, "image/png")


def test_client_discovers_policy_and_uses_only_its_configured_origin(tmp_path, monkeypatch):
    from anvil_serving.media import asset_client
    monkeypatch.setattr("anvil_serving.media.operations.inspect_media", lambda *a: dict(INFO))
    advertised = {**assets.policy(), "available": True}
    calls = []

    @contextmanager
    def client(*args):
        def call(name, arguments):
            calls.append((name, arguments))
            return {"assetUpload": advertised}
        yield call

    monkeypatch.setattr(asset_client, "mcp_client", client)
    monkeypatch.setattr(asset_client, "resolve_controller_token_file", lambda path: "secret")
    path = tmp_path / "face.png"; path.write_bytes(PNG)
    config = tmp_path / "connection.json"
    with gateway_server(tmp_path / "server") as (address, tasks):
        config.write_text(json.dumps({"command": "anvil-serving", "args": ["--controller-url",
            f"http://{address[0]}:{address[1]}/mcp", "--auth-file", "protected.key"]}), encoding="utf-8")
        assert asset_client.upload(path, config, dry_run=True)["dryRun"]
        assert not list(tasks.operations.artifacts.root.glob("*.bin"))
        receipt = asset_client.upload(path, config)
        assert receipt["artifact"]["sha256"] == hashlib.sha256(PNG).hexdigest()
        assert calls == [("media_capabilities", {}), ("media_capabilities", {})]
        advertised["path"] = "http://example.test/steal"
        with pytest.raises(ValueError):
            asset_client.upload(path, config)
        assert len(list(tasks.operations.artifacts.root.glob("*.bin"))) == 1
