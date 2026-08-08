"""File-backed environment fallback for durable secrets (ADR-0033).

Token and credential configuration names environment variables; the values
must also survive a host reboot. This module resolves a variable through the
operator ``.env`` chain — shell environment first, then
``$ANVIL_SERVING_HOME/.env``, then ``~/.env`` — without ever logging values.

Hermeticity rule: when a caller supplies an explicit ``env`` mapping, file
fallback is skipped entirely. Injected environments stay hermetic in tests and
in code that deliberately pins its inputs.
"""

from __future__ import annotations

import os
import re
from typing import Mapping, Optional

from .paths import config_path

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def read_dotenv(path):
    """Read a simple KEY=VALUE .env file without logging values.

    Shell environment wins later; this only fills missing vars for lifecycle
    commands launched from a manifest directory.
    """
    values = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not _ENV_NAME_RE.match(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[name] = value
    return values


def fallback_paths() -> list[str]:
    """Ordered dotenv fallback locations, highest precedence first."""
    return [
        config_path(".env"),
        os.path.join(os.path.expanduser("~"), ".env"),
    ]


def env_sources(name: str) -> list[str]:
    """Locations checked for ``name``, for refusal messages. Paths, no values."""
    return ["environment variable %s" % name] + fallback_paths()


def resolve_env_value(
    name: str,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> tuple[Optional[str], str]:
    """Resolve ``name`` to ``(value, source)``; ``(None, "")`` when unset.

    ``source`` is ``"env"`` for the process environment or the dotenv path the
    value came from. An explicit ``env`` mapping disables file fallback.
    """
    if not name or not _ENV_NAME_RE.match(name):
        return None, ""
    if env is not None:
        value = (env.get(name) or "").strip()
        return (value, "env") if value else (None, "")
    value = (os.environ.get(name) or "").strip()
    if value:
        return value, "env"
    for path in fallback_paths():
        try:
            file_value = (read_dotenv(path).get(name) or "").strip()
        except OSError:
            continue
        if file_value:
            return file_value, path
    return None, ""
