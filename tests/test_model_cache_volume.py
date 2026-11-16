import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from anvil_serving import model_cache_volume as cache_volume
from anvil_serving import models


REPO = "org/model"
REVISION = "a" * 40
VOLUME = "dedicated-cache"
pytestmark = pytest.mark.skipif(os.name != "posix", reason="dedicated volume cleanup requires POSIX locks")


def _done(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def _cache(tmp_path):
    root = tmp_path / "volumes" / VOLUME / "_data"; hub = root / "hub"
    blob = hub / "blobs" / "bb" / ("b" * 64); blob.parent.mkdir(parents=True); blob.write_bytes(b"model")
    (blob.parent / (blob.name + ".lock")).write_bytes(b"")
    (blob.parent / (blob.name + ".refs")).write_text("models--org--model/blobs/" + "b" * 64 + "\n")
    model = hub / "models--org--model"; snapshot = model / "snapshots" / REVISION
    snapshot.mkdir(parents=True); (model / "blobs").mkdir()
    (model / "blobs" / ("b" * 64)).symlink_to(os.path.relpath(blob, model / "blobs"))
    (snapshot / "weight.safetensors").symlink_to(os.path.relpath(blob, snapshot))
    (model / "refs").mkdir(); (model / "refs" / "main").write_text(REVISION + "\n")
    return root


def _auxiliary_cache(root):
    (root / ".check_for_update_done").touch()
    (root / ".check_for_skill_update_done").touch()
    (root / ".agent_harnesses.json").write_text(json.dumps({
        "standardEnvVars": ["AI_AGENT"], "harnesses": {"example": {
            "prettyLabel": "Example", "description": "Public detection metadata",
            "docsUrl": "https://example.test/docs", "repoUrl": "https://example.test/repo",
            "envVars": {"EXAMPLE_AGENT": "*"}}}}))
    (root / "hub" / "CACHEDIR.TAG").write_text(
        "Signature: 8a477f597d28d172789f06886806bc55\n"
        "# This file is a cache directory tag created by huggingface_hub.\n"
        "# For information about cache directory tags, see:\n#\thttps://bford.info/cachedir/\n")
    kernel = root / "hub" / "kernels--org--example-kernel"
    for revision in ("c" * 40, "d" * 40):
        missing = kernel / ".no_exist" / revision
        missing.mkdir(parents=True)
        (missing / "kernel-status.toml").touch()
    (kernel / "refs").mkdir()
    (kernel / "refs" / "main").write_text("d" * 40)
    module = root / "modules" / "transformers_modules" / ("_" + REVISION) / ("e" * 16)
    module.mkdir(parents=True)
    for directory in [root / "modules", module.parent.parent, module.parent, module]:
        (directory / "__init__.py").touch()
    source = module / "processing_model.py"
    source.write_bytes(b"model")
    snapshot = root / "hub" / "models--org--model" / "snapshots" / REVISION
    (snapshot / source.name).symlink_to(os.readlink(snapshot / "weight.safetensors"))
    (module / "__pycache__").mkdir()
    (module / "__pycache__" / "processing_model.cpython-312.pyc").write_bytes(
        b"\xcb\r\r\n" + bytes(4) + int(source.stat().st_mtime).to_bytes(4, "little")
        + source.stat().st_size.to_bytes(4, "little") + b"synthetic bytecode")
    xet = root / "xet"
    (xet / "https___cas_serv-example" / "staging").mkdir(parents=True)
    (xet / "logs").mkdir()
    (xet / "logs" / "xet_20261016T090113740+0000_1.log").write_text("private-log-content-not-for-output\n")
    return module


class Docker:
    def __init__(self, root, *, containers="", config_ref=False, timeout=False, mounts=None):
        self.root = root; self.removed = False; self.calls = []; self.containers = containers; self.timeout = timeout; self.mounts = mounts

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        command = argv[1:]
        if self.timeout and command[:2] == ["volume", "rm"]:
            raise subprocess.TimeoutExpired(argv, 120)
        if command[:2] == ["volume", "inspect"]:
            if self.removed:
                return _done(argv, 1, stderr="No such volume")
            return _done(argv, stdout=json.dumps([{"Name": VOLUME, "Driver": "local", "CreatedAt": "today",
                "Mountpoint": str(self.root), "Options": {}, "Labels": {}}]))
        if command[:2] == ["container", "ls"]:
            return _done(argv, stdout=self.containers)
        if command[:2] == ["container", "inspect"]:
            identifier = self.containers.strip()
            mounts = self.mounts if self.mounts is not None else [{"Type": "volume", "Name": VOLUME, "Source": str(self.root)}]
            return _done(argv, stdout=identifier + "\t" + json.dumps(mounts) + "\t[]\n")
        if command[:2] == ["volume", "rm"]:
            self.removed = True; shutil.rmtree(self.root.parent); return _done(argv, stdout=VOLUME + "\n")
        raise AssertionError(command)


def _home(tmp_path, content="[topology]\nname = 'test'\n"):
    home = tmp_path / "operator"; home.mkdir(); (home / "operator-topology.toml").write_text(content)
    return home


@pytest.fixture(autouse=True)
def _no_external_native_consumers(monkeypatch):
    monkeypatch.setattr(cache_volume, "_native_fd_refs", lambda _mountpoint: [])


def test_preview_is_read_only_and_emits_fingerprint(tmp_path):
    docker = Docker(_cache(tmp_path)); home = _home(tmp_path)
    result = cache_volume.remove(REPO, REVISION, VOLUME, home, expected_fingerprint=None,
                                 dry_run=True, runner=docker)
    assert result["outcome"] == "preview"
    assert len(result["inspection"]["volume_fingerprint"]) == 64
    assert not any(call[1:3] == ["volume", "rm"] for call in docker.calls)


def _mixed_cache(tmp_path):
    root = _cache(tmp_path)
    model = root / "hub" / "models--org--model"
    local = model / "blobs" / ("c" * 40)
    local.write_bytes(b"selected configuration")
    (model / "snapshots" / REVISION / "config.json").symlink_to("../../blobs/" + local.name)
    (root / "hub" / "blobs" / ".huggingface-shared-blobs").write_bytes(b"1\n")
    return root, local


def _tree_metadata(root, local):
    trees = local.parent.parent / "trees"
    trees.mkdir()
    path = trees / (REVISION + ".json")
    value = {"format_version": 1, "files": {
        "config.json": {"size": local.stat().st_size, "blob_id": local.name},
        "weight.safetensors": {"size": 5, "blob_id": "d" * 40,
                               "lfs_sha256": "e" * 64, "lfs_size": 5, "xet_hash": "b" * 64}}}
    path.write_text(json.dumps(value))
    return path, value


@pytest.mark.parametrize("change", [None, "foreign-file", "size", "hash", "extra-field", "other-revision"])
def test_selected_tree_metadata_is_bound_to_snapshot(tmp_path, change):
    root, local = _mixed_cache(tmp_path)
    path, value = _tree_metadata(root, local)
    if change == "foreign-file":
        value["files"]["foreign"] = value["files"].pop("config.json")
    elif change == "size":
        value["files"]["config.json"]["size"] += 1
    elif change == "hash":
        value["files"]["config.json"]["blob_id"] = "e" * 40
    elif change == "extra-field":
        value["files"]["config.json"]["unknown"] = "payload"
    path.write_text(json.dumps(value))
    if change == "other-revision":
        path.rename(path.with_name("f" * 40 + ".json"))
    if change is not None:
        with pytest.raises(cache_volume.ModelCacheVolumeError):
            cache_volume.inspect(REPO, REVISION, VOLUME, _home(tmp_path), runner=Docker(root))
    else:
        result = cache_volume.inspect(REPO, REVISION, VOLUME, _home(tmp_path), runner=Docker(root))
        assert any(row["kind"] == "selected-snapshot-tree-metadata" for row in result["auxiliary_cache"]["entries"])


@pytest.mark.parametrize("change", ["unused-local", "missing-local", "non-hash-local", "escaping-local",
                                     "bad-marker", "foreign-refs"])
def test_mixed_layout_preserves_exact_ownership_checks(tmp_path, change):
    root, local = _mixed_cache(tmp_path)
    if change == "unused-local":
        local.with_name("d" * 40).write_bytes(b"unselected")
    elif change == "missing-local":
        local.unlink()
    elif change == "non-hash-local":
        renamed = local.with_name("unknown")
        local.rename(renamed)
        snapshot_link = root / "hub" / "models--org--model" / "snapshots" / REVISION / "config.json"
        snapshot_link.unlink()
        snapshot_link.symlink_to("../../blobs/unknown")
    elif change == "escaping-local":
        outside = tmp_path / "outside"
        local.rename(outside)
        local.symlink_to(os.path.relpath(outside, local.parent))
    elif change == "bad-marker":
        (root / "hub" / "blobs" / ".huggingface-shared-blobs").write_bytes(b"2\n")
    else:
        next((root / "hub" / "blobs").rglob("*.refs")).write_text("models--other--model/blobs/" + "b" * 64 + "\n")
    with pytest.raises(cache_volume.ModelCacheVolumeError):
        cache_volume.inspect(REPO, REVISION, VOLUME, _home(tmp_path), runner=Docker(root))


def test_apply_requires_fingerprint_and_writer_attestation(tmp_path):
    docker = Docker(_cache(tmp_path)); home = _home(tmp_path)
    with pytest.raises(cache_volume.ModelCacheVolumeError, match="fingerprint"):
        cache_volume.remove(REPO, REVISION, VOLUME, home, expected_fingerprint=None,
                            writers_quiesced=True, confirm=True, runner=docker)
    preview = cache_volume.inspect(REPO, REVISION, VOLUME, home, runner=docker)
    with pytest.raises(cache_volume.ModelCacheVolumeError, match="writers-quiesced"):
        cache_volume.remove(REPO, REVISION, VOLUME, home, expected_fingerprint=preview["volume_fingerprint"],
                            confirm=True, runner=docker)
    assert not any(call[1:3] == ["volume", "rm"] for call in docker.calls)


def test_root_env_symlink_is_opaque_but_cannot_be_manifest(tmp_path, monkeypatch):
    root = _cache(tmp_path)
    home = _home(tmp_path)
    secret = tmp_path / "protected.env"
    secret.write_text("never read this file")
    (home / ".env").symlink_to(secret)
    read = cache_volume._read_metadata

    def guarded_read(path, *args, **kwargs):
        assert Path(path) not in {secret, home / ".env"}
        return read(path, *args, **kwargs)

    monkeypatch.setattr(cache_volume, "_read_metadata", guarded_read)
    readlink = os.readlink

    def guarded_readlink(path, *args, **kwargs):
        assert Path(path) != home / ".env"
        return readlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "readlink", guarded_readlink)
    assert cache_volume.inspect(REPO, REVISION, VOLUME, home, runner=Docker(root))["removable"]
    (home / "operator-topology.toml").write_text('manifest = ".env"\n')
    with pytest.raises(cache_volume.ModelCacheVolumeError, match="unsupported format"):
        cache_volume.inspect(REPO, REVISION, VOLUME, home, runner=Docker(root))
    (home / "operator-topology.toml").write_text("[topology]\n")
    (home / "delegated.toml").symlink_to(secret)
    with pytest.raises(cache_volume.ModelCacheVolumeError, match="symlink"):
        cache_volume.inspect(REPO, REVISION, VOLUME, home, runner=Docker(root))
    (home / "delegated.toml").unlink()
    (home / "nested").mkdir()
    (home / "nested" / ".env").symlink_to(secret)
    with pytest.raises(cache_volume.ModelCacheVolumeError, match="symlink"):
        cache_volume.inspect(REPO, REVISION, VOLUME, home, runner=Docker(root))


