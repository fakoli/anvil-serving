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

import os
import re
import urllib.parse

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - guarded by requires-python >=3.11
    tomllib = None

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CONFIG = os.path.join(REPO_ROOT, "examples", "voice", "voice.example.toml")

_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SECRET_KEY_NAMES = {"api_key", "token", "secret", "password"}
_SECRET_VALUE_PREFIXES = ("sk-", "hf_", "hf-", "ghp_", "ghp-")


class ConfigError(ValueError):
    """Raised when a voice manifest is missing, malformed, or unsafe to use."""


def _resolve_config_path(path: str | None) -> str:
    if path:
        return path
    if os.path.isfile(DEFAULT_CONFIG):
        return DEFAULT_CONFIG
    raise ConfigError(
        "no default voice manifest is available in this installation; pass an explicit path"
    )


def load_manifest(path: str | None = None) -> dict:
    """Load and validate the voice manifest TOML at `path` (or the shipped example)."""
    if tomllib is None:  # pragma: no cover - guarded by requires-python >=3.11
        raise ConfigError("tomllib unavailable (need Python >= 3.11)")
    config_path = _resolve_config_path(path)
    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError("config not found: %s" % config_path)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("cannot parse %s: %s" % (config_path, exc))
    validate_manifest(data)
    return data


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
                if value.startswith(_SECRET_VALUE_PREFIXES):
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
    _validate_host(parsed.hostname or "", key=key)
    return parsed


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
    _parsed_url(_string(table, "base_url"), key="voice.%s.base_url" % name, schemes=("http", "https"))
    if model_required:
        _string(table, "model")
    _check_env_name(table, "api_key")


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
    # `make_ws_server` (e.g. `anvil-serving voice run`) -- a manifest with a
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
