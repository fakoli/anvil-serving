"""Native artifact pulls prove exact inventory, admission, and file integrity."""
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import sys
import types

import pytest

from anvil_serving import cli, models


REVISION = "a" * 40
CONFIG = b'{"model_type":"qwen3"}\n'
WEIGHTS = b"synthetic selected weights\x00"
CONFIG_HASH = hashlib.sha1(b"blob " + str(len(CONFIG)).encode() + b"\0" + CONFIG).hexdigest()
WEIGHT_HASH = hashlib.sha256(WEIGHTS).hexdigest()


def native():
    return importlib.import_module("anvil_serving.model_pull_native")


@pytest.fixture(autouse=True)
def identified_test_downloader(monkeypatch):
    # These unit tests inject acquisition itself; downloader identification has
    # independent real-process coverage in test_model_pull_native_review.py.
    monkeypatch.setattr(native(), "_downloader_version", lambda *_args: "hf 1.33.0")


def metadata():
    return {"sha": REVISION, "siblings": [
        {"rfilename": "config.json", "size": len(CONFIG), "blobId": CONFIG_HASH},
        {"rfilename": "weights.safetensors", "size": len(WEIGHTS), "blobId": "b" * 40,
         "lfs": {"sha256": WEIGHT_HASH, "size": len(WEIGHTS), "pointerSize": 130}},
        {"rfilename": "unused.bin", "size": 1000, "blobId": "c" * 40},
    ]}


def remote(data=None, requests=None):
    def open_response(request, **kwargs):
        if requests is not None:
            requests.append(request)
        return io.BytesIO(json.dumps(metadata() if data is None else data).encode())
    return open_response


def snapshot(cache, *, config=CONFIG, weights=WEIGHTS):
    repo = cache / "models--org--model"
    snap = repo / "snapshots" / REVISION
    blobs = repo / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    snap.mkdir(parents=True, exist_ok=True)
    for filename, digest, value in [
        ("config.json", CONFIG_HASH, config),
        ("weights.safetensors", WEIGHT_HASH, weights),
    ]:
        (blobs / digest).write_bytes(value)
        path = snap / filename
        if not path.is_symlink():
            path.symlink_to(Path("../../blobs") / digest)
    return snap


def invoke(cache, **kwargs):
    return native().pull(
        "org/model", REVISION, str(cache), hf_executable=sys.executable,
        exclude="unused.bin", headroom_bytes=10, no_token=True,
        _open=remote(), **kwargs,
    )


@pytest.mark.parametrize("returncode", [0, 17])
def test_native_xet_uses_owned_cache_local_scratch_and_retains_verified_or_partial_bytes(tmp_path, returncode):
    cache = tmp_path / "hub"
    inherited = tmp_path / "external-xet-cache"
    env = {"PATH": os.environ.get("PATH", ""), "HF_XET_CACHE": str(inherited),
           "HF_XET_CHUNK_CACHE_SIZE_BYTES": "123", "HF_XET_SHARD_CACHE_SIZE_LIMIT": "456"}

    def download(_argv, **kwargs):
        child = kwargs["env"]
        scratch = Path(child["HF_XET_CACHE"])
        assert child["HF_HUB_DISABLE_XET"] == "0"
        assert child["HF_HUB_DISABLE_SHARED_BLOBS"] == "1"
        assert child["HF_XET_HIGH_PERFORMANCE"] == "0"
        assert child["HF_XET_CHUNK_CACHE_SIZE_BYTES"] == "0"
        assert child["HF_XET_SHARD_CACHE_SIZE_LIMIT"] == "0"
        assert scratch.parent == cache and scratch.is_dir() and scratch != inherited
        if returncode:
            partial = cache / "models--org--model" / "blobs" / (WEIGHT_HASH + ".incomplete")
            partial.parent.mkdir(parents=True, exist_ok=True)
            partial.write_bytes(WEIGHTS[:3])
        else:
            snapshot(cache)
        return types.SimpleNamespace(returncode=returncode, stdout="", stderr="")

    result = invoke(cache, xet=True, _environ=env, _run=download)
    assert result["transport"] == "xet"
    if returncode:
        assert result["status"] == "download_failed"
        assert (cache / "models--org--model" / "blobs" / (WEIGHT_HASH + ".incomplete")).read_bytes() == WEIGHTS[:3]
    else:
        assert result["status"] == "verified"
    assert not list(cache.glob(".anvil-serving-xet-*"))
    assert not inherited.exists()


