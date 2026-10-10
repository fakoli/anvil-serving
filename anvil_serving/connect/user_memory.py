"""Create protected user banks before publishing access; never delete memories."""
from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path
from urllib.parse import quote

from .. import memory_access
from ..control_plane.mcp.auth_file import read_private_auth_file
from . import manage, memory_backend

DEFENSE = {"enabled": True, "rules": [{"on": "sensitive_data", "action": "block"}]}


def provision(data: dict, username: str, subject: str, *, admin: bool, publish: bool = True) -> dict:
    try:
        return _provision(data, username, subject, admin=admin, publish=publish)
    except manage.ManageError:
        raise
    except Exception:
        raise manage.ManageError("User memory provisioning failed; bank access remains closed. Retry users memory or users access after checking the bank service.") from None


def _provision(data: dict, username: str, subject: str, *, admin: bool, publish: bool) -> dict:
    """Called under Connect's deployment lock, using the reserved OIDC identity.

    A durable intent and bank name marker distinguish a retry from adoption of
    someone else's bank. Uncreated random bank IDs stay in private receipts.
    Shared overrides are verified, never created or taken into ownership.
    """
    from .users import _principal
    config = data["memory"]
    principal = _principal(data["gateway"]["oidc"]["issuer"], subject)
    marker = "anvil-connect:" + principal
    path = Path(config["access_file"])
    manage._safe_root_ancestors(path)
    info = path.parent.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid() or info.st_gid != config["reader_gid"]
            or stat.S_IMODE(info.st_mode) != 0o750):
        raise ValueError("install a dedicated protected memory policy directory first")
    receipt = path.parent / ("." + principal.removeprefix("human:") + ".creation.json")
    raw_receipt = read_private_auth_file(str(receipt), max_bytes=4096) if os.path.lexists(receipt) else None
    if raw_receipt is not None:
        saved = manage._strict_json(raw_receipt, "invalid memory creation receipt")
        if (type(saved) is not dict or set(saved) != {"schema", "principal", "bank", "name"}
                or saved["schema"] != "anvil-memory-creation/v1" or saved["principal"] != principal
                or saved["name"] != marker or type(saved["bank"]) is not str
                or not memory_access.BANK.fullmatch(saved["bank"]) or not saved["bank"].startswith("user-")):
            raise ValueError("memory creation receipt does not match this account")
        bank = saved["bank"]
    else:
        bank = "user-" + secrets.token_hex(24)
    default = config["default_banks"].get(username, bank)
    shared_banks = dict(config["shared_banks"].get(username, {}))
    if default != bank and default not in shared_banks:
        raise ValueError("default shared bank requires explicit operation grants")
    token = read_private_auth_file(config["auth_file"], max_bytes=8192).decode().strip()
    if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("memory credential is unavailable")

    def request(target, method="GET", body=None):
        raw = memory_backend.request(config["base_url"].rstrip("/") + target,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
            timeout=30, max_bytes=memory_access.MAX_BYTES, method=method)
        return manage._strict_json(raw, "memory provisioning response is invalid")

    found = None
    inventory_banks = set()
    offset = 0
    while offset <= 4096:
        inventory = request("/v1/default/banks?limit=256&offset=" + str(offset))
        entries = inventory.get("banks")
        if type(entries) is not list or len(entries) > 256:
            raise ValueError("memory bank inventory is invalid")
        for entry in entries:
            inventory_banks.add(entry["bank_id"])
            if entry.get("bank_id") == bank:
                found = entry
        offset += len(entries)
        if len(entries) < 256 or offset >= inventory.get("total", 2**63):
            break
    else:
        raise ValueError("memory bank inventory is too large")
    target = "/v1/default/banks/" + quote(bank, safe="")
    if set(shared_banks) - inventory_banks:
        raise ValueError("configured shared memory bank does not exist")
    if bank in shared_banks:
        raise ValueError("personal bank cannot be configured as shared")
    expected = {"schema": "anvil-memory-creation/v1", "principal": principal, "bank": bank, "name": marker}
    if raw_receipt is not None:
        if manage._strict_json(raw_receipt, "invalid memory creation receipt") != expected:
            raise ValueError("memory creation receipt does not match this account")
    else:
        if found is not None:
            raise ValueError("refusing to adopt an existing user memory bank")
        manage._write_atomic(receipt, json.dumps(expected).encode(), 0o600)
    if found is None:
        request(target, "PUT", {"name": marker})
    elif found.get("name") != marker:
        raise ValueError("user memory bank ownership marker does not match")
    request(target + "/config", "PATCH", {"updates": {"memory_defense": DEFENSE}})
    if request(target + "/config").get("config", {}).get("memory_defense") != DEFENSE:
        raise ValueError("user memory protection did not persist")
    # Complete metadata readback also covers a lost successful PUT response.
    check = request("/v1/default/banks?q=" + quote(bank, safe="") + "&limit=256&offset=0")
    if not any(entry.get("bank_id") == bank and entry.get("name") == marker for entry in check.get("banks", [])):
        raise ValueError("user memory bank creation did not persist")
    prepared = {"principal": principal, "grant": {"default_bank": default, "admin": admin,
                "banks": {bank: sorted(memory_access.OPERATIONS), **shared_banks}},
                "bank": bank, "default_bank": default, "shared": default != bank, "ready": True}
    if publish:
        publish_grant(data, prepared)
        return {key: prepared[key] for key in ("bank", "default_bank", "shared", "ready")}
    return prepared


