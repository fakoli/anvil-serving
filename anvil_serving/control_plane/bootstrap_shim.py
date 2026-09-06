"""Opened-object permission inspection for the preprovisioned receiver.

This module intentionally has no receiver dispatcher.  It only classifies the
already-open descriptor supplied by a later, no-follow staging boundary.
"""

from __future__ import annotations

import errno
import os
import stat
import struct
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from ..fleet_bootstrap import (
    MAX_BUNDLE_BYTES,
    BootstrapContractError,
    BootstrapErrorCode,
    BootstrapPermissionVerdict,
    _valid_bootstrap_path,
)


_MAX_SECURITY_BYTES = 64 * 1024
_MAX_ACES = 256
_MAX_SID_BYTES = 68
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_TYPE_DISK = 1
_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x1
_DACL_SECURITY_INFORMATION = 0x4
_TOKEN_QUERY = 0x8
_TOKEN_USER = 1
_ACCESS_ALLOWED_ACE_TYPE = 0
_ACCESS_DENIED_ACE_TYPE = 1
_INHERIT_ONLY_ACE = 0x8
_KNOWN_ACE_FLAGS = 0x1F

_FILE_WRITE_DATA = 0x2
_FILE_APPEND_DATA = 0x4
_FILE_WRITE_EA = 0x10
_FILE_WRITE_ATTRIBUTES = 0x100
_FILE_DELETE_CHILD = 0x40
_DELETE = 0x10000
_WRITE_DAC = 0x40000
_WRITE_OWNER = 0x80000
_GENERIC_WRITE = 0x40000000
_GENERIC_ALL = 0x10000000
_MUTATION_MASK = (
    _FILE_WRITE_DATA
    | _FILE_APPEND_DATA
    | _FILE_WRITE_EA
    | _FILE_WRITE_ATTRIBUTES
    | _FILE_DELETE_CHILD
    | _DELETE
    | _WRITE_DAC
    | _WRITE_OWNER
    | _GENERIC_WRITE
    | _GENERIC_ALL
)
_OWNER_WRITE_MASK = (
    _FILE_WRITE_DATA
    | _FILE_APPEND_DATA
    | _FILE_WRITE_EA
    | _FILE_WRITE_ATTRIBUTES
    | _GENERIC_WRITE
    | _GENERIC_ALL
)
_KNOWN_ACCESS_MASK = (
    _MUTATION_MASK
    | 0x1  # FILE_READ_DATA
    | 0x8  # FILE_READ_EA
    | 0x20  # FILE_EXECUTE
    | 0x80  # FILE_READ_ATTRIBUTES
    | 0x20000  # READ_CONTROL
    | 0x100000  # SYNCHRONIZE
    | 0x20000000  # GENERIC_EXECUTE
    | 0x80000000  # GENERIC_READ
)
_MAX_COMPONENTS = 64
_READ_CHUNK_BYTES = 64 * 1024


class _UnsafeObject(ValueError):
    pass


def _reader_refusal(code: BootstrapErrorCode) -> BootstrapContractError:
    messages = {
        BootstrapErrorCode.INVALID_CONTRACT: "trusted file inputs are invalid",
        BootstrapErrorCode.UNSAFE_PATH: "trusted file path is unsafe",
        BootstrapErrorCode.PRECONDITION_FAILED: "trusted file precondition failed",
        BootstrapErrorCode.UNSUPPORTED_PLATFORM: "trusted file platform is unsupported",
    }
    return BootstrapContractError(code.value, messages[code])


def _sid(authority: int, *subauthorities: int) -> bytes:
    return bytes((1, len(subauthorities))) + authority.to_bytes(6, "big") + b"".join(
        value.to_bytes(4, "little") for value in subauthorities
    )


_SYSTEM_SID = _sid(5, 18)
_ADMINISTRATORS_SID = _sid(5, 32, 544)
# Windows Resource Protection owns some system ancestors. This exception must
# never expand the set of trusted receiver/configuration file principals.
_TRUSTED_INSTALLER_SID = _sid(
    5, 80, 956008885, 3418522649, 1831038044, 1853292631, 2271478464
)


