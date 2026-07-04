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


class ConfigError(ValueError):
    """Raised when a voice sidecar manifest is not safe to render."""


def load_manifest(path: str = DEFAULT_CONFIG) -> dict:
    with open(path, "rb") as f:
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
            if value.startswith(("sk-", "hf_", "ghp_")):
                raise ConfigError("%s looks like a secret literal; use an *_env key" % key)


def _parsed_url(value: str, *, key: str, schemes: tuple[str, ...]) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in schemes or not parsed.netloc:
        raise ConfigError("%s must be a %s URL" % (key, "/".join(schemes)))
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError("%s must not embed credentials; use env vars instead" % key)
    if parsed.hostname == ("local" + "host"):
        raise ConfigError("%s must use 127.0.0.1 or an explicit LAN/tailnet host" % key)
    return parsed


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
    _string(sidecar, "container_image", "speech-to-speech:local")

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
    base_url = _parsed_url(
        _string(llm, "base_url"),
        key="voice_sidecar.llm_backend.base_url",
        schemes=("http", "https"),
    )
    if base_url.path.rstrip("/") != "/v1":
        raise ConfigError("voice_sidecar.llm_backend.base_url must point at /v1")
    _string(llm, "model")
    _bool(llm, "stream", True)
    api_key_env = llm.get("api_key_env")
    if api_key_env is not None:
        if not isinstance(api_key_env, str) or not _ENV_NAME_RE.match(api_key_env):
            raise ConfigError("voice_sidecar.llm_backend.api_key_env must be an env var name")

    _string(models, "stt")
    _string(models, "tts")


def command_args(data: dict, *, include_auth: bool = True) -> list[str]:
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
        _string(llm, "base_url"),
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


def compose_service(data: dict, *, service_name: str = "speech-to-speech") -> str:
    """Render a minimal Docker Compose service for the sidecar.

    The image is intentionally manifest-provided: upstream installation choices
    vary, and this repo should not pretend a single image is canonical.
    """
    validate_manifest(data)
    sidecar = _section(data, "voice_sidecar")
    llm = _section(data, "voice_sidecar", "llm_backend")
    realtime = _parsed_url(
        _string(sidecar, "same_host_realtime_url"),
        key="voice_sidecar.same_host_realtime_url",
        schemes=("ws", "wss"),
    )
    port = realtime.port or 8765
    argv = command_args(data, include_auth=bool(llm.get("api_key_env")))
    executable, args = argv[0], argv[1:]

    lines = [
        "services:",
        "  # Replace speech-to-speech:local with the image you build or publish",
        "  # for Hugging Face speech-to-speech before running this compose file.",
        "  %s:" % service_name,
        "    image: %s" % _string(sidecar, "container_image", "speech-to-speech:local"),
        "    entrypoint:",
        "      - %s" % _tomlish_quote(executable),
        "    command:",
    ]
    for arg in args:
        if arg.startswith("$") and _ENV_NAME_RE.match(arg[1:]):
            lines.append('      - "${%s}"' % arg[1:])
        else:
            lines.append("      - %s" % _tomlish_quote(arg))
    lines.extend([
        "    ports:",
        '      - "127.0.0.1:%d:%d"' % (port, port),
    ])
    api_key_env = llm.get("api_key_env")
    if api_key_env:
        lines.extend([
            "    environment:",
            '      %s: "${%s}"' % (api_key_env, api_key_env),
        ])
    return "\n".join(lines) + "\n"


def _tomlish_quote(value: str) -> str:
    return json.dumps(value)


def _load_for_cli(path: str) -> dict:
    try:
        return load_manifest(path)
    except FileNotFoundError:
        raise ConfigError("config not found: %s" % path)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("cannot parse %s: %s" % (path, exc))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="anvil-serving voice-sidecar",
        description=(
            "Validate and render the Hugging Face speech-to-speech sidecar command "
            "that uses anvil as a Chat Completions backend."
        ),
    )
    sub = p.add_subparsers(dest="action", required=True)

    def add_config(sp):
        sp.add_argument("--config", default=DEFAULT_CONFIG, help="sidecar manifest TOML")

    sp = sub.add_parser("validate", help="validate the sidecar manifest")
    add_config(sp)
    sp.add_argument("--json", action="store_true", help="emit JSON")

    sp = sub.add_parser("command", help="render the host speech-to-speech command")
    add_config(sp)
    sp.add_argument("--no-auth", action="store_true", help="omit the router token argument")
    sp.add_argument("--json", action="store_true", help="emit argv JSON instead of a shell command")

    sp = sub.add_parser("compose", help="render a Docker Compose service skeleton")
    add_config(sp)
    sp.add_argument("--service-name", default="speech-to-speech")

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        data = _load_for_cli(args.config)
        if args.action == "validate":
            if args.json:
                print(json.dumps({"ok": True, "config": args.config}, sort_keys=True))
            else:
                print("OK: %s" % args.config)
            return 0
        if args.action == "command":
            rendered = command_args(data, include_auth=not args.no_auth)
            if args.json:
                print(json.dumps({"argv": rendered}, indent=2))
            else:
                print(shell_command(rendered))
            return 0
        if args.action == "compose":
            print(compose_service(data, service_name=args.service_name), end="")
            return 0
    except ConfigError as exc:
        print("voice-sidecar: %s" % exc, file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
