"""Small, local Authelia account operations; no new authentication protocol."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parseaddr
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import uuid

from ..operator_output import UsageError
from . import manage
from .config import _json_load, read_manifest, role_identity
from .render import _authelia
from .recovery import _path

DEFAULT_MANIFEST = "/etc/anvil-connect/deployment.json"
_USER = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_UNIT = "anvil-connect-authelia.service"
_MAX_FILE = 1024 * 1024
_RESOURCE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_ROLE = frozenset(("member", "admin"))
_CODE = re.compile(
    r"(?ms)^A ONE-TIME CODE HAS BEEN GENERATED TO COMPLETE A REQUESTED ACTION\n"
    r".*?^----------------------------------------\n\n"
    r"([ABCDEFGHJKLMNPQRTUVWYXZ2346789]{8})\n\n----------------------------------------(?:\n|$)"
)


def _invalid(message: str) -> UsageError:
    return UsageError(message, code="connect_users_invalid")


def _partial(message: str, result: dict) -> manage.ManageError:
    error = manage.ManageError(message, may_have_executed=True)
    error.recovery = {key: result[key] for key in ("operation", "username", "backup", "gateway_backup", "handoff_file", "classification", "destination", "sha256", "existing_connect_sessions_revoked", "account_disabled", "account_deleted") if key in result}
    return error


def _require_root() -> None:
    if os.geteuid() != 0:
        raise _invalid("Run Connect user administration with sudo on the authentication host.")


def _scalar(value: str):
    """Only unambiguous scalars from the provisioned file format are accepted."""
    if value.startswith(('"', '[')) or value in {"true", "false"}:
        return _json_load(value)
    if re.fullmatch(r"'([^']|'')*'", value):
        return value[1:-1].replace("''", "'")
    if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_.$/@+=,-]*", value) and value.lower() not in {
        "yes", "no", "on", "off", "null", "true", "false",
    }:
        return value
    raise _invalid("Unsupported users-file YAML; use simple mappings or JSON-compatible YAML.")


def _database(raw: bytes) -> dict:
    text = raw.decode("utf-8")
    if text.lstrip().startswith("{"):
        data = _json_load(text)
    else:
        # ponytail: accept only the documented simple user-file layout. Refuse
        # YAML aliases/tags/multiline values; use a YAML library if needed later.
        data = {"users": {}}
        current = None
        field_indent = None
        groups = False
        header = False
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#") or line in {"---", "..."}:
                continue
            if line == "users:" and not header:
                header = True
                continue
            match = re.fullmatch(r"( +)([a-z][a-z0-9_.-]*):(?: (.*))?", line)
            if match and header:
                indent, key, value = len(match[1]), match[2], match[3]
                if value is None and (field_indent is None or indent == field_indent - 2):
                    if key in data["users"] or not _USER.fullmatch(key):
                        raise _invalid("Duplicate or unsupported username in users file.")
                    current = data["users"][key] = {}
                    field_indent = indent + 2
                    groups = False
                    continue
                if current is not None and indent == field_indent and key not in current:
                    groups = key == "groups" and value is None
                    current[key] = [] if groups else _scalar(value or "")
                    continue
            if groups and re.fullmatch(r" {" + str(field_indent + 2) + r"}- .+", line):
                current["groups"].append(_scalar(line.strip()[2:]))
                continue
            raise _invalid("Unsupported users-file YAML; use simple mappings or JSON-compatible YAML.")
    if not isinstance(data, dict) or set(data) != {"users"} or not isinstance(data["users"], dict):
        raise _invalid("Expected an Authelia users mapping.")
    for name, user in data["users"].items():
        if not _USER.fullmatch(name) or not isinstance(user, dict):
            raise _invalid("Unsupported account record.")
        if set(user) - {"displayname", "password", "email", "groups", "disabled"}:
            raise _invalid("Account attributes outside the simple file backend are not supported by this command.")
        if any(not isinstance(user.get(field), str) for field in ("displayname", "password", "email")):
            raise _invalid("Account requires displayname, password hash and email.")
        if not user["password"].startswith(("$argon2", "$scrypt$", "$pbkdf2", "$2", "$5$", "$6$")):
            raise _invalid("Account password must be a supported hash.")
        if "disabled" in user and type(user["disabled"]) is not bool:
            raise _invalid("Account disabled flag must be boolean.")
        if not isinstance(user.get("groups", []), list) or any(not isinstance(g, str) for g in user.get("groups", [])):
            raise _invalid("Account groups must be strings.")
    return data


def _read_users(path: Path, uid: int, gid: int) -> tuple[bytes, os.stat_result]:
    manage._safe_consumed_file(path, uid, gid)
    info = path.lstat()
    if info.st_nlink != 1:
        raise _invalid("Users file must not have hard links.")
    raw = manage._read_regular(path, _MAX_FILE)
    if raw is None:
        raise _invalid("Users file is missing; provision the Authelia file backend first.")
    return raw, info


def _exclusive(path: Path, raw: bytes, uid: int | None = None, gid: int | None = None, mode: int = 0o600) -> tuple[int, int]:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    info = os.fstat(fd)
    inode = (info.st_dev, info.st_ino)
    try:
        if uid is not None and gid is not None:
            os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
    except BaseException:
        _remove_owned(path, inode)
        raise
    finally:
        os.close(fd)
    return inode


def _remove_owned(path: Path, inode: tuple[int, int]) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (info.st_dev, info.st_ino) == inode:
        path.unlink()


def _next_handoff(directory: Path, username: str, operation: str) -> Path:
    for number in range(1, 10001):
        path = directory / f"{username}-{operation}-{number:04d}.txt"
        if not os.path.lexists(path):
            return path
    raise _invalid("Handoff directory is full; remove delivered handoff files.")


def _replace_users(path: Path, raw: bytes, info: os.stat_result) -> None:
    temporary = path.with_name("." + path.name + ".users-" + secrets.token_hex(12))
    try:
        _exclusive(temporary, raw, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def _password(binary: str, runner) -> tuple[str, str]:
    result = manage._run(runner, (binary, "crypto", "hash", "generate", "argon2", "--random",
                                  "--random.length", "40", "--no-confirm"), 15)
    manage._fail(result, "Authelia password generation failed")
    output = result.stdout.decode("utf-8")
    passwords = re.findall(r"(?m)^Random Password: ([A-Za-z0-9]{40})\s*$", output)
    digests = re.findall(r"(?m)^Digest: (\$argon2id\$v=19\$m=\d+,t=\d+,p=\d+\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+)\s*$", output)
    if len(passwords) != 1 or len(digests) != 1:
        raise manage.ManageError("Authelia returned an unexpected password result")
    return passwords[0], digests[0]


def _notification(raw: bytes, email: str, now: datetime) -> str:
    text = raw.decode("utf-8").replace("\r\n", "\n")
    header = re.match(r"\ADate: (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:\.\d+)? ([+-]\d{4}) [^\n]+\nRecipient: ([^\n]+)\nSubject: Confirm your identity\n", text)
    if header is None or parseaddr(header[3])[1] != email:
        raise _invalid("No enrollment notification for this account; request a new code in Authelia first.")
    created = datetime.strptime(header[1] + " " + header[2], "%Y-%m-%d %H:%M:%S %z")
    if not 0 <= (now - created).total_seconds() < 300:
        raise _invalid("Enrollment notification is stale; request a new code in Authelia.")
    matches = _CODE.findall(text)
    if len(matches) != 1:
        raise _invalid("Enrollment notification format is not recognized.")
    return matches[0]


def _grant_map(grants: list[str] | None, data: dict) -> dict[str, str] | None:
    """Turn the CLI's complete desired grant list into native human-set input."""
    if grants is None:
        return None
    if not isinstance(grants, list) or not grants or len(grants) > 64:
        raise _invalid("Provide between one and 64 --grant resource:member or resource:admin values.")
    declared = {item["rule"]["id"] for item in data["gateway"]["gateway"]["resources"] if item["rule"]["access"] == "browser"}
    result: dict[str, str] = {}
    for value in grants:
        if not isinstance(value, str) or value.count(":") != 1:
            raise _invalid("Use --grant resource:member or resource:admin.")
        resource, role = value.split(":")
        if not _RESOURCE.fullmatch(resource) or role not in _ROLE or resource not in declared or resource in result:
            raise _invalid("Grants must name unique declared browser resources and member or admin roles.")
        result[resource] = role
    return {resource: result[resource] for resource in sorted(result)}


