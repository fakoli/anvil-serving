"""Tests for the `anvil-serving voice` CLI verb (up / down / run / benchmark).

Dependency-light: stdlib only (argparse, subprocess isn't invoked). This unit
is foundation-only -- each subcommand loads + validates the manifest and
prints what it *would* do; no process is spawned, no network touched, no
GPU/torch import happens anywhere in this module or its import chain.
"""
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

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

AUDIO_TOPOLOGY = "examples/primary-node/operator-topology.toml"


def _audio_command(action, *args, runtime="runtime:dark-docker"):
    return [
        "audio", action, *args,
        "--topology", AUDIO_TOPOLOGY,
        "--command-host", "host:primary-node",
        "--command-runtime", runtime,
    ]


def _proxy_command(action, *args, topology=AUDIO_TOPOLOGY):
    return [
        "proxy", action, *args,
        "--topology", topology,
        "--command-host", "host:companion-node",
        "--command-runtime", "runtime:mini-native",
    ]


def _use_native_dark_audio_topology(monkeypatch):
    from dataclasses import replace

    from anvil_serving.topology import load_topology

    topology = load_topology(AUDIO_TOPOLOGY)
    topology = replace(
        topology,
        resources=tuple(
            replace(resource, runtime="dark-native", gpu_role=None)
            if resource.role in {"stt-serve", "tts-serve"}
            else resource
            for resource in topology.resources
        ),
    )
    monkeypatch.setattr(voice_cli, "load_topology", lambda path: topology)


@pytest.fixture(autouse=True)
def isolate_operator_home(tmp_path, monkeypatch):
    """Keep foundation-only voice CLI tests independent of live operator state."""
    config_home = tmp_path / "operator-home"
    config_home.mkdir(exist_ok=True)
    (config_home / "voice.toml").write_text(VALID_MANIFEST, encoding="utf-8")
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(config_home))


@pytest.fixture
def manifest_path(tmp_path):
    p = tmp_path / "voice.toml"
    p.write_text(VALID_MANIFEST, encoding="utf-8")
    return str(p)


def test_audio_subprocess_deadline_refuses_work_after_budget_expires():
    run = voice_cli._deadline_subprocess_runner(time.monotonic() - 1)

    with pytest.raises(voice_cli.subprocess.TimeoutExpired):
        run(["does-not-run"])


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "1"])
def test_audio_lifecycle_rejects_invalid_timeout(timeout):
    with pytest.raises(ValueError, match="positive finite"):
        voice_cli.execute_audio_lifecycle({}, "up", timeout_seconds=timeout)


@pytest.mark.parametrize(
    "argv",
    [
        ["audio", "up", "--timeout-seconds", "0"],
        ["audio", "up", "--timeout-seconds", "nan"],
        ["audio", "down", "--timeout-seconds", "7200.1"],
    ],
)
def test_audio_lifecycle_cli_rejects_invalid_timeout(argv):
    with pytest.raises(SystemExit) as exc:
        voice_cli.build_parser().parse_args(argv)

    assert exc.value.code == 2


@pytest.mark.parametrize("action", ["up", "down"])
def test_audio_lifecycle_cli_forwards_one_overall_timeout(action, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        voice_cli,
        "_resolve_audio_operation",
        lambda args: ({"voice": {"name": "test"}}, None, None, 0),
    )
    monkeypatch.setattr(voice_cli.voice_config, "describe", lambda data: "test")

    def fake_execute(data, selected_action, **kwargs):
        seen.update(kwargs)
        return {
            "action": selected_action,
            "dry_run": kwargs["dry_run"],
            "returncode": 0,
            "serves": [],
        }

    monkeypatch.setattr(voice_cli, "execute_audio_lifecycle", fake_execute)

    assert voice_cli.main(["audio", action, "--timeout-seconds", "12.5"]) == 0
    assert seen["timeout_seconds"] == 12.5


