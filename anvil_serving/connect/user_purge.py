"""Pinned, bounded Authelia SQLite account erasure."""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat
import time

from ..operator_output import UsageError
from . import manage


_LIMIT = 64 * 1024 * 1024
_SCHEMA_VERSION = 24
_TIMEOUT = 10.0
_MAX_IDENTIFIERS = 64
_MAX_CHALLENGES = 512
_MAX_PRECONFIGURATIONS = 512
_MAX_CHANGES = 10_000
_USERNAME_TABLES = (
    "authentication_logs", "identity_verification", "totp_configurations", "totp_history",
    "webauthn_credentials", "webauthn_users", "duo_devices", "user_preferences",
    "one_time_code", "banned_user",
)
_SESSION_TABLES = (
    "oauth2_authorization_code_session", "oauth2_access_token_session",
    "oauth2_refresh_token_session", "oauth2_pkce_request_session",
    "oauth2_openid_connect_session", "oauth2_device_code_session",
)
_COLUMNS = {
    "migrations": {"id", "applied", "version_before", "version_after", "application_version"},
    "authentication_logs": {"id", "time", "successful", "banned", "username", "auth_type", "remote_ip", "request_uri", "request_method"},
    "identity_verification": {"id", "jti", "iat", "issued_ip", "exp", "username", "action", "consumed", "consumed_ip", "revoked", "revoked_ip"},
    "totp_configurations": {"id", "created_at", "last_used_at", "username", "issuer", "algorithm", "digits", "period", "secret"},
    "totp_history": {"id", "created_at", "username", "step"},
    "webauthn_credentials": {"id", "created_at", "last_used_at", "rpid", "username", "description", "kid", "aaguid", "attestation_format", "attachment", "transport", "sign_count", "clone_warning", "legacy", "discoverable", "present", "verified", "backup_eligible", "backup_state", "public_key", "attestation", "attestation_type"},
    "webauthn_users": {"id", "rpid", "username", "userid"},
    "duo_devices": {"id", "username", "device", "method"},
    "user_preferences": {"id", "username", "second_factor_method"},
    "one_time_code": {"id", "public_id", "signature", "issued", "issued_ip", "expires", "username", "intent", "consumed", "consumed_ip", "revoked", "revoked_ip", "code"},
    "banned_user": {"id", "time", "expires", "expired", "revoked", "username", "source", "reason"},
    "banned_ip": {"id", "time", "expires", "expired", "revoked", "ip", "source", "reason"},
    "cached_data": {"id", "created_at", "updated_at", "name", "encrypted", "value"},
    "encryption": {"id", "name", "value"},
    "user_opaque_identifier": {"id", "service", "sector_id", "username", "identifier"},
    "oauth2_blacklisted_jti": {"id", "signature", "expires_at"},
    "oauth2_par_context": {"id", "signature", "request_id", "client_id", "requested_at", "scopes", "audience", "handled_response_types", "response_mode", "response_mode_default", "revoked", "form_data", "session_data"},
    "oauth2_consent_session": {"id", "challenge_id", "client_id", "subject", "authorized", "granted", "requested_at", "responded_at", "form_data", "requested_scopes", "granted_scopes", "requested_audience", "granted_audience", "preconfiguration", "granted_claims", "expires_at"},
    "oauth2_consent_preconfiguration": {"id", "client_id", "subject", "created_at", "expires_at", "revoked", "scopes", "audience", "requested_claims", "signature_claims", "granted_claims"},
}
for _table in _SESSION_TABLES:
    _COLUMNS[_table] = {"id", "challenge_id", "request_id", "client_id", "signature", "subject", "requested_at", "requested_scopes", "granted_scopes", "requested_audience", "granted_audience", "active", "revoked", "form_data", "session_data"}
_COLUMNS["oauth2_device_code_session"] = {"id", "challenge_id", "request_id", "client_id", "signature", "user_code_signature", "status", "subject", "requested_at", "checked_at", "requested_scopes", "granted_scopes", "requested_audience", "granted_audience", "active", "revoked", "form_data", "session_data"}

