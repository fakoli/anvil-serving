"""Filesystem-only inspection of a standard native Hugging Face hub cache."""

from __future__ import annotations

import os
import json
import subprocess

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


def _hf_lock(cache, name="blob.lock"):
    directory = cache / "hub" / ".locks" / "models--example--model"
    directory.mkdir(parents=True)
    lock = directory / name
    lock.write_bytes(b"")
    return lock


def test_native_remove_accepts_persistent_hf_locks_and_holds_their_fence(tmp_path, monkeypatch):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    if not _has_nofollow() or not hasattr(os, "O_DIRECTORY"):
        pytest.skip("native removal requires no-follow directory descriptors")
    lock = _hf_lock(cache)
    plan = native.removal_plan(cache, "example/model", REVISION_A)
    original = native._remove_tree

    def lock_is_held(descriptor):
        import fcntl

        contender = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(contender)
        original(descriptor)

    monkeypatch.setattr(native, "_remove_tree", lock_is_held)
    native.remove(cache, "example/model", REVISION_A, expected_plan=plan)
    assert lock.is_file()
    assert not (repo / "snapshots" / REVISION_A).exists()


def test_native_remove_refuses_held_hf_lock_before_deletion(tmp_path):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    if not _has_nofollow() or not hasattr(os, "O_DIRECTORY"):
        pytest.skip("native removal requires no-follow directory descriptors")
    lock = _hf_lock(cache)
    plan = native.removal_plan(cache, "example/model", REVISION_A)
    import fcntl

    descriptor = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(native.NativeCacheError, match="active Hugging Face lock"):
            native.remove(cache, "example/model", REVISION_A, expected_plan=plan)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    assert (repo / "snapshots" / REVISION_A).is_dir()
    assert (repo / "blobs" / "first").is_file()


def test_native_remove_preserves_new_sharer_added_during_removal(tmp_path, monkeypatch):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    if not _has_nofollow() or not hasattr(os, "O_DIRECTORY"):
        pytest.skip("native removal requires no-follow directory descriptors")
    plan = native.removal_plan(cache, "example/model", REVISION_A)
    original = native._remove_tree

    def add_sharer(descriptor):
        snapshot = repo / "snapshots" / ("c" * 40)
        snapshot.mkdir()
        os.symlink("../../blobs/first", snapshot / "first")
        original(descriptor)

    monkeypatch.setattr(native, "_remove_tree", add_sharer)
    native.remove(cache, "example/model", REVISION_A, expected_plan=plan)
    assert (repo / "blobs" / "first").is_file()