def _service_home(data: dict, grants: dict[str, str] | None) -> str | None:
    if not grants:
        return None
    resources = {item["rule"]["id"]: item["rule"] for item in data["gateway"]["gateway"]["resources"]}
    rule = resources[next(iter(grants))]
    return "https://" + rule["host"] + rule["path_prefix"].rstrip("/") + "/_anvil-connect/home"


def _principal(issuer: str, subject: str) -> str:
    return "human:" + hashlib.sha256((issuer + "\0" + subject).encode()).hexdigest()


def _identifier_value(value: str) -> str:
    if value.startswith('"'):
        parsed = _json_load(value)
        if isinstance(parsed, str):
            return parsed
    if re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        return value
    raise _invalid("Authelia opaque-identifier export is invalid.")


def _identifier_subject(raw: bytes, username: str, *, missing_ok: bool = False) -> str | None:
    """Read exactly the one OIDC identifier this account is entitled through."""
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise _invalid("Authelia opaque-identifier export is invalid.") from exc
    records: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    header = False
    list_indent = None
    for line in lines:
        if not line or line.startswith("#") or line in {"---", "..."}:
            continue
        if line in {"identifiers:", "identifiers: []"} and not header:
            header = True
            continue
        match = re.fullmatch(r"( +)- ([a-z_]+): (.*)", line)
        if match and header:
            if list_indent is None:
                list_indent = len(match[1])
            if len(match[1]) != list_indent:
                raise _invalid("Authelia opaque-identifier export is invalid.")
            if current is not None:
                records.append(current)
            current = {match[2]: _identifier_value(match[3])}
            continue
        match = re.fullmatch(r"( +)([a-z_]+): (.*)", line)
        if match and current is not None and len(match[1]) == list_indent + 2 and match[2] not in current:
            current[match[2]] = _identifier_value(match[3])
            continue
        raise _invalid("Authelia opaque-identifier export is invalid.")
    if current is not None:
        records.append(current)
    # The rendered OIDC client omits sector_identifier_uri, so only Authelia's
    # blank-sector OpenID identifier is the subject Connect will receive.
    matches = [record["identifier"] for record in records if set(record) == {"service", "sector_id", "username", "identifier"} and record["username"] == username and record["service"] == "openid" and record["sector_id"] == ""]
    if header and not records and missing_ok:
        return None
    if not matches and missing_ok and records and all(set(record) == {"service", "sector_id", "username", "identifier"} for record in records):
        return None
    if len(matches) != 1:
        raise _invalid("The account has no unambiguous OpenID Connect subject; sign in once or repair its Authelia identifier.")
    try:
        subject = uuid.UUID(matches[0])
    except ValueError as exc:
        raise _invalid("Authelia opaque-identifier export is invalid.") from exc
    if subject.version != 4 or str(subject) != matches[0].lower():
        raise _invalid("Authelia opaque-identifier export is invalid.")
    return str(subject)