def test_audio_lifecycle_cli_defaults_to_five_minute_timeout():
    parser = voice_cli.build_parser()

    assert parser.parse_args(["audio", "up"]).timeout_seconds == 300.0
    assert parser.parse_args(["audio", "down"]).timeout_seconds == 300.0


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("up", ["stt-up", "tts-up", "proxy-up"]),
        ("down", ["proxy-down", "stt-down", "tts-down"]),
    ],
)
def test_root_voice_lifecycle_runs_shipped_colocated_manifest_without_topology(
    action, expected, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        voice_cli.stt_serve.STTServe,
        "bring_up",
        lambda self, **kwargs: calls.append("stt-up") or 0,
    )
    monkeypatch.setattr(
        voice_cli.tts_serve.TTSServe,
        "bring_up",
        lambda self, **kwargs: calls.append("tts-up") or 0,
    )
    monkeypatch.setattr(
        voice_cli.stt_serve.STTServe,
        "tear_down",
        lambda self, **kwargs: calls.append("stt-down") or 0,
    )
    monkeypatch.setattr(
        voice_cli.tts_serve.TTSServe,
        "tear_down",
        lambda self, **kwargs: calls.append("tts-down") or 0,
    )
    monkeypatch.setattr(
        voice_cli.proxy_serve.ProxyServe,
        "bring_up",
        lambda self, **kwargs: calls.append("proxy-up") or 0,
    )
    monkeypatch.setattr(
        voice_cli.proxy_serve.ProxyServe,
        "tear_down",
        lambda self, **kwargs: calls.append("proxy-down") or 0,
    )

    rc = anvil_cli.main([
        "voice",
        action,
        "--config",
        "examples/primary-node/voice.toml",
        "--dry-run",
    ])

    assert rc == 0
    assert calls == expected


def test_audio_model_free_rejection_happens_before_serve_construction(
    manifest_path, monkeypatch, capsys
):
    from dataclasses import replace

    from anvil_serving.topology import load_topology

    topology = load_topology("examples/primary-node/operator-topology.toml")
    topology = replace(
        topology,
        resources=tuple(
            replace(resource, host="companion-node", runtime="mini-native")
            if resource.role == "stt-serve" else resource
            for resource in topology.resources
        ),
    )
    monkeypatch.setattr(voice_cli, "load_topology", lambda path: topology)
    monkeypatch.setattr(
        voice_cli,
        "_audio_serves",
        lambda *args, **kwargs: pytest.fail("serve construction preceded capacity rejection"),
    )

    rc = voice_cli.main([
        "audio", "status",
        "--config", manifest_path,
        "--topology", "ignored.toml",
    ])

    assert rc == 3
    assert "prohibits 'stt'" in capsys.readouterr().err


@pytest.mark.parametrize("action", ["up", "down", "restart", "status", "logs"])
def test_proxy_lifecycle_is_mini_owned_and_never_invokes_audio_handlers(
    action, manifest_path, monkeypatch, capsys
):
    calls = []

    class FakeProxyProcess:
        def up(self, *, dry_run=False):
            calls.append(("up", dry_run))
            return {"action": "up", "owner": "companion-node", "returncode": 0}

        def down(self, *, dry_run=False):
            calls.append(("down", dry_run))
            return {"action": "down", "owner": "companion-node", "returncode": 0}

        def restart(self, *, dry_run=False):
            calls.append(("restart", dry_run))
            return {"action": "restart", "owner": "companion-node", "returncode": 0}

        def status(self):
            calls.append(("status", False))
            return {"action": "status", "owner": "companion-node", "returncode": 0}

        def logs(self, *, tail):
            calls.append(("logs", tail))
            return {
                "action": "logs",
                "owner": "companion-node",
                "returncode": 0,
                "lines": ["bounded"],
            }

    monkeypatch.setattr(
        voice_cli,
        "execute_audio_lifecycle",
        lambda *args, **kwargs: pytest.fail("proxy invoked audio lifecycle"),
    )
    monkeypatch.setattr(
        voice_cli,
        "_proxy_process_service",
        lambda *args, **kwargs: FakeProxyProcess(),
    )
    extra = ["--dry-run"] if action in {"up", "down", "restart"} else []

    rc = voice_cli.main(_proxy_command(
        action, "--config", manifest_path, *extra
    ))

    assert rc == 0
    assert calls == [(action, 200 if action == "logs" else action in {"up", "down", "restart"})]
    output = capsys.readouterr().out
    assert "owner=companion-node" in output
    assert "stt_proxy=http://127.0.0.1:30110/v1" in output


