"""Read-only, allowlisted account and browser-resource discovery."""
from __future__ import annotations

from . import users


def resources(manifest: str) -> dict:
    """Read declarations only; never imply origin health or user access."""
    users._path(manifest)
    data = users.read_manifest(manifest)
    rows = []
    for resource in data.get("gateway", {}).get("gateway", {}).get("resources", []):
        rule = resource["rule"]
        if rule["access"] != "browser":
            continue
        rows.append({
            "id": rule["id"],
            "url": "https://" + rule["host"] + rule["path_prefix"],
            "native_auth": rule["native_auth"],
            "grants": [rule["id"] + ":member", rule["id"] + ":admin"],
        })
    return {"resources": sorted(rows, key=lambda row: row["id"]),
            "source": "deployment-declaration", "read_only": True,
            "note": "Declared browser resources only; not health or current user grants. Passthrough applications manage their own internal roles."}


def accounts(manifest: str, username: str | None = None) -> dict:
    """Reuse the private-file checks, and never return hashes or extra attributes."""
    users._require_root()
    if username is not None and (not isinstance(username, str) or not users._USER.fullmatch(username)):
        raise users._invalid("Use an exact lowercase local username; run users list to inspect accounts.")
    users._path(manifest)
    data = users.read_manifest(manifest)
    if "authelia" not in data:
        raise users._invalid("Account inventory requires an authentication-host deployment.")
    raw, _ = users._read_users(data)
    records = users._database(raw)["users"]
    if username is not None and username not in records:
        raise users._invalid("Account not found; run users list to inspect existing accounts.")
    names = [username] if username is not None else sorted(records)
    return {"users": [{"username": name, "email": records[name]["email"],
                       "groups": records[name].get("groups", []),
                       "disabled": records[name].get("disabled", False)} for name in names],
            "source": "authelia-file-backend", "read_only": True,
            "classification": "restricted-authentication",
            "note": "Local sign-in accounts only; groups and enabled status do not establish Connect grants. Inspect current grants through Manage access on the service home."}