def _valid_sid(value: object) -> bool:
    if type(value) is not bytes or not 8 <= len(value) <= _MAX_SID_BYTES:
        return False
    count = value[1]
    return value[0] == 1 and count <= 15 and len(value) == 8 + 4 * count


def _classify_linux_mode(
    mode: object,
    owner_uid: object,
    effective_uid: object,
    *,
    ancestor: bool,
) -> BootstrapPermissionVerdict:
    """Classify an fstat snapshot without consulting any path or access() API."""

    if (
        type(mode) is not int
        or type(owner_uid) is not int
        or type(effective_uid) is not int
        or mode < 0
        or owner_uid < 0
        or effective_uid < 0
    ):
        return BootstrapPermissionVerdict.INDETERMINATE
    is_directory = stat.S_ISDIR(mode)
    if not (stat.S_ISREG(mode) or is_directory) or (ancestor and not is_directory):
        return BootstrapPermissionVerdict.INDETERMINATE
    if owner_uid not in {effective_uid, 0}:
        return BootstrapPermissionVerdict.UNTRUSTED_WRITABLE
    group_or_other_write = mode & (stat.S_IWGRP | stat.S_IWOTH)
    if group_or_other_write and not (ancestor and mode & stat.S_ISVTX):
        return BootstrapPermissionVerdict.UNTRUSTED_WRITABLE
    if mode & stat.S_IWUSR:
        return BootstrapPermissionVerdict.OWNER_WRITABLE
    return BootstrapPermissionVerdict.OWNER_READONLY


def _inspect_linux(
    descriptor: int, *, ancestor: bool
) -> BootstrapPermissionVerdict:
    try:
        metadata = os.fstat(descriptor)
        effective_uid = os.geteuid()
    except (AttributeError, OSError, OverflowError, ValueError):
        return BootstrapPermissionVerdict.INDETERMINATE
    return _classify_linux_mode(
        metadata.st_mode,
        metadata.st_uid,
        effective_uid,
        ancestor=ancestor,
    )


def _parse_acl(value: object) -> tuple[tuple[int, int, int, bytes], ...] | None:
    """Parse only bounded ordinary allow/deny ACE bytes."""

    if type(value) is not bytes or not 8 <= len(value) <= _MAX_SECURITY_BYTES:
        return None
    revision, padding, size, count, reserved = struct.unpack_from("<BBHHH", value)
    if revision not in {2, 4} or padding != 0 or reserved != 0 or size != len(value):
        return None
    if count > _MAX_ACES:
        return None
    offset = 8
    parsed: list[tuple[int, int, int, bytes]] = []
    for _ in range(count):
        if offset + 8 > size:
            return None
        ace_type, flags, ace_size = struct.unpack_from("<BBH", value, offset)
        if (
            ace_type not in {_ACCESS_ALLOWED_ACE_TYPE, _ACCESS_DENIED_ACE_TYPE}
            or flags & ~_KNOWN_ACE_FLAGS
            or ace_size < 16
            or ace_size % 4
            or offset + ace_size > size
        ):
            return None
        mask = struct.unpack_from("<I", value, offset + 4)[0]
        sid = value[offset + 8 : offset + ace_size]
        if mask & ~_KNOWN_ACCESS_MASK or not _valid_sid(sid):
            return None
        parsed.append((ace_type, flags, mask, bytes(sid)))
        offset += ace_size
    return tuple(parsed) if offset == size else None