def _oidc_subject(data: dict, config: Path, username: str, runner, *, missing_ok: bool = False) -> str | None:
    """Export all opaque identifiers privately, then discard the PII-bearing file."""
    state = Path(data["authelia"]["state_directory"])
    if missing_ok and not os.path.lexists(state / "authelia.sqlite3"):
        return None
    uid, gid = role_identity(data, "idp")
    manage._safe_private_runtime_directory(state, uid, gid)
    directory = Path(tempfile.mkdtemp(prefix=".anvil-connect-identifiers-", dir=state))
    os.chown(directory, uid, gid)
    os.chmod(directory, 0o700)
    export = directory / "identifiers.yml"
    try:
        result = manage._run(runner, (data["components"]["authelia"], "storage", "user", "identifiers", "export", "--file", str(export), "--config", str(config), "--config.experimental.filters", "template"), 15, manage._role_service_identity(data, "idp"))
        manage._fail(result, "Authelia opaque-identifier export failed")
        info = export.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != uid or info.st_gid != gid):
            raise _invalid("Authelia opaque-identifier export has unsafe ownership or permissions.")
        raw = manage._read_regular(export, _MAX_FILE)
        if raw is None:
            raise _invalid("Authelia opaque-identifier export is unavailable.")
        return _identifier_subject(raw, username, missing_ok=missing_ok)
    finally:
        try:
            export.unlink()
        except FileNotFoundError:
            pass
        try:
            directory.rmdir()
        except FileNotFoundError:
            pass


