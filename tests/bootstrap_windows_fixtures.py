"""Native ACL fixtures restricted to newly created Windows test subtrees."""

from __future__ import annotations

import os
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from anvil_serving.control_plane import bootstrap_shim as shim


_DACL_SECURITY_INFORMATION = 0x00000004
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_OWNER_SECURITY_INFORMATION = 0x00000001
_SE_FILE_OBJECT = 1
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400


def _windows_error(result: int) -> OSError:
    return OSError(result, "Windows security API failed")


def _current_sid_sddl() -> str:
    """Return the current process SID only long enough to build test ACLs."""

    import ctypes

    convert = ctypes.WinDLL("advapi32", use_last_error=True).ConvertSidToStringSidW
    convert.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    convert.restype = ctypes.c_int
    local_free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    rendered = ctypes.c_wchar_p()
    sid = ctypes.create_string_buffer(shim._windows_current_sid())
    if not convert(ctypes.cast(sid, ctypes.c_void_p), ctypes.byref(rendered)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not rendered.value:
            raise ValueError("missing current process SID")
        return rendered.value
    finally:
        local_free(rendered)


def _assert_owned_target(root: Path, target: Path) -> Path:
    """Refuse links, reparse points, hard links, and paths outside ``root``."""

    root = root.resolve(strict=True)
    target = target.absolute()
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ValueError("fixture target escapes its disposable root") from exc
    if ".." in relative.parts:
        raise ValueError("fixture target escapes its disposable root")
    candidates = [root]
    for index in range(1, len(relative.parts) + 1):
        candidates.append(root.joinpath(*relative.parts[:index]))
    for candidate in candidates:
        metadata = os.lstat(candidate)
        if stat.S_ISLNK(metadata.st_mode) or (
            getattr(metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("fixture target contains a reparse point")
    target = target.resolve(strict=True)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("fixture target resolves outside its disposable root") from exc
    if not target.is_dir() and os.stat(target).st_nlink != 1:
        raise ValueError("fixture target has multiple hard links")
    return target


def _set_named_security(path: Path, sddl: str, *, owner: bool) -> None:
    """Apply an owner/DACL once or a replacement DACL to a checked fixture path."""

    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    convert.restype = wintypes.BOOL
    get_owner = advapi32.GetSecurityDescriptorOwner
    get_owner.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    get_owner.restype = wintypes.BOOL
    get_dacl = advapi32.GetSecurityDescriptorDacl
    get_dacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    get_dacl.restype = wintypes.BOOL
    set_named = advapi32.SetNamedSecurityInfoW
    set_named.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    set_named.restype = wintypes.DWORD
    local_free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    descriptor = ctypes.c_void_p()
    size = wintypes.DWORD()
    if not convert(sddl, 1, ctypes.byref(descriptor), ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        dacl_present = wintypes.BOOL()
        dacl = ctypes.c_void_p()
        dacl_defaulted = wintypes.BOOL()
        if not get_dacl(
            descriptor, ctypes.byref(dacl_present), ctypes.byref(dacl), ctypes.byref(dacl_defaulted)
        ) or not dacl_present.value or not dacl.value:
            raise OSError("fixture SDDL did not create a DACL")
        security_information = (
            _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION
        )
        owner_sid = ctypes.c_void_p()
        if owner:
            owner_defaulted = wintypes.BOOL()
            if not get_owner(descriptor, ctypes.byref(owner_sid), ctypes.byref(owner_defaulted)):
                raise ctypes.WinError(ctypes.get_last_error())
            security_information |= _OWNER_SECURITY_INFORMATION
        result = set_named(
            str(path), _SE_FILE_OBJECT, security_information, owner_sid, None, dacl, None
        )
        if result:
            raise _windows_error(result)
    finally:
        local_free(descriptor)


class WindowsFixtureTree:
    """A disposable owned tree whose ACL changes cannot escape its root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        self._sid = _current_sid_sddl()
        self._full_control = f"D:P(A;OICI;FA;;;{self._sid})"
        self.establish_full_control(self.root)

    def file(self, name: str) -> Path:
        if type(name) is not str:
            raise ValueError("fixture file name must be a string")
        path = self.root / name
        if path.parent != self.root or not name or Path(name).name != name:
            raise ValueError("fixture file must be a direct child of its root")
        with path.open("xb"):
            pass
        self.establish_full_control(path)
        return path

    def establish_full_control(self, path: Path) -> None:
        """Set owner once while this new object has inherited full control."""

        target = _assert_owned_target(self.root, path)
        _set_named_security(target, f"O:{self._sid}{self._full_control}", owner=True)

    def replace_dacl(self, path: Path, *aces: str) -> None:
        target = _assert_owned_target(self.root, path)
        _set_named_security(target, "D:P" + "".join(aces), owner=False)

    def restore_full_control(self, path: Path) -> None:
        self.replace_dacl(path, f"(A;OICI;FA;;;{self._sid})")

    def owner_readonly(self, path: Path) -> None:
        self.replace_dacl(path, f"(A;OICI;GR;;;{self._sid})")

    def owner_writable(self, path: Path) -> None:
        self.replace_dacl(path, f"(A;OICI;GW;;;{self._sid})")

    def everyone_writable(self, path: Path) -> None:
        self.replace_dacl(path, "(A;OICI;GW;;;WD)")

    def owner_readonly_with_everyone_write(self, path: Path) -> None:
        self.replace_dacl(path, f"(A;OICI;GR;;;{self._sid})", "(A;OICI;GW;;;WD)")


@contextmanager
def windows_fixture_tree() -> Iterator[WindowsFixtureTree]:
    """Create a uniquely named, test-only root below the current user profile."""

    if sys.platform != "win32":
        raise RuntimeError("Windows fixture requested on a non-Windows platform")
    with TemporaryDirectory(prefix="anvil-bootstrap-", dir=Path.home()) as directory:
        tree = WindowsFixtureTree(Path(directory))
        try:
            yield tree
        finally:
            tree.restore_full_control(tree.root)
