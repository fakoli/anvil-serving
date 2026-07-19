"""Tests for `anvil_serving.models` — the `models` verb, focused on the new
`models pull` sub-action (download a HF repo into a NAMED docker volume so it
serves natively, avoiding the 9P bind-mount tax — CLAUDE.md gotcha #15).

Every test is HERMETIC: docker is never invoked (subprocess is mocked or we
stay on --dry-run). No real docker, no network.
"""
import importlib
import json
from pathlib import Path
import sys

import pytest

from anvil_serving import cache_prune, cli, guard, models, serve_recipes


@pytest.fixture(autouse=True)
def _isolated_host_policy(monkeypatch, tmp_path):
    """Never let a developer's enabled machine policy affect unit timing."""
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / ".anvil-serving"))


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


def test_pull_rejects_image_option_injection(capsys):
    assert models.pull_main([
        "some/repo", "--image=--privileged", "--dry-run"
    ]) == 2
    assert "must be a Docker image reference" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("args", "label"),
    [
        (["org/model", "--revision=--help"], "revision"),
        (["org/model", "--include=--help"], "include"),
        (["org/model", "--exclude=--help"], "exclude"),
    ],
)
def test_pull_rejects_hf_option_injection(args, label, capsys):
    assert models.pull_main([*args, "--dry-run"]) == 2
    assert "%s must be a value" % label in capsys.readouterr().err


def test_pull_rejects_repository_option_injection(capsys):
    assert models.run_pull("--local-dir", revision="attacker/huge-repo", dry_run=True) == 2
    assert "repository must be a value" in capsys.readouterr().err


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
    assert "--dry-run" in out
    assert "Examples:" in out


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


def test_models_sync_replaces_catalog_without_retaining_stale_cards(
    tmp_path, monkeypatch
):
    output = tmp_path / "catalog"
    cards = output / "cards"
    cards.mkdir(parents=True)
    (cards / "removed.json").write_text('{"id":"removed/model"}\n', encoding="utf-8")
    (output / "INDEX.md").write_text("# old\n", encoding="utf-8")

    def fake_sync(_argv, *, env, **_kwargs):
        staged_cards = models.os.path.join(env["ANVIL_MODELS_OUT"], "cards")
        with open(models.os.path.join(staged_cards, "current.json"), "w", encoding="utf-8") as handle:
            handle.write('{"id":"current/model","model_type":"qwen3","format":"safetensors"}\n')
        with open(models.os.path.join(env["ANVIL_MODELS_OUT"], "INDEX.md"), "w", encoding="utf-8") as handle:
            handle.write("# generated\n")
        return models.subprocess.CompletedProcess(_argv, 0, "", "")

    monkeypatch.setattr(models.subprocess, "run", fake_sync)
    assert models.main(["sync", "--out", str(output)]) == 0

    assert not (output / "cards" / "removed.json").exists()
    assert (output / "cards" / "current.json").is_file()
    assert (tmp_path / "catalog.anvil.bak.1" / "cards" / "removed.json").is_file()


def test_models_sync_failure_preserves_existing_catalog(tmp_path, monkeypatch):
    output = tmp_path / "catalog"
    cards = output / "cards"
    cards.mkdir(parents=True)
    original = cards / "current.json"
    original.write_text('{"id":"current/model"}\n', encoding="utf-8")
    (output / "INDEX.md").write_text("# current\n", encoding="utf-8")

    monkeypatch.setattr(
        models.subprocess,
        "run",
        lambda *args, **_kwargs: models.subprocess.CompletedProcess(args[0], 17, "", ""),
    )

    assert models.main(["sync", "--out", str(output)]) == 17
    assert original.read_text(encoding="utf-8") == '{"id":"current/model"}\n'
    assert not list(tmp_path.glob("catalog.anvil.bak.*"))
    assert not list(tmp_path.glob(".catalog.anvil-sync-*"))


