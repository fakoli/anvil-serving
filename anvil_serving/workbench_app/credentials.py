"""Resolve declared credentials without executing configuration or exposing values."""

import os
import stat
from pathlib import Path

from ..observability.dashboard.contracts import ObservatoryError


def resolve_secret(reference, environment):
    try:
        if reference.startswith("file:"):
            path = Path(reference[5:])
            if not path.is_absolute():
                raise ValueError()
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 8192:
                    raise ValueError()
                value = source.read(8193).decode().strip()
        else:
            value = environment.get(reference, "")
        if type(value) is not str or not value or len(value) > 8192 or any(c in value for c in "\r\n\0"):
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise ObservatoryError("credential_unavailable", "The selected connection's protected credential is unavailable.", 503) from None
