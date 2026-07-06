"""Tests for the `anvil-serving voice` CLI verb (up / down / run / benchmark).

Dependency-light: stdlib only (argparse, subprocess isn't invoked). This unit
is foundation-only -- each subcommand loads + validates the manifest and
prints what it *would* do; no process is spawned, no network touched, no
GPU/torch import happens anywhere in this module or its import chain.
"""
import sys
import threading
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


@pytest.fixture
def manifest_path(tmp_path):
    p = tmp_path / "voice.toml"
    p.write_text(VALID_MANIFEST, encoding="utf-8")
    return str(p)


@pytest.fixture
def runnable_manifest_path(tmp_path):
    """Same as `manifest_path`, but `realtime_port = 0` (ephemeral) -- for the
    `run` tests below that actually bind a real (loopback-only) WebSocket
    server socket, so they never fight another test/process for a fixed
    port."""
    p = tmp_path / "voice_runnable.toml"
    p.write_text(VALID_MANIFEST.replace("realtime_port = 8765", "realtime_port = 0"), encoding="utf-8")
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


@pytest.mark.parametrize("action", ["up", "down", "benchmark"])
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


def test_run_reports_unreachable_endpoint_and_exits_nonzero(manifest_path, monkeypatch, capsys):
    """PUNCH-LIST #2, FIX #2: `run` must FAIL LOUDLY (a clear message, nonzero
    exit) when a required serve/router endpoint is unreachable -- never
    pretend the session pool is usable. Deterministic regardless of what
    else happens to be listening on the test machine: `_probe_endpoint` is
    mocked to always report the LLM router as unreachable."""
    monkeypatch.setattr(
        voice_cli, "_probe_endpoint",
        lambda name, base_url, **kw: "%s at %s is unreachable (mocked)" % (name, base_url),
    )
    rc = voice_cli.main(["run", "--config", manifest_path])
    assert rc != 0
    err = capsys.readouterr().err
    assert "unreachable" in err
    assert "voice.llm" in err


def test_run_builds_expected_components_with_fakes(manifest_path, monkeypatch, capsys):
    """`run` builds the real cascade (session pool + realtime server) and
    reports the realtime WS target, without touching any real serve/socket:
    `_check_required_endpoints_reachable` and `_build_realtime_server` are
    both faked, and `_wait_forever_default` returns immediately instead of
    blocking forever."""
    calls = []

    class _FakeServer:
        server_address = ("127.0.0.1", 8765)

        def shutdown(self):
            calls.append("shutdown")

        def server_close(self):
            calls.append("server_close")

    class _FakeThread:
        def join(self, timeout=None):
            calls.append("join")

    class _FakePool:
        size = 3

    def _fake_build(data, voice):
        calls.append("build")
        return _FakeServer(), _FakePool()

    monkeypatch.setattr(voice_cli, "_check_required_endpoints_reachable", lambda voice: None)
    monkeypatch.setattr(voice_cli, "_build_realtime_server", _fake_build)
    monkeypatch.setattr(voice_cli, "serve_forever_in_background", lambda server: _FakeThread())
    monkeypatch.setattr(voice_cli, "_wait_forever_default", lambda: None)

    rc = voice_cli.main(["run", "--config", manifest_path])
    assert rc == 0
    assert calls == ["build", "shutdown", "server_close", "join"]
    out = capsys.readouterr().out
    assert "ws://127.0.0.1:8765/v1/realtime" in out
    assert "pool size 3" in out