def test_models_sync_replacement_failure_restores_existing_catalog(
    tmp_path, monkeypatch, capsys
):
    output = tmp_path / "catalog"
    cards = output / "cards"
    cards.mkdir(parents=True)
    original = cards / "current.json"
    original.write_text('{"id":"current/model"}\n', encoding="utf-8")
    (output / "INDEX.md").write_text("# current\n", encoding="utf-8")

    def fake_sync(_argv, *, env, **_kwargs):
        staged = models.os.path.join(env["ANVIL_MODELS_OUT"], "cards", "new.json")
        with open(staged, "w", encoding="utf-8") as handle:
            handle.write('{"id":"new/model","format":"safetensors"}\n')
        with open(models.os.path.join(env["ANVIL_MODELS_OUT"], "INDEX.md"), "w", encoding="utf-8") as handle:
            handle.write("# generated\n")
        return models.subprocess.CompletedProcess(_argv, 0, "", "")

    real_replace = models.os.replace
    calls = []

    def fail_install(source, destination):
        calls.append((source, destination))
        if len(calls) == 2:
            raise OSError("simulated install failure")
        return real_replace(source, destination)

    monkeypatch.setattr(models.subprocess, "run", fake_sync)
    monkeypatch.setattr(models.os, "replace", fail_install)

    assert models.main(["sync", "--out", str(output)]) == 1
    assert original.read_text(encoding="utf-8") == '{"id":"current/model"}\n'
    assert not (cards / "new.json").exists()
    assert not list(tmp_path.glob("catalog.anvil.bak.*"))
    assert not list(tmp_path.glob(".catalog.anvil-sync-*"))
    assert "cannot replace model catalog" in capsys.readouterr().err


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


def test_models_sync_refuses_unmanaged_existing_directory(tmp_path, capsys):
    output = tmp_path / "important"
    output.mkdir()
    important = output / "important.txt"
    important.write_text("keep me\n", encoding="utf-8")

    assert models.main(["sync", "--out", str(output), "--dry-run"]) == 3
    assert important.read_text(encoding="utf-8") == "keep me\n"
    assert "not an anvil model catalog" in capsys.readouterr().err


def test_models_sync_refuses_current_working_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert models.main(["sync", "--out", ".", "--dry-run"]) == 3
    assert "current working directory" in capsys.readouterr().err


def test_models_sync_refuses_concurrent_writer(tmp_path, capsys):
    output = str(tmp_path / "catalog")
    with models._catalog_lock(output):
        assert models.main(["sync", "--out", output]) == 4
    assert "already in progress" in capsys.readouterr().err


def test_models_sync_invalid_staging_preserves_existing_catalog(
    tmp_path, monkeypatch, capsys
):
    output = tmp_path / "catalog"
    cards = output / "cards"
    cards.mkdir(parents=True)
    original = cards / "current.json"
    original.write_text('{"id":"current/model"}\n', encoding="utf-8")
    (output / "INDEX.md").write_text("# current\n", encoding="utf-8")
    def fake_invalid(*args, **kwargs):
        staging = Path(kwargs["env"]["ANVIL_MODELS_OUT"])
        (staging / "INDEX.md").write_text("# generated\n", encoding="utf-8")
        (staging / "cards" / "bad.json").write_text("{}\n", encoding="utf-8")
        return models.subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr(models.subprocess, "run", fake_invalid)

    assert models.main(["sync", "--out", str(output)]) == 1
    assert original.is_file()
    assert not list(tmp_path.glob("catalog.anvil.bak.*"))
    assert "without a non-empty id and format" in capsys.readouterr().err


def test_models_sync_worker_launch_error_is_structured_and_cleans_staging(
    tmp_path, monkeypatch, capsys
):
    output = tmp_path / "catalog"

    def fail_launch(*_args, **_kwargs):
        raise OSError("cannot launch worker")

    monkeypatch.setattr(models.subprocess, "run", fail_launch)
    assert cli.main([
        "models", "sync", "--out", str(output), "--confirm", "--json",
    ]) == 4
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is False
    assert "cannot launch model catalog worker" in envelope["error"]["message"]
    assert not list(tmp_path.glob(".catalog.anvil-sync-*"))


