"""Tests for `anvil_serving.models` — the `models` verb, focused on the new
`models pull` sub-action (download a HF repo into a NAMED docker volume so it
serves natively, avoiding the 9P bind-mount tax — CLAUDE.md gotcha #15).

Every test is HERMETIC: docker is never invoked (subprocess is mocked or we
stay on --dry-run). No real docker, no network.
"""
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest

from anvil_serving import cache_prune, cli, guard, models, serve_recipes
from tests.conftest import enabled_cache_policy as _enabled_cache_policy


@pytest.fixture(autouse=True)
def _isolated_host_policy(monkeypatch, tmp_path):
    """Never let a developer's enabled machine policy affect unit timing."""
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / ".anvil-serving"))


# --------------------------------------------------------------------------- #
# build_pull_argv — the constructed docker command (the load-bearing invariants)
# --------------------------------------------------------------------------- #


def test_pull_argv_supports_explicit_unauthenticated_pull():
    argv = models.build_pull_argv("openai/gpt-oss-120b", token_env=None)
    assert "-e" not in argv
    assert "HF_TOKEN" not in argv


# --------------------------------------------------------------------------- #
# --dry-run — prints the command, runs nothing
# --------------------------------------------------------------------------- #

def test_pull_dry_run_prints_command_and_runs_nothing(capsys):
    called = []
    rc = models.run_pull("openai/gpt-oss-120b", dry_run=True,
                         _run=lambda *a, **k: called.append((a, k)),
                         _environ={"PATH": "/usr/bin"})
    assert rc == 0
    assert called == []  # nothing executed
    out = capsys.readouterr().out
    assert "MODEL ARTIFACT PULL PLAN" in out
    assert "ordered actions:" in out
    assert "deferred until apply:" in out
    assert "rollback: none automatic" in out
    assert "docker run --rm" in out
    assert "-v vllm-hfcache:/root/.cache/huggingface" in out
    assert "--entrypoint hf" in out
    assert "download openai/gpt-oss-120b" in out
    assert "-e HF_TOKEN" in out
    assert "huggingface-cli" not in out