def test_native_cli_confirmation_and_structured_output(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(native().urllib.request, "urlopen", remote())
    monkeypatch.setattr(models.host_ops, "load_cache_reclaim_policy", lambda: pytest.fail("native invoked Docker host policy"))
    args = ["models", "pull", "org/model", "--revision", REVISION,
            "--cache-dir", str(tmp_path / "hub"), "--hf-executable", sys.executable,
            "--exclude", "unused.bin", "--no-token", "--json"]
    assert cli.main(args) != 0
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "confirmation_required"
    assert cli.main([*args, "--dry-run"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["status"] == "preview" and data["inventory"]["selected_file_count"] == 2
    assert not (tmp_path / "hub").exists()


def test_native_hf_home_normalizes_existing_hub_and_uses_explicit_new_evidence(tmp_path):
    home = tmp_path / "huggingface"
    cache = home / "hub"
    cache.mkdir(parents=True)
    evidence = tmp_path / "campaign" / "pull.json"
    def download(argv, **kwargs):
        assert argv[argv.index("--cache-dir") + 1] == str(cache)
        snapshot(cache)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    result = invoke(home, evidence_out=str(evidence), _run=download)
    assert result["cache_dir"] == str(cache)
    assert result["retained_result"] == str(evidence)
    assert json.loads(evidence.read_text())["verification"]["verified_file_count"] == 2
    with pytest.raises(native().NativePullError, match="new file"):
        invoke(home, evidence_out=str(evidence), _run=lambda *_a, **_k: pytest.fail("overwrote evidence"))


def test_native_preview_never_reads_declared_secret_sources(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(native().urllib.request, "urlopen", remote())
    monkeypatch.setattr(models, "_pull_token", lambda *_a: pytest.fail("preview read a secret"))
    assert models.pull_main(["org/model", "--revision", REVISION,
                             "--cache-dir", str(tmp_path / "hub"), "--hf-executable", sys.executable,
                             "--token-env", "PRIVATE_TOKEN_SOURCE", "--token-file", "private-secret-file",
                             "--exclude", "unused.bin", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "PRIVATE_TOKEN_SOURCE" not in output and "private-secret-file" not in output


def test_native_authenticated_confirmed_cli_maps_reference_without_printing_it(monkeypatch, tmp_path, capsys):
    requests = []
    monkeypatch.setenv("PRIVATE_TOKEN_SOURCE", "private-test-value")
    monkeypatch.setattr(native().urllib.request, "urlopen", remote(requests=requests))
    cache = tmp_path / "hub"
    def download(argv, **kwargs):
        assert kwargs["env"]["HF_TOKEN"] == "private-test-value"
        assert "private-test-value" not in argv
        snapshot(cache)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(native().subprocess, "run", download)
    args = ["models", "pull", "org/model", "--revision", REVISION,
            "--cache-dir", str(cache), "--hf-executable", sys.executable,
            "--token-env", "PRIVATE_TOKEN_SOURCE", "--exclude", "unused.bin", "--confirm", "--json"]
    assert cli.main(args) == 0
    report = json.loads(capsys.readouterr().out)["data"]
    assert report["status"] == "verified"
    assert requests[0].get_header("Authorization") == "Bearer private-test-value"
    assert "private-test-value" not in json.dumps(report)
    assert "PRIVATE_TOKEN_SOURCE" not in json.dumps(report)


def test_native_selected_root_symlink_refused_without_writes(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "hub"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(native().NativePullError, match="symlink"):
        invoke(linked)
    assert list(real.iterdir()) == []
