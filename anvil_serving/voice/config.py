"""Voice-pipeline manifest loader + hygiene validation (anvil task T002).

The voice orchestrator (a later unit: STT/TTS stages, LLM stage, realtime
server) is configured by a small TOML manifest — ``examples/voice/voice.example.toml``
is the reference shape. This module loads and VALIDATES that manifest with the
same hygiene anvil applies everywhere else in the repo:

* ``127.0.0.1``, never ``localhost`` — the Windows IPv6 stall gotcha (see
  root ``CLAUDE.md`` gotcha #1). Any URL pointing at this machine must spell
  the loopback address exactly as ``127.0.0.1``; ``localhost``, other
  ``127.x.x.x`` addresses, ``0.0.0.0``, and ``::1`` are all rejected.
* Secrets are referenced by ENV-VAR NAME ONLY, never as literals. A manifest
  key named ``api_key``/``token``/``secret``/``password`` (or any dotted/
  nested path ending in one of those) is rejected outright — use the
  ``*_env`` sibling (e.g. ``api_key_env = "ANVIL_ROUTER_TOKEN"``) instead.
  Any string value anywhere in the manifest that *looks* like a live secret
  (``sk-``, ``hf_``/``hf-``, ``ghp_``/``ghp-`` prefixes) is rejected too, as
  defense in depth against someone pasting a real key into an ``*_env`` field
  by mistake.
* URL-embedded credentials (``https://user:pass@host/...``) are rejected.

A fresh, from-scratch implementation of anvil's own voice-pipeline manifest
schema — see ``docs/findings/2026-07-04-hf-speech-to-speech-review.md`` for
why we build our own stdlib orchestrator instead of wrapping the HF image.

Stdlib-only: ``tomllib``, ``re``, ``urllib.parse``, ``os``.
"""
from __future__ import annotations

import copy
import os
import re
import shlex
import urllib.parse
from dataclasses import dataclass
from typing import Mapping

from anvil_serving.paths import config_path
from anvil_serving.targets import (
    CommandSpec,
    ExecutionPlan,
    ExecutionPreflight,
    finalize_execution_plan,
    preflight_execution_plan,
)
from anvil_serving.topology import CommandIdentity, Resource, Topology

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - guarded by requires-python >=3.11
    tomllib = None

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CONFIG = os.path.join(REPO_ROOT, "examples", "voice", "voice.example.toml")
CONFIG_HOME_CONFIG = "~/.anvil-serving/voice.toml"
MAX_MANIFEST_BYTES = 1024 * 1024

_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
# Q2 hardening: `realtime_token` is listed explicitly (not just `token`) --
# `_reject_secret_literals` below matches on the LITERAL key name, not a
# substring/suffix, so `voice.realtime_token = "..."` would otherwise sail
# through unrejected even though `token` is already in this set.
_SECRET_KEY_NAMES = {"api_key", "token", "secret", "password", "realtime_token"}
_SECRET_VALUE_PREFIXES = ("sk-", "hf_", "hf-", "ghp_", "ghp-")
_SECRET_VALUE_RE = re.compile(r"\b(sk-(?:proj-)?[A-Za-z0-9_-]{8,}|hf[_-][A-Za-z0-9_-]{8,}|ghp[_-][A-Za-z0-9_-]{8,})\b")
_LIFECYCLES = {"managed", "external", "native"}
_STT_RESPONSE_FORMATS = {"json"}
_TTS_RESPONSE_FORMATS = {"pcm"}
_TTS_PROTOCOLS = {"openai", "cartesia", "gepard"}
_NATIVE_COMMAND_KEYS = ("start_command", "stop_command")
_NATIVE_PATH_KEYS = ("workdir", "pid_file", "log_file")


class ConfigError(ValueError):
    """Raised when a voice manifest is missing, malformed, or unsafe to use."""


