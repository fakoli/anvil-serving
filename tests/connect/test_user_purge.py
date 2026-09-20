"""Focused isolation checks for the pinned Authelia SQLite eraser."""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import sys

import pytest

from anvil_serving.connect import manage, user_purge
from anvil_serving.operator_output import UsageError


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="local Linux account administration")
_ALICE = "11111111-1111-4111-8111-111111111111"
_BOB = "22222222-2222-4222-8222-222222222222"


def _create_schema(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for table, columns in user_purge._COLUMNS.items():
        definitions = ["id INTEGER PRIMARY KEY" if column == "id" else column + " TEXT" for column in sorted(columns)]
        if table == "migrations":
            definitions[definitions.index("version_after TEXT")] = "version_after INTEGER"
        if table == "user_opaque_identifier":
            definitions[definitions.index("identifier TEXT")] = "identifier TEXT UNIQUE"
        if table == "oauth2_consent_session":
            definitions[definitions.index("challenge_id TEXT")] = "challenge_id TEXT UNIQUE"
            definitions.extend((
                "FOREIGN KEY(subject) REFERENCES user_opaque_identifier(identifier) ON DELETE RESTRICT",
                "FOREIGN KEY(preconfiguration) REFERENCES oauth2_consent_preconfiguration(id) ON DELETE CASCADE",
            ))
        elif table == "oauth2_consent_preconfiguration":
            definitions.append("FOREIGN KEY(subject) REFERENCES user_opaque_identifier(identifier) ON DELETE RESTRICT")
        elif table in user_purge._SESSION_TABLES:
            definitions.extend((
                "FOREIGN KEY(subject) REFERENCES user_opaque_identifier(identifier) ON DELETE RESTRICT",
                "FOREIGN KEY(challenge_id) REFERENCES oauth2_consent_session(challenge_id) ON DELETE CASCADE",
            ))
        connection.execute(f"CREATE TABLE {table} (" + ", ".join(definitions) + ")")
    for table, indexes in user_purge._INDEXES.items():
        for name, columns in indexes.items():
            connection.execute(f"CREATE INDEX {name} ON {table} (" + ", ".join(columns) + ")")
    connection.execute("INSERT INTO migrations (id, version_after) VALUES (1, 24)")
    connection.commit()
    connection.close()


def _insert(connection: sqlite3.Connection, table: str, values: dict[str, object]) -> None:
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
    connection.execute("PRAGMA foreign_keys=ON")
    for username in ("alice", "bob"):
        for table in user_purge._USERNAME_TABLES:
            _insert(connection, table, {"username": username})
    _insert(connection, "user_opaque_identifier", {"service": "openid", "sector_id": "", "username": "alice", "identifier": _ALICE})
    _insert(connection, "user_opaque_identifier", {"service": "openid", "sector_id": "", "username": "bob", "identifier": _BOB})
    for subject, challenge, preconfiguration in ((_ALICE, "alice-challenge", 1), (_BOB, "bob-challenge", 2)):
        _insert(connection, "oauth2_consent_preconfiguration", {"id": preconfiguration, "client_id": "client", "subject": subject})
        _insert(connection, "oauth2_consent_session", {"challenge_id": challenge, "client_id": "client", "subject": subject, "preconfiguration": preconfiguration})
        for table in user_purge._SESSION_TABLES:
            _insert(connection, table, {"challenge_id": challenge, "request_id": challenge, "client_id": "client", "signature": challenge, "subject": subject})
    _insert(connection, "oauth2_consent_session", {"challenge_id": "alice-pending", "client_id": "client", "subject": None, "preconfiguration": 1})
    for table in user_purge._SESSION_TABLES:
        _insert(connection, table, {"challenge_id": "alice-pending", "request_id": "alice-pending", "client_id": "client", "signature": "alice-pending", "subject": None})
    _insert(connection, "banned_ip", {"ip": "192.0.2.1"})
    _insert(connection, "oauth2_par_context", {"signature": "unrelated", "request_id": "unrelated", "client_id": "client"})
    _insert(connection, "oauth2_blacklisted_jti", {"signature": "unrelated"})
    connection.commit()
    connection.close()
    return database


def _count(database: Path, table: str, column: str, value: str) -> int:
    with sqlite3.connect(database) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (value,)).fetchone()[0]


def test_purge_erases_only_target_and_allows_username_reuse(database):
    result = user_purge.purge(database, "alice", expected_subject=_ALICE)

    assert result == {"schema_version": 24, "validated_only": False, "applied": True,
                      "username_records": len(user_purge._USERNAME_TABLES), "opaque_identifiers": 1,
                      "oauth_sessions": len(user_purge._SESSION_TABLES) * 2, "consent_sessions": 2,
                      "consent_preconfigurations": 1, "expected_subject_found": True}
    for table in user_purge._USERNAME_TABLES:
        assert _count(database, table, "username", "alice") == 0
        assert _count(database, table, "username", "bob") == 1
    assert _count(database, "user_opaque_identifier", "username", "alice") == 0
    assert _count(database, "user_opaque_identifier", "username", "bob") == 1
    for table in user_purge._SESSION_TABLES:
        assert _count(database, table, "subject", _ALICE) == 0
        assert _count(database, table, "challenge_id", "alice-pending") == 0
        assert _count(database, table, "subject", _BOB) == 1
    assert _count(database, "banned_ip", "ip", "192.0.2.1") == 1
    assert _count(database, "oauth2_par_context", "signature", "unrelated") == 1
    assert _count(database, "oauth2_blacklisted_jti", "signature", "unrelated") == 1
    with sqlite3.connect(database) as connection:
        _insert(connection, "user_opaque_identifier", {"service": "openid", "sector_id": "", "username": "alice", "identifier": "33333333-3333-4333-8333-333333333333"})


def test_validate_only_and_completed_rerun_are_non_destructive(database):
    before = database.read_bytes()
    preview = user_purge.purge(database, "alice", expected_subject=_ALICE, validate_only=True)
    assert preview["validated_only"] and not preview["applied"] and database.read_bytes() == before
    user_purge.purge(database, "alice", expected_subject=_ALICE)
    rerun = user_purge.purge(database, "alice", expected_subject=_ALICE)
    assert rerun["applied"] and rerun["opaque_identifiers"] == 0 and not rerun["expected_subject_found"]


@pytest.mark.parametrize("damage", ("index", "corrupt", "oversize"))
def test_unsupported_or_unsafe_database_never_mutates(database, damage):
    if damage == "index":
        with sqlite3.connect(database) as connection:
            connection.execute("DROP INDEX user_opaque_identifier_lookup_key")
    elif damage == "corrupt":
        database.write_bytes(b"not a SQLite database")
    else:
        os.truncate(database, user_purge._LIMIT + 1)
    before = database.read_bytes() if damage != "oversize" else database.stat().st_size
    with pytest.raises(UsageError):
        user_purge.purge(database, "alice", expected_subject=_ALICE)
    if damage != "oversize":
        assert database.read_bytes() == before
    else:
        assert database.stat().st_size == before
    if damage == "index":
        assert _count(database, "authentication_logs", "username", "alice") == 1


def test_transaction_rolls_back_on_delete_failure(database):
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TRIGGER fail_purge BEFORE DELETE ON totp_history WHEN OLD.username = 'alice' BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
    with pytest.raises(UsageError):
        user_purge.purge(database, "alice", expected_subject=_ALICE)
    assert _count(database, "authentication_logs", "username", "alice") == 1
    assert _count(database, "user_opaque_identifier", "username", "alice") == 1
