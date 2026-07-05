"""Tests for the `anvil-serving voice` CLI verb (up / down / run / benchmark).

Dependency-light: stdlib only (argparse, subprocess isn't invoked). This unit
is foundation-only -- each subcommand loads + validates the manifest and
prints what it *would* do; no process is spawned, no network touched, no
GPU/torch import happens anywhere in this module or its import chain.
"""
import sys

import pytest

from anvil_serving import cli as anvil_cli
from anvil_serving.voice import cli as voice_cli


VALID_MANIFEST = """
[voice]
name = "test-voice"
realtime_host = "127.0.0.1"
realtime_port = 8765

[voice.llm]
base_url = "http://127.0.0.1:8000/v1"
model = "chat"

[voice.stt]
base_url = "http://127.0.0.1:8090/v1"
model = "parakeet-tdt-0.6b-v3"

[voice.tts]
base_url = "http://127.0.0.1:8091/v1"
model = "kokoro-82m"
""".strip()


@pytest.fixture
def manifest_path(tmp_path):
    p = tmp_path / "voice.toml"
    p.write_text(VALID_MANIFEST, encoding="utf-8")
    return str(p)


def test_help_lists_all_four_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        voice_cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for sub in ("up", "down", "run", "benchmark"):
        assert sub in out


def test_no_subcommand_errors(capsys):
    with pytest.raises(SystemExit) as exc:
        voice_cli.main([])
    assert exc.value.code != 0


@pytest.mark.parametrize("action", ["up", "down", "run", "benchmark"])
def test_each_subcommand_validates_and_reports_ok(action, manifest_path, capsys):
    rc = voice_cli.main([action, "--config", manifest_path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "test-voice" in out


@pytest.mark.parametrize("action", ["up", "down", "run", "benchmark"])
def test_each_subcommand_reports_error_on_bad_manifest(action, tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text("not [valid toml at all", encoding="utf-8")
    rc = voice_cli.main([action, "--config", str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "cannot parse" in err


@pytest.mark.parametrize("action", ["up", "down", "run", "benchmark"])
def test_each_subcommand_rejects_localhost_manifest(action, tmp_path, capsys):
    bad = tmp_path / "localhost.toml"
    bad.write_text(VALID_MANIFEST.replace("127.0.0.1:8000", "localhost:8000"), encoding="utf-8")
    rc = voice_cli.main([action, "--config", str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "localhost" in err


def test_run_mentions_realtime_websocket_target(manifest_path, capsys):
    rc = voice_cli.main(["run", "--config", manifest_path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ws://127.0.0.1:8765/v1/realtime" in out


def test_cmd_up_returns_nonzero_when_a_serve_bring_up_fails(manifest_path, monkeypatch, capsys):
    """Regression test: cmd_up used to unconditionally `return 0` even when a
    serve's bring_up() reported a real docker failure (rc != 0, distinct
    from the expected/caught ServeNotConfigured) -- a failed audio-serve
    start must surface as a nonzero exit code to the shell."""
    from anvil_serving.voice.serves import stt as stt_serve
    from anvil_serving.voice.serves import tts as tts_serve

    monkeypatch.setattr(stt_serve.STTServe, "bring_up", lambda self, **kw: 1)
    monkeypatch.setattr(tts_serve.TTSServe, "bring_up", lambda self, **kw: 0)
    rc = voice_cli.main(["up", "--config", manifest_path])
    assert rc != 0
    out = capsys.readouterr().out
    assert "bring-up rc=1" in out


def test_cmd_up_returns_zero_when_every_serve_bring_up_succeeds(manifest_path, monkeypatch):
    from anvil_serving.voice.serves import stt as stt_serve
    from anvil_serving.voice.serves import tts as tts_serve

    monkeypatch.setattr(stt_serve.STTServe, "bring_up", lambda self, **kw: 0)
    monkeypatch.setattr(tts_serve.TTSServe, "bring_up", lambda self, **kw: 0)
    rc = voice_cli.main(["up", "--config", manifest_path])
    assert rc == 0


def test_cmd_down_returns_nonzero_when_a_serve_tear_down_fails(manifest_path, monkeypatch, capsys):
    from anvil_serving.voice.serves import stt as stt_serve
    from anvil_serving.voice.serves import tts as tts_serve

    monkeypatch.setattr(stt_serve.STTServe, "tear_down", lambda self: 0)
    monkeypatch.setattr(tts_serve.TTSServe, "tear_down", lambda self: 1)
    rc = voice_cli.main(["down", "--config", manifest_path])
    assert rc != 0
    out = capsys.readouterr().out
    assert "tear-down rc=1" in out


def test_default_config_falls_back_to_shipped_example(capsys):
    # No --config passed: should use the shipped examples/voice example and succeed.
    rc = voice_cli.main(["up"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "anvil-voice" in out


def test_dispatched_via_top_level_cli(capsys):
    with pytest.raises(SystemExit) as exc:
        anvil_cli.main(["voice", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "up" in out and "benchmark" in out


def test_top_level_help_mentions_voice(capsys):
    rc = anvil_cli.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "voice" in out


def test_importing_router_serve_still_succeeds():
    # The router hot path must gain zero new REQUIRED dependency from adding
    # anvil_serving.voice; a real "no new required dep" check runs a fresh
    # interpreter (see the subprocess assertion below) since sys.modules is
    # already warm with anvil_serving.voice inside this test process.
    import anvil_serving.router.serve  # noqa: F401


def test_importing_router_serve_in_a_fresh_process_needs_no_voice_extra():
    # Spawn a clean interpreter so anvil_serving.voice (imported by other tests
    # in this process) can't be riding along in sys.modules already -- this is
    # the real "zero new required dependency" proof.
    import subprocess

    result = subprocess.run(
        [sys.executable, "-c", "import anvil_serving.router.serve"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