# --------------------------------------------------------------------------- #
# Managed (Docker-container) proxy lifecycle + direct-endpoint run + `voice
# up`/`down` aggregate (voice-proxy-managed-container).
# --------------------------------------------------------------------------- #
MANAGED_PROXY_MANIFEST = VALID_MANIFEST + """

[voice.proxy]
lifecycle = "managed"
serve_name = "realtime-proxy"
listen_host = "127.0.0.1"
listen_port = 8765
"""

DIRECT_PROXY_MANIFEST = VALID_MANIFEST + """

[voice.proxy]
lifecycle = "managed"
stt_url = "http://100.64.0.10:30110/v1"
tts_url = "http://100.64.0.10:30111/v1"
router_url = "http://100.64.0.10:8000/v1"
listen_host = "127.0.0.1"
listen_port = 8765
"""


@pytest.fixture
def managed_proxy_manifest(tmp_path):
    p = tmp_path / "voice_managed.toml"
    p.write_text(MANAGED_PROXY_MANIFEST, encoding="utf-8")
    return str(p)


@pytest.fixture
def direct_proxy_manifest(tmp_path):
    p = tmp_path / "voice_direct.toml"
    p.write_text(DIRECT_PROXY_MANIFEST, encoding="utf-8")
    return str(p)


def _no_topology_guard(monkeypatch):
    monkeypatch.setattr(
        voice_cli,
        "_resolve_proxy_operation",
        lambda *a, **k: pytest.fail("managed/direct proxy path resolved topology"),
    )


@pytest.mark.parametrize("action", ["up", "down", "restart"])
def test_managed_proxy_lifecycle_delegates_to_proxy_serve_without_topology(
    action, managed_proxy_manifest, monkeypatch, capsys
):
    _no_topology_guard(monkeypatch)
    calls = []
    monkeypatch.setattr(
        voice_cli.proxy_serve.ProxyServe,
        "bring_up",
        lambda self, **kw: calls.append(("bring_up", kw.get("dry_run"))) or 0,
    )
    monkeypatch.setattr(
        voice_cli.proxy_serve.ProxyServe,
        "tear_down",
        lambda self, **kw: calls.append(("tear_down", kw.get("dry_run"))) or 0,
    )

    rc = voice_cli.main(["proxy", action, "--config", managed_proxy_manifest, "--dry-run"])

    assert rc == 0
    if action == "up":
        assert calls == [("bring_up", True)]
    elif action == "down":
        assert calls == [("tear_down", True)]
    else:  # restart: down then up
        assert calls == [("tear_down", True), ("bring_up", True)]
    out = capsys.readouterr().out
    assert "managed serve_name=realtime-proxy" in out


def _make_run_fakes(*, address=("127.0.0.1", 8765), pool_size=1, calls=None):
    """Build the `(_FakeServer, _FakeThread, _FakePool)` triple `run`'s fakes
    need to stand in for `_build_realtime_server`'s return value and
    `serve_forever_in_background`'s thread, without binding a real socket or
    thread. When `calls` is given, `shutdown`/`server_close`/`join` each
    append their name to it (for shutdown-order assertions); otherwise
    they're no-ops."""

    def _record(name):
        if calls is not None:
            calls.append(name)

    class _FakeServer:
        server_address = address

        def shutdown(self):
            _record("shutdown")

        def server_close(self):
            _record("server_close")

    class _FakeThread:
        def join(self, timeout=None):
            _record("join")

    class _FakePool:
        size = pool_size

    return _FakeServer, _FakeThread, _FakePool


