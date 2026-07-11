"""anvil-serving voice audio lifecycle plus realtime and benchmark commands.
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
import copy
import ipaddress
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional

from anvil_serving import serves as generic_serves
from anvil_serving.mcp import ToolError, _resolve_benchmark_artifact_path
from anvil_serving.targets import TargetResolutionError
from anvil_serving.topology import TopologyValidationError, load_topology

from . import benchmark as voice_benchmark
from . import bridge as voice_bridge
from . import config as voice_config
from .realtime.app import build_realtime_server_from_manifest
from .realtime.ws import serve_forever_in_background
from .realtime_service import ProxyProcessConfig, RealtimeProxyProcessService
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
_MAX_AUDIO_LOG_BYTES = 1024 * 1024


def _resolve_manifest_reference(path: str, manifest_dir: str | None) -> str:
    expanded = os.path.expanduser(path)
    if not path or os.path.isabs(expanded):
        return expanded
    if manifest_dir:
        return os.path.join(manifest_dir, path)
    return path


def _audio_serves(
    data: dict,
    targets: voice_config.ResolvedAudioTargets | None = None,
    *,
    subprocess_deadline: float | None = None,
):
    voice = data.get("voice", {})
    manifest_dir = data.get("_manifest_dir")
    for kind, config_cls, serve_cls in _AUDIO_SERVES:
        table = dict(voice.get(kind, {}))
        if targets is not None:
            table["base_url"] = getattr(targets, kind).base_url
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
        kwargs = {}
        if subprocess_deadline is not None:
            kwargs["_run"] = _deadline_subprocess_runner(subprocess_deadline)
        yield kind, lifecycle, table, serve_cls(config, **kwargs)


def _deadline_subprocess_runner(deadline: float):
    def run(*args, **kwargs):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(args[0] if args else "subprocess", 0)
        requested = kwargs.get("timeout")
        kwargs["timeout"] = min(float(requested), remaining) if requested else remaining
        return subprocess.run(*args, **kwargs)

    return run


def _resolve_audio_operation(args):
    """Load the manifest and resolve both model owners before lifecycle I/O."""
    data, err = _load(args.config, getattr(args, "profile", None))
    if err:
        return None, None, err, 2
    topology_path = getattr(args, "topology", None)
    if not topology_path:
        return (
            None,
            None,
            "--topology is required so STT/TTS lifecycle resolves its model owner",
            2,
        )
    try:
        topology = load_topology(topology_path)
        targets = voice_config.resolve_audio_targets(
            topology,
            target=getattr(args, "target", None),
            transport=getattr(args, "transport", "auto"),
            command_host=getattr(args, "command_host", None),
            command_runtime=getattr(args, "command_runtime", None),
            overlay=getattr(args, "topology_overlay", None),
            experimental_model_workload=getattr(
                args, "experimental_model_workload", False
            ),
        )
    except TopologyValidationError as exc:
        return None, None, "invalid topology: %s" % exc, 2
    except TargetResolutionError as exc:
        return None, None, str(exc), exc.exit_code
    except voice_config.ConfigError as exc:
        return None, None, str(exc), 2
    remote = [
        endpoint
        for endpoint in (targets.stt, targets.tts)
        if endpoint.plan.transport != "local"
    ]
    if remote:
        details = ", ".join(
            "%s owner=%s transport=%s endpoint=%s"
            % (
                endpoint.kind,
                endpoint.plan.resource_host.id,
                endpoint.plan.transport,
                endpoint.base_url,
            )
            for endpoint in remote
        )
        return (
            None,
            targets,
            "resolved remote audio owner; refusing local lifecycle transport (%s)" % details,
            4,
        )
    voice = data.get("voice", {})
    for endpoint in (targets.stt, targets.tts):
        lifecycle = voice.get(endpoint.kind, {}).get("lifecycle", "managed")
        required_runtime = {"managed": "docker", "native": "native"}.get(lifecycle)
        actual_runtime = endpoint.plan.execution_runtime.role
        if required_runtime is not None and actual_runtime != required_runtime:
            return (
                None,
                targets,
                "%s lifecycle=%s requires a %s runtime, but topology resolves %s"
                % (endpoint.kind, lifecycle, required_runtime, actual_runtime),
                3,
            )
    return data, targets, None, 0


def _audio_context(targets: voice_config.ResolvedAudioTargets | None) -> str:
    if targets is None:
        return ""
    parts = []
    for endpoint in (targets.stt, targets.tts):
        plan = endpoint.plan
        parts.append(
            "%s owner=%s execution=%s transport=%s endpoint=%s endpoint_kind=%s"
            % (
                endpoint.kind,
                plan.resource_host.id,
                plan.execution_host.id,
                plan.transport,
                endpoint.base_url,
                endpoint.endpoint_kind,
            )
        )
    return " -- ".join(parts)


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


def execute_audio_lifecycle(
    data: dict,
    action: str,
    *,
    dry_run: bool = False,
    targets: voice_config.ResolvedAudioTargets | None = None,
    timeout_seconds: float | None = None,
) -> dict:
    """Execute one already-authorized local audio lifecycle and return typed data."""
    if action not in {"up", "down"}:
        raise ValueError("audio lifecycle action must be up or down")
    if timeout_seconds is not None and (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(float(timeout_seconds))
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be a positive finite number")
    results = []
    exit_code = 0
    method_name = "bring_up" if action == "up" else "tear_down"
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    for kind, lifecycle, table, serve in _audio_serves(
        data, targets, subprocess_deadline=deadline
    ):
        item = {"kind": kind, "lifecycle": lifecycle, "state": "pending"}
        if lifecycle == "external":
            item["state"] = "external"
            results.append(item)
            continue
        if lifecycle == "native":
            try:
                if deadline is not None:
                    remaining = max(0.1, deadline - time.monotonic())
                    timeout_key = "ready_timeout" if action == "up" else "stop_timeout"
                    table = dict(table)
                    table[timeout_key] = min(float(table.get(timeout_key, remaining)), remaining)
                native = native_serve.NativeServe(
                    native_serve.NativeServeConfig.from_table(kind, table)
                )
                native_result = getattr(native, method_name)(dry_run=dry_run)
                item.update({"state": "completed", "result": native_result})
                if native_result.get("returncode") != 0:
                    exit_code = 1
            except Exception as exc:  # noqa: BLE001 - configured native process may fail locally
                item.update({"state": "failed", "error": str(exc)})
                exit_code = 1
            results.append(item)
            continue
        try:
            method = getattr(serve, method_name)
            rc = method(dry_run=True) if dry_run else method()
            item.update({"state": "completed", "returncode": rc})
            if rc != 0:
                exit_code = 1
        except ServeNotConfigured as exc:
            item.update({"state": "not_configured", "detail": str(exc), "returncode": 0})
        except subprocess.TimeoutExpired as exc:
            item.update({"state": "failed", "error": str(exc), "returncode": 1})
            exit_code = 1
        results.append(item)
    return {
        "action": action,
        "dry_run": dry_run,
        "returncode": exit_code,
        "serves": results,
    }


def _print_audio_lifecycle(result: dict) -> None:
    action = result["action"]
    prefix = "voice audio %s" % action
    operation = "bring-up" if action == "up" else "tear-down"
    for item in result["serves"]:
        kind = item["kind"]
        if item["state"] == "external":
            print("%s: %s serve lifecycle is external; skipping managed %s" % (
                prefix, kind, operation,
            ))
        elif item["lifecycle"] == "native" and item["state"] == "completed":
            _print_native_result(prefix, kind, item["result"])
        elif item["state"] == "failed":
            print(
                "%s: %s native lifecycle failed -- %s" % (prefix, kind, item["error"]),
                file=sys.stderr,
            )
        elif item["state"] == "not_configured":
            print("%s: %s serve not configured yet -- %s" % (prefix, kind, item["detail"]))
        else:
            print("%s: %s serve %s rc=%s" % (
                prefix, kind, operation, item["returncode"],
            ))


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
    data, targets, err, error_code = _resolve_audio_operation(args)
    if err:
        print("voice audio up: %s" % err, file=sys.stderr)
        return error_code
    assert data is not None
    profile_note = " profile=%s" % args.profile if getattr(args, "profile", None) else ""
    context = _audio_context(targets)
    print(
        "voice audio up: manifest OK%s -- %s%s"
        % (profile_note, voice_config.describe(data), " -- " + context if context else "")
    )
    result = execute_audio_lifecycle(
        data,
        "up",
        dry_run=getattr(args, "dry_run", False),
        targets=targets,
    )
    _print_audio_lifecycle(result)
    return result["returncode"]


def cmd_down(args):
    data, targets, err, error_code = _resolve_audio_operation(args)
    if err:
        print("voice audio down: %s" % err, file=sys.stderr)
        return error_code
    assert data is not None
    profile_note = " profile=%s" % args.profile if getattr(args, "profile", None) else ""
    context = _audio_context(targets)
    print(
        "voice audio down: manifest OK%s -- %s%s"
        % (profile_note, voice_config.describe(data), " -- " + context if context else "")
    )
    result = execute_audio_lifecycle(
        data,
        "down",
        dry_run=getattr(args, "dry_run", False),
        targets=targets,
    )
    _print_audio_lifecycle(result)
    return result["returncode"]


def cmd_audio_status(args):
    resolved = getattr(args, "_resolved_audio", None)
    data, targets, err, error_code = (
        (*resolved, None, 0) if resolved is not None else _resolve_audio_operation(args)
    )
    if err:
        print("voice audio status: %s" % err, file=sys.stderr)
        return error_code
    assert data is not None
    context = _audio_context(targets)
    if context:
        print("voice audio status: %s" % context)
    exit_code = 0
    timeout_seconds = getattr(args, "operation_timeout", None)
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    for kind, lifecycle, table, serve in _audio_serves(
        data, targets, subprocess_deadline=deadline
    ):
        if lifecycle == "native":
            status = native_serve.NativeServe(
                native_serve.NativeServeConfig.from_table(kind, table)
            ).status()
            print("voice audio status: %s %s" % (kind, json.dumps(status, sort_keys=True)))
            continue
        try:
            readiness = serve.wait_ready(timeout=getattr(args, "ready_timeout", 3.0))
        except subprocess.TimeoutExpired as exc:
            print("voice audio status: %s timed out -- %s" % (kind, exc), file=sys.stderr)
            exit_code = 1
            continue
        print(
            "voice audio status: %s lifecycle=%s state=%s ready=%s detail=%s"
            % (kind, lifecycle, readiness.docker_state, readiness.ready, readiness.detail)
        )
        if not readiness.ready:
            exit_code = 1
    return exit_code


def _tail_file(path: str, lines: int) -> list[str]:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _MAX_AUDIO_LOG_BYTES))
            raw = handle.read(_MAX_AUDIO_LOG_BYTES)
    except FileNotFoundError:
        return []
    return raw.decode("utf-8", errors="replace").splitlines(keepends=True)[-lines:]


def cmd_audio_logs(args):
    resolved = getattr(args, "_resolved_audio", None)
    data, targets, err, error_code = (
        (*resolved, None, 0) if resolved is not None else _resolve_audio_operation(args)
    )
    if err:
        print("voice audio logs: %s" % err, file=sys.stderr)
        return error_code
    assert data is not None
    context = _audio_context(targets)
    if context:
        print("voice audio logs: %s" % context)
    exit_code = 0
    timeout_seconds = getattr(args, "operation_timeout", None)
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    for kind, lifecycle, table, serve in _audio_serves(
        data, targets, subprocess_deadline=deadline
    ):
        if lifecycle == "external":
            print("voice audio logs: %s lifecycle is external; no owned logs" % kind)
            continue
        if lifecycle == "native":
            native = native_serve.NativeServe(
                native_serve.NativeServeConfig.from_table(kind, table)
            )
            lines = _tail_file(native.log_file, args.tail)
            if not lines:
                print("voice audio logs: %s has no log output at %s" % (kind, native.log_file))
                continue
            print("voice audio logs: %s %s" % (kind, native.log_file))
            print("".join(lines), end="" if lines[-1].endswith("\n") else "\n")
            continue
        try:
            manifest = generic_serves.load_manifest(serve.config.manifest_path)
            run = _deadline_subprocess_runner(deadline) if deadline is not None else subprocess.run
            rc = generic_serves.cmd_logs(
                manifest, [serve.config.serve_name], tail=str(args.tail), _run=run
            )
        except (FileNotFoundError, ServeNotConfigured, subprocess.TimeoutExpired) as exc:
            print("voice audio logs: %s log read failed -- %s" % (kind, exc), file=sys.stderr)
            rc = 1
        if rc != 0:
            exit_code = 1
    return exit_code


def _resolve_proxy_operation(args, action: str):
    """Resolve the Mini proxy and its local audio forwarders before I/O."""
    resolved, err = _load_resolved_config(args)
    if err:
        return None, None, None, err, 2
    topology_path = getattr(args, "topology", None)
    if not topology_path:
        return None, None, None, "--topology is required to resolve the proxy owner", 2
    try:
        topology = load_topology(topology_path, getattr(args, "topology_overlay", None))
        targets = voice_config.resolve_proxy_targets(
            topology,
            operation="voice-proxy-%s" % action,
            target=getattr(args, "target", None),
            transport=getattr(args, "transport", "auto"),
            command_host=getattr(args, "command_host", None),
            command_runtime=getattr(args, "command_runtime", None),
            overlay=getattr(args, "topology_overlay", None),
        )
    except TopologyValidationError as exc:
        return None, None, None, "invalid topology: %s" % exc, 2
    except TargetResolutionError as exc:
        return None, None, None, str(exc), exc.exit_code
    except voice_config.ConfigError as exc:
        return None, None, None, str(exc), 2
    if targets.proxy.transport != "local":
        return (
            None,
            targets,
            resolved,
            "resolved remote proxy owner; refusing local process execution "
            "(owner=%s transport=%s)"
            % (targets.proxy.resource_host.id, targets.proxy.transport),
            4,
        )
    data = copy.deepcopy(resolved.data)
    voice = data["voice"]
    voice["stt"]["base_url"] = targets.stt_proxy.endpoint
    voice["tts"]["base_url"] = targets.tts_proxy.endpoint
    endpoint = urllib.parse.urlparse(targets.endpoint)
    manifest_host = voice.get("realtime_host", "127.0.0.1")
    manifest_port = int(voice.get("realtime_port", 8765))
    if endpoint.hostname != manifest_host or endpoint.port != manifest_port:
        return (
            None,
            targets,
            resolved,
            "manifest realtime listener %s:%s does not match topology proxy endpoint %s:%s"
            % (manifest_host, manifest_port, endpoint.hostname, endpoint.port),
            3,
        )
    return data, targets, resolved, None, 0


def _proxy_context(targets: voice_config.ResolvedProxyTargets) -> str:
    plan = targets.proxy
    return (
        "owner=%s execution=%s transport=%s proxy=%s stt_proxy=%s tts_proxy=%s"
        % (
            plan.resource_host.id,
            plan.execution_host.id,
            plan.transport,
            targets.endpoint,
            targets.stt_proxy.endpoint,
            targets.tts_proxy.endpoint,
        )
    )


def _proxy_process_service(
    args,
    data: dict,
    targets: voice_config.ResolvedProxyTargets,
) -> RealtimeProxyProcessService:
    voice = data["voice"]
    config = ProxyProcessConfig(
        config_path=voice_config.resolve_config_path(getattr(args, "config", None)),
        topology_path=args.topology,
        topology_overlay=getattr(args, "topology_overlay", None),
        profile=getattr(args, "profile", None),
        command_host=getattr(args, "command_host", None),
        command_runtime=getattr(args, "command_runtime", None),
        target=getattr(args, "target", None),
        host=voice.get("realtime_host", "127.0.0.1"),
        port=int(voice.get("realtime_port", 8765)),
        owner=targets.proxy.resource_host.id,
        pid_file=getattr(args, "pid_file", None) or os.path.join(
            "~/.anvil-serving/run", "voice-proxy.pid"
        ),
        log_file=getattr(args, "log_file", None) or os.path.join(
            "~/.anvil-serving/run", "voice-proxy.log"
        ),
    )
    return RealtimeProxyProcessService(config)


def _print_proxy_process_result(result: dict) -> int:
    print("voice proxy %s: %s" % (result["action"], json.dumps(result, sort_keys=True)))
    return int(result.get("returncode", 0))


def cmd_proxy_lifecycle(args):
    action = args.proxy_action
    data, targets, _resolved, err, error_code = _resolve_proxy_operation(args, action)
    if err:
        print("voice proxy %s: %s" % (action, err), file=sys.stderr)
        return error_code
    assert data is not None and targets is not None
    print("voice proxy %s: %s" % (action, _proxy_context(targets)))
    service = _proxy_process_service(args, data, targets)
    result = getattr(service, action)(dry_run=getattr(args, "dry_run", False))
    return _print_proxy_process_result(result)


def cmd_proxy_status(args):
    data, targets, _resolved, err, error_code = _resolve_proxy_operation(args, "status")
    if err:
        print("voice proxy status: %s" % err, file=sys.stderr)
        return error_code
    assert data is not None and targets is not None
    print("voice proxy status: %s" % _proxy_context(targets))
    return _print_proxy_process_result(_proxy_process_service(args, data, targets).status())


def cmd_proxy_logs(args):
    data, targets, _resolved, err, error_code = _resolve_proxy_operation(args, "logs")
    if err:
        print("voice proxy logs: %s" % err, file=sys.stderr)
        return error_code
    assert data is not None and targets is not None
    print("voice proxy logs: %s" % _proxy_context(targets))
    result = _proxy_process_service(args, data, targets).logs(tail=args.tail)
    for line in result["lines"]:
        print(line)
    return int(result["returncode"])


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
    data, targets, resolved, err, error_code = _resolve_proxy_operation(args, "run")
    if err:
        print("voice proxy run: %s" % err, file=sys.stderr)
        return error_code
    assert data is not None and targets is not None and resolved is not None
    voice = data.get("voice", {})
    summary = _identity_summary(resolved)
    print(
        "voice proxy run: manifest OK -- %s -- %s -- %s"
        % (summary, voice_config.describe(data), _proxy_context(targets))
    )

    problem = _check_required_endpoints_reachable(voice)
    if problem:
        print(
            "voice proxy run: %s -- refusing to start a session pool against an "
            "unreachable endpoint. Bring up the configured serves/router "
            "first (`anvil-serving voice audio up`, `anvil-serving router run`) and "
            "retry." % problem,
            file=sys.stderr,
        )
        return 1

    try:
        server, pool = _build_realtime_server(data, voice)
    except ValueError as exc:
        print("voice proxy run: %s" % exc, file=sys.stderr)
        return 2

    thread = serve_forever_in_background(server)
    host, port = server.server_address[:2]
    print(
        "voice proxy run: realtime server up at ws://%s:%d/v1/realtime (pool size %d)"
        % (host, port, pool.size)
    )
    try:
        _wait_forever_default()
    except KeyboardInterrupt:
        print("\nvoice proxy run: interrupted -- shutting down")
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


def _bounded_tail(value: str) -> int:
    try:
        tail = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("tail must be an integer")
    if tail < 1 or tail > 5000:
        raise argparse.ArgumentTypeError("tail must be between 1 and 5000")
    return tail


def _bounded_ready_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("ready timeout must be a number")
    if not math.isfinite(timeout) or timeout < 0.1 or timeout > 60.0:
        raise argparse.ArgumentTypeError("ready timeout must be between 0.1 and 60 seconds")
    return timeout


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
    if not _host_is_loopback(args.listen_host):
        return "voice proxy bridge listeners must remain Mini-local on 127.0.0.1"
    return None


def _endpoint_port(endpoint: str | None, kind: str) -> int:
    if not endpoint:
        raise voice_config.ConfigError("%s endpoint is missing" % kind)
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.port is None:
        raise voice_config.ConfigError("%s endpoint has no explicit port" % kind)
    return parsed.port


def _apply_bridge_defaults(args, targets: voice_config.ResolvedProxyTargets) -> None:
    args.listen_host = args.listen_host or "127.0.0.1"
    args.stt_listen_port = args.stt_listen_port or _endpoint_port(
        targets.stt_proxy.endpoint, "STT proxy"
    )
    args.tts_listen_port = args.tts_listen_port or _endpoint_port(
        targets.tts_proxy.endpoint, "TTS proxy"
    )
    args.stt_target_host = args.stt_target_host or targets.stt_target_host
    args.tts_target_host = args.tts_target_host or targets.tts_target_host
    if not args.stt_target_host or not args.tts_target_host:
        raise voice_config.ConfigError(
            "remote audio model hosts must declare reachable topology addresses"
        )
    args.stt_target_port = args.stt_target_port or _endpoint_port(
        targets.stt_model.endpoint, "STT model"
    )
    args.tts_target_port = args.tts_target_port or _endpoint_port(
        targets.tts_model.endpoint, "TTS model"
    )


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
    _data, targets, _resolved, err, error_code = _resolve_proxy_operation(args, "bridge")
    if err:
        print("voice proxy bridge: %s" % err, file=sys.stderr)
        return error_code
    assert targets is not None
    print("voice proxy bridge: %s" % _proxy_context(targets))
    try:
        _apply_bridge_defaults(args, targets)
    except voice_config.ConfigError as exc:
        print("voice proxy bridge: %s" % exc, file=sys.stderr)
        return 2
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
        metavar="{audio,proxy,benchmark,profiles,sidecar}",
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

    audio = sub.add_parser("audio", help="manage Dark-owned STT/TTS model serves")
    audio_sub = audio.add_subparsers(
        dest="audio_action",
        required=True,
        metavar="{up,down,status,logs}",
    )

    def add_audio_resolution(sp):
        add_config(sp)
        add_profile(sp)
        sp.add_argument("--topology", help="topology document used to resolve STT/TTS owners")
        sp.add_argument("--topology-overlay", help="deployment overlay identity recorded in context")
        sp.add_argument("--command-host", help="declared command host")
        sp.add_argument("--command-runtime", help="declared command runtime")
        sp.add_argument("--target", help="explicit audio model resource owner")
        sp.add_argument(
            "--transport",
            choices=("auto", "local", "controller"),
            default="auto",
            help="execution transport selected after ownership and capacity checks",
        )
        sp.add_argument(
            "--experimental-model-workload",
            action="store_true",
            help="allow a topology-permitted experimental model workload",
        )

    sp = audio_sub.add_parser("up", help="bring up the Dark-owned STT/TTS serves")
    add_audio_resolution(sp)
    add_dry_run(sp)
    sp.add_argument("--confirm", action="store_true", help=argparse.SUPPRESS)

    sp = audio_sub.add_parser("down", help="tear down the Dark-owned STT/TTS serves")
    add_audio_resolution(sp)
    add_dry_run(sp)
    sp.add_argument("--confirm", action="store_true", help=argparse.SUPPRESS)

    sp = audio_sub.add_parser("status", help="show bounded STT/TTS lifecycle status")
    add_audio_resolution(sp)
    sp.add_argument("--ready-timeout", type=_bounded_ready_timeout, default=3.0)

    sp = audio_sub.add_parser("logs", help="show bounded STT/TTS lifecycle logs")
    add_audio_resolution(sp)
    sp.add_argument("--tail", type=_bounded_tail, default=200)

    proxy = sub.add_parser("proxy", help="manage the Mini-owned realtime proxy")
    proxy_sub = proxy.add_subparsers(
        dest="proxy_action",
        required=True,
        metavar="{run,up,down,restart,status,logs,bridge}",
    )

    def add_proxy_resolution(sp):
        add_config(sp)
        add_profile(sp)
        sp.add_argument("--topology", help="topology document used to resolve the proxy owner")
        sp.add_argument("--topology-overlay", help="deployment overlay applied to the topology")
        sp.add_argument("--command-host", help="declared command host")
        sp.add_argument("--command-runtime", help="declared command runtime")
        sp.add_argument("--target", help="explicit proxy resource owner")
        sp.add_argument(
            "--transport",
            choices=("auto", "local", "controller"),
            default="auto",
            help="execution transport selected after proxy ownership resolution",
        )

    def add_proxy_process_files(sp):
        sp.add_argument("--pid-file", help="owned proxy PID record path")
        sp.add_argument("--log-file", help="bounded proxy process log path")

    sp = proxy_sub.add_parser("run", help="run the realtime server in the foreground")
    add_proxy_resolution(sp)
    sp.add_argument(
        "--candidate",
        help="candidate label recorded in run logs; defaults to overlay file stem",
    )
    sp.add_argument(
        "--candidate-overlay",
        help="candidate TOML overlay applied after the selected profile for this run",
    )

    for action, help_text in (
        ("up", "start the realtime proxy in the background"),
        ("down", "stop the owned realtime proxy process"),
        ("restart", "restart the owned realtime proxy process"),
    ):
        sp = proxy_sub.add_parser(action, help=help_text)
        add_proxy_resolution(sp)
        add_proxy_process_files(sp)
        add_dry_run(sp)
        sp.add_argument("--confirm", action="store_true", help=argparse.SUPPRESS)

    sp = proxy_sub.add_parser("status", help="show bounded realtime proxy status")
    add_proxy_resolution(sp)
    add_proxy_process_files(sp)

    sp = proxy_sub.add_parser("logs", help="show bounded realtime proxy logs")
    add_proxy_resolution(sp)
    add_proxy_process_files(sp)
    sp.add_argument("--tail", type=_bounded_tail, default=200)

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

    sp = proxy_sub.add_parser("bridge", help="forward Mini-local STT/TTS ports to Dark")
    add_proxy_resolution(sp)
    sp.add_argument("--listen-host", help="Mini-local listen host; defaults to 127.0.0.1")
    sp.add_argument("--stt-listen-port", type=_tcp_port, help="STT listener port; defaults from topology")
    sp.add_argument("--stt-target-host", help="Dark STT host; defaults from topology")
    sp.add_argument("--stt-target-port", type=_tcp_port, help="Dark STT port; defaults from topology")
    sp.add_argument("--tts-listen-port", type=_tcp_port, help="TTS listener port; defaults from topology")
    sp.add_argument("--tts-target-host", help="Dark TTS host; defaults from topology")
    sp.add_argument("--tts-target-port", type=_tcp_port, help="Dark TTS port; defaults from topology")
    sp.add_argument("--dry-run", action="store_true", help="print bridge routes without binding sockets")

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
    removed_audio_paths = {
        "up": "voice audio up",
        "down": "voice audio down",
        "start": "voice audio up",
        "stop": "voice audio down",
        "run": "voice proxy run",
        "bridge": "voice proxy bridge",
    }
    if argv and argv[0] in removed_audio_paths:
        print(
            "anvil-serving: `voice %s` was removed; use `%s` instead. "
            "See docs/CLI.md#migration-from-legacy-commands."
            % (argv[0], removed_audio_paths[argv[0]]),
            file=sys.stderr,
        )
        return 2
    if argv and argv[0] == "sidecar":
        from anvil_serving import voice_sidecar
        return voice_sidecar.main(argv[1:], prog="anvil-serving voice sidecar")
    args = build_parser().parse_args(argv)
    handlers = {
        "benchmark": cmd_benchmark,
        "profiles": cmd_profiles,
    }
    if args.action == "audio":
        return {
            "up": cmd_up,
            "down": cmd_down,
            "status": cmd_audio_status,
            "logs": cmd_audio_logs,
        }[args.audio_action](args)
    if args.action == "proxy":
        return {
            "run": cmd_run,
            "up": cmd_proxy_lifecycle,
            "down": cmd_proxy_lifecycle,
            "restart": cmd_proxy_lifecycle,
            "status": cmd_proxy_status,
            "logs": cmd_proxy_logs,
            "bridge": cmd_bridge,
        }[args.proxy_action](args)
    return handlers[args.action](args)


if __name__ == "__main__":
    raise SystemExit(main())
