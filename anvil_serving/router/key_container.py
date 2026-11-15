"""Run key operations as the verified router container user on durable storage."""
from __future__ import annotations

import json
import re
import subprocess
import sys

from .keys import KeyStore, KeyStoreError, _write_secret

def _container_id(container: str, path: str) -> str:
    from ..connect.key_transport import _container_id as invoke
    try:
        return invoke(container, path)
    except ValueError as exc:
        raise KeyStoreError(str(exc)) from None


def _invoke(container_id: str, payload: dict) -> dict:
    from ..connect.key_transport import _invoke as invoke
    try:
        return invoke(container_id, payload)
    except ValueError as exc:
        raise KeyStoreError(str(exc)) from None


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
                   if key not in {"container", "config"}}
        payload["store_path"] = path
        if args.action == "create":
            payload.pop("out", None)
        if args.action in {"backup", "restore"}:
            targets = (args.out, args.snapshot) if args.action == "restore" else (args.out,)
            if any(_container_id(args.container, target) != container_id for target in targets):
                raise KeyStoreError("router container ownership changed")
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
        elif action == "memory-key":
            import os
            from .config import load, load_server_config
            from .connect_keys import identity, principal_checker, provision_memory_key
            from ..memory_access import resolve
            if set(payload) != {"action", "store_path", "principal", "generation", "epoch", "grant", "secret", "expected_key_id", "apply"} or type(payload["apply"]) is not bool:
                raise KeyStoreError("invalid memory onboarding request")
            config = "/etc/anvil/config.toml"
            server = load_server_config(config, container_paths=True)
            check_secret = os.environ.get(server.connect_check_env or "")
            routes = [route for route in load(config).memory_routes if route.alias == "memory.user"]
            if server.api_keys_path != path or not check_secret or len(routes) != 1 or not routes[0].access_file:
                raise KeyStoreError("user memory router configuration is unavailable")
            actor = identity(payload["principal"], payload["generation"], payload["epoch"])
            _, grant = resolve(routes[0].access_file, actor[0], "banks")
            if grant != payload["grant"]:
                raise KeyStoreError("user memory bank configuration differs")
            store.owner_check = principal_checker(server.connect_home_url, check_secret)
            data = provision_memory_key(store, actor, secret=payload["secret"], expected_key_id=payload["expected_key_id"], apply=payload["apply"])
        elif action == "memory-key-retire":
            import hashlib
            if (set(payload) != {"action", "store_path", "principal", "key_id", "secret"}
                    or not isinstance(payload["secret"], str)
                    or not re.fullmatch(r"ask_[A-Za-z0-9_-]{43}", payload["secret"])):
                raise KeyStoreError("invalid managed memory key receipt")
            with store._connect() as db:
                row = db.execute("SELECT k.key_id,o.owner,k.name FROM keys k JOIN connect_key_owners o USING(key_id) WHERE k.token_hash=?", (hashlib.sha256(payload["secret"].encode()).digest(),)).fetchone()
            if row is not None and (row[1:] != (payload["principal"], "User memory") or payload["key_id"] not in (None, row[0])):
                raise KeyStoreError("managed memory key receipt differs")
            data = {"key_id": payload["key_id"], "revoked": row is None or store.revoke(row[0])}
        elif action == "list":
            data = store.list_keys()
        elif action == "usage":
            data = store.usage(payload["key_id"], payload["limit"])
        elif action == "client-readback":
            from ..client_identity import _native_readback
            data = _native_readback(store, payload)
        elif action == "bind":
            actor = store.bind_owner(payload["key_id"], payload["kind"], payload["owner_id"],
                                     payload["expected_revision"], dry_run=payload["dry_run"])
            data = {"key_id": payload["key_id"], "actor": actor.to_dict(), "dry_run": payload["dry_run"]}
        elif action in {"backup", "restore"}:
            from .usage_store import UsageStore
            data = (UsageStore(store).backup(payload["out"]) if action == "backup"
                    else UsageStore.restore(payload["snapshot"], payload["out"]))
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