def _direct_run_fakes(monkeypatch, seen):
    _FakeServer, _FakeThread, _FakePool = _make_run_fakes(pool_size=2)

    def _fake_build(data, voice, *, allow_non_loopback=False):
        seen["stt"] = voice["stt"]["base_url"]
        seen["tts"] = voice["tts"]["base_url"]
        seen["llm"] = voice["llm"]["base_url"]
        seen["realtime_host"] = voice["realtime_host"]
        seen["realtime_port"] = voice["realtime_port"]
        seen["allow_non_loopback"] = allow_non_loopback
        return _FakeServer(), _FakePool()

    monkeypatch.setattr(voice_cli, "_check_required_endpoints_reachable", lambda voice: None)
    monkeypatch.setattr(voice_cli, "_build_realtime_server", _fake_build)
    monkeypatch.setattr(voice_cli, "serve_forever_in_background", lambda server: _FakeThread())
    monkeypatch.setattr(voice_cli, "_wait_forever_default", lambda: None)


def test_run_direct_host_port_override_allows_wildcard_bind(
    direct_proxy_manifest, monkeypatch
):
    _no_topology_guard(monkeypatch)
    seen = {}
    _direct_run_fakes(monkeypatch, seen)

    rc = voice_cli.main([
        "proxy", "run", "--config", direct_proxy_manifest, "--host", "0.0.0.0", "--port", "9999",
    ])

    assert rc == 0
    assert seen["realtime_host"] == "0.0.0.0"
    assert seen["realtime_port"] == 9999
    # A wildcard in-container bind opts out of the F2 loopback guard (published
    # only to 127.0.0.1 on the host).
    assert seen["allow_non_loopback"] is True


def test_profiles_command_lists_and_describes_profiles(tmp_path, capsys):
    manifest = tmp_path / "voice_profiles.toml"
    manifest.write_text(
        VALID_MANIFEST
        + """

[voice.profiles.dark-audio.stt]
base_url = "http://100.64.0.10:30110/v1"
model = "tdt_ctc-110m"
lifecycle = "external"

[voice.profiles.dark-audio.tts]
base_url = "http://100.64.0.10:30111/v1"
model = "kokoro"
lifecycle = "external"
""",
        encoding="utf-8",
    )

    assert voice_cli.main(["profiles", "--config", str(manifest)]) == 0
    out = capsys.readouterr().out
    assert "dark-audio" in out

    assert voice_cli.main(["profiles", "--config", str(manifest), "--profile", "dark-audio"]) == 0
    out = capsys.readouterr().out
    assert "dark-audio OK" in out
    assert "100.64.0.10:30110" in out


