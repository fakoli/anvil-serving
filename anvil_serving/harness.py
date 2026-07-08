"""anvil-serving harness — own the HARNESS-side config, not just the router.

`serves` manages the model backends and `router` manages the deployed front door; this verb
extends that ownership to the harness anvil fronts (CLAUDE.md golden rule "anvil-serving owns the
harness-side config too"). It RENDERS the harness's config FROM the live router config so the two
never drift — instead of hand-editing the gateway out-of-band.

v1 target: **OpenClaw**. `harness sync openclaw --config <router.toml>` emits the OpenClaw provider
config — one selectable model per router preset, each `contextWindow` set to the LARGEST tier that
preset can route to (the contextWindow-clamp gotcha, docs/OPENCLAW-INTEGRATION-SPEC.md §2). It
does NOT emit per-preset thinking overrides: the router owns reasoning/thinking per tier now
(heavy `reasoning_effort`, fast `enable_thinking`), so re-declaring them on the harness is stale.
Pass `--voice` to also render OpenClaw Talk realtime config for the Anvil Voice provider.

`--skills` also renders the OpenClaw-visible workbench skill and Anvil sub-agent roles. It only
touches Anvil-owned skill/agent keys and keeps operator-owned providers, agents, plugins, and
skills around it.

The OpenClaw GATEWAY is typically REMOTE from the router (e.g. Fakoli Mini -> fakoli-dark), so this
either EMITS the config (stdout or `--out`) OR pushes it to the remote gateway over ssh with
`--gateway-host` — MERGING the anvil provider into the remote `~/.openclaw/openclaw.json` (preserving
the operator's other providers/agents/plugins) and backing up the remote first; `--overwrite` does a
full write.

OpenClaw reads its config at gateway STARTUP, so a config change is only picked up after a restart:
`harness sync openclaw ... --restart` restarts the gateway after the push, and `harness restart
openclaw [--gateway-host <mini>]` restarts it on its own — `openclaw gateway restart`, run locally or
over ssh (a single command invocation, not a shell script, so it stays portable against any-OS gateway).

stdlib-only (ssh via `subprocess`, injected for tests).
"""
import argparse
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import urllib.parse

# Per-preset OpenClaw hints (advisory display/caps; contextWindow is computed from the router).
# maxTokens is the harness's output cap; input declares modalities (review accepts images).
_PRESET_MAX_TOKENS = {
    "planning": 32000, "review": 16000, "long-context": 16000,
    "quick-edit": 8192, "chat": 8192,
}
_PRESET_INPUT = {"review": ["text", "image"]}
_DEFAULT_MAX_TOKENS = 8192

# The OpenClaw `plugins.entries` key MUST equal the PACKAGED plugin id
# (plugins/openclaw-anvil-intent-router/openclaw.plugin.json), or the before_model_resolve hook never
# gets its allowConversationAccess gate and intent routing silently no-ops. (The OPENCLAW-INTEGRATION-
# SPEC recipe predates the plugin's `openclaw-` rename; the plugin README + LIVE-VALIDATION are right.)
_PLUGIN_ID = "openclaw-anvil-intent-router"
_GATEWAY_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_GATEWAY_USER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
DEFAULT_TRANSPORT_TIMEOUT_SECONDS = 120
_REMOTE_RESTART_COMMAND = 'exec "${SHELL:-sh}" -lc "openclaw gateway restart"'
_DEFAULT_OPENCLAW_CONFIG_PATH = "~/.openclaw/openclaw.json"
DEFAULT_ANVIL_VOICE_REALTIME_URL = "ws://127.0.0.1:8765/v1/realtime"
_DEFAULT_ANVIL_VOICE_MODEL = "chat-fast"
_DEFAULT_ANVIL_VOICE_CONSULT_MODEL = ""
_DEFAULT_ANVIL_VOICE_CONSULT_THINKING_LEVEL = "off"
_DEFAULT_ANVIL_VOICE_CONSULT_BOOTSTRAP_CONTEXT_MODE = "lightweight"
_ANVIL_VOICE_CONSULT_THINKING_LEVELS = frozenset({
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "adaptive",
    "max",
})
_ANVIL_VOICE_CONSULT_BOOTSTRAP_CONTEXT_MODES = frozenset({"full", "lightweight"})
_ANVIL_VOICE_CONSULT_ROUTING = "force-agent-consult"
_LEGACY_GENERATED_PLUGIN_CONFIG_DEFAULTS = {
    "nativeProvider": "anthropic",
    "nativeModel": "claude-sonnet-4-5",
    "routeTimeoutMs": 30,
}
_WORKBENCH_SKILL_NAME = "anvil-serving-workbench"
_OPENCLAW_AGENT_ROLES = (
    ("anvil-orchestrator", ("planning", "review", "chat"), "planning", False),
    ("anvil-inventory-scout", ("chat-fast", "chat"), "chat", True),
    ("anvil-route-analyst", ("chat-fast", "chat"), "chat", True),
    ("anvil-serve-operator", ("chat-fast", "chat"), "chat", True),
    ("anvil-preflight-runner", ("chat-fast", "chat"), "chat", True),
    ("anvil-benchmark-runner", ("chat-fast", "chat"), "chat", True),
    ("anvil-evidence-reporter", ("chat-fast", "chat"), "chat", True),
    ("anvil-quality-critic", ("review", "planning", "chat"), "review", False),
    ("anvil-adversarial-reviewer", ("review", "planning", "chat"), "review", False),
)
_OPENCLAW_LEGACY_AGENT_NAMES = frozenset({
    "anvil-probe-evidence-runner",
})


def _title(preset_id):
    """`quick-edit` -> `Quick Edit` for the OpenClaw display name."""
    return " ".join(w.capitalize() for w in preset_id.replace("_", "-").split("-"))


