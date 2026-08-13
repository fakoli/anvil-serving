"""Optional lifecycle-event seam for anvil-serving.

The anvil-events CLI owns durable outbox writes and publishing. This module
only loads the explicit operator gate and invokes that CLI without a shell.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

from .envfile import resolve_env_value
from .paths import config_path as operator_config_path

_LIFECYCLE_KINDS = frozenset({
    "serve.up",
    "serve.down",
    "profile.enter",
    "profile.leave",
    "promote.applied",
    "promote.rolled_back",
})
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class LifecycleEventError(RuntimeError):
    """The lifecycle action succeeded but its durable event was not recorded."""


def _load_events_config(config_path):
    path = Path(config_path)
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        document = tomllib.load(handle)
    events = document.get("events", {})
    return events if isinstance(events, dict) else {}


def _validate_enabled_config(config):
    if "nats_url" in config:
        raise ValueError("events nats_url is forbidden; configure its env-var name")
    required = ("command", "host", "producer", "nats_url_env")
    for key in required:
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("events %s must be a non-empty string" % key)
    if not _ENV_NAME_RE.fullmatch(config["nats_url_env"]):
        raise ValueError("events nats_url_env must be an uppercase environment name")


def emit_lifecycle_event(
    kind,
    payload,
    *,
    config_path=None,
    correlation_id=None,
    environ=None,
    _run=subprocess.run,
):
    """Record one lifecycle event through the outbox-owning anvil-events CLI."""
    if kind not in _LIFECYCLE_KINDS:
        raise ValueError("unsupported lifecycle event kind %r" % kind)
    config_path = config_path or operator_config_path("events.toml")
    try:
        config = _load_events_config(config_path)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise LifecycleEventError(
            "could not load events config %s: %s" % (config_path, exc)
        ) from exc
    if config.get("enabled") is not True:
        return {"enabled": False, "emitted": False, "detail": "disabled"}
    try:
        _validate_enabled_config(config)
    except ValueError as exc:
        raise LifecycleEventError(str(exc)) from exc

    env = dict(os.environ if environ is None else environ)
    nats_url_env = config["nats_url_env"]
    nats_url, _source = resolve_env_value(nats_url_env, env=environ)
    if not nats_url:
        raise LifecycleEventError("events nats_url_env %r is not set" % nats_url_env)
    env["ANVIL_EVENTS_NATS_URL"] = nats_url

    argv = [
        config["command"],
        "emit",
        kind,
        "--host",
        config["host"],
        "--producer",
        config["producer"],
    ]
    if correlation_id:
        argv += ["--correlation", correlation_id]
    argv.append(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    try:
        completed = _run(
            argv,
            env=env,
            timeout=5,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise LifecycleEventError(
            "could not invoke anvil-events command %r: %s" % (config["command"], exc)
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise LifecycleEventError("anvil-events emit timed out after 5 seconds") from exc
    if completed.returncode != 0:
        raise LifecycleEventError(
            "anvil-events emit failed (rc=%d): %s"
            % (completed.returncode, (completed.stderr or completed.stdout).strip()[:200])
        )
    return {"enabled": True, "emitted": True, "detail": "recorded"}