def test_run_resolves_profile_candidate_overlay(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "voice_profiles.toml"
    manifest.write_text(
        VALID_MANIFEST
        + """

[voice.profiles.dark-audio.stt]
base_url = "http://100.64.0.10:30110/v1"
model = "tdt_ctc-110m"
lifecycle = "external"

[voice.profiles.dark-audio.tts]
base_url = "http://100.64.0.10:30111/v1"
model = "kokoro"
lifecycle = "external"
""",
        encoding="utf-8",
    )
    overlay = tmp_path / "qwen3-32b.toml"
    overlay.write_text(
        """
[voice.llm]
base_url = "http://100.64.0.10:39000/v1"
model = "qwen3-32b-nvfp4"
api_key_env = "ANVIL_CANDIDATE_LLM_TOKEN"
""".strip(),
        encoding="utf-8",
    )
    seen = {}

    _FakeServer, _FakeThread, _FakePool = _make_run_fakes()

    def _fake_build(data, voice):
        seen["llm"] = voice["llm"]["base_url"]
        seen["llm_model"] = voice["llm"]["model"]
        seen["stt"] = voice["stt"]["base_url"]
        seen["tts"] = voice["tts"]["base_url"]
        return _FakeServer(), _FakePool()

    monkeypatch.setenv("ANVIL_CANDIDATE_LLM_TOKEN", "test-token")
    monkeypatch.setattr(voice_cli, "_check_required_endpoints_reachable", lambda voice: None)
    monkeypatch.setattr(voice_cli, "_build_realtime_server", _fake_build)
    monkeypatch.setattr(voice_cli, "serve_forever_in_background", lambda server: _FakeThread())
    monkeypatch.setattr(voice_cli, "_wait_forever_default", lambda: None)

    rc = voice_cli.main(_proxy_command(
        "run",
        "--config",
        str(manifest),
        "--profile",
        "dark-audio",
        "--candidate-overlay",
        str(overlay),
    ))

    assert rc == 0
    assert seen == {
        "llm": "http://100.64.0.10:39000/v1",
        "llm_model": "qwen3-32b-nvfp4",
        "stt": "http://127.0.0.1:30110/v1",
        "tts": "http://127.0.0.1:30111/v1",
    }
    out = capsys.readouterr().out
    assert "profile=dark-audio" in out
    assert "candidate=qwen3-32b" in out
    assert "llm_model=qwen3-32b-nvfp4" in out


def test_bridge_dry_run_prints_default_routes(capsys):
    rc = voice_cli.main(_proxy_command("bridge", "--dry-run"))

    assert rc == 0
    out = capsys.readouterr().out
    assert "stt 127.0.0.1:30110 -> 192.0.2.20:30010" in out
    assert "tts 127.0.0.1:30111 -> 192.0.2.20:30011" in out


def test_bridge_refuses_non_loopback_live_bind_without_ack(capsys):
    rc = voice_cli.main(_proxy_command("bridge", "--listen-host", "100.64.0.10"))

    assert rc == 2
    err = capsys.readouterr().err
    assert "Mini-local on 127.0.0.1" in err


def _free_tcp_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def test_bridge_module_forwards_bytes_over_loopback():
    target = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    target.bind(("127.0.0.1", 0))
    target.listen(1)
    target_port = target.getsockname()[1]
    bridge_port = _free_tcp_port()
    stop_event = threading.Event()

    def echo_once():
        conn, _addr = target.accept()
        with conn:
            payload = conn.recv(64)
            conn.sendall(b"echo:" + payload)
        target.close()

    echo_thread = threading.Thread(target=echo_once, daemon=True)
    echo_thread.start()

    route = voice_cli.voice_bridge.TCPBridgeRoute(
        "test",
        "127.0.0.1",
        bridge_port,
        "127.0.0.1",
        target_port,
    )
    bridge_thread = threading.Thread(
        target=voice_cli.voice_bridge.serve_until_stopped,
        args=([route], stop_event),
        daemon=True,
    )
    bridge_thread.start()

    deadline = time.monotonic() + 3.0
    last_error = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", bridge_port), timeout=1.0) as client:
                client.sendall(b"ping")
                assert client.recv(64) == b"echo:ping"
            break
        except OSError as exc:
            last_error = exc
            time.sleep(0.05)
    else:
        raise AssertionError("bridge did not accept a loopback connection: %s" % last_error)
    stop_event.set()
    bridge_thread.join(timeout=2.0)
    echo_thread.join(timeout=2.0)


def test_run_builds_real_session_pool_and_ws_server(tmp_path, monkeypatch, capsys):
    """A lower-level, non-mocked proof that `_build_realtime_server` really
    does construct a working `SessionPool` (real `VoicePipeline` instances,
    real STT/TTS/LLM stage configs from the manifest) behind a real (but
    ephemeral-port, loopback-only) `make_ws_server` socket -- no serve/router
    network call happens here (constructing a stage's config, and binding an
    idle pool of pipeline THREADS that never process anything, touches no
    network); only the endpoint-reachability preflight is mocked, since the
    STT/LLM/TTS serves this manifest points at are not actually running."""
    monkeypatch.setattr(voice_cli, "_check_required_endpoints_reachable", lambda voice: None)
    monkeypatch.setattr(voice_cli, "_wait_forever_default", lambda: None)

    port = _free_tcp_port()
    manifest = tmp_path / "voice_runnable.toml"
    manifest.write_text(
        VALID_MANIFEST.replace("realtime_port = 8765", "realtime_port = %d" % port),
        encoding="utf-8",
    )
    topology = tmp_path / "operator-topology.toml"
    with open(AUDIO_TOPOLOGY, "r", encoding="utf-8") as handle:
        topology_text = handle.read()
    topology.write_text(
        topology_text.replace(
            "127.0.0.1:8765/usage", "127.0.0.1:%d/usage" % port
        ),
        encoding="utf-8",
    )

    rc = voice_cli.main(_proxy_command(
        "run", "--config", str(manifest), topology=str(topology)
    ))
    assert rc == 0
    out = capsys.readouterr().out
    assert "ws://127.0.0.1:" in out
    assert "/v1/realtime" in out
    assert "pool size" in out


def test_cmd_up_uses_manifest_declared_audio_serve_name(tmp_path, monkeypatch):
    manifest = tmp_path / "voice_gepard.toml"
    serves_manifest = tmp_path / "serves.toml"
    manifest.write_text(
        VALID_MANIFEST.replace(
            '[voice.stt]\nbase_url = "http://127.0.0.1:8090/v1"\nmodel = "parakeet-tdt-0.6b-v3"',
            '[voice.stt]\nbase_url = "http://127.0.0.1:8090/v1"\nmodel = "parakeet-tdt-0.6b-v3"\nlifecycle = "external"',
        ).replace(
            '[voice.tts]\nbase_url = "http://127.0.0.1:8091/v1"\nmodel = "kokoro-82m"',
            (
                '[voice.tts]\n'
                'base_url = "http://127.0.0.1:39111"\n'
                'model = "gepard-1.0"\n'
                'protocol = "gepard"\n'
                'lifecycle = "managed"\n'
                'serve_name = "tts-gepard-fast"\n'
                'manifest_path = "%s"'
            ) % str(serves_manifest).replace("\\", "\\\\"),
        ),
        encoding="utf-8",
    )
    seen = {}

    from anvil_serving.voice.serves import tts as tts_serve

    def fake_bring_up(self, **kwargs):
        seen["serve_name"] = self.config.serve_name
        seen["manifest_path"] = self.config.manifest_path
        return 0

    monkeypatch.setattr(tts_serve.TTSServe, "bring_up", fake_bring_up)

    rc = voice_cli.main(_audio_command("up", "--config", str(manifest)))

    assert rc == 0
    assert seen == {
        "serve_name": "tts-gepard-fast",
        "manifest_path": str(serves_manifest),
    }


def test_cmd_up_runs_native_lifecycle_for_companion_node_style_manifest(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "voice_native.toml"
    manifest.write_text(
        VALID_MANIFEST.replace(
            '[voice.stt]\nbase_url = "http://127.0.0.1:8090/v1"\nmodel = "parakeet-tdt-0.6b-v3"',
            '[voice.stt]\nbase_url = "http://127.0.0.1:8090/v1"\nmodel = "parakeet-tdt-0.6b-v3"\n'
            'lifecycle = "native"\nstart_command = "python -m mlx_audio.server --port 8090"\n'
            'pid_file = "/tmp/stt.pid"\nlog_file = "/tmp/stt.log"',
        ).replace(
            '[voice.tts]\nbase_url = "http://127.0.0.1:8091/v1"\nmodel = "kokoro-82m"',
            '[voice.tts]\nbase_url = "http://127.0.0.1:8091/v1"\nmodel = "kokoro-82m"\n'
            'lifecycle = "native"\nstart_command = "python -m mlx_audio.server --port 8091"\n'
            'pid_file = "/tmp/tts.pid"\nlog_file = "/tmp/tts.log"',
        ),
        encoding="utf-8",
    )
    calls = []

    class FakeNative:
        def __init__(self, config):
            self.config = config

        def bring_up(self, *, dry_run=False):
            calls.append((self.config.kind, dry_run))
            return {
                "returncode": 0,
                "reason": "started",
                "pid": 123 if self.config.kind == "stt" else 456,
                "ready": True,
                "log_file": self.config.log_file,
            }

    monkeypatch.setattr(voice_cli.native_serve, "NativeServe", FakeNative)
    _use_native_dark_audio_topology(monkeypatch)

    rc = voice_cli.main(_audio_command(
        "up", "--config", str(manifest), "--dry-run", runtime="runtime:dark-native"
    ))

    assert rc == 0
    assert calls == [("stt", True), ("tts", True)]
    out = capsys.readouterr().out
    assert "stt native lifecycle rc=0" in out
    assert "tts native lifecycle rc=0" in out


def _pcm16_wav(pcm: bytes) -> bytes:
    fmt = struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["--input-wav", "sample.wav"], "must be provided together"),
        (["--reference-text", "words"], "must be provided together"),
        (["--scope", "audio", "--reference-text", "words"], "valid only"),
    ],
)
def test_cmd_benchmark_rejects_unpaired_or_wrong_scope_wav_options(
    manifest_path, argv, expected, capsys
):
    rc = voice_cli.main(["benchmark", "--config", manifest_path, *argv])

    assert rc == 2
    assert expected in capsys.readouterr().err