def render_openclaw_provider(config, *, base_url, api_key_env="ANVIL_ROUTER_TOKEN"):
    """Render the OpenClaw provider + agent config dict from a loaded RouterConfig.

    One model per preset; `contextWindow` = max `context_limit` among the preset's candidate
    tiers (so a request within the window always fits SOME routed tier — clamp gotcha). No
    per-preset thinking overrides (the router owns reasoning/thinking per tier).
    """
    models = []
    for preset_id, tier_ids in config.presets.items():
        windows = [config.tier(t).context_limit for t in tier_ids]
        ctx = max(windows) if windows else 0
        models.append({
            "id": preset_id,
            "name": "Anvil · " + _title(preset_id),
            # reasoning:true surfaces OpenClaw's per-message reasoning selector. Every preset can
            # route to the reasoning-capable heavy tier, whose reasoning_effort is a soft default
            # (extra_body_defaults) the request overrides; a fast-only fallback ignores it harmlessly.
            "reasoning": True,
            "input": _PRESET_INPUT.get(preset_id, ["text"]),
            "contextWindow": ctx,
            "maxTokens": _PRESET_MAX_TOKENS.get(preset_id, _DEFAULT_MAX_TOKENS),
        })
    return {
        "models": {
            "mode": "merge",
            "providers": {
                "anvil": {
                    "baseUrl": base_url,
                    "apiKey": "${%s}" % api_key_env,
                    "api": "openai-completions",
                    "models": models,
                }
            },
        },
        # agents.defaults.models is OpenClaw's DROPDOWN ALLOWLIST — a preset only shows in the picker
        # if it has an entry here. So list EVERY preset, with EMPTY params (no per-preset thinking
        # override — the router owns reasoning/thinking per tier). Deleting these entries removes the
        # anvil presets from OpenClaw entirely (the 2026-07-04 regression); the goal is to strip only
        # the stale params, KEEPING the allowlist entry.
        "agents": {"defaults": {"model": {"primary": "anvil/chat"},
                                "models": {"anvil/" + m["id"]: {} for m in models}}},
        "plugins": {"entries": {_PLUGIN_ID: {
            "hooks": {"allowConversationAccess": True},
        }}},
    }


def _validate_env_var_name(value, *, arg_name):
    if not value:
        return None
    if not isinstance(value, str) or not _ENV_NAME_RE.fullmatch(value):
        raise ValueError("%s must be an ENV_VAR_NAME" % arg_name)
    return value


def _is_tailscale_v4(addr):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10")


def _is_safe_voice_ip(addr):
    ip = ipaddress.ip_address(addr)
    if ip.is_unspecified or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        return False
    if ip.version == 4:
        rfc1918 = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        )
        return bool(ip.is_loopback or _is_tailscale_v4(addr) or any(ip in network for network in rfc1918))
    return bool(ip.is_loopback or ip in ipaddress.ip_network("fc00::/7"))


