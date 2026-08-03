"""Read-only inventory and sanitized export of operator-owned configuration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import stat
import sys
import tomllib
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import __version__
from .paths import config_home


DEFAULT_MAX_BYTES = 1024 * 1024
DEFAULT_MAX_FILES = 4096
MAX_EXPORT_SNAPSHOT_BYTES = 16 * 1024 * 1024
DEFAULT_GATEWAY_PATH = "~/.openclaw/openclaw.json"
_VERSIONABLE_NAMES = {
    ".env.example",
    "operator-topology.toml",
    "voice.toml",
    "host.toml",
    "router.toml",
    "serves.toml",
    "serve-recipes.toml",
    "edge.toml",
}
_VERSIONABLE_NAME_RE = re.compile(
    r"^(?:anvil-router|router|serves|voice|host|operator-topology|serve-recipes)"
    r"(?:\.[a-z0-9][a-z0-9._-]*)?\.toml$"
)
_UNSUPPORTED_VERSIONABLE_SUFFIXES = (".yaml", ".yml")
_SECRET_PARTS = {"secrets", "credentials", "identity"}
_RUNTIME_SUFFIXES = (".sqlite", ".sqlite3", ".db", ".log", ".pid")
_CACHE_PARTS = {"cache", "caches", "tmp", "temp", "__pycache__"}
_DEPENDENCY_KEYS = {
    "router_config",
    "rollback_router_config",
    "topology",
    "topology_overlay",
    "manifest",
    "manifest_path",
    "serves_manifest",
    "voice_config",
    "compose_file",
}
_SECRET_KEY_RE = re.compile(
    r"(?:^|_)(?:api_?key|token|secret|password|credential|"
    r"authorization|proxy_authorization|cookie|set_cookie|private_?key)(?:$|_)",
    re.IGNORECASE,
)
_ENV_NAME_RE = re.compile(r"^(?=.{1,128}$)[A-Z_][A-Z0-9_]*$")
_ENV_REFERENCE_RE = re.compile(r"^\$\{[A-Z_][A-Z0-9_]*\}$")
_REDACTED_PLACEHOLDER_RE = re.compile(
    r"^<(?:redacted|required|optional|set-via-env|replace-me)>$", re.IGNORECASE
)
_REFERENCE_PROVIDER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_JSON_POINTER_RE = re.compile(
    r"^/(?:[^~/\x00-\x1f]|~[01])+(?:/(?:[^~/\x00-\x1f]|~[01])+)*$"
)
_AUTH_VALUE_RE = re.compile(r"^\s*(?:bearer|basic)\s+\S+\s*$", re.IGNORECASE)
_HEADER_CREDENTIAL_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:[a-z0-9-]*(?:authorization|token|api-?key|cookie))"
    r"\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_CLI_SECRET_ARG_RE = re.compile(
    r"(?:^|\s)--?(?:api-?key|token|cookie|authorization)\s+\S+", re.IGNORECASE
)
_PRIVATE_KEY_RE = re.compile(
    r"(?:-{4,5}\s*BEGIN (?:PGP |SSH2 )?(?:ENCRYPTED )?PRIVATE KEY"
    r"(?: BLOCK)?\s*-{4,5}|PuTTY-User-Key-File-[23]:)",
    re.IGNORECASE,
)
_SECRET_ENV_EXPRESSION_RE = re.compile(
    r"\$\{(?=[A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|CREDENTIAL|API_?KEY|PRIVATE_?KEY)"
    r"[A-Z0-9_]*(?::[-=+?]|[-=+?]))[A-Z_][A-Z0-9_]*"
    r"(?::[-=+?]|[-=+?])[^}]+\}",
    re.IGNORECASE,
)
_URL_CANDIDATE_RE = re.compile(
    r"(?:[a-z][a-z0-9+.-]{0,63}:|//|/|\?)[^\s'\"<>]+", re.IGNORECASE
)


class ConfigExportError(ValueError):
    """A candidate cannot be inventoried or exported safely."""


def default_gateway_path() -> str | None:
    """Return the standard OpenClaw gateway path only when it exists as a file."""

    candidate = Path(DEFAULT_GATEWAY_PATH).expanduser()
    return str(candidate) if candidate.is_file() else None


def _resolved_home(
    home: str | os.PathLike[str] | None,
) -> tuple[Path, os.stat_result]:
    selected = Path(home or config_home()).expanduser().absolute()
    _assert_no_link_components(selected, label="operator config home")
    try:
        before = selected.lstat()
    except OSError as exc:
        raise ConfigExportError(
            f"could not inspect operator config home: {selected}: {exc}"
        ) from exc
    if _is_link_or_reparse(before):
        raise ConfigExportError(
            f"operator config home must not be a symlink: {selected}"
        )
    if not stat.S_ISDIR(before.st_mode):
        raise ConfigExportError(f"operator config home is not a directory: {selected}")
    try:
        root = selected.resolve(strict=True)
        _assert_no_link_components(selected, label="operator config home")
        after = selected.lstat()
        resolved = root.stat()
    except OSError as exc:
        raise ConfigExportError(
            f"operator config home changed during validation: {selected}: {exc}"
        ) from exc
    if (
        _is_link_or_reparse(after)
        or not stat.S_ISDIR(after.st_mode)
        or not stat.S_ISDIR(resolved.st_mode)
        or not os.path.samestat(before, after)
        or not os.path.samestat(before, resolved)
    ):
        raise ConfigExportError(
            f"operator config home changed during validation: {selected}"
        )
    return root, before


def _classification(relative: Path) -> str:
    lower_parts = tuple(part.lower() for part in relative.parts)
    name = relative.name.lower()
    if ".anvil.bak." in name or name.endswith((".bak", ".backup")):
        return "backup"
    if any(part in _CACHE_PARTS for part in lower_parts) or name.endswith(
        (".lock", ".tmp", ".pyc")
    ):
        return "cache"
    if (
        name == ".env"
        or name == "openclaw.json"
        or any(part in _SECRET_PARTS for part in lower_parts)
        or any(
            token in name
            for token in ("secret", "credential", "device-auth", "cookie")
        )
        or ("token" in name and not name.endswith(".example"))
    ):
        return "secret"
    if name.endswith(_RUNTIME_SUFFIXES) or any(
        token in name for token in ("operation-state", "controller-operations")
    ):
        return "runtime"
    if name.endswith(_UNSUPPORTED_VERSIONABLE_SUFFIXES):
        return "unsupported"
    if name in _VERSIONABLE_NAMES or _VERSIONABLE_NAME_RE.fullmatch(name):
        return "versionable"
    return "unknown"


def _parser_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".toml":
        return "toml"
    if suffix == ".json":
        return "json"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    if path.name.lower().startswith(".env"):
        return "env"
    return "binary"


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        reparse_flag and file_attributes & reparse_flag
    )


def _stat_snapshot(metadata: os.stat_result) -> tuple[int, int, int]:
    return (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)


def _path_snapshot(metadata: os.stat_result) -> tuple[int, int]:
    # Windows reports creation/change time differently for path and descriptor
    # stat calls, so only compare their shared stable fields here. Descriptor
    # snapshots below still compare ctime across the read interval.
    return (metadata.st_size, metadata.st_mtime_ns)


def _assert_root_identity(root: Path, identity: os.stat_result) -> None:
    try:
        current = root.lstat()
    except OSError as exc:
        raise ConfigExportError(
            f"operator config home changed during operation: {root}: {exc}"
        ) from exc
    if (
        _is_link_or_reparse(current)
        or not stat.S_ISDIR(current.st_mode)
        or not os.path.samestat(identity, current)
    ):
        raise ConfigExportError(
            f"operator config home changed during operation: {root}"
        )


@contextmanager
def _windows_directory_lock(path: Path):
    if os.name != "nt":
        yield None
        return

    import ctypes
    from ctypes import wintypes

    file_share_read = 0x00000001
    file_share_write = 0x00000002
    generic_read = 0x80000000
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        generic_read,
        file_share_read | file_share_write,
        None,
        open_existing,
        file_flag_backup_semantics | file_flag_open_reparse_point,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in (None, invalid_handle):
        error = ctypes.get_last_error()
        raise ConfigExportError(
            f"could not anchor operator config home: {path}: Windows error {error}"
        )
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    try:
        information = FileInformation()
        get_information = kernel32.GetFileInformationByHandle
        get_information.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
        get_information.restype = wintypes.BOOL
        if not get_information(
            handle, ctypes.byref(information)
        ):
            error = ctypes.get_last_error()
            raise ConfigExportError(
                f"could not inspect anchored operator config home: Windows error {error}"
            )
        if (
            not information.dwFileAttributes & file_attribute_directory
            or information.dwFileAttributes & file_attribute_reparse_point
        ):
            raise ConfigExportError(
                "operator config home anchor must be a non-reparse directory"
            )
        yield handle
    finally:
        close_handle(handle)


def _windows_file_handle(path: Path, *, deny_writes: bool):
    import ctypes
    from ctypes import wintypes

    generic_read = 0x80000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    file_share_delete = 0x00000004
    open_existing = 3
    file_flag_open_reparse_point = 0x00200000
    share_mode = file_share_read
    if not deny_writes:
        share_mode |= file_share_write | file_share_delete
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        generic_read,
        share_mode,
        None,
        open_existing,
        file_flag_open_reparse_point,
        None,
    )
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ConfigExportError(
            f"candidate is unreadable: {path.name}: Windows error {ctypes.get_last_error()}"
        )
    return handle


def _windows_handle_identity(handle) -> tuple[int, int, int, int, int]:
    import ctypes
    from ctypes import wintypes

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [
            ("CreationTime", ctypes.c_longlong),
            ("LastAccessTime", ctypes.c_longlong),
            ("LastWriteTime", ctypes.c_longlong),
            ("ChangeTime", ctypes.c_longlong),
            ("FileAttributes", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    information = FileInformation()
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    get_information.restype = wintypes.BOOL
    basic = FileBasicInfo()
    get_information_ex = kernel32.GetFileInformationByHandleEx
    get_information_ex.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    get_information_ex.restype = wintypes.BOOL
    if not get_information(handle, ctypes.byref(information)) or not get_information_ex(
        handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)
    ):
        raise ConfigExportError(
            "could not inspect candidate handle: Windows error %s"
            % ctypes.get_last_error()
        )
    file_index = (information.nFileIndexHigh << 32) | information.nFileIndexLow
    file_size = (information.nFileSizeHigh << 32) | information.nFileSizeLow
    return (
        information.dwVolumeSerialNumber,
        file_index,
        file_size,
        basic.LastWriteTime,
        basic.ChangeTime,
    )


def _windows_file_identity(path: Path) -> tuple[int, int, int, int, int]:
    import ctypes
    from ctypes import wintypes

    handle = _windows_file_handle(path, deny_writes=False)
    try:
        return _windows_handle_identity(handle)
    finally:
        close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        close_handle(handle)


def _windows_candidate_descriptor(
    path: Path, expected_identity: tuple[int, int, int, int, int]
) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    handle = _windows_file_handle(path, deny_writes=True)
    try:
        if _windows_handle_identity(handle) != expected_identity:
            raise ConfigExportError(
                f"candidate changed since enumeration: {path.name}"
            )
        descriptor = msvcrt.open_osfhandle(
            int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0)
        )
        handle = None
        return descriptor
    finally:
        if handle is not None:
            close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            close_handle(handle)


@contextmanager
def _operator_home_anchor(home: str | os.PathLike[str] | None):
    root, identity = _resolved_home(home)
    if os.name == "nt":
        with _windows_directory_lock(root):
            _assert_root_identity(root, identity)
            yield root, identity, None
        return

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise ConfigExportError(
            f"could not anchor operator config home: {root}: {exc}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or not os.path.samestat(identity, opened):
            raise ConfigExportError(
                f"operator config home changed before anchoring: {root}"
            )
        yield root, identity, descriptor
    finally:
        os.close(descriptor)


def _read_descriptor_bounded(
    descriptor: int, *, path: Path, max_bytes: int
) -> bytes:
    chunks = []
    total = 0
    while total <= max_bytes:
        chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total > max_bytes:
        raise ConfigExportError(
            f"candidate exceeds the {max_bytes}-byte size limit: {path.name}"
        )
    return b"".join(chunks)


def _read_bounded(
    path: Path,
    *,
    max_bytes: int,
    root: Path | None = None,
    root_identity: os.stat_result | None = None,
    expected_identity: tuple[int, int, int, int, int] | None = None,
    expected_windows_identity: tuple[int, int, int, int, int] | None = None,
) -> bytes:
    if (root is None) != (root_identity is None):
        raise ConfigExportError("root and root_identity must be provided together")
    if root is not None and root_identity is not None:
        _assert_root_identity(root, root_identity)
    _assert_no_link_components(path, label="candidate")
    try:
        before = path.lstat()
    except OSError as exc:
        raise ConfigExportError(f"could not inspect candidate {path.name}: {exc}") from exc
    if _is_link_or_reparse(before):
        raise ConfigExportError(f"candidate must not be a symlink: {path.name}")
    if not stat.S_ISREG(before.st_mode):
        raise ConfigExportError(f"candidate must be a regular file: {path.name}")
    if before.st_size > max_bytes:
        raise ConfigExportError(
            f"candidate exceeds the {max_bytes}-byte size limit: {path.name}"
        )
    if expected_identity is not None and (
        _candidate_identity(before) != expected_identity
    ):
        raise ConfigExportError(
            f"candidate changed since enumeration: {path.name}"
        )

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        if os.name == "nt" and expected_windows_identity is not None:
            descriptor = _windows_candidate_descriptor(
                path, expected_windows_identity
            )
        else:
            descriptor = os.open(path, flags)
    except ConfigExportError:
        raise
    except OSError as exc:
        raise ConfigExportError(f"candidate is unreadable: {path.name}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ConfigExportError(f"candidate must be a regular file: {path.name}")
        if opened.st_size > max_bytes:
            raise ConfigExportError(
                f"candidate exceeds the {max_bytes}-byte size limit: {path.name}"
            )
        if expected_identity is not None and (
            _descriptor_identity(opened) != expected_identity[:4]
        ):
            raise ConfigExportError(
                f"candidate changed since enumeration: {path.name}"
            )

        # Validate the pathname again after opening, then compare it to the
        # descriptor. Reads below use that same descriptor, closing the
        # lstat-to-open replacement race without trusting a second pathname
        # open. The component check also catches a swapped ancestor junction.
        _assert_no_link_components(path, label="candidate")
        try:
            after = path.lstat()
        except OSError as exc:
            raise ConfigExportError(
                f"candidate changed during validation: {path.name}: {exc}"
            ) from exc
        if (
            _is_link_or_reparse(after)
            or not stat.S_ISREG(after.st_mode)
            or not os.path.samestat(before, opened)
            or not os.path.samestat(after, opened)
            or _path_snapshot(before) != _path_snapshot(opened)
            or _path_snapshot(after) != _path_snapshot(opened)
            or (
                expected_identity is not None
                and _candidate_identity(after) != expected_identity
            )
        ):
            raise ConfigExportError(
                f"candidate changed during validation: {path.name}"
            )
        if root is not None and root_identity is not None:
            _assert_root_identity(root, root_identity)

        data = _read_descriptor_bounded(
            descriptor, path=path, max_bytes=max_bytes
        )
        middle = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        repeated = _read_descriptor_bounded(
            descriptor, path=path, max_bytes=max_bytes
        )
        final = os.fstat(descriptor)
        if (
            not os.path.samestat(opened, middle)
            or not os.path.samestat(opened, final)
            or _stat_snapshot(middle) != _stat_snapshot(opened)
            or _stat_snapshot(final) != _stat_snapshot(opened)
            or repeated != data
        ):
            raise ConfigExportError(f"candidate changed while reading: {path.name}")
        if root is not None and root_identity is not None:
            _assert_root_identity(root, root_identity)
        return data
    except OSError as exc:
        raise ConfigExportError(f"candidate is unreadable: {path.name}: {exc}") from exc
    finally:
        os.close(descriptor)


def _assert_no_link_components(path: Path, *, label: str) -> None:
    current = path
    while True:
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ConfigExportError(f"could not inspect {label} path component: {exc}") from exc
        else:
            if _is_link_or_reparse(metadata):
                raise ConfigExportError(f"{label} path must not contain a symlink or junction")
        if current.parent == current:
            return
        current = current.parent


def _parse(path: Path, data: bytes, parser: str) -> Any:
    if parser == "binary":
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigExportError(f"candidate is not UTF-8 text: {path.name}") from exc
    try:
        if parser == "toml":
            return tomllib.loads(text)
        if parser == "json":
            return json.loads(text)
    except (tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
        raise ConfigExportError(f"candidate does not parse as {parser}: {path.name}: {exc}") from exc
    return text


def _dependency_values(value: Any, *, key: str = ""):
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from _dependency_values(child, key=str(child_key).lower())
    elif isinstance(value, list):
        for child in value:
            yield from _dependency_values(child, key=key)
    elif isinstance(value, str) and key in _DEPENDENCY_KEYS:
        yield value, False
    elif isinstance(value, str) and key == "up":
        try:
            arguments = shlex.split(value)
        except ValueError as exc:
            raise ConfigExportError(f"could not parse lifecycle command: {exc}") from exc
        for index, argument in enumerate(arguments):
            if argument in {"-f", "--file", "--registry"}:
                if index + 1 >= len(arguments):
                    raise ConfigExportError(
                        f"lifecycle command is missing a value for {argument}"
                    )
                yield arguments[index + 1], argument == "--registry"
            elif argument.startswith(("--file=", "--registry=")):
                flag, raw = argument.split("=", 1)
                if not raw:
                    raise ConfigExportError(
                        f"lifecycle command is missing a value for {flag}"
                    )
                yield raw, flag == "--registry"
            elif argument.startswith("-f") and len(argument) > 2:
                yield argument[2:], False


def _is_external_product_registry(target: Path) -> bool:
    name = target.name.lower()
    if not name.endswith(".toml") or not (
        name == "serve-recipes.toml" or "recipe" in name
    ):
        return False
    packaged_source_root = Path(__file__).resolve().parent.parent / "configs"
    declared_roots = (packaged_source_root,)
    if os.name != "nt":
        declared_roots += (Path("/configs"),)
    for root in declared_roots:
        try:
            target.relative_to(root)
        except ValueError:
            continue
        try:
            _assert_no_link_components(target, label="external product registry")
            metadata = target.lstat()
        except (OSError, ConfigExportError):
            return False
        return stat.S_ISREG(metadata.st_mode) and not _is_link_or_reparse(metadata)
    return False


def _dependencies(
    root: Path,
    source_relative: Path,
    parsed: Any,
    exact_paths: set[str],
    folded_paths: dict[str, list[str]],
) -> list[dict[str, str]]:
    edges = []
    if not isinstance(parsed, (dict, list)):
        return edges
    for raw, external_product_allowed in _dependency_values(parsed):
        source = root / source_relative
        expanded = raw.replace("{dir}", str(source.parent))
        target = Path(expanded).expanduser()
        if not target.is_absolute():
            target = source.parent / target
        target = Path(os.path.abspath(target))
        try:
            relative = target.relative_to(root)
        except ValueError:
            if external_product_allowed and _is_external_product_registry(target):
                edges.append(
                    {
                        "source": source_relative.as_posix(),
                        "target": "<external-product-registry>",
                        "scope": "external-product",
                    }
                )
                continue
            raise ConfigExportError(
                f"dependency outside approved root: {source_relative.name}"
            )
        requested = relative.as_posix()
        if requested in exact_paths:
            normalized = requested
        else:
            matches = folded_paths.get(os.path.normcase(requested), [])
            if len(matches) > 1:
                raise ConfigExportError(
                    f"ambiguous dependency path from: {source_relative.name}"
                )
            normalized = matches[0] if matches else None
        if normalized is None:
            raise ConfigExportError(
                f"unresolved dependency from: {source_relative.name}"
            )
        edges.append(
            {
                "source": source_relative.as_posix(),
                "target": normalized,
            }
        )
    return edges


def _installed_revisions() -> dict[str, str]:
    from .mcp import PROTOCOL_VERSION

    return {
        "anvil_serving": __version__,
        "packaged_mcp_bridge": __version__,
        "mcp_protocol": PROTOCOL_VERSION,
        "python": platform.python_version(),
    }


def _finalize_inventory_rows(
    root: Path,
    rows: list[dict],
    contents: dict[str, bytes],
    parsed_by_path: dict[str, Any],
) -> tuple[list[dict], list[dict], dict[str, bytes]]:
    dependency_edges: list[dict] = []
    rows.sort(key=lambda row: row["path"].lower())
    exact_paths = {row["path"] for row in rows}
    folded_paths: dict[str, list[str]] = {}
    for path in exact_paths:
        folded_paths.setdefault(os.path.normcase(path), []).append(path)
    for row in rows:
        edges = _dependencies(
            root,
            Path(row["path"]),
            parsed_by_path[row["path"]],
            exact_paths,
            folded_paths,
        )
        row["dependencies"] = [
            edge["target"] for edge in edges if edge.get("scope") != "external-product"
        ]
        dependency_edges.extend(edges)
    return rows, dependency_edges, contents


def _inventory_rows(
    root: Path,
    root_identity: os.stat_result,
    *,
    max_bytes: int,
    max_files: int,
    capture_contents: bool,
) -> tuple[list[dict], list[dict], dict[str, bytes]]:
    rows: list[dict] = []
    contents: dict[str, bytes] = {}
    parsed_by_path: dict[str, Any] = {}
    versionable_bytes = 0
    _assert_root_identity(root, root_identity)
    try:
        candidates = []
        for path in root.rglob("*"):
            if len(candidates) >= max_files:
                raise ConfigExportError(
                    f"operator config home exceeds the {max_files}-entry inventory limit"
                )
            candidates.append(path)
        candidates.sort(key=lambda path: path.as_posix().lower())
    except ConfigExportError:
        raise
    except OSError as exc:
        raise ConfigExportError(f"could not enumerate operator config home: {exc}") from exc
    _assert_root_identity(root, root_identity)
    for path in candidates:
        relative = path.relative_to(root)
        relative_text = relative.as_posix()
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise ConfigExportError(
                f"could not inspect candidate {relative_text}: {exc}"
            ) from exc
        if _is_link_or_reparse(metadata):
            raise ConfigExportError(
                f"candidate must not be a symlink or junction: {relative_text}"
            )
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigExportError(
                f"candidate must be a regular file: {relative_text}"
            )
        parser = _parser_type(path)
        classification = _classification(relative)
        if classification == "versionable":
            windows_identity = (
                _windows_file_identity(path) if os.name == "nt" else None
            )
            data = _read_bounded(
                path,
                max_bytes=max_bytes,
                root=root,
                root_identity=root_identity,
                expected_identity=_candidate_identity(metadata),
                expected_windows_identity=windows_identity,
            )
            versionable_bytes += len(data)
            if versionable_bytes > MAX_EXPORT_SNAPSHOT_BYTES:
                raise ConfigExportError(
                    "versionable operator config exceeds the "
                    f"{MAX_EXPORT_SNAPSHOT_BYTES}-byte aggregate snapshot limit"
                )
            parsed = _parse(path, data, parser)
            size_bytes = len(data)
            digest = hashlib.sha256(data).hexdigest()
        else:
            data = b""
            parsed = None
            size_bytes = metadata.st_size
            digest = None
        if capture_contents and classification == "versionable":
            contents[relative_text] = data
        parsed_by_path[relative_text] = parsed
        rows.append(
            {
                "path": relative_text,
                "classification": classification,
                "size_bytes": size_bytes,
                "sha256": digest,
                "parser": parser,
                "dependencies": [],
                **(
                    {"_identity": windows_identity or _candidate_identity(metadata)}
                    if classification == "versionable"
                    else {}
                ),
            }
        )
    _assert_root_identity(root, root_identity)
    return _finalize_inventory_rows(root, rows, contents, parsed_by_path)


def _inventory_rows_posix(
    root: Path,
    root_descriptor: int,
    *,
    max_bytes: int,
    max_files: int,
    capture_contents: bool,
) -> tuple[list[dict], list[dict], dict[str, bytes]]:
    rows: list[dict] = []
    contents: dict[str, bytes] = {}
    parsed_by_path: dict[str, Any] = {}
    versionable_bytes = 0
    entry_count = 0
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)

    try:
        walker = os.fwalk(
            ".",
            topdown=True,
            follow_symlinks=False,
            dir_fd=root_descriptor,
        )
        for directory, directory_names, file_names, directory_descriptor in walker:
            for name in sorted(directory_names):
                entry_count += 1
                if entry_count > max_files:
                    raise ConfigExportError(
                        "operator config home exceeds the "
                        f"{max_files}-entry inventory limit"
                    )
                metadata = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                if _is_link_or_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
                    raise ConfigExportError(
                        f"candidate directory must not be a symlink: {name}"
                    )
            for name in sorted(file_names):
                entry_count += 1
                if entry_count > max_files:
                    raise ConfigExportError(
                        "operator config home exceeds the "
                        f"{max_files}-entry inventory limit"
                    )
                metadata = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                relative = (
                    Path(name)
                    if directory == "."
                    else Path(directory) / name
                )
                relative_text = relative.as_posix()
                if _is_link_or_reparse(metadata):
                    raise ConfigExportError(
                        f"candidate must not be a symlink: {relative_text}"
                    )
                if not stat.S_ISREG(metadata.st_mode):
                    raise ConfigExportError(
                        f"candidate must be a regular file: {relative_text}"
                    )
                parser = _parser_type(relative)
                classification = _classification(relative)
                if classification == "versionable":
                    descriptor = os.open(
                        name,
                        flags,
                        dir_fd=directory_descriptor,
                    )
                    try:
                        opened = os.fstat(descriptor)
                        if (
                            not stat.S_ISREG(opened.st_mode)
                            or not os.path.samestat(metadata, opened)
                            or _path_snapshot(metadata) != _path_snapshot(opened)
                        ):
                            raise ConfigExportError(
                                f"candidate changed during validation: {relative_text}"
                            )
                        data = _read_descriptor_bounded(
                            descriptor,
                            path=relative,
                            max_bytes=max_bytes,
                        )
                        middle = os.fstat(descriptor)
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        repeated = _read_descriptor_bounded(
                            descriptor,
                            path=relative,
                            max_bytes=max_bytes,
                        )
                        final = os.fstat(descriptor)
                        if (
                            not os.path.samestat(opened, middle)
                            or not os.path.samestat(opened, final)
                            or _stat_snapshot(middle) != _stat_snapshot(opened)
                            or _stat_snapshot(final) != _stat_snapshot(opened)
                            or repeated != data
                        ):
                            raise ConfigExportError(
                                f"candidate changed while reading: {relative_text}"
                            )
                    finally:
                        os.close(descriptor)
                    versionable_bytes += len(data)
                    if versionable_bytes > MAX_EXPORT_SNAPSHOT_BYTES:
                        raise ConfigExportError(
                            "versionable operator config exceeds the "
                            f"{MAX_EXPORT_SNAPSHOT_BYTES}-byte aggregate snapshot limit"
                        )
                    parsed = _parse(relative, data, parser)
                    size_bytes = len(data)
                    digest = hashlib.sha256(data).hexdigest()
                else:
                    data = b""
                    parsed = None
                    size_bytes = metadata.st_size
                    digest = None
                if capture_contents and classification == "versionable":
                    contents[relative_text] = data
                parsed_by_path[relative_text] = parsed
                rows.append(
                    {
                        "path": relative_text,
                        "classification": classification,
                        "size_bytes": size_bytes,
                        "sha256": digest,
                        "parser": parser,
                        "dependencies": [],
                        **(
                            {"_identity": _candidate_identity(metadata)}
                            if classification == "versionable"
                            else {}
                        ),
                    }
                )
    except OSError as exc:
        raise ConfigExportError(
            f"could not traverse anchored operator config home: {exc}"
        ) from exc
    return _finalize_inventory_rows(root, rows, contents, parsed_by_path)


def _inventory_with_anchor(
    home: str | os.PathLike[str] | None,
    *,
    max_bytes: int,
    capture_contents: bool,
    max_files: int = DEFAULT_MAX_FILES,
) -> tuple[dict, dict[str, bytes]]:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ConfigExportError("max_bytes must be a positive integer")
    if isinstance(max_files, bool) or not isinstance(max_files, int) or max_files <= 0:
        raise ConfigExportError("max_files must be a positive integer")
    with _operator_home_anchor(home) as (root, root_identity, root_descriptor):
        def capture():
            if root_descriptor is None:
                return _inventory_rows(
                    root,
                    root_identity,
                    max_bytes=max_bytes,
                    max_files=max_files,
                    capture_contents=capture_contents,
                )
            return _inventory_rows_posix(
                root,
                root_descriptor,
                max_bytes=max_bytes,
                max_files=max_files,
                capture_contents=capture_contents,
            )

        rows, edges, contents = capture()
        repeated_rows, repeated_edges, repeated_contents = capture()
        versionable_rows = [
            row for row in rows if row["classification"] == "versionable"
        ]
        repeated_versionable_rows = [
            row for row in repeated_rows if row["classification"] == "versionable"
        ]
        if (
            repeated_versionable_rows != versionable_rows
            or repeated_edges != edges
            or repeated_contents != contents
        ):
            raise ConfigExportError(
                "operator config home changed while capturing a coherent snapshot"
            )
        for row in rows:
            row.pop("_identity", None)
    report = {
        "schema": "operator-config-inventory/v1",
        "effective_home": str(root),
        "read_only": True,
        "files": rows,
        "dependency_edges": edges,
        "installed_revisions": _installed_revisions(),
    }
    return report, contents


def inventory(
    home: str | os.PathLike[str] | None = None,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
) -> dict:
    """Return metadata-only classification for one effective operator home."""

    report, _ = _inventory_with_anchor(
        home,
        max_bytes=max_bytes,
        max_files=max_files,
        capture_contents=False,
    )
    return report


def _descriptor_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _candidate_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    # Path ctime catches same-inode rewrites whose mtime is restored. Windows
    # descriptor ctime has different semantics, so path-to-descriptor binding
    # uses _descriptor_identity while path snapshots retain this fifth field.
    return (*_descriptor_identity(metadata), metadata.st_ctime_ns)


def _enumerate_anchored(
    root: Path,
    root_identity: os.stat_result,
    root_descriptor: int | None,
    *,
    max_files: int,
) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}

    def add(
        relative: Path,
        metadata: os.stat_result,
    ) -> None:
        relative_text = relative.as_posix()
        if _is_link_or_reparse(metadata):
            raise ConfigExportError(f"candidate must not be a symlink: {relative_text}")
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigExportError(
                f"candidate must be a regular file: {relative_text}"
            )
        candidate = {
            "path": relative_text,
            "classification": _classification(relative),
            "parser": _parser_type(relative),
            "size_bytes": metadata.st_size,
            "identity": _candidate_identity(metadata),
        }
        candidates[relative_text] = candidate

    if root_descriptor is None:
        _assert_root_identity(root, root_identity)
        try:
            paths = []
            for path in root.rglob("*"):
                if len(paths) >= max_files:
                    raise ConfigExportError(
                        "operator config home exceeds the "
                        f"{max_files}-entry inventory limit"
                    )
                paths.append(path)
            paths.sort(key=lambda path: path.as_posix().lower())
            for path in paths:
                relative = path.relative_to(root)
                metadata = path.lstat()
                if _is_link_or_reparse(metadata):
                    raise ConfigExportError(
                        f"candidate must not be a symlink: {relative.as_posix()}"
                    )
                if stat.S_ISDIR(metadata.st_mode):
                    continue
                add(relative, metadata)
        except ConfigExportError:
            raise
        except OSError as exc:
            raise ConfigExportError(
                f"could not enumerate operator config home: {exc}"
            ) from exc
        _assert_root_identity(root, root_identity)
        return candidates

    entry_count = 0
    try:
        for directory, directory_names, file_names, directory_descriptor in os.fwalk(
            ".", topdown=True, follow_symlinks=False, dir_fd=root_descriptor
        ):
            for name in sorted(directory_names):
                entry_count += 1
                if entry_count > max_files:
                    raise ConfigExportError(
                        "operator config home exceeds the "
                        f"{max_files}-entry inventory limit"
                    )
                metadata = os.stat(
                    name, dir_fd=directory_descriptor, follow_symlinks=False
                )
                if _is_link_or_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
                    raise ConfigExportError(
                        f"candidate directory must not be a symlink: {name}"
                    )
            for name in sorted(file_names):
                entry_count += 1
                if entry_count > max_files:
                    raise ConfigExportError(
                        "operator config home exceeds the "
                        f"{max_files}-entry inventory limit"
                    )
                metadata = os.stat(
                    name, dir_fd=directory_descriptor, follow_symlinks=False
                )
                relative = Path(name) if directory == "." else Path(directory) / name
                add(relative, metadata)
    except ConfigExportError:
        raise
    except OSError as exc:
        raise ConfigExportError(
            f"could not traverse anchored operator config home: {exc}"
        ) from exc
    return candidates


def _read_relative_posix(
    root_descriptor: int,
    relative: Path,
    *,
    max_bytes: int,
    expected_identity: tuple[int, int, int, int, int] | None = None,
) -> bytes:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    file_flags |= getattr(os, "O_NOFOLLOW", 0)
    directory_descriptor = os.dup(root_descriptor)
    try:
        for part in relative.parts[:-1]:
            metadata = os.stat(
                part, dir_fd=directory_descriptor, follow_symlinks=False
            )
            if _is_link_or_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
                raise ConfigExportError(
                    f"candidate path must not contain a symlink: {relative.as_posix()}"
                )
            child_descriptor = os.open(part, directory_flags, dir_fd=directory_descriptor)
            opened = os.fstat(child_descriptor)
            if not os.path.samestat(metadata, opened):
                os.close(child_descriptor)
                raise ConfigExportError(
                    f"candidate changed during validation: {relative.as_posix()}"
                )
            os.close(directory_descriptor)
            directory_descriptor = child_descriptor

        name = relative.parts[-1]
        metadata = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if expected_identity is not None and (
            _candidate_identity(metadata) != expected_identity
        ):
            raise ConfigExportError(
                f"candidate changed since enumeration: {relative.as_posix()}"
            )
        descriptor = os.open(name, file_flags, dir_fd=directory_descriptor)
        try:
            opened = os.fstat(descriptor)
            if (
                _is_link_or_reparse(metadata)
                or not stat.S_ISREG(opened.st_mode)
                or not os.path.samestat(metadata, opened)
                or (
                    expected_identity is not None
                    and _descriptor_identity(opened) != expected_identity[:4]
                )
            ):
                raise ConfigExportError(
                    f"candidate changed during validation: {relative.as_posix()}"
                )
            data = _read_descriptor_bounded(
                descriptor, path=relative, max_bytes=max_bytes
            )
            middle = os.fstat(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            repeated = _read_descriptor_bounded(
                descriptor, path=relative, max_bytes=max_bytes
            )
            final = os.fstat(descriptor)
            if (
                _candidate_identity(opened) != _candidate_identity(middle)
                or _candidate_identity(opened) != _candidate_identity(final)
                or repeated != data
            ):
                raise ConfigExportError(
                    f"candidate changed while reading: {relative.as_posix()}"
                )
            return data
        finally:
            os.close(descriptor)
    except ConfigExportError:
        raise
    except OSError as exc:
        raise ConfigExportError(
            f"candidate is unreadable: {relative.as_posix()}: {exc}"
        ) from exc
    finally:
        os.close(directory_descriptor)


def _normalize_selected_paths(
    paths: list[str] | tuple[str, ...],
) -> list[str]:
    if not isinstance(paths, (list, tuple)):
        raise ConfigExportError("paths must be an array of relative file paths")
    selected_paths: list[str] = []
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            raise ConfigExportError("paths must contain non-empty strings")
        relative = Path(raw.strip())
        if relative.is_absolute() or ".." in relative.parts:
            raise ConfigExportError(f"selected path must stay relative: {raw}")
        normalized = relative.as_posix()
        if normalized not in selected_paths:
            selected_paths.append(normalized)
    return selected_paths


def _selected_export_snapshot(
    home: str | os.PathLike[str] | None,
    paths: list[str] | tuple[str, ...],
    *,
    max_bytes: int,
    max_files: int,
) -> tuple[dict, dict[str, bytes], dict[str, int], list[str]]:
    selected_paths = _normalize_selected_paths(paths)
    with _operator_home_anchor(home) as (root, root_identity, root_descriptor):
        available = _enumerate_anchored(
            root, root_identity, root_descriptor, max_files=max_files
        )
        exact_paths = set(available)
        folded_paths: dict[str, list[str]] = {}
        for candidate in exact_paths:
            folded_paths.setdefault(os.path.normcase(candidate), []).append(candidate)

        def canonical(requested: str, *, source: str = "selected path") -> str:
            if requested in exact_paths:
                return requested
            matches = folded_paths.get(os.path.normcase(requested), [])
            if len(matches) > 1:
                raise ConfigExportError(f"ambiguous {source}: {requested}")
            if not matches:
                raise ConfigExportError(f"{source} does not exist: {requested}")
            return matches[0]

        pending = [canonical(path) for path in selected_paths]
        selected: set[str] = set()
        contents: dict[str, bytes] = {}
        windows_identities: dict[str, tuple[int, int, int, int, int]] = {}
        rows_by_path: dict[str, dict[str, Any]] = {}
        aggregate_bytes = 0
        edges: list[dict[str, str]] = []

        while pending:
            relative_text = pending.pop()
            if relative_text in selected:
                continue
            metadata = available[relative_text]
            classification = metadata["classification"]
            if classification == "unsupported":
                raise ConfigExportError(
                    "operator config export does not support YAML without a safe "
                    f"stdlib parser: {relative_text}"
                )
            if classification != "versionable":
                raise ConfigExportError(
                    f"selected path is not safe versionable config: {relative_text}"
                )
            relative = Path(relative_text)
            if root_descriptor is None:
                windows_identity = _windows_file_identity(root / relative)
                windows_identities[relative_text] = windows_identity
                data = _read_bounded(
                    root / relative,
                    max_bytes=max_bytes,
                    root=root,
                    root_identity=root_identity,
                    expected_identity=metadata["identity"],
                    expected_windows_identity=windows_identity,
                )
            else:
                data = _read_relative_posix(
                    root_descriptor,
                    relative,
                    max_bytes=max_bytes,
                    expected_identity=metadata["identity"],
                )
            aggregate_bytes += len(data)
            if aggregate_bytes > MAX_EXPORT_SNAPSHOT_BYTES:
                raise ConfigExportError(
                    "versionable operator config exceeds the "
                    f"{MAX_EXPORT_SNAPSHOT_BYTES}-byte aggregate snapshot limit"
                )
            parser = metadata["parser"]
            parsed = _parse(relative, data, parser)
            contents[relative_text] = data
            row = {
                "path": relative_text,
                "classification": classification,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "parser": parser,
                "dependencies": [],
            }
            discovered = _dependencies(
                root, relative, parsed, exact_paths, folded_paths
            )
            row["dependencies"] = [
                edge["target"]
                for edge in discovered
                if edge.get("scope") != "external-product"
            ]
            rows_by_path[relative_text] = row
            edges.extend(discovered)
            selected.add(relative_text)
            pending.extend(
                edge["target"]
                for edge in discovered
                if edge.get("scope") != "external-product"
            )

        repeated: dict[str, bytes] = {}
        for relative_text in sorted(selected):
            relative = Path(relative_text)
            if root_descriptor is None:
                repeated[relative_text] = _read_bounded(
                    root / relative,
                    max_bytes=max_bytes,
                    root=root,
                    root_identity=root_identity,
                    expected_identity=available[relative_text]["identity"],
                    expected_windows_identity=windows_identities[relative_text],
                )
            else:
                repeated[relative_text] = _read_relative_posix(
                    root_descriptor,
                    relative,
                    max_bytes=max_bytes,
                    expected_identity=available[relative_text]["identity"],
                )
        final_available = _enumerate_anchored(
            root, root_identity, root_descriptor, max_files=max_files
        )
        selected_changed = any(
            final_available.get(relative_text) != available[relative_text]
            for relative_text in selected
        )
        if root_descriptor is None and not selected_changed:
            selected_changed = any(
                _windows_file_identity(root / Path(relative_text))
                != windows_identities[relative_text]
                for relative_text in selected
            )
        if repeated != contents or selected_changed:
            raise ConfigExportError(
                "operator config home changed while capturing a coherent snapshot"
            )

    rows = sorted(rows_by_path.values(), key=lambda row: row["path"].lower())
    report = {
        "schema": "operator-config-inventory/v1",
        "effective_home": str(root),
        "read_only": True,
        "files": rows,
        "dependency_edges": edges,
        "installed_revisions": _installed_revisions(),
    }
    classification_counts = {
        classification: sum(
            item["classification"] == classification for item in available.values()
        )
        for classification in (
            "secret", "runtime", "backup", "cache", "unsupported", "unknown"
        )
    }
    return report, contents, classification_counts, selected_paths


def _normalized_key(value: Any) -> str:
    text = str(value)
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()


def _is_secret_key(value: Any) -> bool:
    return bool(_SECRET_KEY_RE.search(_normalized_key(value)))


def _looks_like_secret_reference(value: Any) -> bool:
    return isinstance(value, dict) and (
        value.get("source") in {"env", "file"}
        or ({"source", "id"} <= set(value) and set(value) <= {"source", "provider", "id"})
    )


def _safe_secret_reference(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"source", "provider", "id"}:
        return False
    source = value.get("source")
    provider = value.get("provider")
    reference_id = value.get("id")
    if (
        source not in {"env", "file"}
        or not isinstance(provider, str)
        or not _REFERENCE_PROVIDER_RE.fullmatch(provider)
        or not isinstance(reference_id, str)
    ):
        return False
    if source == "env":
        return bool(_ENV_NAME_RE.fullmatch(reference_id))
    return len(reference_id) <= 256 and bool(_JSON_POINTER_RE.fullmatch(reference_id))


def _is_capability_url(value: str) -> bool:
    decoded = value
    for _ in range(2):
        next_value = urllib.parse.unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    if not any(delimiter in decoded for delimiter in ":/?"):
        return False
    for match in _URL_CANDIDATE_RE.finditer(decoded):
        candidate = match.group(0).rstrip("),];}")
        try:
            parsed = urllib.parse.urlsplit(candidate)
        except ValueError:
            return True
        if (
            parsed.password
            or (parsed.username and parsed.scheme.lower() != "ssh")
            or (parsed.query and "=" in parsed.query)
            or parsed.fragment
            or re.search(r";[^;/?#=]+=", parsed.path)
        ):
            return True
    return False


def _looks_like_secret_literal(value: str) -> bool:
    return bool(
        _AUTH_VALUE_RE.search(value)
        or _HEADER_CREDENTIAL_RE.search(value)
        or _CLI_SECRET_ARG_RE.search(value)
        or _PRIVATE_KEY_RE.search(value)
        or _SECRET_ENV_EXPRESSION_RE.search(value)
    )


def _header_pair(value: Any) -> tuple[str, Any] | None:
    if (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and isinstance(value[0], str)
    ):
        return value[0], value[1]
    return None


def _is_header_collection_key(value: Any) -> bool:
    normalized = _normalized_key(value)
    return normalized in {"headers", "header_pairs", "headers_list"} or normalized.endswith(
        ("_headers", "_header_pairs", "_headers_list")
    )


def _named_secret_record(value: Any) -> tuple[str, Any | None] | None:
    if not isinstance(value, dict):
        return None
    normalized_fields = [(_normalized_key(field), field) for field in value]
    name_fields = {"name", "key", "header", "header_name", "key_name"}
    value_fields = {
        "value",
        "values",
        "header_value",
        "header_values",
        "key_value",
        "key_values",
    }
    secret_names = [
        (actual, value[actual])
        for normalized, actual in normalized_fields
        if normalized in name_fields
        and isinstance(value[actual], str)
        and _is_secret_key(value[actual])
    ]
    if not secret_names:
        return None
    actual_values = [
        actual for normalized, actual in normalized_fields if normalized in value_fields
    ]
    label = str(secret_names[0][1])
    if len(secret_names) != 1 or len(actual_values) != 1:
        return label, None
    return label, actual_values[0]


def _safe_named_secret_value(value: Any) -> bool:
    return _safe_secret_reference(value) or (
        isinstance(value, list)
        and bool(value)
        and all(_safe_secret_reference(item) for item in value)
    )


def _assert_no_secret_literals(value: Any, *, path: str, key: str = "") -> None:
    if _looks_like_secret_reference(value):
        if _safe_secret_reference(value):
            return
        raise ConfigExportError(f"versionable config contains an invalid SecretRef: {path}:{key}")
    named_secret = _named_secret_record(value)
    if named_secret and (
        named_secret[1] is None
        or not _safe_named_secret_value(value[named_secret[1]])
    ):
        raise ConfigExportError(
            f"versionable config contains a named secret value: {path}:{named_secret[0]}"
        )
    if isinstance(value, dict):
        for child_key, child in value.items():
            child_name = str(child_key)
            normalized = _normalized_key(child_name)
            if _is_secret_key(child_name) and not normalized.endswith("_env"):
                if _safe_secret_reference(child):
                    continue
                if _looks_like_secret_reference(child):
                    raise ConfigExportError(
                        f"versionable config contains an invalid SecretRef: "
                        f"{path}:{child_name}"
                    )
                raise ConfigExportError(
                    f"versionable config contains a secret-like field without a SecretRef: "
                    f"{path}:{child_name}"
                )
            _assert_no_secret_literals(child, path=path, key=child_name)
    elif isinstance(value, list):
        if _is_header_collection_key(key):
            flat_pair = _header_pair(value)
            candidates = [value] if flat_pair else value
            for child in candidates:
                pair = _header_pair(child)
                if (
                    pair
                    and _is_secret_key(pair[0])
                    and not _safe_secret_reference(pair[1])
                ):
                    raise ConfigExportError(
                        f"versionable config contains a secret-like header field: "
                        f"{path}:{pair[0]}"
                    )
        for child in value:
            _assert_no_secret_literals(child, path=path, key=key)
    elif isinstance(value, str) and _normalized_key(key).endswith("_env"):
        if not _ENV_NAME_RE.fullmatch(value):
            raise ConfigExportError(
                f"versionable config has an invalid environment reference: {path}:{key}"
            )
    elif isinstance(value, str):
        if _is_capability_url(value):
            raise ConfigExportError(
                f"versionable config contains a capability-bearing URL: {path}:{key}"
            )
        if _looks_like_secret_literal(value):
            raise ConfigExportError(
                f"versionable config contains a credential-like value: {path}:{key}"
            )


def _assert_text_config_safe(text: str, *, parser: str, path: str) -> None:
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if _PRIVATE_KEY_RE.search(raw_line):
            raise ConfigExportError(
                f"versionable config contains private-key material: {path}:{line_number}"
            )
        scrubbed = re.sub(r"\$\{[A-Z_][A-Z0-9_]*\}", "", raw_line)
        stripped = raw_line.strip()
        safe_assignment = False
        if not stripped.startswith("#") and "=" in stripped:
            _, raw_value = stripped.split("=", 1)
            value = raw_value.strip().strip("'\"")
            safe_assignment = bool(
                _ENV_REFERENCE_RE.fullmatch(value)
                or _REDACTED_PLACEHOLDER_RE.fullmatch(value)
            )
        unsafe_comment_assignment = False
        if stripped.startswith("#"):
            comment = stripped[1:].strip()
            delimiters = [index for token in ("=", ":") if (index := comment.find(token)) >= 0]
            if delimiters:
                split_at = min(delimiters)
                name = comment[:split_at].strip()
                value = comment[split_at + 1 :].strip().strip("'\"")
                unsafe_comment_assignment = bool(
                    _is_secret_key(name)
                    and value
                    and not _ENV_REFERENCE_RE.fullmatch(value)
                    and not _REDACTED_PLACEHOLDER_RE.fullmatch(value)
                )
        if (
            (
                _HEADER_CREDENTIAL_RE.search(scrubbed)
                and not safe_assignment
                and (parser != "env" or stripped.startswith("#"))
            )
            or _CLI_SECRET_ARG_RE.search(scrubbed)
            or _SECRET_ENV_EXPRESSION_RE.search(raw_line)
            or unsafe_comment_assignment
        ):
            raise ConfigExportError(
                f"versionable config contains credential-like raw text: "
                f"{path}:{line_number}"
            )
    if parser == "env":
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            normalized = _normalized_key(name.strip())
            value = value.strip()
            if _is_secret_key(normalized) and value and not (
                _ENV_REFERENCE_RE.fullmatch(value)
                or _REDACTED_PLACEHOLDER_RE.fullmatch(value)
            ):
                raise ConfigExportError(
                    f"versionable config contains a secret-like field: {path}:{name.strip()}"
                )
            if _looks_like_secret_literal(value) or _is_capability_url(value):
                raise ConfigExportError(
                    f"versionable config contains a credential-like value: "
                    f"{path}:{name.strip()}"
                )
        return


def _sanitize_gateway(value: Any, *, key: str = "") -> tuple[Any, int]:
    if _looks_like_secret_reference(value):
        if _safe_secret_reference(value):
            return dict(value), 0
        return "<redacted-invalid-secret-ref>", 1
    if isinstance(value, dict):
        if _normalized_key(key) == "env":
            sanitized_env = {str(name): "<redacted>" for name in sorted(value)}
            redactions = sum(value[name] != "<redacted>" for name in value)
            return sanitized_env, redactions
        named_secret = _named_secret_record(value)
        if named_secret and named_secret[1] is None:
            return "<redacted-invalid-named-secret>", 1
        result = {}
        count = 0
        for child_key, child in value.items():
            normalized = _normalized_key(child_key)
            if named_secret and child_key == named_secret[1]:
                if _safe_secret_reference(child):
                    result[child_key] = dict(child)
                elif _safe_named_secret_value(child):
                    result[child_key] = [dict(item) for item in child]
                else:
                    result[child_key] = "<redacted>"
                    count += 1
                continue
            if _is_secret_key(child_key):
                if _safe_secret_reference(child):
                    result[child_key] = dict(child)
                    continue
                if (
                    normalized.endswith("_env")
                    and isinstance(child, str)
                    and _ENV_NAME_RE.fullmatch(child)
                ):
                    result[child_key] = child
                    continue
                result[child_key] = "<redacted>"
                count += 1
                continue
            sanitized, child_count = _sanitize_gateway(child, key=str(child_key))
            result[child_key] = sanitized
            count += child_count
        return result, count
    if isinstance(value, list):
        flat_pair = _header_pair(value) if _is_header_collection_key(key) else None
        if flat_pair and _is_secret_key(flat_pair[0]):
            if _safe_secret_reference(flat_pair[1]):
                return [flat_pair[0], dict(flat_pair[1])], 0
            return [flat_pair[0], "<redacted>"], 1
        result = []
        count = 0
        for child in value:
            pair = _header_pair(child)
            if (
                _is_header_collection_key(key)
                and pair
                and _is_secret_key(pair[0])
            ):
                if _safe_secret_reference(pair[1]):
                    result.append([pair[0], dict(pair[1])])
                else:
                    result.append([pair[0], "<redacted>"])
                    count += 1
                continue
            sanitized, child_count = _sanitize_gateway(child, key=key)
            result.append(sanitized)
            count += child_count
        return result, count
    if isinstance(value, str):
        if _is_capability_url(value):
            return "<redacted-capability-url>", 1
        if _looks_like_secret_literal(value):
            return "<redacted-credential>", 1
    return value, 0


def _safe_mcp_server_fragment(value: Any) -> tuple[dict | None, int]:
    """Return only the known local stdio launch schema; omit every other shape."""

    if not isinstance(value, dict):
        return None, 1
    command = value.get("command")
    args = value.get("args")
    accepted = {
        "anvil-serving": ["mcp", "serve"],
        "python": ["-m", "anvil_serving.cli", "mcp", "serve"],
        "python3": ["-m", "anvil_serving.cli", "mcp", "serve"],
        "py": ["-m", "anvil_serving.cli", "mcp", "serve"],
    }
    if command not in accepted or args != accepted[command]:
        return None, 1
    if "type" in value and value["type"] != "stdio":
        return None, 1

    result: dict[str, Any] = {"command": command, "args": list(args)}
    if value.get("type") == "stdio":
        result["type"] = "stdio"
    if "enabled" in value:
        if not isinstance(value["enabled"], bool):
            return None, 1
        result["enabled"] = value["enabled"]

    redactions = 0
    env = value.get("env")
    if env is not None:
        if not isinstance(env, dict) or not all(
            isinstance(name, str) and _ENV_NAME_RE.fullmatch(name) for name in env
        ):
            return None, 1
        result["env"] = {name: "<redacted>" for name in sorted(env)}
        redactions += len(env)

    allowed_fields = {"type", "command", "args", "env", "enabled"}
    redactions += len(set(value) - allowed_fields)
    return result, redactions


def _gateway_fragment(path: Path, *, max_bytes: int) -> tuple[dict, dict, int]:
    if path.name.lower() != "openclaw.json":
        raise ConfigExportError("gateway configuration must be named openclaw.json")
    _assert_no_link_components(path, label="gateway configuration")
    data = _read_bounded(path, max_bytes=max_bytes)
    parsed = _parse(path, data, "json")
    if not isinstance(parsed, dict):
        raise ConfigExportError("gateway configuration must be a JSON object")
    fragment: dict[str, Any] = {}
    providers = parsed.get("models", {}).get("providers", {})
    if isinstance(providers, dict) and "anvil" in providers:
        fragment["models"] = {"providers": {"anvil": providers["anvil"]}}
    models = parsed.get("agents", {}).get("defaults", {}).get("models", {})
    if isinstance(models, dict):
        selected = {name: value for name, value in models.items() if name.startswith("anvil/")}
        if selected:
            fragment["agents"] = {"defaults": {"models": selected}}
    talk = parsed.get("talk")
    if isinstance(talk, dict):
        selected_talk = {
            key: talk[key]
            for key in (
                "consultModel",
                "consultThinkingLevel",
                "consultBootstrapContextMode",
            )
            if key in talk
        }
        realtime = talk.get("realtime")
        if isinstance(realtime, dict):
            selected_realtime = {}
            if realtime.get("provider") == "anvil":
                selected_realtime["provider"] = "anvil"
            realtime_providers = realtime.get("providers")
            if isinstance(realtime_providers, dict) and "anvil" in realtime_providers:
                selected_realtime["providers"] = {"anvil": realtime_providers["anvil"]}
            if selected_realtime:
                selected_talk["realtime"] = selected_realtime
        if selected_talk:
            fragment["talk"] = selected_talk
    pre_redactions = 0
    servers = parsed.get("mcpServers")
    if isinstance(servers, dict):
        selected_servers = {}
        for name, value in servers.items():
            if str(name).lower() not in {"anvil", "anvil-serving", "anvil_controller"}:
                continue
            selected, count = _safe_mcp_server_fragment(value)
            pre_redactions += count
            if selected is not None:
                selected_servers[name] = selected
        if selected_servers:
            fragment["mcpServers"] = selected_servers
    sanitized, redactions = _sanitize_gateway(fragment)
    metadata = {
        "path": path.name,
        "classification": "versionable",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "parser": "json",
    }
    return sanitized, metadata, pre_redactions + redactions


def export(
    home: str | os.PathLike[str] | None = None,
    *,
    gateway_path: str | os.PathLike[str] | None = None,
    paths: list[str] | tuple[str, ...] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
) -> dict:
    """Return exact safe config files and an allowlisted, redacted gateway fragment."""

    if paths is None:
        report, contents = _inventory_with_anchor(
            home,
            max_bytes=max_bytes,
            max_files=max_files,
            capture_contents=True,
        )
        classification_counts = {
            classification: sum(
                row["classification"] == classification for row in report["files"]
            )
            for classification in (
                "secret", "runtime", "backup", "cache", "unsupported", "unknown"
            )
        }
        selected_paths: list[str] | None = None
    else:
        report, contents, classification_counts, selected_paths = (
            _selected_export_snapshot(
                home,
                paths,
                max_bytes=max_bytes,
                max_files=max_files,
            )
        )
    by_path = {row["path"]: row for row in report["files"]}
    if paths is None:
        selected = {
            row["path"]
            for row in report["files"]
            if row["classification"] in {"versionable", "unsupported"}
        }
    else:
        selected = set(by_path)

    pending = list(selected)
    while pending:
        relative = pending.pop()
        row = by_path.get(relative)
        if row is None:
            raise ConfigExportError(f"selected path does not exist: {relative}")
        if row["classification"] == "unsupported":
            raise ConfigExportError(
                "operator config export does not support YAML without a safe "
                f"stdlib parser: {relative}"
            )
        if row["classification"] != "versionable":
            raise ConfigExportError(
                f"selected path is not safe versionable config: {relative}"
            )
        for dependency in row["dependencies"]:
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)

    exported = []
    for row in report["files"]:
        if row["path"] not in selected:
            continue
        path = Path(report["effective_home"]) / Path(row["path"])
        data = contents[row["path"]]
        parsed = _parse(path, data, row["parser"])
        if row["parser"] in {"toml", "json"}:
            _assert_no_secret_literals(parsed, path=row["path"])
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigExportError(
                f"versionable config is not UTF-8 text: {row['path']}"
            ) from exc
        _assert_text_config_safe(content, parser=row["parser"], path=row["path"])
        exported.append({**row, "content": content})
    fragment = {}
    gateway_metadata = None
    redaction_count = 0
    if gateway_path:
        resolved_gateway = Path(gateway_path).expanduser().absolute()
        fragment, gateway_metadata, redaction_count = _gateway_fragment(
            resolved_gateway, max_bytes=max_bytes
        )
    return {
        "schema": "operator-config-export/v1",
        "effective_home": report["effective_home"],
        "read_only": True,
        "selected_paths": selected_paths,
        "dependency_complete": True,
        "files": exported,
        "dependency_edges": report["dependency_edges"],
        "gateway_fragment": fragment,
        "gateway_metadata": gateway_metadata,
        "installed_revisions": report["installed_revisions"],
        "redaction_count": redaction_count,
        "excluded_counts": classification_counts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="anvil-serving host config")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("inventory", "export"):
        command = subparsers.add_parser(name)
        command.add_argument("--home")
        command.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
        if name == "export":
            command.add_argument("--gateway-path")
            command.add_argument("--path", dest="paths", action="append")
    args = parser.parse_args(argv)
    try:
        if args.command == "inventory":
            result = inventory(args.home, max_bytes=args.max_bytes)
        else:
            result = export(
                args.home,
                gateway_path=args.gateway_path,
                paths=args.paths,
                max_bytes=args.max_bytes,
            )
    except ConfigExportError as exc:
        print(f"[anvil-serving] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
