"""Fail-closed private credential-file reader for remote MCP clients."""

from __future__ import annotations

import os
import errno
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .. import bootstrap_shim


class AuthFileError(ValueError):
    """Credential-file metadata did not meet the local-only policy."""


_MACOS_ACL_TYPE_EXTENDED = 0x00000100
_MACOS_ACL_EXTENDED_ALLOW = 1
_MACOS_ACL_EXTENDED_DENY = 2
_MACOS_ACL_FIRST_ENTRY = 0
_MACOS_ACL_NEXT_ENTRY = -1
_MACOS_ACL_MAX_ENTRIES = 128


def _macos_acl_library():
    """Load Darwin's exported ACL symbols only on the native inspection path."""

    import ctypes

    return ctypes, ctypes.CDLL(None, use_errno=True)


def _macos_extended_acl_entries(
    descriptor: int, *, ancestor_allow_deny_only: bool = False
) -> int:
    """Count extended ACL entries on one held macOS descriptor.

    Credential leaves reject every extended entry. Ancestor directories may
    carry only deny entries, such as macOS's standard ``everyone deny delete``
    home-directory protection. A deny cannot expand access to the credential;
    every allow, unknown tag, malformed enumeration, or unavailable inspection
    remains fail-closed.
    """

    try:
        ctypes, libc = _macos_acl_library()
        get_acl = libc.acl_get_fd_np
        get_acl.argtypes = [ctypes.c_int, ctypes.c_int]
        get_acl.restype = ctypes.c_void_p
        valid_acl = libc.acl_valid
        valid_acl.argtypes = [ctypes.c_void_p]
        valid_acl.restype = ctypes.c_int
        get_entry = libc.acl_get_entry
        get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
        get_entry.restype = ctypes.c_int
        get_tag = libc.acl_get_tag_type
        get_tag.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        get_tag.restype = ctypes.c_int
        free_acl = libc.acl_free
        free_acl.argtypes = [ctypes.c_void_p]
        free_acl.restype = ctypes.c_int
        ctypes.set_errno(0)
        acl = get_acl(descriptor, _MACOS_ACL_TYPE_EXTENDED)
        if not acl:
            if ctypes.get_errno() == errno.ENOENT:
                return 0
            raise OSError
        try:
            ctypes.set_errno(0)
            if valid_acl(acl) != 0:
                raise OSError
            count = 0
            entry_id = _MACOS_ACL_FIRST_ENTRY
            while True:
                entry = ctypes.c_void_p()
                ctypes.set_errno(0)
                result = get_entry(acl, entry_id, ctypes.byref(entry))
                if result == -1:
                    if ctypes.get_errno() == errno.EINVAL:
                        return count
                    raise OSError
                if result != 0 or not entry.value or count >= _MACOS_ACL_MAX_ENTRIES:
                    raise OSError
                if ancestor_allow_deny_only:
                    tag = ctypes.c_int()
                    ctypes.set_errno(0)
                    if get_tag(entry, ctypes.byref(tag)) != 0:
                        raise OSError
                    if tag.value != _MACOS_ACL_EXTENDED_DENY:
                        raise AuthFileError(
                            "auth file ancestor has an access-expanding extended ACL"
                        )
                count += 1
                entry_id = _MACOS_ACL_NEXT_ENTRY
        finally:
            ctypes.set_errno(0)
            if free_acl(acl) != 0:
                raise OSError
    except AuthFileError:
        raise
    except (AttributeError, OSError, OverflowError, ValueError):
        raise AuthFileError("auth file extended ACL inspection is unavailable") from None


def _require_macos_no_extended_acl(
    descriptor: int, *, ancestor_allow_deny_only: bool = False
) -> None:
    if sys.platform == "darwin" and _macos_extended_acl_entries(
        descriptor, ancestor_allow_deny_only=ancestor_allow_deny_only
    ):
        if ancestor_allow_deny_only:
            return
        raise AuthFileError("auth file has an extended ACL")