def _classify_windows_acl(
    owner_sid: object,
    current_sid: object,
    dacl: object,
    *,
    ancestor: bool,
) -> BootstrapPermissionVerdict:
    """Conservatively classify copied owner/SID/DACL bytes from one handle."""

    if not _valid_sid(owner_sid) or not _valid_sid(current_sid):
        return BootstrapPermissionVerdict.INDETERMINATE
    owner = bytes(owner_sid)
    current = bytes(current_sid)
    trusted = {current, _SYSTEM_SID, _ADMINISTRATORS_SID}
    if ancestor:
        trusted.add(_TRUSTED_INSTALLER_SID)
    if owner not in trusted:
        return BootstrapPermissionVerdict.UNTRUSTED_WRITABLE
    if dacl is None:
        return BootstrapPermissionVerdict.UNTRUSTED_WRITABLE
    aces = _parse_acl(dacl)
    if aces is None:
        return BootstrapPermissionVerdict.INDETERMINATE
    untrusted_mutation_mask = _MUTATION_MASK
    if ancestor:
        untrusted_mutation_mask &= ~(_FILE_WRITE_DATA | _FILE_APPEND_DATA)
    owner_writable = False
    for ace_type, flags, mask, sid in aces:
        # A deny ACE is structurally validated but never used to subtract an
        # overlapping allow. This is deliberately not an access evaluator.
        if flags & _INHERIT_ONLY_ACE or ace_type == _ACCESS_DENIED_ACE_TYPE:
            continue
        if sid not in trusted and mask & untrusted_mutation_mask:
            return BootstrapPermissionVerdict.UNTRUSTED_WRITABLE
        if (sid == current or sid == owner) and mask & _OWNER_WRITE_MASK:
            owner_writable = True
    return (
        BootstrapPermissionVerdict.OWNER_WRITABLE
        if owner_writable
        else BootstrapPermissionVerdict.OWNER_READONLY
    )


def _sid_from_pointer(pointer: object) -> bytes:
    import ctypes

    address = getattr(pointer, "value", pointer)
    if type(address) is not int or not address:
        raise ValueError
    prefix = ctypes.string_at(address, 8)
    if len(prefix) != 8:
        raise ValueError
    size = 8 + 4 * prefix[1]
    if prefix[0] != 1 or not 8 <= size <= _MAX_SID_BYTES:
        raise ValueError
    value = ctypes.string_at(address, size)
    if not _valid_sid(value):
        raise ValueError
    return bytes(value)


def _acl_from_pointer(pointer: object) -> bytes:
    import ctypes

    address = getattr(pointer, "value", pointer)
    if type(address) is not int or not address:
        raise ValueError
    header = ctypes.string_at(address, 8)
    if len(header) != 8:
        raise ValueError
    size = struct.unpack_from("<H", header, 2)[0]
    if not 8 <= size <= _MAX_SECURITY_BYTES:
        raise ValueError
    return bytes(ctypes.string_at(address, size))


def _windows_current_sid() -> bytes:
    import ctypes
    from ctypes import wintypes

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    open_token = advapi32.OpenProcessToken
    open_token.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    open_token.restype = wintypes.BOOL
    get_information = advapi32.GetTokenInformation
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = []
    get_current_process.restype = wintypes.HANDLE
    token = wintypes.HANDLE()
    if not open_token(get_current_process(), _TOKEN_QUERY, ctypes.byref(token)):
        raise OSError
    try:
        needed = wintypes.DWORD()
        get_information(token, _TOKEN_USER, None, 0, ctypes.byref(needed))
        if not 1 <= needed.value <= _MAX_SECURITY_BYTES:
            raise ValueError
        buffer = ctypes.create_string_buffer(needed.value)
        if not get_information(
            token, _TOKEN_USER, buffer, needed.value, ctypes.byref(needed)
        ):
            raise OSError
        return _sid_from_pointer(ctypes.cast(buffer, ctypes.POINTER(SidAndAttributes)).contents.Sid)
    finally:
        close_handle(token)


