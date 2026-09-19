"""Filesystem-only inspection of a standard native Hugging Face hub cache."""

from __future__ import annotations

import os

import pytest


REVISION_A = "a" * 40
REVISION_B = "b" * 40


def _has_nofollow() -> bool:
    return hasattr(os, "O_NOFOLLOW")


def _allocated(path) -> int | None:
    blocks = getattr(os.lstat(path), "st_blocks", None)
    return int(blocks) * 512 if isinstance(blocks, int) and blocks >= 0 else None


def _cache(tmp_path):
    cache = tmp_path / "cache"
    repo = cache / "hub" / "models--example--model"
    blobs = repo / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "first").write_bytes(b"first")
    (blobs / "shared").write_bytes(b"shared!")
    for revision, names in ((REVISION_A, ("first", "shared")), (REVISION_B, ("shared",))):
        snapshot = repo / "snapshots" / revision
        snapshot.mkdir(parents=True)
        for name in names:
            os.symlink("../../blobs/" + name, snapshot / name)
    refs = repo / "refs"
    refs.mkdir()
    (refs / "main").write_text(REVISION_A + "\n", encoding="utf-8")
    return cache, repo


def test_inventory_reports_local_link_integrity_and_physical_blob_bytes(tmp_path):
    from anvil_serving import model_cache_native as native

    cache, _repo = _cache(tmp_path)
    report = native.inventory(cache)

    assert report["schema_version"] == "model-cache-native-inventory/v1"
    row = report["repositories"][0]
    assert row["repo_id"] == "example/model"
    if _has_nofollow():
        assert row["refs"] == {"main": REVISION_A}
    else:
        assert row["refs"] == {}
        assert "refs/main" in row["unsafe_paths"]
    assert row["unique_logical_blob_bytes"] == len(b"firstshared!")
    allocated = [_allocated(_repo / "blobs" / name) for name in ("first", "shared")]
    if all(size is not None for size in allocated):
        assert row["unique_allocated_blob_bytes"] == sum(allocated)
        assert row["allocation_status"] == "known"
    else:
        assert row["unique_allocated_blob_bytes"] is None
        assert row["allocation_status"] == "unknown"
    assert row["unsafe_paths"] == ([] if _has_nofollow() else ["refs/main"])
    snapshots = {item["revision"]: item for item in row["snapshots"]}
    assert snapshots[REVISION_A]["logical_bytes"] == len(b"firstshared!")
    assert snapshots[REVISION_A]["local_link_integrity"] == "valid"
    assert snapshots[REVISION_A]["artifact_completeness"] == "unverified"
    assert snapshots[REVISION_B]["logical_bytes"] == len(b"shared!")
    assert report["disk"]["capacity_bytes"] >= report["disk"]["used_bytes"]


def test_exact_removal_plan_separates_exclusive_and_shared_blobs(tmp_path):
    from anvil_serving import model_cache_native as native

    cache, _repo = _cache(tmp_path)
    if not _has_nofollow():
        with pytest.raises(native.NativeCacheError, match="unsafe"):
            native.removal_plan(cache, "example/model", REVISION_A)
        return
    plan = native.removal_plan(cache, "example/model", REVISION_A)

    assert plan["snapshot_exists"] is True
    assert plan["snapshot_logical_bytes"] == len(b"firstshared!")
    assert plan["exclusive_blob_names"] == ["first"]
    assert plan["shared_blob_names"] == ["shared"]
    assert plan["reclaimable_bytes"] == _allocated(_repo / "blobs" / "first")
    assert plan["artifact_completeness"] == "unverified"
    assert plan["refs_to_remove"] == ["main"]
    assert plan["apply"] == "unsupported_native_cache_ownership"


def test_inventory_marks_broken_and_escaping_snapshot_links_unsafe(tmp_path):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    snapshot = repo / "snapshots" / REVISION_A
    os.symlink("../../blobs/missing", snapshot / "broken")
    os.symlink("../../../../outside", snapshot / "escape")

    row = native.inventory(cache)["repositories"][0]
    record = next(item for item in row["snapshots"] if item["revision"] == REVISION_A)
    assert record["local_link_integrity"] == "unsafe"
    assert "snapshots/%s/broken" % REVISION_A in record["unsafe_paths"]
    assert "snapshots/%s/escape" % REVISION_A in record["unsafe_paths"]
    with pytest.raises(native.NativeCacheError, match="unsafe"):
        native.removal_plan(cache, "example/model", REVISION_A)


