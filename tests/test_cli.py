"""Tests for the anvil-serving CLI dispatch — in particular the early
Python-version guard (`anvil_serving.cli._check_python_version`) and the
`calibrate` verb (the operator entry to the guarded write-back batch, T006).
"""
import json
import socket

import pytest

from anvil_serving import calibrate as calibrate_mod
from anvil_serving import cli
from anvil_serving import harness
from anvil_serving import host
from anvil_serving import router_manage
from anvil_serving import serves


def test_python_version_guard_blocks_old_interpreter():
    assert cli._check_python_version((3, 10, 0)) == (
        "anvil-serving needs Python >=3.11; you have 3.10"
    )


def test_python_version_guard_blocks_even_older_interpreter():
    assert cli._check_python_version((2, 7, 18)) == (
        "anvil-serving needs Python >=3.11; you have 2.7"
    )


def test_python_version_guard_allows_supported_interpreter():
    assert cli._check_python_version((3, 11, 0)) is None
    assert cli._check_python_version((3, 13, 0)) is None


def test_python_version_guard_blocks_main_under_simulated_old_interpreter(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "version_info", (3, 9, 0))
    rc = cli.main(["--help"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "anvil-serving needs Python >=3.11; you have 3.9" in captured.err


def test_top_level_help_groups_commands_and_shows_examples(capsys):
    rc = cli.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    for token in (
        "Data plane:",
        "Local serving tools:",
        "Quality loop:",
        "Control plane & integrations:",
        "Voice:",
        "anvil-serving serves status",
        "http://127.0.0.1:30000/v1",
        "Docs: docs/CLI.md",
    ):
        assert token in out


def test_unknown_top_level_command_suggests_close_match(capsys):
    rc = cli.main(["routr"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown command: routr" in err
    assert "Did you mean 'router'?" in err
    assert "anvil-serving --help" in err


def test_init_and_onboard_dispatch_to_same_module(monkeypatch):
    from anvil_serving import init as init_mod

    calls = []
    monkeypatch.setattr(init_mod, "main", lambda argv: calls.append(list(argv)) or 0)

    assert cli.main(["init", "--dry-run"]) == 0
    assert cli.main(["onboard", "--detect-only"]) == 0
    assert calls == [["--dry-run"], ["--detect-only"]]


def test_focused_action_help_for_operational_verbs(capsys):
    with pytest.raises(SystemExit) as exc:
        router_manage.main(["logs", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: anvil-serving router logs" in out
    assert "--tail" in out

    with pytest.raises(SystemExit) as exc:
        serves.main(["logs", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: anvil-serving serves logs" in out
    assert "--follow" in out

    with pytest.raises(SystemExit) as exc:
        host.main(["wsl-config", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: anvil-serving host wsl-config" in out
    assert "--memory" in out

    with pytest.raises(SystemExit) as exc:
        harness.main(["restart", "openclaw", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: anvil-serving harness restart openclaw" in out
    assert "--timeout-seconds" in out


def test_focused_action_help_includes_action_specific_flags(capsys):
    with pytest.raises(SystemExit) as exc:
        router_manage.main(["promote", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for token in ("--profile", "--config", "--container", "--cfg-volume", "--image",
                  "--profile-dest", "--config-dest", "--no-reload"):
        assert token in out

    with pytest.raises(SystemExit) as exc:
        harness.main(["sync", "openclaw", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for token in ("--config", "--out", "--base-url", "--api-key-env",
                  "--gateway-host", "--gateway-path", "--overwrite", "--restart",
                  "--skills", "--skill-dir", "--voice", "--voice-consult-model",
                  "--voice-consult-thinking-level"):
        assert token in out

    with pytest.raises(SystemExit) as exc:
        serves.main(["up", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for token in ("--manifest", "--dry-run", "--recreate", "--compose"):
        assert token in out


def test_top_level_command_registry_is_dispatch_registry():
    assert sorted(cli.COMMAND_BY_NAME) == sorted(item["name"] for item in cli.COMMANDS)
    for item in cli.COMMANDS:
        assert callable(item["handler"])


# --------------------------------------------------------------------------- #
# `anvil-serving calibrate` — operator entry to the guarded write-back batch
# (flexibility:T006). Every test here is HERMETIC: the guard refuses BEFORE any
# network, or run_live is injected as a fake — CI makes ZERO tier/`claude` calls.
# --------------------------------------------------------------------------- #

# Minimal valid router config with one LOCAL tier (model set -> no 404 warning).
_LOCAL_TIER_CONFIG = """\
[router]
mapping_version = "test.calibrate.0"

[[router.tiers]]
id            = "fast-local"
base_url      = "http://127.0.0.1:30001/v1"
model         = "test-model"
dialect       = "openai"
context_limit = 32768
privacy       = "local"
tool_support  = true
auth_env      = "ANVIL_FAST_LOCAL_KEY"

[router.presets]
planning = ["fast-local"]
"""

# A LOCAL + CLOUD topology: the verb must pass BOTH to run_live (cloud filtering
# is run_live's job, not the verb's).
_LOCAL_AND_CLOUD_CONFIG = _LOCAL_TIER_CONFIG + """
[[router.tiers]]
id            = "cloud"
base_url      = "https://api.anthropic.com"
model         = "claude-opus-4-20250514"
dialect       = "anthropic"
context_limit = 200000
privacy       = "cloud"
tool_support  = true
auth_env      = "ANTHROPIC_API_KEY"
"""


def _write_config(tmp_path, body=_LOCAL_TIER_CONFIG):
    cfg = tmp_path / "router.toml"
    cfg.write_text(body, encoding="utf-8")
    return str(cfg)


def _block_network(monkeypatch):
    """Fail hard if any socket is opened — proves the guard refuses before dialing."""
    def boom(*a, **k):  # pragma: no cover - must never fire
        raise AssertionError("calibrate attempted a network connection")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


def _clear_mode_env(monkeypatch):
    for var in ("ANVIL_MODE", "ANVIL_MODES_CONFIG", "ANVIL_CONFIG_AGENTIC",
                "ANVIL_CONFIG_FLEXIBILITY"):
        monkeypatch.delenv(var, raising=False)


def test_calibrate_help_documents_verb_and_flags(capsys):
    """AC1: `calibrate --help` documents the config source, --out, the guard, prompts."""
    with pytest.raises(SystemExit) as exc:
        calibrate_mod.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for token in ("--config", "--mode", "--out", "--endpoint",
                  "--i-understand-this-calls-real-tiers", "--eval-data"):
        assert token in out, token
    # The verb's purpose is described (guarded, reviewable candidate, no auto-promote).
    assert "candidate" in out.lower()


def test_calibrate_requires_a_config_selector(tmp_path, monkeypatch, capsys):
    """AC2: bare calibrate (no --config/--mode/env) is a usage error — never a
    silent default; run_live is never reached."""
    _clear_mode_env(monkeypatch)
    _block_network(monkeypatch)
    called = []
    monkeypatch.setattr(calibrate_mod, "run_live", lambda **k: called.append(k))
    rc = calibrate_mod.main(["--out", str(tmp_path / "c.json")])
    assert rc == 2
    assert called == []
    assert "no config selected" in capsys.readouterr().err


def test_calibrate_refuses_without_confirmation(tmp_path, monkeypatch, capsys):
    """AC2/AC4: with a config + endpoint but NO confirmation, run_live's guard
    refuses cleanly (exit 2) before any tier/judge call — no network, no file."""
    _clear_mode_env(monkeypatch)
    _block_network(monkeypatch)
    out = tmp_path / "candidate.json"
    rc = calibrate_mod.main([
        "--config", _write_config(tmp_path),
        "--out", str(out),
        "--endpoint", "fast-local=http://127.0.0.1:30001/v1",
        # deliberately NO --i-understand-this-calls-real-tiers
    ])
    assert rc == 2
    assert not out.exists()  # nothing written, nothing measured
    assert "not configured to run" in capsys.readouterr().err


def test_calibrate_refuses_without_endpoints(tmp_path, monkeypatch, capsys):
    """AC2/AC4: confirmation alone (no --endpoint) still refuses — the endpoints
    that CONFIRM which tiers to dial are mandatory."""
    _clear_mode_env(monkeypatch)
    _block_network(monkeypatch)
    out = tmp_path / "candidate.json"
    rc = calibrate_mod.main([
        "--config", _write_config(tmp_path),
        "--out", str(out),
        "--i-understand-this-calls-real-tiers",
        # deliberately NO --endpoint
    ])
    assert rc == 2
    assert not out.exists()
    assert "not configured to run" in capsys.readouterr().err


def test_calibrate_malformed_endpoint_is_a_clean_error(tmp_path, monkeypatch, capsys):
    """A bad --endpoint spec is a clean exit 2, not a traceback."""
    _clear_mode_env(monkeypatch)
    _block_network(monkeypatch)
    rc = calibrate_mod.main([
        "--config", _write_config(tmp_path),
        "--out", str(tmp_path / "c.json"),
        "--endpoint", "no-equals-sign",
        "--i-understand-this-calls-real-tiers",
    ])
    assert rc == 2
    assert "TIER=URL" in capsys.readouterr().err


def test_calibrate_wires_run_live_and_prints_promote(tmp_path, monkeypatch, capsys):
    """AC2/AC4: with config + endpoint + confirmation, the verb loads the config's
    tiers, calls run_live with the guard args intact, and prints the review->promote
    instruction. run_live is a FAKE (no tier/judge call); nothing is auto-promoted."""
    _clear_mode_env(monkeypatch)
    from anvil_serving.router import config as rconfig

    cfg_path = _write_config(tmp_path, _LOCAL_AND_CLOUD_CONFIG)
    loaded = rconfig.load(cfg_path)
    out = tmp_path / "candidate.json"

    seen = {}

    def fake_run_live(**kwargs):
        seen.update(kwargs)
        # A real run_live writes the candidate; mimic that so the summary path runs.
        kwargs["out_path"].write_text(
            json.dumps({"schema": "x", "mode": "live",
                        "entries": [{"tier_id": "fast-local", "work_class": "planning"}]}),
            encoding="utf-8",
        )
        return None  # the verb ignores the return; it works off the written file

    monkeypatch.setattr(calibrate_mod, "run_live", fake_run_live)

    rc = calibrate_mod.main([
        "--config", cfg_path,
        "--out", str(out),
        "--endpoint", "fast-local=http://127.0.0.1:30001/v1",
        "--i-understand-this-calls-real-tiers",
    ])
    assert rc == 0

    # The guard args reached run_live intact, with the config's tiers (BOTH the
    # local and cloud tier — the verb does not pre-filter; that is run_live's job).
    assert seen["confirm_calls_real_tiers"] is True
    assert seen["endpoints"] == {"fast-local": "http://127.0.0.1:30001/v1"}
    assert seen["tiers"] == loaded.tiers
    assert {t.id for t in seen["tiers"]} == {"fast-local", "cloud"}
    assert seen["out_path"] == out
    # The resolved mode is forwarded to run_live (None for a --config boot) so the
    # candidate fingerprints match the live serve's mode (ADR-0011 / T013).
    assert seen["mode"] is None

    # The review -> promote instruction is printed and points [router].profile_path
    # at the candidate; nothing was auto-promoted.
    printed = capsys.readouterr().out
    assert "profile_path" in printed
    assert str(out) in printed
    assert "Nothing was promoted" in printed
    assert "1 measured row(s)" in printed


def test_calibrate_rejects_missing_out_dir_before_running_batch(tmp_path, monkeypatch, capsys):
    """A missing --out directory is rejected BEFORE the expensive live batch, not as
    a late write error after real tiers were dialed. run_live must never be called."""
    _clear_mode_env(monkeypatch)
    called = []
    monkeypatch.setattr(calibrate_mod, "run_live", lambda **k: called.append(k))
    rc = calibrate_mod.main([
        "--config", _write_config(tmp_path),
        "--out", str(tmp_path / "nope" / "candidate.json"),  # 'nope' dir does not exist
        "--endpoint", "fast-local=http://127.0.0.1:30001/v1",
        "--i-understand-this-calls-real-tiers",
    ])
    assert rc == 2
    assert called == []  # the batch never ran — no measurement work lost
    assert "output directory does not exist" in capsys.readouterr().err


def test_calibrate_rejects_missing_eval_data_dir(tmp_path, monkeypatch, capsys):
    """A missing --eval-data directory is rejected cleanly (exit 2), not surfaced as a
    late FileNotFoundError traceback after the batch starts. run_live never runs. (Copilot #119)"""
    _clear_mode_env(monkeypatch)
    called = []
    monkeypatch.setattr(calibrate_mod, "run_live", lambda **k: called.append(k))
    rc = calibrate_mod.main([
        "--config", _write_config(tmp_path),
        "--out", str(tmp_path / "candidate.json"),
        "--eval-data", str(tmp_path / "no-such-fixtures"),  # missing dir
        "--endpoint", "fast-local=http://127.0.0.1:30001/v1",
        "--i-understand-this-calls-real-tiers",
    ])
    assert rc == 2
    assert called == []
    assert "eval-data directory does not exist" in capsys.readouterr().err


def test_calibrate_dispatches_through_cli(tmp_path, monkeypatch):
    """The verb is wired into the top-level CLI dispatch (`anvil-serving calibrate`)."""
    _clear_mode_env(monkeypatch)
    calls = []
    monkeypatch.setattr(calibrate_mod, "run_live", lambda **k: calls.append(k) or None)
    rc = cli.main([
        "calibrate",
        "--config", _write_config(tmp_path),
        "--out", str(tmp_path / "c.json"),
        "--endpoint", "fast-local=http://127.0.0.1:30001/v1",
        "--i-understand-this-calls-real-tiers",
    ])
    assert rc == 0
    assert len(calls) == 1


def test_calibrate_forwards_max_tokens_when_set(tmp_path, monkeypatch):
    """--max-tokens overrides run_live's default budget; unset -> not forwarded."""
    _clear_mode_env(monkeypatch)
    seen = {}
    monkeypatch.setattr(calibrate_mod, "run_live", lambda **k: seen.update(k) or None)

    calibrate_mod.main([
        "--config", _write_config(tmp_path),
        "--out", str(tmp_path / "c.json"),
        "--endpoint", "fast-local=http://127.0.0.1:30001/v1",
        "--i-understand-this-calls-real-tiers",
        "--max-tokens", "8192",
    ])
    assert seen["max_tokens"] == 8192

    seen.clear()
    calibrate_mod.main([
        "--config", _write_config(tmp_path),
        "--out", str(tmp_path / "c.json"),
        "--endpoint", "fast-local=http://127.0.0.1:30001/v1",
        "--i-understand-this-calls-real-tiers",
    ])
    assert "max_tokens" not in seen  # unset -> run_live's own default applies
