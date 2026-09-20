"""Forward-only permanent-account deletion through native Connect authority."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import select
import stat
import time
import uuid
from datetime import datetime, timezone

from ..operator_output import UsageError
from . import manage, users
from .config import read_manifest, role_identity
from .recovery import _path
from .render import _authelia

_PHASE_SCHEMA = "anvil-connect.user-deletion/v1"
_PHASE_DIR = "user-deletions"
_PHASE_NAME = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.json$")
_MAX_PHASES = 1024


def _invalid(message: str) -> UsageError:
    return UsageError(message, code="connect_users_invalid")


def _request_id(value: object) -> str:
    if type(value) is not str:
        raise _invalid("Permanent deletion recovery state is invalid.")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise _invalid("Permanent deletion recovery state is invalid.") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise _invalid("Permanent deletion recovery state is invalid.")
    return value


def _phase_root(manifest: str) -> Path:
    root = Path(manifest).parent / _PHASE_DIR
    manage._safe_root_ancestors(root.parent)
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 2
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise _invalid("Permanent deletion recovery directory is unsafe.")
    return root


def _phase_path(root: Path, request_id: str) -> Path:
    return root / (_request_id(request_id) + ".json")


def _phase(value: object) -> dict:
    if type(value) is not dict or set(value) != {
        "schema", "request_id", "principal", "username", "subject", "generation", "original_active", "idp_delete",
    } or value.get("schema") != _PHASE_SCHEMA:
        raise _invalid("Permanent deletion recovery state is invalid.")
    _request_id(value["request_id"])
    if (not isinstance(value["principal"], str) or not re.fullmatch(r"human:[0-9a-f]{64}", value["principal"])
            or (value["username"] != "" and not users._USER.fullmatch(value["username"]))
            or (value["subject"] != "" and not _subject(value["subject"]))
            or type(value["generation"]) is not int or isinstance(value["generation"], bool) or value["generation"] < 1
            or type(value["original_active"]) is not bool or type(value["idp_delete"]) is not bool):
        raise _invalid("Permanent deletion recovery state is invalid.")
    if value["idp_delete"] != bool(value["subject"]) or (value["username"] == "" and value["idp_delete"]):
        raise _invalid("Permanent deletion recovery state is invalid.")
    return value


def _subject(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == value


def _write_phase(root: Path, value: dict) -> Path:
    value = _phase(value)
    path = _phase_path(root, value["request_id"])
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(raw) > 8192:
        raise _invalid("Permanent deletion recovery state is invalid.")
    if os.path.lexists(path):
        existing = _read_phase(path)
        if (json.dumps(existing, sort_keys=True, separators=(",", ":")) + "\n").encode() != raw:
            raise _invalid("Permanent deletion request conflicts with retained recovery state.")
        return path
    manage._write_atomic(path, raw, 0o600)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise _invalid("Permanent deletion recovery state is unsafe.")
    return path


def _read_phase(path: Path) -> dict:
    if _PHASE_NAME.fullmatch(path.name) is None:
        raise _invalid("Permanent deletion recovery state is invalid.")
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise _invalid("Permanent deletion recovery state is unsafe.")
    raw = manage._read_regular(path, 8192)
    if raw is None:
        raise _invalid("Permanent deletion recovery state is unavailable.")
    try:
        return _phase(manage._strict_json(raw, "Permanent deletion recovery state is invalid."))
    except manage.ManageError as exc:
        raise _invalid("Permanent deletion recovery state is invalid.") from exc


def _phases(root: Path) -> dict[str, tuple[Path, dict]]:
    values = {}
    for path in sorted(root.iterdir()):
        if _PHASE_NAME.fullmatch(path.name) is None:
            raise _invalid("Permanent deletion recovery directory is unsafe.")
        if len(values) >= _MAX_PHASES:
            raise _invalid("Permanent deletion recovery capacity is full.")
        value = _read_phase(path)
        if value["request_id"] in values:
            raise _invalid("Permanent deletion recovery state is invalid.")
        values[value["request_id"]] = (path, value)
    return values


def _human_response(data: dict, manifest: str, payload: dict, runner, operation: str) -> dict:
    response = users._human_admin_read(data, manifest, payload, runner)
    if type(response) is not dict or response.get("operation") != operation:
        raise _invalid("Native permanent deletion response is invalid.")
    return response


def _human(value: object) -> dict:
    if type(value) is not dict or set(value) - {"id", "username", "deletion_request", "generation", "disabled", "resources", "application_roles", "browser_transaction_floor"}:
        raise _invalid("Native permanent deletion response is invalid.")
    if (not isinstance(value.get("id"), str) or not re.fullmatch(r"human:[0-9a-f]{64}", value["id"])
            or not isinstance(value.get("username", ""), str) or (value.get("username") and not users._USER.fullmatch(value["username"]))
            or type(value.get("generation")) is not int or isinstance(value["generation"], bool) or value["generation"] < 1
            or type(value.get("disabled")) is not bool):
        raise _invalid("Native permanent deletion response is invalid.")
    return value


def _inspection(response: dict) -> dict | None:
    """Validate the native found/human union before choosing a prepare path."""
    # admin.Response deliberately keeps its stable generic fields for every
    # operation. Accept this exact zero-value envelope rather than a reduced
    # test-only object, while keeping the inspect-specific union closed.
    envelope = {
        "operation", "epoch", "secret", "key_id", "principal", "grants", "invitation",
        "installation", "role", "resources", "generation", "fingerprint", "status", "found",
    }
    expected_status = {"id", "status", "fingerprint", "epoch", "generation", "resources"}
    if (not isinstance(response.get("operation"), str) or response["operation"] != "human-inspect"
            or any(response.get(key) != "" for key in (
                "epoch", "secret", "key_id", "principal", "invitation", "installation", "role", "fingerprint"))
            or type(response.get("generation")) is not int or isinstance(response["generation"], bool)
            or response["generation"] != 0 or type(response.get("grants")) is not list or response["grants"] != []
            or type(response.get("resources")) is not list or response["resources"] != []
            or type(response.get("status")) is not dict or set(response["status"]) != expected_status
            or any(response["status"].get(key) != "" for key in ("id", "status", "fingerprint", "epoch"))
            or type(response["status"].get("generation")) is not int or isinstance(response["status"]["generation"], bool)
            or response["status"]["generation"] != 0 or response["status"].get("resources") != []):
        raise _invalid("Native permanent deletion response is invalid.")
    found = response.get("found")
    if type(found) is not bool:
        raise _invalid("Native permanent deletion response is invalid.")
    if found:
        if set(response) != envelope | {"human"}:
            raise _invalid("Native permanent deletion response is invalid.")
        return _human(response["human"])
    if set(response) != envelope:
        raise _invalid("Native permanent deletion response is invalid.")
    return None


def _deletion(value: object) -> dict:
    if type(value) is not dict or set(value) - {"request_id", "principal", "username", "generation", "epoch", "digest", "complete", "completed_at"}:
        raise _invalid("Native permanent deletion response is invalid.")
    completed_at = value.get("completed_at")
    try:
        parsed_completed_at = datetime.fromisoformat(completed_at.replace("Z", "+00:00")) if type(completed_at) is str else None
    except ValueError:
        parsed_completed_at = None
    if (_request_id(value.get("request_id")) != value["request_id"]
            or not isinstance(value.get("principal"), str) or not re.fullmatch(r"human:[0-9a-f]{64}", value["principal"])
            or (value.get("username", "") != "" and not users._USER.fullmatch(value["username"]))
            or type(value.get("generation")) is not int or isinstance(value["generation"], bool) or value["generation"] < 1
            or not isinstance(value.get("epoch"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["epoch"])
            or not isinstance(value.get("digest"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["digest"])
            or type(value.get("complete")) is not bool
            or (value["complete"] and parsed_completed_at is None)
            or (not value["complete"] and "completed_at" in value and parsed_completed_at != datetime.min.replace(tzinfo=timezone.utc))):
        raise _invalid("Native permanent deletion response is invalid.")
    return value


def _intent(data: dict, manifest: str, principal: str, generation: int, request_id: str, runner) -> dict:
    response = _human_response(data, manifest, {
        "operation": "human-delete-prepare", "principal": principal,
        "expected_generation": generation, "request_id": request_id,
    }, runner, "human-delete-prepare")
    intent = _deletion(response.get("deletion"))
    if (intent["request_id"] != request_id or intent["principal"] != principal or intent["complete"]):
        raise _invalid("Native permanent deletion response is invalid.")
    return intent


def _absent_intent(data: dict, manifest: str, principal: str, username: str, request_id: str, runner) -> dict:
    """Ask native authority to atomically fence an IdP-only principal."""
    response = _human_response(data, manifest, {
        "operation": "human-delete-prepare-absent", "principal": principal,
        "username": username, "request_id": request_id,
    }, runner, "human-delete-prepare-absent")
    intent = _deletion(response.get("deletion"))
    if (intent["request_id"] != request_id or intent["principal"] != principal
            or intent["username"] != username or intent["generation"] != 2 or intent["complete"]):
        raise _invalid("Native permanent deletion response is invalid.")
    return intent


def _intents(data: dict, manifest: str, runner) -> list[dict]:
    response = _human_response(data, manifest, {"operation": "human-deletions"}, runner, "human-deletions")
    values = response.get("deletions")
    if type(values) is not list or len(values) > _MAX_PHASES:
        raise _invalid("Native permanent deletion response is invalid.")
    result = [_deletion(value) for value in values]
    if len({value["request_id"] for value in result}) != len(result):
        raise _invalid("Native permanent deletion response is invalid.")
    return result


def _mapped_subject(data: dict, config: Path, principal: str, username: str, runner) -> str:
    issuer = data["gateway"]["oidc"]["issuer"]
    records = users._oidc_identifiers(data, config, runner, missing_ok=True)
    matches = [row for row in records if row["service"] == "openid" and row["sector_id"] == ""
               and users._principal(issuer, row["identifier"]) == principal]
    same_username = [row for row in records if row["service"] == "openid" and row["sector_id"] == "" and row["username"] == username]
    if username == "":
        if matches:
            raise _invalid("Native deletion metadata is incomplete for an IdP-linked account.")
        return ""
    if len(matches) != 1 or matches[0]["username"] != username or any(
            users._principal(issuer, row["identifier"]) != principal for row in same_username):
        raise _invalid("Authelia identifier does not match the native deletion intent.")
    return matches[0]["identifier"]


def _preflight(data: dict, manifest: str, runner) -> tuple[Path, bool]:
    root = Path(data["config_root"])
    manage._verify_owned_tree(root)
    config = root / "authelia/configuration.yml"
    if manage._read_regular(config, users._MAX_FILE) != _authelia(data).encode():
        raise _invalid("Authelia configuration differs from this declaration; reconcile it before user administration.")
    if manage._digest(Path(data["components"]["authelia"])) != manage._component_lock()["authelia"]:
        raise _invalid("Authelia executable does not match the pinned component.")
    active, _ = manage._unit_state(runner, users._UNIT)
    return config, active


def _phase_matches_intent(phase: dict, intent: dict) -> None:
    if any(phase[key] != intent[key] for key in ("request_id", "principal", "username", "generation")) or intent["complete"]:
        raise _invalid("Permanent deletion recovery state does not match the native intent.")


_PURGE_RESULT_KEYS = frozenset((
    "schema_version", "validated_only", "applied", "username_records", "opaque_identifiers", "oauth_sessions",
    "consent_sessions", "consent_preconfigurations", "expected_subject_found",
))


def _purge_as_idp(database: Path, username: str, subject: str, *, validate_only: bool, uid: int, gid: int) -> dict:
    """Run the pinned database adapter as the IdP account, with a bounded result."""
    if type(uid) is not int or type(gid) is not int or uid <= 0 or gid <= 0:
        raise _invalid("Authelia IdP identity is invalid.")
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    child = os.fork()
    if child == 0:  # pragma: no cover - parent asserts the closed child response.
        try:
            os.close(read_fd)
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
            from .user_purge import purge
            result = purge(database, username, expected_subject=subject, validate_only=validate_only, uid=uid, gid=gid)
            payload = {"ok": True, "result": result}
        except BaseException:
            payload = {"ok": False}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        try:
            if len(raw) <= 8192:
                offset = 0
                while offset < len(raw):
                    count = os.write(write_fd, raw[offset:])
                    if count <= 0:
                        break
                    offset += count
        finally:
            os._exit(0)
    os.close(write_fd)
    chunks: list[bytes] = []
    deadline = time.monotonic() + 15
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            readable, _, _ = select.select((read_fd,), (), (), min(remaining, 0.1))
            if readable:
                chunk = os.read(read_fd, 8193 - sum(len(item) for item in chunks))
                if not chunk:
                    break
                chunks.append(chunk)
                if sum(len(item) for item in chunks) > 8192:
                    raise ValueError
            waited, _ = os.waitpid(child, os.WNOHANG)
            if waited:
                child = 0
                # A closed pipe will be observed on the next loop iteration.
        if child:
            _, status = os.waitpid(child, 0)
            child = 0
            if status != 0:
                raise ValueError
    except (OSError, TimeoutError, ValueError):
        if child:
            try:
                os.kill(child, 9)
            except ProcessLookupError:
                pass
            os.waitpid(child, 0)
        raise _invalid("Authelia permanent deletion could not safely complete.") from None
    finally:
        os.close(read_fd)
    try:
        value = manage._strict_json(b"".join(chunks), "Authelia permanent deletion could not safely complete.")
    except manage.ManageError as exc:
        raise _invalid("Authelia permanent deletion could not safely complete.") from exc
    result = value.get("result")
    if (set(value) != {"ok", "result"} or value.get("ok") is not True or type(result) is not dict
            or set(result) != _PURGE_RESULT_KEYS or result.get("schema_version") != 24
            or result.get("validated_only") is not validate_only or result.get("applied") is not (not validate_only)
            or any(type(result.get(key)) is not int or result[key] < 0 for key in _PURGE_RESULT_KEYS - {"schema_version", "validated_only", "applied", "expected_subject_found"})
            or type(result.get("expected_subject_found")) is not bool):
        raise _invalid("Authelia permanent deletion could not safely complete.")
    return result


def _finalize_phase(data: dict, manifest: str, path: Path, phase: dict, phase_info: os.stat_result, runner) -> dict:
    _human_response(data, manifest, {
        "operation": "human-delete-finalize", "principal": phase["principal"],
        "expected_generation": phase["generation"], "request_id": phase["request_id"],
    }, runner, "human-delete-finalize")
    try:
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (phase_info.st_dev, phase_info.st_ino):
            raise _invalid("Permanent deletion recovery state changed during finalization.")
        path.unlink()
    except FileNotFoundError as exc:
        raise _invalid("Permanent deletion recovery state changed during finalization.") from exc
    return {"request_id": phase["request_id"], "applied": True, "finalized": True}


def _run_phase(data: dict, manifest: str, path: Path, phase: dict, runner, unit_root: Path, *, intent: dict | None = None) -> dict:
    phase = _phase(phase)
    phase_info = path.lstat()
    if (not stat.S_ISREG(phase_info.st_mode) or stat.S_ISLNK(phase_info.st_mode) or phase_info.st_nlink != 1
            or phase_info.st_uid != os.geteuid() or stat.S_IMODE(phase_info.st_mode) != 0o600):
        raise _invalid("Permanent deletion recovery state is unsafe.")
    if intent is not None:
        _phase_matches_intent(phase, intent)
    elif phase["idp_delete"]:
        raise _invalid("Permanent deletion phase is held until its native intent is available.")
    # A legacy Connect-only identity has no supported IdP mapping. It remains
    # disabled at native prepare and can be finalized without touching IdP data.
    if not phase["idp_delete"]:
        return _finalize_phase(data, manifest, path, phase, phase_info, runner)
    _config, active = _preflight(data, manifest, runner)
    source = manage._read_unit(Path(data["config_root"]) / "systemd" / users._UNIT)
    if source is None or manage._unit_exec_path(source) != Path(data["components"]["authelia"]):
        raise _invalid("Authelia unit does not match its declared executable.")
    manage._verify_unit(unit_root, users._UNIT, source)
    manage._unit_metadata(runner, unit_root, (users._UNIT,), present=True)
    stopped = False
    removed = False
    restart_needed = phase["original_active"]
    try:
        if active:
            manage._action(runner, (manage._SYSTEMCTL, "stop", users._UNIT), 30, "Authelia stop failed")
            stopped = True
        if manage._unit_state(runner, users._UNIT)[0]:
            raise manage.ManageError("Authelia is still active")
        try:
            raw, info = users._read_users(data)
            database = users._database(raw)
        except UsageError:
            # This workflow removes one account, never the whole file. A
            # missing file is not a recoverable per-account completion proof.
            raise _invalid("Authelia users file is unavailable; native intent remains held.") from None
        prior_removed = phase["username"] not in database["users"]
        uid, gid = role_identity(data, "idp")
        preview = _purge_as_idp(Path(data["authelia"]["state_directory"]) / "authelia.sqlite3", phase["username"], phase["subject"],
                                validate_only=True, uid=uid, gid=gid)
        if not preview["expected_subject_found"]:
            if not prior_removed or any(preview[key] for key in (
                    "username_records", "opaque_identifiers", "oauth_sessions", "consent_sessions", "consent_preconfigurations")):
                raise _invalid("Authelia account no longer matches the prepared deletion; native intent remains held.")
        if raw is not None and phase["username"] in database["users"]:
            from .user_backup import snapshot
            snapshot(data, manifest, users_raw=raw)
            del database["users"][phase["username"]]
            users._replace_users(Path(data["authelia"]["users_file"]),
                                 (json.dumps(database, indent=2, ensure_ascii=True) + "\n").encode(), info)
            removed = True
        result = _purge_as_idp(Path(data["authelia"]["state_directory"]) / "authelia.sqlite3", phase["username"], phase["subject"],
                               validate_only=False, uid=uid, gid=gid)
        if not result["expected_subject_found"] and not prior_removed and not removed:
            raise _invalid("Authelia permanent deletion could not be verified; native intent remains held.")
        return _finalize_phase(data, manifest, path, phase, phase_info, runner)
    except BaseException as exc:
        error = manage.ManageError("Permanent deletion remains pending for forward recovery.", may_have_executed=removed or stopped)
        raise error from exc
    finally:
        if restart_needed:
            manage._action(runner, (manage._SYSTEMCTL, "start", users._UNIT), 30, "Authelia restart failed")
            if not manage._unit_state(runner, users._UNIT)[0]:
                raise manage.ManageError("Authelia restart failed after permanent deletion", may_have_executed=True)

def _new_phase(data: dict, manifest: str, intent: dict, runner) -> dict:
    root = Path(data["config_root"])
    config = root / "authelia/configuration.yml"
    subject = _mapped_subject(data, config, intent["principal"], intent["username"], runner)
    active, _ = manage._unit_state(runner, users._UNIT)
    return {"schema": _PHASE_SCHEMA, "request_id": intent["request_id"], "principal": intent["principal"],
            "username": intent["username"], "subject": subject, "generation": intent["generation"],
            "original_active": active, "idp_delete": bool(subject)}


def delete(manifest: str, username: str, *, apply: bool = False, runner=None, unit_root: Path = Path("/etc/systemd/system")) -> dict:
    """Prepare one native deletion intent and synchronously advance it when confirmed."""
    users._require_root()
    if not isinstance(username, str) or not users._USER.fullmatch(username):
        raise _invalid("Use a lowercase username containing letters, digits, dots, underscores or hyphens.")
    _path(manifest)
    data = read_manifest(manifest)
    result = {"schema": _PHASE_SCHEMA, "operation": "delete", "username": username, "applied": bool(apply), "pending": False}
    if not apply:
        return result
    # The same lifecycle lock covers native authority, the durable phase, IdP
    # mutation and finalization.  A render/activation cannot change operator
    # policy between the disabled intent and its irreversible IdP purge.
    with manage._deployment_lock(Path(data["config_root"])):
        root = _phase_root(manifest)
        retained = [(path, phase) for path, phase in _phases(root).values() if phase["username"] == username]
        if len(retained) > 1:
            raise _invalid("Permanent deletion recovery state is invalid.")
        if retained:
            path, phase = retained[0]
            pending = {intent["request_id"]: intent for intent in _intents(data, manifest, runner)}
            intent = pending.get(phase["request_id"])
            if intent is not None:
                _phase_matches_intent(phase, intent)
            if intent is None:
                # Native finalization is idempotent only for the exact retained
                # completed receipt. Prove it before any IdP/service operation.
                return _finalize_phase(data, manifest, path, phase, path.lstat(), runner)
            return _run_phase(data, manifest, path, phase, runner, Path(unit_root), intent=intent)
        config, active = _preflight(data, manifest, runner)
        raw, _info = users._read_users(data)
        if username not in users._database(raw)["users"]:
            raise _invalid("Account does not exist.")
        subject = users._oidc_subject(data, config, username, runner, missing_ok=True)
        if subject is None:
            raise _invalid("The account has no unambiguous OpenID Connect subject; permanent deletion requires the native authority.")
        principal = users._principal(data["gateway"]["oidc"]["issuer"], subject)
        inspection = _inspection(_human_response(
            data, manifest, {"operation": "human-inspect", "principal": principal}, runner, "human-inspect"))
        request_id = str(uuid.uuid4())
        if inspection is None:
            # Native creates the disabled, zero-grant tombstone atomically. It
            # fences a concurrent provisioning attempt before local IdP data is
            # changed, then returns the ordinary pending intent.
            intent = _absent_intent(data, manifest, principal, username, request_id, runner)
        else:
            if inspection["username"] != username or inspection.get("deletion_request"):
                raise _invalid("Native account does not permit permanent deletion.")
            intent = _intent(data, manifest, principal, inspection["generation"], request_id, runner)
        phase = {"schema": _PHASE_SCHEMA, "request_id": intent["request_id"], "principal": principal, "username": username,
                 "subject": subject, "generation": intent["generation"], "original_active": active, "idp_delete": True}
        path = _write_phase(root, phase)  # Before any Authelia data mutation.
        return _run_phase(data, manifest, path, phase, runner, Path(unit_root), intent=intent)


def process_pending(manifest: str, *, apply: bool = False, runner=None, unit_root: Path = Path("/etc/systemd/system")) -> dict:
    """Advance at most one native-authorized permanent deletion intent."""
    users._require_root()
    _path(manifest)
    data = read_manifest(manifest)
    if not apply:
        return {"schema": _PHASE_SCHEMA, "operation": "process-pending", "applied": False, "maximum_intents": 1}
    # Hold the lock before listing native intents and through the IdP operation
    # and terminal native write.  This makes the durable phase an authority
    # fence, not merely a local retry record.
    with manage._deployment_lock(Path(data["config_root"])):
        root = _phase_root(manifest)
        intents = _intents(data, manifest, runner)
        phases = _phases(root)
        if not intents:
            if not phases:
                return {"schema": _PHASE_SCHEMA, "operation": "process-pending", "applied": True, "processed": 0}
            # Finalize accepts only its exact retained completed receipt. A
            # reset or stale authority record rejects before IdP/service work.
            path, phase = next(iter(phases.values()))
            result = _finalize_phase(data, manifest, path, phase, path.lstat(), runner)
            return {"schema": _PHASE_SCHEMA, "operation": "process-pending", "applied": True, "processed": 1, "result": result}
        intent = intents[0]
        found = phases.get(intent["request_id"])
        if found is None:
            phase = _new_phase(data, manifest, intent, runner)
            path = _write_phase(root, phase)
        else:
            path, phase = found
            _phase_matches_intent(phase, intent)
        result = _run_phase(data, manifest, path, phase, runner, Path(unit_root), intent=intent)
        return {"schema": _PHASE_SCHEMA, "operation": "process-pending", "applied": True, "processed": 1, "result": result}