def test_symlinked_cache_root_or_metadata_is_refused(tmp_path):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    linked_root = tmp_path / "linked-cache"
    os.symlink(cache, linked_root)
    with pytest.raises(native.NativeCacheError, match="symlink"):
        native.inventory(linked_root)

    linked_hub = tmp_path / "linked-hub-cache"
    linked_hub.mkdir()
    os.symlink(cache / "hub", linked_hub / "hub")
    with pytest.raises(native.NativeCacheError, match="symlink"):
        native.inventory(linked_hub)

    (repo / "refs" / "main").unlink()
    os.symlink("../snapshots/" + REVISION_A, repo / "refs" / "main")
    row = native.inventory(cache)["repositories"][0]
    assert "refs/main" in row["unsafe_paths"]
    with pytest.raises(native.NativeCacheError, match="unsafe"):
        native.removal_plan(cache, "example/model", REVISION_A)


def test_removal_plan_refuses_malformed_repository_or_revision(tmp_path):
    from anvil_serving import model_cache_native as native

    cache, _repo = _cache(tmp_path)
    with pytest.raises(native.NativeCacheError, match="exact OWNER/REPO"):
        native.removal_plan(cache, "example/model/extra", REVISION_A)
    with pytest.raises(native.NativeCacheError, match="40 lowercase"):
        native.removal_plan(cache, "example/model", "main")
    with pytest.raises(native.NativeCacheError, match="exact OWNER/REPO"):
        native.removal_plan(cache, "example--alias/model", REVISION_A)


def test_nonstandard_layout_is_disclosed_without_following_it(tmp_path):
    from anvil_serving import model_cache_native as native

    cache = tmp_path / "cache"
    cache.mkdir()
    row = native.inventory(cache)
    assert row["layout"] == "unknown"
    assert row["repositories"] == []


