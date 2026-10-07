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


def test_native_preview_exact_inventory_has_no_filesystem_or_process_mutation(tmp_path):
    cache = tmp_path / "hub"
    result = invoke(cache, dry_run=True, _run=lambda *_a, **_k: pytest.fail("executed hf"))
    assert result["status"] == "preview"
    assert result["inventory"]["selected_file_count"] == 2
    assert result["preflight"]["expected_bytes"] == len(CONFIG) + len(WEIGHTS)
    assert result["preflight"]["missing_bytes"] == len(CONFIG) + len(WEIGHTS)
    assert result["snapshot_path"] == str(cache / "models--org--model" / "snapshots" / REVISION)
    assert not cache.exists()


@pytest.mark.parametrize("revision", [None, "main", "a" * 39, "../bad", "A" * 40])
def test_native_requires_exact_lowercase_immutable_revision(tmp_path, revision):
    with pytest.raises(native().NativePullError, match="40 lowercase"):
        native().pull("org/model", revision, str(tmp_path), hf_executable=sys.executable,
                      dry_run=True, _open=lambda *_a, **_k: pytest.fail("queried mutable revision"))


@pytest.mark.parametrize("change", ["wrong_revision", "missing_size", "missing_hash", "traversal", "duplicate"])
def test_native_refuses_untrusted_or_incomplete_remote_inventory(tmp_path, change):
    data = metadata()
    if change == "wrong_revision":
        data["sha"] = "b" * 40
    elif change == "missing_size":
        del data["siblings"][0]["size"]
    elif change == "missing_hash":
        del data["siblings"][0]["blobId"]
    elif change == "traversal":
        data["siblings"][0]["rfilename"] = "../config.json"
    else:
        data["siblings"].append(data["siblings"][0])
    with pytest.raises(native().NativePullError):
        native().pull("org/model", REVISION, str(tmp_path), hf_executable=sys.executable,
                      dry_run=True, exclude="unused.bin", _open=remote(data))


def test_native_space_admission_does_not_credit_unrelated_repository_bytes(tmp_path):
    cache = tmp_path / "hub"
    unrelated = cache / "models--org--model" / "blobs"
    unrelated.mkdir(parents=True)
    (unrelated / ("f" * 64)).write_bytes(b"unrelated" * 100)
    with pytest.raises(native().NativePullError, match="insufficient"):
        invoke(cache, _disk_usage=lambda _path: types.SimpleNamespace(free=10),
               _run=lambda *_a, **_k: pytest.fail("download before admission"))
    assert (unrelated / ("f" * 64)).is_file()