def test_confirmed_removal_is_exact_and_post_verified(tmp_path):
    docker = Docker(_cache(tmp_path)); home = _home(tmp_path)
    preview = cache_volume.inspect(REPO, REVISION, VOLUME, home, runner=docker)
    result = cache_volume.remove(REPO, REVISION, VOLUME, home,
                                 expected_fingerprint=preview["volume_fingerprint"], writers_quiesced=True,
                                 confirm=True, runner=docker)
    assert result["outcome"] == "removed" and result["removed_volume"] == VOLUME
    assert ["docker", "volume", "rm", VOLUME] in docker.calls
    assert not docker.root.parent.exists()
    assert isinstance(result["actual_free_space_delta_bytes"], int)


def test_generic_hf_fence_retains_native_writer_lock_through_removal(tmp_path, monkeypatch):
    root = _cache(tmp_path)
    docker = Docker(root)
    home = _home(tmp_path)
    preview = cache_volume.inspect(REPO, REVISION, VOLUME, home, runner=docker)
    verified = []
    original = cache_volume.model_cache_native._verify_hf_lock_fence

    def verify(hub_fd, lock_root, lock_dirs, lock_files):
        assert any(relative == ".locks/anvil-serving/native-pull.lock" for _fd, relative, _binding in lock_files)
        original(hub_fd, lock_root, lock_dirs, lock_files)
        verified.append(True)

    monkeypatch.setattr(cache_volume.model_cache_native, "_verify_hf_lock_fence", verify)
    attempted = []

    def runner(argv, **kwargs):
        if argv[1:3] == ["volume", "rm"]:
            assert verified
            with pytest.raises(cache_volume.model_pull_native.NativePullError, match="already in progress"):
                with cache_volume.model_pull_native._writer(root / "hub", "other--repo"):
                    pytest.fail("a competing native writer entered during volume removal")
            attempted.append(True)
        return docker(argv, **kwargs)

    result = cache_volume.remove(REPO, REVISION, VOLUME, home,
                                 expected_fingerprint=preview["volume_fingerprint"], writers_quiesced=True,
                                 confirm=True, runner=runner)
    assert result["outcome"] == "removed"
    assert attempted == [True]