@dataclass(frozen=True)
class ResolvedVoiceConfig:
    """A validated voice manifest plus its resolved benchmark identity."""

    data: dict
    profile: str | None
    candidate: str | None
    llm_base_url: str
    llm_model: str
    stt_base_url: str
    stt_model: str
    tts_base_url: str
    tts_model: str

    def identity(self) -> dict[str, str | None]:
        return {
            "profile": self.profile,
            "candidate": self.candidate,
            "llm_base_url": self.llm_base_url,
            "llm_model": self.llm_model,
            "stt_base_url": self.stt_base_url,
            "stt_model": self.stt_model,
            "tts_base_url": self.tts_base_url,
            "tts_model": self.tts_model,
        }


@dataclass(frozen=True)
class ResolvedAudioEndpoint:
    """One topology-owned audio model endpoint and its execution context."""

    kind: str
    plan: ExecutionPlan

    @property
    def base_url(self) -> str:
        endpoint = self.plan.resource_endpoint
        if endpoint is None:
            raise ConfigError(f"topology resource for {self.kind} has no endpoint")
        return endpoint

    @property
    def endpoint_kind(self) -> str | None:
        resource = self.plan.resource
        return resource.endpoint_kind if resource else None

    def as_dict(self) -> dict[str, object]:
        """Return stable endpoint identity without flattening host-relative loopback."""
        context = self.plan.as_dict()
        context.update(
            {
                "audio_kind": self.kind,
                "base_url": self.base_url,
                "endpoint_kind": self.endpoint_kind,
            }
        )
        return context


@dataclass(frozen=True)
class ResolvedAudioTargets:
    """Topology-resolved STT/TTS model owners for voice audio operations."""

    stt: ResolvedAudioEndpoint
    tts: ResolvedAudioEndpoint

    def as_dict(self) -> dict[str, dict[str, object]]:
        return {"stt": self.stt.as_dict(), "tts": self.tts.as_dict()}

    @property
    def warnings(self) -> tuple[str, ...]:
        return self.stt.plan.warnings + self.tts.plan.warnings


@dataclass(frozen=True)
class ResolvedProxyTargets:
    """Mini-owned Realtime proxy plus its host-relative audio forwarders."""

    proxy: ExecutionPlan
    stt_proxy: Resource
    tts_proxy: Resource
    stt_model: Resource
    tts_model: Resource
    stt_target_host: str | None
    tts_target_host: str | None

    @property
    def endpoint(self) -> str:
        endpoint = self.proxy.resource_endpoint
        if endpoint is None:
            raise ConfigError("topology realtime proxy resource has no endpoint")
        return endpoint

    def as_dict(self) -> dict[str, object]:
        context = self.proxy.as_dict()
        context["proxy_endpoint"] = self.endpoint
        context["audio_proxy_endpoints"] = {
            "stt": self.stt_proxy.endpoint,
            "tts": self.tts_proxy.endpoint,
        }
        context["audio_model_owners"] = {
            "stt": self.stt_model.host,
            "tts": self.tts_model.host,
        }
        return context


def resolve_proxy_targets(
    topology: Topology,
    *,
    operation: str,
    target: str | None = None,
    transport: str = "auto",
    command_identity: CommandIdentity | None = None,
    command_host: str | None = None,
    command_runtime: str | None = None,
    environment: Mapping[str, str] | None = None,
    overlay: str | None = None,
) -> ResolvedProxyTargets:
    """Resolve the Mini proxy owner without classifying forwarders as models."""
    preflight = preflight_execution_plan(
        topology,
        CommandSpec(
            name=operation,
            resource_role="realtime-proxy",
            supported_transports=("local", "controller"),
            execution_runtime_roles=("native",),
            mutation_class="service",
            recovery_capable=False,
            gpu_role_required=False,
        ),
        target=target,
        command_identity=command_identity,
        command_host=command_host,
        command_runtime=command_runtime,
        environment=environment,
        overlay=overlay,
    )
    plan = finalize_execution_plan(topology, preflight, transport=transport)
    proxies = {
        kind: topology.resource_owner("%s-proxy" % kind)
        for kind in ("stt", "tts")
    }
    for kind, resource in proxies.items():
        if resource.host != plan.resource_host.id or resource.runtime != plan.resource_runtime.id:
            raise ConfigError(
                "%s proxy must be co-owned with realtime proxy on %s/%s"
                % (kind, plan.resource_host.id, plan.resource_runtime.id)
            )
        if resource.endpoint is None or resource.endpoint_kind != "host-relative-loopback":
            raise ConfigError("%s proxy must declare a host-relative loopback endpoint" % kind)
    models = {
        kind: topology.resource_owner("%s-serve" % kind)
        for kind in ("stt", "tts")
    }
    for kind, resource in models.items():
        if resource.endpoint is None:
            raise ConfigError("%s model resource must declare an endpoint" % kind)
    return ResolvedProxyTargets(
        plan,
        proxies["stt"],
        proxies["tts"],
        models["stt"],
        models["tts"],
        topology.host(models["stt"].host).address,
        topology.host(models["tts"].host).address,
    )


