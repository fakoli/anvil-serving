"""Tests for `anvil_serving.models` — the `models` verb, focused on the new
`models pull` sub-action (download a HF repo into a NAMED docker volume so it
serves natively, avoiding the 9P bind-mount tax — CLAUDE.md gotcha #15).

Every test is HERMETIC: docker is never invoked (subprocess is mocked or we
stay on --dry-run). No real docker, no network.
"""
import pytest
import json

from anvil_serving import cache_prune, cli, guard, models


# --------------------------------------------------------------------------- #
# build_pull_argv — the constructed docker command (the load-bearing invariants)
# --------------------------------------------------------------------------- #

def test_pull_argv_mounts_named_volume_at_hf_cache():
    argv = models.build_pull_argv("openai/gpt-oss-120b")
    # NAMED VOLUME mounted at the HF cache — never a host C:/ bind mount (#15).
    assert "-v" in argv
    assert f"{models.DEFAULT_PULL_VOLUME}:{models.HF_CACHE_MOUNTPOINT}" in argv
    # No host-path bind mount snuck in.
    assert not any(":" in tok and tok.startswith(("C:", "c:", "/mnt/", "/c/")) for tok in argv)


def test_pull_argv_invokes_hf_download_not_huggingface_cli():
    argv = models.build_pull_argv("openai/gpt-oss-120b")
    # The NEW `hf` CLI via an overridden entrypoint — the OLD `huggingface-cli`
    # is REMOVED in huggingface_hub >=1.21 and must never be emitted. Docker argv
    # order is `--entrypoint hf <image> download <repo-id>`, so the container runs
    # `hf download <repo-id>` (the image name sits between `hf` and `download`).
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--entrypoint" in argv
    assert argv[argv.index("--entrypoint") + 1] == "hf"
    di = argv.index("download")
    assert argv[di + 1] == "openai/gpt-oss-120b"  # `download <repo-id>`
    assert "huggingface-cli" not in argv
    assert "huggingface-cli" not in " ".join(argv)


def test_pull_argv_uses_given_image_after_entrypoint():
    argv = models.build_pull_argv("openai/gpt-oss-120b", image="my/img:tag")
    # image must sit AFTER `--entrypoint hf` and BEFORE `download` (docker argv order).
    ei, ii, di = argv.index("--entrypoint"), argv.index("my/img:tag"), argv.index("download")
    assert ei < ii < di
    assert argv[di + 1] == "openai/gpt-oss-120b"


def test_pull_argv_default_image_is_vllm_nightly():
    argv = models.build_pull_argv("openai/gpt-oss-120b")
    assert models.DEFAULT_PULL_IMAGE in argv
    assert models.DEFAULT_PULL_IMAGE == "vllm/vllm-openai:nightly"


def test_pull_argv_honors_volume_image_revision_include_exclude():
    argv = models.build_pull_argv(
        "meta/model", volume="myvol", image="img:1",
        revision="v2", include="*.safetensors", exclude="*.bin")
    assert "myvol:/root/.cache/huggingface" in argv
    assert "img:1" in argv
    assert argv[argv.index("--revision") + 1] == "v2"
    assert argv[argv.index("--include") + 1] == "*.safetensors"
    assert argv[argv.index("--exclude") + 1] == "*.bin"


def test_pull_argv_forwards_hf_token_by_default():
    argv = models.build_pull_argv("openai/gpt-oss-120b")
    assert argv[argv.index("-e") + 1] == "HF_TOKEN"


def test_pull_argv_supports_explicit_unauthenticated_pull():
    argv = models.build_pull_argv("openai/gpt-oss-120b", token_env=None)
    assert "-e" not in argv
    assert "HF_TOKEN" not in argv


def test_pull_argv_token_env_passes_by_name_never_value():
    argv = models.build_pull_argv("openai/gpt-oss-120b", token_env="MY_HF_TOKEN")
    # `-e HF_TOKEN` (by NAME) is present; the ENV VAR NAME the user chose and any
    # token VALUE never appear on argv.
    assert argv[argv.index("-e") + 1] == "HF_TOKEN"
    assert "MY_HF_TOKEN" not in argv
    assert not any("HF_TOKEN=" in tok for tok in argv)


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