def _require_posix_private_descriptor(descriptor: int) -> None:
    """Accept only a regular, singly linked file private to its owner."""

    try:
        metadata = os.fstat(descriptor)
        effective_uid = os.geteuid()
    except (AttributeError, OSError, OverflowError, ValueError) as exc:
        raise AuthFileError("auth file permissions are unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid not in {effective_uid, 0}
        or metadata.st_mode & 0o077
    ):
        raise AuthFileError("auth file must be owned by the current user or root and private")
    _require_macos_no_extended_acl(descriptor)


def _windows_descriptor_security(descriptor: int) -> tuple[bytes, bytes, bytes | None]:
    """Copy owner and DACL bytes from an already-open Windows file handle."""

    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        handle = msvcrt.get_osfhandle(descriptor)
        if handle == -1:
            raise OSError
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
            bootstrap_shim._SE_FILE_OBJECT,
            bootstrap_shim._OWNER_SECURITY_INFORMATION
            | bootstrap_shim._DACL_SECURITY_INFORMATION,
            ctypes.byref(owner_pointer),
            None,
            ctypes.byref(dacl_pointer),
            None,
            ctypes.byref(descriptor_pointer),
        )
        if result != 0 or not descriptor_pointer.value:
            raise OSError
        try:
            owner = bootstrap_shim._sid_from_pointer(owner_pointer)
            dacl = (
                None
                if not dacl_pointer.value
                else bootstrap_shim._acl_from_pointer(dacl_pointer)
            )
            return owner, bootstrap_shim._windows_current_sid(), dacl
        finally:
            local_free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
            local_free.argtypes = [ctypes.c_void_p]
            local_free.restype = ctypes.c_void_p
            local_free(descriptor_pointer)
    except (AttributeError, OSError, OverflowError, ValueError):
        raise AuthFileError("auth file permissions are unavailable") from None


def _require_windows_private_descriptor(descriptor: int) -> None:
    """Reject any owner or effective allow ACE outside the local trust set."""

    owner, current, dacl = _windows_descriptor_security(descriptor)
    trusted = {
        current,
        bootstrap_shim._SYSTEM_SID,
        bootstrap_shim._ADMINISTRATORS_SID,
    }
    if owner not in trusted or dacl is None:
        raise AuthFileError("auth file owner or ACL is not private")
    aces = bootstrap_shim._parse_acl(dacl)
    if aces is None:
        raise AuthFileError("auth file permissions are unavailable")
    for ace_type, flags, mask, sid in aces:
        if (
            ace_type == bootstrap_shim._ACCESS_ALLOWED_ACE_TYPE
            and not flags & bootstrap_shim._INHERIT_ONLY_ACE
            and sid not in trusted
            and mask
        ):
            raise AuthFileError("auth file ACL grants access outside the trusted principals")


def _require_private_descriptor(descriptor: int) -> None:
    if sys.platform == "win32":
        _require_windows_private_descriptor(descriptor)
        return
    if os.name == "posix":
        _require_posix_private_descriptor(descriptor)
        return
    raise AuthFileError("auth file permission checks are unavailable on this platform")


def _posix_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _require_posix_private_ancestor(descriptor: int) -> tuple[int, int]:
    """Verify an opened directory can anchor the next credential component."""

    try:
        metadata = os.fstat(descriptor)
        effective_uid = os.geteuid()
    except (AttributeError, OSError, OverflowError, ValueError) as exc:
        raise AuthFileError("auth file permissions are unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid not in {effective_uid, 0}
        or (
            metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            and not metadata.st_mode & stat.S_ISVTX
        )
    ):
        raise AuthFileError("auth file ancestor is not a private trusted directory")
    _require_macos_no_extended_acl(descriptor, ancestor_allow_deny_only=True)
    return metadata.st_dev, metadata.st_ino


def _validate_posix_auth_path(path: str) -> tuple[str, ...]:
    if type(path) is not str or not path.startswith("/") or path.startswith("//"):
        raise AuthFileError("auth file path must be an absolute local POSIX path")
    components = tuple(path[1:].split("/"))
    if not components or len(components) > 64 or any(
        not component or component in {".", ".."} or "\x00" in component
        for component in components
    ):
        raise AuthFileError("auth file path is unsafe")
    return components