def resolve_audio_targets(
    topology: Topology,
    *,
    operation: str = "voice-status",
    target: str | None = None,
    transport: str = "auto",
    command_identity: CommandIdentity | None = None,
    command_host: str | None = None,
    command_runtime: str | None = None,
    environment: Mapping[str, str] | None = None,
    overlay: str | None = None,
    experimental_model_workload: bool = False,
) -> ResolvedAudioTargets:
    """Resolve STT/TTS model owners without dispatching a CLI or transport.

    Proxy resources are intentionally ineligible: audio lifecycle targets the
    ``stt-serve`` and ``tts-serve`` roles only. A loopback URL is returned
    verbatim and remains relative to the resolved resource/execution host.
    """
    common = {
        "target": target,
        "command_identity": command_identity,
        "command_host": command_host,
        "command_runtime": command_runtime,
        "environment": environment,
        "overlay": overlay,
        "experimental_model_workload": experimental_model_workload,
    }

    def preflight(kind: str) -> ExecutionPreflight:
        result = preflight_execution_plan(
            topology,
            CommandSpec(
                name=operation,
                resource_role=f"{kind}-serve",
                supported_transports=("local", "controller"),
                execution_runtime_roles=("native", "docker"),
                mutation_class="service",
                recovery_capable=False,
                gpu_role_required=False,
            ),
            **common,
        )
        if result.resource.endpoint is None:
            raise ConfigError(f"topology resource for {kind} has no endpoint")
        return result

    preflights = {kind: preflight(kind) for kind in ("stt", "tts")}

    def resolve(kind: str) -> ResolvedAudioEndpoint:
        plan = finalize_execution_plan(topology, preflights[kind], transport=transport)
        return ResolvedAudioEndpoint(kind, plan)

    return ResolvedAudioTargets(stt=resolve("stt"), tts=resolve("tts"))


def _resolve_config_path(path: str | None) -> str:
    if path:
        return path
    operator_config = config_path("voice.toml")
    if os.path.isfile(operator_config):
        return operator_config
    if os.path.isfile(DEFAULT_CONFIG):
        return DEFAULT_CONFIG
    raise ConfigError(
        "no default voice manifest is available in ~/.anvil-serving/voice.toml "
        "or this installation; pass an explicit path"
    )


def resolve_config_path(path: str | None = None) -> str:
    return _resolve_config_path(path)


def load_raw_manifest(path: str | None = None) -> dict:
    """Load the voice manifest TOML without applying profiles or validation."""
    if tomllib is None:  # pragma: no cover - guarded by requires-python >=3.11
        raise ConfigError("tomllib unavailable (need Python >= 3.11)")
    config_path = _resolve_config_path(path)
    try:
        with open(config_path, "rb") as f:
            payload = f.read(MAX_MANIFEST_BYTES + 1)
        if len(payload) > MAX_MANIFEST_BYTES:
            raise ConfigError(
                "voice manifest exceeds %d bytes: %s"
                % (MAX_MANIFEST_BYTES, config_path)
            )
        data = tomllib.loads(payload.decode("utf-8"))
    except FileNotFoundError:
        raise ConfigError("config not found: %s" % config_path)
    except UnicodeDecodeError as exc:
        raise ConfigError("voice manifest is not valid UTF-8: %s" % config_path) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("cannot parse %s: %s" % (config_path, exc))
    if isinstance(data, dict):
        data["_manifest_path"] = os.path.abspath(config_path)
        data["_manifest_dir"] = os.path.dirname(os.path.abspath(config_path))
    return data


