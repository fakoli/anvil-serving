"""Shared filesystem conventions for anvil-serving operator state."""
from __future__ import annotations

import os

CONFIG_HOME_ENV = "ANVIL_SERVING_HOME"
DEFAULT_CONFIG_HOME = "~/.anvil-serving"


def config_home() -> str:
    """Return the operator config directory.

    ``ANVIL_SERVING_HOME`` is intentionally a directory override, not a config
    file override. Individual commands still accept explicit paths for exact
    one-off operations.
    """
    raw = os.environ.get(CONFIG_HOME_ENV) or DEFAULT_CONFIG_HOME
    return os.path.abspath(os.path.expanduser(raw))


def config_path(*parts: str) -> str:
    return os.path.join(config_home(), *parts)


def first_existing(paths: list[str]) -> str | None:
    for path in paths:
        if os.path.isfile(os.path.expanduser(path)):
            return path
    return None
