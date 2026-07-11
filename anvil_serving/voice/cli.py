"""anvil-serving voice — up / down / start / stop / run / benchmark / profiles / bridge (anvil task T001;
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
endpoints on a same-host audio owner launch/stop trusted manifest commands with
PID/log files. `benchmark` (T015) now replays one turn end-to-end
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
import ipaddress
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

from anvil_serving.mcp import ToolError, _resolve_benchmark_artifact_path

from . import benchmark as voice_benchmark
from . import bridge as voice_bridge
from . import config as voice_config
from .realtime.app import build_realtime_server_from_manifest
from .realtime.ws import serve_forever_in_background
from .serves import native as native_serve
from .serves import stt as stt_serve
from .serves import tts as tts_serve
from .serves._common import ServeNotConfigured

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - guarded by requires-python >=3.11
    tomllib = None

DEFAULT_MANIFEST = voice_config.CONFIG_HOME_CONFIG
#: Preflight reachability-probe timeout for `run`'s "fail loudly, don't
#: pretend" endpoint check (see `_probe_endpoint`).
ENDPOINT_PROBE_TIMEOUT_S = 3.0
TAILNET_IPV4 = ipaddress.ip_network("100.64.0.0/10")


def _redact_bearer_token(text: str) -> str:
    return re.sub(r"Bearer\s+[^'\"\s\\]+", "Bearer <redacted>", text)

# (kind, ServeConfig class, Serve class) -- drives the bring-up/tear-down loop
# in cmd_up/cmd_down; keeps them from repeating themselves per-serve-kind.
_AUDIO_SERVES = (
    ("stt", stt_serve.STTServeConfig, stt_serve.STTServe),
    ("tts", tts_serve.TTSServeConfig, tts_serve.TTSServe),
)


def _resolve_manifest_reference(path: str, manifest_dir: str | None) -> str:
    expanded = os.path.expanduser(path)
    if not path or os.path.isabs(expanded):
        return expanded
    if manifest_dir:
        return os.path.join(manifest_dir, path)
    return path


def _audio_serves(data: dict):
    voice = data.get("voice", {})
    manifest_dir = data.get("_manifest_dir")
    for kind, config_cls, serve_cls in _AUDIO_SERVES:
        table = voice.get(kind, {})
        lifecycle = table.get("lifecycle", "managed")
        config_kwargs = {
            "base_url": table.get("base_url", ""),
            "model": table.get("model", ""),
        }
        if table.get("serve_name"):
            config_kwargs["serve_name"] = table["serve_name"]
        manifest_path = table.get("manifest_path") or table.get("serves_manifest")
        if manifest_path:
            config_kwargs["manifest_path"] = _resolve_manifest_reference(
                manifest_path, manifest_dir
            )
        config = config_cls(**config_kwargs)
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


def _load(config_path, profile=None):
    """Load + validate the manifest; returns (data, None) or (None, error message)."""
    try:
        return voice_config.load_manifest(config_path, profile=profile), None
    except voice_config.ConfigError as exc:
        return None, str(exc)


def _load_candidate_overlay(path: Optional[str]) -> Optional[dict]:
    if not path:
        return None
    if tomllib is None:  # pragma: no cover - guarded by requires-python >=3.11
        raise voice_config.ConfigError("tomllib unavailable (need Python >= 3.11)")
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise voice_config.ConfigError("candidate overlay not found: %s" % path)
    except tomllib.TOMLDecodeError as exc:
        raise voice_config.ConfigError("cannot parse candidate overlay %s: %s" % (path, exc))


def _candidate_name(args) -> Optional[str]:
    candidate = getattr(args, "candidate", None)
    overlay_path = getattr(args, "candidate_overlay", None)
    candidate_model = getattr(args, "candidate_model", None)
    if candidate or not overlay_path:
        return candidate or candidate_model
    stem = os.path.splitext(os.path.basename(overlay_path))[0]
    return stem or None


def _loaded_candidate_overlay_from_args(args) -> Optional[dict]:
    base_url = getattr(args, "candidate_base_url", None)
    model = getattr(args, "candidate_model", None)
    api_key_env = getattr(args, "candidate_api_key_env", None)
    if not any((base_url, model, api_key_env)):
        return None
    if getattr(args, "candidate_overlay", None):
        raise voice_config.ConfigError(
            "--candidate-overlay cannot be combined with --candidate-base-url/--candidate-model"
        )
    if not base_url or not model:
        raise voice_config.ConfigError(
            "--candidate-base-url and --candidate-model must be provided together"
        )
    llm = {"base_url": base_url, "model": model}
    if api_key_env:
        llm["api_key_env"] = api_key_env
    return {"voice": {"llm": llm}}


def _candidate_overlay_from_args(args) -> Optional[dict]:
    overlay = _load_candidate_overlay(getattr(args, "candidate_overlay", None))
    loaded_overlay = _loaded_candidate_overlay_from_args(args)
    return loaded_overlay if loaded_overlay is not None else overlay


def _load_benchmark_config(args):
    """Resolve benchmark config with profile/candidate overlays and preserve identity."""
    return _load_resolved_config(args)


def _load_resolved_config(args):
    """Resolve a voice config with profile/candidate overlays and preserve identity."""
    try:
        candidate_overlay = _candidate_overlay_from_args(args)
        return (
            voice_config.resolve_manifest(
                args.config,
                profile=getattr(args, "profile", None),
                candidate_overlay=candidate_overlay,
                candidate=_candidate_name(args),
            ),
            None,
        )
    except voice_config.ConfigError as exc:
        return None, str(exc)


def _identity_summary(resolved: voice_config.ResolvedVoiceConfig) -> str:
    identity = resolved.identity()
    return (
        "profile=%s candidate=%s llm_model=%s llm_base_url=%s "
        "stt_model=%s stt_base_url=%s tts_model=%s tts_base_url=%s"
        % (
            identity.get("profile") or "-",
            identity.get("candidate") or "-",
            identity.get("llm_model") or "-",
            identity.get("llm_base_url") or "-",
            identity.get("stt_model") or "-",
            identity.get("stt_base_url") or "-",
            identity.get("tts_model") or "-",
            identity.get("tts_base_url") or "-",
        )
    )


def _resolve_evidence_output_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        target, _roots = _resolve_benchmark_artifact_path(path)
    except ToolError as exc:
        raise voice_config.ConfigError(exc.message)
    return target


def _write_benchmark_evidence(path: str, evidence: dict) -> str:
    target = _resolve_evidence_output_path(path)
    if target is None:
        raise voice_config.ConfigError("evidence output path is required")
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(json.dumps(evidence, indent=2, sort_keys=True))
        f.write("\n")
    return target


def cmd_up(args):
    data, err = _load(args.config, getattr(args, "profile", None))
    if err:
        print("voice up: %s" % err, file=sys.stderr)
        return 2
    profile_note = " profile=%s" % args.profile if getattr(args, "profile", None) else ""
    print("voice up: manifest OK%s -- %s" % (profile_note, voice_config.describe(data)))
    exit_code = 0
    for kind, lifecycle, table, serve in _audio_serves(data):
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
    print("voice up: realtime server is run in the foreground with `anvil-serving voice proxy run`.")
    return exit_code


def cmd_down(args):
    data, err = _load(args.config, getattr(args, "profile", None))
    if err:
        print("voice down: %s" % err, file=sys.stderr)
        return 2
    profile_note = " profile=%s" % args.profile if getattr(args, "profile", None) else ""
    print("voice down: manifest OK%s -- %s" % (profile_note, voice_config.describe(data)))
    exit_code = 0
    for kind, lifecycle, table, serve in _audio_serves(data):
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
    print("voice down: realtime server foreground process stops with Ctrl+C in `anvil-serving voice proxy run`.")
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
    `build_server` so `anvil-serving voice proxy run` does not need that script.

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
    resolved, err = _load_resolved_config(args)
    if err:
        print("voice run: %s" % err, file=sys.stderr)
        return 2
    assert resolved is not None
    data = resolved.data
    voice = data.get("voice", {})
    summary = _identity_summary(resolved)
    print("voice run: manifest OK -- %s -- %s" % (summary, voice_config.describe(data)))

    problem = _check_required_endpoints_reachable(voice)
    if problem:
        print(
            "voice run: %s -- refusing to start a session pool against an "
            "unreachable endpoint. Bring up the configured serves/router "
            "first (`anvil-serving voice audio up`, `anvil-serving router run`) and "
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
    resolved, err = _load_benchmark_config(args)
    if err:
        print("voice benchmark: %s" % err, file=sys.stderr)
        return 2
    assert resolved is not None
    summary = _identity_summary(resolved)
    try:
        evidence_target = _resolve_evidence_output_path(getattr(args, "evidence_out", None))
    except voice_config.ConfigError as exc:
        print("voice benchmark: %s" % exc, file=sys.stderr)
        return 2
    print("voice benchmark: manifest OK -- %s -- %s" % (summary, voice_config.describe(resolved.data)))
    try:
        result = voice_benchmark.run_benchmark_from_manifest(
            resolved.data,
            profile=resolved.profile,
            candidate=resolved.candidate,
        )
    except Exception as exc:  # noqa: BLE001 - the configured serves may simply not be up yet
        print(
            "voice benchmark: could not reach the configured STT/LLM/TTS serves (%s); "
            "bring them up with `anvil-serving voice audio up` first. Nothing was measured. "
            "Active config: %s" % (exc, summary)
        )
        return 0
    if evidence_target:
        evidence = result.get("evidence") if isinstance(result, dict) else None
        if not isinstance(evidence, dict):
            print("voice benchmark: benchmark result did not include structured evidence", file=sys.stderr)
            return 1
        _write_benchmark_evidence(evidence_target, evidence)
        print("voice benchmark: evidence written %s" % evidence_target)
    print(voice_benchmark.to_json(result))
    return 0


def cmd_profiles(args):
    data, err = _load(args.config, getattr(args, "profile", None))
    if err:
        print("voice profiles: %s" % err, file=sys.stderr)
        return 2
    if getattr(args, "profile", None):
        print("voice profiles: %s OK -- %s" % (args.profile, voice_config.describe(data)))
        return 0

    names = voice_config.profile_names(data)
    if not names:
        print("voice profiles: no profiles declared")
        return 0
    print("voice profiles:")
    for name in names:
        print("  %s" % name)
    return 0


def _tcp_port(value: str) -> int:
    try:
        port = int(value, 10)
    except ValueError:
        raise argparse.ArgumentTypeError("port must be an integer")
    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _host_is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _host_is_wildcard(host: str) -> bool:
    return host in ("0.0.0.0", "::")


def _host_is_public_ip(host: str) -> bool:
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError:
        return False
    if parsed.is_loopback or parsed.is_private:
        return False
    if parsed.version == 4 and parsed in TAILNET_IPV4:
        return False
    return parsed.is_global


def _validate_bridge_hosts(args) -> Optional[str]:
    hosts = (
        ("listen_host", args.listen_host),
        ("stt_target_host", args.stt_target_host),
        ("tts_target_host", args.tts_target_host),
    )
    for key, host in hosts:
        if host.lower() == "localhost":
            return "%s must use 127.0.0.1 or an explicit private/tailnet address, not localhost" % key
        if key != "listen_host" and _host_is_wildcard(host):
            return "%s must be a concrete target host, not %s" % (key, host)
        if _host_is_public_ip(host):
            return "%s must be a private/tailnet address or private DNS name, not a public IP (%s)" % (key, host)
    if _host_is_wildcard(args.listen_host) and not args.allow_wildcard_listen:
        return (
            "voice bridge listen_host is wildcard (%s); bind a concrete private/tailnet address "
            "or pass --allow-wildcard-listen only after firewall/private-network scoping is proven"
            % args.listen_host
        )
    if (
        not args.dry_run
        and not _host_is_loopback(args.listen_host)
        and not args.i_understand_this_exposes_voice_audio
    ):
        return (
            "voice bridge listen_host is non-loopback (%s); rerun with "
            "--i-understand-this-exposes-voice-audio after confirming the bind is private/tailnet-scoped"
            % args.listen_host
        )
    return None


def _bridge_routes_from_args(args):
    return [
        voice_bridge.TCPBridgeRoute(
            "stt",
            args.listen_host,
            args.stt_listen_port,
            args.stt_target_host,
            args.stt_target_port,
        ),
        voice_bridge.TCPBridgeRoute(
            "tts",
            args.listen_host,
            args.tts_listen_port,
            args.tts_target_host,
            args.tts_target_port,
        ),
    ]


def cmd_bridge(args):
    problem = _validate_bridge_hosts(args)
    if problem:
        print("voice bridge: %s" % problem, file=sys.stderr)
        return 2
    routes = _bridge_routes_from_args(args)
    for route in routes:
        prefix = "voice bridge: dry-run" if args.dry_run else "voice bridge: forwarding"
        print("%s %s" % (prefix, voice_bridge.describe_route(route)))
    if args.dry_run:
        return 0
    try:
        voice_bridge.serve_forever(routes, log=lambda message: print("voice bridge: %s" % message))
    except KeyboardInterrupt:
        print("\nvoice bridge: interrupted -- shutting down")
        return 0
    except OSError as exc:
        print("voice bridge: failed to bind or forward routes (%s)" % exc, file=sys.stderr)
        return 1
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="anvil-serving voice",
        description="Voice-pipeline verb: an anvil-style stdlib VAD->STT->LLM->TTS "
                    "orchestrator + realtime server (see "
                    "docs/findings/2026-07-04-hf-speech-to-speech-review.md).",
    )
    sub = p.add_subparsers(
        dest="action",
        required=True,
        metavar="{up,down,run,benchmark,profiles,bridge,sidecar}",
    )

    def add_config(sp):
        sp.add_argument(
            "--config",
            help="voice manifest TOML; defaults to ~/.anvil-serving/voice.toml "
                 "when present, else the shipped example",
        )

    def add_profile(sp):
        sp.add_argument("--profile", help="voice profile overlay from [voice.profiles.<name>]")

    def add_dry_run(sp):
        sp.add_argument("--dry-run", action="store_true", help="print the lifecycle action without starting/stopping processes")

    sp = sub.add_parser("up", help="bring up the STT/TTS serves (validates manifest)")
    add_config(sp)
    add_profile(sp)
    add_dry_run(sp)

    sp = sub.add_parser("down", help="tear down the STT/TTS serves")
    add_config(sp)
    add_profile(sp)
    add_dry_run(sp)

    sp = sub.add_parser("run", help="run the realtime server in the foreground")
    add_config(sp)
    add_profile(sp)
    sp.add_argument(
        "--candidate",
        help="candidate label recorded in run logs; defaults to overlay file stem",
    )
    sp.add_argument(
        "--candidate-overlay",
        help="candidate TOML overlay applied after the selected profile for this run",
    )

    sp = sub.add_parser("benchmark", help="replay a recorded session end-to-end and report latency")
    add_config(sp)
    add_profile(sp)
    sp.add_argument(
        "--candidate",
        help="candidate label recorded in benchmark evidence; defaults to overlay file stem",
    )
    sp.add_argument(
        "--candidate-overlay",
        help="candidate TOML overlay applied after the selected profile for this benchmark run",
    )
    sp.add_argument(
        "--candidate-base-url",
        help="OpenAI-compatible /v1 base URL for an already-loaded Fast candidate "
             "used only for this benchmark run",
    )
    sp.add_argument(
        "--candidate-model",
        help="model id served by --candidate-base-url; defaults the candidate label when --candidate is omitted",
    )
    sp.add_argument(
        "--candidate-api-key-env",
        help="optional ENV_VAR_NAME holding the bearer token for --candidate-base-url",
    )
    sp.add_argument(
        "--evidence-out",
        help="write structured benchmark evidence JSON under the workspace or configured evidence root",
    )

    sp = sub.add_parser("profiles", help="list profiles or validate one resolved profile")
    add_config(sp)
    add_profile(sp)

    sp = sub.add_parser("bridge", help="forward STT/TTS TCP ports from this host to local audio endpoints")
    sp.add_argument("--listen-host", default="127.0.0.1", help="host/interface to bind; non-loopback requires explicit acknowledgement")
    sp.add_argument("--stt-listen-port", type=_tcp_port, default=30110, help="bridge listen port for STT")
    sp.add_argument("--stt-target-host", default="127.0.0.1", help="target host for the local STT endpoint")
    sp.add_argument("--stt-target-port", type=_tcp_port, default=30010, help="target port for the local STT endpoint")
    sp.add_argument("--tts-listen-port", type=_tcp_port, default=30111, help="bridge listen port for TTS")
    sp.add_argument("--tts-target-host", default="127.0.0.1", help="target host for the local TTS endpoint")
    sp.add_argument("--tts-target-port", type=_tcp_port, default=30011, help="target port for the local TTS endpoint")
    sp.add_argument("--dry-run", action="store_true", help="print bridge routes without binding sockets")
    sp.add_argument(
        "--i-understand-this-exposes-voice-audio",
        action="store_true",
        help="acknowledge that a non-loopback bridge bind exposes STT/TTS audio traffic",
    )
    sp.add_argument(
        "--allow-wildcard-listen",
        action="store_true",
        help="allow 0.0.0.0/:: listen hosts after separate firewall/private-network scoping",
    )

    sub.add_parser("sidecar", help="validate or render the Hugging Face speech-to-speech sidecar")

    return p


def main_profiles_list(argv=None):
    """Canonical ``voice profiles list`` adapter."""
    parser = argparse.ArgumentParser(prog="anvil-serving voice")
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    forwarded = ["profiles"]
    if args.config:
        forwarded.extend(("--config", args.config))
    return main(forwarded)


def main_profiles_validate(argv=None):
    """Canonical ``voice profiles validate`` adapter."""
    parser = argparse.ArgumentParser(prog="anvil-serving voice")
    parser.add_argument("--config")
    parser.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    forwarded = ["profiles"]
    if args.config:
        forwarded.extend(("--config", args.config))
    forwarded.extend(("--profile", args.profile))
    return main(forwarded)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "sidecar":
        from anvil_serving import voice_sidecar
        return voice_sidecar.main(argv[1:], prog="anvil-serving voice sidecar")
    if argv and argv[0] == "start":
        print(
            "anvil-serving: `voice start` is a compatibility alias; use `voice up` instead.",
            file=sys.stderr,
        )
        argv[0] = "up"
    elif argv and argv[0] == "stop":
        print(
            "anvil-serving: `voice stop` is a compatibility alias; use `voice down` instead.",
            file=sys.stderr,
        )
        argv[0] = "down"
    args = build_parser().parse_args(argv)
    handlers = {
        "up": cmd_up,
        "down": cmd_down,
        "run": cmd_run,
        "benchmark": cmd_benchmark,
        "profiles": cmd_profiles,
        "bridge": cmd_bridge,
    }
    return handlers[args.action](args)


if __name__ == "__main__":
    raise SystemExit(main())