def load_manifest(
    path: str | None = None,
    *,
    profile: str | None = None,
    candidate_overlay: dict | None = None,
    candidate: str | None = None,
) -> dict:
    """Load and validate the voice manifest TOML at `path` (or the shipped example).

    When `profile` is provided, `[voice.profiles.<name>]` is applied as an
    operator-selected overlay before validation. Profiles are for topology
    switching, such as keeping Realtime on a gateway host while selecting Mini
    or Dark STT/TTS endpoints through the same anvil-serving utility command.
    """
    return resolve_manifest(
        path,
        profile=profile,
        candidate_overlay=candidate_overlay,
        candidate=candidate,
    ).data


def resolve_manifest(
    path: str | None = None,
    *,
    profile: str | None = None,
    candidate_overlay: dict | None = None,
    candidate: str | None = None,
) -> ResolvedVoiceConfig:
    """Load, apply profile/candidate overlays, validate, and summarize a manifest."""
    data = load_raw_manifest(path)
    return resolve_manifest_data(
        data,
        profile=profile,
        candidate_overlay=candidate_overlay,
        candidate=candidate,
    )


def resolve_manifest_data(
    data: dict,
    *,
    profile: str | None = None,
    candidate_overlay: dict | None = None,
    candidate: str | None = None,
) -> ResolvedVoiceConfig:
    """Resolve an already-loaded manifest into one concrete voice config.

    Candidate overlays are deliberately in-memory only. They use the same shape
    as a profile overlay, merge after the selected profile, and never mutate the
    caller's manifest dictionary or on-disk production config.
    """
    if not isinstance(data, dict):
        raise ConfigError("manifest must be a TOML table")
    if profile or candidate_overlay is not None:
        _reject_secret_literals(data)
    if profile:
        data = apply_profile(data, profile)
    else:
        data = copy.deepcopy(data)
    if candidate_overlay is not None:
        _reject_secret_literals(candidate_overlay)
        data = apply_candidate_overlay(data, candidate_overlay, name=candidate)
    identity = _resolved_identity(data, profile=profile, candidate=candidate)
    validate_manifest(data)
    return ResolvedVoiceConfig(data=data, **identity)


def profile_names(data: dict) -> list[str]:
    """Return sorted profile names declared under `[voice.profiles]`."""
    voice = data.get("voice") if isinstance(data, dict) else None
    profiles = voice.get("profiles", {}) if isinstance(voice, dict) else {}
    if profiles is None:
        return []
    if not isinstance(profiles, dict):
        raise ConfigError("voice.profiles must be a TOML table")
    for name, value in profiles.items():
        if not isinstance(value, dict):
            raise ConfigError("voice.profiles.%s must be a TOML table" % name)
    return sorted(str(name) for name in profiles)


def apply_profile(data: dict, profile: str) -> dict:
    """Return a copy of `data` with the named voice profile applied.

    Profile overlays live under `[voice.profiles.<name>]`. Top-level keys in
    that profile merge into `[voice]`; nested `llm`, `stt`, and `tts` tables
    merge into their active endpoint sections. If a profile changes an audio
    endpoint away from `lifecycle = "native"`, inherited native process keys
    are stripped so a Mini-native base profile can safely switch to Dark-host
    external audio endpoints.
    """
    if not isinstance(profile, str) or not profile:
        raise ConfigError("profile must be a non-empty string")
    if not isinstance(data, dict):
        raise ConfigError("manifest must be a TOML table")
    voice = data.get("voice")
    if not isinstance(voice, dict):
        raise ConfigError("missing [voice] section")
    profiles = voice.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ConfigError("voice.profiles must be a TOML table")
    overlay = profiles.get(profile)
    if overlay is None:
        names = ", ".join(profile_names(data)) or "(none)"
        raise ConfigError("unknown voice profile %s; available profiles: %s" % (profile, names))
    if not isinstance(overlay, dict):
        raise ConfigError("voice.profiles.%s must be a TOML table" % profile)

    return _apply_voice_overlay(
        data,
        overlay,
        label="voice.profiles.%s" % profile,
        strip_profiles=True,
    )