def test_models_sync_bad_parent_is_clean_error(tmp_path, capsys):
    parent = tmp_path / "not-a-directory"
    parent.write_text("file\n", encoding="utf-8")
    output = parent / "catalog"
    assert models.main(["sync", "--out", str(output)]) == 1
    assert "cannot prepare model catalog output" in capsys.readouterr().err


def test_models_sync_json_captures_child_output_in_one_document(
    tmp_path, monkeypatch, capsys
):
    output = tmp_path / "catalog"

    def fake_sync(*args, **kwargs):
        staging = kwargs["env"]["ANVIL_MODELS_OUT"]
        _write_fake_catalog(staging)
        return models.subprocess.CompletedProcess(args[0], 0, "RAW CHILD OUTPUT\n", "")

    monkeypatch.setattr(models.subprocess, "run", fake_sync)
    assert cli.main([
        "models", "sync", "--out", str(output), "--confirm", "--json",
    ]) == 0

    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is True
    assert "RAW CHILD OUTPUT" in envelope["data"]


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


@pytest.mark.parametrize(
    ("path", "os_name", "platform_name", "expected"),
    [
        ("C:/models/model", "nt", "win32", "windows"),
        ("/Users/operator/models/model", "posix", "darwin", "macos"),
        ("/srv/models/model", "posix", "linux", "linux"),
        ("/mnt/c/Users/operator/models/model", "posix", "linux", "windows-wsl"),
    ],
)
def test_sync_source_platform_labels_native_hosts(
    tmp_path, monkeypatch, path, os_name, platform_name, expected
):
    monkeypatch.setenv("ANVIL_MODELS_OUT", str(tmp_path / "staging"))
    sys.modules.pop("anvil_serving._sync", None)
    sync_worker = importlib.import_module("anvil_serving._sync")
    assert sync_worker._source_platform(
        path, os_name=os_name, platform_name=platform_name
    ) == expected
    sys.modules.pop("anvil_serving._sync", None)