def test_cmd_benchmark_audio_scope_skips_llm_and_proxy(manifest_path, monkeypatch, capsys):
    seen = {}

    def fake_run(data, **kwargs):
        seen["data"] = data
        seen["kwargs"] = kwargs
        return {"scope": "audio", "audio_roundtrip_ms": 42.0}

    monkeypatch.setattr(
        voice_cli.voice_benchmark,
        "run_audio_benchmark_from_manifest",
        fake_run,
    )

    rc = voice_cli.main(
        ["benchmark", "--scope", "audio", "--config", manifest_path]
    )

    assert rc == 0
    assert seen["kwargs"] == {"profile": None}
    assert '"scope": "audio"' in capsys.readouterr().out


def test_cmd_benchmark_audio_scope_rejects_candidate_options(manifest_path, capsys):
    rc = voice_cli.main([
        "benchmark",
        "--scope",
        "audio",
        "--config",
        manifest_path,
        "--candidate",
        "not-used",
    ])

    assert rc == 2
    assert "does not call an LLM" in capsys.readouterr().err


def test_benchmark_evidence_write_is_atomic_on_publication_failure(tmp_path, monkeypatch):
    evidence_root = tmp_path / "evidence-root"
    evidence_root.mkdir()
    target = evidence_root / "voice.json"
    target.write_text('{"old": true}\n', encoding="utf-8")
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(evidence_root))
    monkeypatch.setattr(
        voice_cli.os,
        "link",
        lambda source, destination: (_ for _ in ()).throw(OSError("publication failed")),
    )

    with pytest.raises(OSError, match="publication failed"):
        voice_cli._write_benchmark_evidence(str(target), {"new": True})

    assert target.read_text(encoding="utf-8") == '{"old": true}\n'
    assert list(evidence_root.glob(".*.tmp")) == []


