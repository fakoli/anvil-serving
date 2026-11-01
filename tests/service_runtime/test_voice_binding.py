"""Voice bindings delegate supervised lifecycle work to service_runtime."""

from __future__ import annotations

from anvil_serving.voice import cli as voice_cli


def _voice_manifest(*, stt_lifecycle="service", tts_lifecycle="external"):
    return {
        "voice": {
            "name": "voice-test",
            "llm": {"base_url": "http://127.0.0.1:8000/v1", "model": "llm.voice"},
            "stt": {
                "base_url": "http://127.0.0.1:8090/v1",
                "model": "stt-test",
                "lifecycle": stt_lifecycle,
                "service": "voice-stt",
            },
            "tts": {
                "base_url": "http://127.0.0.1:8091/v1",
                "model": "tts-test",
                "lifecycle": tts_lifecycle,
            },
        }
    }


def test_service_audio_status_and_logs_use_shared_dispatcher(monkeypatch, tmp_path, capsys):
    calls = []

    def execute(action, service=None, **kwargs):
        calls.append((action, service, kwargs))
        return {"action": action, "service": service, "returncode": 0, "lines": ["voice log"]}

    monkeypatch.setattr(voice_cli, "_service_execute", execute)
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    data = _voice_manifest()
    data["voice"]["tts"].update(lifecycle="service", service="voice-tts")
    args = voice_cli.argparse.Namespace(
        _resolved_audio=(data, None),
        topology="/private/operator-topology.toml",
        command_host="host:voice-owner",
        command_runtime="runtime:voice-native",
        target=None,
        transport="local",
        operation_timeout=9.0,
        ready_timeout=3.0,
        tail=7,
    )

    assert voice_cli.cmd_audio_status(args) == 0
    assert voice_cli.cmd_audio_logs(args) == 0
    assert [(action, service) for action, service, _kwargs in calls] == [
        ("status", "voice-stt"),
        ("status", "voice-tts"),
        ("logs", "voice-stt"),
        ("logs", "voice-tts"),
    ]
    assert calls[0][2]["tail"] == calls[1][2]["tail"] == 100
    assert calls[2][2]["tail"] == calls[3][2]["tail"] == 7
    assert "voice log" in capsys.readouterr().out