def test_native_download_verifies_hashes_retains_receipt_and_strips_tokens(tmp_path):
    cache = tmp_path / "hub"
    requests = []
    env = {"PATH": os.environ.get("PATH", ""), "HF_TOKEN": "secret-a",
           "HUGGING_FACE_HUB_TOKEN": "secret-b", "HUGGINGFACE_HUB_TOKEN": "secret-c"}
    def download(argv, **kwargs):
        assert argv[:3] == [str(Path(sys.executable).resolve()), "download", "org/model"]
        assert argv[argv.index("--revision") + 1] == REVISION
        assert argv[argv.index("--cache-dir") + 1] == str(cache)
        assert "--local-dir" not in argv and not kwargs.get("shell")
        assert not any(name in kwargs["env"] for name in (
            "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_HUB_TOKEN"))
        assert kwargs["env"]["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"
        assert kwargs["env"]["HF_HUB_DISABLE_XET"] == "1"
        snapshot(cache)
        return types.SimpleNamespace(returncode=0, stdout="ignored mutable path\n", stderr="")
    result = native().pull(
        "org/model", REVISION, str(cache), hf_executable=sys.executable, no_token=True,
        exclude="unused.bin", headroom_bytes=10, _environ=env,
        _open=remote(requests=requests), _run=download,
    )
    assert result["status"] == "verified" and result["exit_code"] == 0
    assert result["verification"]["verified_file_count"] == 2
    assert {item["hash_algorithm"] for item in result["verification"]["files"]} == {"git-sha1", "sha256"}
    assert Path(result["retained_result"]).is_file()
    assert json.loads(Path(result["retained_result"]).read_text())["status"] == "verified"
    assert requests[0].get_header("Authorization") is None
    assert env["HF_TOKEN"] == "secret-a"
    assert "secret-" not in json.dumps(result)
    assert models.model_cache_native.inventory(cache)["layout"] == "standard"


@pytest.mark.parametrize("failure", ["missing", "hash", "incomplete", "external_link"])
def test_native_hf_success_cannot_bypass_independent_completeness_check(tmp_path, failure):
    cache = tmp_path / "hub"
    outside = tmp_path / "outside"
    outside.write_bytes(CONFIG)
    def download(*_args, **_kwargs):
        snap = snapshot(cache, weights=b"x" * len(WEIGHTS) if failure == "hash" else WEIGHTS)
        if failure == "missing":
            (snap / "config.json").unlink()
        elif failure == "incomplete":
            (cache / "models--org--model" / "blobs" / (WEIGHT_HASH + ".incomplete")).write_bytes(b"x")
        elif failure == "external_link":
            (snap / "config.json").unlink()
            (snap / "config.json").symlink_to(outside)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    result = invoke(cache, _run=download)
    assert result["status"] == "verification_failed" and result["exit_code"] != 0
    assert json.loads(Path(result["retained_result"]).read_text())["status"] == "verification_failed"
    assert outside.read_bytes() == CONFIG


def test_native_repeat_credits_only_verified_selected_blobs_and_preserves_other_files(tmp_path):
    cache = tmp_path / "hub"
    snapshot(cache)
    partial = cache / "models--org--model" / "blobs" / ("f" * 64 + ".incomplete")
    partial.write_bytes(b"unrelated partial download")
    result = invoke(cache, dry_run=True)
    assert result["preflight"]["missing_bytes"] == 0
    assert result["preflight"]["cached_selected_bytes"] == len(CONFIG) + len(WEIGHTS)
    assert partial.read_bytes() == b"unrelated partial download"


def test_native_rejects_corrupt_existing_complete_blob_without_deleting_it(tmp_path):
    cache = tmp_path / "hub"
    snapshot(cache, weights=b"x" * len(WEIGHTS))
    with pytest.raises(native().NativePullError, match="hash"):
        invoke(cache, _run=lambda *_a, **_k: pytest.fail("hf would reuse corrupt blob"))
    assert (cache / "models--org--model" / "blobs" / WEIGHT_HASH).read_bytes() == b"x" * len(WEIGHTS)


def test_native_concurrent_anvil_writer_refused_and_lock_reusable(tmp_path):
    cache = tmp_path / "hub"
    def download(*_args, **_kwargs):
        with pytest.raises(native().NativePullError, match="already in progress"):
            invoke(cache, _run=lambda *_a, **_k: pytest.fail("second writer"))
        snapshot(cache)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    assert invoke(cache, _run=download)["status"] == "verified"
    assert invoke(cache, _run=download)["status"] == "verified"


def test_native_failure_preserves_partial_download_and_can_resume(tmp_path):
    cache = tmp_path / "hub"
    partial = cache / "models--org--model" / "blobs" / (WEIGHT_HASH + ".incomplete")
    def interrupted(*_args, **_kwargs):
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(WEIGHTS[:3])
        return types.SimpleNamespace(returncode=17, stdout="", stderr="connection lost")
    result = invoke(cache, _run=interrupted)
    assert result["exit_code"] == 17 and result["status"] == "download_failed"
    assert partial.read_bytes() == WEIGHTS[:3]
    def resume(*_args, **_kwargs):
        assert partial.read_bytes() == WEIGHTS[:3]
        snapshot(cache)
        partial.unlink()  # hf owns completing its own partial artifact.
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    assert invoke(cache, _run=resume)["status"] == "verified"


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


def test_native_pull_help_uses_canonical_command_once(capsys):
    assert cli.main(["models", "pull", "--help"]) == 0
    output = capsys.readouterr().out
    assert "anvil-serving models pull pull" not in output
    assert "--cache-dir" in output and "--hf-executable" in output and "--confirm" in output


@pytest.mark.parametrize("option", [["--volume", "volume"], ["--image", "image"], ["--expected-bytes", "1"]])
def test_native_cli_rejects_docker_or_inventory_bypass_options(tmp_path, option, capsys):
    assert models.pull_main(["org/model", "--revision", REVISION,
                             "--cache-dir", str(tmp_path), "--no-token", "--dry-run", *option]) == 2
    assert capsys.readouterr().err
