"""Stdlib-only transport to the managed router credential owner."""
from __future__ import annotations

import json
from pathlib import PurePosixPath
import re
import subprocess

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_MAX_RESPONSE = 1024 * 1024


def _container_id(container: str, path: str) -> str:
    if not _NAME.fullmatch(container):
        raise ValueError("invalid router container")
    result = subprocess.run(
        ["docker", "inspect", "--format", '{"Id":{{json .Id}},"State":{{json .State}},"Config":{"Labels":{{json .Config.Labels}}},"Mounts":{{json .Mounts}}}', container],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode or len(result.stdout) > _MAX_RESPONSE:
        raise ValueError("router container is unavailable")
    row = json.loads(result.stdout)
    labels = row.get("Config", {}).get("Labels") or {}
    target = PurePosixPath(path)
    if (not target.is_absolute() or ".." in target.parts
            or row.get("State", {}).get("Running") is not True
            or labels.get("com.docker.compose.project") != "anvil-serving"
            or labels.get("com.docker.compose.service") != "router"
            or not re.fullmatch(r"[0-9a-f]{64}", row.get("Id", ""))):
        raise ValueError("router container ownership is unverified")
    # The most specific covering mount owns the database directory. A file
    # mount cannot preserve SQLite's journal alongside the database.
    mounts = [mount for mount in row.get("Mounts", [])
              if target.is_relative_to(PurePosixPath(mount["Destination"]))]
    mount = max(mounts, key=lambda item: len(item["Destination"]), default={})
    if (sum(item["Destination"] == mount.get("Destination") for item in mounts) != 1
            or mount.get("Type") not in {"bind", "volume"} or mount.get("RW") is not True
            or target == PurePosixPath(mount.get("Destination", "/"))):
        raise ValueError("router credential directory requires durable writable storage")
    return row["Id"]


def _invoke(container_id: str, payload: dict) -> dict:
    result = subprocess.run(
        ["docker", "exec", "-i", container_id, "python", "-m", "anvil_serving.router.key_container"],
        input=json.dumps(payload), capture_output=True, text=True, timeout=30,
    )
    if result.returncode or len(result.stdout) > _MAX_RESPONSE:
        raise ValueError("router credential operation failed")
    response = json.loads(result.stdout)
    if not isinstance(response, dict) or set(response) - {"data", "secret"} or "data" not in response:
        raise ValueError("router credential response is invalid")
    return response
