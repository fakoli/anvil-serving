import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "examples" / "openclaw" / "validate.py"
SPEC = importlib.util.spec_from_file_location("openclaw_validate", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
openclaw_validate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(openclaw_validate)


def test_wire_form_uses_openclaw_vocabulary_with_unmapped_global_presets(capsys):
    from anvil_serving.router.config import load
    from anvil_serving.router.intent import PRESETS

    cfg = load(str(openclaw_validate.CONFIG_PATH))
    configured = {str(preset).lower() for preset in cfg.presets}
    global_ids = {preset.id for preset in PRESETS}

    assert {"ocr", "vision"} <= global_ids
    assert {"ocr", "vision"}.isdisjoint(configured)
    assert openclaw_validate.check_wire_form(None, None)

    output = capsys.readouterr().out
    assert "router-global preset(s) outside the OpenClaw contract: ['ocr', 'vision']" in output
    assert "[wire-form] PASS" in output


def test_aggregate_fixture_command_passes(capsys):
    fire_log = MODULE_PATH.with_name("hook-fire-log.jsonl")

    assert (
        openclaw_validate.main(
            ["--assert-wire-form", "--assert-fire-cadence", str(fire_log)]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "[wire-form] PASS" in output
    assert "[fire-cadence] 1.0 fire/message (PASS)" in output
    assert "RESULT: PASS" in output


def test_documented_cli_command_passes_from_another_working_directory(tmp_path):
    fire_log = MODULE_PATH.with_name("hook-fire-log.jsonl")

    result = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--assert-wire-form",
            "--assert-fire-cadence",
            str(fire_log),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT: PASS" in result.stdout


def test_plugin_decision_log_skips_native_models_but_checks_anvil_models(capsys):
    decision_log = (
        MODULE_PATH.parents[2]
        / "plugins"
        / "openclaw-anvil-intent-router"
        / "decision_log.fixture.jsonl"
    )

    assert openclaw_validate.main(["--assert-wire-form", "--capture", str(decision_log)]) == 0

    output = capsys.readouterr().out
    assert "claude-sonnet-4-5" not in output
    assert "[wire-form] PASS" in output


def test_explicit_capture_fails_closed_without_a_strict_model(tmp_path, capsys):
    bad_captures = [
        {},
        {"model": 0},
        {"model": "chat\n"},
        {"providerOverride": "anvil", "model": "chat"},
        {
            "destination": "typo",
            "providerOverride": None,
            "modelOverride": "not-a-preset",
        },
        {
            "destination": "anvil",
            "providerOverride": "anvil",
            "model": "chat",
            "modelOverride": "not-a-preset",
        },
        {
            "destination": "native",
            "providerOverride": "anthropic",
            "modelOverride": None,
        },
        {
            "destination": "native",
            "providerOverride": "anthropic",
            "modelOverride": {"bad": 1},
        },
        {
            "destination": "native",
            "providerOverride": "   ",
            "modelOverride": "claude",
        },
    ]

    for index, payload in enumerate(bad_captures):
        capture = tmp_path / f"bad-{index}.json"
        capture.write_text(json.dumps(payload), encoding="utf-8")
        assert (
            openclaw_validate.main(
                ["--assert-wire-form", "--capture", str(capture)]
            )
            == 1
        )

    output = capsys.readouterr().out
    assert output.count("RESULT: FAIL") == len(bad_captures)


def test_wire_form_fails_when_plugin_preset_is_not_configured(monkeypatch, capsys):
    from anvil_serving.router import config as config_module

    cfg = config_module.load(str(openclaw_validate.CONFIG_PATH))
    presets = dict(cfg.presets)
    presets.pop("planning")
    monkeypatch.setattr(
        config_module,
        "load",
        lambda _path: replace(cfg, presets=presets),
    )

    assert not openclaw_validate.check_wire_form(None, None)

    output = capsys.readouterr().out
    assert "OpenClaw preset(s) are not configured" in output
    assert "'planning'" in output
    assert "[wire-form] FAIL" in output


def test_config_option_fails_for_target_missing_plugin_preset(tmp_path, capsys):
    config_text = openclaw_validate.CONFIG_PATH.read_text(encoding="utf-8")
    target_config = tmp_path / "target-router.toml"
    target_config.write_text(
        config_text.replace('planning     = ["heavy-local"]\n', ""),
        encoding="utf-8",
    )

    assert (
        openclaw_validate.main(
            ["--assert-wire-form", "--config", str(target_config)]
        )
        == 1
    )

    output = capsys.readouterr().out
    assert "OpenClaw preset(s) are not configured" in output
    assert "'planning'" in output