def test_pull_dry_run_token_env_shows_name_only_not_value(monkeypatch, capsys):
    monkeypatch.setenv("MY_HF_TOKEN", "hf_supersecret_value")
    rc = models.pull_main(["some/repo", "--token-env", "MY_HF_TOKEN", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "-e HF_TOKEN" in out
    assert "hf_supersecret_value" not in out  # the VALUE is never printed
    assert "MY_HF_TOKEN" not in out           # nor the source var name


# --------------------------------------------------------------------------- #
# run_pull execution — subprocess wiring, exit-code passthrough, token env
# --------------------------------------------------------------------------- #

def test_pull_execution_shells_out_with_constructed_argv():
    seen = {}

    def fake_run(argv, env=None):
        seen["argv"] = argv
        seen["env"] = env
        return 0

    rc = models.run_pull("openai/gpt-oss-120b", _run=fake_run,
                         _environ={"HF_TOKEN": "hf_secret"})
    assert rc == 0
    argv = seen["argv"]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "vllm-hfcache:/root/.cache/huggingface" in argv
    assert "download" in argv and "openai/gpt-oss-120b" in argv
    assert "huggingface-cli" not in argv


def test_pull_nonzero_docker_exit_surfaces_clean_rc():
    rc = models.run_pull("openai/gpt-oss-120b", _run=lambda *a, **k: 17,
                         _environ={"HF_TOKEN": "hf_secret"})
    assert rc == 17  # docker failure passed through, not a traceback


def test_pull_missing_docker_binary_is_clean_127(capsys):
    def boom(*a, **k):
        raise FileNotFoundError("docker not found")

    rc = models.run_pull("openai/gpt-oss-120b", _run=boom,
                         _environ={"HF_TOKEN": "hf_secret"})
    assert rc == 127
    assert "docker" in capsys.readouterr().err.lower()


def test_pull_rejects_path_like_volume_that_would_reintroduce_9p(capsys):
    """A --volume that looks like a host PATH is rejected before shelling out — a
    bind mount would reintroduce the exact 9P tax this command exists to avoid
    (gotcha #15). The named-volume default is the whole point."""
    called = []
    for bad in ("C:/models", "/mnt/d/models", "models\\dir"):
        rc = models.run_pull("some/repo", volume=bad,
                             _run=lambda *a, **k: called.append(1),
                             _environ={"HF_TOKEN": "hf_secret"})
        assert rc == 2
    assert called == []  # never shells out to docker for a path-like volume
    assert "9p" in capsys.readouterr().err.lower()


def test_pull_token_env_forwards_value_into_child_env_only():
    seen = {}

    def fake_run(argv, env=None):
        seen["argv"] = argv
        seen["env"] = env
        return 0

    rc = models.run_pull(
        "some/repo", token_env="MY_HF_TOKEN", _run=fake_run,
        _environ={"MY_HF_TOKEN": "hf_secret", "PATH": "/usr/bin"})
    assert rc == 0
    # The token is placed in the CHILD env as HF_TOKEN (docker `-e HF_TOKEN`
    # forwards it by reference) and never on the argv.
    assert seen["env"]["HF_TOKEN"] == "hf_secret"
    assert "hf_secret" not in seen["argv"]
    assert "-e" in seen["argv"] and seen["argv"][seen["argv"].index("-e") + 1] == "HF_TOKEN"


def test_pull_token_env_set_but_missing_is_clean_error(capsys):
    called = []
    rc = models.run_pull(
        "some/repo", token_env="MISSING_TOKEN",
        token_file=None, _run=lambda *a, **k: called.append(1),
        _environ={"PATH": "/usr/bin"})
    assert rc == 2
    assert called == []  # never shelled out
    err = capsys.readouterr().err
    assert "MISSING_TOKEN" in err


def test_pull_unreadable_token_file_is_clean_error(tmp_path, capsys):
    called = []
    rc = models.run_pull(
        "some/repo", token_file=str(tmp_path),
        _run=lambda *a, **k: called.append(1), _environ={"PATH": "/usr/bin"})
    assert rc == 2
    assert called == []
    err = capsys.readouterr().err
    assert "not a regular file" in err
    assert tmp_path.name in err


def test_pull_whitespace_exported_token_falls_back_to_dotenv(tmp_path):
    seen = {}
    dotenv = tmp_path / ".env"
    dotenv.write_text("HF_TOKEN=hf_from_file\n", encoding="utf-8")

    def fake_run(argv, env=None):
        seen["env"] = env
        return 0

    rc = models.run_pull(
        "some/repo", token_file=str(dotenv), _run=fake_run,
        _environ={"HF_TOKEN": "   ", "PATH": "/usr/bin"})
    assert rc == 0
    assert seen["env"]["HF_TOKEN"] == "hf_from_file"


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


def test_pull_exported_token_wins_over_dotenv(tmp_path):
    seen = {}
    dotenv = tmp_path / ".env"
    dotenv.write_text("HF_TOKEN=hf_from_file\n", encoding="utf-8")

    def fake_run(argv, env=None):
        seen["env"] = env
        return 0

    rc = models.run_pull(
        "some/repo", token_file=str(dotenv), _run=fake_run,
        _environ={"HF_TOKEN": "hf_from_shell", "PATH": "/usr/bin"})
    assert rc == 0
    assert seen["env"]["HF_TOKEN"] == "hf_from_shell"


# --------------------------------------------------------------------------- #
# CLI dispatch + help
# --------------------------------------------------------------------------- #

def test_models_parent_help_lists_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        models.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "sync" in out
    assert "pull" in out
    assert "recipe" in out
    assert "cache" in out
    assert "score" in out


def test_models_sync_help_documents_sync_flags(capsys):
    with pytest.raises(SystemExit) as exc:
        models.main(["sync", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--out" in out
    assert "--hf-roots" in out
    assert "--model-dirs" in out


def test_models_cache_prune_help_uses_canonical_usage(capsys):
    with pytest.raises(SystemExit) as exc:
        models.main(["cache", "prune", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: anvil-serving models cache prune" in out
    assert "--mixture" in out
    assert "--dry-run" in out


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


def test_models_score_help_uses_canonical_usage(capsys):
    with pytest.raises(SystemExit) as exc:
        models.main(["score", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: anvil-serving models score" in out
    assert "--json" in out
    assert "--no-local" in out


def test_models_score_dispatches_canonically_and_root_score_refuses(capsys):
    assert models.main(["score", "--json", "--no-local"]) == 0
    direct = json.loads(capsys.readouterr().out)
    assert cli.main(["models", "score", "--json", "--no-local"]) == 0
    canonical = json.loads(capsys.readouterr().out)
    assert canonical["ok"] is True
    assert direct["candidates"]
    assert "Per-candidate role scores" in canonical["data"]
    assert cli.main(["score", "--no-local"]) == 2
    assert "was removed" in capsys.readouterr().err


def test_pull_dispatches_through_top_level_cli(monkeypatch, capsys):
    rc = cli.main(["models", "pull", "openai/gpt-oss-120b", "--dry-run", "--confirm"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "download openai/gpt-oss-120b" in out
    assert "-v vllm-hfcache:/root/.cache/huggingface" in out


def test_pull_help_documents_flags_and_rationale(capsys):
    with pytest.raises(SystemExit) as exc:
        models.pull_main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for token in ("--volume", "--image", "--revision", "--include", "--exclude",
                  "--token-env", "--dry-run"):
        assert token in out, token
    # The 9P-avoidance rationale and lock-deadlock note are documented.
    assert "9P" in out
    assert "lock" in out.lower()


def test_models_sync_still_dispatches(tmp_path, monkeypatch):
    """The pre-existing `sync` action is untouched by the pull branch."""
    calls = []
    monkeypatch.setattr(models.subprocess, "call", lambda *a, **k: calls.append((a, k)) or 0)
    rc = models.main(["sync", "--out", str(tmp_path / "model-library")])
    assert rc == 0
    assert len(calls) == 1  # _sync.py was shelled out exactly once


def test_build_sync_argv_includes_optional_roots(tmp_path):
    out = str(tmp_path / "model-library")
    argv = models.build_sync_argv(out, hf_roots="C:/hf", model_dirs="D:/models")
    assert argv[:3] == [models.sys.executable, "-m", "anvil_serving.cli"]
    assert argv[3:7] == ["models", "sync", "--out", out]
    assert argv[argv.index("--hf-roots") + 1] == "C:/hf"
    assert argv[argv.index("--model-dirs") + 1] == "D:/models"


def test_models_import_does_not_create_default_catalog_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / models.DEFAULT_CATALOG_DIR).exists()
    assert models.is_real_catalog_entry({"id": "real/model", "format": "safetensors"}) is True
    assert not (tmp_path / models.DEFAULT_CATALOG_DIR).exists()


def test_load_model_catalog_reads_cards_json_not_index(tmp_path):
    root = tmp_path / "model-library"
    cards = root / "cards"
    cards.mkdir(parents=True)
    (root / "INDEX.md").write_text("| stale human table |\n| fake/model |\n", encoding="utf-8")
    (cards / "real__model.json").write_text(json.dumps({
        "id": "real/model",
        "format": "safetensors",
        "context": 131072,
    }), encoding="utf-8")

    catalog = models.load_model_catalog(str(root))
    assert catalog["count"] == 1
    assert catalog["index_path"] == str(root / "INDEX.md")
    assert catalog["entries"][0]["id"] == "real/model"
    assert "fake/model" not in json.dumps(catalog)


def test_load_model_catalog_rejects_index_only_catalog(tmp_path):
    root = tmp_path / "model-library"
    (root / "cards").mkdir(parents=True)
    (root / "INDEX.md").write_text("| stale human table |\n", encoding="utf-8")
    with pytest.raises(models.CatalogNotFound):
        models.load_model_catalog(str(root))


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


def test_load_model_catalog_missing_is_clear(tmp_path):
    missing = tmp_path / "missing-library"
    with pytest.raises(models.CatalogNotFound) as exc:
        models.load_model_catalog(str(missing))
    assert str(missing) in str(exc.value)


def test_load_model_catalog_malformed_summary_is_clean_error(tmp_path):
    root = tmp_path / "model-library"
    cards = root / "cards"
    cards.mkdir(parents=True)
    (cards / "bad.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(models.CatalogError) as exc:
        models.load_model_catalog(str(root))
    assert "summaries" in str(exc.value)
    assert exc.value.details["errors"][0]["path"].endswith("bad.json")


# --- serve-recipe READ tests (models recipe) ---


def _registry(request):
    return str(request.config.rootpath / "configs" / "serve-recipes.toml")


def test_recipe_list_tables_recorded_recipes(request, capsys):
    rc = models.main(["recipe", "list", "--registry", _registry(request)])
    out = capsys.readouterr().out
    assert rc == 0
    # header + the three shipped rows.
    assert "status" in out and "throughput" in out and "intent" in out
    assert "openai/gpt-oss-120b" in out
    assert "183.2 tok/s" in out
    assert "nvidia/Qwen3-32B-NVFP4" in out
    assert "Qwen/Qwen3.6-27B" in out


def test_recipe_show_prints_reconstructed_command_and_stats(request, capsys):
    rc = models.main(["recipe", "show", "gpt-oss-120b", "--registry", _registry(request)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "183.2" in out  # measured throughput
    # the reconstructed, reproducible docker run (positional model after the image).
    assert "docker run -d --gpus device=GPU-d0f446cf" in out
    assert "vllm/vllm-openai:nightly openai/gpt-oss-120b" in out
    assert "-v vllm-hfcache:/root/.cache/huggingface" in out
    # intent + download surfaced.
    assert "flexibility" in out
    assert "anvil-serving models pull openai/gpt-oss-120b" in out


def test_recipe_show_unknown_model_is_clean_error(request, capsys):
    rc = models.main(["recipe", "show", "no-such-model", "--registry", _registry(request)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no serve recipe" in err


def test_recipe_show_missing_registry_is_clean_error(tmp_path, capsys):
    rc = models.main(["recipe", "show", "x", "--registry", str(tmp_path / "nope.toml")])
    err = capsys.readouterr().err
    assert rc == 1
    assert "registry not found" in err


def test_recipe_default_registry_resolves_without_explicit_flag(request, capsys):
    # No --registry: the default resolves to the shipped configs/serve-recipes.toml.
    rc = models.main(["recipe", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "openai/gpt-oss-120b" in out


def test_recipe_dispatches_through_cli(request, capsys):
    rc = cli.main(["models", "recipes", "show", "gpt-oss-120b",
                   "--registry", _registry(request)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "docker run -d" in out


def test_models_sync_action_still_accepted(monkeypatch, tmp_path):
    # Additive change must not break `models sync`: it still shells out to _sync.py.
    calls = {}

    def fake_call(cmd, env=None):
        calls["cmd"] = cmd
        calls["env"] = env
        return 0

    monkeypatch.setattr(models.subprocess, "call", fake_call)
    rc = models.main(["sync", "--out", str(tmp_path / "lib")])
    assert rc == 0
    assert calls["cmd"][1].endswith("_sync.py")
    assert calls["env"]["ANVIL_MODELS_OUT"] == str(tmp_path / "lib")


def test_recipe_list_shows_aggregate_throughput(tmp_path, capsys):
    """A recipe recorded at concurrency>1 stores throughput_aggregate_tok_s; the list
    table shows it (labeled agg), not '-' (Copilot review)."""
    reg = tmp_path / "r.toml"
    reg.write_text(
        'schema="x"\n[[recipe]]\nmodel="m"\nstatus="verified"\n'
        '[recipe.measured]\nthroughput_aggregate_tok_s=250.0\nconcurrency=20\n',
        encoding="utf-8")
    rc = models.main(["recipe", "list", "--registry", str(reg)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "250.0 tok/s (agg x20)" in out


def test_recipe_malformed_registry_is_clean_error(tmp_path, capsys):
    """A malformed registry TOML is a clean rc=1, not a TOMLDecodeError traceback (Copilot)."""
    reg = tmp_path / "bad.toml"
    reg.write_text("this is [not valid toml", encoding="utf-8")
    rc = models.main(["recipe", "list", "--registry", str(reg)])
    assert rc == 1
    assert "cannot read serve-recipe registry" in capsys.readouterr().err
