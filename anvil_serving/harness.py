"""OpenClaw direct-alias provider sync and bounded gateway lifecycle helpers."""
from __future__ import annotations

import argparse
from collections.abc import Mapping
import ipaddress
import json
import os
import re
import subprocess
import sys
import urllib.parse


_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DEFAULT_TRANSPORT_TIMEOUT_SECONDS = 120
DEFAULT_STATUS_MAX_OUTPUT_BYTES = 64 * 1024
DEFAULT_ANVIL_VOICE_REALTIME_URL = "ws://127.0.0.1:8765/v1/realtime"
_DEFAULT_OPENCLAW_CONFIG_PATH = "~/.openclaw/openclaw.json"
_THINKING_LEVELS = frozenset({"off", "minimal", "low", "medium", "high", "xhigh", "adaptive", "max"})
_BOOTSTRAP_CONTEXT_MODES = frozenset({"full", "lightweight"})


def _validate_env(value, name):
    if not isinstance(value, str) or not _ENV_NAME_RE.fullmatch(value):
        raise ValueError("%s must be an ENV_VAR_NAME" % name)
    return value


def _validate_gateway_host(host):
    if not isinstance(host, str) or not _HOST_RE.fullmatch(host):
        raise ValueError("gateway_host must be a hostname or IP address without shell syntax")
    return host


def _normalize_voice_consult_thinking_level(value):
    if value not in _THINKING_LEVELS:
        raise ValueError("voice consult thinking level must be one of: %s" % ", ".join(sorted(_THINKING_LEVELS)))
    return value


def _normalize_voice_consult_bootstrap_context_mode(value):
    if value not in _BOOTSTRAP_CONTEXT_MODES:
        raise ValueError("voice consult bootstrap context mode must be one of: %s" % ", ".join(sorted(_BOOTSTRAP_CONTEXT_MODES)))
    return value


def _validate_voice_realtime_url(value, *, api_key_env=None):
    if not isinstance(value, str) or not value:
        raise ValueError("voice realtime URL must be a non-empty ws:// or wss:// URL")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("ws", "wss") or not parsed.netloc:
        raise ValueError("voice realtime URL must be a ws:// or wss:// URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "voice realtime URL must not embed credentials, query strings, or fragments"
        )
    host = (parsed.hostname or "").lower().rstrip(".")
    if host == "localhost":
        raise ValueError("voice realtime URL must use 127.0.0.1")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError(
            "voice realtime URL host must be loopback, private, or tailnet-safe"
        ) from None
    tailnet = address.version == 4 and address in ipaddress.ip_network("100.64.0.0/10")
    safe = address.is_loopback or address.is_private or tailnet
    if not safe or address.is_unspecified or address.is_multicast:
        raise ValueError(
            "voice realtime URL host must be loopback, private, or tailnet-safe"
        )
    if not address.is_loopback and not api_key_env:
        raise ValueError(
            "non-loopback Anvil Voice realtime URLs require --voice-api-key-env"
        )
    return value


def _title(value):
    return " ".join(part.capitalize() for part in value.replace("_", "-").split("-"))


def _openclaw_input(tier):
    result = ["text"]
    params = getattr(tier, "params", None)
    capabilities = params.get("capabilities", {}) if isinstance(params, Mapping) else {}
    modalities = capabilities.get("modalities", []) if isinstance(capabilities, Mapping) else []
    if isinstance(modalities, list) and "image" in modalities:
        result.append("image")
    return result


def render_openclaw_provider(config, *, base_url, api_key_env="ANVIL_ROUTER_TOKEN", **_ignored):
    """Render ordinary OpenAI-compatible OpenClaw models for direct aliases."""
    _validate_env(api_key_env, "api_key_env")
    routes = getattr(config, "model_routes", {})
    if not routes:
        raise ValueError("router config declares no [router.model_routes]")
    models = [
        {
            "id": alias,
            "name": "Anvil · " + _title(alias),
            "reasoning": True,
            "input": _openclaw_input(config.tier(tier_id)),
            "contextWindow": config.tier(tier_id).context_limit,
            "maxTokens": 8192,
        }
        for alias, tier_id in routes.items()
    ]
    defaults = {"models": {
        "anvil/%s" % model["id"]: {} for model in models
    }}
    if any(
        model["id"] == "vision.general" and "image" in model["input"]
        for model in models
    ):
        defaults["imageModel"] = {"primary": "anvil/vision.general"}
    return {
        "models": {"mode": "merge", "providers": {"anvil": {
            "baseUrl": base_url, "apiKey": "${%s}" % api_key_env,
            "api": "openai-completions", "models": models,
        }}},
        "agents": {"defaults": defaults},
    }