def _inspect_windows(
    descriptor: int, *, ancestor: bool
) -> BootstrapPermissionVerdict:
    """Read only metadata allocated by the OS for this borrowed descriptor."""

    try:
        import ctypes
        import msvcrt
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

        handle = msvcrt.get_osfhandle(descriptor)
        if handle == -1:
            raise OSError
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_file_type = kernel32.GetFileType
        get_file_type.argtypes = [wintypes.HANDLE]
        get_file_type.restype = wintypes.DWORD
        get_information = kernel32.GetFileInformationByHandle
        get_information.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
        get_information.restype = wintypes.BOOL
        if get_file_type(handle) != _FILE_TYPE_DISK:
            raise ValueError
        information = FileInformation()
        if not get_information(handle, ctypes.byref(information)):
            raise OSError
        attributes = information.dwFileAttributes
        is_directory = bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT or (ancestor and not is_directory):
            raise ValueError

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        get_security = advapi32.GetSecurityInfo
        get_security.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        get_security.restype = wintypes.DWORD
        descriptor_pointer = ctypes.c_void_p()
        owner_pointer = ctypes.c_void_p()
        dacl_pointer = ctypes.c_void_p()
        result = get_security(
            handle,
            _SE_FILE_OBJECT,
            _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
            ctypes.byref(owner_pointer),
            None,
            ctypes.byref(dacl_pointer),
            None,
            ctypes.byref(descriptor_pointer),
        )
        if result != 0 or not descriptor_pointer.value:
            raise OSError
        try:
            get_length = advapi32.GetSecurityDescriptorLength
            get_length.argtypes = [ctypes.c_void_p]
            get_length.restype = wintypes.DWORD
            if not 1 <= get_length(descriptor_pointer) <= _MAX_SECURITY_BYTES:
                raise ValueError
            owner = _sid_from_pointer(owner_pointer)
            dacl = None if not dacl_pointer.value else _acl_from_pointer(dacl_pointer)
            current = _windows_current_sid()
            return _classify_windows_acl(owner, current, dacl, ancestor=ancestor)
        finally:
            local_free = kernel32.LocalFree
            local_free.argtypes = [ctypes.c_void_p]
            local_free.restype = ctypes.c_void_p
            local_free(descriptor_pointer)
    except (AttributeError, OSError, OverflowError, ValueError, struct.error):
        return BootstrapPermissionVerdict.INDETERMINATE


def inspect_opened_permissions(
    descriptor: int, *, ancestor: bool = False
) -> BootstrapPermissionVerdict:
    """Return a conservative permission verdict for one still-open descriptor."""

    if type(descriptor) is not int or descriptor < 0 or type(ancestor) is not bool:
        return BootstrapPermissionVerdict.INDETERMINATE
    if sys.platform == "linux":
        return _inspect_linux(descriptor, ancestor=ancestor)
    if sys.platform == "win32":
        return _inspect_windows(descriptor, ancestor=ancestor)
    return BootstrapPermissionVerdict.UNSUPPORTED


def _linux_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _linux_object_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _permission_is_allowed(
    descriptor: int,
    *,
    ancestor: bool,
    require_readonly: bool,
) -> bool:
    verdict = inspect_opened_permissions(descriptor, ancestor=ancestor)
    return verdict is BootstrapPermissionVerdict.OWNER_READONLY or (
        not require_readonly and verdict is BootstrapPermissionVerdict.OWNER_WRITABLE
    )


def _require_permission(
    descriptor: int,
    *,
    ancestor: bool,
    require_readonly: bool,
) -> None:
    if not _permission_is_allowed(
        descriptor,
        ancestor=ancestor,
        require_readonly=require_readonly,
    ):
        raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED)


@dataclass(frozen=True)
class _LinuxAnchor:
    descriptor: int
    parent_descriptor: int | None
    name: str | None
    identity: tuple[int, int]


