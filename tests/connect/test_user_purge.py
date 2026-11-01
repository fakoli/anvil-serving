"""Focused isolation checks for the pinned Authelia SQLite eraser."""
from __future__ import annotations

import base64
from pathlib import Path
import sqlite3
import sys
import zlib

import pytest

from anvil_serving.connect import manage, user_purge
from anvil_serving.operator_output import UsageError


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="local Linux account administration")
_ALICE = "11111111-1111-4111-8111-111111111111"
_BOB = "22222222-2222-4222-8222-222222222222"
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
# Generated from Authelia v4.39.20 (commit 1b524f7f4bbf7b5637f4c6b98f4f66fd4b4aed91)
# internal/storage/migrations/sqlite/V0001...V0024; sqlite_sequence is omitted.
_SCHEMA24_ZLIB_B64 = "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"


def _create_schema(path: Path) -> None:
    connection = sqlite3.connect(path)
    # The compact export uses literal ``\\n`` separators between source migration
    # statements; restore them before executing the upstream-derived fixture.
    schema = zlib.decompress(base64.b64decode(_SCHEMA24_ZLIB_B64)).decode().replace("\\n", "\n")
    connection.executescript(schema)
    connection.execute("INSERT INTO migrations (version_after, application_version) VALUES (24, '4.39.20')")
    connection.commit()
    connection.close()


# Exact upstream SQLite migrations V0027 and V0029 from Authelia v4.39.28.
# V0025, V0026, and V0028 intentionally contain no SQLite statements.
_SCHEMA29_MIGRATIONS = """
ALTER TABLE oauth2_refresh_token_session ADD COLUMN access_signature VARCHAR(768) NOT NULL DEFAULT '';

UPDATE oauth2_refresh_token_session
SET access_signature = COALESCE((
        SELECT a.signature
        FROM oauth2_access_token_session a
        WHERE a.request_id = oauth2_refresh_token_session.request_id
          AND a.revoked = FALSE), '')
WHERE revoked = FALSE
  AND access_signature = ''
  AND (SELECT COUNT(*)
       FROM oauth2_access_token_session a
       WHERE a.request_id = oauth2_refresh_token_session.request_id
         AND a.revoked = FALSE) = 1;

ALTER TABLE oauth2_access_token_session ADD COLUMN requested_resource TEXT NULL DEFAULT '';
ALTER TABLE oauth2_access_token_session ADD COLUMN granted_resource TEXT NULL DEFAULT '';

ALTER TABLE oauth2_authorization_code_session ADD COLUMN requested_resource TEXT NULL DEFAULT '';
ALTER TABLE oauth2_authorization_code_session ADD COLUMN granted_resource TEXT NULL DEFAULT '';

ALTER TABLE oauth2_openid_connect_session ADD COLUMN requested_resource TEXT NULL DEFAULT '';
ALTER TABLE oauth2_openid_connect_session ADD COLUMN granted_resource TEXT NULL DEFAULT '';

ALTER TABLE oauth2_pkce_request_session ADD COLUMN requested_resource TEXT NULL DEFAULT '';
ALTER TABLE oauth2_pkce_request_session ADD COLUMN granted_resource TEXT NULL DEFAULT '';

ALTER TABLE oauth2_refresh_token_session ADD COLUMN requested_resource TEXT NULL DEFAULT '';
ALTER TABLE oauth2_refresh_token_session ADD COLUMN granted_resource TEXT NULL DEFAULT '';

ALTER TABLE oauth2_device_code_session ADD COLUMN requested_resource TEXT NULL DEFAULT '';
ALTER TABLE oauth2_device_code_session ADD COLUMN granted_resource TEXT NULL DEFAULT '';

ALTER TABLE oauth2_consent_session ADD COLUMN requested_resource TEXT NULL DEFAULT '';
ALTER TABLE oauth2_consent_session ADD COLUMN granted_resource TEXT NULL DEFAULT '';

ALTER TABLE oauth2_par_context ADD COLUMN resource TEXT NOT NULL DEFAULT '';

ALTER TABLE oauth2_consent_preconfiguration ADD COLUMN resource TEXT NULL;
"""


def _migrate_schema29(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA29_MIGRATIONS)
    for version in range(25, 30):
        connection.execute(
            "INSERT INTO migrations (version_after, application_version) VALUES (?, '4.39.28')",
            (version,),
        )


