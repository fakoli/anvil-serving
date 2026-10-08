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
import subprocess
import stat
import threading
from collections import deque
import sys
import time
import unicodedata
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .identity import CallerSnapshot

from ..control_plane import bootstrap_shim
from ..control_plane.mcp import auth_file
from .. import operator_config


_KEY_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_MAX_KEYS = 1024
_MAX_AUDIT = 10_000
_SUPPORTED_VERSIONS = (1, 2, 3)
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
    caller_snapshot: CallerSnapshot | None = None

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


def _secure_database(path: Path, *, exists: bool, identity: os.stat_result | None = None) -> None:
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
    if identity is not None and not os.path.samestat(info, identity):
        raise KeyStoreError("credential store file changed during creation")
    _private_path(path, directory=False)


def _unlink_created(path: Path, created: os.stat_result) -> None:
    """Failure cleanup must never remove a competing replacement file."""
    try:
        current = path.lstat()
        if stat.S_ISREG(current.st_mode) and os.path.samestat(current, created):
            path.unlink()
    except OSError:
        pass



@contextmanager
def _staged_database(target: Path):
    """Keep SQLite writes in a fresh private directory on the target filesystem."""
    _secure_directory(target.parent, create=True)
    _secure_database(target, exists=False)
    if target.exists():
        raise KeyStoreError("credential store already exists")
    directory = target.parent / (".anvil-keys-" + secrets.token_hex(16))
    try:
        directory.mkdir(mode=0o777 if _is_windows() else 0o700)
    except OSError:
        raise KeyStoreError("credential store staging is unavailable") from None
    created = directory.lstat()
    try:
        _secure_directory(directory, create=False)
        yield directory / "database.sqlite3"
    finally:
        try:
            if os.path.samestat(directory.lstat(), created):
                directory.rmdir()
        except OSError:
            pass