def test_pull_dry_run_via_pull_main_explicit_unauthenticated(capsys):
    rc = models.pull_main(["openai/gpt-oss-120b", "--no-token", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "-v vllm-hfcache:/root/.cache/huggingface" in out
    assert "vllm/vllm-openai:nightly" in out
    assert "--entrypoint hf" in out
    assert "download openai/gpt-oss-120b" in out
    assert "huggingface-cli" not in out
    assert "HF_TOKEN" not in out  # unauthenticated by default
    assert "automatic cache reclaim: disabled" in out


def test_pull_dry_run_json_retains_requested_memory_argv(capsys):
    assert cli.main([
        "models", "pull", "some/repo", "--no-token", "--memory-mib", "2048",
        "--memory-swap-mib", "2048", "--dry-run", "--json",
    ]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert "--memory 2147483648 --memory-swap 2147483648" in data
    assert "requested memory=2147483648 bytes" in data


def test_pull_dry_run_token_env_shows_name_only_not_value(monkeypatch, capsys):
    monkeypatch.setenv("MY_HF_TOKEN", "hf_supersecret_value")
    rc = models.pull_main(["some/repo", "--token-env", "MY_HF_TOKEN", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "-e HF_TOKEN" in out
    assert "hf_supersecret_value" not in out  # the VALUE is never printed
    assert "token environment variable: MY_HF_TOKEN" in out
    assert "token dotenv fallback:" in out


# --------------------------------------------------------------------------- #
# run_pull execution — subprocess wiring, exit-code passthrough, token env
# --------------------------------------------------------------------------- #


def test_pull_space_preflight_counts_only_missing_bytes_and_headroom(monkeypatch):
    monkeypatch.setattr(
        models,
        "_volume_cache_report",
        lambda *args, **kwargs: {"repo_bytes": 60, "free_bytes": 50},
    )
    report = models.preflight_pull(
        "org/model",
        "abc",
        expected_bytes=100,
        headroom_bytes=5,
    )
    assert report["missing_bytes"] == 40
    assert report["required_free_bytes"] == 45
    assert report["space_ok"] is True


def test_volume_report_timeout_is_bounded_and_not_retried():
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    with pytest.raises(ValueError, match="timed out"):
        models._volume_cache_report("report", "org/model", "abc", _run=run)
    assert len(calls) == 1


def _cache_report_from_tree(cache):
    """Run the embedded volume inspector against a temporary cache, never Docker."""
    def run(argv, **_kwargs):
        script = argv[-5]
        action, _mount, repo_id, revision = argv[-4:]
        result = subprocess.run(
            [sys.executable, "-c", script, action, str(cache), repo_id, revision],
            capture_output=True,
            text=True,
            check=False,
        )
        return types.SimpleNamespace(
            returncode=result.returncode, stdout=result.stdout, stderr=result.stderr
        )
    return run


def _complete_partial_cache(
    tmp_path, *, partial_kind="regular", snapshot_partial=False, hash_kind="sha256"
):
    cache = tmp_path / "cache"
    root = cache / "hub" / "models--org--model"
    blobs = root / "blobs"
    snapshot = root / "snapshots" / "revision"
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    content = b"fully downloaded model weight"
    if hash_kind == "sha256":
        digest = hashlib.sha256(content).hexdigest()
    elif hash_kind == "git_blob_sha1":
        digest = hashlib.sha1(
            f"blob {len(content)}\0".encode() + content
        ).hexdigest()
    else:
        raise AssertionError(hash_kind)
    complete = blobs / digest
    complete.write_bytes(content)
    partial = blobs / (digest + ".deadbeef.incomplete")
    if partial_kind == "regular":
        partial.write_bytes(b"abandoned partial")
    elif partial_kind == "symlink":
        partial.symlink_to(complete.name)
    else:
        raise AssertionError(partial_kind)
    target = "../../blobs/" + (partial.name if snapshot_partial else complete.name)
    (snapshot / "weights.safetensors").symlink_to(target)
    return cache, root, complete, partial


@pytest.mark.parametrize("hash_kind", ["sha256", "git_blob_sha1"])
def test_verify_pull_accepts_only_hashed_completed_counterpart(tmp_path, hash_kind):
    cache, _root, _complete, _partial = _complete_partial_cache(
        tmp_path, hash_kind=hash_kind
    )
    report = models.verify_pull(
        "org/model", "revision", _run=_cache_report_from_tree(cache)
    )

    assert report["incomplete_count"] == 1
    assert report["stale_completed_incomplete_count"] == 1
    partial = report["incomplete_files"]
    assert len(partial) == 1
    assert partial[0]["path"].endswith(".deadbeef.incomplete")
    assert partial[0]["expected_blob_name"] == partial[0]["counterpart_path"].split("/")[-1]
    assert partial[0]["bytes"] == len(b"abandoned partial")
    assert partial[0]["counterpart_regular"] is True
    assert partial[0]["counterpart_hash_matches"] is True
    assert partial[0]["selected_snapshot_references"] == []
    assert partial[0]["classification"] == "stale_completed_counterpart"


def test_cache_remove_docker_revision_preview_is_read_only(monkeypatch, capsys):
    calls = []

    def report(action, *args, **kwargs):
        calls.append(action)
        if action == "plan-remove":
            return {
                "snapshot": "abc",
                "snapshot_exists": True,
                "repo_bytes": 100,
                "reclaimable_bytes": 80,
            }
        return {
            "snapshot_exists_after": False,
            "reclaimed_bytes": 80,
            "repo_bytes_after": 20,
        }

    monkeypatch.setattr(models, "_volume_cache_report", report)
    assert models.cache_remove_main(
        ["org/model", "--revision", "abc", "--dry-run"]
    ) == 0
    assert calls == ["plan-remove"]
    assert "estimated reclaimable bytes: 80" in capsys.readouterr().out

    assert models.cache_remove_main(
        ["org/model", "--revision", "abc", "--confirm"]
    ) == 2
    assert calls == ["plan-remove"]
    assert "legacy Docker revision removal is disabled" in capsys.readouterr().err


@pytest.mark.parametrize("surface", ["leaf-confirm", "dispatcher-confirm", "authorization"])
def test_legacy_docker_revision_apply_never_dispatches_even_with_confirmation(monkeypatch, surface):
    monkeypatch.setattr(models, "_volume_cache_report", lambda *_args, **_kwargs: pytest.fail("must not inspect or remove"))
    monkeypatch.setattr(guard, "confirmation_authorized", lambda: surface == "authorization")
    argv = ["org/model", "--revision", "a" * 40, "--volume", "retained-cache"]
    if surface != "authorization": argv.append("--confirm")
    if surface == "dispatcher-confirm":
        assert cli.main(["models", "cache", "remove", *argv]) != 0
    else:
        assert models.cache_remove_main(argv) == 2


def test_whole_cache_discard_requires_posix_before_audit(monkeypatch, capsys):
    from anvil_serving import model_cache_discard
    monkeypatch.setattr(model_cache_discard.volume_ops, "fcntl", None)
    monkeypatch.setattr(model_cache_discard.volume_ops, "_config_home",
                        lambda *_args: pytest.fail("unsupported platform must not audit state"))
    assert model_cache_discard.main([
        "--volume", "retired", "--config-home", "operator", "--confirm",
    ]) == 1
    assert json.loads(capsys.readouterr().out)["outcome"] == "refused"


def test_pull_reads_default_hf_token_from_home_dotenv(tmp_path):
    seen = {}
    dotenv = tmp_path / ".env"
    dotenv.write_text("HF_TOKEN=hf_from_file\n", encoding="utf-8")

    def fake_run(argv, env=None):
        seen["argv"] = argv
        seen["env"] = env
        return 0

    rc = models.run_pull(
        "some/repo", token_file=str(dotenv), _run=fake_run,
        _environ={"PATH": "/usr/bin"})
    assert rc == 0
    assert seen["env"]["HF_TOKEN"] == "hf_from_file"
    assert "hf_from_file" not in seen["argv"]


# --------------------------------------------------------------------------- #
# CLI dispatch + help
# --------------------------------------------------------------------------- #


def _write_fake_catalog(staging):
    cards = Path(staging) / "cards"
    cards.mkdir(parents=True, exist_ok=True)
    (cards / "owner__model.json").write_text(
        '{"id":"owner/model","format":"safetensors"}\n',
        encoding="utf-8",
    )
    (Path(staging) / "INDEX.md").write_text("# generated\n", encoding="utf-8")


def test_models_sync_dry_run_resolves_plan_without_writing_or_scanning(
    tmp_path, monkeypatch, capsys
):
    calls = []
    output = tmp_path / "catalog"
    monkeypatch.setattr(
        models.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert cli.main([
        "models", "sync", "--out", str(output), "--dry-run",
    ]) == 0

    out = capsys.readouterr().out
    assert "MODEL CATALOG SYNC PLAN" in out
    assert str(output) in out
    assert "deferred until apply" in out
    assert "model-card fetches" in out
    assert calls == []
    assert not output.exists()


def test_models_sync_dry_run_resolves_environment_model_dirs(
    tmp_path, monkeypatch, capsys
):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    monkeypatch.setenv("ANVIL_MODEL_DIRS", str(model_dir))

    assert models.main([
        "sync", "--out", str(tmp_path / "catalog"), "--dry-run",
    ]) == 0
    assert str(model_dir) in capsys.readouterr().out


def test_models_sync_dry_run_resolves_environment_hf_roots(
    tmp_path, monkeypatch, capsys
):
    hf_root = tmp_path / "hf-root"
    hf_root.mkdir()
    monkeypatch.setenv("ANVIL_HF_ROOTS", str(hf_root))

    assert models.main([
        "sync", "--out", str(tmp_path / "catalog"), "--dry-run",
    ]) == 0
    assert str(hf_root) in capsys.readouterr().out


def test_models_sync_apply_requires_shared_confirmation(tmp_path, monkeypatch, capsys):
    calls = []
    output = tmp_path / "catalog"

    def fake_sync(*args, **kwargs):
        calls.append((args, kwargs))
        staging = kwargs["env"]["ANVIL_MODELS_OUT"]
        _write_fake_catalog(staging)
        return models.subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr(
        models.subprocess,
        "run",
        fake_sync,
    )

    assert cli.main(["models", "sync", "--out", str(output)]) == 3
    assert "confirmation required" in capsys.readouterr().err
    assert calls == []
    assert not output.exists()

    assert cli.main([
        "models", "sync", "--out", str(output), "--confirm",
    ]) == 0
    assert len(calls) == 1
    assert (output / "cards").is_dir()


def test_models_sync_failed_rollback_names_backup_and_recovery_command(
    tmp_path, monkeypatch, capsys
):
    output = tmp_path / "catalog"
    _write_fake_catalog(output)

    def fake_sync(*args, **kwargs):
        _write_fake_catalog(kwargs["env"]["ANVIL_MODELS_OUT"])
        return models.subprocess.CompletedProcess(args[0], 0, "", "")

    real_replace = models.os.replace
    calls = []

    def fail_install_and_restore(source, destination):
        calls.append((source, destination))
        if len(calls) >= 2:
            raise OSError("simulated replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr(models.subprocess, "run", fake_sync)
    monkeypatch.setattr(models.os, "replace", fail_install_and_restore)

    assert models.main(["sync", "--out", str(output)]) == 5
    backup = tmp_path / "catalog.anvil.bak.1"
    assert backup.is_dir()
    err = capsys.readouterr().err
    assert str(backup) in err
    assert "recovery command:" in err
    assert not list(tmp_path.glob(".catalog.anvil-sync-*"))


def test_sync_worker_returns_nonzero_when_any_model_summary_fails(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("ANVIL_MODELS_OUT", str(tmp_path / "staging"))
    sys.modules.pop("anvil_serving._sync", None)
    sync_worker = importlib.import_module("anvil_serving._sync")
    monkeypatch.setattr(
        sync_worker,
        "discover",
        lambda: [("org", "broken", str(tmp_path / "model"), "dir")],
    )
    monkeypatch.setattr(
        sync_worker,
        "summarize",
        lambda *_args: (_ for _ in ()).throw(ValueError("bad config")),
    )

    assert sync_worker.main() == 1
    assert "ERROR org/broken: bad config" in capsys.readouterr().err
    sys.modules.pop("anvil_serving._sync", None)


def test_cache_inventory_normalizes_volume_and_docker_storage():
    volume_report = {
        "schema_version": "model-cache-inventory/v1",
        "cache": {
            "capacity_bytes": 1000,
            "used_bytes": 700,
            "available_bytes": 300,
            "logical_bytes": 600,
        },
        "repositories": [{
            "repo_id": "org/model",
            "logical_bytes": 10,
            "snapshots": ["abc"],
            "refs": {"main": "abc"},
            "incomplete_count": 0,
            "incomplete_bytes": 0,
            "broken_symlink_count": 0,
            "latest_cache_modification_at": "2026-07-01T00:00:00Z",
        }],
        "timestamp_caveat": "not last use",
    }
    docker_report = {
        "Images": [{
            "Repository": "example/image",
            "Tag": "1",
            "ID": "sha256:abc",
            "CreatedAt": "2026-07-01 00:00:00 +0000 UTC",
            "Containers": "1",
            "Size": "2GB",
            "SharedSize": "500MB",
            "UniqueSize": "1.5GB",
        }],
        "Containers": [{
            "Container ID": "container-1",
            "Names": "candidate",
            "Image": "example/image:1",
            "CreatedAt": "2026-07-02 00:00:00 +0000 UTC",
            "State": "running",
            "Status": "Up 1 hour",
            "Labels": "com.docker.compose.project.config_files=C:/private/path",
        }],
        "LocalVolumes": [{"Name": "vllm-hfcache", "Links": "0", "Size": "600B"}],
        "BuildCache": [{
            "ID": "cache-1",
            "CacheType": "regular",
            "InUse": "false",
            "Size": "25MB",
            "CreatedAt": "2026-07-01 00:00:00 +0000 UTC",
            "LastUsedAt": "2026-07-03 00:00:00 +0000 UTC",
            "UsageCount": "2",
        }],
    }
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        payload = volume_report if argv[:2] == ["docker", "run"] else docker_report
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    report = models.cache_inventory(
        volume="vllm-hfcache",
        image="example/inspector:1",
        _run=fake_run,
    )

    assert report["schema_version"] == "model-cache-inventory/v1"
    assert report["repositories"][0]["repo_id"] == "org/model"
    assert report["docker"]["images"][0]["unique_bytes"] == 1_500_000_000
    assert report["docker"]["images"][0]["last_container_observed_at"].startswith("2026-07-02")
    assert report["docker"]["containers"] == [{
        "container_id": "container-1",
        "name": "candidate",
        "image_reference": "example/image:1",
        "image_id": None,
        "created_at": "2026-07-02 00:00:00 +0000 UTC",
        "state": "running",
        "status": "Up 1 hour",
    }]
    assert report["docker"]["build_cache"][0]["last_used_at"].startswith("2026-07-03")
    assert "readonly" in calls[0][calls[0].index("--mount") + 1]
    assert calls[1] == [
        "docker", "system", "df", "-v", "--format", "{{json .}}"
    ]


def test_cache_inventory_cli_dispatches_renders_and_writes_atomically(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        models,
        "cache_inventory",
        lambda **_kwargs: {
            "schema_version": "model-cache-inventory/v1",
            "repositories": [],
        },
    )

    output = tmp_path / "inventory.json"
    assert cli.main([
        "models", "cache", "inventory", "--output", str(output)
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "model-cache-inventory/v1"
    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert list(tmp_path.glob(".*.tmp")) == []


def test_cache_prune_default_dry_run_does_not_create_catalog_directory(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("anvil_serving._sync", None)
    sync_worker = importlib.import_module("anvil_serving._sync")
    sync_worker.ROOTS = []
    assert cache_prune.main(["--dry-run"]) == 0
    assert not (tmp_path / "model-library").exists()
    assert "DRY-RUN" in capsys.readouterr().out


def test_models_cache_prune_only_confirms_execute_and_propagates_authorization(
    monkeypatch, capsys
):
    seen = []

    def fake_prune(argv, **_kwargs):
        seen.append((list(argv), guard.confirmation_authorized()))
        return 0

    monkeypatch.setattr(cache_prune, "main", fake_prune)

    assert cli.main(["models", "cache", "prune"]) == 0
    assert seen == [([], False)]

    assert cli.main(["models", "cache", "prune", "--execute"]) == 3
    assert "confirmation required" in capsys.readouterr().err
    assert seen == [([], False)]

    assert cli.main([
        "models", "cache", "prune", "--execute", "--confirm"
    ]) == 0
    assert seen[-1] == (["--execute"], True)

    assert cli.main([
        "models", "cache", "prune", "--execute", "--dry-run",
    ]) == 0
    assert seen[-1] == (["--execute", "--dry-run"], False)


def test_cache_prune_direct_execute_requires_confirmation_before_scan(capsys):
    scanned = []

    assert cache_prune.main(
        ["--execute"], scan=lambda: scanned.append(True) or []
    ) == 3
    assert scanned == []
    assert "--confirm" in capsys.readouterr().err


def test_cache_prune_dry_run_reports_plan_without_deleting(tmp_path, capsys):
    candidate = tmp_path / "model"
    candidate.mkdir()
    rows = [{
        "id": "org/model",
        "format": "safetensors",
        "local_path": str(candidate),
        "dead_everywhere": True,
        "size_bytes": 1,
        "size_gb": 0.0,
    }]
    assert cache_prune.main(["--dry-run"], scan=lambda: rows) == 0
    assert candidate.is_dir()
    out = capsys.readouterr().out
    assert "would delete" in out
    assert "SCAN ROOTS" in out
    assert "ordered apply actions:" in out
    assert "drift note:" in out
    assert "rollback: none automatic" in out


def test_models_score_dispatches_canonically_and_root_score_refuses(capsys):
    assert models.main(["score", "--json", "--no-local"]) == 0
    direct = json.loads(capsys.readouterr().out)
    assert cli.main(["models", "score", "--json", "--no-local"]) == 0
    canonical = json.loads(capsys.readouterr().out)
    assert canonical["ok"] is True
    assert direct["candidates"]
    assert "Per-candidate role scores" in canonical["data"]
    assert cli.main(["score", "--no-local"]) == 2
    assert "unknown command: score" in capsys.readouterr().err


def test_pull_apply_requires_shared_confirmation(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        models,
        "pull_main",
        lambda argv: calls.append(list(argv)) or 0,
    )
    command = ["models", "pull", "openai/gpt-oss-120b"]
    assert cli.main(command) == 3
    assert calls == []
    assert "confirmation required" in capsys.readouterr().err
    assert cli.main([*command, "--confirm"]) == 0
    assert calls == [["openai/gpt-oss-120b"]]


def test_load_model_catalog_filters_non_model_summaries(tmp_path):
    root = tmp_path / "model-library"
    cards = root / "cards"
    cards.mkdir(parents=True)
    (cards / "dataset.json").write_text(json.dumps({
        "id": "not/a-model",
        "format": "?",
        "size_gb": 10.0,
    }), encoding="utf-8")
    (cards / "real.json").write_text(json.dumps({
        "id": "real/model",
        "format": "safetensors",
        "model_type": "qwen3",
    }), encoding="utf-8")
    catalog = models.load_model_catalog(str(root))
    assert [entry["id"] for entry in catalog["entries"]] == ["real/model"]


# --- serve-recipe READ tests (models recipe) ---


def _registry(request):
    return str(request.config.rootpath / "configs" / "serve-recipes.toml")


def test_recipe_default_registry_uses_packaged_data_outside_checkout(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / "empty-home"))
    rc = models.main(["recipe", "list"])
    assert rc == 0
    assert "openai/gpt-oss-120b" in capsys.readouterr().out


def test_recipe_dispatches_through_cli(request, capsys):
    rc = cli.main(["models", "recipes", "show", "gpt-oss-120b",
                   "--registry", _registry(request)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "docker run -d" in out


def _recipe_input(path, model, *, status="unverified"):
    path.write_text(
        '[[recipe]]\nmodel = "%s"\nstatus = "%s"\n\n[recipe.serve]\nimage = "example/image"\nport = 30123\n' % (model, status),
        encoding="utf-8",
    )


def test_recipe_create_update_delete_crud_with_backups(tmp_path, capsys):
    registry = tmp_path / "operator-recipes.toml"
    recipe = tmp_path / "new.toml"
    _recipe_input(recipe, "org/model")

    assert cli.main(["models", "recipes", "create", "--registry", str(registry),
                     "--recipe-file", str(recipe), "--confirm"]) == 0
    assert "created recipe" in capsys.readouterr().out
    assert serve_recipes.find_recipe(serve_recipes.load_registry(registry), "org/model")

    _recipe_input(recipe, "org/model-v2", status="verified")
    assert models.main(["recipe", "update", "org/model", "--registry", str(registry),
                        "--recipe-file", str(recipe), "--confirm"]) == 0
    assert registry.with_name(registry.name + ".anvil.bak.1").exists()
    assert serve_recipes.find_recipe(serve_recipes.load_registry(registry), "org/model-v2")

    assert models.main(["recipe", "delete", "org/model-v2", "--registry", str(registry),
                        "--confirm"]) == 0
    assert registry.with_name(registry.name + ".anvil.bak.2").exists()
    assert serve_recipes.load_registry(registry).get("recipe", []) == []


def test_recipe_crud_dry_run_never_writes(tmp_path, capsys):
    registry = tmp_path / "operator-recipes.toml"
    recipe = tmp_path / "new.toml"
    _recipe_input(recipe, "org/model")
    assert models.main(["recipe", "create", "--registry", str(registry),
                        "--recipe-file", str(recipe), "--dry-run"]) == 0
    assert not registry.exists()
    out = capsys.readouterr().out
    assert "RECIPE CREATE PLAN" in out
    assert "proposed registry entry" in out
    assert "image = \"example/image\"" in out
    assert "recipe input digest" in out
    assert "manual recovery: remove the newly created registry" in out
    assert "create backup" not in out

    registry.write_text(
        'schema = "x"\n[[recipe]]\nmodel = "org/model"\n\n'
        '[recipe.serve]\nimage = "example/image"\n',
        encoding="utf-8",
    )
    before = registry.read_text(encoding="utf-8")
    _recipe_input(recipe, "org/model-v2")
    assert models.main([
        "recipe", "update", "org/model", "--registry", str(registry),
        "--recipe-file", str(recipe), "--dry-run",
    ]) == 0
    assert "RECIPE UPDATE PLAN" in capsys.readouterr().out
    assert registry.read_text(encoding="utf-8") == before
    assert models.main([
        "recipe", "delete", "org/model", "--registry", str(registry), "--dry-run",
    ]) == 0
    delete_out = capsys.readouterr().out
    assert "recipe selected for deletion" in delete_out
    assert "proposed registry entry" not in delete_out
    assert registry.read_text(encoding="utf-8") == before


def test_recipe_load_dry_run_selects_recipe_without_docker(request, capsys):
    rc = models.main([
        "recipe", "load", "gpt-oss-120b", "--container", "recipe-heavy",
        "--registry", _registry(request), "--gpu-device", "0", "--dry-run",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "RECIPE LOAD PLAN" in out
    assert "--name recipe-heavy" in out
    assert "127.0.0.1:30002:30002" in out
    assert "ownership condition:" in out
    assert "wait up to 600s for declared HTTP health" in out
    assert "not performed by load: eval preflight" in out
    assert "automatic cache reclaim: disabled" in out


def _recipe_container_inspect(
    *,
    container_id="e" * 64,
    model="openai/gpt-oss-120b",
    revision="0123456789abcdef0123456789abcdef01234567",
    state="running",
):
    return json.dumps([{
        "Id": container_id,
        "Config": {
            "Image": "vllm/vllm-openai:nightly",
            "Labels": {
                "io.anvil-serving.managed-by": "models-recipes",
                "io.anvil-serving.recipe.model": model,
                "io.anvil-serving.recipe.revision": revision,
            },
        },
        "Image": "sha256:" + "a" * 64,
        "State": {
            "Status": state,
            "Running": state == "running",
            "Health": {"Status": "healthy"},
        },
    }])


def _recipe_identity():
    return {
        "model": "openai/gpt-oss-120b",
        "download": {"revision": "0123456789abcdef0123456789abcdef01234567"},
    }


def test_recipe_container_status_requires_exact_recipe_ownership():
    def fake(argv, **_kwargs):
        assert argv == ["docker", "inspect", "recipe-heavy"]
        return types.SimpleNamespace(
            returncode=0,
            stdout=_recipe_container_inspect(),
            stderr="",
        )

    identity = models._recipe_container_identity(
        _recipe_identity(), "recipe-heavy", _run=fake
    )

    assert identity["state"] == "running"
    assert identity["health"] == "healthy"
    assert identity["container_id"] == "e" * 64
    assert identity["image_id"] == "sha256:" + "a" * 64


def test_recipe_container_unload_refuses_same_name_replacement():
    calls = []
    inspections = iter(("a" * 64, "f" * 64))

    def fake(argv, **_kwargs):
        calls.append(argv)
        if argv[:2] == ["docker", "inspect"]:
            return types.SimpleNamespace(
                returncode=0,
                stdout=_recipe_container_inspect(container_id=next(inspections)),
                stderr="",
            )
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    with pytest.raises(serve_recipes.RecipeError, match="identity changed"):
        models._recipe_container_unload(
            _recipe_identity(),
            "recipe-heavy",
            confirm=True,
            _run=fake,
        )

    assert all(call[:3] != ["docker", "rm", "-f"] for call in calls)


def test_recipe_container_unload_reclaims_native_offload_after_remove(
    monkeypatch,
):
    calls = []
    recipe = {
        **_recipe_identity(),
        "serve": {"env": ["KV_OFFLOADING_SIZE=8"]},
    }

    def fake(argv, **_kwargs):
        calls.append(argv)
        if argv[:2] == ["docker", "inspect"]:
            return types.SimpleNamespace(
                returncode=0, stdout=_recipe_container_inspect(), stderr="",
            )
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    reclaimed = []
    monkeypatch.setattr(
        models.host_ops, "prepare_native_kv_offload_shared_memory",
        lambda: reclaimed.append(list(calls)) or {"outcome": "reclaimed"},
    )
    monkeypatch.setattr(
        models.host_ops, "render_vllm_offload_shared_memory", lambda _result: None,
    )

    assert models._recipe_container_unload(
        recipe, "recipe-heavy", confirm=True, _run=fake,
    ) == 0
    assert reclaimed and ["docker", "rm", "-f", "e" * 64] in reclaimed[0]


def test_recipe_load_confirmed_invokes_loader_once(request, monkeypatch, capsys):
    seen = {}

    def fake_load(recipe, container, **_kwargs):
        seen["model"] = recipe["model"]
        seen["container"] = container
        return ["docker", "run"], 0

    monkeypatch.setattr(models.serve_recipes, "load_recipe", fake_load)
    monkeypatch.setattr(
        models,
        "_await_recipe_container_healthy",
        lambda *_args: (True, "running"),
    )
    rc = models.main([
        "recipe", "load", "gpt-oss-120b", "--container", "recipe-heavy",
        "--registry", _registry(request), "--gpu-device", "0", "--confirm",
    ])
    assert rc == 0
    assert seen == {"model": "openai/gpt-oss-120b", "container": "recipe-heavy"}
    assert "preflight before trusting" in capsys.readouterr().out


def test_recipe_load_uses_recipe_specific_startup_timeout(
        request, monkeypatch):
    recipe = {
        "model": "org/model",
        "serve": {
            "image": "example/image:pinned",
            "port": 30123,
            "startup_timeout_seconds": 1800,
        },
    }
    observed = []
    monkeypatch.setattr(models.serve_recipes, "find_recipe", lambda *_args: recipe)
    monkeypatch.setattr(
        models.serve_recipes,
        "load_recipe",
        lambda *_args, **_kwargs: (["docker", "run"], 0),
    )
    monkeypatch.setattr(
        models,
        "_await_recipe_container_healthy",
        lambda _recipe, _container, _target, timeout, _poll: (
            observed.append(timeout) or (True, "running")
        ),
    )

    assert models.main([
        "recipe", "load", "org/model", "--container", "candidate",
        "--registry", _registry(request), "--confirm",
    ]) == 0
    assert observed == [1800]


def test_recipe_readiness_polls_loading_states_until_healthy():
    states = iter(["created", "restarting", "running"])
    sleeps = []
    clock = iter([0, 0, 1, 2])

    ready, state = models._await_recipe_container_healthy(
        _recipe_identity(),
        "recipe-heavy",
        {"port": 30002, "health": "/health"},
        600,
        2,
        _identity=lambda *_args: {"state": next(states)},
        _health=lambda *_args: 200 if len(sleeps) == 2 else None,
        _sleep=sleeps.append,
        _monotonic=lambda: next(clock),
    )

    assert ready is True
    assert state == "running"
    assert sleeps == [2, 2]


def test_pull_failure_and_dry_run_never_reclaim(monkeypatch, capsys):
    policy = _enabled_cache_policy()
    monkeypatch.setattr(models.host_ops, "load_cache_reclaim_policy", lambda: policy)
    reclaimed = []
    monkeypatch.setattr(
        models.host_ops, "automatic_cache_reclaim",
        lambda *_args, **_kwargs: reclaimed.append(True),
    )
    monkeypatch.setattr(models, "run_pull", lambda *_args, **_kwargs: 17)
    monkeypatch.setattr(
        models,
        "preflight_pull",
        lambda *_args, **_kwargs: {
            "expected_bytes": 10,
            "cached_repo_bytes": 0,
            "missing_bytes": 10,
            "free_bytes": 100,
            "headroom_bytes": 5,
        },
    )
    assert models.pull_main(["org/model", "--no-token"]) == 17
    assert reclaimed == []

    captures = []
    monkeypatch.setattr(
        models.host_ops, "capture_cache_before",
        lambda *_args, **_kwargs: captures.append(True),
    )
    monkeypatch.setattr(models, "run_pull", lambda *_args, **_kwargs: 0)
    assert models.pull_main(["org/model", "--no-token", "--dry-run"]) == 0
    assert captures == [] and reclaimed == []
    assert "automatic cache reclaim: enabled" in capsys.readouterr().out


def test_recipe_load_readiness_timeout_fails_closed(request, monkeypatch):
    policy = _enabled_cache_policy()
    seen = []
    monkeypatch.setattr(models.host_ops, "load_cache_reclaim_policy", lambda: policy)
    monkeypatch.setattr(
        models.host_ops, "capture_cache_before", lambda _policy: {"cached_gb": 10.0}
    )
    monkeypatch.setattr(
        models.serve_recipes,
        "load_recipe",
        lambda *_args, **_kwargs: (["docker", "run"], 0),
    )
    monkeypatch.setattr(
        models,
        "_await_recipe_container_healthy",
        lambda *_args, **_kwargs: (False, "running"),
    )
    monkeypatch.setattr(
        models.host_ops, "automatic_cache_reclaim",
        lambda _policy, _before, **kwargs: seen.append(kwargs) or {
            "outcome": "readiness-timeout"
        },
    )
    monkeypatch.setattr(models.host_ops, "render_cache_reclaim_result", lambda _result: None)
    assert models.main([
        "recipe", "load", "gpt-oss-120b", "--container", "recipe-heavy",
        "--registry", _registry(request), "--gpu-device", "0", "--confirm",
    ]) == 1
    assert seen == []
