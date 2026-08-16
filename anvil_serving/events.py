"""Optional local-first lifecycle-event seam for anvil-serving.

The anvil-events CLI owns durable SQLite acceptance and asynchronous broker
delivery. This module only loads the explicit operator gate and invokes the
v2 ``record`` command without a shell or network dependency.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tomllib
import uuid

from .paths import config_path as operator_config_path

_LIFECYCLE_KINDS = frozenset({
    "serve.up",
    "serve.down",
    "profile.enter",
    "profile.leave",
    "promote.applied",
    "promote.rolled_back",
})
_NODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_PRODUCER_RE = re.compile(r"^[A-Za-z0-9_-]+(?::[A-Za-z0-9_-]+)*$")
_RECORD_TIMEOUT_SECONDS = 35


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


def _validate_enabled_config(config, config_path):
    retired = sorted({"host", "nats_url", "nats_url_env"} & set(config))
    if retired:
        raise ValueError(
            "events config uses retired v1 fields: %s" % ", ".join(retired)
        )
    for key in ("command", "node", "producer", "root"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("events %s must be a non-empty string" % key)
    if not _NODE_RE.fullmatch(config["node"]):
        raise ValueError("events node must be one safe token")
    if not _PRODUCER_RE.fullmatch(config["producer"]):
        raise ValueError("events producer must be colon-separated safe tokens")
    if config["producer"].split(":", 1)[0] != config["node"]:
        raise ValueError("events producer identity must belong to events node")
    root = Path(os.path.expanduser(config["root"]))
    if not root.is_absolute():
        raise ValueError(
            "events root must be absolute (config %s)" % Path(config_path)
        )
    return root


def _operation_key(kind, make_uuid):
    return "anvil-serving:%s:%s" % (kind, make_uuid().hex)


def _accepted_record(stdout):
    try:
        result = json.loads(stdout)
    except (TypeError, ValueError) as exc:
        raise LifecycleEventError(
            "anvil-events record returned invalid acceptance evidence"
        ) from exc
    if (
        not isinstance(result, dict)
        or result.get("accepted") is not True
        or not isinstance(result.get("already_recorded"), bool)
        or not isinstance(result.get("event_id"), str)
        or not result["event_id"]
    ):
        raise LifecycleEventError(
            "anvil-events record returned invalid acceptance evidence"
        )
    return result


def emit_lifecycle_event(
    kind,
    payload,
    *,
    config_path=None,
    correlation_id=None,
    environ=None,
    _run=subprocess.run,
    _make_uuid=uuid.uuid4,
):
    """Record one lifecycle fact through the outbox-owning v2 CLI."""
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
        root = _validate_enabled_config(config, config_path)
    except ValueError as exc:
        raise LifecycleEventError(str(exc)) from exc

    argv = [
        config["command"],
        f"--root={root}",
        "record",
        kind,
        f"--node={config['node']}",
        f"--producer={config['producer']}",
        f"--operation-key={_operation_key(kind, _make_uuid)}",
    ]
    if correlation_id:
        argv.append(f"--correlation={correlation_id}")
    try:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LifecycleEventError(
            "lifecycle event payload must contain only JSON values"
        ) from exc
    try:
        completed = _run(
            argv,
            env=dict(os.environ if environ is None else environ),
            input=encoded,
            timeout=_RECORD_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise LifecycleEventError(
            "could not invoke anvil-events command %r: %s" % (config["command"], exc)
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise LifecycleEventError(
            "anvil-events record timed out after %d seconds"
            % _RECORD_TIMEOUT_SECONDS
        ) from exc
    if completed.returncode != 0:
        raise LifecycleEventError(
            "anvil-events record failed (rc=%d): %s"
            % (completed.returncode, (completed.stderr or completed.stdout).strip()[:200])
        )
    accepted = _accepted_record(completed.stdout)
    return {
        "enabled": True,
        "emitted": True,
        "detail": "recorded",
        "event_id": accepted["event_id"],
        "already_recorded": accepted["already_recorded"],
    }
