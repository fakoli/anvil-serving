"""Hugging Face speech-to-speech sidecar helper.

This module keeps the v1 voice integration outside the router hot path.  It
validates a small sidecar manifest and renders the command/container shape that
points Hugging Face `speech-to-speech` at anvil's Chat Completions endpoint.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import tomllib
import urllib.parse


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(
    REPO_ROOT,
    "examples",
    "huggingface-speech-to-speech",
    "openclaw-gateway.example.toml",
)

_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SECRET_PREFIXES = ("sk" + "-", "hf" + "_", "ghp" + "_")
_COMPOSE_SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ConfigError(ValueError):
    """Raised when a voice sidecar manifest is not safe to render."""


def _resolve_config_path(path: str | None) -> str:
    if path:
        return path
    if os.path.isfile(DEFAULT_CONFIG):
        return DEFAULT_CONFIG
    raise ConfigError(
        "no default voice sidecar manifest is available in this installation; pass --config PATH"
    )


def load_manifest(path: str | None = None) -> dict:
    config_path = _resolve_config_path(path)
    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    validate_manifest(data)
    return data


def _section(data: dict, *keys: str) -> dict:
    cur = data
    for key in keys:
        cur = cur.get(key)
        if not isinstance(cur, dict):
            raise ConfigError("missing [%s] section" % ".".join(keys))
    return cur


def _string(table: dict, key: str, default: str | None = None) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError("%s must be a non-empty string" % key)
    return value


def _reject_control_chars(value: str, *, key: str) -> None:
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ConfigError("%s must not contain control characters" % key)


def _validate_container_image(value: str) -> None:
    _reject_control_chars(value, key="voice_sidecar.container_image")
    if any(ch.isspace() for ch in value):
        raise ConfigError("voice_sidecar.container_image must not contain whitespace")


def _validate_service_name(value: str) -> None:
    _reject_control_chars(value, key="service-name")
    if not _COMPOSE_SERVICE_RE.match(value):
        raise ConfigError("service-name must be a Docker Compose service name")


def _bool(table: dict, key: str, default: bool) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError("%s must be true or false" % key)
    return value


def _reject_secret_literals(table: dict) -> None:
    for key, value in table.items():
        if isinstance(value, dict):
            _reject_secret_literals(value)
        elif isinstance(value, str):
            lower_key = key.lower()
            if lower_key in {"api_key", "token", "secret", "password"}:
                raise ConfigError("%s must be referenced by env var name, not stored inline" % key)
            if value.startswith(_SECRET_PREFIXES):
                raise ConfigError("%s looks like a secret literal; use an *_env key" % key)


def _parsed_url(value: str, *, key: str, schemes: tuple[str, ...]) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in schemes or not parsed.netloc:
        raise ConfigError("%s must be a %s URL" % (key, "/".join(schemes)))
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError("%s must not embed credentials; use env vars instead" % key)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname in {("local" + "host"), "0.0.0.0", "::", "::1"}:
        raise ConfigError("%s must use 127.0.0.1 or an explicit LAN/tailnet host" % key)
    return parsed


def _llm_base_url(llm: dict, *, for_container: bool = False) -> str:
    key = "container_base_url" if for_container else "base_url"
    value = llm.get(key) or llm.get("base_url")
    if not isinstance(value, str) or not value:
        raise ConfigError("voice_sidecar.llm_backend.%s must be a non-empty string" % key)
    parsed = _parsed_url(
        value,
        key="voice_sidecar.llm_backend.%s" % key,
        schemes=("http", "https"),
    )
    if parsed.path.rstrip("/") != "/v1":
        raise ConfigError("voice_sidecar.llm_backend.%s must point at /v1" % key)
    if for_container and parsed.hostname == "127.0.0.1":
        raise ConfigError(
            "voice_sidecar.llm_backend.container_base_url must not point at container loopback"
        )
    return value


def validate_manifest(data: dict) -> None:
    """Validate the sidecar manifest without touching the network or Docker."""
    _reject_secret_literals(data)
    sidecar = _section(data, "voice_sidecar")
    llm = _section(data, "voice_sidecar", "llm_backend")
    models = _section(data, "voice_sidecar", "models")

    provider = _string(sidecar, "provider")
    if provider != "huggingface-speech-to-speech":
        raise ConfigError("voice_sidecar.provider must be huggingface-speech-to-speech")

    _string(sidecar, "command", "speech-to-speech")
    _validate_container_image(_string(sidecar, "container_image", "speech-to-speech:local"))

    same_host = _parsed_url(
        _string(sidecar, "same_host_realtime_url"),
        key="voice_sidecar.same_host_realtime_url",
        schemes=("ws", "wss"),
    )
    gateway = _parsed_url(
        _string(sidecar, "gateway_realtime_url"),
        key="voice_sidecar.gateway_realtime_url",
        schemes=("ws", "wss"),
    )
    for key, parsed in (
        ("voice_sidecar.same_host_realtime_url", same_host),
        ("voice_sidecar.gateway_realtime_url", gateway),
    ):
        if parsed.path.rstrip("/") != "/v1/realtime":
            raise ConfigError("%s must point at /v1/realtime" % key)

    backend = _string(llm, "backend")
    if backend != "chat-completions":
        raise ConfigError("voice_sidecar.llm_backend.backend must be chat-completions")
    _llm_base_url(llm)
    if "container_base_url" in llm:
        _llm_base_url(llm, for_container=True)
    _string(llm, "model")
    _bool(llm, "stream", True)
    api_key_env = llm.get("api_key_env")
    if api_key_env is not None:
        if not isinstance(api_key_env, str) or not _ENV_NAME_RE.match(api_key_env):
            raise ConfigError("voice_sidecar.llm_backend.api_key_env must be an env var name")

    _string(models, "stt")
    _string(models, "tts")


def command_args(
    data: dict,
    *,
    include_auth: bool = False,
    for_container: bool = False,
) -> list[str]:
    """Return the host command argv for the sidecar."""
    validate_manifest(data)
    sidecar = _section(data, "voice_sidecar")
    llm = _section(data, "voice_sidecar", "llm_backend")
    models = _section(data, "voice_sidecar", "models")

    argv = [
        _string(sidecar, "command", "speech-to-speech"),
        "--mode",
        "realtime",
        "--stt",
        _string(models, "stt"),
        "--llm_backend",
        "chat-completions",
        "--tts",
        _string(models, "tts"),
        "--model_name",
        _string(llm, "model"),
        "--responses_api_base_url",
        _llm_base_url(llm, for_container=for_container),
    ]
    api_key_env = llm.get("api_key_env")
    if include_auth and api_key_env:
        argv.extend(["--responses_api_api_key", "$" + api_key_env])
    if _bool(llm, "stream", True):
        argv.append("--responses_api_stream")
    argv.append("--enable_live_transcription")
    return argv


def shell_command(argv: list[str]) -> str:
    parts = []
    for arg in argv:
        if arg.startswith("$") and _ENV_NAME_RE.match(arg[1:]):
            parts.append('"%s"' % arg)
        else:
            parts.append(shlex.quote(arg))
    return " ".join(parts)


def compose_service(
    data: dict,
    *,
    service_name: str = "speech-to-speech",
    include_auth: bool = False,
) -> str:
    """Render a minimal Docker Compose service for the sidecar.

    The image is intentionally manifest-provided: upstream installation choices
    vary, and this repo should not pretend a single image is canonical.
    """
    validate_manifest(data)
    _validate_service_name(service_name)
    sidecar = _section(data, "voice_sidecar")
    llm = _section(data, "voice_sidecar", "llm_backend")
    realtime = _parsed_url(
        _string(sidecar, "same_host_realtime_url"),
        key="voice_sidecar.same_host_realtime_url",
        schemes=("ws", "wss"),
    )
    port = realtime.port or 8765
    argv = command_args(data, include_auth=include_auth, for_container=True)
    executable, args = argv[0], argv[1:]

    lines = [
        "services:",
        "  # Replace speech-to-speech:local with the image you build or publish",
        "  # for Hugging Face speech-to-speech before running this compose file.",
        "  %s:" % service_name,
        "    image: %s" % _tomlish_quote(
            _string(sidecar, "container_image", "speech-to-speech:local")
        ),
        "    entrypoint:",
        "      - %s" % _tomlish_quote(executable),
        "    command:",
    ]
    api_key_env = llm.get("api_key_env")
    if include_auth and api_key_env:
        lines.extend([
            "      # Auth expands ${%s} into process argv at runtime." % api_key_env,
            "      # Use only on private hosts where process and Docker metadata are protected.",
        ])
    for arg in args:
        if arg.startswith("$") and _ENV_NAME_RE.match(arg[1:]):
            lines.append('      - "${%s}"' % arg[1:])
        else:
            lines.append("      - %s" % _tomlish_quote(arg))
    lines.extend([
        "    ports:",
        '      - "127.0.0.1:%d:%d"' % (port, port),
    ])
    if include_auth and api_key_env:
        lines.extend([
            "    environment:",
            '      %s: "${%s}"' % (api_key_env, api_key_env),
        ])
    container_base = llm.get("container_base_url", "")
    parsed_container_base = (
        urllib.parse.urlparse(container_base) if isinstance(container_base, str) else None
    )
    if parsed_container_base and parsed_container_base.hostname == "host.docker.internal":
        lines.extend([
            "    extra_hosts:",
            '      - "host.docker.internal:host-gateway"',
        ])
    return "\n".join(lines) + "\n"


def _tomlish_quote(value: str) -> str:
    return json.dumps(value)


def _load_for_cli(path: str | None) -> tuple[dict, str]:
    config_path = _resolve_config_path(path)
    try:
        return load_manifest(config_path), config_path
    except FileNotFoundError:
        raise ConfigError("config not found: %s" % config_path)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("cannot parse %s: %s" % (config_path, exc))


def build_parser(prog: str = "anvil-serving voice-sidecar") -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Validate and render the Hugging Face speech-to-speech sidecar command "
            "that uses anvil as a Chat Completions backend."
        ),
    )
    sub = p.add_subparsers(dest="action", required=True)

    def add_config(sp):
        sp.add_argument(
            "--config",
            help="sidecar manifest TOML; defaults to the source-checkout example when present",
        )

    sp = sub.add_parser("validate", help="validate the sidecar manifest")
    add_config(sp)
    sp.add_argument("--json", action="store_true", help="emit JSON")

    sp = sub.add_parser("command", help="render the host speech-to-speech command")
    add_config(sp)
    sp.add_argument(
        "--with-auth",
        action="store_true",
        help="include the router token argument; it expands into process argv at runtime",
    )
    sp.add_argument("--json", action="store_true", help="emit argv JSON instead of a shell command")

    sp = sub.add_parser("compose", help="render a Docker Compose service skeleton")
    add_config(sp)
    sp.add_argument("--service-name", default="speech-to-speech")
    sp.add_argument(
        "--with-auth",
        action="store_true",
        help="include the router token argument; it expands into process argv at runtime",
    )

    return p


def main(argv=None, *, prog: str = "anvil-serving voice-sidecar") -> int:
    args = build_parser(prog=prog).parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        data, config_path = _load_for_cli(args.config)
        if args.action == "validate":
            if args.json:
                print(json.dumps({"ok": True, "config": config_path}, sort_keys=True))
            else:
                print("OK: %s" % config_path)
            return 0
        if args.action == "command":
            rendered = command_args(data, include_auth=args.with_auth)
            if args.json:
                print(json.dumps({"argv": rendered}, indent=2))
            else:
                print(shell_command(rendered))
            return 0
        if args.action == "compose":
            print(
                compose_service(data, service_name=args.service_name, include_auth=args.with_auth),
                end="",
            )
            return 0
    except ConfigError as exc:
        print("voice-sidecar: %s" % exc, file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
