"""Offline managed caller enrollment. No account, credential or provider discovery."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import urlsplit

from .control_plane.mcp.auth_file import read_private_auth_file
from .observability.dashboard.contracts import canonical, strict_json
from .router.config import load_server_config
from .router.identity import Actor, opaque_id
from .router.keys import KeyStore, KeyStoreError, _write_secret
from .router.serve import resolve_config_path

SCHEMA = "anvil.managed-client-identity/v1"
PATHS = {"foreground", "title", "tag", "background"}
LIMIT = 1024 * 1024


def _require(condition):
    if not condition:
        raise ValueError("invalid managed client declaration")


def _closed(value, names):
    _require(type(value) is dict and set(value) == set(names))
    return value


def _list(value, maximum=128):
    _require(type(value) is list and len(value) <= maximum)
    return value


def _origin(value):
    _require(type(value) is str and len(value) <= 4096)
    parsed = urlsplit(value)
    _require(parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
             and not parsed.password and not parsed.query and not parsed.fragment)
    _require(parsed.port is None or 1 <= parsed.port <= 65535)
    return value.rstrip("/")


def _read(path):
    # The existing held-descriptor guard rejects symlinks, hardlinks, unsafe
    # ancestors, swaps and oversized files; this never reads credential refs.
    raw = read_private_auth_file(str(Path(path).absolute()), max_bytes=LIMIT)
    result = strict_json(raw)
    canonical(result)  # Also rejects floating-point overflow such as 1e400.
    return result


def _default():
    from .router.serve import default_config_candidates
    candidates = default_config_candidates()
    return str(Path(candidates[0]).parent / "client-identity.json")


def _load(path):
    value = _closed(_read(path), {"schema", "router_config", "installed_path", "bindings", "webui"})
    _require(value["schema"] == SCHEMA)
    for name in ("router_config", "installed_path"):
        _require(type(value[name]) is str and os.path.isabs(value[name]))
    _require(Path(value["installed_path"]).absolute() != Path(path).absolute())
    server = load_server_config(resolve_config_path(value["router_config"]))
    _require(bool(server.api_keys_path))
    protected_refs = {str(Path(value["router_config"]).absolute()), str(Path(server.api_keys_path).absolute()),
                      str(Path(path).absolute())}
    protected_refs.update(p.signer_file for p in server.webui_identity if p.signer_file is not None)
    _require(str(Path(value["installed_path"]).absolute()) not in protected_refs)
    bindings = _list(value["bindings"])
    _require(bool(bindings))
    seen = set()
    for binding in bindings:
        _closed(binding, {"client_id", "key_id", "kind", "owner_id", "expected_revision", "policy"})
        opaque_id(binding["client_id"])
        _require(binding["client_id"] not in seen)
        seen.add(binding["client_id"])
        Actor(binding["kind"], binding["owner_id"], binding["expected_revision"] + 1
              if type(binding["expected_revision"]) is int else None)
        _require(type(binding["expected_revision"]) is int and 0 <= binding["expected_revision"] < 2**53-1)
        _require(type(binding["policy"]) is str and binding["policy"] in {"direct", "service", "webui", "detached_service_only"})
        _require(binding["policy"] == "direct" or binding["kind"] == "service")
        _require(type(binding["key_id"]) is str)
    _require(len({b["key_id"] for b in bindings}) == len(bindings))
    by_id = {b["client_id"]: b for b in bindings}
    profiles = {p.instance: p for p in server.webui_identity}
    webuis = _list(value["webui"], 32)
    seen_webui = set()
    for webui in webuis:
        _closed(webui, {"client_id", "instance", "native", "approved_recipients", "request_paths"})
        opaque_id(webui["client_id"])
        binding = by_id.get(webui["client_id"])
        _require(binding is not None and binding["policy"] == "webui")
        _require(webui["client_id"] not in seen_webui)
        seen_webui.add(webui["client_id"])
        opaque_id(webui["instance"])
        profile = profiles.get(webui["instance"])
        _require(profile is not None and profile.credential_kind == "device_key"
                 and profile.credential_id == binding["key_id"] and profile.require_user
                 and profile.signer_file is not None)
        native = _closed(webui["native"], {"openai.api_base_urls", "openai.api_configs", "other_recipients"})
        urls = _list(native["openai.api_base_urls"])
        configs = native["openai.api_configs"]
        _require(type(configs) is dict and set(configs) == {str(i) for i in range(len(urls))})
        recipients = []
        for index, url in enumerate(urls):
            config = _closed(configs[str(index)], {"enable", "custom_header_names"})
            _require(type(config["enable"]) is bool)
            names = _list(config["custom_header_names"])
            _require(all(type(n) is str and n and len(n) <= 128 for n in names))
            _require(not any(n.casefold().startswith("x-openwebui-") or n.casefold().startswith("x-open-webui-")
                             or n.casefold() in {"authorization", "x-api-key"} for n in names))
            if config["enable"]:
                recipients.append(_origin(url))
        # Explicit non-OpenAI plugin/tool/other recipients participate in the
        # global-forwarding audit, never vanish behind a completeness boolean.
        recipients += [_origin(url) for url in _list(native["other_recipients"])]
        approved = [_origin(url) for url in _list(webui["approved_recipients"])]
        _require(bool(recipients) and len(set(approved)) == len(approved)
                 and set(recipients) == set(approved))
        paths = _closed(webui["request_paths"], PATHS)
        for indices in paths.values():
            _require(bool(_list(indices)))
            _require(all(type(i) is int and 0 <= i < len(urls) and configs[str(i)]["enable"] for i in indices))
    _require(seen_webui == {b["client_id"] for b in bindings if b["policy"] == "webui"})
    return value, server


def _owner(store, binding):
    # Owner-only non-secret readback uses the original full Principal, and a
    # writer lock before sampling time just like bind_owner's expiry boundary.
    with store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        principal = store._caller_candidate(connection, binding["key_id"], time.time())
        _require(principal is not None and principal.owner is None)
        actor = store._bound_actor(connection, binding["key_id"])
        connection.execute("COMMIT")
    return actor


def _state(store, binding):
    # One validated owner sample supplies both public status and current_actor.
    # A later writer can change current state, but cannot splice two revisions
    # into an internally contradictory installed readback.
    actor = _owner(store, binding)
    revision = actor.binding_revision if actor.kind != "unattributed" else 0
    same = (actor.kind == binding["kind"] and actor.id == binding["owner_id"]
            and revision == binding["expected_revision"] + 1)
    if not same:
        store.bind_owner(binding["key_id"], binding["kind"], binding["owner_id"],
                         binding["expected_revision"], dry_run=True)
    return same, actor


def run(action, path, *, confirm=False, dry_run=False):
    value, server = _load(path)
    store = KeyStore(server.api_keys_path)
    payload = canonical({"schema": SCHEMA, "declaration_sha256": hashlib.sha256(canonical(value)).hexdigest(),
                         "bindings": value["bindings"], "webui": value["webui"]}) + b"\n"
    target = Path(value["installed_path"])
    installed = None
    if os.path.lexists(target):
        installed = canonical(_read(target)) + b"\n"
        _require(installed == payload)  # Never overwrite a different binding.
    statuses = []
    for binding in value["bindings"]:
        same, actor = _state(store, binding)
        statuses.append({"client_id": binding["client_id"], "policy": binding["policy"],
                         "binding_installed": same, "key_id": binding["key_id"],
                         "desired_actor": Actor(binding["kind"], binding["owner_id"], binding["expected_revision"] + 1).to_dict(),
                         "current_actor": actor.to_dict()})
    apply = action == "install" and confirm and not dry_run
    if apply:
        for binding, status in zip(value["bindings"], statuses):
            if not status["binding_installed"]:
                store.bind_owner(binding["key_id"], binding["kind"], binding["owner_id"], binding["expected_revision"])
        if installed is None:
            # Existing exclusive, protected, inode-guarded writer. This feature
            # document does not activate any client. Failure may leave enrolled
            # keys; retry validates their exact CAS, never rolls back grants.
            _write_secret(str(target), payload.decode().rstrip("\n"))
        installed = canonical(_read(target)) + b"\n"
        _require(installed == payload)
        for binding, status in zip(value["bindings"], statuses):
            same, actor = _state(store, binding)
            status["binding_installed"] = same
            status["current_actor"] = actor.to_dict()
    ready = installed == payload and all(s["binding_installed"] for s in statuses)
    return {"schema": SCHEMA, "action": action, "applied": apply,
            "configuration_status": "installed" if ready else "incomplete",
            "installed_sha256": hashlib.sha256(installed).hexdigest() if installed else None,
            "clients": statuses,
            "request_paths": {w["client_id"]: {p: ("configuration_present_live_pending" if ready else "incomplete_live_pending") for p in sorted(PATHS)}
                              for w in value["webui"]},
            "recipients": {w["client_id"]: {
                "enabled_connection_indices": [int(i) for i, c in w["native"]["openai.api_configs"].items() if c["enable"]],
                "other_recipient_count": len(w["native"]["other_recipients"]),
                "approved_destination_sha256": [hashlib.sha256(_origin(url).encode()).hexdigest()
                                                for url in w["approved_recipients"]],
            } for w in value["webui"]},
            "recipient_status": "declared_inventory_validated_live_pending",
            "live_status": "unqualified", "providers_modified": False}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid managed client command")


def dispatch(argv=None):
    try:
        parser = _Parser(prog="anvil-serving router clients")
        parser.add_argument("action", choices=("preview", "install", "readback"))
        parser.add_argument("--config", default=None)
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        args = parser.parse_args(argv)
        print(canonical(run(args.action, args.config or _default(), confirm=args.confirm,
                            dry_run=args.dry_run)).decode())
        return 0
    except (ValueError, OSError, sqlite3.Error, KeyStoreError):
        print("anvil-serving router clients: configuration/enrollment failed; readback required", file=sys.stderr)
        return 2