def _voice_alias(config, explicit=None):
    if explicit:
        return explicit
    routes = getattr(config, "model_routes", {})
    for alias in ("llm.voice", "llm.primary"):
        if alias in routes:
            return alias
    return next(iter(routes), "llm.primary")


def render_openclaw_voice_config(*, realtime_url, model, consult_model="", consult_thinking_level="off", consult_bootstrap_context_mode="lightweight", api_key_env=None):
    _normalize_voice_consult_thinking_level(consult_thinking_level)
    _normalize_voice_consult_bootstrap_context_mode(consult_bootstrap_context_mode)
    env_name = _validate_env(api_key_env, "voice_api_key_env") if api_key_env else None
    provider = {
        "realtimeUrl": _validate_voice_realtime_url(
            realtime_url, api_key_env=env_name
        ),
        "model": model,
        "silenceDurationMs": 200,
    }
    if env_name:
        provider["apiKey"] = {
            "source": "env", "provider": "default", "id": env_name,
        }
    talk = {"realtime": {
                "mode": "realtime",
                "transport": "gateway-relay",
                "brain": "agent-consult",
                "consultRouting": "force-agent-consult",
                "provider": "anvil",
                "providers": {"anvil": provider},
            },
            "consultThinkingLevel": consult_thinking_level,
            "consultBootstrapContextMode": consult_bootstrap_context_mode}
    if consult_model:
        talk["consultModel"] = consult_model
    return talk


def _with_voice(payload, voice):
    result = json.loads(json.dumps(payload))
    result["talk"] = voice
    return result


def _openclaw_model_primary(value):
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        primary = value.get("primary")
        return primary if isinstance(primary, str) else None
    return None


def _openclaw_payload_summary(provider, *, skills=False, voice=False, **_ignored):
    models = provider["models"]["providers"]["anvil"]["models"]
    realtime = provider.get("talk", {}).get("realtime", {})
    talk = provider.get("talk", {})
    voice_provider = realtime.get("providers", {}).get("anvil", {}) if isinstance(realtime, dict) else {}
    return {
        "provider": provider,
        "model_count": len(models),
        "model_ids": [model["id"] for model in models],
        "base_url": provider["models"]["providers"]["anvil"]["baseUrl"],
        "api_key": provider["models"]["providers"]["anvil"]["apiKey"],
        "direct_aliases": True,
        "image_model": _openclaw_model_primary(
            provider.get("agents", {}).get("defaults", {}).get("imageModel")
        ),
        "skills": False,
        "voice": bool(voice),
        "voice_provider": realtime.get("provider") if isinstance(realtime, dict) else None,
        "voice_realtime_url": voice_provider.get("realtimeUrl"),
        "voice_model": voice_provider.get("model"),
        "voice_consult_model": talk.get("consultModel"),
        "voice_consult_thinking_level": talk.get("consultThinkingLevel"),
        "voice_consult_bootstrap_context_mode": talk.get("consultBootstrapContextMode"),
    }


