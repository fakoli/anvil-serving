"""Voice bindings delegate supervised lifecycle work to service_runtime."""

from __future__ import annotations

from anvil_serving.voice import cli as voice_cli
from anvil_serving.voice import config as voice_config


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


def test_service_audio_lifecycle_requires_service_id():
    data = _voice_manifest()
    data["voice"]["stt"].pop("service")

    try:
        voice_config.validate_manifest(data)
    except voice_config.ConfigError as exc:
        assert "service" in str(exc)
    else:
        raise AssertionError("service lifecycle without an exact service id was accepted")


def test_service_audio_lifecycle_delegates_with_resolved_command_identity(monkeypatch, tmp_path):
    seen = {}

    def execute(action, service=None, **kwargs):
        seen.update(action=action, service=service, **kwargs)
        return {"action": action, "service": service, "applied": False, "returncode": 0}

    monkeypatch.setattr(voice_cli, "_service_execute", execute)
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))

    result = voice_cli.execute_audio_lifecycle(
        _voice_manifest(),
        "up",
        topology="/private/operator-topology.toml",
        command_host="host:voice-owner",
        command_runtime="runtime:voice-native",
        target="resource:stt-serve",
        transport="local",
        dry_run=True,
        timeout_seconds=12,
    )

    assert result["returncode"] == 0
    assert result["serves"][0]["state"] == "completed"
    expected = {
        "action": "up",
        "service": "voice-stt",
        "manifest": str(tmp_path / "services.toml"),
            "topology": "/private/operator-topology.toml",
            "topology_overlay": None,
        "command_host": "host:voice-owner",
        "command_runtime": "runtime:voice-native",
        "target": "resource:stt-serve",
        "transport": "local",
        "dry_run": True,
        "confirm": False,
        "tail": 100,
        "binding": None,
            "remote": False,
            "expected_model": "stt-test",
    }
    assert {key: value for key, value in seen.items() if key != "timeout_seconds"} == expected
    assert 0 < seen["timeout_seconds"] <= 12


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


def test_mcp_voice_plan_identifies_the_declared_service_binding(monkeypatch, tmp_path):
    from anvil_serving.control_plane.mcp.tools import voice as voice_tools

    manifest = tmp_path / "voice.toml"
    operator_home = tmp_path / "operator-home"
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(operator_home))
    manifest.write_text(
        """
[voice]
name = "voice-test"
[voice.llm]
base_url = "http://127.0.0.1:8000/v1"
model = "llm.voice"
[voice.stt]
base_url = "http://127.0.0.1:8090/v1"
model = "stt-test"
lifecycle = "service"
service = "voice-stt"
[voice.tts]
base_url = "http://127.0.0.1:8091/v1"
model = "tts-test"
lifecycle = "external"
""".strip(),
        encoding="utf-8",
    )

    plan = voice_tools._voice_manage_plan(str(manifest))

    assert plan["audio_serves"][0] == {
        "kind": "stt",
        "lifecycle": "service",
        "base_url": "http://127.0.0.1:8090/v1",
        "model": "stt-test",
        "service": "voice-stt",
        "services_manifest": str(operator_home / "services.toml"),
    }


def test_service_proxy_lifecycle_delegates_after_topology_resolution(monkeypatch, tmp_path):
    seen = {}
    data = _voice_manifest(stt_lifecycle="external")
    data["voice"]["proxy"] = {"lifecycle": "service", "service": "voice-proxy"}
    resolved = type("Resolved", (), {"data": data})()

    def execute(action, service=None, **kwargs):
        seen.update(action=action, service=service, **kwargs)
        return {"action": action, "service": service, "returncode": 0}

    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    monkeypatch.setattr(voice_cli, "_service_execute", execute)
    monkeypatch.setattr(
        voice_cli,
        "_resolve_proxy_operation",
        lambda args, action: (data, object(), resolved, None, 0),
    )
    args = voice_cli.argparse.Namespace(
        topology="/private/operator-topology.toml",
        command_host="host:voice-owner",
        command_runtime="runtime:voice-native",
        target="resource:realtime-proxy",
        transport="local",
        dry_run=True,
        confirm=False,
        tail=20,
        operation_timeout=10,
    )

    result = voice_cli._execute_proxy_service_lifecycle(args, "logs")

    assert result["returncode"] == 0
    assert seen["service"] == "voice-proxy"
    assert seen["action"] == "logs"
    assert seen["topology"] == "/private/operator-topology.toml"
    assert seen["command_host"] == "host:voice-owner"
    assert seen["command_runtime"] == "runtime:voice-native"
    assert seen["tail"] == 20