def _publish_database(staged: Path, target: Path, created: os.stat_result) -> None:
    """Publish a completed snapshot atomically; never open the final path for writing."""
    _secure_database(staged, exists=True, identity=created)
    _secure_directory(target.parent, create=False)
    descriptor = os.open(staged, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not os.path.samestat(os.fstat(descriptor), created):
            raise KeyStoreError("credential store file changed during creation")
        _private_created_descriptor(descriptor)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        # Native create-if-absent, as in service installation. Rename/replace
        # would erase a newer database that appeared while SQLite was working.
        os.link(staged, target)
    except FileExistsError:
        raise KeyStoreError("credential store already exists") from None
    try:
        _unlink_created(staged, created)
        _secure_database(target, exists=True, identity=created)
        if not _is_windows():
            descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except (KeyStoreError, OSError):
        _unlink_created(target, created)
        raise


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


def _private_json(path):
    _secure_database(path, exists=True)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _private_created_descriptor(descriptor)
        _secure_database(path, exists=True, identity=os.fstat(descriptor))
        raw = os.read(descriptor, 16385)
        from ..observability.dashboard.contracts import strict_json
        if len(raw) > 16384:
            raise KeyStoreError("credential writer ownership is invalid")
        return strict_json(raw)
    finally:
        os.close(descriptor)


def _validate_store_binding(binding, path):
    if (type(binding) is not dict or set(binding) != {"schema", "store_identity", "gate_identity", "state_path", "owner_id"}
            or binding["schema"] != "router-store-owner/v1"
            or type(binding["owner_id"]) is not str or not 1 <= len(binding["owner_id"]) <= 256
            or type(binding["state_path"]) is not str or not Path(binding["state_path"]).is_absolute()
            or any(type(binding[key]) is not list or len(binding[key]) != 2
                   or any(type(item) is not int or item < 0 for item in binding[key])
                   for key in ("store_identity", "gate_identity"))):
        raise KeyStoreError("credential writer ownership is invalid")
    _secure_database(path, exists=True)
    actual = path.stat()
    if (actual.st_dev, actual.st_ino) != tuple(binding["store_identity"]):
        raise KeyStoreError("credential writer ownership changed")


def _bind_router_store(path, state_path, owner_id):
    """Only the verified native producer binds custody; CLI callers cannot mint it."""
    import fcntl
    path = Path(path).absolute()
    _secure_database(path, exists=True)
    gate = Path(str(path) + ".router-writers.lock")
    if os.path.lexists(gate):
        _secure_database(gate, exists=True)
    descriptor = os.open(gate, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        _private_created_descriptor(descriptor)
        identity = os.fstat(descriptor)
        _secure_database(gate, exists=True, identity=identity)
    finally:
        os.close(descriptor)
    store_identity = path.stat()
    binding = {"schema": "router-store-owner/v1", "owner_id": owner_id,
               "state_path": str(Path(state_path).absolute()),
               "store_identity": [store_identity.st_dev, store_identity.st_ino],
               "gate_identity": [identity.st_dev, identity.st_ino]}
    marker = Path(str(path) + ".router-owner")
    if not os.path.lexists(marker):
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            _private_created_descriptor(descriptor)
            with os.fdopen(descriptor, "w", encoding="utf-8") as out:
                descriptor = -1
                json.dump(binding, out, sort_keys=True, separators=(",", ":"), allow_nan=False)
                out.flush(); os.fsync(out.fileno())
            directory = os.open(marker.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if descriptor != -1:
                os.close(descriptor)
    if _private_json(marker) != binding:
        raise KeyStoreError("credential writer ownership differs")
    descriptor = os.open(gate, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _private_created_descriptor(descriptor)
        if not os.path.samestat(identity, os.fstat(descriptor)):
            raise KeyStoreError("credential writer ownership changed")
        os.write(descriptor, b"router-managed-store/v1\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    def observe():
        _validate_store_binding(_private_json(marker), path)
        if _private_json(marker) != binding:
            raise KeyStoreError("credential writer ownership changed")
        _secure_database(gate, exists=True, identity=identity)
        descriptor = os.open(gate, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            _private_created_descriptor(descriptor)
            if not os.path.samestat(identity, os.fstat(descriptor)):
                raise KeyStoreError("credential writer ownership changed")
            _secure_database(gate, exists=True, identity=identity)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 1, False
            return 0, False
        finally:
            os.close(descriptor)
    return observe


class _WriterConnection(sqlite3.Connection):
    """Apply one owned writer budget to each lock-acquiring statement."""

    writer_deadline: float | None = None

    def execute(self, sql, parameters=(), /):
        if self.writer_deadline is None:
            return super().execute(sql, parameters)
        # One original owner/FIFO deadline, without an unbounded native wait.
        # Always try immediately: available commit/rollback may finish expired.
        sqlite3.Connection.execute(self, "PRAGMA busy_timeout=0")
        while True:
            try:
                return super().execute(sql, parameters)
            except sqlite3.OperationalError as exc:
                if (getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_BUSY
                        or (self.in_transaction and sql != "COMMIT")):
                    raise
                remaining = self.writer_deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(.01, remaining))
                if time.monotonic() >= self.writer_deadline:
                    raise


class KeyStore:
    """A small SQLite-backed device-key store with fail-closed reads."""

    def __init__(self, path: str | os.PathLike[str], *, owner_check=None) -> None:
        self.owner_check = owner_check
        self._writer_condition = threading.Condition()
        self._writer_queue = deque()
        self._writer_context = threading.local()
        self.path = Path(path).expanduser().absolute()
        _secure_database(self.path, exists=True)
        try:
            with self._connect() as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in _SUPPORTED_VERSIONS:
                raise KeyStoreError("credential store format is unsupported")
            self.version = version
        except sqlite3.Error as exc:
            raise KeyStoreError("credential store is unavailable") from exc

    @classmethod
    def initialize(cls, path: str | os.PathLike[str]) -> "KeyStore":
        target = Path(path).expanduser().absolute()
        with _staged_database(target) as staged:
            created = None
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(staged, flags, 0o600)
                try:
                    created = os.fstat(descriptor)
                    _private_created_descriptor(descriptor)
                finally:
                    os.close(descriptor)
                _secure_database(staged, exists=True, identity=created)
                connection = sqlite3.connect(staged)
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
                _secure_database(staged, exists=True, identity=created)
                _publish_database(staged, target, created)
                store = cls(target)
                _secure_database(target, exists=True, identity=created)
                store._created_identity = created
                return store
            except (KeyStoreError, OSError, sqlite3.Error) as exc:
                if created is not None:
                    _unlink_created(target, created)
                raise KeyStoreError("credential store could not be initialized") from exc
            finally:
                if created is not None:
                    _unlink_created(staged, created)

    @contextmanager
    def _ownership(self):
        """Own shared-store work before any writer wait or mutation.

        Ordinary reads keep their existing authentication/snapshot semantics.
        Storage ownership cannot be inherited as inference admission.
        """
        if getattr(self._writer_context, 'offline_custody', False):
            yield
            return
        owner = getattr(self, "_router_admission", None)
        if owner is None:
            with self._external_ownership():
                yield
            return
        from .admission import RouterAdmissionClosed
        try:
            permit = owner.acquire("maintenance", storage_only=True)
        except RouterAdmissionClosed:
            raise KeyStoreError("credential store is quiesced") from None
        try:
            with permit.bind():
                yield
        finally:
            permit.release()

    @contextmanager
    def _offline_custody(self, server, *, producer_descriptor=None, allow_retained=False):
        """Own protected storage with actual producer and writer exclusion.

        The operator separately holds legacy producers stopped. These fences
        exclude current native producers/admin writers; a flag is not that hold.
        """
        if (os.name != 'posix' or not server.router_owner_id
                or server.router_owner_roster != (server.router_owner_id,)
                or not server.admission_state_path
                or Path(server.api_keys_path).expanduser().absolute() != self.path
                or getattr(self._writer_context, 'offline_custody', False)):
            raise KeyStoreError('offline router custody is unavailable')
        import fcntl
        state = Path(server.admission_state_path + '.router').expanduser().absolute()
        _secure_directory(state.parent, create=False)
        producer = Path(str(state) + '.lock')
        gate = Path(str(self.path) + '.router-writers.lock')
        with ExitStack() as held:
            for path in (producer, gate):
                if os.path.lexists(path):
                    _secure_database(path, exists=True)
                if path == producer and producer_descriptor is not None:
                    fd = producer_descriptor
                else:
                    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                    held.callback(os.close, fd)
                _private_created_descriptor(fd)
                _secure_database(path, exists=True, identity=os.fstat(fd))
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise KeyStoreError('offline router custody is busy') from None
                if path == gate:
                    gate_identity, gate_bound = os.fstat(fd), bool(os.read(fd, 64))
            marker = Path(str(self.path) + '.router-owner')
            if allow_retained:
                if os.path.lexists(marker):
                    binding = _private_json(marker)
                    _validate_store_binding(binding, self.path)
                    if (binding['owner_id'] != server.router_owner_id
                            or binding['state_path'] != str(state)
                            or tuple(binding['gate_identity']) != (gate_identity.st_dev, gate_identity.st_ino)):
                        raise KeyStoreError('retained router ownership differs')
                elif gate_bound:
                    raise KeyStoreError('retained router ownership is unavailable')
            elif gate_bound or os.path.lexists(state) or os.path.lexists(marker):
                raise KeyStoreError('retained router ownership requires transfer')
            with self._connect() as db:
                if not allow_retained and self.version == 3 and db.execute('SELECT 1 FROM usage_runs LIMIT 1').fetchone():
                    raise KeyStoreError('retained router runs require owner reconciliation')
            self._writer_context.offline_custody = True
            try:
                yield
            finally:
                del self._writer_context.offline_custody

    @contextmanager
    def _external_ownership(self):
        """Standalone admin writers join the native producer's platform fence."""
        marker = Path(str(self.path) + ".router-owner")
        if os.name != "posix":
            if os.path.lexists(marker):
                raise KeyStoreError("credential writer ownership is unavailable")
            yield
            return
        import fcntl
        gate = Path(str(self.path) + ".router-writers.lock")
        if os.path.lexists(gate):
            _secure_database(gate, exists=True)
        descriptor = os.open(gate, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            _private_created_descriptor(descriptor)
            identity = os.fstat(descriptor)
            _secure_database(gate, exists=True, identity=identity)
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            if not os.path.lexists(marker):
                if os.read(descriptor, 64):
                    raise KeyStoreError("credential writer ownership is unavailable")
                yield
                return
            binding = _private_json(marker)
            _validate_store_binding(binding, self.path)
            if (identity.st_dev, identity.st_ino) != tuple(binding["gate_identity"]):
                raise KeyStoreError("credential writer ownership changed")
            state = _private_json(Path(binding["state_path"]))
            if (type(state) is not dict or set(state) != {"schema", "owner_id", "closure"}
                    or state["schema"] != "router-admission/v1" or state["owner_id"] != binding["owner_id"]
                    or state["closure"] is not None):
                raise KeyStoreError("credential store is quiesced")
            # The shared lock stays held through the actual SQLite writer wait,
            # commit and cleanup. Closure persists before the owner probes zero.
            yield
        finally:
            os.close(descriptor)

    @contextmanager
    def _write(self):
        # SQLite's busy handler does not fairly order local connections. Queue
        # the producer's writers before connection setup too: its PRAGMAs can
        # otherwise starve behind another writer before BEGIN IMMEDIATE.
        with self._ownership():
            deadline = time.monotonic() + 1.0
            ticket = object()
            with self._writer_condition:
                self._writer_queue.append(ticket)
                try:
                    while self._writer_queue[0] is not ticket:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise KeyStoreError("credential writer wait expired")
                        self._writer_condition.wait(remaining)
                except BaseException:
                    self._writer_queue.remove(ticket)
                    self._writer_condition.notify_all()
                    raise
            self._writer_context.deadline = deadline
            try:
                with self._connect() as connection:
                    yield connection
            finally:
                del self._writer_context.deadline
                with self._writer_condition:
                    self._writer_queue.popleft()
                    self._writer_condition.notify_all()

    @contextmanager
    def _connect(self):
        _secure_database(self.path, exists=True)
        deadline = getattr(self._writer_context, "deadline", None)
        def remaining():
            value = 1.0 if deadline is None else deadline - time.monotonic()
            if value <= 0:
                raise KeyStoreError("credential writer wait expired")
            return value
        connection = sqlite3.connect(self.path, timeout=remaining(), isolation_level=None,
                                     factory=_WriterConnection)
        connection.writer_deadline = deadline
        try:
            def bound_wait():
                connection.execute("PRAGMA busy_timeout=" + str(max(1, int(remaining() * 1000))))
            bound_wait()
            connection.execute("PRAGMA synchronous=FULL")
            bound_wait()
            if connection.execute("PRAGMA user_version").fetchone()[0] not in _SUPPORTED_VERSIONS:
                raise KeyStoreError("credential store format is unsupported")
            bound_wait()  # Queue/setup time cannot become a second SQLite wait.
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
                with self._write() as connection:
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

    @staticmethod
    def _bound_actor(connection, key_id):
        """Validate the current ordinary binding without serializing its grants."""
        from .identity import Actor
        binding = connection.execute("SELECT kind,owner_id,revision FROM key_owner_bindings WHERE key_id=?", (key_id,)).fetchone()
        return Actor("unattributed") if binding is None else Actor(*binding)

    def _caller_candidate(self, connection, key_id, now, *, snapshot=False):
        """Read authority together; build bounded metadata only for tracked callers."""
        row = connection.execute(
            "SELECT key_id,name,models,paths,rpm,created_at,expires_at,revoked_at FROM keys WHERE key_id=?",
            (key_id,),
        ).fetchone()
        if row is None:
            return None
        metadata = self._metadata(row)
        if metadata["revoked_at"] is not None or (metadata["expires_at"] is not None and metadata["expires_at"] <= int(now)):
            return None
        policy = {name: tuple(metadata[name]) if name in {"models", "paths"} else metadata[name]
                  for name in ("models", "paths", "rpm", "created_at", "expires_at")}
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        owner = None
        if version in (2, 3):
            from .connect_keys import owned_account
            binding, account = owned_account(connection, key_id)
            if binding is not None:
                owner = binding[:3]
        if not snapshot:
            return Principal(key_id, policy["models"], policy["paths"], owner)
        from .identity import Actor, CallerSnapshot, EffectiveGrant, _digest, connect_reference
        if owner is not None:
            actor = Actor("human", binding[0], binding[3], binding[1], binding[2])
            grant = EffectiveGrant("connect", reference=connect_reference(*binding), revision=binding[3],
                owner=binding[0], generation=binding[1], epoch=binding[2], approval_revision=binding[3],
                account_models=tuple(account["models"]), account_paths=tuple(account["paths"]),
                account_rpm=account["rpm"], account_expires_days=account["expires_days"], **policy)
            return Principal(key_id, policy["models"], policy["paths"], owner,
                             CallerSnapshot(key_id, actor, grant, "owned_human"))
        actor = self._bound_actor(connection, key_id) if version == 3 else Actor("unattributed")
        grant = EffectiveGrant("key_policy", reference=key_id, policy_digest=_digest(policy), **policy)
        caller = CallerSnapshot(key_id, actor, grant, "unbound" if actor.kind == "unattributed" else "owned_" + actor.kind)
        return Principal(key_id, policy["models"], policy["paths"], owner, caller)

    def bind_owner(self, key_id, kind, owner_id, expected_revision, *, dry_run=False):
        """Operator-only ordinary-key binding CAS; grants are unchanged."""
        from .identity import Actor
        if (not isinstance(key_id, str) or _KEY_ID_RE.fullmatch(key_id) is None
                or type(expected_revision) is not int or not 0 <= expected_revision < 2**53-1):
            raise KeyStoreError("invalid owner binding")
        if type(dry_run) is not bool:
            raise KeyStoreError("invalid owner binding")
        actor = Actor(kind, owner_id, expected_revision + 1)
        if actor.kind not in {"human", "service"}:
            raise KeyStoreError("invalid owner binding")
        try:
            with self._write() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute("PRAGMA user_version").fetchone()[0] != 3:
                    raise KeyStoreError("owner binding requires accounting migration")
                principal = self._caller_candidate(connection, key_id, time.time())
                if principal is None or principal.owner is not None:
                    raise KeyStoreError("ordinary credential key is unavailable")
                current = self._bound_actor(connection, key_id).binding_revision or 0
                if current != expected_revision:
                    raise KeyStoreError("admission_policy_changed")
                if dry_run:
                    connection.execute("ROLLBACK")
                    return actor
                connection.execute("INSERT INTO key_owner_bindings VALUES (?,?,?,?) ON CONFLICT(key_id) DO UPDATE SET kind=excluded.kind,owner_id=excluded.owner_id,revision=excluded.revision",
                                   (key_id, kind, owner_id, actor.binding_revision))
                connection.execute("COMMIT")
            return actor
        except sqlite3.Error:
            raise KeyStoreError("owner binding is unavailable") from None

    def authenticate(self, token: str, *, check_owner=True, snapshot=False) -> Principal | None:
        """Authenticate complete grants; opt into bounded tracked metadata explicitly."""
        if type(snapshot) is not bool:
            raise KeyStoreError("invalid authentication request")
        if not isinstance(token, str) or not 40 <= len(token) <= 128:
            return None
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN")
                rows = connection.execute(
                    "SELECT key_id, token_hash "
                    "FROM keys WHERE token_hash = ? LIMIT 2", (digest,)
                ).fetchall()
                if not rows:
                    return None
                if len(rows) != 1:
                    raise KeyStoreError("credential store contains duplicate token hashes")
                key_id, stored = rows[0]
                if (not isinstance(key_id, str) or _KEY_ID_RE.fullmatch(key_id) is None
                        or not isinstance(stored, bytes) or len(stored) != 32 or not hmac.compare_digest(stored, digest)):
                    raise KeyStoreError("credential store token index is invalid")
                from .connect_keys import Denied
                try:
                    principal = self._caller_candidate(connection, key_id, time.time(), snapshot=snapshot)
                except Denied:
                    return None
                connection.execute("COMMIT")
        except KeyStoreError:
            raise
        except sqlite3.Error as exc:
            raise KeyStoreError("credential store is unavailable") from exc
        if principal is not None and principal.owner is not None and check_owner:
            if self.owner_check is None or not self.owner_check(*principal.owner):
                return None
        return principal

    def admit(self, key_id: str | Principal, requested_path=None, requested_model=None, *, normalize=True, local_check=None):
        """Tracked admission freezes the exact locally admitted candidate after commit.

        key_id-only callers retain their existing integer rate-admission API.
        T008 moves the front door to the tracked path/model form.
        """
        from .identity import AdmissionDecision
        candidate = key_id if type(key_id) is Principal else None
        if candidate is not None:
            key_id = candidate.key_id
            if candidate.caller_snapshot is None:
                raise KeyStoreError("admission_policy_changed")
            if local_check is not None and not callable(local_check):
                raise KeyStoreError("invalid admission request")
            if type(normalize) is not bool:
                raise KeyStoreError("invalid admission request")
            # External account liveness is never atomic with local SQLite.
            if candidate.owner is not None and (self.owner_check is None or not self.owner_check(*candidate.owner)):
                raise KeyStoreError("credential key is unavailable")
        elif requested_path is not None or requested_model is not None or local_check is not None:
            raise KeyStoreError("tracked admission requires a principal")
        if not isinstance(key_id, str) or _KEY_ID_RE.fullmatch(key_id) is None:
            raise KeyStoreError("invalid credential key")
        try:
            with self._write() as connection:
                connection.execute("BEGIN IMMEDIATE")
                # Lock contention may outlive the credential. Check authority
                # and refill rate buckets using the time we acquire the lock.
                now = time.time()
                if candidate is not None:
                    from .connect_keys import Denied
                    try:
                        admitted = self._caller_candidate(connection, key_id, now, snapshot=True)
                    except Denied:
                        raise KeyStoreError("admission_policy_changed") from None
                    if admitted != candidate:
                        raise KeyStoreError("admission_policy_changed")
                    method = "GET" if requested_path == _MODELS_PATH else "POST"
                    if not admitted.allows_path(method, requested_path) or (
                            requested_model is not None and not admitted.allows_model(requested_model, normalize=normalize)):
                        raise KeyStoreError("credential access denied")
                    if local_check is not None:
                        local_check(admitted.caller_snapshot, now)
                row = connection.execute(
                    "SELECT rpm, revoked_at, expires_at FROM keys WHERE key_id = ?", (key_id,)
                ).fetchone()
                if row is None or row[1] is not None or (row[2] is not None and row[2] <= int(now)):
                    connection.execute("ROLLBACK")
                    raise KeyStoreError("credential key is unavailable")
                if connection.execute("PRAGMA user_version").fetchone()[0] in (2, 3):
                    from .connect_keys import admit_owner
                    retry = admit_owner(connection, key_id, now)
                    if retry:
                        connection.execute("COMMIT")
                        return AdmissionDecision(retry, None) if candidate is not None else retry
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
                if candidate is not None:
                    return AdmissionDecision(retry_after, None if retry_after else admitted.caller_snapshot)
                return retry_after
        except KeyStoreError:
            raise
        except sqlite3.Error as exc:
            raise KeyStoreError("credential admission is unavailable") from exc

    def revoke(self, key_id: str) -> bool:
        if not isinstance(key_id, str) or _KEY_ID_RE.fullmatch(key_id) is None:
            raise KeyStoreError("invalid credential key")
        try:
            with self._write() as connection:
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
            with self._write() as connection:
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
    for action in ("init", "create", "list", "revoke", "usage", "bind", "backup", "restore", "migrate"):
        item = actions.add_parser(action, allow_abbrev=False)
        item.add_argument("--config", metavar="PATH")
        item.add_argument("--container", metavar="NAME")
        if action == 'migrate':
            item.add_argument('--backup-out', required=True, metavar='PATH')
            item.add_argument('--offline', action='store_true')
            item.add_argument('--confirm', action='store_true')
            item.add_argument('--compose', metavar='PATH')
            item.add_argument('--env-file', metavar='PATH')
        elif action == "create":
            item.add_argument("--name", required=True)
            item.add_argument("--model", action="append", required=True)
            item.add_argument("--path", action="append", required=True)
            item.add_argument("--rpm", type=int, default=60)
            item.add_argument("--expires-days", type=int)
            item.add_argument("--out", required=True, metavar="PATH")
        elif action == "revoke":
            item.add_argument("--key-id", required=True)
        elif action == "bind":
            item.add_argument("--key-id", required=True)
            item.add_argument("--kind", choices=("human", "service"), required=True)
            item.add_argument("--owner-id", required=True)
            item.add_argument("--expected-revision", type=int, required=True)
            item.add_argument("--dry-run", action="store_true")
        elif action in {"backup", "restore"}:
            item.add_argument("--out", required=True, metavar="PATH")
            if action == "restore":
                item.add_argument("--snapshot", required=True, metavar="PATH")
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
        _unlink_created(target, created)
        raise


def dispatch(argv: list[str] | None = None) -> int:
    """Run the local credential CLI; stdout is always public JSON."""
    try:
        args = _parser().parse_args(argv)
        if args.action == 'migrate':
            if not args.offline or not args.confirm or args.container:
                raise KeyStoreError('explicit offline migration is required')
            if args.compose:
                if args.config:
                    raise KeyStoreError('Compose migration uses its mounted router config')
                from ..router_manage import migrate_router_offline
                result = migrate_router_offline(args.compose, args.backup_out, env_file=args.env_file)
            else:
                result = _migrate_offline(args.config, args.backup_out)
            print(json.dumps(result, sort_keys=True))
            return 0
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
        elif args.action in {"backup", "restore"}:
            from .usage_store import UsageStore
            result = (UsageStore(_store_from_config(args.config, initialize=False)).backup(args.out)
                      if args.action == "backup" else UsageStore.restore(args.snapshot, args.out))
        else:
            store = _store_from_config(args.config, initialize=False)
            if args.action == "list":
                result = store.list_keys()
            elif args.action == "bind":
                actor = store.bind_owner(args.key_id, args.kind, args.owner_id, args.expected_revision,
                                         dry_run=args.dry_run)
                result = {"key_id": args.key_id, "actor": actor.to_dict(), "dry_run": args.dry_run}
            elif args.action == "revoke":
                if not store.revoke(args.key_id):
                    raise KeyStoreError("credential key was not found")
                result = {"key_id": args.key_id, "revoked": True}
            else:
                result = store.usage(args.key_id, args.limit)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (KeyStoreError, OSError, sqlite3.Error, ValueError, RuntimeError, subprocess.SubprocessError):
        print("anvil-serving router keys: command failed", file=sys.stderr)
        return 2


def _migrate_offline(config_path, backup_out):
    from .config import load_server_config
    from .serve import resolve_config_path
    from .usage_store import UsageStore
    from ..serves import _switch_role_lock
    server = load_server_config(resolve_config_path(config_path))
    store = _store_from_config(config_path, initialize=False)
    with _switch_role_lock('promotion'), store._offline_custody(server):
        usage = UsageStore(store)
        snapshot = usage.backup(backup_out)
        return {**usage.migrate(), 'backup_schema_version': snapshot['schema_version'], 'offline': True}


if __name__ == "__main__":
    raise SystemExit(dispatch())