@contextmanager
def _open_macos_private_auth_file(path: str, *, max_bytes: int) -> Iterator[int]:
    """Hold a no-follow macOS descriptor chain through one bounded read."""

    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
    if any(not hasattr(os, name) for name in required) or os.stat not in os.supports_dir_fd:
        raise AuthFileError("auth file permission checks are unavailable")
    components = _validate_posix_auth_path(path)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    descriptors: list[int] = []
    anchors: list[tuple[int, int, int, str | None]] = []
    try:
        root = os.open("/", directory_flags)
        descriptors.append(root)
        root_identity = _require_posix_private_ancestor(root)
        anchors.append((root, *root_identity, None))
        parent = root
        for component in components[:-1]:
            descriptor = os.open(component, directory_flags, dir_fd=parent)
            descriptors.append(descriptor)
            identity = _require_posix_private_ancestor(descriptor)
            anchors.append((descriptor, *identity, component))
            parent = descriptor
        descriptor = os.open(components[-1], file_flags, dir_fd=parent)
        descriptors.append(descriptor)
        metadata = os.fstat(descriptor)
        _require_posix_private_descriptor(descriptor)
        if metadata.st_size > max_bytes:
            raise AuthFileError("auth file exceeds its bounded size")

        def verify() -> int:
            for index, (anchor, device, inode, component) in enumerate(anchors):
                current = os.fstat(anchor)
                if (
                    (current.st_dev, current.st_ino) != (device, inode)
                    or _require_posix_private_ancestor(anchor) != (device, inode)
                ):
                    raise AuthFileError("auth file ancestor changed while reading")
                if index and component is not None:
                    named = os.stat(component, dir_fd=anchors[index - 1][0], follow_symlinks=False)
                    if (
                        stat.S_ISLNK(named.st_mode)
                        or (named.st_dev, named.st_ino) != (device, inode)
                    ):
                        raise AuthFileError("auth file ancestor changed while reading")
            current = os.fstat(descriptor)
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or _posix_identity(current) != _posix_identity(metadata)
            ):
                raise AuthFileError("auth file changed while reading")
            _require_posix_private_descriptor(descriptor)
            named = os.stat(components[-1], dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(named.st_mode) or (named.st_dev, named.st_ino) != (
                current.st_dev,
                current.st_ino,
            ):
                raise AuthFileError("auth file changed while reading")
            return current.st_size

        verify()
        yield descriptor
        verify()
    except AuthFileError:
        raise
    except (AttributeError, OSError, OverflowError, ValueError):
        raise AuthFileError("auth file must be a bounded private regular file") from None
    finally:
        while descriptors:
            try:
                os.close(descriptors.pop())
            except OSError:
                pass


def _read_macos_private_auth_file(path: str, *, max_bytes: int) -> bytes:
    """Read macOS credentials from the verified descriptor, never the pathname."""

    with _open_macos_private_auth_file(path, max_bytes=max_bytes) as descriptor:
        def read_bounded() -> bytes:
            chunks: list[bytes] = []
            total = 0
            while total <= max_bytes:
                chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > max_bytes:
                raise AuthFileError("auth file exceeds its bounded size")
            return b"".join(chunks)

        first = read_bounded()
        os.lseek(descriptor, 0, os.SEEK_SET)
        if read_bounded() != first:
            raise AuthFileError("auth file changed while reading")
        return first


def read_private_auth_file(path: str, *, max_bytes: int) -> bytes:
    """Read one absolute credential file after checking its held descriptor.

    The shared opened-file guard rejects path links, reparse points, nonregular
    and multi-link files, swaps, oversized data, and unsafe ancestor writes.
    This module adds the stricter credential confidentiality policy to that
    same descriptor before and after the bounded read.
    """

    try:
        candidate = Path(path)
        if not path or not candidate.is_absolute():
            raise AuthFileError("auth file path must be absolute")
        if sys.platform == "darwin":
            return _read_macos_private_auth_file(str(candidate), max_bytes=max_bytes)
        with bootstrap_shim.open_trusted_file(
            str(candidate), max_bytes=max_bytes, require_readonly=False
        ) as opened:
            return opened.read_verified(_require_private_descriptor)
    except AuthFileError:
        raise
    except Exception:
        raise AuthFileError("auth file must be a bounded private regular file") from None