def test_native_guard_semantically_audits_compose_binds_without_environment_resolution(tmp_path):
    from anvil_serving import models

    cache, _repo = _cache(tmp_path)
    home = tmp_path / "operator-home"
    home.mkdir()
    compose = home / "candidate.yaml"
    compose.write_text(
        "services:\n  app:\n    command: --topology topology.toml\n"
        "    environment:\n      API_TOKEN: ${API_TOKEN}\n",
        encoding="utf-8",
    )
    payload = {"services": {"app": {
        "command": "--topology topology.toml",
        "environment": {"API_TOKEN": "${API_TOKEN}"},
        "volumes": [],
    }}}
    calls = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        if argv[2:4] == ["ls", "--all"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1:2] == ["compose"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        pytest.fail("unexpected command: %r" % argv)

    models._native_cache_guards(cache, home, _run=runner)
    compose_call = next(call for call in calls if call[1:2] == ["compose"])
    assert compose_call[1:] == [
        "compose", "--env-file", os.devnull, "--project-directory", str(home),
        "--file", str(compose), "config", "--format", "json", "--no-interpolate",
        "--no-env-resolution", "--no-path-resolution",
    ]

    payload["services"]["app"]["volumes"] = [{
        "type": "bind", "source": str(cache), "target": "/cache",
    }]
    with pytest.raises(ValueError, match="current operator configuration"):
        models._native_cache_guards(cache, home, _run=runner)

    payload["services"]["app"]["volumes"] = [{
        "type": "bind", "source": "${CACHE_ROOT}", "target": "/cache",
    }]
    with pytest.raises(ValueError, match="configuration audit"):
        models._native_cache_guards(cache, home, _run=runner)


@pytest.mark.parametrize("dependency_key", [
    "manifest_path", "serves_manifest", "services_manifest",
])
def test_native_guard_refuses_symlinked_config_paths_and_archive_dependencies(tmp_path, dependency_key):
    from anvil_serving import models

    cache, _repo = _cache(tmp_path)
    home = tmp_path / "operator-home"
    home.mkdir()
    alias = tmp_path / "cache-alias"
    os.symlink(cache, alias)
    (home / "live.json").write_text(
        json.dumps({"cache_dir": str(alias)}), encoding="utf-8"
    )
    def empty(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, "", "")
    with pytest.raises(ValueError, match="current operator configuration"):
        models._native_cache_guards(cache, home, _run=empty)

    (home / "live.json").unlink()
    archive = home / "recovered-configs"
    archive.mkdir()
    (archive / "serves.toml").write_text('cache_dir = "/unused"\n', encoding="utf-8")
    (home / "voice.toml").write_text(
        '[stt]\n%s = "recovered-configs/serves.toml"\n' % dependency_key, encoding="utf-8"
    )
    with pytest.raises(ValueError, match="dependency audit"):
        models._native_cache_guards(cache, home, _run=empty)


def test_native_guard_resolves_relative_config_paths_and_refuses_unknown_values(tmp_path):
    from anvil_serving import models

    cache, _repo = _cache(tmp_path)
    home = tmp_path / "operator-home"
    home.mkdir()

    def empty(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, "", "")

    alias = tmp_path / "cache-alias"
    os.symlink(cache, alias)
    config = home / "live.json"
    config.write_text(json.dumps({"cache_dir": "../cache-alias"}), encoding="utf-8")
    with pytest.raises(ValueError, match="current operator configuration"):
        models._native_cache_guards(cache, home, _run=empty)

    alias.unlink()
    (home / "unrelated").mkdir()
    config.write_text(json.dumps({"cache_dir": "unrelated/models"}), encoding="utf-8")
    models._native_cache_guards(cache, home, _run=empty)

    config.write_text(json.dumps({"cache_dir": "${CACHE_ROOT}"}), encoding="utf-8")
    with pytest.raises(ValueError, match="unresolved path"):
        models._native_cache_guards(cache, home, _run=empty)


def test_native_guard_permits_only_a_declared_non_model_readonly_root_view(tmp_path):
    from anvil_serving import models

    cache, _repo = _cache(tmp_path)
    home = tmp_path / "operator-home"
    home.mkdir()
    container_id = "a" * 64
    row = {
        "Id": container_id,
        "HostConfig": {"DeviceRequests": []},
        "Mounts": [{
            "Type": "bind", "Source": os.path.sep, "RW": False,
            "Destination": "/host/root",
        }],
    }

    def runner(argv, **_kwargs):
        if argv[2:4] == ["ls", "--all"]:
            return subprocess.CompletedProcess(argv, 0, container_id + "\n", "")
        if argv[2:3] == ["inspect"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps([row]), "")
        pytest.fail("unexpected command: %r" % argv)

    with pytest.raises(ValueError, match="referenced"):
        models._native_cache_guards(cache, home, _run=runner)
    models._native_cache_guards(
        cache, home, non_model_root_views=[container_id], _run=runner,
    )
    with pytest.raises(ValueError, match="absent"):
        models._native_cache_guards(
            cache, home, non_model_root_views=["b" * 64], _run=runner,
        )

    row["Mounts"][0]["RW"] = True
    with pytest.raises(ValueError, match="referenced|declared"):
        models._native_cache_guards(
            cache, home, non_model_root_views=[container_id], _run=runner,
        )
    row["Mounts"][0]["RW"] = False
    row["HostConfig"]["DeviceRequests"] = [{}]
    with pytest.raises(ValueError, match="GPU"):
        models._native_cache_guards(
            cache, home, non_model_root_views=[container_id], _run=runner,
        )
    row["HostConfig"]["DeviceRequests"] = []
    row["Mounts"].append({
        "Type": "bind", "Source": str(cache), "RW": False, "Destination": "/cache",
    })
    with pytest.raises(ValueError, match="referenced"):
        models._native_cache_guards(
            cache, home, non_model_root_views=[container_id], _run=runner,
        )


def test_native_guard_blocks_resolved_docker_bind_alias_to_cache(tmp_path):
    from anvil_serving import models

    cache, _repo = _cache(tmp_path)
    home = tmp_path / "operator-home"
    home.mkdir()
    alias = tmp_path / "cache-alias"
    os.symlink(cache, alias)

    def mounted(argv, **_kwargs):
        if argv[2:4] == ["ls", "--all"]:
            return subprocess.CompletedProcess(argv, 0, "container-id\n", "")
        return subprocess.CompletedProcess(argv, 0, json.dumps([{
            "Id": "container-id", "Mounts": [{"Type": "bind", "Source": str(alias)}],
        }]), "")

    with pytest.raises(ValueError, match="referenced"):
        models._native_cache_guards(cache, home, _run=mounted)


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


@pytest.mark.parametrize("force_missing_nofollow", (False, True))
def test_cli_native_inventory_and_removal_plan_do_not_use_docker(
    tmp_path, monkeypatch, capsys, force_missing_nofollow
):
    from anvil_serving import models

    cache, _repo = _cache(tmp_path)
    if force_missing_nofollow:
        monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    monkeypatch.setattr(models, "cache_inventory", lambda **_kwargs: pytest.fail("Docker must not run"))
    assert models.cache_inventory_main(["--cache-dir", str(cache)]) == 0
    assert '"schema_version": "model-cache-native-inventory/v1"' in capsys.readouterr().out

    result = models.cache_remove_main([
        "example/model", "--revision", REVISION_A, "--cache-dir", str(cache), "--dry-run",
    ])
    output = capsys.readouterr()
    if _has_nofollow():
        assert result == 0
        assert "NATIVE MODEL CACHE REMOVE PLAN" in output.out
    else:
        assert result == 1
        assert "unsafe or unsupported topology" in output.err
        assert "NATIVE MODEL CACHE REMOVE PLAN" not in output.out

    expected = 3 if _has_nofollow() else 1
    assert models.cache_remove_main([
        "example/model", "--revision", REVISION_A, "--cache-dir", str(cache),
    ]) == expected
    assert ("--confirm" if _has_nofollow() else "unsafe") in capsys.readouterr().err

    assert models.cache_remove_main([
        "example/model", "--revision", REVISION_A, "--cache-dir", str(cache), "--confirm",
    ]) == (2 if _has_nofollow() else 1)
    assert ("requires --config-home" if _has_nofollow() else "unsafe") in capsys.readouterr().err
