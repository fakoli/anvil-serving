"""Offline managed caller enrollment. No account, credential or provider discovery."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
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


NAMESPACE_SCHEMA = "anvil.client-namespace/v1"


def _namespace(path):
    target = Path(path).absolute().with_name("client-namespace.json")
    if not os.path.lexists(target):
        return None
    value = _closed(_read(target), {"schema", "compose", "container", "expected_image_id", "declaration_path"})
    _require(value["schema"] == NAMESPACE_SCHEMA and type(value["compose"]) is str
             and os.path.isabs(value["compose"]) and value["container"] == "anvil-router"
             and value["declaration_path"] == "/run/anvil-client-worker/client-identity.json"
             and type(value["expected_image_id"]) is str
             and value["expected_image_id"].startswith("sha256:")
             and len(value["expected_image_id"]) == 71
             and all(c in "0123456789abcdef" for c in value["expected_image_id"][7:]))
    return value


def _managed(action, path, namespace, *, confirm=False, dry_run=False, credential=None, client_id=None):
    from . import router_manage
    from .router import key_container

    value, server = _load(path)
    declaration_sha = hashlib.sha256(canonical(value)).hexdigest()
    state, _ = router_manage._container_compose_project(namespace["container"])
    if state in {"absent", "created", "exited"}:
        _require((credential is None and client_id is None)
                 or (type(credential) is str and len(credential) <= 8192 and type(client_id) is str))
        auth = None if credential is None else {"credential": credential, "client_id": client_id,
                 "router_config_sha256": hashlib.sha256(read_private_auth_file(value["router_config"], max_bytes=LIMIT)).hexdigest()}
        return router_manage.enroll_clients_offline(namespace, action, declaration_sha,
                                                   confirm=confirm, dry_run=dry_run, _client_auth=auth)
    _require(state == "running" and action in {"preview", "readback"})
    container_id = key_container._container_id(namespace["container"], server.api_keys_path)
    before = router_manage._restart_custody(namespace["container"], router_manage.subprocess.run)
    _require(before.get("available") is True and before["container_id"] == container_id
             and before["image_id"] == namespace["expected_image_id"])
    payload = {"action": "client-readback", "store_path": server.api_keys_path,
               "declaration_path": namespace["declaration_path"], "declaration_sha256": declaration_sha,
               "router_config_sha256": hashlib.sha256(read_private_auth_file(value["router_config"], max_bytes=LIMIT)).hexdigest(),
               "client_id": client_id, "credential": credential}
    response = key_container._invoke(container_id, payload)
    _require(before == router_manage._restart_custody(namespace["container"], router_manage.subprocess.run)
             and type(response.get("data")) is dict
             and response["data"].get("declaration_sha256") == declaration_sha)
    return response["data"]


def _native_readback(store, payload):
    _closed(payload, {"action", "store_path", "declaration_path", "declaration_sha256", "router_config_sha256", "client_id", "credential"})
    _require(payload["declaration_path"] == str(WORKER_ROOT / "client-identity.json"))
    value, server = _load(payload["declaration_path"])
    _require(store.path == Path(server.api_keys_path).expanduser().absolute()
             and hashlib.sha256(canonical(value)).hexdigest() == payload["declaration_sha256"]
             and hashlib.sha256(read_private_auth_file(value["router_config"], max_bytes=LIMIT)).hexdigest()
             == payload["router_config_sha256"])
    result = run("readback", payload["declaration_path"], _store=store, _declaration=(value, server))
    if payload["client_id"] is not None:
        _require(type(payload["credential"]) is str and len(payload["credential"]) <= 8192)
        bindings = [b for b in value["bindings"] if b["client_id"] == payload["client_id"]]
        _require(len(bindings) == 1 and result["configuration_status"] == "installed")
        binding = bindings[0]
        with store._write() as db:
            db.execute("BEGIN IMMEDIATE")
            principal = store._authenticate_in_transaction(db, payload["credential"])
            actor = store._bound_actor(db, binding["key_id"])
            _require(principal is not None and principal.owner is None
                     and principal.key_id == binding["key_id"]
                     and bool(principal.models) and "*" not in principal.models
                     and bool(principal.paths) and "*" not in principal.paths
                     and actor.kind == binding["kind"] and actor.id == binding["owner_id"]
                     and actor.binding_revision == binding["expected_revision"] + 1)
            if binding["policy"] == "webui":
                _require(set(principal.paths) <= {"/v1/models", "/v1/chat/completions", "/v1/messages", "/v1/responses"})
            result["credential_validated"] = True
            result["grant_sha256"] = hashlib.sha256(canonical({"models": list(principal.models),
                                               "paths": list(principal.paths), "actor": actor.to_dict()})).hexdigest()
            db.execute("COMMIT")
    else:
        _require(payload["credential"] is None)
    _require(_load(payload["declaration_path"]) == (value, server))
    result["declaration_sha256"] = payload["declaration_sha256"]
    return result


def _owner(store, binding):
    # Owner-only non-secret readback uses the original full Principal, and a
    # writer lock before sampling time just like bind_owner's expiry boundary.
    with store._write() as connection:
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


def run(action, path, *, confirm=False, dry_run=False, _store=None, _declaration=None):
    if _store is None and _declaration is None:
        namespace = _namespace(path)
        if namespace is not None:
            return _managed(action, path, namespace, confirm=confirm, dry_run=dry_run)
    value, server = _load(path) if _declaration is None else _declaration
    store = KeyStore(server.api_keys_path) if _store is None else _store
    _require(isinstance(store, KeyStore)
             and store.path == Path(server.api_keys_path).expanduser().absolute())
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


@contextmanager
def _offline_store(path):
    """Native worker custody; the same store must reach every enrollment call.

    Legacy producers remain an external operator hold. Retained native owners
    are classified under the original fences; remote UNKNOWN stays UNKNOWN.
    """
    from .router.usage_store import UsageStore, _validate_schema

    value, server = _load(path)
    store = KeyStore(server.api_keys_path)
    with store._offline_custody(server, allow_retained=True):
        with store._connect() as connection:
            _require(store.version == 3)
            _validate_schema(connection)
        usage = UsageStore(store)
        usage.owner_config = server
        usage.inactive_native_runs(usage._observed_owner(server.router_owner_id),
                                   domain_id=server.usage_domain_id)
        # Bind custody to the exact protected declaration/config that was read.
        current, current_server = _load(path)
        _require(current == value and current_server == server)
        yield store, value, server


def _run_offline(action, path, *, confirm=False, dry_run=False):
    with _offline_store(path) as (store, value, server):
        return run(action, path, confirm=confirm, dry_run=dry_run, _store=store,
                   _declaration=(value, server))

WORKER_SCHEMA = "anvil.client-worker/v1"
WORKER_ROOT = Path("/run/anvil-client-worker")


def _worker_job(path, value, server):
    _require(Path(path) == WORKER_ROOT / "client-identity.json")
    job = _closed(_read(WORKER_ROOT / "worker.json"),
                  {"schema", "declaration_sha256", "helper_sha256", "recipients", "material_directories"})
    _require(job["schema"] == WORKER_SCHEMA
             and job["declaration_sha256"] == hashlib.sha256(canonical(value)).hexdigest())
    hashes = _closed(job["helper_sha256"], {"webui", "recipient"})
    for digest in hashes.values():
        _require(digest is None or (type(digest) is str and len(digest) == 64
                                   and all(c in "0123456789abcdef" for c in digest)))
    directories = _list(job["material_directories"])
    _require(len(set(directories)) == len(directories))
    for directory in directories:
        _require(type(directory) is str and os.path.isabs(directory)
                 and ".." not in Path(directory).parts and len(Path(directory).parts) >= 4
                 and not Path(server.api_keys_path).is_relative_to(directory)
                 and not WORKER_ROOT.is_relative_to(directory)
                 and not Path(value["router_config"]).is_relative_to(directory))
    _require(str(Path(value["installed_path"]).parent) in directories)
    profiles = {p.instance: p for p in server.webui_identity}
    for webui in value["webui"]:
        _require(str(Path(profiles[webui["instance"]].signer_file).parent) in directories)
    recipients = _list(job["recipients"])
    for entry in recipients:
        _closed(entry, {"declaration", "stage_directory"})
        _require(entry["stage_directory"] in directories and type(entry["declaration"]) is dict)
        source = entry["declaration"].get("source_file")
        _require(type(source) is str and os.path.isabs(source)
                 and ".." not in Path(source).parts
                 and any(Path(source).is_relative_to(p) for p in directories))
    _require((hashes["webui"] is not None) == bool(value["webui"]))
    _require((hashes["recipient"] is not None) == bool(recipients))
    return job


def _worker_helper(kind, expected):
    # Fixed native helper operations only. Execute the exact protected bytes
    # already compared with the accepted source digest; do not reopen to import.
    import types
    names = {"webui": "webui-integrations.py", "recipient": "configure-pi.py"}
    source = WORKER_ROOT / names[kind]
    raw = read_private_auth_file(str(source), max_bytes=LIMIT)
    _require(hashlib.sha256(raw).hexdigest() == expected)
    module = types.ModuleType("_anvil_client_" + kind)
    module.__file__ = str(source)
    exec(compile(raw, str(source), "exec"), module.__dict__)
    return module, raw


def _native_offline_clients(action, path, *, confirm=False, dry_run=False):
    """Bounded fixed-operation native worker; no inference/remote entrypoint."""
    import json
    import select

    _require(action in {"preview", "install", "readback"}
             and type(confirm) is bool and type(dry_run) is bool and os.name == "posix")
    with _offline_store(path) as (store, value, server):
        job = _worker_job(path, value, server)
        digest = hashlib.sha256(canonical(job)).hexdigest()
        helpers = {kind: _worker_helper(kind, expected) for kind, expected in job["helper_sha256"].items()
                   if expected is not None}
        run_id = job["declaration_sha256"]
        print(json.dumps({"pending_sha256": digest, "run_id": run_id}), flush=True)
        _require(bool(select.select([sys.stdin], [], [], 30)[0]))
        raw = sys.stdin.buffer.readline(16385)
        ack = strict_json(raw)
        _require(len(raw) <= 16384 and type(ack) is dict and ack.get("commit") == digest
                 and (set(ack) == {"commit"} or set(ack) == {"commit", "client_id", "credential", "router_config_sha256"}))
        _require(_worker_job(path, value, server) == job and _load(path) == (value, server))
        if "webui" in helpers:
            helper, source = helpers["webui"]
            result = helper.client_provision(action, path, confirm=confirm, dry_run=dry_run,
                                             _store=store, _declaration=(value, server),
                                             _source=source.decode("utf-8"))
        else:
            result = run(action, path, confirm=confirm, dry_run=dry_run, _store=store,
                         _declaration=(value, server))
        staged = 0
        for entry in job["recipients"]:
            state = helpers["recipient"][0].stage_recipient(
                entry["declaration"], path, entry["stage_directory"], _store=store,
                _declaration=(value, server), dry_run=not (action == "install" and confirm and not dry_run))
            staged += int(state["staged"])
        _require(_worker_job(path, value, server) == job and _load(path) == (value, server))
        for kind, (_, source) in helpers.items():
            _require(read_private_auth_file(str(WORKER_ROOT / {"webui": "webui-integrations.py",
                       "recipient": "configure-pi.py"}[kind]), max_bytes=LIMIT) == source)
        summary = {"schema": WORKER_SCHEMA, "configuration_status": result["configuration_status"],
                   "installed_sha256": result["installed_sha256"], "clients_count": len(result["clients"]),
                   "recipients_staged": staged, "providers_modified": False, "live_status": "unqualified"}
        if "client_id" in ack:
            auth_result = _native_readback(store, {"action": "client-readback", "store_path": str(store.path),
                "declaration_path": path, "declaration_sha256": run_id,
                "router_config_sha256": ack["router_config_sha256"],
                "client_id": ack["client_id"], "credential": ack["credential"]})
            summary.update({k: auth_result[k] for k in ("credential_validated", "grant_sha256")})
        print(json.dumps({"finalized": "clients", "run_id": run_id, "result": summary}), flush=True)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid managed client command")


def dispatch(argv=None):
    try:
        parser = _Parser(prog="anvil-serving router clients")
        parser.add_argument("action", choices=("preview", "install", "readback"))
        parser.add_argument("--config", default=None)
        parser.add_argument("--compose", default=None)
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        args = parser.parse_args(argv)
        path = args.config or _default()
        if args.compose is not None:
            namespace = _namespace(path)
            _require(namespace is not None and os.path.isabs(args.compose))
            namespace = {**namespace, "compose": args.compose}
            result = _managed(args.action, path, namespace, confirm=args.confirm, dry_run=args.dry_run)
        else:
            result = run(args.action, path, confirm=args.confirm, dry_run=args.dry_run)
        print(canonical(result).decode())
        return 0
    except (ValueError, OSError, sqlite3.Error, KeyStoreError):
        print("anvil-serving router clients: configuration/enrollment failed; readback required", file=sys.stderr)
        return 2
