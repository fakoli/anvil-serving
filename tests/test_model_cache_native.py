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
    assert plan["apply"] == "requires_confirmed_guarded_native_removal"


def test_native_remove_keeps_shared_blobs_and_other_revision(tmp_path):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    if not _has_nofollow() or not hasattr(os, "O_DIRECTORY"):
        pytest.skip("native removal requires no-follow directory descriptors")
    plan = native.removal_plan(cache, "example/model", REVISION_A)
    result = native.remove(cache, "example/model", REVISION_A, expected_plan=plan)

    assert result["outcome"] == "removed"
    assert not (repo / "snapshots" / REVISION_A).exists()
    assert not (repo / "blobs" / "first").exists()
    assert (repo / "snapshots" / REVISION_B).is_dir()
    assert (repo / "blobs" / "shared").is_file()
    assert not (repo / "refs" / "main").exists()


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


@pytest.mark.parametrize("unsafe", ("symlink", "fifo"))
def test_native_remove_refuses_unsafe_hf_lock_entries(tmp_path, unsafe):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    if not _has_nofollow() or not hasattr(os, "O_DIRECTORY"):
        pytest.skip("native removal requires no-follow directory descriptors")
    lock = _hf_lock(cache)
    if unsafe == "symlink":
        lock.unlink()
        os.symlink(repo / "blobs" / "first", lock)
    else:
        lock.unlink()
        os.mkfifo(lock)
    plan = native.removal_plan(cache, "example/model", REVISION_A)
    with pytest.raises(native.NativeCacheError, match="unsafe Hugging Face lock topology"):
        native.remove(cache, "example/model", REVISION_A, expected_plan=plan)
    assert (repo / "snapshots" / REVISION_A).is_dir()
    assert (repo / "blobs" / "first").is_file()


def test_native_remove_refuses_hf_lock_replacement_before_deletion(tmp_path, monkeypatch):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    if not _has_nofollow() or not hasattr(os, "O_DIRECTORY"):
        pytest.skip("native removal requires no-follow directory descriptors")
    lock = _hf_lock(cache)
    plan = native.removal_plan(cache, "example/model", REVISION_A)
    original = native._verify_hf_lock_fence

    def replace_then_verify(*args):
        replacement = lock.with_name("replacement.lock")
        replacement.write_bytes(b"successor")
        os.replace(replacement, lock)
        original(*args)

    monkeypatch.setattr(native, "_verify_hf_lock_fence", replace_then_verify)
    with pytest.raises(native.NativeCacheError, match="lock tree changed"):
        native.remove(cache, "example/model", REVISION_A, expected_plan=plan)
    assert (repo / "snapshots" / REVISION_A).is_dir()
    assert (repo / "blobs" / "first").is_file()


def test_native_remove_refuses_a_changed_plan_before_deletion(tmp_path):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    if not _has_nofollow() or not hasattr(os, "O_DIRECTORY"):
        pytest.skip("native removal requires no-follow directory descriptors")
    plan = native.removal_plan(cache, "example/model", REVISION_A)
    (repo / "blobs" / "new").write_bytes(b"new")
    os.symlink("../../blobs/new", repo / "snapshots" / REVISION_A / "new")

    with pytest.raises(native.NativeCacheError, match="plan changed"):
        native.remove(cache, "example/model", REVISION_A, expected_plan=plan)
    assert (repo / "snapshots" / REVISION_A).is_dir()
    assert (repo / "blobs" / "first").is_file()


def test_native_remove_refuses_same_name_blob_replacement(tmp_path):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    if not _has_nofollow() or not hasattr(os, "O_DIRECTORY"):
        pytest.skip("native removal requires no-follow directory descriptors")
    plan = native.removal_plan(cache, "example/model", REVISION_A)
    replacement = repo / "blobs" / "replacement"
    replacement.write_bytes(b"replacement")
    os.replace(replacement, repo / "blobs" / "first")

    with pytest.raises(native.NativeCacheError, match="plan changed"):
        native.remove(cache, "example/model", REVISION_A, expected_plan=plan)
    assert (repo / "blobs" / "first").read_bytes() == b"replacement"


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