def _is_loopback_voice_host(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_voice_realtime_host(host):
    normalized = host.lower().rstrip(".")
    if normalized == "local" + "host":
        raise ValueError("voice realtime URL must use 127.0.0.1 or a private/tailnet host")
    try:
        if not _is_safe_voice_ip(host):
            raise ValueError(
                "voice realtime URL host must be loopback, private, or tailnet-safe"
            )
        return _is_loopback_voice_host(host)
    except ValueError as exc:
        if "voice realtime URL" in str(exc):
            raise
    if normalized.endswith(".local") or normalized.endswith(".ts.net"):
        return False
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("voice realtime URL host could not be resolved safely: %s" % exc)
    addrs = []
    for info in infos:
        try:
            addrs.append(info[4][0])
        except (IndexError, TypeError):
            pass
    if not addrs or any(not _is_safe_voice_ip(addr) for addr in addrs):
        raise ValueError("voice realtime URL host must resolve only to loopback, private, or tailnet addresses")
    return all(_is_loopback_voice_host(addr) for addr in addrs)


def _validate_realtime_url(value, *, api_key_env=None):
    if not isinstance(value, str) or not value:
        raise ValueError("voice realtime URL must be a non-empty ws:// or wss:// URL")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("ws", "wss") or not parsed.netloc:
        raise ValueError("voice realtime URL must be a ws:// or wss:// URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("voice realtime URL must not embed credentials, query strings, or fragments")
    loopback = _validate_voice_realtime_host(parsed.hostname or "")
    if not loopback and not api_key_env:
        raise ValueError(
            "non-loopback Anvil Voice realtime URLs require --voice-api-key-env"
        )
    return value


def _normalize_voice_consult_thinking_level(value):
    normalized = (value or "").strip().lower()
    if not normalized:
        return None
    if normalized not in _ANVIL_VOICE_CONSULT_THINKING_LEVELS:
        raise ValueError(
            "voice consult thinking level must be one of: %s"
            % ", ".join(sorted(_ANVIL_VOICE_CONSULT_THINKING_LEVELS))
        )
    return normalized


def _normalize_voice_consult_bootstrap_context_mode(value):
    normalized = (value or "").strip().lower()
    if not normalized:
        return None
    if normalized not in _ANVIL_VOICE_CONSULT_BOOTSTRAP_CONTEXT_MODES:
        raise ValueError(
            "voice consult bootstrap context mode must be one of: %s"
            % ", ".join(sorted(_ANVIL_VOICE_CONSULT_BOOTSTRAP_CONTEXT_MODES))
        )
    return normalized


def render_openclaw_voice_config(
        *, realtime_url=DEFAULT_ANVIL_VOICE_REALTIME_URL,
        model=_DEFAULT_ANVIL_VOICE_MODEL,
        consult_model=None,
        consult_thinking_level=_DEFAULT_ANVIL_VOICE_CONSULT_THINKING_LEVEL,
        consult_bootstrap_context_mode=_DEFAULT_ANVIL_VOICE_CONSULT_BOOTSTRAP_CONTEXT_MODE,
        api_key_env=None):
    """Render OpenClaw Talk realtime config for the Anvil Voice gateway-relay provider."""
    env_name = _validate_env_var_name(api_key_env, arg_name="voice_api_key_env")
    normalized_consult_model = (consult_model or "").strip()
    normalized_consult_thinking_level = _normalize_voice_consult_thinking_level(
        consult_thinking_level
    )
    normalized_consult_bootstrap_context_mode = _normalize_voice_consult_bootstrap_context_mode(
        consult_bootstrap_context_mode
    )
    provider_config = {
        "realtimeUrl": _validate_realtime_url(realtime_url, api_key_env=env_name),
        "model": model or _DEFAULT_ANVIL_VOICE_MODEL,
        "silenceDurationMs": 200,
    }
    if env_name:
        provider_config["apiKey"] = {"source": "env", "provider": "default", "id": env_name}
    return {
        "talk": {
            **({"consultModel": normalized_consult_model} if normalized_consult_model else {}),
            **(
                {"consultThinkingLevel": normalized_consult_thinking_level}
                if normalized_consult_thinking_level
                else {}
            ),
            **(
                {"consultBootstrapContextMode": normalized_consult_bootstrap_context_mode}
                if normalized_consult_bootstrap_context_mode
                else {}
            ),
            "realtime": {
                "mode": "realtime",
                "transport": "gateway-relay",
                "brain": "agent-consult",
                "consultRouting": _ANVIL_VOICE_CONSULT_ROUTING,
                "provider": "anvil",
                "providers": {"anvil": provider_config},
            }
        }
    }


def _openclaw_voice_consult_model(config, explicit=None):
    explicit = (explicit or "").strip()
    if explicit:
        return explicit
    return _anvil_preset_ref(config, ("chat-fast", "chat"), fallback="chat")


def _openclaw_voice_model(config, explicit=None):
    explicit = (explicit or "").strip()
    if explicit:
        return explicit
    presets = getattr(config, "presets", {}) or {}
    if "chat-fast" in presets:
        return "chat-fast"
    if "chat" in presets:
        return "chat"
    for preset_id in presets:
        return str(preset_id)
    return _DEFAULT_ANVIL_VOICE_MODEL


def _anvil_preset_ref(config, preferred, *, fallback="chat", allow_first_fallback=True):
    presets = getattr(config, "presets", {}) or {}
    for preset_id in preferred:
        if preset_id in presets:
            return "anvil/" + preset_id
    if "chat" in presets:
        return "anvil/chat"
    if allow_first_fallback:
        for preset_id in presets:
            return "anvil/" + str(preset_id)
    return "anvil/" + fallback


def render_openclaw_skills(config, *, skill_dir=None):
    """Render Anvil-owned OpenClaw skill and sub-agent config.

    ``skill_dir`` enables checkout-loaded skills through ``skills.load.extraDirs``. When omitted,
    the payload assumes the workbench skill was installed into OpenClaw's workspace skill directory
    with ``openclaw skills install ... --as anvil-serving-workbench``.
    """
    roles = []
    for role_name, preferred_presets, fallback, allow_first_fallback in _OPENCLAW_AGENT_ROLES:
        roles.append({
            "name": role_name,
            "model": _anvil_preset_ref(
                config,
                preferred_presets,
                fallback=fallback,
                allow_first_fallback=allow_first_fallback,
            ),
            "skills": [_WORKBENCH_SKILL_NAME],
        })
    out = {
        "agents": {
            "defaults": {"skills": [_WORKBENCH_SKILL_NAME]},
            "list": roles,
        },
    }
    if skill_dir:
        out["skills"] = {"load": {"extraDirs": [skill_dir]}}
    return out


def _merge_unique_strings(existing, additions):
    out = []
    for value in existing if isinstance(existing, list) else []:
        if isinstance(value, str) and value not in out:
            out.append(value)
    for value in additions if isinstance(additions, list) else []:
        if isinstance(value, str) and value not in out:
            out.append(value)
    return out


def _merge_openclaw_skill_config(out, rendered):
    """Merge Anvil-owned skill/agent keys from ``rendered`` into ``out`` in place."""
    rendered_skills = rendered.get("skills") if isinstance(rendered.get("skills"), dict) else {}
    rendered_load = rendered_skills.get("load") if isinstance(rendered_skills.get("load"), dict) else {}
    rendered_dirs = rendered_load.get("extraDirs") if isinstance(rendered_load.get("extraDirs"), list) else []
    if rendered_dirs:
        skills = out.setdefault("skills", {})
        if not isinstance(skills, dict):
            skills = {}
            out["skills"] = skills
        load = skills.setdefault("load", {})
        if not isinstance(load, dict):
            load = {}
            skills["load"] = load
        load["extraDirs"] = _merge_unique_strings(load.get("extraDirs", []), rendered_dirs)

    rendered_agents = rendered.get("agents") if isinstance(rendered.get("agents"), dict) else {}
    rendered_defaults = (
        rendered_agents.get("defaults") if isinstance(rendered_agents.get("defaults"), dict) else {}
    )
    rendered_default_skills = rendered_defaults.get("skills")
    rendered_roles = rendered_agents.get("list")
    if rendered_default_skills or rendered_roles:
        agents = out.setdefault("agents", {})
        if not isinstance(agents, dict):
            agents = {}
            out["agents"] = agents
        defaults = agents.setdefault("defaults", {})
        if not isinstance(defaults, dict):
            defaults = {}
            agents["defaults"] = defaults
        if rendered_default_skills:
            defaults["skills"] = _merge_unique_strings(
                defaults.get("skills", []),
                rendered_default_skills,
            )
        if isinstance(rendered_roles, list):
            existing_roles = agents.get("list", [])
            if not isinstance(existing_roles, list):
                existing_roles = []
            rendered_by_name = {
                role.get("name"): role
                for role in rendered_roles
                if isinstance(role, dict) and isinstance(role.get("name"), str)
            }
            preserved = [
                role for role in existing_roles
                if not (
                    isinstance(role, dict)
                    and (
                        role.get("name") in rendered_by_name
                        or role.get("name") in _OPENCLAW_LEGACY_AGENT_NAMES
                    )
                )
            ]
            agents["list"] = preserved + list(rendered_by_name.values())
    return out


def _with_openclaw_skills(provider, skills_payload):
    out = json.loads(json.dumps(provider))
    return _merge_openclaw_skill_config(out, skills_payload)


def _merge_openclaw_voice_config(out, rendered):
    rendered_talk = rendered.get("talk") if isinstance(rendered.get("talk"), dict) else {}
    rendered_rt = rendered_talk.get("realtime") if isinstance(rendered_talk.get("realtime"), dict) else {}
    rendered_providers = (
        rendered_rt.get("providers") if isinstance(rendered_rt.get("providers"), dict) else {}
    )
    rendered_anvil = rendered_providers.get("anvil")
    if not isinstance(rendered_anvil, dict):
        return out

    talk = out.setdefault("talk", {})
    if not isinstance(talk, dict):
        talk = {}
        out["talk"] = talk
    for key, value in rendered_talk.items():
        if key != "realtime":
            talk[key] = value
    realtime = talk.setdefault("realtime", {})
    if not isinstance(realtime, dict):
        realtime = {}
        talk["realtime"] = realtime
    for key, value in rendered_rt.items():
        if key != "providers":
            realtime[key] = value
    providers = realtime.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        realtime["providers"] = providers
    existing_anvil = providers.get("anvil") if isinstance(providers.get("anvil"), dict) else {}
    merged_anvil = json.loads(json.dumps(existing_anvil))
    merged_anvil.update(rendered_anvil)
    providers["anvil"] = merged_anvil
    return out


def _with_openclaw_voice_config(provider, voice_payload):
    out = json.loads(json.dumps(provider))
    return _merge_openclaw_voice_config(out, voice_payload)


# --------------------------------------------------------------------------- #
# remote (ssh) sync — the OpenClaw gateway is typically REMOTE from the router
# --------------------------------------------------------------------------- #

def _ssh_target(host, user):
    _validate_gateway_target(host, user)
    return ("%s@%s" % (user, host)) if user else host


def _normalize_timeout_seconds(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 7200:
        raise ValueError("timeout_seconds must be an integer between 1 and 7200")
    return value


def _ssh_options(timeout_seconds):
    timeout_seconds = _normalize_timeout_seconds(timeout_seconds)
    connect_timeout = max(1, min(int(timeout_seconds), 60))
    return [
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=%d" % connect_timeout,
        "-o", "ServerAliveInterval=5",
        "-o", "ServerAliveCountMax=1",
    ]


def _validate_gateway_target(host, user=None):
    """Reject SSH/SCP option injection before user strings reach OpenSSH."""
    if not host or not isinstance(host, str) or not _GATEWAY_HOST_RE.fullmatch(host):
        raise ValueError("gateway host must be a DNS name or IPv4-style token, not an SSH option")
    if user and (not isinstance(user, str) or not _GATEWAY_USER_RE.fullmatch(user) or user.startswith("-")):
        raise ValueError("gateway user must not be an SSH option")


def _is_default_openclaw_config_path(path):
    return (
        os.path.abspath(os.path.expanduser(path))
        == os.path.abspath(os.path.expanduser(_DEFAULT_OPENCLAW_CONFIG_PATH))
    )


def _is_stdout_out(path):
    return not path or path == "-"


def _merge_anvil_provider(existing, rendered):
    """Merge ONLY anvil-owned keys of `rendered` into the operator's existing OpenClaw config,
    preserving their OTHER providers / agents / plugins / skills. Returns a NEW dict."""
    out = json.loads(json.dumps(existing))  # deep copy
    models = out.setdefault("models", {})
    models["mode"] = "merge"
    providers = models.setdefault("providers", {})
    existing_anvil = providers.get("anvil") or {}
    new_anvil = dict(rendered["models"]["providers"]["anvil"])
    # PRESERVE the operator's LIVE baseUrl + apiKey if already set on the remote — the rendered ones
    # are a default host + a `${ENV}` placeholder, and a sync must NEVER clobber a working URL/token
    # (e.g. the Mini gateway pins a LITERAL token its env may not otherwise provide; overwriting it
    # with `${ANVIL_ROUTER_TOKEN}` would 401 every request). The models[] (reasoning, contextWindow,
    # …) DO get updated — that is the point of the sync.
    for k in ("baseUrl", "apiKey"):
        if existing_anvil.get(k):
            new_anvil[k] = existing_anvil[k]
    providers["anvil"] = new_anvil
    defaults = out.setdefault("agents", {}).setdefault("defaults", {})
    defaults.setdefault("model", {}).setdefault("primary", "anvil/chat")
    # Re-assert the anvil/* DROPDOWN ALLOWLIST: drop any stale entries (they may carry an old
    # thinking override) then re-add the rendered ones (EMPTY params). Keeping the entries is
    # essential — deleting them removes the presets from OpenClaw's picker entirely.
    dmodels = defaults.setdefault("models", {})
    if isinstance(dmodels, dict):
        for k in [k for k in dmodels if str(k).startswith("anvil/")]:
            del dmodels[k]
        dmodels.update(rendered["agents"]["defaults"]["models"])
    entries = out.setdefault("plugins", {}).setdefault("entries", {})
    rendered_entry = rendered["plugins"]["entries"][_PLUGIN_ID]
    existing_entry = entries.get(_PLUGIN_ID)
    if isinstance(existing_entry, dict):
        merged_entry = json.loads(json.dumps(existing_entry))
        hooks = merged_entry.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            hooks = {}
        hooks.update(rendered_entry["hooks"])
        merged_entry["hooks"] = hooks
        config = merged_entry.get("config")
        if isinstance(config, dict):
            for key, default in _LEGACY_GENERATED_PLUGIN_CONFIG_DEFAULTS.items():
                if config.get(key) == default:
                    del config[key]
            if not config:
                merged_entry.pop("config", None)
        entries[_PLUGIN_ID] = merged_entry
    else:
        entries[_PLUGIN_ID] = rendered_entry
    out = _merge_openclaw_skill_config(out, rendered)
    return _merge_openclaw_voice_config(out, rendered)


def _payload_for_existing_config(existing_text, rendered, *, overwrite, path):
    if overwrite or not existing_text.strip():
        return rendered, "overwrite" if existing_text.strip() else "created"
    try:
        return _merge_anvil_provider(json.loads(existing_text), rendered), "merged"
    except ValueError:
        raise ValueError(
            "refusing to merge: %s is not plain JSON (JSON5/comments?). Re-run with "
            "--overwrite (back up the file first), or edit it by hand." % path
        )


def _tmpfile():
    fd, p = tempfile.mkstemp(prefix="anvil-harness-", suffix=".json")
    os.close(fd)
    return p


def _sync_over_ssh(host, user, path, rendered, *, overwrite,
                   timeout_seconds=DEFAULT_TRANSPORT_TIMEOUT_SECONDS, _run):
    """Push the rendered config to a REMOTE gateway via **scp** — deliberately NO remote shell, so it
    works FROM a Windows or Linux local host AND against a Windows / macOS / Linux gateway (all ship
    OpenSSH's `scp`/sftp-server; a POSIX remote-shell script would break on a Windows gateway). Reads
    the remote with scp, MERGES/overwrites LOCALLY, backs the remote up (pushes the ORIGINAL back as
    `<path>.bak`), then writes. A merge is REFUSED if the remote isn't plain JSON (JSON5/comments) —
    re-run with --overwrite (backup still taken). Returns 0/1; scp runs through the injected `_run`."""
    try:
        tgt = _ssh_target(host, user)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        timeout_seconds = _normalize_timeout_seconds(timeout_seconds)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    remote = "%s:%s" % (tgt, path)
    read_tmp, write_tmp = _tmpfile(), _tmpfile()
    ssh_opts = _ssh_options(timeout_seconds)
    try:
        # 1. READ the remote via scp. A MISSING file is a clean "create", not a hard error.
        try:
            r = _run(["scp", "-q", *ssh_opts, "--", remote, read_tmp],
                     capture_output=True, text=True, timeout=timeout_seconds)
        except FileNotFoundError:
            print("scp not available on PATH (install the OpenSSH client)", file=sys.stderr)
            return 1
        except subprocess.TimeoutExpired:
            print("timed out reading %s over scp" % remote, file=sys.stderr)
            return 1
        existed = r.returncode == 0
        err = (r.stderr or "").strip()
        if not existed and err and "no such file" not in err.lower():
            print("cannot reach %s over scp: %s" % (remote, err), file=sys.stderr)
            return 1
        with open(read_tmp, "r", encoding="utf-8") as f:
            existing_text = f.read() if existed else ""

        # 2. MERGE / OVERWRITE locally.
        try:
            payload, mode = _payload_for_existing_config(
                existing_text, rendered, overwrite=overwrite, path="remote %s" % path,
            )
        except ValueError as exc:
            print(str(exc).replace("back up the file first", "a .bak is taken first"), file=sys.stderr)
            return 1
        with open(write_tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

        # 3. BACKUP (push the ORIGINAL content back as .bak — portable, no remote shell) then WRITE.
        if existed:
            try:
                b = _run(["scp", "-q", *ssh_opts, "--", read_tmp, "%s.bak" % remote],
                         capture_output=True, text=True, timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                print("WARNING: timed out backing up %s" % remote, file=sys.stderr)
                b = None
            if b is not None and b.returncode != 0:
                print("WARNING: could not back up %s: %s"
                      % (remote, (b.stderr or "").strip()), file=sys.stderr)
        try:
            w = _run(["scp", "-q", *ssh_opts, "--", write_tmp, remote],
                     capture_output=True, text=True, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            print("timed out writing %s over scp" % remote, file=sys.stderr)
            return 1
        if w.returncode != 0:
            print("FAILED to write %s: %s" % (remote, (w.stderr or w.stdout or "").strip()),
                  file=sys.stderr)
            return 1
        n = len(rendered["models"]["providers"]["anvil"]["models"])
        print("synced OpenClaw provider (%d preset models, %s) -> %s%s"
              % (n, mode, remote, " (backup taken)" if existed else ""))
        return 0
    finally:
        for t in (read_tmp, write_tmp):
            try:
                os.unlink(t)
            except OSError:
                pass


def _restart_openclaw_gateway(host, user, *, timeout_seconds=DEFAULT_TRANSPORT_TIMEOUT_SECONDS, _run):
    """Restart the OpenClaw gateway so it picks up config changes — `openclaw gateway restart`,
    locally or over ssh when the gateway is remote (--gateway-host). It's a single command
    invocation (NOT a shell script), so it stays portable against a Windows/macOS/Linux gateway.
    Returns 0/1."""
    try:
        timeout_seconds = _normalize_timeout_seconds(timeout_seconds)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if host:
        try:
            target = _ssh_target(host, user)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        argv = ["ssh", *_ssh_options(timeout_seconds), "--", target, _REMOTE_RESTART_COMMAND]
        where, missing = target, "ssh"
    else:
        argv, where, missing = ["openclaw", "gateway", "restart"], "local", "openclaw"
    try:
        r = _run(argv, capture_output=True, text=True, timeout=timeout_seconds)
    except FileNotFoundError:
        print("cannot restart gateway: %s not available on PATH" % missing, file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("timed out restarting the OpenClaw gateway on %s" % where, file=sys.stderr)
        return 1
    if r.returncode != 0:
        print("FAILED to restart the OpenClaw gateway on %s: %s"
              % (where, (r.stderr or r.stdout or "").strip()), file=sys.stderr)
        return 1
    print("restarted the OpenClaw gateway on %s (settings reloaded)" % where)
    return 0


def cmd_restart_openclaw(gateway_host=None, gateway_user=None,
                         timeout_seconds=DEFAULT_TRANSPORT_TIMEOUT_SECONDS, _run=subprocess.run):
    return _restart_openclaw_gateway(gateway_host, gateway_user,
                                     timeout_seconds=timeout_seconds, _run=_run)


def openclaw_sync_preview(config_path, *, base_url, api_key_env="ANVIL_ROUTER_TOKEN",
                          skills=False, skill_dir=None, voice=False,
                          voice_realtime_url=DEFAULT_ANVIL_VOICE_REALTIME_URL,
                          voice_model=None,
                          voice_consult_model=_DEFAULT_ANVIL_VOICE_CONSULT_MODEL,
                          voice_consult_thinking_level=_DEFAULT_ANVIL_VOICE_CONSULT_THINKING_LEVEL,
                          voice_consult_bootstrap_context_mode=(
                              _DEFAULT_ANVIL_VOICE_CONSULT_BOOTSTRAP_CONTEXT_MODE
                          ),
                          voice_api_key_env=None, _load=None):
    """Return the rendered OpenClaw sync payload without writing it anywhere."""
    if _load is None:
        from .router.config import load as _load
    config = _load(config_path)
    provider = render_openclaw_provider(config, base_url=base_url, api_key_env=api_key_env)
    if skills:
        provider = _with_openclaw_skills(provider, render_openclaw_skills(config, skill_dir=skill_dir))
    if voice:
        voice_payload = render_openclaw_voice_config(
            realtime_url=voice_realtime_url,
            model=_openclaw_voice_model(config, voice_model),
            consult_model=_openclaw_voice_consult_model(config, voice_consult_model),
            consult_thinking_level=voice_consult_thinking_level,
            consult_bootstrap_context_mode=voice_consult_bootstrap_context_mode,
            api_key_env=voice_api_key_env,
        )
        provider = _with_openclaw_voice_config(provider, voice_payload)
    models = provider["models"]["providers"]["anvil"]["models"]
    roles = provider.get("agents", {}).get("list", [])
    load_dirs = provider.get("skills", {}).get("load", {}).get("extraDirs", [])
    realtime = provider.get("talk", {}).get("realtime", {})
    voice_anvil = realtime.get("providers", {}).get("anvil", {}) if isinstance(realtime, dict) else {}
    return {
        "provider": provider,
        "model_count": len(models),
        "model_ids": [m["id"] for m in models],
        "plugin_id": _PLUGIN_ID,
        "base_url": provider["models"]["providers"]["anvil"]["baseUrl"],
        "api_key": provider["models"]["providers"]["anvil"]["apiKey"],
        "skills": bool(skills),
        "skill_name": _WORKBENCH_SKILL_NAME if skills else None,
        "skill_load_dirs": list(load_dirs) if isinstance(load_dirs, list) else [],
        "agent_names": [r.get("name") for r in roles if isinstance(r, dict) and r.get("name")],
        "agent_models": {
            r.get("name"): r.get("model")
            for r in roles
            if isinstance(r, dict) and r.get("name")
        },
        "voice": bool(voice),
        "voice_provider": realtime.get("provider") if isinstance(realtime, dict) else None,
        "voice_realtime_url": voice_anvil.get("realtimeUrl") if isinstance(voice_anvil, dict) else None,
        "voice_model": voice_anvil.get("model") if isinstance(voice_anvil, dict) else None,
        "voice_consult_model": provider.get("talk", {}).get("consultModel"),
        "voice_consult_thinking_level": provider.get("talk", {}).get("consultThinkingLevel"),
        "voice_consult_bootstrap_context_mode": provider.get("talk", {}).get(
            "consultBootstrapContextMode"
        ),
    }


def cmd_sync_openclaw(config_path, *, out=None, base_url, api_key_env, skills=False,
                      skill_dir=None, voice=False,
                      voice_realtime_url=DEFAULT_ANVIL_VOICE_REALTIME_URL,
                      voice_model=None,
                      voice_consult_model=_DEFAULT_ANVIL_VOICE_CONSULT_MODEL,
                      voice_consult_thinking_level=_DEFAULT_ANVIL_VOICE_CONSULT_THINKING_LEVEL,
                      voice_consult_bootstrap_context_mode=(
                          _DEFAULT_ANVIL_VOICE_CONSULT_BOOTSTRAP_CONTEXT_MODE
                      ),
                      voice_api_key_env=None,
                      gateway_host=None, gateway_user=None,
                      gateway_path=_DEFAULT_OPENCLAW_CONFIG_PATH, overwrite=False, restart=False,
                      timeout_seconds=DEFAULT_TRANSPORT_TIMEOUT_SECONDS, _load=None, _run=subprocess.run):
    if skill_dir and not skills:
        print("--skill-dir requires --skills", file=sys.stderr)
        return 2
    if _load is None:
        from .router.config import load as _load
    try:
        config = _load(config_path)
    except FileNotFoundError:
        print("router config not found: %s" % config_path, file=sys.stderr)
        return 2
    except Exception as e:  # malformed config
        print("cannot load router config %s: %s" % (config_path, e), file=sys.stderr)
        return 2
    if not getattr(config, "presets", None):
        print("router config %s declares no [router.presets] — nothing to sync." % config_path,
              file=sys.stderr)
        return 1
    try:
        provider = render_openclaw_provider(config, base_url=base_url, api_key_env=api_key_env)
        if skills:
            provider = _with_openclaw_skills(provider, render_openclaw_skills(config, skill_dir=skill_dir))
        if voice:
            voice_payload = render_openclaw_voice_config(
                realtime_url=voice_realtime_url,
                model=_openclaw_voice_model(config, voice_model),
                consult_model=_openclaw_voice_consult_model(config, voice_consult_model),
                consult_thinking_level=voice_consult_thinking_level,
                consult_bootstrap_context_mode=voice_consult_bootstrap_context_mode,
                api_key_env=voice_api_key_env,
            )
            provider = _with_openclaw_voice_config(provider, voice_payload)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if gateway_host:  # push to the REMOTE gateway over ssh (Mini -> the router)
        rc = _sync_over_ssh(gateway_host, gateway_user, gateway_path, provider,
                            overwrite=overwrite, timeout_seconds=timeout_seconds, _run=_run)
        if rc == 0 and restart:  # so the gateway picks up the new config
            return _restart_openclaw_gateway(gateway_host, gateway_user,
                                             timeout_seconds=timeout_seconds, _run=_run)
        return rc

    if restart and _is_stdout_out(out):
        print("--restart with a stdout-only sync would reload the gateway's OLD config (nothing "
              "was applied). Use --gateway-host <host>, or --out %s." % _DEFAULT_OPENCLAW_CONFIG_PATH,
              file=sys.stderr)
        return 2
    if restart and out and not _is_default_openclaw_config_path(out):
        print("--restart with --out requires the real local OpenClaw config path (%s); "
              "%s looks like a preview file." % (_DEFAULT_OPENCLAW_CONFIG_PATH, out),
              file=sys.stderr)
        return 2

    text = json.dumps(provider, indent=2, ensure_ascii=False) + "\n"
    if out and out != "-":
        existing_text = ""
        existed = os.path.exists(out)
        if existed:
            try:
                with open(out, "r", encoding="utf-8") as f:
                    existing_text = f.read()
            except OSError as exc:
                print("cannot read existing OpenClaw config %s: %s" % (out, exc), file=sys.stderr)
                return 1
        try:
            payload, mode = _payload_for_existing_config(existing_text, provider,
                                                         overwrite=overwrite, path=out)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if existed and existing_text:
            try:
                with open(out + ".bak", "w", encoding="utf-8") as f:
                    f.write(existing_text)
            except OSError as exc:
                print("WARNING: could not back up %s: %s" % (out, exc), file=sys.stderr)
        with open(out, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        n = len(provider["models"]["providers"]["anvil"]["models"])
        suffix = ""
        if skills:
            suffix = ", %d workbench agents" % len(provider.get("agents", {}).get("list", []))
        if voice:
            suffix += ", Anvil Voice realtime"
        print("wrote OpenClaw provider config (%d preset models%s, %s) -> %s"
              % (n, suffix, mode, out))
        print("  apply on the OpenClaw gateway at ~/.openclaw/openclaw.json, or push directly with "
              "--gateway-host <mini> (ssh; merges by default, backs up the remote first).")
    else:
        sys.stdout.write(text)
    if restart:  # config emitted locally; restart the LOCAL gateway to pick it up
        return _restart_openclaw_gateway(None, None, timeout_seconds=timeout_seconds, _run=_run)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    provided_options = {
        arg.split("=", 1)[0]
        for arg in argv
        if isinstance(arg, str) and arg.startswith("--")
    }
    p = argparse.ArgumentParser(
        prog="anvil-serving harness",
        description="Own the harness-side config: render a harness's model/provider config FROM "
                    "the live router config so the two never drift. v1: OpenClaw models.")
    p.add_argument("action", choices=["sync", "restart"],
                   help="sync: render the harness config from the router config; restart: restart "
                        "the gateway so it picks up config changes.")
    p.add_argument("harness", choices=["openclaw"], help="target harness (v1: openclaw).")
    p.add_argument("--config",
                   help="sync: router config TOML to render the harness models from (its presets + "
                        "tier context limits). Required for `sync`.")
    p.add_argument("--out", help="write the harness config here (default: stdout).")
    p.add_argument("--base-url", default="http://127.0.0.1:8000/v1",
                   help="the router front door the harness dials (default: %(default)s; use the "
                        "router's reachable host when the gateway is REMOTE).")
    p.add_argument("--api-key-env", default="ANVIL_ROUTER_TOKEN",
                   help="env var name holding the router bearer token (default: %(default)s); the "
                        "emitted config references it by name, never the secret.")
    p.add_argument("--gateway-host",
                   help="push the config to a REMOTE OpenClaw gateway over ssh (e.g. fakoli-mini); "
                        "MERGES the anvil provider into the remote config by default (backup taken).")
    p.add_argument("--gateway-user", help="ssh user for --gateway-host (default: your ssh config).")
    p.add_argument("--gateway-path", default=_DEFAULT_OPENCLAW_CONFIG_PATH,
                   help="remote OpenClaw config path for --gateway-host (default: %(default)s).")
    p.add_argument("--overwrite", action="store_true",
                   help="OVERWRITE the target config instead of merging Anvil-owned keys "
                        "(an existing local/remote target is backed up first).")
    p.add_argument("--restart", action="store_true",
                   help="after `sync`: restart the OpenClaw gateway so it picks up the new config "
                        "(over ssh when --gateway-host is set). Also the `restart` action on its own.")
    p.add_argument("--timeout-seconds", type=int, default=DEFAULT_TRANSPORT_TIMEOUT_SECONDS,
                   help="bound each ssh/scp/openclaw subprocess call (default: %(default)s).")
    p.add_argument("--skills", action="store_true",
                   help="also render/apply OpenClaw-visible workbench skill and Anvil sub-agent config.")
    p.add_argument("--skill-dir",
                   help="with --skills: add this OpenClaw-gateway-visible directory to "
                        "skills.load.extraDirs. Omit when the workbench skill is workspace-installed.")
    p.add_argument("--voice", action="store_true",
                   help="also render/apply OpenClaw Talk realtime config for the Anvil Voice provider.")
    p.add_argument("--voice-realtime-url", default=DEFAULT_ANVIL_VOICE_REALTIME_URL,
                   help="with --voice: Anvil Voice WebSocket URL (default: %(default)s).")
    p.add_argument("--voice-model",
                   help="with --voice: model sent to the Anvil Voice realtime session "
                        "(default: chat-fast when configured, else chat).")
    p.add_argument("--voice-consult-model", default=_DEFAULT_ANVIL_VOICE_CONSULT_MODEL,
                   help="with --voice: OpenClaw model used for forced agent consults "
                        "(default: anvil/chat-fast when configured, else anvil/chat).")
    p.add_argument("--voice-consult-thinking-level", default=_DEFAULT_ANVIL_VOICE_CONSULT_THINKING_LEVEL,
                   help="with --voice: OpenClaw thinking level for forced agent consults "
                        "(default: %(default)s for lower latency).")
    p.add_argument("--voice-consult-bootstrap-context-mode",
                   default=_DEFAULT_ANVIL_VOICE_CONSULT_BOOTSTRAP_CONTEXT_MODE,
                   help="with --voice: OpenClaw bootstrap context mode for forced agent consults "
                        "(default: %(default)s to skip workspace bootstrap files).")
    p.add_argument("--voice-api-key-env",
                   help="with --voice: env var name for the Anvil Voice bearer token; omitted for loopback.")
    a = p.parse_args(argv)

    if a.action == "restart" and a.harness == "openclaw":
        # `restart` only restarts the gateway; sync-only flags would be silently discarded, which
        # reads as "it did something" — reject them so the misuse is visible.
        sync_only_flags = {
            "--api-key-env",
            "--base-url",
            "--config",
            "--out",
            "--overwrite",
            "--skill-dir",
            "--skills",
            "--voice",
            "--voice-api-key-env",
            "--voice-consult-bootstrap-context-mode",
            "--voice-consult-model",
            "--voice-consult-thinking-level",
            "--voice-model",
            "--voice-realtime-url",
        }
        stray = sorted(sync_only_flags.intersection(provided_options))
        if stray:
            print("restart openclaw takes only --gateway-host/--gateway-user; drop %s (it does not "
                  "sync)." % ", ".join(stray), file=sys.stderr)
            return 2
        return cmd_restart_openclaw(gateway_host=a.gateway_host, gateway_user=a.gateway_user,
                                    timeout_seconds=a.timeout_seconds)

    if a.action == "sync" and a.harness == "openclaw":
        if not a.config:
            print("harness sync openclaw requires --config <router.toml>", file=sys.stderr)
            return 2
        if a.skill_dir and not a.skills:
            print("--skill-dir requires --skills", file=sys.stderr)
            return 2
        # A stdout-only sync isn't applied to the gateway's config file, so restarting would just
        # reload the OLD config and falsely report success. Require an APPLIED target for --restart.
        if a.restart and not a.gateway_host and _is_stdout_out(a.out):
            print("--restart with a stdout-only sync would reload the gateway's OLD config (nothing "
                  "was applied). Use --gateway-host <host>, or --out %s." % _DEFAULT_OPENCLAW_CONFIG_PATH,
                  file=sys.stderr)
            return 2
        if (a.restart and a.out and a.out != "-" and not a.gateway_host
                and not _is_default_openclaw_config_path(a.out)):
            print("--restart with --out requires the real local OpenClaw config path (%s); "
                  "%s looks like a preview file." % (_DEFAULT_OPENCLAW_CONFIG_PATH, a.out),
                  file=sys.stderr)
            return 2
        return cmd_sync_openclaw(a.config, out=a.out, base_url=a.base_url,
                                 api_key_env=a.api_key_env, skills=a.skills,
                                 skill_dir=a.skill_dir, voice=a.voice,
                                 voice_realtime_url=a.voice_realtime_url,
                                 voice_model=a.voice_model,
                                 voice_consult_model=a.voice_consult_model,
                                 voice_consult_thinking_level=a.voice_consult_thinking_level,
                                 voice_consult_bootstrap_context_mode=(
                                     a.voice_consult_bootstrap_context_mode
                                 ),
                                 voice_api_key_env=a.voice_api_key_env,
                                 gateway_host=a.gateway_host, gateway_user=a.gateway_user,
                                 gateway_path=a.gateway_path, overwrite=a.overwrite,
                                 restart=a.restart, timeout_seconds=a.timeout_seconds)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
