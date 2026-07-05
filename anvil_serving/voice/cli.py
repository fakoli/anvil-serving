"""anvil-serving voice — up / down / run / benchmark (anvil task T001;
`run` wired to the real cascade for PUNCH-LIST #2).

The voice-pipeline verb for the anvil-style stdlib orchestrator described in
``docs/findings/2026-07-04-hf-speech-to-speech-review.md``: a realtime
VAD -> STT -> LLM -> TTS pipeline that talks to three wires — an STT serve, a
TTS serve, and the anvil router (Chat Completions) for the brain — instead of
running any of that in-process.

T001/T002 shipped manifest loading + validation + this CLI skeleton.
`up`/`down` (T006/T008) now bring up/tear down the STT and TTS serves via
`anvil_serving.voice.serves` -- which itself delegates to
`anvil_serving.serves`'s declarative docker-manifest lifecycle, so there is
still no raw `docker run` in this file. `benchmark` (T015) now replays one
turn end-to-end via `anvil_serving.voice.benchmark` and prints the
TTFA/latency/WER/RTF metrics as JSON. `run` now builds the REAL cascade
(STT/TTS out-of-process serves + the LLM stage routed at the anvil router,
wired via `anvil_serving.voice.pipeline.real_pipeline_factory_from_manifest`
into a bounded `anvil_serving.voice.realtime.pool.SessionPool`) and starts the
Realtime WebSocket server (`anvil_serving.voice.realtime.ws.make_ws_server`)
in the foreground -- promoting the wiring that used to live only in
`scripts/voice/realtime_sdk_client_demo.py`'s `build_server` into the package
so the CLI verb itself does this, not just a demo script.

Nothing here is proven against real audio hardware, a GPU, or a live STT/TTS
serve -- see the module docstring honesty notes in `voice/serves/`,
`voice/stages/stt.py`/`tts.py`, and `voice/benchmark.py`. `up`/`down`/
`benchmark` degrade gracefully (a clear message, not a crash) when the
serves manifest isn't configured yet or the serves aren't reachable. `run`
is different on purpose: once the manifest validates, it is a REQUEST to
actually stand up a live session pool, so an unreachable required serve/
router endpoint (or an unsafe non-loopback bind with no token configured)
must FAIL LOUDLY with a clear, non-crashing message -- never pretend the
pool is usable when it is not.
"""
import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

from . import benchmark as voice_benchmark
from . import config as voice_config
from .pipeline import real_pipeline_factory_from_manifest
from .realtime.pool import SessionPool
from .realtime.service import RealtimeService
from .realtime.ws import make_ws_server, serve_forever_in_background
from .serves import stt as stt_serve
from .serves import tts as tts_serve
from .serves._common import ServeNotConfigured

DEFAULT_MANIFEST = voice_config.DEFAULT_CONFIG
DEFAULT_POOL_SIZE = 4

#: How often `run`'s per-connection sender thread polls
#: `RealtimeService.drain_pipeline_events()` and forwards anything buffered to
#: the client -- mirrors `scripts/voice/realtime_sdk_client_demo.py`'s own
#: `SENDER_POLL_INTERVAL_S` (a plain sleep-poll, matching
#: `drain_pipeline_events`'s own non-blocking contract).
SENDER_POLL_INTERVAL_S = 0.05

#: Preflight reachability-probe timeout for `run`'s "fail loudly, don't
#: pretend" endpoint check (see `_probe_endpoint`).
ENDPOINT_PROBE_TIMEOUT_S = 3.0

# (kind, ServeConfig class, Serve class) -- drives the bring-up/tear-down loop
# in cmd_up/cmd_down; keeps them from repeating themselves per-serve-kind.
_AUDIO_SERVES = (
    ("stt", stt_serve.STTServeConfig, stt_serve.STTServe),
    ("tts", tts_serve.TTSServeConfig, tts_serve.TTSServe),
)


def _audio_serves(voice: dict):
    for kind, config_cls, serve_cls in _AUDIO_SERVES:
        table = voice.get(kind, {})
        config = config_cls(base_url=table.get("base_url", ""), model=table.get("model", ""))
        yield kind, serve_cls(config)


def _load(config_path):
    """Load + validate the manifest; returns (data, None) or (None, error message)."""
    try:
        return voice_config.load_manifest(config_path), None
    except voice_config.ConfigError as exc:
        return None, str(exc)


def cmd_up(args):
    data, err = _load(args.config)
    if err:
        print("voice up: %s" % err, file=sys.stderr)
        return 2
    print("voice up: manifest OK -- %s" % voice_config.describe(data))
    voice = data.get("voice", {})
    exit_code = 0
    for kind, serve in _audio_serves(voice):
        try:
            rc = serve.bring_up()
            print("voice up: %s serve bring-up rc=%s" % (kind, rc))
            if rc != 0:
                exit_code = 1
        except ServeNotConfigured as exc:
            print("voice up: %s serve not configured yet -- %s" % (kind, exc))
    print("voice up: TODO -- the realtime server process itself ships in a later unit.")
    return exit_code