class _OpenedTrustedFile:
    """Private held descriptors and snapshots for one verified bounded read."""

    def __init__(
        self,
        *,
        platform: str,
        descriptors: list[int],
        file_descriptor: int,
        max_bytes: int,
        require_readonly: bool,
        file_identity: tuple[int, ...],
        file_name: str = "",
        ancestors: tuple[_LinuxAnchor, ...] = (),
        windows_ancestor_identities: tuple[tuple[int, int], ...] = (),
    ) -> None:
        self._platform = platform
        self._descriptors = descriptors
        self._file_descriptor = file_descriptor
        self._max_bytes = max_bytes
        self._require_readonly = require_readonly
        self._file_identity = file_identity
        self._file_name = file_name
        self._ancestors = ancestors
        self._windows_ancestor_identities = windows_ancestor_identities
        self._closed = False

    def __repr__(self) -> str:
        return "OpenedTrustedFile(closed)" if self._closed else "OpenedTrustedFile(open)"

    def _ensure_open(self) -> None:
        if self._closed:
            raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED)

    def _verify_linux(self) -> int:
        try:
            for anchor in self._ancestors:
                current = os.fstat(anchor.descriptor)
                if (
                    not stat.S_ISDIR(current.st_mode)
                    or _linux_object_identity(current) != anchor.identity
                    or not _permission_is_allowed(
                        anchor.descriptor,
                        ancestor=True,
                        require_readonly=False,
                    )
                ):
                    raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED)
                if anchor.parent_descriptor is not None and anchor.name is not None:
                    named = os.stat(
                        anchor.name,
                        dir_fd=anchor.parent_descriptor,
                        follow_symlinks=False,
                    )
                    if (
                        stat.S_ISLNK(named.st_mode)
                        or _linux_object_identity(named) != anchor.identity
                    ):
                        raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED)
            current = os.fstat(self._file_descriptor)
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or _linux_identity(current) != self._file_identity
                or not _permission_is_allowed(
                    self._file_descriptor,
                    ancestor=False,
                    require_readonly=self._require_readonly,
                )
            ):
                raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED)
            if self._ancestors:
                final = self._ancestors[-1]
                named = os.stat(
                    self._file_name,
                    dir_fd=final.descriptor,
                    follow_symlinks=False,
                )
                if stat.S_ISLNK(named.st_mode) or _linux_object_identity(named) != _linux_object_identity(current):
                    raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED)
            return current.st_size
        except BootstrapContractError:
            raise
        except (AttributeError, OSError, OverflowError, ValueError):
            raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED) from None

    def _verify_windows(self) -> int:
        try:
            details = _windows_handle_details_from_descriptor(self._file_descriptor)
            if (
                details[0]
                or details[1] != self._file_identity
                or details[2] != 1
                or not _permission_is_allowed(
                    self._file_descriptor,
                    ancestor=False,
                    require_readonly=self._require_readonly,
                )
            ):
                raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED)
            for descriptor, identity in zip(
                self._descriptors[:-1], self._windows_ancestor_identities, strict=True
            ):
                ancestor = _windows_handle_details_from_descriptor(descriptor)
                if (
                    not ancestor[0]
                    or ancestor[1][:2] != identity
                    or not _permission_is_allowed(
                        descriptor,
                        ancestor=True,
                        require_readonly=False,
                    )
                ):
                    raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED)
            return details[1][2]
        except BootstrapContractError:
            raise
        except (AttributeError, OSError, OverflowError, ValueError):
            raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED) from None

    def _verify(self) -> int:
        self._ensure_open()
        if self._platform == "linux":
            return self._verify_linux()
        return self._verify_windows()

    def read_verified(self) -> bytes:
        size = self._verify()
        if size > self._max_bytes:
            raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED)
        try:
            os.lseek(self._file_descriptor, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            total = 0
            while total <= self._max_bytes:
                chunk = os.read(
                    self._file_descriptor,
                    min(_READ_CHUNK_BYTES, self._max_bytes + 1 - total),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > self._max_bytes or total != size:
                raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED)
            value = b"".join(chunks)
        except BootstrapContractError:
            raise
        except (OSError, OverflowError, ValueError):
            raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED) from None
        if self._verify() != size:
            raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED)
        return value

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        while self._descriptors:
            descriptor = self._descriptors.pop()
            try:
                os.close(descriptor)
            except OSError:
                pass


