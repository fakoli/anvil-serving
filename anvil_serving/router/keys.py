"""Bounded, local device credentials for the router front door.

The database contains only SHA-256 token digests.  The one-time plaintext
token returned by :meth:`KeyStore.create` is deliberately never retained.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import stat
import sys
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..control_plane import bootstrap_shim
from ..control_plane.mcp import auth_file
from .. import operator_config


_KEY_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_MAX_KEYS = 1024
_MAX_AUDIT = 10_000
_POST_PATHS = frozenset({
    "/v1/chat/completions", "/v1/messages", "/v1/responses",
    "/v1/embeddings", "/v1/rerank",
    "/v1/memory", "/v1/memory/mcp",
})
_MODELS_PATH = "/v1/models"


class KeyStoreError(ValueError):
    """A safe, non-secret-bearing credential-store failure."""


@dataclass(frozen=True)
class Principal:
    """The public identity and closed grants associated with one device key."""

    key_id: str
    models: tuple[str, ...]
    paths: tuple[str, ...]
    owner: tuple[str, str, str] | None = None

    def allows_model(self, model: str, normalize: bool = True) -> bool:
        if not isinstance(model, str):
            return False
        if normalize:
            candidate = model.strip().lower()
            return any(candidate == granted.lower() for granted in self.models)
        return model in self.models

    def allows_path(self, method: str, path: str) -> bool:
        """Return whether this principal has the closed front-door grant."""
        if not isinstance(method, str) or not isinstance(path, str):
            return False
        clean = path.split("?", 1)[0]
        if method.upper() == "GET":
            return clean == _MODELS_PATH and _MODELS_PATH in self.paths
        return method.upper() == "POST" and clean in self.paths and clean in _POST_PATHS


def _is_windows() -> bool:
    return os.name == "nt"


def _audit_key_id(value: str | None) -> bool:
    return value is None or value == "_legacy" or (
        isinstance(value, str) and _KEY_ID_RE.fullmatch(value) is not None
    )


def _audit_method(value: str) -> str:
    value = value.upper()
    return value if value in {"GET", "POST"} else "[other]"


def _audit_path(value: str) -> str:
    value = value.split("?", 1)[0]
    return value if value in _POST_PATHS | {_MODELS_PATH} else "[other]"


def _link_or_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(bootstrap_shim, "_FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _safe_ancestors(path: Path) -> None:
    """Reject lexical link/reparse substitutions without resolving them."""
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except OSError as exc:
            raise KeyStoreError("credential store path is unavailable") from exc
        if _link_or_reparse(info):
            raise KeyStoreError("credential store path is unsafe")


def _windows_open_verification_file(path: Path) -> int:
    """Open a held non-following verifier handle without blocking SQLite writers."""
    import msvcrt
    import ctypes
    from ctypes import wintypes

    handle = operator_config._windows_file_handle(path, deny_writes=False)
    descriptor: int | None = None
    try:
        descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0))
        handle = None
        os.set_inheritable(descriptor, False)
        return descriptor
    except BaseException:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if handle is not None:
            close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            close_handle(handle)
        raise


def _windows_private_path(path: Path, *, directory: bool) -> None:
    """Validate the held Windows object with the shared DACL policy."""
    try:
        descriptor = (
            bootstrap_shim._windows_open_prefix(str(path), directory=True)
            if directory else _windows_open_verification_file(path)
        )
        try:
            is_directory, _identity, links = bootstrap_shim._windows_handle_details_from_descriptor(descriptor)
            if is_directory != directory or (not directory and links != 1):
                raise KeyStoreError("credential store path is unsafe")
            auth_file._require_windows_private_descriptor(descriptor)
        finally:
            os.close(descriptor)
    except KeyStoreError:
        raise
    except (AttributeError, OSError, ValueError, auth_file.AuthFileError, operator_config.ConfigExportError):
        raise KeyStoreError("credential store path is not private") from None


def _posix_private_path(path: Path, *, directory: bool) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        listed = path.lstat()
        descriptor = os.open(path, flags)
        try:
            info = os.fstat(descriptor)
            if (not os.path.samestat(listed, info)
                    or stat.S_ISDIR(info.st_mode) != directory or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or (not directory and info.st_nlink != 1)):
                raise KeyStoreError("credential store path must be owner-only")
        finally:
            os.close(descriptor)
    except KeyStoreError:
        raise
    except (AttributeError, OSError, ValueError):
        raise KeyStoreError("credential store path is unavailable") from None


def _private_path(path: Path, *, directory: bool) -> None:
    _safe_ancestors(path)
    if _is_windows():
        _windows_private_path(path, directory=directory)
    else:
        _posix_private_path(path, directory=directory)


def _private_created_descriptor(descriptor: int) -> None:
    try:
        info = os.fstat(descriptor)
        if _is_windows():
            is_directory, _identity, links = bootstrap_shim._windows_handle_details_from_descriptor(descriptor)
            if is_directory or links != 1:
                raise KeyStoreError("credential store file is unsafe")
            auth_file._require_windows_private_descriptor(descriptor)
        elif (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
              or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise KeyStoreError("credential store file must be owner-only")
    except KeyStoreError:
        raise
    except (AttributeError, OSError, ValueError, auth_file.AuthFileError):
        raise KeyStoreError("credential store file is not private") from None


def _secure_directory(path: Path, *, create: bool) -> None:
    if create:
        # CPython's Windows ``mode=0o700`` adds an effective OWNER_RIGHTS ACE
        # even below a protected owner-only parent. Use the inherited DACL,
        # then validate the held directory descriptor below.
        path.mkdir(mode=0o777 if _is_windows() else 0o700, parents=True, exist_ok=True)
    try:
        info = path.lstat()
    except OSError as exc:
        raise KeyStoreError("credential store directory is unavailable") from exc
    if _link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise KeyStoreError("credential store directory is unsafe")
    _private_path(path, directory=True)


def _secure_database(path: Path, *, exists: bool) -> None:
    _secure_directory(path.parent, create=False)
    try:
        info = path.lstat()
    except FileNotFoundError:
        if exists:
            raise KeyStoreError("credential store is not initialized") from None
        return
    except OSError as exc:
        raise KeyStoreError("credential store is unavailable") from exc
    if _link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise KeyStoreError("credential store file is unsafe")
    _private_path(path, directory=False)


def _json_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > 64:
        raise KeyStoreError("credential store contains invalid grants")
    if any(not isinstance(item, str) or not item or len(item) > 128 for item in value):
        raise KeyStoreError("credential store contains invalid grants")
    if len(set(value)) != len(value):
        raise KeyStoreError("credential store contains invalid grants")
    return tuple(value)


def _stored_grants(models: object, paths: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        parsed_models = json.loads(models) if isinstance(models, str) else None
        parsed_paths = json.loads(paths) if isinstance(paths, str) else None
        return KeyStore._grants(parsed_models, parsed_paths)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise KeyStoreError("credential store contains invalid grants") from None


class KeyStore:
    """A small SQLite-backed device-key store with fail-closed reads."""

    def __init__(self, path: str | os.PathLike[str], *, owner_check=None) -> None:
        self.owner_check = owner_check
        self.path = Path(path).expanduser().absolute()
        _secure_database(self.path, exists=True)
        try:
            with self._connect() as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (1, 2):
                raise KeyStoreError("credential store format is unsupported")
            self.version = version
        except sqlite3.Error as exc:
            raise KeyStoreError("credential store is unavailable") from exc

    @classmethod
    def initialize(cls, path: str | os.PathLike[str]) -> "KeyStore":
        target = Path(path).expanduser().absolute()
        _secure_directory(target.parent, create=True)
        _secure_database(target, exists=False)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(target, flags, 0o600)
        except FileExistsError:
            raise KeyStoreError("credential store already exists") from None
        except OSError as exc:
            raise KeyStoreError("credential store could not be initialized") from exc
        else:
            try:
                _private_created_descriptor(descriptor)
            except KeyStoreError:
                try:
                    target.unlink()
                except OSError:
                    pass
                raise
            finally:
                os.close(descriptor)
        try:
            _secure_database(target, exists=True)
        except KeyStoreError:
            try:
                target.unlink()
            except OSError:
                pass
            raise
        try:
            connection = sqlite3.connect(target)
            try:
                connection.executescript("""
                    PRAGMA journal_mode=DELETE;
                    PRAGMA user_version=1;
                    CREATE TABLE keys (
                        key_id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash BLOB NOT NULL,
                        models TEXT NOT NULL, paths TEXT NOT NULL, rpm INTEGER NOT NULL,
                        created_at INTEGER NOT NULL, expires_at INTEGER, revoked_at INTEGER
                    );
                    CREATE TABLE buckets (
                        key_id TEXT PRIMARY KEY, tokens REAL NOT NULL, updated_at REAL NOT NULL
                    );
                    CREATE TABLE audit (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at INTEGER NOT NULL,
                        key_id TEXT, request_id TEXT NOT NULL, method TEXT NOT NULL,
                        path TEXT NOT NULL, status INTEGER NOT NULL, elapsed_ms INTEGER NOT NULL
                    );
                    CREATE UNIQUE INDEX keys_token_hash ON keys(token_hash);
                    CREATE INDEX audit_key_id_id ON audit(key_id, id DESC);
                """)
            finally:
                connection.close()
            os.chmod(target, 0o600)
        except (OSError, sqlite3.Error) as exc:
            try:
                target.unlink()
            except OSError:
                pass
            raise KeyStoreError("credential store could not be initialized") from exc
        return cls(target)

    @contextmanager
    def _connect(self):
        _secure_database(self.path, exists=True)
        connection = sqlite3.connect(self.path, timeout=1.0, isolation_level=None)
        try:
            connection.execute("PRAGMA busy_timeout=1000")
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _metadata(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
        key_id, name, models, paths, rpm, created_at, expires_at, revoked_at = row
        if (not isinstance(key_id, str) or _KEY_ID_RE.fullmatch(key_id) is None
                or not isinstance(name, str) or not name or len(name) > 128
                or type(rpm) is not int or isinstance(rpm, bool) or not 1 <= rpm <= 100_000
                or type(created_at) is not int or isinstance(created_at, bool) or created_at < 0
                or (expires_at is not None and (type(expires_at) is not int or isinstance(expires_at, bool)))
                or (revoked_at is not None and (type(revoked_at) is not int or isinstance(revoked_at, bool)))):
            raise KeyStoreError("credential store contains invalid key metadata")
        grants_models, grants_paths = _stored_grants(models, paths)
        return {
            "key_id": key_id, "name": name, "models": list(grants_models),
            "paths": list(grants_paths), "rpm": rpm, "created_at": created_at,
            "expires_at": expires_at, "revoked_at": revoked_at,
        }

    @staticmethod
    def _grants(models: list[str], paths: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if not isinstance(models, list) or not models or len(models) > 64:
            raise KeyStoreError("at least one model grant is required")
        if any(
            not isinstance(model, str) or not model.strip() or len(model) > 128
            or "*" in model or any(unicodedata.category(char).startswith("C") for char in model)
            for model in models
        ):
            raise KeyStoreError("invalid model grants")
        granted_models = tuple(model.strip() for model in models)
        if len(set(granted_models)) != len(granted_models):
            raise KeyStoreError("model grants must be unique")
        if not isinstance(paths, list) or not paths or len(paths) > len(_POST_PATHS) + 1:
            raise KeyStoreError("at least one POST path grant is required")
        if any(not isinstance(path, str) for path in paths) or any(path not in _POST_PATHS | {_MODELS_PATH} for path in paths):
            raise KeyStoreError("invalid path grants")
        granted = tuple(dict.fromkeys(paths))
        if len(granted) != len(paths) or not any(path in _POST_PATHS for path in granted):
            raise KeyStoreError("at least one POST path grant is required")
        return granted_models, tuple(dict.fromkeys((_MODELS_PATH, *granted)))

    def create(self, name: str, models: list[str], paths: list[str], rpm: int = 60,
               expires_days: int | None = None, *, owner=None) -> tuple[dict[str, Any], str]:
        if (not isinstance(name, str) or not name.strip() or len(name) > 128
                or any(unicodedata.category(char).startswith("C") for char in name)):
            raise KeyStoreError("invalid key name")
        grants_models, grants_paths = self._grants(models, paths)
        if type(rpm) is not int or not 1 <= rpm <= 100_000:
            raise KeyStoreError("rpm must be an integer from 1 to 100000")
        if expires_days is not None and (type(expires_days) is not int or not 1 <= expires_days <= 3650):
            raise KeyStoreError("expires_days must be an integer from 1 to 3650")
        now = int(time.time())
        expires_at = now + expires_days * 86400 if expires_days is not None else None
        secret = "ask_" + secrets.token_urlsafe(32)
        digest = hashlib.sha256(secret.encode("ascii")).digest()
        for _ in range(4):
            key_id = "key_" + secrets.token_hex(8)
            try:
                with self._connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    if owner is not None:
                        from .connect_keys import authorize_creation
                        authorize_creation(connection, owner, grants_models, grants_paths, rpm, expires_days)
                    count = connection.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
                    if count >= _MAX_KEYS:
                        connection.execute(
                            "DELETE FROM buckets WHERE key_id IN "
                            "(SELECT key_id FROM keys WHERE revoked_at IS NOT NULL OR expires_at <= ?)",
                            (now,),
                        )
                        connection.execute(
                            "DELETE FROM keys WHERE revoked_at IS NOT NULL OR expires_at <= ?", (now,)
                        )
                        count = connection.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
                        if count >= _MAX_KEYS:
                            raise KeyStoreError("credential store key limit reached")
                    connection.execute(
                        "INSERT INTO keys VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                        (key_id, name.strip(), digest, json.dumps(grants_models), json.dumps(grants_paths), rpm, now, expires_at),
                    )
                    if owner is not None:
                        connection.execute("DELETE FROM connect_key_owners WHERE key_id NOT IN (SELECT key_id FROM keys) AND key_id NOT IN (SELECT key_id FROM audit)")
                        connection.execute("INSERT INTO connect_key_owners VALUES (?, ?, ?, ?, ?)", (key_id, *owner))
                    connection.execute("COMMIT")
                return ({"key_id": key_id, "name": name.strip(), "models": list(grants_models),
                         "paths": list(grants_paths), "rpm": rpm, "created_at": now,
                         "expires_at": expires_at, "revoked_at": None}, secret)
            except sqlite3.IntegrityError:
                continue
            except sqlite3.Error as exc:
                raise KeyStoreError("credential store is unavailable") from exc
        raise KeyStoreError("credential key allocation failed")

    def authenticate(self, token: str, *, check_owner=True) -> Principal | None:
        if not isinstance(token, str) or not 40 <= len(token) <= 128:
            return None
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT key_id, token_hash, models, paths, expires_at, revoked_at "
                    "FROM keys WHERE token_hash = ? LIMIT 2", (digest,)
                ).fetchall()
                version = connection.execute("PRAGMA user_version").fetchone()[0]
        except KeyStoreError:
            raise
        except sqlite3.Error as exc:
            raise KeyStoreError("credential store is unavailable") from exc
        if not rows:
            return None
        if len(rows) != 1:
            raise KeyStoreError("credential store contains duplicate token hashes")
        key_id, stored, models, paths, expires_at, revoked_at = rows[0]
        if (not isinstance(key_id, str) or _KEY_ID_RE.fullmatch(key_id) is None
                or not isinstance(stored, bytes) or len(stored) != 32
                or (expires_at is not None and (type(expires_at) is not int or isinstance(expires_at, bool)))
                or (revoked_at is not None and (type(revoked_at) is not int or isinstance(revoked_at, bool)))):
            raise KeyStoreError("credential store contains invalid key metadata")
        if not hmac.compare_digest(stored, digest):
            raise KeyStoreError("credential store token index is invalid")
        if revoked_at is not None or (expires_at is not None and expires_at <= int(time.time())):
            return None
        try:
            grants_models, grants_paths = _stored_grants(models, paths)
            owner = None
            if version == 2:
                from .connect_keys import owned_binding, Denied
                try:
                    owner = owned_binding(self, key_id)
                except Denied:
                    return None
                if owner is not None and check_owner and (self.owner_check is None or not self.owner_check(*owner)):
                    return None
            return Principal(key_id, grants_models, grants_paths, owner)
        except KeyStoreError:
            raise

    def admit(self, key_id: str) -> int:
        if not isinstance(key_id, str) or _KEY_ID_RE.fullmatch(key_id) is None:
            raise KeyStoreError("invalid credential key")
        now = time.time()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT rpm, revoked_at, expires_at FROM keys WHERE key_id = ?", (key_id,)
                ).fetchone()
                if row is None or row[1] is not None or (row[2] is not None and row[2] <= int(now)):
                    connection.execute("ROLLBACK")
                    raise KeyStoreError("credential key is unavailable")
                if connection.execute("PRAGMA user_version").fetchone()[0] == 2:
                    from .connect_keys import admit_owner
                    retry = admit_owner(connection, key_id, now)
                    if retry:
                        connection.execute("COMMIT")
                        return retry
                rpm = row[0]
                if (type(rpm) is not int or isinstance(rpm, bool) or not 1 <= rpm <= 100_000
                        or (row[1] is not None and (type(row[1]) is not int or isinstance(row[1], bool)))
                        or (row[2] is not None and (type(row[2]) is not int or isinstance(row[2], bool)))):
                    connection.execute("ROLLBACK")
                    raise KeyStoreError("credential store contains invalid key metadata")
                bucket = connection.execute(
                    "SELECT tokens, updated_at FROM buckets WHERE key_id = ?", (key_id,)
                ).fetchone()
                tokens, updated = (float(rpm), now) if bucket is None else bucket
                if (type(tokens) not in (int, float) or isinstance(tokens, bool)
                        or type(updated) not in (int, float) or isinstance(updated, bool)
                        or not math.isfinite(tokens) or not math.isfinite(updated)
                        or not 0 <= tokens <= rpm):
                    connection.execute("ROLLBACK")
                    raise KeyStoreError("credential store contains invalid rate state")
                observed_at = max(now, float(updated))
                tokens = min(float(rpm), float(tokens) + (observed_at - float(updated)) * rpm / 60.0)
                if tokens >= 1.0:
                    tokens -= 1.0
                    retry_after = 0
                else:
                    retry_after = max(0, math.ceil(
                        observed_at - now + (1.0 - tokens) * 60.0 / rpm
                    ))
                connection.execute(
                    "INSERT INTO buckets(key_id, tokens, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key_id) DO UPDATE SET tokens=excluded.tokens, updated_at=excluded.updated_at",
                    (key_id, tokens, observed_at),
                )
                connection.execute("COMMIT")
                return retry_after
        except KeyStoreError:
            raise
        except sqlite3.Error as exc:
            raise KeyStoreError("credential admission is unavailable") from exc

    def revoke(self, key_id: str) -> bool:
        if not isinstance(key_id, str) or _KEY_ID_RE.fullmatch(key_id) is None:
            raise KeyStoreError("invalid credential key")
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "UPDATE keys SET revoked_at = COALESCE(revoked_at, ?) WHERE key_id = ?",
                    (int(time.time()), key_id),
                )
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise KeyStoreError("credential store is unavailable") from exc

    def list_keys(self) -> list[dict[str, Any]]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT key_id, name, models, paths, rpm, created_at, expires_at, revoked_at FROM keys ORDER BY created_at, key_id"
                ).fetchall()
            return [self._metadata(row) for row in rows]
        except (sqlite3.Error, json.JSONDecodeError, TypeError) as exc:
            raise KeyStoreError("credential store is unavailable") from exc

    def record(self, key_id: str | None, request_id: str | None, method: str, path: str,
               status: int, elapsed_ms: int) -> None:
        if not _audit_key_id(key_id):
            raise KeyStoreError("invalid credential key")
        if (request_id is not None and (not isinstance(request_id, str) or len(request_id) > 128)
                or not isinstance(method, str) or not isinstance(path, str)
                or type(status) is not int or not 100 <= status <= 599
                or type(elapsed_ms) is not int or not 0 <= elapsed_ms <= 86_400_000):
            raise KeyStoreError("invalid credential audit record")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO audit(recorded_at, key_id, request_id, method, path, status, elapsed_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (int(time.time()), key_id, request_id or "[none]", _audit_method(method), _audit_path(path), status, elapsed_ms),
                )
                connection.execute(
                    "DELETE FROM audit WHERE id <= COALESCE((SELECT id FROM audit ORDER BY id DESC LIMIT 1 OFFSET ?), -1)",
                    (_MAX_AUDIT,),
                )
                connection.execute("COMMIT")
        except sqlite3.Error as exc:
            raise KeyStoreError("credential audit is unavailable") from exc

    def usage(self, key_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if not _audit_key_id(key_id):
            raise KeyStoreError("invalid credential key")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise KeyStoreError("usage limit must be an integer from 1 to 1000")
        try:
            with self._connect() as connection:
                query = "SELECT recorded_at, key_id, request_id, method, path, status, elapsed_ms FROM audit"
                values: tuple[Any, ...] = ()
                if key_id is not None:
                    query += " WHERE key_id = ?"
                    values = (key_id,)
                rows = connection.execute(query + " ORDER BY id DESC LIMIT ?", (*values, limit)).fetchall()
            return [dict(zip(("recorded_at", "key_id", "request_id", "method", "path", "status", "elapsed_ms"), row)) for row in rows]
        except sqlite3.Error as exc:
            raise KeyStoreError("credential store is unavailable") from exc


class _Parser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise KeyStoreError("invalid router keys command arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="anvil-serving router keys", allow_abbrev=False)
    actions = parser.add_subparsers(dest="action", required=True, parser_class=_Parser)
    for action in ("init", "create", "list", "revoke", "usage"):
        item = actions.add_parser(action, allow_abbrev=False)
        item.add_argument("--config", metavar="PATH")
        item.add_argument("--container", metavar="NAME")
        if action == "create":
            item.add_argument("--name", required=True)
            item.add_argument("--model", action="append", required=True)
            item.add_argument("--path", action="append", required=True)
            item.add_argument("--rpm", type=int, default=60)
            item.add_argument("--expires-days", type=int)
            item.add_argument("--out", required=True, metavar="PATH")
        elif action == "revoke":
            item.add_argument("--key-id", required=True)
        elif action == "usage":
            item.add_argument("--key-id")
            item.add_argument("--limit", type=int, default=50)
    return parser


def _store_from_config(config_path: str | None, *, initialize: bool) -> KeyStore:
    from .config import load_server_config
    from .serve import resolve_config_path
    server = load_server_config(resolve_config_path(config_path))
    path = getattr(server, "api_keys_path", None)
    if not isinstance(path, str) or not path:
        raise KeyStoreError("router credential store path is not configured")
    return KeyStore.initialize(path) if initialize else KeyStore(path)


def _write_secret(path: str, secret: str) -> None:
    target = Path(path).expanduser().absolute()
    _secure_directory(target.parent, create=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags, 0o600)
    created = os.fstat(descriptor)
    try:
        _private_created_descriptor(descriptor)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            output.write(secret + "\n")
    except BaseException:
        if descriptor != -1:
            os.close(descriptor)
        try:
            current = target.lstat()
            if (stat.S_ISREG(current.st_mode) and current.st_dev == created.st_dev
                    and current.st_ino == created.st_ino):
                target.unlink()
        except OSError:
            pass
        raise


def dispatch(argv: list[str] | None = None) -> int:
    """Run the local credential CLI; stdout is always public JSON."""
    try:
        args = _parser().parse_args(argv)
        if args.container:
            from .key_container import dispatch_container
            return dispatch_container(args)
        if args.action == "init":
            store = _store_from_config(args.config, initialize=True)
            result: Any = {"initialized": True, "key_count": len(store.list_keys())}
        elif args.action == "create":
            output = Path(args.out).expanduser().absolute()
            if os.path.lexists(output):
                raise KeyStoreError("credential output file already exists")
            store = _store_from_config(args.config, initialize=False)
            metadata, secret = store.create(args.name, args.model, args.path, args.rpm, args.expires_days)
            try:
                _write_secret(args.out, secret)
            except (KeyStoreError, OSError) as exc:
                try:
                    store.revoke(metadata["key_id"])
                except KeyStoreError:
                    pass
                raise KeyStoreError("credential output could not be written") from exc
            result = metadata
        else:
            store = _store_from_config(args.config, initialize=False)
            if args.action == "list":
                result = store.list_keys()
            elif args.action == "revoke":
                if not store.revoke(args.key_id):
                    raise KeyStoreError("credential key was not found")
                result = {"key_id": args.key_id, "revoked": True}
            else:
                result = store.usage(args.key_id, args.limit)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (KeyStoreError, OSError, sqlite3.Error, ValueError):
        print("anvil-serving router keys: command failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(dispatch())
