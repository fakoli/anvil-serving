"""Pinned, bounded Authelia SQLite account erasure."""
from __future__ import annotations

import hashlib
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
# SHA-256 of every non-internal sqlite_master SQL definition after Authelia
# v4.39.20 (commit 1b524f7f4bbf7b5637f4c6b98f4f66fd4b4aed91) migrations V0001-V0024.
# Matching the complete closure rejects added triggers, views, or indexes too.
_PINNED_OBJECTS = frozenset("""
table:authentication_logs:72850792e2dbf17c56ea769239b7ce50c99ada5f8adaec5a9a5dbe9100966d23
table:banned_ip:fd075760e7ab62225abb52a428fcb849c7cb84de00de764946652cdaede935fc
table:banned_user:30867e490fe74a7157dcb8728f88dbcb24635350c11282d5f9c69f99583e4459
table:cached_data:80062e186c60f1d3f6d1144f82e9f398c2fe6bff85f83d63663b280c7227ff4a
table:duo_devices:26331f747ac77d75c990a77d9706af9c703f1ea8c1ab4f9522d322382f9ec5f6
table:encryption:a88a31862dc40711312adff23c78831ac8533352d29b92dd803fea8d6c6ceb02
table:identity_verification:a30abb0fc3a943dac6b88ff30a8c2890e39a2251fc600ca4910b2eb3133b8772
table:migrations:beca21214a050a3f28135d7852e9207ad2d2305664252b99057b535440f1a465
table:oauth2_access_token_session:2bcd18a53b828443ceacfa5ce2d88ab63057eecb740e3caab2a72ca2ba9121df
table:oauth2_authorization_code_session:8a0df2cfe2df42a5251489185495672d03b8beb00d4722326fe53a5610c89101
table:oauth2_blacklisted_jti:63b716e864abcc0fe4c85ee527e4ef02aefe283e4bb8fb25b612e0d8a4a8f3d5
table:oauth2_consent_preconfiguration:31d511cba79dbf745d411d489c1fd4b8f64c2b2d3490afff3a17a46b2ffa891a
table:oauth2_consent_session:09f0bde07d1f485131607a728a2fd4e6761e2255d99c56b8a91415d3ced09503
table:oauth2_device_code_session:358295bfd476bc411228e45a71e382297b519dccb7ca34083802819a64a09857
table:oauth2_openid_connect_session:f48626b160454a728af29960df96442f832612bf93fc9ef3872e75385bd381b8
table:oauth2_par_context:c1b46bbf16ac551113c7bc8df8ca14f176a16d26498027c7b2b5343eb1e7423e
table:oauth2_pkce_request_session:b2471c31db920ceb9a3a1496e9b36ffbe2150984304b5e7aa936f4f039ad7b93
table:oauth2_refresh_token_session:410596112e58f4b50bc3ada3c0d0e0d32a602efaca15c66a2f3816f0109f6e0b
table:one_time_code:6ded68c6da90b2a029bf8a879e70d004964701a52ad528da044626993af0aecc
table:totp_configurations:2898d8076c1948fa0d2cc2758709cee52ab7feaf02a69242abe3bd8cbb9d52cc
table:totp_history:aceada09fcd75441e555e0d0f07ce85ab4436f6b4ae8b48c43270fbea26227c0
table:user_opaque_identifier:e93c3a58f1d8f24cc26340907c27f6f28897f1136e220d2b95146e155a17608b
table:user_preferences:db07734a81e4498bdb8a0b63f580e3e686712fcd4f8df07556e129bbc094889c
table:webauthn_credentials:b98bf5ec89047b0f860878f1c4404fabfcc96359625b3e3cc9b3a0382d5f3de0
table:webauthn_users:b0deebe26550f3a5e0aa8d69ddbf1a0b51f8c9766646630fe6359e3c45124197
index:authentication_logs_remote_ip_idx:243bd2b92012276b8dfda0ec894bdc1d692bd0789a365072407fe178091ffcde
index:authentication_logs_username_idx:60477d8a017b6f4db19b23350b30a3d472b8d4bbdc44905a904db98133d265b7
index:banned_ip_ip_idx:5c7983740bc7bf7230ba8cfd00b8157ac730412b8a14bd4affa023c31a5abbf5
index:banned_ip_list_idx:babe160aedff15156476974793c91d772d8d222ba7868014adab5e774cd07323
index:banned_ip_lookup_idx:b3c3a1b2cec2518bcd9e35aee3b8f0992994fb8ff7647efadef9274e98f24588
index:banned_user_list_idx:db7ca0be1250317c309eb66e3f3860b9fb9d70264d2b51afccdf6e030181bc1c
index:banned_user_lookup_idx:1a2a26ed1dcbd7bd7b38acbdf05096372b5157a20baca7a4680fe66ac9044cd9
index:banned_user_username_idx:986c3d4e13ca968c7797bf37e61fbf11520ff60e1a77932be59a3f473eafdcca
index:cached_data_name_key:b337d4811f0899c64d2c87d2069628306c77099d35adec4b392f3630d1a62b65
index:duo_devices_username_key:767ed2be4717fa855127cdaa613fc4ec4bf2e1f0a7bc3c3672bfe69baea7ac2f
index:encryption_name_key:1a67b45d4d0d402c2fa4d70b9ca9bceedd138e30f15180b23c20e09b792338cd
index:identity_verification_jti_key:730baf1d0338b22be389b09eb15078371f80c01e1185a7dd5136fab74bc91cd4
index:oauth2_access_token_session_client_id_idx:d44cd77b589f929e99d9e5d82db8ce21e488e02f28699fc0429e89f10775c621
index:oauth2_access_token_session_client_id_subject_idx:872ec05efa8a68f89750e7533fb2eee481745c223eca34e04a5a2a0f74b7198e
index:oauth2_access_token_session_request_id_idx:9dbd6f724b4687af4090283924dfbdfd295b704c65ee986110dd9293f4278e48
index:oauth2_authorization_code_session_client_id_idx:33571c1dd85e565717e0324406f5469abd79542dc46f5ae1a53c33cebbc3eb82
index:oauth2_authorization_code_session_client_id_subject_idx:037b0204f1c7527cb3d7fafcd3d6872a9438bcf83070cb68e59af17fd2d2e9c6
index:oauth2_authorization_code_session_request_id_idx:56431f95f8e24a9fd1f84fde140326ea31e2a32322c16fdb3268a1a1710f6b2e
index:oauth2_blacklisted_jti_signature_key:cfe85995d6e6d2cc4c498bb56fb8a8c2725515416317c6c76dbfae87fefaf181
index:oauth2_consent_session_challenge_id_key:029ca233e346460a184c8f13a5deede0ad9296b311a2c7ee5cf5dcb956395953
index:oauth2_device_code_session_client_id_idx:7155ed661bbda3715a4372f676a99121c1edf837d15434feb0e884c3f754e360
index:oauth2_device_code_session_client_id_subject_idx:55d030a505bbb2f8f74fe9aed79ce8812cc9e18ebca9f90f70267f2864c2fd12
index:oauth2_device_code_session_request_id_idx:25484c351ee0baa740c2fe7eb3f4fae4489f57229603262a54e89ab9b5c28806
index:oauth2_openid_connect_session_client_id_idx:5eaaf43ea5284595e20a4361735afcf50335e262c21b60dfd8187b108467b3d2
index:oauth2_openid_connect_session_client_id_subject_idx:d69157fc2795d51ecaeb87f3f68eee029cb9152164d51572efada291669c94db
index:oauth2_openid_connect_session_request_id_idx:e346a7490f83af0536666592018b1940306741da950261c57c0d001e4f5028ed
index:oauth2_par_context_signature_key:a6215ef340fd125d0560c7b00a6f280c2edcbe35c4e926de45314edd854c65ae
index:oauth2_pkce_request_session_client_id_idx:b0d0079dc9ff37613a0222e05c8079457d13bf843488966a87d7ce3233a670f2
index:oauth2_pkce_request_session_client_id_subject_idx:1ac9bb2cb0c52bbb3d76e1b933b0f730cca0cb2445a5b7f3e996e34b3c9bfc79
index:oauth2_pkce_request_session_request_id_idx:300f7c1364a9657d65d4896d1dcf1ab751adc044c9c94d69eba32606387ef698
index:oauth2_refresh_token_session_client_id_idx:f1865951c65f206ca812fccfcc98a3f6be1d1644c0503fd6dba0cd1e2ae82e66
index:oauth2_refresh_token_session_client_id_subject_idx:a8dd822346fa15e1082aa0d25f0bcd2c275d2a0af218405c737cecd622eb562b
index:oauth2_refresh_token_session_request_id_idx:f117051f4adea7ded2ee76996b119bab5e7a1810f0b41450b2c28e272fe334bc
index:one_time_code_lookup_key:ff55f597023e4519fefbc8259b26ce24237fa098b5b74051c6bf97a587390fb9
index:totp_configurations_username_key:3a38a39ad89a6a772645609fb0e18ee4f426945c138dd92059d686bdceac75ca
index:totp_history_lookup_key:b5836b0f59a3c3c3c3f9e9567f435bc184880d0292cae1612cf0f08f7c4821e3
index:user_opaque_identifier_identifier_key:d1b7b8467a86e3e31e218f4b919c2371e75ae2cb639bb4f930d962780c920404
index:user_opaque_identifier_lookup_key:6c9c34569c326faa6c7a3a852442c38bb0fa12c970be842b58a1f7d36c6d078f
index:user_preferences_username_key:235929f0aba45eda668c003eaf2b703eadcc81070d14d4c5e834870a3aa7a7b3
index:webauthn_credentials_kid_key:4f2117e50c45114f00db257d3487818d0857941d5f2f0709dca3848ba07fb638
index:webauthn_credentials_lookup_key:65c6e7eaba7c681149fd0150c98cdc126120df2980bcaedafaa24e60adb93648
index:webauthn_users_lookup_key:f2ceb712ed0de1dd723ebc54f93e22374f403512d405472b82ebd95f12ca2393
""".split())


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


def _schema(connection: sqlite3.Connection) -> None:
    observed = frozenset(
        f"{kind}:{name}:{hashlib.sha256(sql.encode()).hexdigest()}"
        for kind, name, sql in connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
        )
    )
    if observed != _PINNED_OBJECTS:
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
