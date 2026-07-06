"""anvil-serving voice — up / down / start / stop / run / benchmark (anvil task T001;
`run` wired to the real cascade for PUNCH-LIST #2).

The voice-pipeline verb for the anvil-style stdlib orchestrator described in
``docs/findings/2026-07-04-hf-speech-to-speech-review.md``: a realtime
VAD -> STT -> LLM -> TTS pipeline that talks to three wires — an STT serve, a
TTS serve, and the anvil router (Chat Completions) for the brain — instead of
running any of that in-process.

T001/T002 shipped manifest loading + validation + this CLI skeleton.
`up`/`down` (T006/T008) bring up/tear down the STT and TTS serves via
`anvil_serving.voice.serves`: Docker-managed endpoints delegate to
`anvil_serving.serves`'s declarative lifecycle, while `lifecycle = "native"`
endpoints (Fakoli Mini's MLX Audio setup) launch/stop trusted manifest
commands with PID/log files. `benchmark` (T015) now replays one turn end-to-end
via `anvil_serving.voice.benchmark` and prints the TTFA/latency/WER/RTF metrics
as JSON. `run` now builds the REAL cascade (STT/TTS out-of-process serves + the
LLM stage routed at the anvil router, wired via
`anvil_serving.voice.pipeline.real_pipeline_factory_from_manifest` into a
bounded `anvil_serving.voice.realtime.pool.SessionPool`) and starts the
Realtime WebSocket server (`anvil_serving.voice.realtime.ws.make_ws_server`) in
the foreground -- promoting the wiring that used to live only in
`scripts/voice/realtime_sdk_client_demo.py`'s `build_server` into the package so
the CLI verb itself does this, not just a demo script.

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
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

from . import benchmark as voice_benchmark
from . import config as voice_config
from .realtime.app import build_realtime_server_from_manifest
from .realtime.ws import serve_forever_in_background
from .serves import native as native_serve
from .serves import stt as stt_serve
from .serves import tts as tts_serve
from .serves._common import ServeNotConfigured

DEFAULT_MANIFEST = voice_config.DEFAULT_CONFIG
#: Preflight reachability-probe timeout for `run`'s "fail loudly, don't
#: pretend" endpoint check (see `_probe_endpoint`).
ENDPOINT_PROBE_TIMEOUT_S = 3.0


def _redact_bearer_token(text: str) -> str:
    return re.sub(r"Bearer\s+[^'\"\s\\]+", "Bearer <redacted>", text)

# (kind, ServeConfig class, Serve class) -- drives the bring-up/tear-down loop
# in cmd_up/cmd_down; keeps them from repeating themselves per-serve-kind.
_AUDIO_SERVES = (
    ("stt", stt_serve.STTServeConfig, stt_serve.STTServe),
    ("tts", tts_serve.TTSServeConfig, tts_serve.TTSServe),
)


def _audio_serves(voice: dict):
    for kind, config_cls, serve_cls in _AUDIO_SERVES:
        table = voice.get(kind, {})
        lifecycle = table.get("lifecycle", "managed")
        config = config_cls(base_url=table.get("base_url", ""), model=table.get("model", ""))
        yield kind, lifecycle, table, serve_cls(config)


def _print_native_result(prefix: str, kind: str, result: dict) -> None:
    pid = result.get("pid")
    details = []
    if pid:
        details.append("pid=%s" % pid)
    if result.get("ready") is not None:
        details.append("ready=%s" % result.get("ready"))
    if result.get("reason"):
        details.append("reason=%s" % result.get("reason"))
    if result.get("dry_run"):
        details.append("dry-run")
    suffix = " (%s)" % ", ".join(details) if details else ""
    print("%s: %s native lifecycle rc=%s%s" % (prefix, kind, result.get("returncode"), suffix))
    if result.get("log_file"):
        print("%s: %s log %s" % (prefix, kind, result["log_file"]))
    if result.get("error"):
        print("%s: %s %s" % (prefix, kind, result["error"]), file=sys.stderr)


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
    for kind, lifecycle, table, serve in _audio_serves(voice):
        if lifecycle == "external":
            print("voice up: %s serve lifecycle is external; skipping managed bring-up" % kind)
            continue
        if lifecycle == "native":
            try:
                native = native_serve.NativeServe(
                    native_serve.NativeServeConfig.from_table(kind, table)
                )
                result = native.bring_up(dry_run=getattr(args, "dry_run", False))
                _print_native_result("voice up", kind, result)
                if result.get("returncode") != 0:
                    exit_code = 1
            except Exception as exc:  # noqa: BLE001 - configured native process may fail locally
                print("voice up: %s native lifecycle failed -- %s" % (kind, exc), file=sys.stderr)
                exit_code = 1
            continue
        try:
            dry_run = getattr(args, "dry_run", False)
            rc = serve.bring_up(dry_run=True) if dry_run else serve.bring_up()
            print("voice up: %s serve bring-up rc=%s" % (kind, rc))
            if rc != 0:
                exit_code = 1
        except ServeNotConfigured as exc:
            print("voice up: %s serve not configured yet -- %s" % (kind, exc))
    print("voice up: realtime server is run in the foreground with `anvil-serving voice run`.")
    return exit_code


def cmd_down(args):
    data, err = _load(args.config)
    if err:
        print("voice down: %s" % err, file=sys.stderr)
        return 2
    print("voice down: manifest OK -- %s" % voice_config.describe(data))
    voice = data.get("voice", {})
    exit_code = 0
    for kind, lifecycle, table, serve in _audio_serves(voice):
        if lifecycle == "external":
            print("voice down: %s serve lifecycle is external; skipping managed tear-down" % kind)
            continue
        if lifecycle == "native":
            try:
                native = native_serve.NativeServe(
                    native_serve.NativeServeConfig.from_table(kind, table)
                )
                result = native.tear_down(dry_run=getattr(args, "dry_run", False))
                _print_native_result("voice down", kind, result)
                if result.get("returncode") != 0:
                    exit_code = 1
            except Exception as exc:  # noqa: BLE001 - configured native process may fail locally
                print("voice down: %s native lifecycle failed -- %s" % (kind, exc), file=sys.stderr)
                exit_code = 1
            continue
        try:
            dry_run = getattr(args, "dry_run", False)
            rc = serve.tear_down(dry_run=True) if dry_run else serve.tear_down()
            print("voice down: %s serve tear-down rc=%s" % (kind, rc))
            if rc != 0:
                exit_code = 1
        except ServeNotConfigured as exc:
            print("voice down: %s serve not configured yet -- %s" % (kind, exc))
    print("voice down: realtime server foreground process stops with Ctrl+C in `anvil-serving voice run`.")
    return exit_code


def _probe_endpoint(
    name: str, base_url: str, *, timeout: float = ENDPOINT_PROBE_TIMEOUT_S,
    token: Optional[str] = None, _open: Callable[..., Any] = urllib.request.urlopen,
) -> Optional[str]:
    """Cheap reachability probe: ``GET {base_url}/models`` (every
    OpenAI-compatible server -- the anvil router included, see
    ``anvil_serving/router/discovery.py`` -- exposes this).

    Returns ``None`` if the endpoint is REACHABLE; otherwise a short,
    human-readable problem string (never raises). This backs `run`'s "fail
    loudly, don't pretend a live capability is proven" preflight -- see the
    module docstring and CLAUDE.md's own rule of the same name. Injectable
    (``_open``) so tests can simulate reachable/unreachable without a real
    socket. If ``token`` is given, it is sent as ``Authorization: Bearer
    <token>`` -- see :func:`_resolve_probe_token`, which resolves it the same
    way the LLM/STT/TTS stages resolve their own bearer token.

    B1 fix -- REACHABILITY vs HEALTH are different questions. A serve that
    RESPONDS at all -- including a 401/403 (auth required/rejected), 404/405
    (path not routed the way we guessed), or any other status < 500 -- is
    reachable and actively routing traffic: something is definitely up and
    answering HTTP on that socket, so `run` must NOT refuse to start over it.
    Before this fix, a token-authed router that correctly requires auth on
    ``GET /v1/models`` was probed with NO token, got a 401, and was
    misreported "unhealthy" -- blocking a perfectly healthy `voice run`.
    Only a genuine connection failure (``URLError`` that is NOT an
    ``HTTPError``, or ``OSError`` -- see the U2-b note below) is
    "unreachable", and only an actual 5xx (a running-but-broken serve, e.g. a
    500 from a half-initialized model server) is "unhealthy". Both of those
    still block startup (the caller keeps returning rc=1 for them); anything
    else (2xx, or a 4xx meaning "up and routing, just didn't like this exact
    unauthenticated/guessed-path probe request") is treated as OK.

    U2-b note (still applies): ``urllib.error.HTTPError`` IS a ``URLError``
    subclass, and real ``urlopen()`` RAISES it for a 4xx/5xx response instead
    of returning a response object with a non-2xx ``.status`` -- so
    ``HTTPError`` is caught FIRST and classified by ``exc.code`` (5xx vs
    everything else); the generic ``URLError``/``OSError`` branch below it is
    both the real connection-failure path and a defensive fallback for any
    injected ``_open`` fake that returns (rather than raises) a status.
    """
    url = base_url.rstrip("/") + "/models"
    stripped_token = token.strip() if token else None
    headers = {"Authorization": "Bearer %s" % stripped_token} if stripped_token else {}
    req = urllib.request.Request(url, headers=headers)
    try:
        with _open(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
    except urllib.error.HTTPError as exc:
        if exc.code >= 500:
            return "%s at %s returned HTTP %s (unhealthy)" % (name, url, exc.code)
        return None  # up and routing (e.g. 401/403/404/405) -- reachable, not blocking
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return "%s at %s is unreachable (%s)" % (name, url, _redact_bearer_token(str(exc)))
    if status >= 500:
        return "%s at %s returned HTTP %s (unhealthy)" % (name, url, status)
    return None


def _resolve_probe_token(table: Dict[str, Any]) -> Optional[str]:
    """Resolve `table`'s configured bearer token the SAME way the LLM/STT/TTS
    stages resolve their own (see ``stages/llm.py``'s ``_post_stream``,
    ``stages/stt.py``'s ``transcribe_stream``, ``stages/tts.py``'s
    ``stream_speech`` -- all three do ``if config.api_key_env:
    os.environ.get(config.api_key_env)``), so the preflight probe
    authenticates exactly like the real request traffic will.

    Returns ``None`` when this endpoint has no `api_key_env` configured, or
    when it names an environment variable that is not currently set --
    mirroring the stages' own silent-no-header fallback (never raises;
    ``voice_config.resolve_secret``'s ``required=True`` semantics are
    deliberately NOT used here, since a missing token should make the probe
    request unauthenticated -- exactly what the real stage traffic would also
    send -- not crash the preflight check)."""
    api_key_env = table.get("api_key_env")
    if not api_key_env:
        return None
    token = os.environ.get(api_key_env)
    return token.strip() if token else None


def _check_required_endpoints_reachable(voice: Dict[str, Any]) -> Optional[str]:
    """Probe the LLM router + STT/TTS serves declared in the manifest; return
    the first problem found, or ``None`` if all three answer. Calls the
    MODULE-LEVEL ``_probe_endpoint`` (not a private closure) so tests can
    monkeypatch it directly. Each endpoint's own configured `api_key_env` is
    resolved (:func:`_resolve_probe_token`) and sent along -- a token-authed
    endpoint is probed WITH its token, not bare (see the B1 fix note on
    :func:`_probe_endpoint`)."""
    for name, table_key in (("anvil router (voice.llm)", "llm"), ("STT serve (voice.stt)", "stt"), ("TTS serve (voice.tts)", "tts")):
        table = voice.get(table_key, {})
        base_url = table.get("base_url", "")
        token = _resolve_probe_token(table)
        problem = _probe_endpoint(name, base_url, token=token)
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
    return build_realtime_server_from_manifest(data, voice)


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

    def add_dry_run(sp):
        sp.add_argument("--dry-run", action="store_true", help="print the lifecycle action without starting/stopping processes")

    sp = sub.add_parser("up", help="bring up the STT/TTS serves (validates manifest)")
    add_config(sp)
    add_dry_run(sp)

    sp = sub.add_parser("start", help="alias for up")
    add_config(sp)
    add_dry_run(sp)

    sp = sub.add_parser("down", help="tear down the STT/TTS serves")
    add_config(sp)
    add_dry_run(sp)

    sp = sub.add_parser("stop", help="alias for down")
    add_config(sp)
    add_dry_run(sp)

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
        "start": cmd_up,
        "down": cmd_down,
        "stop": cmd_down,
        "run": cmd_run,
        "benchmark": cmd_benchmark,
    }
    return handlers[args.action](args)


if __name__ == "__main__":
    raise SystemExit(main())