def openclaw_sync_preview(config_path, *, base_url, api_key_env="ANVIL_ROUTER_TOKEN", voice=False,
                          voice_realtime_url=DEFAULT_ANVIL_VOICE_REALTIME_URL, voice_model=None,
                          voice_consult_model="", voice_consult_thinking_level="off",
                          voice_consult_bootstrap_context_mode="lightweight", voice_api_key_env=None,
                          _load=None, **_ignored):
    if _load is None:
        from .router.config import load as _load
    config = _load(config_path)
    provider = render_openclaw_provider(config, base_url=base_url, api_key_env=api_key_env)
    if voice:
        alias = _voice_alias(config, voice_model)
        provider = _with_voice(provider, render_openclaw_voice_config(
            realtime_url=voice_realtime_url, model=alias,
            consult_model=voice_consult_model or alias,
            consult_thinking_level=voice_consult_thinking_level,
            consult_bootstrap_context_mode=voice_consult_bootstrap_context_mode,
            api_key_env=voice_api_key_env,
        ))
    return _openclaw_payload_summary(provider, voice=voice)


def _merge_provider(existing, rendered):
    result = json.loads(json.dumps(existing)) if isinstance(existing, dict) else {}
    models = result.setdefault("models", {})
    models["mode"] = "merge"
    providers = models.setdefault("providers", {})
    previous_anvil = providers.get("anvil", {})
    rendered_anvil = rendered["models"]["providers"]["anvil"]
    if isinstance(previous_anvil, dict) and isinstance(previous_anvil.get("apiKey"), dict):
        rendered_anvil["apiKey"] = previous_anvil["apiKey"]
    providers["anvil"] = rendered_anvil
    defaults = result.setdefault("agents", {}).setdefault("defaults", {})
    allowed = defaults.setdefault("models", {})
    if not isinstance(allowed, dict):
        allowed = {}
        defaults["models"] = allowed
    for model_id in tuple(allowed):
        if model_id.startswith("anvil/"):
            allowed.pop(model_id)
    rendered_defaults = rendered["agents"]["defaults"]
    allowed.update(rendered_defaults["models"])
    current_image_model = defaults.get("imageModel")
    current_image_primary = _openclaw_model_primary(current_image_model)
    rendered_image_model = rendered_defaults.get("imageModel")
    if rendered_image_model is not None:
        if current_image_primary is None or current_image_primary.startswith("anvil/"):
            if isinstance(current_image_model, dict):
                current_image_model["primary"] = rendered_image_model["primary"]
                defaults["imageModel"] = current_image_model
            else:
                defaults["imageModel"] = rendered_image_model
    elif isinstance(current_image_primary, str) and current_image_primary.startswith("anvil/"):
        if isinstance(current_image_model, dict):
            current_image_model.pop("primary", None)
            if current_image_model:
                defaults["imageModel"] = current_image_model
            else:
                defaults.pop("imageModel", None)
        else:
            defaults.pop("imageModel", None)
    # Delete the only Anvil-owned compatibility path. Other installed plugins remain.
    plugins = result.get("plugins")
    if isinstance(plugins, dict):
        entries = plugins.get("entries")
        if isinstance(entries, dict):
            entries.pop("openclaw-anvil-intent-router", None)
    if "talk" in rendered:
        result["talk"] = rendered["talk"]
    return result


def _write_local(path, rendered, overwrite):
    existed = os.path.exists(path)
    old = ""
    if existed:
        with open(path, "r", encoding="utf-8") as handle:
            old = handle.read()
    if existed and not overwrite:
        try:
            existing = json.loads(old) if old.strip() else {}
        except ValueError as exc:
            raise ValueError("OpenClaw config must be JSON when merging: %s" % exc) from exc
        rendered = _merge_provider(existing, rendered)
    if existed:
        with open(path + ".bak", "w", encoding="utf-8") as handle:
            handle.write(old)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(rendered, indent=2, ensure_ascii=False) + "\n")
    return "merged" if existed and not overwrite else "written"