def test_direct_hub_and_pinned_cache_without_refs_are_standard(tmp_path):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    for item in (repo / "refs").iterdir():
        item.unlink()
    (repo / "refs").rmdir()
    (cache / "hub" / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")
    (cache / "hub" / ".locks").mkdir()

    report = native.inventory(cache / "hub")
    assert report["layout"] == "standard"
    assert report["unrecognized_entries"] == []
    assert report["repositories"][0]["refs"] == {}


def test_custom_direct_hub_cache_root_and_ambiguous_home_are_refused(tmp_path):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    custom = tmp_path / "custom-hf-cache"
    custom.mkdir()
    os.replace(cache / "hub" / "models--example--model", custom / "models--example--model")
    report = native.inventory(custom)
    assert report["repositories"][0]["repo_id"] == "example/model"
    if _has_nofollow():
        assert native.removal_plan(custom, "example/model", REVISION_A)["snapshot_exists"] is True
    else:
        with pytest.raises(native.NativeCacheError, match="unsafe"):
            native.removal_plan(custom, "example/model", REVISION_A)

    (custom / "hub").mkdir()
    with pytest.raises(native.NativeCacheError, match="ambiguous"):
        native.inventory(custom)


def test_partial_snapshot_does_not_claim_upstream_completeness(tmp_path):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    (repo / "snapshots" / REVISION_A / "shared").unlink()
    snapshot = next(
        item for item in native.inventory(cache)["repositories"][0]["snapshots"]
        if item["revision"] == REVISION_A
    )
    assert snapshot["local_link_integrity"] == "valid"
    assert snapshot["artifact_completeness"] == "unverified"
    assert snapshot["logical_bytes"] == len(b"first")


def test_hardlinked_blob_names_are_inode_deduplicated(tmp_path):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    os.link(repo / "blobs" / "first", repo / "blobs" / "first-alias")
    row = native.inventory(cache)["repositories"][0]
    allocated = [_allocated(repo / "blobs" / name) for name in ("first", "shared")]
    if all(size is not None for size in allocated):
        assert row["unique_allocated_blob_bytes"] == sum(allocated)
    else:
        assert row["unique_allocated_blob_bytes"] is None
    assert "blobs/first" in row["unsafe_paths"]
    assert "blobs/first-alias" in row["unsafe_paths"]
    with pytest.raises(native.NativeCacheError, match="unsafe"):
        native.removal_plan(cache, "example/model", REVISION_A)


def test_external_hardlinks_and_ref_hardlinks_are_unsafe_for_removal(tmp_path):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    os.link(repo / "blobs" / "first", tmp_path / "external-blob-link")
    row = native.inventory(cache)["repositories"][0]
    assert "blobs/first" in row["unsafe_paths"]
    with pytest.raises(native.NativeCacheError, match="unsafe"):
        native.removal_plan(cache, "example/model", REVISION_A)

    (tmp_path / "external-blob-link").unlink()
    os.link(repo / "refs" / "main", repo / "refs" / "alias")
    row = native.inventory(cache)["repositories"][0]
    assert "refs/main" in row["unsafe_paths"]
    assert "refs/alias" in row["unsafe_paths"]
    with pytest.raises(native.NativeCacheError, match="unsafe"):
        native.removal_plan(cache, "example/model", REVISION_A)


def test_inventory_refuses_unbounded_cache_walk(tmp_path, monkeypatch):
    from anvil_serving import model_cache_native as native

    cache, _repo = _cache(tmp_path)
    monkeypatch.setattr(native, "_MAX_SCAN_ENTRIES", 1)
    with pytest.raises(native.NativeCacheError, match="entry limit"):
        native.inventory(cache)


def test_inventory_refuses_excessive_depth_or_elapsed_scan(tmp_path, monkeypatch):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    (repo / "snapshots" / REVISION_A / "nested").mkdir()
    monkeypatch.setattr(native, "_MAX_SCAN_DEPTH", 0)
    with pytest.raises(native.NativeCacheError, match="depth limit"):
        native.inventory(cache)

    monkeypatch.setattr(native, "_MAX_SCAN_DEPTH", 32)
    monkeypatch.setattr(native, "_MAX_SCAN_SECONDS", -1.0)
    with pytest.raises(native.NativeCacheError, match="timed out"):
        native.inventory(cache)


def test_inventory_marks_top_level_metadata_error_unknown(tmp_path, monkeypatch):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    original_lstat = type(repo).lstat

    def denied_lstat(path):
        if path == repo:
            raise OSError("denied fixture")
        return original_lstat(path)

    monkeypatch.setattr(type(repo), "lstat", denied_lstat)
    report = native.inventory(cache)
    assert report["layout"] == "unknown"
    assert "hub/models--example--model" in report["unsafe_paths"]


def test_missing_nofollow_capability_fails_closed_and_preserves_allocation_unknown(
    tmp_path, monkeypatch
):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    monkeypatch.delattr(native.os, "O_NOFOLLOW", raising=False)
    report = native.inventory(cache)
    row = report["repositories"][0]
    assert row["refs"] == {}
    assert "refs/main" in row["unsafe_paths"]
    with pytest.raises(native.NativeCacheError, match="unsafe"):
        native.removal_plan(cache, "example/model", REVISION_A)

    class NoBlocks:
        st_size = 7
        st_dev = 1
        st_ino = 2
        st_nlink = 1

    assert native._blob_size(NoBlocks())["allocated_bytes"] is None


def test_cli_native_inventory_and_removal_plan_do_not_use_docker(tmp_path, monkeypatch, capsys):
    from anvil_serving import models

    cache, _repo = _cache(tmp_path)
    monkeypatch.setattr(models, "cache_inventory", lambda **_kwargs: pytest.fail("Docker must not run"))
    assert models.cache_inventory_main(["--cache-dir", str(cache)]) == 0
    assert '"schema_version": "model-cache-native-inventory/v1"' in capsys.readouterr().out

    assert models.cache_remove_main([
        "example/model", "--revision", REVISION_A, "--cache-dir", str(cache), "--dry-run",
    ]) == 0
    assert "NATIVE MODEL CACHE REMOVE PLAN" in capsys.readouterr().out

    assert models.cache_remove_main([
        "example/model", "--revision", REVISION_A, "--cache-dir", str(cache),
    ]) == 2
    assert "preview-only" in capsys.readouterr().err

    assert models.cache_remove_main([
        "example/model", "--revision", REVISION_A, "--cache-dir", str(cache), "--confirm",
    ]) == 2
    assert "apply is unsupported" in capsys.readouterr().err


@pytest.mark.parametrize("extra", ((), ("--dry-run",)))
def test_dispatcher_forwards_native_confirm_and_refuses_before_inspection(
    tmp_path, monkeypatch, capsys, extra
):
    from anvil_serving import cli
    from anvil_serving import model_cache_native as native

    monkeypatch.setattr(
        native,
        "removal_plan",
        lambda *_args, **_kwargs: pytest.fail("native inspection ran after --confirm"),
    )
    assert cli.main([
        "models", "cache", "remove", "example/model", "--revision", REVISION_A,
        "--cache-dir", str(tmp_path / "not-inspected"), *extra, "--confirm",
    ]) == 2
    assert "apply is unsupported" in capsys.readouterr().err