def cmd_down(args):
    data, err = _load(args.config)
    if err:
        print("voice down: %s" % err, file=sys.stderr)
        return 2
    print("voice down: manifest OK -- %s" % voice_config.describe(data))
    voice = data.get("voice", {})
    exit_code = 0
    for kind, serve in _audio_serves(voice):
        try:
            rc = serve.tear_down()
            print("voice down: %s serve tear-down rc=%s" % (kind, rc))
            if rc != 0:
                exit_code = 1
        except ServeNotConfigured as exc:
            print("voice down: %s serve not configured yet -- %s" % (kind, exc))
    print("voice down: TODO -- realtime-server process teardown ships in a later unit.")
    return exit_code


def _probe_endpoint(
    name: str, base_url: str, *, timeout: float = ENDPOINT_PROBE_TIMEOUT_S, _open: Callable[..., Any] = urllib.request.urlopen,
) -> Optional[str]:
    """Cheap reachability probe: ``GET {base_url}/models`` (every
    OpenAI-compatible server -- the anvil router included, see
    ``anvil_serving/router/discovery.py`` -- exposes this).

    Returns ``None`` if it answers 2xx; otherwise a short, human-readable
    problem string (never raises). This backs `run`'s "fail loudly, don't
    pretend a live capability is proven" preflight -- see the module
    docstring and CLAUDE.md's own rule of the same name. Injectable
    (``_open``) so tests can simulate reachable/unreachable without a real
    socket.

    U2-b fix: ``urllib.error.HTTPError`` IS a ``URLError`` subclass, and real
    ``urlopen()`` RAISES it for a 4xx/5xx response instead of returning a
    response object with a non-2xx ``.status`` -- so the ``200 <= status <
    300`` check below was unreachable against a real endpoint: a
    running-but-unhealthy serve (e.g. a 500 from a half-initialized model
    server) was misreported as "unreachable" (indistinguishable from a
    genuine connection failure). ``HTTPError`` is now caught FIRST and
    reported distinctly as "unhealthy"; the generic ``URLError``/``OSError``
    branch is left in place both for a real connection failure and as a
    defensive fallback for any injected ``_open`` fake that returns (rather
    than raises) a non-2xx response.
    """
    url = base_url.rstrip("/") + "/models"
    try:
        with _open(url, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
    except urllib.error.HTTPError as exc:
        return "%s at %s returned HTTP %s (unhealthy)" % (name, url, exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return "%s at %s is unreachable (%s)" % (name, url, exc)
    if not (200 <= status < 300):
        return "%s at %s returned HTTP %s (unhealthy)" % (name, url, status)
    return None


def _check_required_endpoints_reachable(voice: Dict[str, Any]) -> Optional[str]:
    """Probe the LLM router + STT/TTS serves declared in the manifest; return
    the first problem found, or ``None`` if all three answer. Calls the
    MODULE-LEVEL ``_probe_endpoint`` (not a private closure) so tests can
    monkeypatch it directly."""
    for name, table_key in (("anvil router (voice.llm)", "llm"), ("STT serve (voice.stt)", "stt"), ("TTS serve (voice.tts)", "tts")):
        base_url = voice.get(table_key, {}).get("base_url", "")
        problem = _probe_endpoint(name, base_url)
        if problem:
            return problem
    return None


def _build_realtime_server(data: dict, voice: Dict[str, Any]):
    """Wire the REAL cascade together: a `SessionPool` of real `VoicePipeline`
    instances (STT/TTS out-of-process serves + the LLM stage routed at the
    anvil router -- see `pipeline.real_pipeline_factory_from_manifest`)
    behind a Realtime WebSocket server (`realtime.ws.make_ws_server`),
    mirroring `scripts/voice/realtime_sdk_client_demo.py`'s own
    `build_server` so `anvil-serving voice run` does not need that script.

    Returns ``(server, pool)`` -- ``server`` is constructed (its socket is
    bound) but `serve_forever_in_background` has not been called on it yet.

    Raises :class:`ValueError` if `voice.realtime_host` is a non-loopback
    address with no `realtime_token_env` configured (`make_ws_server`'s own
    F2 guard, honored here rather than duplicated) -- the caller (`cmd_run`)
    turns that into a clean, non-crashing CLI error instead of a traceback.
    Nothing is bound/started before that guard runs.
    """
    pool_size = int(voice.get("pool_size", DEFAULT_POOL_SIZE))
    pipeline_factory = real_pipeline_factory_from_manifest(data)
    pool = SessionPool(size=pool_size, pipeline_factory=pipeline_factory)
    session_counter = [0]

    def on_connect(conn, path) -> None:
        session_counter[0] += 1
        session_id = "voice-run-%d" % session_counter[0]
        try:
            unit = pool.claim(session_id)
        except Exception:  # noqa: BLE001 - SessionPoolExhausted: reject cleanly, matching the Realtime API's own behavior
            conn.send_json({"type": "error", "event_id": "evt_reject", "error": {
                "type": "session_limit_reached", "message": "no free session slot",
            }})
            conn.close(code=1008, reason="session_limit_reached")
            return

        service = RealtimeService(pipeline=unit.pipeline, send_event=conn.send_json, session_id=session_id)
        unit.service = service
        conn.send_json({"type": "session.created", "event_id": "evt_session", "session": {"id": session_id}})

        stop_sender = threading.Event()

        def sender_loop() -> None:
            while not stop_sender.is_set():
                for event in service.drain_pipeline_events():
                    try:
                        conn.send_text(json.dumps(event))
                    except OSError:
                        return
                time.sleep(SENDER_POLL_INTERVAL_S)

        sender_thread = threading.Thread(target=sender_loop, daemon=True, name="voice-run-sender-%s" % session_id)
        sender_thread.start()
        try:
            while True:
                text = conn.recv_text()
                if text is None:
                    break
                service.handle_client_message(text)
        finally:
            stop_sender.set()
            sender_thread.join(timeout=2.0)
            pool.release(unit)

    server = make_ws_server(
        voice.get("realtime_host", "127.0.0.1"),
        int(voice.get("realtime_port", 8765)),
        on_connect,
        extra_routes={"/pool": pool.pool_status, "/usage": pool.usage_stats},
        token_env=voice.get("realtime_token_env"),
    )
    return server, pool


def _wait_forever_default() -> None:
    """Blocks the calling thread until interrupted (Ctrl+C). A plain sleep
    loop so `cmd_run` has something interruptible to wait on without needing
    the main thread to own the WS server's own `serve_forever()` (that runs
    on its own background thread -- see `serve_forever_in_background`).
    A module-level function (not a closure) so tests can monkeypatch it to
    return immediately instead of actually blocking.
    """
    while True:
        time.sleep(1.0)


def cmd_run(args):
    data, err = _load(args.config)
    if err:
        print("voice run: %s" % err, file=sys.stderr)
        return 2
    voice = data.get("voice", {})
    print("voice run: manifest OK -- %s" % voice_config.describe(data))

    problem = _check_required_endpoints_reachable(voice)
    if problem:
        print(
            "voice run: %s -- refusing to start a session pool against an "
            "unreachable endpoint. Bring up the configured serves/router "
            "first (`anvil-serving voice up`, `anvil-serving serve`) and "
            "retry." % problem,
            file=sys.stderr,
        )
        return 1

    try:
        server, pool = _build_realtime_server(data, voice)
    except ValueError as exc:
        print("voice run: %s" % exc, file=sys.stderr)
        return 2

    thread = serve_forever_in_background(server)
    host, port = server.server_address[:2]
    print(
        "voice run: realtime server up at ws://%s:%d/v1/realtime (pool size %d)"
        % (host, port, pool.size)
    )
    try:
        _wait_forever_default()
    except KeyboardInterrupt:
        print("\nvoice run: interrupted -- shutting down")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)
    return 0


def cmd_benchmark(args):
    data, err = _load(args.config)
    if err:
        print("voice benchmark: %s" % err, file=sys.stderr)
        return 2
    print("voice benchmark: manifest OK -- %s" % voice_config.describe(data))
    try:
        result = voice_benchmark.run_benchmark_from_manifest(data)
    except Exception as exc:  # noqa: BLE001 - the configured serves may simply not be up yet
        print(
            "voice benchmark: could not reach the configured STT/LLM/TTS serves (%s); "
            "bring them up with `anvil-serving voice up` first. Nothing was measured." % exc
        )
        return 0
    print(json.dumps(result, indent=2))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="anvil-serving voice",
        description="Voice-pipeline verb: an anvil-style stdlib VAD->STT->LLM->TTS "
                    "orchestrator + realtime server (see "
                    "docs/findings/2026-07-04-hf-speech-to-speech-review.md).",
    )
    sub = p.add_subparsers(dest="action", required=True)

    def add_config(sp):
        sp.add_argument(
            "--config",
            help="voice manifest TOML; defaults to the shipped example when present (%s)"
            % DEFAULT_MANIFEST,
        )

    sp = sub.add_parser("up", help="bring up the STT/TTS serves + realtime server (validates manifest)")
    add_config(sp)

    sp = sub.add_parser("down", help="tear down the STT/TTS serves + realtime server")
    add_config(sp)

    sp = sub.add_parser("run", help="run the realtime server in the foreground")
    add_config(sp)

    sp = sub.add_parser("benchmark", help="replay a recorded session end-to-end and report latency")
    add_config(sp)

    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    handlers = {
        "up": cmd_up,
        "down": cmd_down,
        "run": cmd_run,
        "benchmark": cmd_benchmark,
    }
    return handlers[args.action](args)


if __name__ == "__main__":
    raise SystemExit(main())