def _publish(data, policy):
    path = Path(data["memory"]["access_file"])
    memory_access.validate(policy)
    mode = 0o640
    gid = data["memory"]["reader_gid"]
    # Preserve the reader group when atomically replacing the policy inode.
    payload = (json.dumps(policy, sort_keys=True, indent=2) + "\n").encode()
    temporary = path.with_name("." + path.name + ".publish")
    if os.path.lexists(temporary):
        raise ValueError("memory policy publication requires recovery")
    try:
        manage._write_atomic(temporary, payload, mode)
        os.chown(temporary, os.geteuid(), gid)
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def publish_grant(data, prepared):
    path = Path(data["memory"]["access_file"])
    policy = memory_access.read(str(path)) if path.exists() else {"schema": memory_access.SCHEMA, "users": {}}
    policy["users"][prepared["principal"]] = prepared["grant"]
    _publish(data, policy)
    return {key: prepared[key] for key in ("bank", "default_bank", "shared", "ready")}


def quarantine(data, subject):
    from .users import _principal
    quarantine_principal(data, _principal(data["gateway"]["oidc"]["issuer"], subject))


def quarantine_principal(data, principal):
    if type(principal) is not str or not memory_access.HUMAN.fullmatch(principal):
        raise ValueError("invalid memory principal")
    path = Path(data["memory"]["access_file"])
    if path.exists():
        policy = memory_access.read(str(path))
        if principal in policy["users"]:
            del policy["users"][principal]
            _publish(data, policy)


def configure(manifest: str, input_file: str, *, apply: bool = False) -> dict:
    """Install non-secret bank settings from protected, outside-Git input."""
    from .config import _json_load, read_manifest, validate_manifest
    from .users import _read_users, _database, _require_root
    _require_root()
    source = Path(input_file)
    if any((parent / ".git").exists() for parent in source.parents):
        raise ValueError("memory account mappings must be outside Git")
    declaration = _json_load(read_private_auth_file(str(source), max_bytes=memory_access.MAX_BYTES).decode())
    if type(declaration) is not dict or set(declaration) != {"memory", "user_defaults"} or type(declaration["user_defaults"]) is not list:
        raise ValueError("invalid memory installation declaration")
    data = read_manifest(manifest)
    raw, _ = _read_users(data)
    accounts = _database(raw)["users"]
    desired = validate_manifest({**data, "memory": declaration["memory"]})["memory"]
    for override in declaration["user_defaults"]:
        if type(override) is not dict or set(override) != {"email", "bank", "operations"} or type(override["email"]) is not str:
            raise ValueError("invalid memory default mapping")
        matches = [user for user, account in accounts.items() if account["email"].casefold() == override["email"].casefold() and not account.get("disabled", False)]
        if len(matches) != 1:
            raise ValueError("memory default must match exactly one enabled account")
        user = matches[0]
        if user in desired["default_banks"] or user in desired["shared_banks"]:
            raise ValueError("duplicate memory default mapping")
        desired["default_banks"][user] = override["bank"]
        desired["shared_banks"][user] = {override["bank"]: override["operations"]}
    validated = validate_manifest({**data, "memory": desired})
    path = Path(validated["memory"]["access_file"])
    result = {"applied": False, "account_defaults": len(desired["default_banks"]), "existing_users_require_memory_provisioning": True}
    if not apply:
        return result
    with manage._deployment_lock(Path(data["config_root"])):
        manage._require_no_authelia_upgrade(Path(data["config_root"]))
        if read_manifest(manifest) != data or _read_users(data)[0] != raw:
            raise ValueError("Connect declaration or accounts changed during preparation")
        manage._safe_root_ancestors(path.parent)
        if not os.path.lexists(path.parent):
            path.parent.mkdir(mode=0o750)
            os.chown(path.parent, os.geteuid(), desired["reader_gid"])
            os.chmod(path.parent, 0o750)
        info = path.parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_gid != desired["reader_gid"] or stat.S_IMODE(info.st_mode) != 0o750):
            raise ValueError("memory policy directory ownership differs")
        if os.path.lexists(path):
            memory_access.read(str(path))
            info = path.lstat()
            if info.st_uid != os.geteuid() or info.st_gid != desired["reader_gid"] or stat.S_IMODE(info.st_mode) != 0o640:
                raise ValueError("memory policy ownership differs from the configured reader group")
        else:
            _publish(validated, {"schema": memory_access.SCHEMA, "users": {}})
        manage._write_atomic(Path(manifest), (json.dumps(validated, indent=2, sort_keys=True) + "\n").encode(), 0o600)
    result["applied"] = True
    return result