def test_cmd_benchmark_reports_unserializable_evidence_without_traceback(
    manifest_path, tmp_path, monkeypatch, capsys
):
    evidence_root = tmp_path / "evidence-root"
    evidence_root.mkdir()
    target = evidence_root / "voice.json"
    monkeypatch.setenv("ANVIL_BENCHMARK_EVIDENCE_DIR", str(evidence_root))
    monkeypatch.setattr(
        voice_cli.voice_benchmark,
        "run_benchmark_from_manifest",
        lambda data, **kwargs: {"evidence": {"invalid": object()}},
    )

    rc = voice_cli.main(
        ["benchmark", "--config", manifest_path, "--evidence-out", str(target)]
    )

    assert rc == 1
    assert "could not write evidence atomically" in capsys.readouterr().err
    assert not target.exists()


# --------------------------------------------------------------------------- #
# U2-b -- `_probe_endpoint` must distinguish "reachable but unhealthy" (a real
# HTTP error status) from "unreachable" (a genuine connection failure).
# `urllib.error.HTTPError` IS a `URLError` subclass, and real `urlopen()`
# RAISES it for a 4xx/5xx response rather than returning a response object
# with a non-2xx `.status` -- so a 500-returning-but-running serve used to be
# misreported through the generic "is unreachable" branch. Both tests below
# drive the REAL `urllib.request.urlopen` against a real 127.0.0.1 socket (no
# fake `_open`), matching this repo's "prove it against the real transport"
# convention (see tests/voice/test_llm_stage_incremental_emission.py).
# --------------------------------------------------------------------------- #
def _make_status_handler(status: int):
    class _StatusHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib-mandated method name
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # silence the test server's stderr access log

    return _StatusHandler


