"""Focused checks for the Windows-native artifact staging escape hatch."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from anvil_serving import models
from tests.conftest import Response


REPO = "wallawalla47/Qwen3.8-27B-Quasar-NVFP4-NInferV3"
REVISION = "fd3d06cea9df58a54d5051aac9545a4a01a922db"
FILENAME = "qwen3_8_27b_nvfp4-quasar-proposal.ninfer"


def test_native_stage_metadata_requires_exact_safe_lfs_artifact():
    content = b"artifact"
    sha256 = hashlib.sha256(content).hexdigest()
    seen = {}

    def open_(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        return Response(json.dumps({
            "sha": REVISION,
            "siblings": [{"rfilename": FILENAME, "lfs": {"sha256": sha256, "size": len(content)}}],
        }).encode())

    result = models._native_stage_metadata(REPO, REVISION, FILENAME, _open=open_)
    assert result == {"revision": REVISION, "filename": FILENAME, "sha256": sha256, "size": len(content)}
    assert "/revision/%s?blobs=true" % REVISION in seen["url"]
    assert not any(name.lower() == "authorization" for name in seen["headers"])
    with pytest.raises(ValueError, match="safe literal"):
        models._native_stage_metadata(REPO, REVISION, "../outside", _open=open_)

    def oid_only_open(_request, timeout):
        return Response(json.dumps({
            "sha": REVISION,
            "siblings": [{"rfilename": FILENAME, "lfs": {
                "oid": sha256, "size": len(content),
            }}],
        }).encode())

    with pytest.raises(ValueError, match="LFS SHA256"):
        models._native_stage_metadata(REPO, REVISION, FILENAME, _open=oid_only_open)


def test_native_stage_rejects_parent_link_escape(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (stage / "nested").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows does not permit this test's directory symlink")
    with pytest.raises(ValueError, match="escapes"):
        models.preflight_native_stage(str(stage), "nested/artifact.ninfer", 1)


def test_native_stage_import_uses_read_only_bind_and_offline_docker(tmp_path):
    stage = tmp_path / "stage"
    target = stage / FILENAME
    target.parent.mkdir(parents=True)
    content = b"verified artifact"
    target.write_bytes(content)
    sha256 = hashlib.sha256(content).hexdigest()
    seen = []

    def run(argv, **_kwargs):
        seen.append(argv)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"filename": FILENAME, "bytes": len(content)}) + "\n",
            stderr="",
        )

    result = models.run_native_stage_pull(
        REPO, REVISION, FILENAME, sha256, str(stage), volume="vllm-hfcache", _run=run,
    )
    assert result["filename"] == FILENAME
    assert len(seen) == 1
    argv = seen[0]
    assert argv[:5] == ["docker", "run", "--rm", "--network", "none"]
    mounts = [argv[index + 1] for index, item in enumerate(argv) if item == "--mount"]
    assert "type=volume,source=vllm-hfcache,target=/root/.cache/huggingface" in mounts
    assert any("target=/anvil-stage,readonly" in mount for mount in mounts)
    assert FILENAME in argv and sha256 in argv


@pytest.mark.parametrize("public_cli", [False, True])
def test_native_stage_dry_run_does_not_probe_or_download(monkeypatch, capsys, public_cli):
    from anvil_serving import cli

    def unexpected(*_args, **_kwargs):
        pytest.fail("native-stage preview must not probe metadata, storage, or download")

    monkeypatch.setattr(models.host_ops, "load_cache_reclaim_policy", lambda: {})
    monkeypatch.setattr(models.host_ops, "render_cache_reclaim_plan", lambda *_args: None)
    for name in ("_native_stage_metadata", "preflight_native_stage", "preflight_native_import",
                 "run_native_stage_pull"):
        monkeypatch.setattr(models, name, unexpected)
    args = [
        REPO, "--native-stage", "stage", "--no-token", "--revision", REVISION,
        "--include", FILENAME, "--dry-run",
    ]
    monkeypatch.setattr(cli, "_resolve_dispatch_plan", lambda *_args: None)
    assert (cli.main(["models", "pull", *args]) if public_cli else models.pull_main(args)) == 0
    assert "MODEL ARTIFACT NATIVE-STAGE PLAN" in capsys.readouterr().out
