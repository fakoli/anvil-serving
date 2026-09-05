"""Opened-object permission inspection for the preprovisioned receiver.

This module intentionally has no receiver dispatcher.  It only classifies the
already-open descriptor supplied by a later, no-follow staging boundary.
"""

from __future__ import annotations

import os
import stat
import struct
import sys

from ..fleet_bootstrap import BootstrapPermissionVerdict


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


def _sid(authority: int, *subauthorities: int) -> bytes:
    return bytes((1, len(subauthorities))) + authority.to_bytes(6, "big") + b"".join(
        value.to_bytes(4, "little") for value in subauthorities
    )


_SYSTEM_SID = _sid(5, 18)
_ADMINISTRATORS_SID = _sid(5, 32, 544)


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
    if owner not in {current, _SYSTEM_SID, _ADMINISTRATORS_SID}:
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
    trusted = {current, _SYSTEM_SID, _ADMINISTRATORS_SID}
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


__all__ = ["inspect_opened_permissions"]