def _linux_open_trusted_file(
    path: str,
    *,
    max_bytes: int,
    require_readonly: bool,
) -> _OpenedTrustedFile:
    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
    if any(not hasattr(os, flag) for flag in required) or os.stat not in os.supports_dir_fd:
        raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED)
    components = path[1:].split("/")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    descriptors: list[int] = []
    ancestors: list[_LinuxAnchor] = []
    try:
        root = os.open("/", directory_flags)
        descriptors.append(root)
        root_metadata = os.fstat(root)
        if not stat.S_ISDIR(root_metadata.st_mode):
            raise _reader_refusal(BootstrapErrorCode.UNSAFE_PATH)
        _require_permission(root, ancestor=True, require_readonly=False)
        ancestors.append(_LinuxAnchor(root, None, None, _linux_object_identity(root_metadata)))
        parent = root
        for component in components[:-1]:
            descriptor = os.open(component, directory_flags, dir_fd=parent)
            descriptors.append(descriptor)
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                raise _reader_refusal(BootstrapErrorCode.UNSAFE_PATH)
            _require_permission(descriptor, ancestor=True, require_readonly=False)
            ancestors.append(
                _LinuxAnchor(descriptor, parent, component, _linux_object_identity(metadata))
            )
            parent = descriptor
        file_name = components[-1]
        file_descriptor = os.open(file_name, file_flags, dir_fd=parent)
        descriptors.append(file_descriptor)
        metadata = os.fstat(file_descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise _reader_refusal(BootstrapErrorCode.UNSAFE_PATH)
        _require_permission(
            file_descriptor,
            ancestor=False,
            require_readonly=require_readonly,
        )
        if metadata.st_size > max_bytes:
            raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED)
        opened = _OpenedTrustedFile(
            platform="linux",
            descriptors=descriptors,
            file_descriptor=file_descriptor,
            max_bytes=max_bytes,
            require_readonly=require_readonly,
            file_identity=_linux_identity(metadata),
            file_name=file_name,
            ancestors=tuple(ancestors),
        )
        return opened
    except BootstrapContractError:
        while descriptors:
            try:
                os.close(descriptors.pop())
            except OSError:
                pass
        raise
    except OSError as exc:
        while descriptors:
            try:
                os.close(descriptors.pop())
            except OSError:
                pass
        if exc.errno == errno.ELOOP:
            raise _reader_refusal(BootstrapErrorCode.UNSAFE_PATH) from None
        raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED) from None
    except (AttributeError, OverflowError, ValueError):
        while descriptors:
            try:
                os.close(descriptors.pop())
            except OSError:
                pass
        raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED) from None


def _windows_handle_details(handle: int) -> tuple[bool, tuple[int, int, int, int, int], int]:
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
    get_type = kernel32.GetFileType
    get_type.argtypes = [wintypes.HANDLE]
    get_type.restype = wintypes.DWORD
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    get_information.restype = wintypes.BOOL
    get_information_ex = kernel32.GetFileInformationByHandleEx
    get_information_ex.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    get_information_ex.restype = wintypes.BOOL
    if get_type(handle) != _FILE_TYPE_DISK:
        raise _UnsafeObject
    information = FileInformation()
    basic = FileBasicInfo()
    if not get_information(handle, ctypes.byref(information)) or not get_information_ex(
        handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)
    ):
        raise OSError
    if information.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise _UnsafeObject
    identity = (
        int(information.dwVolumeSerialNumber),
        (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow),
        (int(information.nFileSizeHigh) << 32) | int(information.nFileSizeLow),
        int(basic.LastWriteTime),
        int(basic.ChangeTime),
    )
    return bool(information.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY), identity, int(information.nNumberOfLinks)


def _windows_handle_details_from_descriptor(
    descriptor: int,
) -> tuple[bool, tuple[int, int, int, int, int], int]:
    import msvcrt

    handle = msvcrt.get_osfhandle(descriptor)
    if handle == -1:
        raise OSError
    return _windows_handle_details(handle)


def _windows_open_prefix(path: str, *, directory: bool) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    share_read = 0x1
    share_write = 0x2
    open_existing = 3
    backup_semantics = 0x02000000
    open_reparse = 0x00200000
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
        path,
        generic_read,
        share_read | share_write if directory else share_read,
        None,
        open_existing,
        open_reparse | (backup_semantics if directory else 0),
        None,
    )
    if handle in (None, ctypes.c_void_p(-1).value):
        raise OSError
    descriptor: int | None = None
    transferred = False
    try:
        descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0))
        handle = None
        os.set_inheritable(descriptor, False)
        transferred = True
        return descriptor
    finally:
        if descriptor is not None and not transferred:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if handle is not None:
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            close_handle(handle)