def test_models_cache_prune_help_uses_canonical_usage(capsys):
    with pytest.raises(SystemExit) as exc:
        models.main(["cache", "prune", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: anvil-serving models cache prune" in out
    assert "--mixture" in out
    assert "--dry-run" in out


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


def test_cache_prune_scan_failure_is_structured_json(monkeypatch, capsys):
    sync_worker = importlib.import_module("anvil_serving._sync")
    monkeypatch.setattr(
        sync_worker,
        "discover",
        lambda: (_ for _ in ()).throw(PermissionError("scan denied")),
    )
    assert cli.main(["models", "cache", "prune", "--dry-run", "--json"]) == 1
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is False
    assert "cache scan failed: scan denied" in envelope["error"]["message"]


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


def test_models_cache_prune_removed_yes_points_to_confirm(capsys):
    assert cli.main(["models", "cache", "prune", "--yes"]) == 2
    err = capsys.readouterr().err
    assert "--yes" in err
    assert "--confirm" in err
    assert "was removed" in err


def test_cache_prune_direct_execute_requires_confirmation_before_scan(capsys):
    scanned = []

    assert cache_prune.main(
        ["--execute"], scan=lambda: scanned.append(True) or []
    ) == 3
    assert scanned == []
    assert "--confirm" in capsys.readouterr().err


def test_cache_prune_metadata_caveat_is_not_current_host_deletion_proof():
    plan = cache_prune.classify_rows([{
        "id": "org/fp8-moe",
        "format": "safetensors",
        "sm120_caveat": "unsafe only on sm_120",
        "local_path": "ignored",
    }], mixture=[])
    assert plan["candidates"][0]["reason"] == "incompatible-sm120"
    assert plan["candidates"][0]["dead_everywhere"] is False


def test_cache_prune_delete_failure_returns_partial(monkeypatch, capsys):
    monkeypatch.setattr(cache_prune, "execute_plan", lambda *_args, **_kwargs: {
        "dry_run": False,
        "include_servable": False,
        "deleted": [],
        "would_delete": [],
        "kept": [],
        "skipped": [{"id": "org/model", "reason": "refused:rmtree-error:PermissionError"}],
        "reclaimed_bytes": 0,
        "planned_bytes": 1,
        "reclaimed_gb": 0.0,
    })
    assert cache_prune.main(["--execute", "--confirm"], scan=lambda: []) == 5
    assert "undeleted candidates" in capsys.readouterr().err


def test_cache_prune_json_preserves_broad_wipe_refusal(capsys):
    assert cli.main([
        "models", "cache", "prune", "--execute", "--include-servable",
        "--confirm", "--json",
    ]) == 2
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is False
    assert "REFUSING broad wipe" in envelope["error"]["message"]


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

    def fake_sync(*args, **kwargs):
        calls.append((args, kwargs))
        staging = kwargs["env"]["ANVIL_MODELS_OUT"]
        _write_fake_catalog(staging)
        return models.subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr(models.subprocess, "run", fake_sync)
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
    assert "--confirm" not in argv
    assert "--dry-run" not in argv


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
    assert "activates" in out and "heavy" in out
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
    assert "vllm/vllm-openai@sha256:e4f88a835143cd22aee2397a26ec6bb80b3a4a6fe0c882bcbc63822904766089 openai/gpt-oss-120b" in out
    assert "-v vllm-hfcache:/root/.cache/huggingface" in out
    # intent + download surfaced.
    assert "flexibility" in out
    assert "anvil-serving models pull openai/gpt-oss-120b" in out
    assert "activation:" in out
    assert "direction: rollback" in out
    assert "compose_service: heavy-gptoss-rollback" in out
    assert "serves switch heavy openai/gpt-oss-120b --dry-run" in out


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


def test_recipe_default_registry_uses_packaged_data_outside_checkout(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / "empty-home"))
    rc = models.main(["recipe", "list"])
    assert rc == 0
    assert "openai/gpt-oss-120b" in capsys.readouterr().out


def test_recipe_default_registry_prefers_operator_config_home(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "anvil-home"
    home.mkdir()
    (home / "serve-recipes.toml").write_text(
        'schema="x"\n[[recipe]]\nmodel="operator/model"\nstatus="verified"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(home))
    assert models.main(["recipe", "list"]) == 0
    out = capsys.readouterr().out
    assert "operator/model" in out
    assert "openai/gpt-oss-120b" not in out


def test_recipe_dispatches_through_cli(request, capsys):
    rc = cli.main(["models", "recipes", "show", "gpt-oss-120b",
                   "--registry", _registry(request)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "docker run -d" in out


@pytest.mark.parametrize("action", ["list", "show", "create", "update", "delete", "load"])
def test_recipe_leaf_help_is_actionable(action, capsys):
    with pytest.raises(SystemExit) as exc:
        models.main(["recipe", action, "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Examples:" in out
    assert "anvil-serving models recipes %s" % action in out


def test_models_cli_docs_cover_each_operator_workflow():
    text = (Path(__file__).parents[1] / "docs" / "cli" / "models.md").read_text(
        encoding="utf-8"
    )
    for heading in (
        "## Switch Heavy to another model",
        "## Catalog sync",
        "## Artifact pull",
        "### Discover recipes",
        "### Create, update, or delete a recipe",
        "### Load a recipe",
        "## Model scoring",
        "## Cache prune",
    ):
        assert heading in text
    assert 'flags = ["--served-model-name org/model"]' in text
    assert "Choose a row that activates `heavy`" in text


def test_models_sync_action_still_accepted(monkeypatch, tmp_path):
    # Additive change must not break `models sync`: it still shells out to _sync.py.
    calls = {}

    def fake_call(cmd, env=None, **_kwargs):
        calls["cmd"] = cmd
        calls["env"] = env
        _write_fake_catalog(env["ANVIL_MODELS_OUT"])
        return models.subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(models.subprocess, "run", fake_call)
    rc = models.main(["sync", "--out", str(tmp_path / "lib")])
    assert rc == 0
    assert calls["cmd"][1].endswith("_sync.py")
    assert calls["env"]["ANVIL_MODELS_OUT"].startswith(str(tmp_path / ".lib.anvil-sync-"))
    assert (tmp_path / "lib" / "cards").is_dir()


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


def test_recipe_write_refuses_state_drift_without_backup(tmp_path):
    registry = tmp_path / "operator-recipes.toml"
    registry.write_text(
        'schema = "x"\n[[recipe]]\nmodel = "org/one"\n', encoding="utf-8"
    )
    before = serve_recipes.registry_digest(registry)
    updated = {"schema": "x", "recipe": [{"model": "org/two"}]}
    registry.write_text(
        'schema = "x"\n[[recipe]]\nmodel = "org/concurrent"\n', encoding="utf-8"
    )

    with pytest.raises(serve_recipes.RecipeError, match="changed after it was read"):
        models._write_recipe_registry(registry, updated, expected_digest=before)

    assert serve_recipes.load_registry(registry)["recipe"][0]["model"] == "org/concurrent"
    assert not registry.with_name(registry.name + ".anvil.bak.1").exists()


def test_recipe_write_rejects_unsupported_value_before_backup(tmp_path):
    registry = tmp_path / "operator-recipes.toml"
    registry.write_text(
        'schema = "x"\n[[recipe]]\nmodel = "org/one"\n', encoding="utf-8"
    )
    updated = {
        "schema": "x",
        "recipe": [{"model": "org/two", "unsupported": object()}],
    }

    with pytest.raises(serve_recipes.RecipeError, match="unsupported TOML scalar type"):
        models._write_recipe_registry(
            registry,
            updated,
            expected_digest=serve_recipes.registry_digest(registry),
        )

    assert not registry.with_name(registry.name + ".anvil.bak.1").exists()


def test_recipe_load_dry_run_selects_recipe_without_docker(request, capsys):
    rc = models.main(["recipe", "load", "gpt-oss-120b", "--container", "recipe-heavy",
                      "--registry", _registry(request), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "RECIPE LOAD PLAN" in out
    assert "--name recipe-heavy" in out
    assert "127.0.0.1:30002:30002" in out
    assert "ownership condition:" in out
    assert "wait up to 600s for declared HTTP health" in out
    assert "not performed by load: eval preflight" in out
    assert "automatic cache reclaim: disabled" in out


def test_recipe_load_dispatches_through_canonical_cli(request, capsys):
    rc = cli.main([
        "models", "recipes", "load", "gpt-oss-120b", "--container", "recipe-heavy",
        "--registry", _registry(request), "--dry-run",
    ])
    assert rc == 0
    assert "RECIPE LOAD PLAN" in capsys.readouterr().out


def test_recipe_load_confirmed_invokes_loader_once(request, monkeypatch, capsys):
    seen = {}

    def fake_load(recipe, container):
        seen["model"] = recipe["model"]
        seen["container"] = container
        return ["docker", "run"], 0

    monkeypatch.setattr(models.serve_recipes, "load_recipe", fake_load)
    rc = models.main([
        "recipe", "load", "gpt-oss-120b", "--container", "recipe-heavy",
        "--registry", _registry(request), "--confirm",
    ])
    assert rc == 0
    assert seen == {"model": "openai/gpt-oss-120b", "container": "recipe-heavy"}
    assert "preflight before trusting" in capsys.readouterr().out


def _enabled_cache_policy():
    return {
        "enabled": True,
        "distro": "docker-desktop",
        "threshold_gb": 16.0,
        "source_path": "host.toml",
        "configured": True,
        "applicable": True,
        "schema_version": 1,
    }


def test_pull_validates_host_policy_before_download(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    (tmp_path / "host.toml").write_text(
        "schema_version = 1\n[cache_reclaim]\nunknown = true\n",
        encoding="utf-8",
    )
    called = []
    monkeypatch.setattr(models, "run_pull", lambda *_args, **_kwargs: called.append(True) or 0)
    assert models.pull_main(["org/model", "--no-token"]) == 2
    assert called == []
    assert "unknown field" in capsys.readouterr().err


def test_pull_runs_reclaim_once_only_after_success(monkeypatch):
    policy = _enabled_cache_policy()
    before = {"cached_gb": 10.0}
    events = []
    monkeypatch.setattr(models.host_ops, "load_cache_reclaim_policy", lambda: policy)
    monkeypatch.setattr(
        models.host_ops, "capture_cache_before",
        lambda resolved: events.append(("capture", resolved)) or before,
    )
    monkeypatch.setattr(
        models, "run_pull",
        lambda *_args, **_kwargs: events.append("pull") or 0,
    )
    monkeypatch.setattr(
        models.host_ops, "automatic_cache_reclaim",
        lambda resolved, baseline, **kwargs: events.append(
            ("reclaim", resolved, baseline, kwargs)
        ) or {"outcome": "reclaimed"},
    )
    monkeypatch.setattr(
        models.host_ops, "render_cache_reclaim_result",
        lambda result: events.append(("render", result)),
    )
    assert models.pull_main(["org/model", "--no-token"]) == 0
    assert [event if isinstance(event, str) else event[0] for event in events] == [
        "capture", "pull", "reclaim", "render",
    ]
    assert events[2][3]["operation"] == "models pull"


def test_pull_failure_and_dry_run_never_reclaim(monkeypatch, capsys):
    policy = _enabled_cache_policy()
    monkeypatch.setattr(models.host_ops, "load_cache_reclaim_policy", lambda: policy)
    reclaimed = []
    monkeypatch.setattr(
        models.host_ops, "automatic_cache_reclaim",
        lambda *_args, **_kwargs: reclaimed.append(True),
    )
    monkeypatch.setattr(models, "run_pull", lambda *_args, **_kwargs: 17)
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


def test_recipe_load_waits_for_health_then_reclaims_once(
        request, monkeypatch):
    from anvil_serving import serves

    policy = _enabled_cache_policy()
    before = {"cached_gb": 10.0}
    events = []
    monkeypatch.setattr(models.host_ops, "load_cache_reclaim_policy", lambda: policy)
    monkeypatch.setattr(models.host_ops, "capture_cache_before", lambda _policy: before)
    monkeypatch.setattr(
        models.serve_recipes, "load_recipe",
        lambda *_args, **_kwargs: (events.append("load") or (["docker", "run"], 0)),
    )
    monkeypatch.setattr(
        serves, "_await_healthy",
        lambda target, timeout, poll: events.append(
            ("health", target, timeout, poll)
        ) or True,
    )
    monkeypatch.setattr(
        models.host_ops, "automatic_cache_reclaim",
        lambda resolved, baseline, **kwargs: events.append(
            ("reclaim", resolved, baseline, kwargs)
        ) or {"outcome": "reclaimed"},
    )
    monkeypatch.setattr(models.host_ops, "render_cache_reclaim_result", lambda _result: None)
    assert models.main([
        "recipe", "load", "gpt-oss-120b", "--container", "recipe-heavy",
        "--registry", _registry(request), "--confirm",
    ]) == 0
    assert events[0] == "load"
    assert events[1][0] == "health"
    assert events[1][1] == {"port": 30002, "health": "/health"}
    assert events[1][2:] == (600, 2)
    assert events[2][0] == "reclaim"
    assert events[2][3]["readiness"] is True


def test_recipe_load_readiness_timeout_is_warning_only(request, monkeypatch):
    from anvil_serving import serves

    policy = _enabled_cache_policy()
    seen = []
    monkeypatch.setattr(models.host_ops, "load_cache_reclaim_policy", lambda: policy)
    monkeypatch.setattr(
        models.host_ops, "capture_cache_before", lambda _policy: {"cached_gb": 10.0}
    )
    monkeypatch.setattr(
        models.serve_recipes, "load_recipe", lambda *_args: (["docker", "run"], 0)
    )
    monkeypatch.setattr(serves, "_await_healthy", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        models.host_ops, "automatic_cache_reclaim",
        lambda _policy, _before, **kwargs: seen.append(kwargs) or {
            "outcome": "readiness-timeout"
        },
    )
    monkeypatch.setattr(models.host_ops, "render_cache_reclaim_result", lambda _result: None)
    assert models.main([
        "recipe", "load", "gpt-oss-120b", "--container", "recipe-heavy",
        "--registry", _registry(request), "--confirm",
    ]) == 0
    assert seen == [{"operation": "models recipes load", "readiness": False}]
