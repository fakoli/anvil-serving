"""Tests for `anvil_serving.models` — the `models` verb, focused on the new
`models pull` sub-action (download a HF repo into a NAMED docker volume so it
serves natively, avoiding the 9P bind-mount tax — CLAUDE.md gotcha #15).

Every test is HERMETIC: docker is never invoked (subprocess is mocked or we
stay on --dry-run). No real docker, no network.
"""
import pytest

from anvil_serving import cli
from anvil_serving import models


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


def test_pull_argv_unauthenticated_by_default():
    argv = models.build_pull_argv("openai/gpt-oss-120b")
    # No token forwarding of any kind by default.
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
                         _run=lambda *a, **k: called.append((a, k)))
    assert rc == 0
    assert called == []  # nothing executed
    out = capsys.readouterr().out
    assert "docker run --rm" in out
    assert "-v vllm-hfcache:/root/.cache/huggingface" in out
    assert "--entrypoint hf" in out
    assert "download openai/gpt-oss-120b" in out
    assert "huggingface-cli" not in out


def test_pull_dry_run_via_pull_main_default_unauthenticated(capsys):
    rc = models.pull_main(["openai/gpt-oss-120b", "--dry-run"])
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

    rc = models.run_pull("openai/gpt-oss-120b", _run=fake_run)
    assert rc == 0
    argv = seen["argv"]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "vllm-hfcache:/root/.cache/huggingface" in argv
    assert "download" in argv and "openai/gpt-oss-120b" in argv
    assert "huggingface-cli" not in argv


def test_pull_nonzero_docker_exit_surfaces_clean_rc():
    rc = models.run_pull("openai/gpt-oss-120b", _run=lambda *a, **k: 17)
    assert rc == 17  # docker failure passed through, not a traceback


def test_pull_missing_docker_binary_is_clean_127(capsys):
    def boom(*a, **k):
        raise FileNotFoundError("docker not found")

    rc = models.run_pull("openai/gpt-oss-120b", _run=boom)
    assert rc == 127
    assert "docker" in capsys.readouterr().err.lower()


def test_pull_rejects_path_like_volume_that_would_reintroduce_9p(capsys):
    """A --volume that looks like a host PATH is rejected before shelling out — a
    bind mount would reintroduce the exact 9P tax this command exists to avoid
    (gotcha #15). The named-volume default is the whole point."""
    called = []
    for bad in ("C:/models", "/mnt/d/models", "models\\dir"):
        rc = models.run_pull("some/repo", volume=bad, _run=lambda *a, **k: called.append(1))
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
        _run=lambda *a, **k: called.append(1), _environ={"PATH": "/usr/bin"})
    assert rc == 2
    assert called == []  # never shelled out
    err = capsys.readouterr().err
    assert "MISSING_TOKEN" in err


# --------------------------------------------------------------------------- #
# CLI dispatch + help
# --------------------------------------------------------------------------- #

def test_pull_dispatches_through_top_level_cli(monkeypatch, capsys):
    rc = cli.main(["models", "pull", "openai/gpt-oss-120b", "--dry-run"])
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