def _windows_open_trusted_file(
    path: str,
    *,
    max_bytes: int,
    require_readonly: bool,
) -> _OpenedTrustedFile:
    drive = path[:3]
    components = path[3:].split("\\")
    descriptors: list[int] = []
    identities: list[tuple[int, int]] = []
    try:
        # This is classification only; the retained final handle below remains
        # the authority for content, identity, link and permission validation.
        preliminary = os.lstat(path)
        if (
            stat.S_ISLNK(preliminary.st_mode)
            or getattr(preliminary, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
            or not stat.S_ISREG(preliminary.st_mode)
        ):
            raise _reader_refusal(BootstrapErrorCode.UNSAFE_PATH)
        current = drive
        root = _windows_open_prefix(current, directory=True)
        descriptors.append(root)
        details = _windows_handle_details_from_descriptor(root)
        if not details[0]:
            raise _reader_refusal(BootstrapErrorCode.UNSAFE_PATH)
        _require_permission(root, ancestor=True, require_readonly=False)
        identities.append(details[1][:2])
        for component in components[:-1]:
            current += component + "\\"
            descriptor = _windows_open_prefix(current, directory=True)
            descriptors.append(descriptor)
            details = _windows_handle_details_from_descriptor(descriptor)
            if not details[0]:
                raise _reader_refusal(BootstrapErrorCode.UNSAFE_PATH)
            _require_permission(descriptor, ancestor=True, require_readonly=False)
            identities.append(details[1][:2])
        current += components[-1]
        file_descriptor = _windows_open_prefix(current, directory=False)
        descriptors.append(file_descriptor)
        details = _windows_handle_details_from_descriptor(file_descriptor)
        if details[0] or details[2] != 1:
            raise _reader_refusal(BootstrapErrorCode.UNSAFE_PATH)
        _require_permission(
            file_descriptor,
            ancestor=False,
            require_readonly=require_readonly,
        )
        if details[1][2] > max_bytes:
            raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED)
        return _OpenedTrustedFile(
            platform="windows",
            descriptors=descriptors,
            file_descriptor=file_descriptor,
            max_bytes=max_bytes,
            require_readonly=require_readonly,
            file_identity=details[1],
            windows_ancestor_identities=tuple(identities),
        )
    except BootstrapContractError:
        while descriptors:
            try:
                os.close(descriptors.pop())
            except OSError:
                pass
        raise
    except _UnsafeObject:
        while descriptors:
            try:
                os.close(descriptors.pop())
            except OSError:
                pass
        raise _reader_refusal(BootstrapErrorCode.UNSAFE_PATH) from None
    except (AttributeError, OSError, OverflowError, ValueError):
        while descriptors:
            try:
                os.close(descriptors.pop())
            except OSError:
                pass
        raise _reader_refusal(BootstrapErrorCode.PRECONDITION_FAILED) from None


@contextmanager
def open_trusted_file(
    path: str,
    *,
    max_bytes: int,
    require_readonly: bool,
) -> Iterator[_OpenedTrustedFile]:
    """Hold a verified existing regular file and its ancestors for bounded reads."""
    if type(path) is not str or type(max_bytes) is not int or type(require_readonly) is not bool:
        raise _reader_refusal(BootstrapErrorCode.INVALID_CONTRACT)
    if not 1 <= max_bytes <= MAX_BUNDLE_BYTES:
        raise _reader_refusal(BootstrapErrorCode.INVALID_CONTRACT)
    if sys.platform == "linux":
        platform = "linux"
    elif sys.platform == "win32":
        platform = "windows"
    else:
        raise _reader_refusal(BootstrapErrorCode.UNSUPPORTED_PLATFORM)
    if not _valid_bootstrap_path(path, platform):
        raise _reader_refusal(BootstrapErrorCode.UNSAFE_PATH)
    components = path[1:].split("/") if platform == "linux" else path[3:].split("\\")
    if len(components) > _MAX_COMPONENTS:
        raise _reader_refusal(BootstrapErrorCode.UNSAFE_PATH)
    opened = (
        _linux_open_trusted_file(path, max_bytes=max_bytes, require_readonly=require_readonly)
        if platform == "linux"
        else _windows_open_trusted_file(path, max_bytes=max_bytes, require_readonly=require_readonly)
    )
    try:
        yield opened
    finally:
        opened.close()


__all__ = ["inspect_opened_permissions", "open_trusted_file"]