def test_cache_fingerprint_detects_changed_global_blob(tmp_path):
    root = _cache(tmp_path); docker = Docker(root); home = _home(tmp_path)
    preview = cache_volume.inspect(REPO, REVISION, VOLUME, home, runner=docker)
    blob = next((root / "hub" / "blobs").rglob("[0-9a-f]" * 64))
    blob.write_bytes(b"other")  # Same size: counts/total bytes cannot detect this.
    result = cache_volume.remove(REPO, REVISION, VOLUME, home,
                                 expected_fingerprint=preview["volume_fingerprint"], writers_quiesced=True,
                                 confirm=True, runner=docker)
    assert result["outcome"] == "identity-drift"
    assert not any(call[1:3] == ["volume", "rm"] for call in docker.calls)


def test_blob_lock_is_fenced_and_checked_before_removal(tmp_path, monkeypatch):
    docker = Docker(_cache(tmp_path)); home = _home(tmp_path)
    preview = cache_volume.inspect(REPO, REVISION, VOLUME, home, runner=docker)
    verified = []
    original = cache_volume._verify_blob_lock_fence
    monkeypatch.setattr(cache_volume, "_verify_blob_lock_fence", lambda records: (verified.append(bool(records)), original(records)))
    result = cache_volume.remove(REPO, REVISION, VOLUME, home,
                                 expected_fingerprint=preview["volume_fingerprint"], writers_quiesced=True,
                                 confirm=True, runner=docker)
    assert result["outcome"] == "removed" and verified == [True]