def apply_candidate_overlay(
    data: dict,
    overlay: dict,
    *,
    name: str | None = None,
) -> dict:
    """Return a copy of `data` with an in-memory candidate overlay applied."""
    if not isinstance(overlay, dict):
        raise ConfigError("candidate overlay must be a TOML table")
    label = "candidate overlay %s" % name if name else "candidate overlay"
    return _apply_voice_overlay(data, overlay, label=label, strip_profiles=True)


def _apply_voice_overlay(
    data: dict,
    overlay: dict,
    *,
    label: str,
    strip_profiles: bool,
) -> dict:
    if not isinstance(data, dict):
        raise ConfigError("manifest must be a TOML table")
    voice = data.get("voice")
    if not isinstance(voice, dict):
        raise ConfigError("missing [voice] section")
    overlay = {
        key: value
        for key, value in overlay.items()
        if not str(key).startswith("_")
    }
    if "voice" in overlay:
        if len(overlay) != 1 or not isinstance(overlay["voice"], dict):
            raise ConfigError("%s must contain either voice keys or a single [voice] table" % label)
        overlay = overlay["voice"]
    if not isinstance(overlay, dict):
        raise ConfigError("%s must be a TOML table" % label)

    resolved = copy.deepcopy(data)
    resolved_voice = resolved["voice"]
    if strip_profiles:
        resolved_voice.pop("profiles", None)
    for key, value in overlay.items():
        if key == "profiles":
            raise ConfigError("%s must not contain nested profiles" % label)
        if isinstance(value, dict):
            base = resolved_voice.get(key)
            if isinstance(base, dict):
                merged = copy.deepcopy(base)
                _merge_table(merged, value)
                if key in ("stt", "tts") and value.get("lifecycle", merged.get("lifecycle")) != "native":
                    for native_key in (*_NATIVE_COMMAND_KEYS, *_NATIVE_PATH_KEYS):
                        merged.pop(native_key, None)
                resolved_voice[key] = merged
            else:
                resolved_voice[key] = copy.deepcopy(value)
        else:
            resolved_voice[key] = copy.deepcopy(value)
    return resolved


def _resolved_string(data: dict, section: str, key: str) -> str:
    table = _section(data, "voice", section)
    value = table.get(key)
    path = "voice.%s.%s" % (section, key)
    if not isinstance(value, str) or not value:
        raise ConfigError("resolved voice config missing %s" % path)
    return value


def _resolved_identity(
    data: dict,
    *,
    profile: str | None,
    candidate: str | None,
) -> dict[str, object]:
    return {
        "profile": profile,
        "candidate": candidate,
        "llm_base_url": _resolved_string(data, "llm", "base_url"),
        "llm_model": _resolved_string(data, "llm", "model"),
        "stt_base_url": _resolved_string(data, "stt", "base_url"),
        "stt_model": _resolved_string(data, "stt", "model"),
        "tts_base_url": _resolved_string(data, "tts", "base_url"),
        "tts_model": _resolved_string(data, "tts", "model"),
    }


def _merge_table(base: dict, overlay: dict) -> None:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_table(base[key], value)
        else:
            base[key] = copy.deepcopy(value)


def _section(data: dict, *keys: str) -> dict:
    cur = data
    for key in keys:
        cur = cur.get(key) if isinstance(cur, dict) else None
        if not isinstance(cur, dict):
            raise ConfigError("missing [%s] section" % ".".join(keys))
    return cur


def _string(table: dict, key: str, default: str | None = None) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError("%s must be a non-empty string" % key)
    return value


def _bool(table: dict, key: str, default: bool) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError("%s must be true or false" % key)
    return value


def _int(table: dict, key: str, default: int | None = None) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("%s must be an integer" % key)
    return value


def _float(table: dict, key: str, default: float | None = None) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError("%s must be a number" % key)
    return float(value)