_INDEXES = {
    "authentication_logs": {"authentication_logs_username_idx": ("time", "username", "auth_type")},
    "identity_verification": {"identity_verification_jti_key": ("jti",)},
    "totp_configurations": {"totp_configurations_username_key": ("username",)},
    "totp_history": {"totp_history_lookup_key": ("username", "step")},
    "webauthn_credentials": {"webauthn_credentials_kid_key": ("kid",), "webauthn_credentials_lookup_key": ("rpid", "username", "description")},
    "webauthn_users": {"webauthn_users_lookup_key": ("rpid", "username")},
    "duo_devices": {"duo_devices_username_key": ("username",)},
    "user_preferences": {"user_preferences_username_key": ("username",)},
    "one_time_code": {"one_time_code_lookup_key": ("signature", "username")},
    "banned_user": {"banned_user_username_idx": ("username",), "banned_user_lookup_idx": ("username", "revoked", "expires", "expired"), "banned_user_list_idx": ("revoked", "expires", "expired")},
    "banned_ip": {"banned_ip_ip_idx": ("ip",), "banned_ip_lookup_idx": ("ip", "revoked", "expires", "expired"), "banned_ip_list_idx": ("revoked", "expires", "expired")},
    "cached_data": {"cached_data_name_key": ("name",)},
    "encryption": {"encryption_name_key": ("name",)},
    "user_opaque_identifier": {"user_opaque_identifier_lookup_key": ("service", "sector_id", "username"), "user_opaque_identifier_identifier_key": ("identifier",)},
    "oauth2_blacklisted_jti": {"oauth2_blacklisted_jti_signature_key": ("signature",)},
    "oauth2_par_context": {"oauth2_par_context_signature_key": ("signature",)},
    "oauth2_consent_session": {"oauth2_consent_session_challenge_id_key": ("challenge_id",)},
}
for _table in _SESSION_TABLES:
    _prefix = _table
    _INDEXES[_table] = {f"{_prefix}_request_id_idx": ("request_id",), f"{_prefix}_client_id_idx": ("client_id",), f"{_prefix}_client_id_subject_idx": ("client_id", "subject")}


def _invalid(message: str) -> UsageError:
    return UsageError(message, code="connect_user_purge_invalid")


def _safe_database(path: Path, uid: int, gid: int) -> None:
    if not path.is_absolute() or ".." in path.parts or any(c in str(path) for c in "\x00\r\n\t"):
        raise _invalid("Authelia database path is invalid.")
    manage._safe_private_runtime_directory(path.parent, uid, gid)
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if not os.path.lexists(candidate):
            continue
        try:
            info = candidate.lstat()
        except OSError as exc:
            raise _invalid("Authelia database metadata is unavailable.") from exc
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
                or info.st_uid != uid or info.st_gid != gid or info.st_mode & 0o022 or info.st_size > _LIMIT):
            raise _invalid("Authelia database has unsafe metadata or exceeds the 64 MiB limit.")
    if not os.path.lexists(path):
        raise _invalid("Authelia database is unavailable.")


def _deadline(connection: sqlite3.Connection, end: float) -> None:
    def progress() -> int:
        return int(time.monotonic() >= end)
    connection.set_progress_handler(progress, 1_000)


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _indexes(connection: sqlite3.Connection, table: str) -> dict[str, tuple[str, ...]]:
    return {row[1]: tuple(item[2] for item in connection.execute(f"PRAGMA index_info({row[1]})"))
            for row in connection.execute(f"PRAGMA index_list({table})")}


def _foreign_keys(connection: sqlite3.Connection, table: str) -> set[tuple[str, str, str, str]]:
    return {(row[3], row[2], row[4], row[6]) for row in connection.execute(f"PRAGMA foreign_key_list({table})")}


def _schema(connection: sqlite3.Connection) -> None:
    for table, columns in _COLUMNS.items():
        if _columns(connection, table) != columns:
            raise _invalid("Authelia database is not the pinned schema 24.")
    for table, indexes in _INDEXES.items():
        observed = _indexes(connection, table)
        if any(observed.get(name) != columns for name, columns in indexes.items()):
            raise _invalid("Authelia database is not the pinned schema 24.")
    restrict = {("subject", "user_opaque_identifier", "identifier", "RESTRICT")}
    if not restrict.issubset(_foreign_keys(connection, "oauth2_consent_preconfiguration")):
        raise _invalid("Authelia database is not the pinned schema 24.")
    if not {("subject", "user_opaque_identifier", "identifier", "RESTRICT"), ("preconfiguration", "oauth2_consent_preconfiguration", "id", "CASCADE")}.issubset(_foreign_keys(connection, "oauth2_consent_session")):
        raise _invalid("Authelia database is not the pinned schema 24.")
    for table in _SESSION_TABLES:
        required = {("subject", "user_opaque_identifier", "identifier", "RESTRICT"), ("challenge_id", "oauth2_consent_session", "challenge_id", "CASCADE")}
        if not required.issubset(_foreign_keys(connection, table)):
            raise _invalid("Authelia database is not the pinned schema 24.")
    version = connection.execute("SELECT version_after FROM migrations ORDER BY id DESC LIMIT 1").fetchone()
    if version != (_SCHEMA_VERSION,):
        raise _invalid("Authelia database is not the pinned schema 24.")