def test_timeout_never_retries_volume_removal(tmp_path):
    docker = Docker(_cache(tmp_path), timeout=True); home = _home(tmp_path)
    preview = cache_volume.inspect(REPO, REVISION, VOLUME, home, runner=docker)
    result = cache_volume.remove(REPO, REVISION, VOLUME, home,
                                 expected_fingerprint=preview["volume_fingerprint"], writers_quiesced=True,
                                 confirm=True, runner=docker)
    assert result["outcome"] == "unknown"
    assert sum(call[1:3] == ["volume", "rm"] for call in docker.calls) == 1


def test_cli_discard_requires_explicit_preview_contract(monkeypatch, capsys):
    monkeypatch.setattr(models.model_cache_volume, "remove", lambda *_args, **_kwargs: pytest.fail("must not remove"))
    assert models.cache_remove_main([REPO, "--revision", REVISION, "--discard-volume", "--volume", VOLUME,
                                     "--config-home", "/tmp/operator", "--confirm"]) == 2
    assert "expected-volume-fingerprint" in capsys.readouterr().err


def test_real_layout_auxiliary_inventory_requires_review_and_is_content_private(tmp_path):
    root = _cache(tmp_path)
    _auxiliary_cache(root)
    docker = Docker(root)
    home = _home(tmp_path)
    preview = cache_volume.inspect(REPO, REVISION, VOLUME, home, runner=docker)
    auxiliary = preview["auxiliary_cache"]
    assert auxiliary["module_revision"] == REVISION
    assert auxiliary["kernel_miss_caches"] == [{"repo_id": "org/example-kernel", "ref_revision": "d" * 40,
                                                "miss_revisions": ["c" * 40, "d" * 40]}]
    assert auxiliary["file_count"] == 14 and auxiliary["logical_bytes"] > 0
    assert "private-log-content" not in json.dumps(preview)
    kwargs = {"expected_fingerprint": preview["volume_fingerprint"], "writers_quiesced": True,
              "confirm": True, "runner": docker}
    with pytest.raises(cache_volume.ModelCacheVolumeError, match="auxiliary-caches-reviewed"):
        cache_volume.remove(REPO, REVISION, VOLUME, home, **kwargs)
    assert not docker.removed
    assert cache_volume.remove(REPO, REVISION, VOLUME, home, auxiliary_caches_reviewed=True, **kwargs)["outcome"] == "removed"