def _insert(connection: sqlite3.Connection, table: str, values: dict[str, object]) -> None:
    identity = str(values.get("username", values.get("subject", values.get("challenge_id", "row"))))
    for _, column, kind, required, default, primary in connection.execute(f"PRAGMA table_info({table})"):
        if required and default is None and not primary and column not in values:
            values[column] = 0 if "INT" in kind else f"{column}-{identity}".encode() if "BLOB" in kind else f"{column}-{identity}"
    columns = tuple(values)
    connection.execute(f"INSERT INTO {table} (" + ", ".join(columns) + ") VALUES (" + ", ".join("?" for _ in columns) + ")", tuple(values.values()))


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(manage, "_safe_root_ancestors", lambda *_: None)
    state = tmp_path / "idp"
    state.mkdir(mode=0o700)
    database = state / "authelia.sqlite3"
    _create_schema(database)
    database.chmod(0o600)
    connection = sqlite3.connect(database)
    _migrate_schema29(connection)
    connection.execute("PRAGMA foreign_keys=ON")
    for username in ("alice", "bob"):
        for table in _USERNAME_TABLES:
            _insert(connection, table, {"username": username})
    _insert(connection, "user_opaque_identifier", {"service": "openid", "sector_id": "", "username": "alice", "identifier": _ALICE})
    _insert(connection, "user_opaque_identifier", {"service": "openid", "sector_id": "", "username": "bob", "identifier": _BOB})
    for subject, challenge, preconfiguration in ((_ALICE, "alice-challenge", 1), (_BOB, "bob-challenge", 2)):
        _insert(connection, "oauth2_consent_preconfiguration", {"id": preconfiguration, "client_id": "client", "subject": subject})
        _insert(connection, "oauth2_consent_session", {"challenge_id": challenge, "client_id": "client", "subject": subject, "preconfiguration": preconfiguration})
        for table in _SESSION_TABLES:
            _insert(connection, table, {"challenge_id": challenge, "request_id": challenge, "client_id": "client", "signature": challenge, "subject": subject})
    _insert(connection, "oauth2_consent_session", {"challenge_id": "alice-pending", "client_id": "client", "subject": None, "preconfiguration": 1})
    for table in _SESSION_TABLES:
        _insert(connection, table, {"challenge_id": "alice-pending", "request_id": "alice-pending", "client_id": "client", "signature": "alice-pending", "subject": _ALICE})
    _insert(connection, "banned_ip", {"ip": "192.0.2.1"})
    _insert(connection, "oauth2_par_context", {"signature": "unrelated", "request_id": "unrelated", "client_id": "client"})
    _insert(connection, "oauth2_blacklisted_jti", {"signature": "unrelated"})
    connection.commit()
    connection.close()
    return database


def _count(database: Path, table: str, column: str, value: str) -> int:
    with sqlite3.connect(database) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (value,)).fetchone()[0]


def test_purge_schema29_resource_fields_and_access_signature_are_scoped_to_target(database):
    with sqlite3.connect(database) as connection:
        for subject, prefix in ((_ALICE, "alice"), (_BOB, "bob")):
            requested, granted = (f"urn:{prefix}:requested", f"urn:{prefix}:granted")
            connection.execute(
                "UPDATE oauth2_access_token_session SET requested_resource = ?, granted_resource = ? WHERE subject = ?",
                (requested, granted, subject),
            )
            connection.execute(
                "UPDATE oauth2_refresh_token_session "
                "SET access_signature = ?, requested_resource = ?, granted_resource = ? WHERE subject = ?",
                (f"{prefix}-access-signature", requested, granted, subject),
            )
            connection.execute(
                "UPDATE oauth2_consent_session SET requested_resource = ?, granted_resource = ? WHERE subject = ?",
                (requested, granted, subject),
            )
            connection.execute(
                "UPDATE oauth2_consent_preconfiguration SET resource = ? WHERE subject = ?",
                (requested, subject),
            )
        connection.execute("UPDATE oauth2_par_context SET resource = ? WHERE signature = 'unrelated'", ("urn:unrelated",))

    user_purge.purge(database, "alice", expected_subject=_ALICE)

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT access_signature, requested_resource, granted_resource "
            "FROM oauth2_refresh_token_session WHERE subject = ?", (_ALICE,)
        ).fetchone() is None
        assert connection.execute(
            "SELECT requested_resource, granted_resource FROM oauth2_access_token_session WHERE subject = ?", (_ALICE,)
        ).fetchone() is None
        assert connection.execute(
            "SELECT access_signature, requested_resource, granted_resource "
            "FROM oauth2_refresh_token_session WHERE subject = ?", (_BOB,)
        ).fetchone() == ("bob-access-signature", "urn:bob:requested", "urn:bob:granted")
        assert connection.execute(
            "SELECT requested_resource, granted_resource FROM oauth2_access_token_session WHERE subject = ?", (_BOB,)
        ).fetchone() == ("urn:bob:requested", "urn:bob:granted")
        assert connection.execute(
            "SELECT requested_resource, granted_resource FROM oauth2_consent_session WHERE subject = ?", (_BOB,)
        ).fetchone() == ("urn:bob:requested", "urn:bob:granted")
        assert connection.execute(
            "SELECT resource FROM oauth2_consent_preconfiguration WHERE subject = ?", (_BOB,)
        ).fetchone() == ("urn:bob:requested",)
        assert connection.execute(
            "SELECT resource FROM oauth2_par_context WHERE signature = 'unrelated'"
        ).fetchone() == ("urn:unrelated",)


@pytest.mark.parametrize("validate_only", (True, False))
def test_require_zero_opaque_rejects_mapping_without_mutation(database, validate_only):
    with pytest.raises(UsageError):
        user_purge.purge(database, "alice", validate_only=validate_only, require_zero_opaque=True)
    assert _count(database, "authentication_logs", "username", "alice") == 1
    assert _count(database, "user_opaque_identifier", "username", "alice") == 1


def test_rejects_collateral_delete_trigger_before_mutation(database):
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TRIGGER collateral AFTER DELETE ON authentication_logs WHEN OLD.username = 'alice' BEGIN DELETE FROM authentication_logs WHERE username = 'bob'; END")
    with pytest.raises(UsageError):
        user_purge.purge(database, "alice", expected_subject=_ALICE)
    assert _count(database, "authentication_logs", "username", "alice") == 1
    assert _count(database, "authentication_logs", "username", "bob") == 1


def test_transaction_rolls_back_on_delete_failure(database, monkeypatch):
    original_integrity = user_purge._integrity
    calls = 0

    def fail_after_delete(connection):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise sqlite3.OperationalError("synthetic failure")
        original_integrity(connection)

    monkeypatch.setattr(user_purge, "_integrity", fail_after_delete)
    with pytest.raises(UsageError):
        user_purge.purge(database, "alice", expected_subject=_ALICE)
    assert _count(database, "authentication_logs", "username", "alice") == 1
    assert _count(database, "user_opaque_identifier", "username", "alice") == 1