def _human_set(data: dict, manifest: str, subject: str, grants: dict[str, str], runner) -> dict:
    """Use the pinned same-user native admin socket; never touch Connect storage."""
    return _human_admin(data, manifest, {"operation": "human-set", "subject": subject,
                                       "resources": list(grants), "application_roles": grants}, runner)


def _human_admin(data: dict, manifest: str, payload: dict, runner) -> dict:
    state = Path(data["gateway"]["state_directory"])
    uid, gid = role_identity(data, "gateway")
    manage._safe_private_runtime_directory(state, uid, gid)
    request = state / (".anvil-connect-human-" + secrets.token_hex(12) + ".json")
    inode = _exclusive(request, (json.dumps({**payload, "issuer": data["gateway"]["oidc"]["issuer"]}) + "\n").encode(), uid, gid)
    try:
        return manage.admin(manifest, request_path=request, apply=True, runner=runner)
    finally:
        _remove_owned(request, inode)


def operate(manifest: str, operation: str, username: str | None, *, email: str | None = None, role: str | None = None,
            output: str | None = None, grants: list[str] | None = None, apply: bool = False, runner=None,
            unit_root: Path = Path("/etc/systemd/system"), include_gateway: bool = False) -> dict:
    _require_root()
    if operation not in {"create", "access", "suspend", "delete", "reset-password", "reset-mfa", "code", "backup"}:
        raise _invalid("Unsupported account operation.")
    if operation == "backup" and username is not None:
        raise _invalid("Authentication backup includes all accounts; omit the username.")
    if include_gateway and operation != "backup":
        raise _invalid("--include-gateway is only accepted for authentication backup.")
    if operation != "backup" and (not isinstance(username, str) or not _USER.fullmatch(username)):
        raise _invalid("Use a lowercase username containing letters, digits, dots, underscores or hyphens.")
    if operation == "create":
        if not isinstance(email, str) or not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", email):
            raise _invalid("Account creation requires a valid --email for Authelia identity verification.")
        role = role or "member"
        if role not in _ROLE:
            raise _invalid("Choose --role member or --role admin.")
    elif email is not None:
        raise _invalid("--email is only accepted for account creation.")
    elif role is not None:
        raise _invalid("--role is only accepted for account creation; resets preserve existing groups.")
    if operation not in {"create", "reset-password", "code"} and output is not None:
        raise _invalid("This operation does not produce a credential handoff file.")
    _path(manifest)
    data = read_manifest(manifest)
    auth = data["authelia"]
    uid, gid = role_identity(data, "idp")
    path, root = Path(auth["users_file"]), Path(data["config_root"])
    raw, info = _read_users(path, uid, gid)
    database = _database(raw)
    users = database["users"]
    if operation != "backup" and (operation == "create") == (username in users):
        raise _invalid("Account already exists; use reset-password." if operation == "create" else "Account does not exist.")
    if operation == "create" and any(u["email"].casefold() == email.casefold() for u in users.values()):
        raise _invalid("Email is already assigned to another account.")
    if operation not in {"create", "backup", "access", "suspend", "delete"} and users[username].get("disabled", False):
        raise _invalid("Account is disabled; this command does not enable accounts or grant access.")
    grant_map = _grant_map(grants, data)
    if operation not in {"create", "access"} and grant_map is not None:
        raise _invalid("--grant is only accepted for account creation or access provisioning.")
    if operation == "access" and grant_map is None:
        raise _invalid("Access provisioning requires at least one --grant.")
    destination = (_path(output) if output else _next_handoff(Path(manifest).parent / "handoffs", username or "all", operation)) if operation in {"create", "reset-password", "code"} else None
    result = {"operation": operation, "username": username, "applied": False,
              "grants_changed": False, "existing_connect_sessions_revoked": False,
              "force_password_change": False,
              "authelia_restart_if_active": operation not in {"code", "access"}, "classification": "restricted-authentication"}
    if grant_map is not None:
        result["resources"] = list(grant_map)
        result["application_roles"] = grant_map
    if operation == "create":
        result["role"] = role
        result["groups"] = ["admins" if role == "admin" else "members"]
    if operation == "access":
        result["impact"] = "Replaces the complete browser grant list and enables the account; invalidates existing Connect browser and terminal credentials. Resuming a suspended account briefly restarts Authelia."
        result["authelia_restart_if_active"] = users[username].get("disabled", False)
    if operation not in {"code", "access"}:
        result["impact"] = "If active, briefly stops Authelia and clears its in-memory sessions; existing Connect sessions and API keys are not revoked."
        result["backup_directory"] = str(Path(manifest).parent / "backups")
    if operation in {"suspend", "delete"}:
        result["impact"] = "Disables sign-in and invalidates Connect browser and human-approved terminal credentials. Briefly restarts Authelia if active. Separately issued API keys and application data remain unchanged."
        result["account_disabled"] = False
        result["account_deleted"] = False
        if operation == "delete":
            result["impact"] += " Removes the account and registered factors; retained OIDC identifiers reserve the username. Backups and disabled authority history remain."
    if include_gateway:
        result["include_gateway"] = True
        result["impact"] += " Then briefly stops only the native gateway for a sequential authority backup and restores its prior running state."
    if destination is not None:
        result["handoff_file"] = str(destination)
    if not apply:
        return result
    if operation == "access" and not users[username].get("disabled", False):
        with manage._deployment_lock(root):
            # Do not re-enable a human after a concurrent suspension/deletion.
            latest, _ = _read_users(path, uid, gid)
            if latest != raw:
                raise _invalid("Users file changed during preparation; retry the command.")
            config = root / "authelia/configuration.yml"
            manage._verify_owned_tree(root)
            if manage._read_regular(config, _MAX_FILE) != _authelia(data).encode():
                raise _invalid("Authelia configuration differs from this declaration; reconcile it before user administration.")
            if manage._digest(Path(data["components"]["authelia"])) != manage._component_lock()["authelia"]:
                raise _invalid("Authelia executable does not match the pinned component.")
            subject = _oidc_subject(data, config, username, runner)
            _human_set(data, manifest, subject, grant_map, runner)
        return {**result, "applied": True, "grants_changed": True, "existing_connect_sessions_revoked": True,
                "principal": _principal(data["gateway"]["oidc"]["issuer"], subject)}
    with manage._deployment_lock(root):
        manage._verify_owned_tree(root)
        config = root / "authelia/configuration.yml"
        if manage._read_regular(config, _MAX_FILE) != _authelia(data).encode():
            raise _invalid("Authelia configuration differs from this declaration; reconcile it before user administration.")
        binary = data["components"]["authelia"]
        if manage._digest(Path(binary)) != manage._component_lock()["authelia"]:
            raise _invalid("Authelia executable does not match the pinned component.")
        if operation == "create" and _oidc_subject(data, config, username, runner, missing_ok=True) is not None:
            raise _invalid("Username has a retained OpenID identifier; use a new username, or use users access to resume an existing suspended account.")
        if destination is not None:
            if output is None:
                manage._safe_root_ancestors(destination.parent)
                destination.parent.mkdir(mode=0o700, exist_ok=True)
            manage._safe_root_ancestors(destination)
            if any((ancestor / ".git").exists() for ancestor in destination.parents):
                raise _invalid("Credential handoff files must be outside Git workspaces.")
            parent = destination.parent.stat()
            if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
                raise _invalid("Handoff directory must be root-owned with mode 0700.")
            if os.path.lexists(destination):
                raise _invalid("Handoff output already exists; select a new file.")
        if operation == "code":
            notice = Path(auth["state_directory"]) / "notifications.txt"
            manage._safe_private_runtime_directory(notice.parent, uid, gid)
            notice_info = notice.lstat()
            if not stat.S_ISREG(notice_info.st_mode) or notice_info.st_nlink != 1 or notice_info.st_uid != uid or stat.S_IMODE(notice_info.st_mode) != 0o600:
                raise _invalid("Notification file has unsafe ownership or permissions.")
            code = _notification(manage._read_regular(notice, 32768) or b"", users[username]["email"], datetime.now(timezone.utc))
            _exclusive(destination, (code + "\n").encode())
            return {**result, "applied": True}
        source = manage._read_unit(root / "systemd" / _UNIT)
        if source is None or manage._unit_exec_path(source) != Path(binary):
            raise _invalid("Authelia unit does not match its declared executable.")
        manage._verify_unit(unit_root, _UNIT, source)
        manage._unit_metadata(runner, unit_root, (_UNIT,), present=True)
        active, _ = manage._unit_state(runner, _UNIT)
        result["authelia_was_active"] = active
        result["restarted"] = False
        password, digest = _password(binary, runner) if operation in {"create", "reset-password"} else (None, None)
        changed = False
        replaced = False
        stopped = False
        handoff_inode = None
        identity_attempted = False
        oidc_subject = None
        try:
            if active:
                stopped = True
                manage._action(runner, (manage._SYSTEMCTL, "stop", _UNIT), 30, "Authelia stop failed")
            if manage._unit_state(runner, _UNIT)[0]:
                raise manage.ManageError("Authelia is still active")
            # Read again after stopping the writer. Never overwrite a concurrent
            # self-service password change using the earlier preview snapshot.
            latest, info = _read_users(path, uid, gid)
            if latest != raw:
                raise _invalid("Users file changed during preparation; retry the command.")
            from .user_backup import snapshot
            result["backup"] = snapshot(data, manifest, users_raw=raw)
            if operation == "backup":
                pass
            elif operation in {"suspend", "delete", "access"}:
                subject = _oidc_subject(data, config, username, runner, missing_ok=operation != "access")
                if subject is not None:
                    changed = True
                    if operation == "access":
                        _human_set(data, manifest, subject, grant_map, runner)
                    else:
                        _human_admin(data, manifest, {"operation": "human-suspend", "subject": subject}, runner)
                    result["existing_connect_sessions_revoked"] = True
                    result["principal"] = _principal(data["gateway"]["oidc"]["issuer"], subject)
                changed = True
                users[username]["disabled"] = operation != "access"
                _replace_users(path, (json.dumps(database, indent=2, ensure_ascii=True) + "\n").encode(), info)
                result["account_disabled"] = operation != "access"
                result["grants_changed"] = operation == "access"
            if operation in {"reset-mfa", "delete"}:
                identity = manage._role_service_identity(data, "idp")
                for factor, extra in (("webauthn", ("--all",)), ("totp", ())):
                    changed = True
                    deletion = manage._run(runner, (binary, "storage", "user", factor, "delete", username, *extra,
                                                  "--config", str(config), "--config.experimental.filters", "template"),
                                           15, identity)
                    # The pinned provider reports an absent TOTP as an error;
                    # accept only that exact condition, never an arbitrary failure.
                    missing = f"Error: failed to delete TOTP configuration for user '{username}': no TOTP configuration for user"
                    first_line = deletion.stderr.decode("utf-8", "replace").splitlines()[:1]
                    if not (factor == "totp" and deletion.returncode == 1 and first_line == [missing]):
                        manage._fail(deletion, "Authelia factor reset failed")
                if operation == "delete":
                    del users[username]
                    _replace_users(path, (json.dumps(database, indent=2, ensure_ascii=True) + "\n").encode(), info)
                    result["account_deleted"] = True
            elif operation in {"create", "reset-password"}:
                if operation == "create":
                    users[username] = {"displayname": username, "email": email, "groups": result["groups"], "disabled": False}
                users[username]["password"] = digest
                service_home = _service_home(data, grant_map)
                handoff = f"Username: {username}\nPassword: {password}\nSign in: https://{auth['host']}\n"
                if service_home is not None:
                    handoff += f"Your services: {service_home}\n"
                handoff += "Change your password after signing in. This password does not automatically expire.\n"
                handoff_inode = _exclusive(destination, handoff.encode())
                changed = True
                _replace_users(path, (json.dumps(database, indent=2, ensure_ascii=True) + "\n").encode(), info)
                replaced = True
                if operation == "create" and grant_map is not None:
                    oidc_subject = str(uuid.uuid4())
                    identity_attempted = True
                    manage._action(runner, (binary, "storage", "user", "identifiers", "add", username,
                                           "--identifier", oidc_subject, "--service", "openid", "--sector", "",
                                           "--config", str(config), "--config.experimental.filters", "template"),
                                   15, "Authelia OpenID Connect identifier provisioning failed", manage._role_service_identity(data, "idp"))
            result["applied"] = True
        except BaseException as exc:
            if handoff_inode is not None and not identity_attempted:
                try:
                    _replace_users(path, raw, info)
                    _remove_owned(destination, handoff_inode)
                    replaced = False
                except BaseException as rollback_error:
                    raise _partial("Account rollback failed; inspect the retained backup and account state.", result) from rollback_error
            if changed or stopped or "backup" in result:
                raise _partial("User operation may have partially completed; inspect the retained backup and account state.", result) from exc
            raise
        finally:
            if stopped:
                try:
                    manage._action(runner, (manage._SYSTEMCTL, "start", _UNIT), 30, "Authelia restart failed")
                    if not manage._unit_state(runner, _UNIT)[0]:
                        raise manage.ManageError("Authelia did not become active", may_have_executed=True)
                    result["restarted"] = True
                except BaseException as exc:
                    try:
                        manage._action(runner, (manage._SYSTEMCTL, "stop", _UNIT), 30, "Authelia recovery stop failed")
                        if replaced and not identity_attempted:
                            _replace_users(path, raw, info)
                            if handoff_inode is not None:
                                _remove_owned(destination, handoff_inode)
                        manage._action(runner, (manage._SYSTEMCTL, "start", _UNIT), 30, "Authelia recovery start failed")
                        if not manage._unit_state(runner, _UNIT)[0]:
                            raise manage.ManageError("Authelia recovery did not become active")
                    except BaseException as recovery_error:
                        raise _partial("Authelia recovery failed; inspect service health and the retained backup.", result) from recovery_error
                    if operation in {"suspend", "delete", "access"}:
                        raise _partial("Authelia activation failed; account changes were retained and restart completed. Inspect the retained backup and account state.", result) from exc
                    if identity_attempted:
                        raise _partial("Authelia activation failed after account and OpenID Connect identity provisioning; restart completed. Inspect the retained backup before retrying.", result) from exc
                    raise _partial("Authelia activation failed; password rollback and restart completed. Inspect the retained backup before retrying.", result) from exc
        if not include_gateway:
            from .user_backup import prune
            try:
                result["backup"].update(prune(Path(result["backup"]["file"]).parent))
            except BaseException as exc:
                raise _partial("Account operation completed, but backup retention did not finish.", result) from exc
        if operation == "create" and grant_map is not None:
            try:
                _human_set(data, manifest, oidc_subject, grant_map, runner)
            except BaseException as exc:
                raise _partial("Account and OpenID Connect identity were created, but access provisioning may not have completed; inspect the account, then use users access with the intended grants to retry access provisioning.", result) from exc
            result.update({"grants_changed": True, "principal": _principal(data["gateway"]["oidc"]["issuer"], oidc_subject)})
        completed = result
    if include_gateway:
        try:
            from .gateway_backup import snapshot
            completed["gateway_backup"] = snapshot(data, manifest, completed["backup"], runner=runner, unit_root=unit_root)
            from .gateway_backup import prune
            gateway_root = Path(data["gateway"]["state_directory"]).parent / "gateway-backups"
            gateway_uid, _ = role_identity(data, "gateway")
            from .user_backup import prune as prune_auth
            with manage._deployment_lock(Path(data["config_root"])):
                completed["gateway_backup"].update(prune(Path(completed["backup"]["file"]).parent, gateway_root, gateway_uid))
                completed["backup"].update(prune_auth(Path(completed["backup"]["file"]).parent))
        except BaseException as exc:
            partial = {**completed, **getattr(exc, "recovery", {})}
            raise _partial("Authentication backup completed, but gateway authority backup failed; inspect the retained authentication backup and gateway state.", partial) from exc
    return completed