def test_native_remove_refuses_late_blob_replacement_before_unlink(tmp_path, monkeypatch):
    from anvil_serving import model_cache_native as native

    cache, repo = _cache(tmp_path)
    if not _has_nofollow() or not hasattr(os, "O_DIRECTORY"):
        pytest.skip("native removal requires no-follow directory descriptors")
    plan = native.removal_plan(cache, "example/model", REVISION_A)
    original = native._remove_tree

    def replace_blob(descriptor):
        original(descriptor)
        replacement = repo / "blobs" / "replacement"
        replacement.write_bytes(b"successor")
        os.replace(replacement, repo / "blobs" / "first")

    monkeypatch.setattr(native, "_remove_tree", replace_blob)
    with pytest.raises(native.NativeCacheError, match="blob changed"):
        native.remove(cache, "example/model", REVISION_A, expected_plan=plan)
    assert (repo / "blobs" / "first").read_bytes() == b"successor"


def test_native_guard_rejects_escaped_json_path_and_incomplete_container_audit(tmp_path):
    from anvil_serving import models

    cache, _repo = _cache(tmp_path)
    home = tmp_path / "operator-home"
    home.mkdir()

    def incomplete(argv, **_kwargs):
        if argv[2:4] == ["ls", "--all"]:
            return subprocess.CompletedProcess(argv, 0, "container-id\n", "")
        return subprocess.CompletedProcess(argv, 0, "[{}]", "")

    with pytest.raises(ValueError, match="container"):
        models._native_cache_guards(cache, home, _run=incomplete)
    (home / "live.json").write_text('{"cache_dir":"%s"}' % str(cache).replace("/", "\\u002f"), encoding="utf-8")
    with pytest.raises(ValueError, match="current operator configuration"):
        models._native_cache_guards(cache, home, _run=lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "", ""))
    (home / "live.json").unlink()
    nested = home / "nested" / "recovered-configs"
    nested.mkdir(parents=True)
    (nested / "live.toml").write_text('cache_dir = "%s"\n' % cache, encoding="utf-8")
    with pytest.raises(ValueError, match="current operator configuration"):
        models._native_cache_guards(cache, home, _run=lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "", ""))


def test_native_apply_guards_block_mounts_and_live_config_references(tmp_path):
    from anvil_serving import models

    cache, _repo = _cache(tmp_path)
    home = tmp_path / "operator-home"
    home.mkdir()

    def mounted(argv, **_kwargs):
        if argv[2:4] == ["ls", "--all"]:
            return subprocess.CompletedProcess(argv, 0, "container-id\n", "")
        return subprocess.CompletedProcess(argv, 0, '[{"Mounts":[{"Type":"bind","Source":"%s"}]}]' % cache, "")

    with pytest.raises(ValueError, match="Docker container"):
        models._native_cache_guards(cache, home, _run=mounted)

    def empty(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, "", "")

    (home / "live.toml").write_text('cache_dir = "%s"\n' % cache.parent, encoding="utf-8")
    with pytest.raises(ValueError, match="current operator configuration"):
        models._native_cache_guards(cache, home, _run=empty)


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


def test_native_guard_refuses_malformed_compose_yaml(tmp_path):
    from anvil_serving import models

    cache, _repo = _cache(tmp_path)
    home = tmp_path / "operator-home"
    home.mkdir()
    (home / "candidate.yaml").write_text("services: [\n", encoding="utf-8")

    def runner(argv, **_kwargs):
        if argv[2:4] == ["ls", "--all"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1:2] == ["compose"]:
            return subprocess.CompletedProcess(argv, 1, "", "invalid compose YAML")
        pytest.fail("unexpected command: %r" % argv)

    with pytest.raises(ValueError, match="configuration audit"):
        models._native_cache_guards(cache, home, _run=runner)


