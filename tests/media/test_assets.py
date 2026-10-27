import base64
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import http.client
import io
import json
import shutil
import subprocess

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


@pytest.mark.parametrize("limits", [{"frames": float("nan")}, {"width": True}, {"unknown": 1}, {"frames": 0}])
def test_invalid_extent_contract_is_rejected(limits):
    with pytest.raises(MediaError):
        ParameterSpec("artifact", media_limits=limits)


@pytest.mark.parametrize("info", [{**INFO, "frames": 125}, {**INFO, "frames": float("nan")}, {"height": 1}])
def test_extent_checks_do_not_trust_compressed_size(info):
    with pytest.raises(MediaError):
        assets.enforce_limits(info, {"width": 864, "height": 480, "frames": 124})


def test_probe_uses_decoded_count_and_rejects_malformed_or_multistream(monkeypatch):
    raw = {"streams": [{"codec_type": "video", "width": 864, "height": 480,
                        "nb_frames": "1", "nb_read_frames": "125"}], "format": {"duration": "5.2"}}
    monkeypatch.setattr(assets, "_probe", lambda *a, **k: raw)
    info = assets.inspect_media(None, "video/mp4")
    assert info["frames"] == 125 and info["decodedBytes"] == 125 * 864 * 480 * 12
    raw["format"]["duration"] = "NaN"
    with pytest.raises(MediaError):
        assets.inspect_media(None, "video/mp4")
    raw["format"]["duration"] = "5.2"
    raw["streams"].append(dict(raw["streams"][0]))
    with pytest.raises(MediaError):
        assets.inspect_media(None, "video/mp4")


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


@pytest.mark.parametrize("suffix,media_type", [("jpg", "image/jpeg"), ("mp4", "video/mp4"), ("wav", "audio/wav")])
def test_real_decoder_accepts_video_image_and_audio(tmp_path, suffix, media_type):
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        pytest.skip("managed decoders not installed on this test host")
    path = tmp_path / ("input." + suffix)
    generator = ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.5"] if suffix == "wav" else [
        "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=8:d=0.5"]
    if suffix == "jpg":
        generator += ["-frames:v", "1", "-update", "1"]
    subprocess.run(["ffmpeg", "-v", "error", *generator, "-y", str(path)], timeout=15, check=True)
    info = assets.inspect_media(path, media_type)
    assert info["decodedBytes"] > 0
    if suffix == "mp4":
        assert info["frames"] == 4 and info["durationSeconds"] == 0.5


def test_cli_command_does_not_forward_resource_words(monkeypatch, capsys):
    from anvil_serving import cli
    from anvil_serving.media import asset_client
    called = []
    monkeypatch.setattr(asset_client, "upload", lambda path, config, **kw: called.append((path, config, kw)) or {"dryRun": True})
    assert cli.main(["media", "asset", "upload", "--file", "reference.png", "--client-config", "connection.json", "--dry-run"]) == 0
    assert called == [("reference.png", "connection.json", {"dry_run": True})]
    assert json.loads(capsys.readouterr().out)["dryRun"]


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