def cmd_sync_openclaw(config_path, *, out=None, base_url, api_key_env, voice=False,
                      voice_realtime_url=DEFAULT_ANVIL_VOICE_REALTIME_URL, voice_model=None,
                      voice_consult_model="", voice_consult_thinking_level="off",
                      voice_consult_bootstrap_context_mode="lightweight", voice_api_key_env=None,
                      gateway_host=None, gateway_user=None, overwrite=False, restart=False,
                      timeout_seconds=DEFAULT_TRANSPORT_TIMEOUT_SECONDS, _load=None,
                      _applied_payload=None, **_ignored):
    try:
        preview = openclaw_sync_preview(
            config_path, base_url=base_url, api_key_env=api_key_env, voice=voice,
            voice_realtime_url=voice_realtime_url, voice_model=voice_model,
            voice_consult_model=voice_consult_model,
            voice_consult_thinking_level=voice_consult_thinking_level,
            voice_consult_bootstrap_context_mode=voice_consult_bootstrap_context_mode,
            voice_api_key_env=voice_api_key_env, _load=_load,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    payload = preview["provider"]
    if gateway_host:
        print("remote OpenClaw sync is no longer supported; render locally and apply on the gateway", file=sys.stderr)
        return 2
    if not out or out == "-":
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    else:
        try:
            mode = _write_local(os.path.expanduser(out), payload, overwrite)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print("wrote OpenClaw direct-alias provider (%d aliases, %s) -> %s" % (
            preview["model_count"], mode, out))
    if _applied_payload is not None:
        _applied_payload.clear()
        _applied_payload.update(payload)
    if restart:
        return cmd_restart_openclaw(timeout_seconds=timeout_seconds)
    return 0


def cmd_restart_openclaw(gateway_host=None, gateway_user=None, *, timeout_seconds=DEFAULT_TRANSPORT_TIMEOUT_SECONDS,
                         dry_run=False, _run=subprocess.run):
    if gateway_host:
        host = _validate_gateway_host(gateway_host)
        target = (gateway_user + "@" if gateway_user else "") + host
        argv = ["ssh", target, "openclaw", "gateway", "restart"]
    else:
        argv = ["openclaw", "gateway", "restart"]
    if dry_run:
        print(" ".join(argv))
        return 0
    try:
        completed = _run(argv, capture_output=True, text=True, timeout=timeout_seconds)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print("OpenClaw restart failed: %s" % exc, file=sys.stderr)
        return 1
    if completed.returncode:
        print((completed.stderr or completed.stdout or "OpenClaw restart failed").strip(), file=sys.stderr)
        return 1
    return 0


def openclaw_gateway_status(*, timeout_seconds=DEFAULT_TRANSPORT_TIMEOUT_SECONDS,
                            max_output_bytes=DEFAULT_STATUS_MAX_OUTPUT_BYTES, _run=subprocess.run):
    try:
        completed = _run(["openclaw", "gateway", "status", "--json"], capture_output=True,
                         text=True, timeout=timeout_seconds)
    except FileNotFoundError:
        return {"ok": False, "error": "openclaw is not installed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "OpenClaw gateway status timed out"}
    stdout = (completed.stdout or "")[:max_output_bytes]
    stderr = (completed.stderr or "")[:max_output_bytes]
    result = {"ok": completed.returncode == 0, "returncode": completed.returncode,
              "stdout": stdout, "stderr": stderr,
              "stdout_truncated": len(completed.stdout or "") > max_output_bytes,
              "stderr_truncated": len(completed.stderr or "") > max_output_bytes}
    if result["ok"]:
        try:
            result["status"] = json.loads(stdout)
        except ValueError:
            pass
    return result


def cmd_status_openclaw(**kwargs):
    result = openclaw_gateway_status(**kwargs)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def cmd_sync_clients(*, base_url, api_key_env="ANVIL_ROUTER_TOKEN",
                     clients="openclaw,pi",
                     openclaw_config="~/.openclaw/openclaw.json",
                     hermes_config="~/.hermes/config.yaml",
                     pi_models="~/.pi/agent/models.json",
                     pi_settings="~/.pi/agent/settings.json",
                     state_path="~/.anvil-serving/state/client-catalog.json",
                     backup_root="~/.anvil-serving/backups/client-catalog",
                     restart_openclaw_on_change=False, dry_run=True, confirm=False,
                     timeout_seconds=15, _opener=None, _restart=None, _environ=None):
    """Reconcile Mini clients from authenticated router model metadata."""
    from .client_catalog_sync import ClientCatalogError, sync_clients

    try:
        result = sync_clients(
            base_url=base_url,
            api_key_env=api_key_env,
            clients=clients,
            openclaw_config=openclaw_config,
            hermes_config=hermes_config,
            pi_models=pi_models,
            pi_settings=pi_settings,
            state_path=state_path,
            backup_root=backup_root,
            restart_openclaw_on_change=restart_openclaw_on_change,
            dry_run=dry_run,
            confirm=confirm,
            timeout_seconds=timeout_seconds,
            opener=_opener,
            restart=_restart,
            environ=_environ,
        )
    except (OSError, ClientCatalogError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _build_parser():
    parser = argparse.ArgumentParser(prog="anvil-serving harness")
    actions = parser.add_subparsers(dest="action", required=True)
    sync_targets = actions.add_parser("sync").add_subparsers(dest="target", required=True)
    sync = sync_targets.add_parser("openclaw")
    sync.add_argument("--config", required=True)
    sync.add_argument("--out")
    sync.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    sync.add_argument("--api-key-env", default="ANVIL_ROUTER_TOKEN")
    sync.add_argument("--voice", action="store_true")
    sync.add_argument("--voice-realtime-url", default=DEFAULT_ANVIL_VOICE_REALTIME_URL)
    sync.add_argument("--voice-model")
    sync.add_argument("--voice-api-key-env")
    sync.add_argument("--overwrite", action="store_true")
    clients = sync_targets.add_parser("clients")
    clients.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    clients.add_argument("--api-key-env", default="ANVIL_ROUTER_TOKEN")
    clients.add_argument(
        "--clients",
        default="openclaw,pi",
        help="comma-separated clients to reconcile: openclaw, hermes, pi",
    )
    clients.add_argument("--openclaw-config", default="~/.openclaw/openclaw.json")
    clients.add_argument("--hermes-config", default="~/.hermes/config.yaml")
    clients.add_argument("--pi-models", default="~/.pi/agent/models.json")
    clients.add_argument("--pi-settings", default="~/.pi/agent/settings.json")
    clients.add_argument("--state-path", default="~/.anvil-serving/state/client-catalog.json")
    clients.add_argument("--backup-root", default="~/.anvil-serving/backups/client-catalog")
    clients.add_argument("--restart-openclaw-on-change", action="store_true")
    clients.add_argument("--dry-run", action="store_true")
    clients.add_argument("--timeout-seconds", type=int, default=15)
    restart = actions.add_parser("restart").add_subparsers(dest="target", required=True).add_parser("openclaw")
    restart.add_argument("--gateway-host")
    restart.add_argument("--gateway-user")
    restart.add_argument("--dry-run", action="store_true")
    status = actions.add_parser("status").add_subparsers(dest="target", required=True).add_parser("openclaw")
    status.add_argument("--timeout-seconds", type=int, default=DEFAULT_TRANSPORT_TIMEOUT_SECONDS)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    if args.action == "sync":
        if args.target == "clients":
            from . import guard

            return cmd_sync_clients(
                base_url=args.base_url,
                api_key_env=args.api_key_env,
                clients=args.clients,
                openclaw_config=args.openclaw_config,
                hermes_config=args.hermes_config,
                pi_models=args.pi_models,
                pi_settings=args.pi_settings,
                state_path=args.state_path,
                backup_root=args.backup_root,
                restart_openclaw_on_change=args.restart_openclaw_on_change,
                dry_run=args.dry_run,
                confirm=guard.confirmation_authorized(),
                timeout_seconds=args.timeout_seconds,
                _restart=lambda: cmd_restart_openclaw(
                    timeout_seconds=DEFAULT_TRANSPORT_TIMEOUT_SECONDS
                ),
            )
        return cmd_sync_openclaw(args.config, out=args.out, base_url=args.base_url,
                                 api_key_env=args.api_key_env, voice=args.voice,
                                 voice_realtime_url=args.voice_realtime_url,
                                 voice_model=args.voice_model, voice_api_key_env=args.voice_api_key_env,
                                 overwrite=args.overwrite)
    if args.action == "restart":
        return cmd_restart_openclaw(args.gateway_host, args.gateway_user, dry_run=args.dry_run)
    return cmd_status_openclaw(timeout_seconds=args.timeout_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
