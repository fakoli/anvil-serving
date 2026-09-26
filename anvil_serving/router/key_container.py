"""Run key operations as the verified router container user on durable storage."""
from __future__ import annotations

import json
from pathlib import PurePosixPath
import re
import subprocess
import sys

from .keys import KeyStore, KeyStoreError, _write_secret

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_MAX_RESPONSE = 1024 * 1024


def _container_id(container: str, path: str) -> str:
    if not _NAME.fullmatch(container):
        raise KeyStoreError("invalid router container")
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{json .}}", container],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode or len(result.stdout) > _MAX_RESPONSE:
        raise KeyStoreError("router container is unavailable")
    row = json.loads(result.stdout)
    labels = row.get("Config", {}).get("Labels") or {}
    target = PurePosixPath(path)
    if (not target.is_absolute() or ".." in target.parts
            or row.get("State", {}).get("Running") is not True
            or labels.get("com.docker.compose.project") != "anvil-serving"
            or labels.get("com.docker.compose.service") != "router"
            or not re.fullmatch(r"[0-9a-f]{64}", row.get("Id", ""))):
        raise KeyStoreError("router container ownership is unverified")
    # The most specific covering mount owns the database directory. A file
    # mount cannot preserve SQLite's journal alongside the database.
    mounts = [mount for mount in row.get("Mounts", [])
              if target.is_relative_to(PurePosixPath(mount["Destination"]))]
    mount = max(mounts, key=lambda item: len(item["Destination"]), default={})
    if (sum(item["Destination"] == mount.get("Destination") for item in mounts) != 1
            or mount.get("Type") not in {"bind", "volume"} or mount.get("RW") is not True
            or target == PurePosixPath(mount.get("Destination", "/"))):
        raise KeyStoreError("router credential directory requires durable writable storage")
    return row["Id"]


def _invoke(container_id: str, payload: dict) -> dict:
    result = subprocess.run(
        ["docker", "exec", "-i", container_id, "python", "-m", __name__],
        input=json.dumps(payload), capture_output=True, text=True, timeout=30,
    )
    if result.returncode or len(result.stdout) > _MAX_RESPONSE:
        raise KeyStoreError("router credential operation failed")
    response = json.loads(result.stdout)
    if not isinstance(response, dict) or set(response) - {"data", "secret"} or "data" not in response:
        raise KeyStoreError("router credential response is invalid")
    return response


def dispatch_container(args) -> int:
    """Capture the one-time secret over a pipe; only public metadata is printed."""
    from .config import load_server_config
    from .serve import resolve_config_path

    try:
        path = load_server_config(resolve_config_path(args.config), container_paths=True).api_keys_path
        if not path:
            raise KeyStoreError("router credential store path is not configured")
        if args.action == "create":
            from pathlib import Path
            import os
            if os.path.lexists(Path(args.out).expanduser()):
                raise KeyStoreError("credential output file already exists")
        container_id = _container_id(args.container, path)
        payload = {key: value for key, value in vars(args).items()
                   if key not in {"container", "config", "out"}}
        payload["store_path"] = path
        response = _invoke(container_id, payload)
        if args.action == "create":
            try:
                secret = response["secret"]
                if not isinstance(secret, str) or not re.fullmatch(r"ask_[A-Za-z0-9_-]{43}", secret):
                    raise KeyStoreError("router credential response is invalid")
                _write_secret(args.out, secret)
            except (KeyStoreError, OSError, KeyError):
                key_id = response["data"]["key_id"]
                try:
                    _invoke(container_id, {"action": "revoke", "store_path": path, "key_id": key_id})
                except (KeyStoreError, OSError, ValueError, subprocess.SubprocessError):
                    print(json.dumps({"key_id": key_id, "cleanup_required": True}))
                raise KeyStoreError("credential output could not be written") from None
        print(json.dumps(response["data"], sort_keys=True))
        return 0
    except (KeyStoreError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print("anvil-serving router keys: container command failed", file=sys.stderr)
        return 2


def main() -> int:
    """Private pipe protocol, executed inside the router with no shell or TTY."""
    try:
        raw = sys.stdin.read(16_385)
        if len(raw) > 16_384:
            raise KeyStoreError("request too large")
        payload = json.loads(raw)
        action = payload["action"]
        path = payload["store_path"]
        store = KeyStore.initialize(path) if action == "init" else KeyStore(path)
        response = {}
        if action == "init":
            data = {"initialized": True, "key_count": len(store.list_keys())}
        elif action == "create":
            data, response["secret"] = store.create(
                payload["name"], payload["model"], payload["path"], payload["rpm"], payload["expires_days"],
            )
        elif action == "list":
            data = store.list_keys()
        elif action == "usage":
            data = store.usage(payload["key_id"], payload["limit"])
        elif action == "revoke":
            if not store.revoke(payload["key_id"]):
                raise KeyStoreError("credential key was not found")
            data = {"key_id": payload["key_id"], "revoked": True}
        else:
            raise KeyStoreError("unknown credential operation")
        response["data"] = data
        print(json.dumps(response))
        return 0
    except (KeyStoreError, OSError, ValueError, TypeError, KeyError):
        print("router credential operation failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
