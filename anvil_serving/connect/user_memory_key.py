"""Protected personal router credentials owned by the user-bank configuration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

from . import manage
from ..control_plane.mcp.auth_file import read_private_auth_file
from .. import memory_access
from . import key_transport as key_container


def validate_identity(principal, generation, epoch):
    if (type(principal) is not str or not re.fullmatch(r"human:[0-9a-f]{64}", principal)
            or type(generation) is not str or not re.fullmatch(r"[1-9][0-9]{0,19}", generation)
            or int(generation) >= 2**64 or type(epoch) is not str or not re.fullmatch(r"[0-9a-f]{64}", epoch)):
        raise manage.ManageError("Native router identity is invalid")
    return principal, generation, epoch


def validate(value):
    from .config import _abs_path, _mapping, _error
    value = _mapping(value, "$.memory.router", {"container", "store_path", "key_directory"})
    if type(value["container"]) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value["container"]):
        raise _error("$.memory.router.container", "invalid managed container name")
    return {"container": value["container"], **{name: _abs_path(value[name], "$.memory.router." + name)
            for name in ("store_path", "key_directory")}}


def provision(data, manifest, username, subject, *, apply, runner=None):
    """The caller holds the deployment lock and has verified the local account."""
    from .users import _human_admin_read, _principal
    config = data["memory"]["router"]
    principal = _principal(data["gateway"]["oidc"]["issuer"], subject)
    default, grant = memory_access.resolve(data["memory"]["access_file"], principal, "banks")
    operators = data["gateway"]["gateway"].get("browser_administration", {}).get("operators", [])
    operation = "operator-inspect" if principal in operators else "human-inspect"
    native = _human_admin_read(data, manifest, {"operation": operation, "principal": principal}, runner)
    human = native.get("human", {})
    if (native.get("operation") != operation or native.get("found") is not True
            or human.get("id") != principal or human.get("username") != username
            or human.get("disabled") is not False or human.get("deletion_request")
            or type(human.get("generation")) is not int or not 1 <= human["generation"] < 2**64):
        raise manage.ManageError("Enabled native account identity is required for its memory key")
    directory = Path(config["key_directory"])
    manage._safe_root_ancestors(directory)
    if any((parent / ".git").exists() for parent in (directory, *directory.parents)):
        raise manage.ManageError("Memory keys must be outside Git")
    if not directory.exists() and apply:
        directory.mkdir(mode=0o700)
    if directory.exists():
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise manage.ManageError("Memory key directory must be root-owned mode 0700")
    path = directory / (principal.removeprefix("human:") + ".json")
    saved = None
    if os.path.lexists(path):
        saved = manage._strict_json(read_private_auth_file(str(path), max_bytes=4096), "Invalid protected memory key")
        if (type(saved) is not dict or set(saved) != {"schema", "principal", "generation", "epoch", "key_id", "secret", "expires_at", "previous"}
                or saved["schema"] != "anvil-user-memory-key/v1" or saved["principal"] != principal
                or type(saved["secret"]) is not str or not re.fullmatch(r"ask_[A-Za-z0-9_-]{43}", saved["secret"])
                or (saved["key_id"] is not None and (type(saved["key_id"]) is not str or not re.fullmatch(r"key_[0-9a-f]{16}", saved["key_id"])))
                or (saved["expires_at"] is not None and type(saved["expires_at"]) is not int)):
            raise manage.ManageError("Protected memory key identity differs")
        validate_identity(saved["principal"], saved["generation"], saved["epoch"])
        previous = saved["previous"]
        if previous is not None and (type(previous) is not dict or set(previous) != {"key_id", "secret", "retired"}
                or (previous["key_id"] is not None and (type(previous["key_id"]) is not str or not re.fullmatch(r"key_[0-9a-f]{16}", previous["key_id"])))
                or type(previous["secret"]) is not str or not re.fullmatch(r"ask_[A-Za-z0-9_-]{43}", previous["secret"])
                or type(previous["retired"]) is not bool or previous["secret"] == saved["secret"]):
            raise manage.ManageError("Previous managed memory key receipt is invalid")
    result = {"key_file": str(path), "default_bank": default, "created": False, "applied": apply}
    if not apply:
        return {**result, "would_create": saved is None,
            "would_rotate": saved is not None and (saved["generation"] != str(human["generation"])
                or saved["expires_at"] is not None and saved["expires_at"] <= time.time() + 3600),
            "router_validation": "deferred until confirm"}
    identity = _human_admin_read(data, manifest, {"operation": "human-router-identity", "principal": principal,
        "expected_generation": human["generation"]}, runner)
    actor = validate_identity(principal, str(human["generation"]), identity.get("epoch"))
    if (identity.get("operation") != "human-router-identity" or identity.get("principal") != principal
            or identity.get("generation") != human["generation"]):
        raise manage.ManageError("Native router identity changed")
    container = key_container._container_id(config["container"], config["store_path"])
    def retire(previous):
        retired = key_container._invoke(container, {"action": "memory-key-retire", "store_path": config["store_path"],
            "principal": principal, **{key: previous[key] for key in ("key_id", "secret")}})
        if retired != {"data": {"key_id": previous["key_id"], "revoked": True}}:
            raise manage.ManageError("Previous managed key retirement was not verified")
        previous["retired"] = True

    if saved and saved["previous"] and not saved["previous"]["retired"]:
        retire(saved["previous"])
        manage._write_atomic(path, (json.dumps(saved) + "\n").encode(), 0o600)
    # Persist a generated token before the router sees it. Repeating this exact
    # request recovers a successful creation whose pipe response was lost.
    rotate = saved is not None and ((saved["generation"], saved["epoch"]) != actor[1:]
        or saved["expires_at"] is not None and saved["expires_at"] <= time.time() + 3600)
    if saved is None or rotate:
        previous = {**{key: saved[key] for key in ("key_id", "secret")}, "retired": False} if saved else None
        saved = {"schema": "anvil-user-memory-key/v1", "principal": principal, "generation": actor[1],
            "epoch": actor[2], "key_id": None, "secret": "ask_" + secrets.token_urlsafe(32),
            "expires_at": None, "previous": previous}
        manage._write_atomic(path, (json.dumps(saved) + "\n").encode(), 0o600)
    response = key_container._invoke(container, {"action": "memory-key", "store_path": config["store_path"],
        "principal": principal, "generation": actor[1], "epoch": actor[2], "grant": grant,
        "secret": saved["secret"], "expected_key_id": saved["key_id"], "apply": True})
    metadata = response["data"]
    created = metadata.get("created") is True
    if key_container._container_id(config["container"], config["store_path"]) != container:
        raise manage.ManageError("Router changed during memory key provisioning; retry memory-key")
    if ("secret" in response or type(metadata.get("key_id")) is not str
            or not re.fullmatch(r"key_[0-9a-f]{16}", metadata["key_id"])
            or type(metadata.get("expires_at")) is not int
            or saved["key_id"] not in (None, metadata["key_id"])):
        raise manage.ManageError("Saved memory key readback differs")
    completed = {**saved, "key_id": metadata["key_id"], "expires_at": metadata["expires_at"]}
    if completed != saved:
        manage._write_atomic(path, (json.dumps(completed) + "\n").encode(), 0o600)
    if completed["previous"] and not completed["previous"]["retired"]:
        retire(completed["previous"])
        manage._write_atomic(path, (json.dumps(completed) + "\n").encode(), 0o600)
    if key_container._container_id(config["container"], config["store_path"]) != container:
        raise manage.ManageError("Router changed during memory key completion; retry memory-key")
    return {**result, "created": created, "key_id": metadata["key_id"], "expires_at": metadata["expires_at"]}


def operate(manifest, username, *, apply=False, runner=None):
    from . import users
    from .config import read_manifest
    from .render import _authelia
    users._require_root()
    if type(username) is not str or not users._USER.fullmatch(username):
        raise manage.ManageError("An exact Connect username is required")
    data = read_manifest(manifest)
    if "router" not in data.get("memory", {}):
        raise manage.ManageError("Configure memory.router before creating personal memory keys")
    root = Path(data["config_root"])
    with manage._deployment_lock(root):
        manage._require_no_authelia_upgrade(root)
        if read_manifest(manifest) != data:
            raise manage.ManageError("Connect configuration changed")
        raw, _ = users._read_users(data)
        account = users._database(raw)["users"].get(username)
        if account is None or account.get("disabled", False):
            raise manage.ManageError("An enabled Connect account is required")
        config = root / "authelia/configuration.yml"
        if (manage._read_regular(config, users._MAX_FILE) != _authelia(data).encode()
                or manage._digest(Path(data["components"]["authelia"])) != manage._component_lock()["authelia"]):
            raise manage.ManageError("Installed identity provider differs from its declaration")
        subject = users._oidc_subject(data, config, username, runner)
        return provision(data, manifest, username, subject, apply=apply, runner=runner)