def _integrity(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA quick_check").fetchone() != ("ok",) or connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise _invalid("Authelia database integrity check failed.")


def _marks(values: tuple[str, ...]) -> str:
    return ", ".join("?" for _ in values)


def purge(database_path: str | Path, username: str, *, expected_subject: str | None = None,
          validate_only: bool = False, uid: int | None = None,
          gid: int | None = None) -> dict[str, int | bool]:
    """Validate or erase one account from stopped Authelia SQLite schema 24.

    The caller must fork/drop to the IdP identity first so SQLite journals retain
    the owner Authelia expects.
    """
    if not isinstance(username, str) or not username or len(username) > 100 or "\x00" in username:
        raise _invalid("Authelia username is invalid.")
    if expected_subject is not None and (not isinstance(expected_subject, str) or not expected_subject or len(expected_subject) > 64):
        raise _invalid("Authelia subject is invalid.")
    uid = os.geteuid() if uid is None else uid
    gid = os.getegid() if gid is None else gid
    if (type(uid) is not int or type(gid) is not int or uid <= 0 or gid <= 0
            or os.geteuid() != uid or os.getegid() != gid):
        raise _invalid("Authelia purge must run as the declared IdP identity.")
    path = Path(database_path)
    _safe_database(path, uid, gid)
    end = time.monotonic() + _TIMEOUT
    try:
        connection = sqlite3.connect(str(path), timeout=5, isolation_level=None)
        try:
            _deadline(connection, end)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            _schema(connection)
            _integrity(connection)
            subjects = tuple(row[0] for row in connection.execute("SELECT identifier FROM user_opaque_identifier WHERE username = ? ORDER BY id", (username,)))
            if len(subjects) > _MAX_IDENTIFIERS:
                raise _invalid("Authelia account purge exceeds its identifier bound.")
            if subjects and expected_subject is not None and expected_subject not in subjects:
                raise _invalid("Authelia account does not match its prepared OpenID Connect subject.")
            preconfiguration_ids = tuple(row[0] for row in connection.execute("SELECT id FROM oauth2_consent_preconfiguration WHERE subject IN (" + _marks(subjects) + ")", subjects)) if subjects else ()
            if len(preconfiguration_ids) > _MAX_PRECONFIGURATIONS:
                raise _invalid("Authelia account purge exceeds its consent-preconfiguration bound.")
            challenges = tuple(row[0] for row in connection.execute("SELECT challenge_id FROM oauth2_consent_session WHERE subject IN (" + _marks(subjects) + ") OR preconfiguration IN (" + _marks(preconfiguration_ids) + ")", (*subjects, *preconfiguration_ids))) if subjects else ()
            if len(challenges) > _MAX_CHALLENGES:
                raise _invalid("Authelia account purge exceeds its consent-session bound.")
            username_rows = {table: connection.execute(f"SELECT COUNT(*) FROM {table} WHERE username = ?", (username,)).fetchone()[0] for table in _USERNAME_TABLES}
            session_rows = sum(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE subject IN ({_marks(subjects)}) OR challenge_id IN ({_marks(challenges)})", (*subjects, *challenges)).fetchone()[0] for table in _SESSION_TABLES) if subjects or challenges else 0
            changes = sum(username_rows.values()) + len(subjects) + len(challenges) + session_rows + len(preconfiguration_ids)
            if changes > _MAX_CHANGES:
                raise _invalid("Authelia account purge exceeds its change bound.")
            result = {"schema_version": _SCHEMA_VERSION, "validated_only": validate_only, "applied": False,
                      "username_records": sum(username_rows.values()), "opaque_identifiers": len(subjects),
                      "oauth_sessions": session_rows, "consent_sessions": len(challenges),
                      "consent_preconfigurations": len(preconfiguration_ids),
                      "expected_subject_found": expected_subject in subjects if expected_subject is not None else False}
            if validate_only:
                return result
            connection.execute("BEGIN IMMEDIATE")
            try:
                for table in _USERNAME_TABLES:
                    connection.execute(f"DELETE FROM {table} WHERE username = ?", (username,))
                if subjects or challenges:
                    for table in _SESSION_TABLES:
                        connection.execute(f"DELETE FROM {table} WHERE subject IN ({_marks(subjects)}) OR challenge_id IN ({_marks(challenges)})", (*subjects, *challenges))
                if subjects:
                    connection.execute("DELETE FROM oauth2_consent_session WHERE subject IN (" + _marks(subjects) + ") OR preconfiguration IN (" + _marks(preconfiguration_ids) + ")", (*subjects, *preconfiguration_ids))
                    connection.execute("DELETE FROM oauth2_consent_preconfiguration WHERE subject IN (" + _marks(subjects) + ")", subjects)
                    connection.execute("DELETE FROM user_opaque_identifier WHERE username = ?", (username,))
                _integrity(connection)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            result.update({"validated_only": False, "applied": True})
            return result
        finally:
            connection.close()
    except UsageError:
        raise
    except (OSError, sqlite3.Error, TimeoutError) as exc:
        raise _invalid("Authelia account purge could not safely complete.") from exc