def test_run_builds_real_session_pool_and_ws_server(runnable_manifest_path, monkeypatch, capsys):
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

    rc = voice_cli.main(["run", "--config", runnable_manifest_path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ws://127.0.0.1:" in out
    assert "/v1/realtime" in out
    assert "pool size" in out


def test_run_refuses_non_loopback_bind_without_token(tmp_path, monkeypatch, capsys):
    """FIX #2's honesty requirement: a non-loopback `realtime_host` with no
    `realtime_token_env` configured must be REFUSED (a clear error, nonzero
    exit), never silently bound wide-open.

    U2-a hardened `voice_config.validate_manifest` itself to reject this
    combination, so this now fails at manifest-load time (`_load` inside
    `cmd_run`) rather than reaching `_build_realtime_server`/
    `realtime.ws.make_ws_server` at all -- belt (manifest validation, tested
    directly in tests/voice/test_voice_config.py) over suspenders
    (`make_ws_server`'s own F2 bind guard, still exercised directly by
    tests/voice/test_ws_transport.py::test_make_ws_server_refuses_non_loopback_host_without_token
    for any caller that constructs it without going through this manifest).
    Nothing real starts either way."""
    manifest = tmp_path / "voice_non_loopback.toml"
    manifest.write_text(
        VALID_MANIFEST.replace('realtime_host = "127.0.0.1"', 'realtime_host = "100.87.34.66"'),
        encoding="utf-8",
    )
    monkeypatch.setattr(voice_cli, "_check_required_endpoints_reachable", lambda voice: None)

    rc = voice_cli.main(["run", "--config", str(manifest)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "token" in err.lower()


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


def test_cmd_up_skips_external_lifecycle_serves(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "voice_external.toml"
    manifest.write_text(
        VALID_MANIFEST
        + '\n\n# external native Mini sidecars\n'
        + 'lifecycle = "external"\n',
        encoding="utf-8",
    )
    # Appending lifecycle at EOF attaches it to [voice.tts]; set STT explicitly.
    text = manifest.read_text(encoding="utf-8").replace(
        '[voice.stt]\nbase_url = "http://127.0.0.1:8090/v1"\nmodel = "parakeet-tdt-0.6b-v3"',
        '[voice.stt]\nbase_url = "http://127.0.0.1:8090/v1"\nmodel = "parakeet-tdt-0.6b-v3"\nlifecycle = "external"',
    )
    manifest.write_text(text, encoding="utf-8")

    from anvil_serving.voice.serves import stt as stt_serve
    from anvil_serving.voice.serves import tts as tts_serve

    monkeypatch.setattr(stt_serve.STTServe, "bring_up", lambda self, **kw: (_ for _ in ()).throw(AssertionError("skip stt")))
    monkeypatch.setattr(tts_serve.TTSServe, "bring_up", lambda self, **kw: (_ for _ in ()).throw(AssertionError("skip tts")))

    rc = voice_cli.main(["up", "--config", str(manifest)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "stt serve lifecycle is external" in out
    assert "tts serve lifecycle is external" in out


def test_cmd_down_returns_nonzero_when_a_serve_tear_down_fails(manifest_path, monkeypatch, capsys):
    from anvil_serving.voice.serves import stt as stt_serve
    from anvil_serving.voice.serves import tts as tts_serve

    monkeypatch.setattr(stt_serve.STTServe, "tear_down", lambda self: 0)
    monkeypatch.setattr(tts_serve.TTSServe, "tear_down", lambda self: 1)
    rc = voice_cli.main(["down", "--config", manifest_path])
    assert rc != 0
    out = capsys.readouterr().out
    assert "tear-down rc=1" in out


def test_cmd_down_skips_external_lifecycle_serves(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "voice_external.toml"
    manifest.write_text(
        VALID_MANIFEST.replace(
            '[voice.stt]\nbase_url = "http://127.0.0.1:8090/v1"\nmodel = "parakeet-tdt-0.6b-v3"',
            '[voice.stt]\nbase_url = "http://127.0.0.1:8090/v1"\nmodel = "parakeet-tdt-0.6b-v3"\nlifecycle = "external"',
        ).replace(
            '[voice.tts]\nbase_url = "http://127.0.0.1:8091/v1"\nmodel = "kokoro-82m"',
            '[voice.tts]\nbase_url = "http://127.0.0.1:8091/v1"\nmodel = "kokoro-82m"\nlifecycle = "external"',
        ),
        encoding="utf-8",
    )

    from anvil_serving.voice.serves import stt as stt_serve
    from anvil_serving.voice.serves import tts as tts_serve

    monkeypatch.setattr(stt_serve.STTServe, "tear_down", lambda self: (_ for _ in ()).throw(AssertionError("skip stt")))
    monkeypatch.setattr(tts_serve.TTSServe, "tear_down", lambda self: (_ for _ in ()).throw(AssertionError("skip tts")))

    rc = voice_cli.main(["down", "--config", str(manifest)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "stt serve lifecycle is external" in out
    assert "tts serve lifecycle is external" in out


def test_cmd_benchmark_prints_success_json(manifest_path, monkeypatch, capsys):
    monkeypatch.setattr(
        voice_cli.voice_benchmark,
        "run_benchmark_from_manifest",
        lambda data: {"ok": True, "ttfa_ms": 12.3},
    )

    rc = voice_cli.main(["benchmark", "--config", manifest_path])

    assert rc == 0
    out = capsys.readouterr().out
    assert '"ok": true' in out
    assert '"ttfa_ms": 12.3' in out


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


def test_probe_endpoint_reports_5xx_as_unhealthy_not_unreachable(status_server):
    base_url = status_server(500)
    problem = voice_cli._probe_endpoint("TTS serve", base_url)
    assert problem is not None
    assert "unhealthy" in problem
    assert "unreachable" not in problem
    assert "500" in problem


def test_probe_endpoint_reports_refused_connection_as_unreachable():
    # Bind an ephemeral port, then close it immediately -- nothing is
    # listening there anymore, so the connection is genuinely refused (a
    # real failure mode distinct from a running-but-unhealthy serve above).
    probe = HTTPServer(("127.0.0.1", 0), _make_status_handler(200))
    port = probe.server_port
    probe.server_close()

    problem = voice_cli._probe_endpoint("TTS serve", "http://127.0.0.1:%d" % port, timeout=1.0)
    assert problem is not None
    assert "unreachable" in problem
    assert "unhealthy" not in problem


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


def test_probe_endpoint_treats_401_as_reachable_not_blocking(auth_server):
    """The core B1 assertion: an endpoint that answers 401 to an
    unauthenticated probe is UP and routing traffic -- reachable, not
    "unhealthy" -- so it must not block `voice run` from starting."""
    base_url, received = auth_server("expected-secret")
    problem = voice_cli._probe_endpoint("anvil router (voice.llm)", base_url)  # no token passed
    assert problem is None
    assert received == [None]  # confirms no Authorization header was sent for this probe


def test_probe_endpoint_sends_configured_bearer_token(auth_server):
    """`_probe_endpoint(..., token=...)` actually SENDS the bearer token --
    proven by a server that only answers 200 (vs 401) when the exact header
    is present."""
    base_url, received = auth_server("expected-secret")
    problem = voice_cli._probe_endpoint("anvil router (voice.llm)", base_url, token="expected-secret")
    assert problem is None
    assert received == ["Bearer expected-secret"]


def test_check_required_endpoints_reachable_resolves_and_sends_each_configured_token(auth_server, monkeypatch):
    """`_check_required_endpoints_reachable` resolves EACH table's own
    `api_key_env` (mirroring how the LLM/STT/TTS stages read theirs) and
    passes it into `_probe_endpoint` -- proven against three independent real
    servers, each requiring its own distinct token."""
    llm_url, llm_received = auth_server("llm-secret")
    stt_url, stt_received = auth_server("stt-secret")
    tts_url, tts_received = auth_server("tts-secret")
    monkeypatch.setenv("TEST_VOICE_LLM_TOKEN", "llm-secret")
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


def test_run_does_not_refuse_start_when_endpoints_return_401(tmp_path, monkeypatch, auth_server):
    """B1 end-to-end regression through `cmd_run` itself: three real
    127.0.0.1 servers stand in for a token-authed router/STT/TTS that reject
    an unauthenticated `GET /models` with 401 (this manifest sets no
    `api_key_env`, so the probe is sent bare -- exactly the reported bug's
    scenario). `voice run` must proceed past the preflight instead of
    refusing to start. `_build_realtime_server`/the WS server/the forever-wait
    are faked (as in `test_run_builds_expected_components_with_fakes`) so
    this stays a preflight-classification test, not a full realtime-stack
    test; `_check_required_endpoints_reachable` and `_probe_endpoint` run
    for REAL, unmocked."""
    llm_url, _ = auth_server("irrelevant-1")
    stt_url, _ = auth_server("irrelevant-2")
    tts_url, _ = auth_server("irrelevant-3")

    manifest = tmp_path / "voice_401.toml"
    manifest.write_text(
        (
            "[voice]\n"
            'name = "test-voice"\n'
            'realtime_host = "127.0.0.1"\n'
            "realtime_port = 0\n"
            "\n"
            "[voice.llm]\n"
            'base_url = "%s"\n'
            'model = "chat"\n'
            "\n"
            "[voice.stt]\n"
            'base_url = "%s"\n'
            'model = "parakeet-tdt-0.6b-v3"\n'
            "\n"
            "[voice.tts]\n"
            'base_url = "%s"\n'
            'model = "kokoro-82m"\n'
        )
        % (llm_url, stt_url, tts_url),
        encoding="utf-8",
    )

    calls = []

    class _FakeServer:
        server_address = ("127.0.0.1", 0)

        def shutdown(self):
            calls.append("shutdown")

        def server_close(self):
            calls.append("server_close")

    class _FakeThread:
        def join(self, timeout=None):
            calls.append("join")

    class _FakePool:
        size = 1

    def _fake_build(data, voice):
        calls.append("build")
        return _FakeServer(), _FakePool()

    monkeypatch.setattr(voice_cli, "_build_realtime_server", _fake_build)
    monkeypatch.setattr(voice_cli, "serve_forever_in_background", lambda server: _FakeThread())
    monkeypatch.setattr(voice_cli, "_wait_forever_default", lambda: None)

    rc = voice_cli.main(["run", "--config", str(manifest)])
    assert rc == 0
    assert "build" in calls  # proves the preflight passed and `run` proceeded


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