def _positive_int(table: dict, key: str) -> int:
    value = _int(table, key)
    if value <= 0:
        raise ConfigError("%s must be positive" % key)
    return value


def _nonnegative_int(table: dict, key: str) -> int:
    value = _int(table, key)
    if value < 0:
        raise ConfigError("%s must be nonnegative" % key)
    return value


def _positive_float(table: dict, key: str) -> float:
    value = _float(table, key)
    if value <= 0:
        raise ConfigError("%s must be positive" % key)
    return value


def _nonnegative_float(table: dict, key: str) -> float:
    value = _float(table, key)
    if value < 0:
        raise ConfigError("%s must be nonnegative" % key)
    return value


def _reject_secret_literals(node, path: str = "") -> None:
    """Walk the manifest and reject secret literals wherever they'd hide.

    Rejects: (a) any key literally named api_key/token/secret/password holding
    a string value (the ``*_env`` sibling must be used instead), and (b) any
    string value anywhere that starts with a known live-secret prefix, even
    under an innocuous-looking key.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            key_path = "%s.%s" % (path, key) if path else str(key)
            if isinstance(value, str):
                if key.lower() in _SECRET_KEY_NAMES:
                    raise ConfigError(
                        "%s must be referenced by env var name (use %s_env), not stored inline"
                        % (key_path, key)
                    )
                if value.startswith(_SECRET_VALUE_PREFIXES) or _SECRET_VALUE_RE.search(value):
                    raise ConfigError(
                        "%s looks like a secret literal; reference it by an *_env key instead"
                        % key_path
                    )
            else:
                _reject_secret_literals(value, key_path)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _reject_secret_literals(item, "%s[%d]" % (path, i))


def _validate_host(host: str, *, key: str) -> None:
    """Reject the bad loopback spellings CLAUDE.md gotcha #1 warns about.

    Shared by :func:`_parsed_url` (a URL's hostname) and
    ``validate_manifest``'s own ``voice.realtime_host`` check (a bare host,
    no URL) -- a legitimate non-loopback host (LAN/tailnet, e.g. STT/TTS
    living on another box) is NOT rejected here; only ``localhost`` and the
    non-canonical loopback spellings are.
    """
    h = host.lower()
    if h == "localhost":
        raise ConfigError(
            "%s must use 127.0.0.1, not localhost (Windows IPv6 stall — see CLAUDE.md gotcha #1)"
            % key
        )
    if h in ("0.0.0.0", "::1") or (h.startswith("127.") and h != "127.0.0.1"):
        raise ConfigError("%s must use exactly 127.0.0.1 for a same-host address, not %s" % (key, h))


def _parsed_url(value: str, *, key: str, schemes: tuple[str, ...]) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in schemes or not parsed.netloc:
        raise ConfigError("%s must be a %s URL" % (key, "/".join(schemes)))
    if parsed.username or parsed.password:
        raise ConfigError("%s must not embed credentials in the URL" % key)
    if parsed.query or parsed.fragment:
        raise ConfigError("%s must not contain query strings or fragments; use *_env for credentials" % key)
    _validate_host(parsed.hostname or "", key=key)
    return parsed


def _split_command(value: str) -> list[str]:
    argv = shlex.split(value, posix=(os.name != "nt"))
    if os.name == "nt":
        argv = [
            part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in ("'", '"') else part
            for part in argv
        ]
    return argv


def _check_env_name(table: dict, key: str) -> str | None:
    """Validate an optional `<key>_env` field names a plausible env var; return it (or None)."""
    env_key = "%s_env" % key
    value = table.get(env_key)
    if value is None:
        return None
    if not isinstance(value, str) or not _ENV_NAME_RE.match(value):
        raise ConfigError("%s must be an ENV_VAR_NAME (uppercase, digits, underscore)" % env_key)
    return value


def resolve_secret(table: dict, key: str, *, required: bool = False) -> str | None:
    """Resolve `<key>_env`'s named environment variable to its value.

    Never accepts a literal secret in the manifest itself — only a variable
    NAME, which is then looked up in `os.environ` at call time. Returns None
    when the manifest doesn't reference a secret for this key and it isn't
    `required`; raises `ConfigError` if it's required but unset/unnamed.
    """
    env_name = _check_env_name(table, key)
    if env_name is None:
        if required:
            raise ConfigError("%s_env is required (name the env var holding the secret)" % key)
        return None
    value = os.environ.get(env_name)
    if value is None:
        raise ConfigError("%s_env names %s, which is not set in the environment" % (key, env_name))
    return value


def _validate_endpoint(data: dict, name: str, *, model_required: bool = True) -> None:
    table = _section(data, "voice", name)
    parsed = _parsed_url(_string(table, "base_url"), key="voice.%s.base_url" % name, schemes=("http", "https"))
    if model_required:
        _string(table, "model")
    _check_env_name(table, "api_key")
    lifecycle = table.get("lifecycle", "managed")
    if lifecycle not in _LIFECYCLES:
        raise ConfigError(
            "voice.%s.lifecycle must be one of %s" % (name, ", ".join(sorted(_LIFECYCLES)))
        )
    _validate_native_lifecycle(table, name, lifecycle, parsed)
    if "timeout" in table:
        _positive_float(table, "timeout")
    if "ready_timeout" in table:
        _positive_float(table, "ready_timeout")
    if "ready_url" in table:
        _parsed_url(
            _string(table, "ready_url"),
            key="voice.%s.ready_url" % name,
            schemes=("http", "https"),
        )
    if "stop_timeout" in table:
        _positive_float(table, "stop_timeout")
    for key in ("serve_name", "manifest_path", "serves_manifest"):
        if key in table:
            _string(table, key)
    if name == "stt":
        if "stream" in table:
            _bool(table, "stream", True)
        if "response_format" in table:
            response_format = _string(table, "response_format")
            if response_format not in _STT_RESPONSE_FORMATS:
                raise ConfigError(
                    "voice.stt.response_format must be json because the non-streaming STT client consumes JSON"
                )
    if name == "tts":
        if "protocol" in table:
            protocol = _string(table, "protocol")
            if protocol not in _TTS_PROTOCOLS:
                raise ConfigError(
                    "voice.tts.protocol must be one of %s" % ", ".join(sorted(_TTS_PROTOCOLS))
                )
        if "response_format" in table:
            response_format = _string(table, "response_format")
            if response_format not in _TTS_RESPONSE_FORMATS:
                raise ConfigError(
                    "voice.tts.response_format must be pcm because the voice pipeline consumes raw PCM"
                )
        for key in ("source_sample_rate", "target_sample_rate", "chunk_bytes"):
            if key in table:
                _positive_int(table, key)
        for key in ("voice_id", "language"):
            if key in table:
                _string(table, key)


def _validate_native_lifecycle(table: dict, name: str, lifecycle: str, parsed_base_url: urllib.parse.ParseResult) -> None:
    has_native_keys = any(key in table for key in (*_NATIVE_COMMAND_KEYS, *_NATIVE_PATH_KEYS))
    if lifecycle != "native":
        if has_native_keys:
            raise ConfigError(
                "voice.%s native process keys require lifecycle = \"native\"" % name
            )
        return

    start = _string(table, "start_command")
    if parsed_base_url.hostname != "127.0.0.1":
        raise ConfigError(
            "voice.%s.lifecycle = \"native\" requires a same-host base_url on 127.0.0.1; use lifecycle = \"external\" for remote endpoints"
            % name
        )
    try:
        _split_command(start)
    except ValueError as exc:
        raise ConfigError("voice.%s.start_command is not a valid argv string: %s" % (name, exc))
    if not _split_command(start):
        raise ConfigError("voice.%s.start_command must not be empty" % name)
    if "stop_command" in table:
        stop = _string(table, "stop_command")
        try:
            _split_command(stop)
        except ValueError as exc:
            raise ConfigError("voice.%s.stop_command is not a valid argv string: %s" % (name, exc))
        if not _split_command(stop):
            raise ConfigError("voice.%s.stop_command must not be empty" % name)
    for key in _NATIVE_PATH_KEYS:
        if key in table:
            _string(table, key)


def validate_manifest(data: dict) -> None:
    """Validate the voice manifest without touching the network or a filesystem serve."""
    if not isinstance(data, dict):
        raise ConfigError("manifest must be a TOML table")
    _reject_secret_literals(data)

    voice = _section(data, "voice")
    _string(voice, "name", "anvil-voice")
    realtime_host = _string(voice, "realtime_host", "127.0.0.1")
    _validate_host(realtime_host, key="voice.realtime_host")
    _int(voice, "realtime_port", 8765)
    # Optional: names the env var holding a bearer token the realtime WS
    # server requires (anvil_serving.voice.realtime.ws's F2 gate). Loopback
    # binds work with no token configured (trusted-local default); a
    # non-loopback realtime_host is refused at server-construction time
    # (make_ws_server) unless this is set -- validated here only for shape
    # (a plausible ENV_VAR_NAME), never resolved against os.environ at
    # manifest-validation time.
    realtime_token_env = _check_env_name(voice, "realtime_token")
    # U2-a: defense in depth. `make_ws_server`'s own F2 guard already refuses
    # to BIND a non-loopback host with no token at server-construction time,
    # but that only protects a process that actually gets as far as calling
    # `make_ws_server` (e.g. `anvil-serving voice proxy run`) -- a manifest with a
    # non-loopback `realtime_host` and no `realtime_token_env` would still
    # pass `load_manifest`/`validate_manifest` cleanly, describe as "OK", and
    # only fail much later (or in a caller that never reaches make_ws_server
    # at all). Reject the unsafe combination here, at the manifest boundary,
    # so it can never be validated as OK in the first place.
    if realtime_host != "127.0.0.1" and not realtime_token_env:
        raise ConfigError(
            "voice.realtime_host is non-loopback (%s); voice.realtime_token_env must "
            "name the env var holding a bearer token (the realtime WS server refuses an "
            "unauthenticated non-loopback bind -- see realtime/ws.py's F2 guard), or use "
            "127.0.0.1" % realtime_host
        )
    _int(voice, "pool_size", 4)

    llm = _section(data, "voice", "llm")
    _parsed_url(_string(llm, "base_url"), key="voice.llm.base_url", schemes=("http", "https"))
    _string(llm, "model")
    _bool(llm, "stream", True)
    _check_env_name(llm, "api_key")
    if "system_prompt" in llm:
        _string(llm, "system_prompt")
    if "timeout" in llm:
        _positive_float(llm, "timeout")
    if "max_tokens" in llm:
        _positive_int(llm, "max_tokens")
    if "temperature" in llm:
        _nonnegative_float(llm, "temperature")
    if "history_max_turns" in llm:
        _nonnegative_int(llm, "history_max_turns")
    if "history_max_message_chars" in llm:
        _positive_int(llm, "history_max_message_chars")
    if "tool_result_timeout" in llm:
        _positive_float(llm, "tool_result_timeout")
    if "tool_call_max_rounds" in llm:
        _nonnegative_int(llm, "tool_call_max_rounds")
    if "tool_result_max_chars" in llm:
        _positive_int(llm, "tool_result_max_chars")
    if "speech_chunk_max_chars" in llm:
        _positive_int(llm, "speech_chunk_max_chars")

    _validate_endpoint(data, "stt")
    _validate_endpoint(data, "tts")


def describe(data: dict) -> str:
    """One-line, secret-free summary of a validated manifest (for CLI output)."""
    voice = data.get("voice", {})
    llm = voice.get("llm", {})
    stt = voice.get("stt", {})
    tts = voice.get("tts", {})
    return (
        "%s realtime=%s:%s llm=%s(%s) stt=%s(%s) tts=%s(%s)"
        % (
            voice.get("name", "anvil-voice"),
            voice.get("realtime_host", "127.0.0.1"),
            voice.get("realtime_port", 8765),
            llm.get("base_url", "?"), llm.get("model", "?"),
            stt.get("base_url", "?"), stt.get("model", "?"),
            tts.get("base_url", "?"), tts.get("model", "?"),
        )
    )