def test_native_guard_semantically_audits_standalone_yaml_paths(tmp_path):
    from anvil_serving import models

    cache, _repo = _cache(tmp_path)
    home = tmp_path / "operator-home"
    home.mkdir()
    config = home / "application.yaml"
    config.write_text("application:\n  auth: ${UNRESOLVED_SECRET}\n", encoding="utf-8")
    payload = {"x-native-cache-audit": {"application": {"auth": "${UNRESOLVED_SECRET}"}}, "services": {}}
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[2:4] == ["ls", "--all"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1:2] == ["compose"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        pytest.fail("unexpected command: %r" % argv)

    models._native_cache_guards(cache, home, _run=runner)
    compose_call, compose_kwargs = next(call for call in calls if call[0][1:2] == ["compose"])
    assert compose_call[compose_call.index("--file") + 1] == "-"
    assert compose_kwargs["input"].startswith("x-native-cache-audit:\n  application:")

    config.write_text('"cache\\u005fdir": ignored\n', encoding="utf-8")
    payload["x-native-cache-audit"] = {"cache_dir": str(cache)}
    with pytest.raises(ValueError, match="current operator configuration"):
        models._native_cache_guards(cache, home, _run=runner)

    payload["x-native-cache-audit"] = {"cache_dir": "${CACHE_ROOT}"}
    with pytest.raises(ValueError, match="unresolved path"):
        models._native_cache_guards(cache, home, _run=runner)

    def malformed(argv, **_kwargs):
        if argv[2:4] == ["ls", "--all"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1:2] == ["compose"]:
            return subprocess.CompletedProcess(argv, 1, "", "invalid YAML")
        pytest.fail("unexpected command: %r" % argv)

    with pytest.raises(ValueError, match="configuration audit"):
        models._native_cache_guards(cache, home, _run=malformed)


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


@pytest.mark.parametrize("mount", [
    {"Type": "bind", "Source": "relative-cache"},
    {"Type": "bind", "Source": ""},
    {"Type": "mystery", "Source": "/plain/path"},
])
def test_native_guard_refuses_malformed_or_unknown_docker_mounts(tmp_path, mount):
    from anvil_serving import models

    cache, _repo = _cache(tmp_path)
    home = tmp_path / "operator-home"
    home.mkdir()

    def mounted(argv, **_kwargs):
        if argv[2:4] == ["ls", "--all"]:
            return subprocess.CompletedProcess(argv, 0, "container-id\n", "")
        return subprocess.CompletedProcess(
            argv, 0, json.dumps([{"Id": "container-id", "Mounts": [mount]}]), ""
        )

    with pytest.raises(ValueError, match="mount|bind mount"):
        models._native_cache_guards(cache, home, _run=mounted)


def test_native_guard_compares_lexical_and_resolved_config_aliases(tmp_path):
    from anvil_serving import models

    cache, _repo = _cache(tmp_path)
    home = tmp_path / "operator-home"
    home.mkdir()

    def empty(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, "", "")

    direct_alias = tmp_path / "cache-alias"
    os.symlink(cache, direct_alias)
    (home / "live.json").write_text(json.dumps({"cache_dir": str(direct_alias)}), encoding="utf-8")
    with pytest.raises(ValueError, match="current operator configuration"):
        models._native_cache_guards(cache, home, _run=empty)

    enclosing_alias = tmp_path / "root-alias"
    os.symlink(tmp_path, enclosing_alias)
    (home / "live.json").write_text(
        json.dumps({"cache_dir": str(enclosing_alias / "cache")}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="current operator configuration"):
        models._native_cache_guards(cache, home, _run=empty)

    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    unrelated_alias = tmp_path / "unrelated-alias"
    os.symlink(unrelated, unrelated_alias)
    (home / "live.json").write_text(
        json.dumps({"cache_dir": str(unrelated_alias / "models")}), encoding="utf-8"
    )
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


def test_native_guard_allows_only_grafana_template_definition_queries(tmp_path):
    from anvil_serving import models

    cache, _repo = _cache(tmp_path)
    home = tmp_path / "operator-home"
    home.mkdir()
    dashboard = home / "dashboard.json"
    dashboard.write_text(json.dumps({
        "schemaVersion": 1, "panels": [],
        "templating": {"list": [{"definition": "label_values(${UNRESOLVED_QUERY})"}]},
    }), encoding="utf-8")

    def empty(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, "", "")

    models._native_cache_guards(cache, home, _run=empty)
    dashboard.write_text(json.dumps({
        "schemaVersion": 1, "panels": [], "cache_dir": str(cache),
        "templating": {"list": [{"definition": "label_values(instance)"}]},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="current operator configuration"):
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


def test_dispatcher_requires_native_config_home_before_guard_audit(tmp_path, monkeypatch, capsys):
    from anvil_serving import cli
    from anvil_serving import model_cache_native as native

    monkeypatch.setattr(
        native,
        "remove",
        lambda *_args, **_kwargs: pytest.fail("native deletion ran without config home"),
    )
    cache, _repo = _cache(tmp_path)
    assert cli.main([
        "models", "cache", "remove", "example/model", "--revision", REVISION_A,
        "--cache-dir", str(cache), "--confirm",
    ]) == 2
    assert "requires --config-home" in capsys.readouterr().err


def test_cli_help_describes_guarded_native_apply(capsys):
    from anvil_serving import cli

    assert cli.main(["models", "cache", "remove", "--help"]) == 0
    output = capsys.readouterr().out
    assert "guarded native-cache revision" in output
    assert "preview native-cache" not in output
    assert "--config-home" in output
    assert "--non-model-root-view" in output