@pytest.fixture
def status_server():
    def _start(status: int):
        server = HTTPServer(("127.0.0.1", 0), _make_status_handler(status))  # 127.0.0.1, never localhost
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        return server, thread

    servers = []

    def _factory(status: int) -> str:
        server, thread = _start(status)
        servers.append((server, thread))
        return "http://127.0.0.1:%d" % server.server_port

    yield _factory
    for server, thread in servers:
        server.server_close()
        thread.join(timeout=2.0)


# --------------------------------------------------------------------------- #
# B1 (Opus gate, blocking) -- a serve that RESPONDS is reachable, even with a
# 401/403/404/405: `_probe_endpoint` must not misreport a token-authed router
# (which correctly rejects an unauthenticated `GET /v1/models`) as
# "unhealthy" and block `voice run` from starting. And when a token IS
# configured, it must actually be sent as `Authorization: Bearer <token>`,
# not silently dropped. All three tests below drive a REAL 127.0.0.1
# http.server (no fake `_open`), matching this file's existing convention.
# --------------------------------------------------------------------------- #
def _make_auth_handler(expected_token, received_headers):
    """A `GET /models` handler that answers 200 only when the exact
    `Authorization: Bearer <expected_token>` header is present (401
    otherwise), recording every `Authorization` header value it saw (or
    `None`) into `received_headers` -- this is the DIRECT proof that a caller
    did/didn't send a bearer token, independent of the 200-vs-401 status
    classification itself (a 401 is now "reachable" either way -- see B1)."""

    class _AuthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib-mandated method name
            received_headers.append(self.headers.get("Authorization"))
            if self.headers.get("Authorization") == "Bearer %s" % expected_token:
                self.send_response(200)
            else:
                self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # silence the test server's stderr access log

    return _AuthHandler


@pytest.fixture
def auth_server():
    servers = []

    def _factory(expected_token: str):
        received = []
        server = HTTPServer(("127.0.0.1", 0), _make_auth_handler(expected_token, received))  # 127.0.0.1, never localhost
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        servers.append((server, thread))
        return "http://127.0.0.1:%d" % server.server_port, received

    yield _factory
    for server, thread in servers:
        server.server_close()
        thread.join(timeout=2.0)


def test_probe_endpoint_strips_and_redacts_configured_bearer_token():
    def fake_open(req, timeout):
        headers = dict(req.header_items())
        assert headers["Authorization"] == "Bearer secret-token"
        raise ValueError("Invalid header value b'Bearer secret-token\\r'")

    problem = voice_cli._probe_endpoint(
        "anvil router (voice.llm)",
        "http://127.0.0.1:8000/v1",
        token=" secret-token\r\n",
        _open=fake_open,
    )

    assert problem is not None
    assert "secret-token" not in problem
    assert "Bearer <redacted>" in problem


def test_check_required_endpoints_reachable_resolves_and_sends_each_configured_token(auth_server, monkeypatch):
    """`_check_required_endpoints_reachable` resolves EACH table's own
    `api_key_env` (mirroring how the LLM/STT/TTS stages read theirs) and
    passes it into `_probe_endpoint` -- proven against three independent real
    servers, each requiring its own distinct token."""
    llm_url, llm_received = auth_server("llm-secret")
    stt_url, stt_received = auth_server("stt-secret")
    tts_url, tts_received = auth_server("tts-secret")
    monkeypatch.setenv("TEST_VOICE_LLM_TOKEN", " llm-secret\r\n")
    monkeypatch.setenv("TEST_VOICE_STT_TOKEN", "stt-secret")
    monkeypatch.setenv("TEST_VOICE_TTS_TOKEN", "tts-secret")

    voice = {
        "llm": {"base_url": llm_url, "api_key_env": "TEST_VOICE_LLM_TOKEN"},
        "stt": {"base_url": stt_url, "api_key_env": "TEST_VOICE_STT_TOKEN"},
        "tts": {"base_url": tts_url, "api_key_env": "TEST_VOICE_TTS_TOKEN"},
    }
    problem = voice_cli._check_required_endpoints_reachable(voice)
    assert problem is None
    assert llm_received == ["Bearer llm-secret"]
    assert stt_received == ["Bearer stt-secret"]
    assert tts_received == ["Bearer tts-secret"]
