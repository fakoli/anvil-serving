"""Regression coverage for local MCP credential-file confidentiality."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from anvil_serving.control_plane import bootstrap_shim
from anvil_serving.control_plane.mcp import auth_file


def _acl(*entries: tuple[int, int, int, bytes]) -> bytes:
    body = b"".join(
        struct.pack("<BBHI", kind, flags, 8 + len(sid), mask) + sid
        for kind, flags, mask, sid in entries
    )
    return struct.pack("<BBHHH", 2, 0, 8 + len(body), len(entries), 0) + body


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor permissions")
def test_posix_private_descriptor_accepts_owner_only_file(tmp_path):
    candidate = tmp_path / "controller.token"
    candidate.write_text("credential\n", encoding="utf-8")
    candidate.chmod(0o600)
    descriptor = os.open(candidate, os.O_RDONLY)
    try:
        auth_file._require_posix_private_descriptor(descriptor)
    finally:
        os.close(descriptor)


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor permissions")
@pytest.mark.parametrize("mode", (0o640, 0o604, 0o701, 0o707))
def test_posix_private_descriptor_rejects_every_group_or_other_permission(tmp_path, mode):
    candidate = tmp_path / "controller.token"
    candidate.write_text("credential\n", encoding="utf-8")
    candidate.chmod(mode)
    descriptor = os.open(candidate, os.O_RDONLY)
    try:
        with pytest.raises(auth_file.AuthFileError):
            auth_file._require_posix_private_descriptor(descriptor)
    finally:
        os.close(descriptor)


def test_posix_private_descriptor_rejects_foreign_owner(monkeypatch):
    metadata = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_nlink=1,
        st_uid=2002,
    )
    monkeypatch.setattr(auth_file.os, "fstat", lambda _descriptor: metadata)
    monkeypatch.setattr(auth_file.os, "geteuid", lambda: 1001, raising=False)

    with pytest.raises(auth_file.AuthFileError):
        auth_file._require_posix_private_descriptor(9)


def test_macos_dispatch_uses_the_held_descriptor_reader(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(auth_file.sys, "platform", "darwin")
    monkeypatch.setattr(
        auth_file,
        "_read_macos_private_auth_file",
        lambda path, *, max_bytes: seen.update(path=path, max_bytes=max_bytes) or b"ok",
    )

    candidate = tmp_path / "controller.token"
    assert auth_file.read_private_auth_file(str(candidate), max_bytes=64) == b"ok"
    assert seen == {"path": str(candidate), "max_bytes": 64}


def test_macos_acl_policy_rejects_leaf_extended_entries(monkeypatch):
    mode = stat.S_IFREG | 0o600
    metadata = SimpleNamespace(st_mode=mode, st_nlink=1, st_uid=1001)
    monkeypatch.setattr(auth_file.sys, "platform", "darwin")
    monkeypatch.setattr(auth_file.os, "fstat", lambda _descriptor: metadata)
    monkeypatch.setattr(auth_file.os, "geteuid", lambda: 1001, raising=False)
    monkeypatch.setattr(auth_file, "_macos_extended_acl_entries", lambda _descriptor, **_kwargs: 1)

    with pytest.raises(auth_file.AuthFileError, match="extended ACL"):
        auth_file._require_posix_private_descriptor(9)


def test_macos_acl_policy_fails_closed_when_inspection_is_unavailable(monkeypatch):
    metadata = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_nlink=1, st_uid=1001)
    monkeypatch.setattr(auth_file.sys, "platform", "darwin")
    monkeypatch.setattr(auth_file.os, "fstat", lambda _descriptor: metadata)
    monkeypatch.setattr(auth_file.os, "geteuid", lambda: 1001, raising=False)
    monkeypatch.setattr(
        auth_file,
        "_macos_extended_acl_entries",
        lambda _descriptor, **_kwargs: (
            _ for _ in ()
        ).throw(auth_file.AuthFileError("unavailable")),
    )

    with pytest.raises(auth_file.AuthFileError, match="unavailable"):
        auth_file._require_posix_private_descriptor(9)


def test_macos_acl_policy_rejects_uninspectable_ancestor(monkeypatch):
    metadata = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=1001)
    monkeypatch.setattr(auth_file.sys, "platform", "darwin")
    monkeypatch.setattr(auth_file.os, "fstat", lambda _descriptor: metadata)
    monkeypatch.setattr(auth_file.os, "geteuid", lambda: 1001, raising=False)
    monkeypatch.setattr(
        auth_file,
        "_macos_extended_acl_entries",
        lambda _descriptor, **_kwargs: (
            _ for _ in ()
        ).throw(auth_file.AuthFileError("unavailable")),
    )

    with pytest.raises(auth_file.AuthFileError, match="unavailable"):
        auth_file._require_posix_private_ancestor(9)


class _NativeCall:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class _MacAclLibrary:
    def __init__(
        self, *, pointer=1, tags=(), get_errno=None, valid_result=0,
        entry_result=None, entry_errno=None, tag_result=0, free_result=0,
    ):
        self.tags = tuple(tags)
        self.entry_result = entry_result
        self.entry_errno = entry_errno
        self.tag_result = tag_result
        self._index = 0
        def get_acl(_descriptor, _kind):
            if get_errno is not None:
                ctypes.set_errno(get_errno)
            return pointer

        def get_entry(_acl, entry_id, entry):
            if entry_result is not None:
                if entry_errno is not None:
                    ctypes.set_errno(entry_errno)
                return entry_result
            if entry_id == auth_file._MACOS_ACL_FIRST_ENTRY:
                self._index = 0
            elif entry_id == auth_file._MACOS_ACL_NEXT_ENTRY:
                self._index += 1
            else:
                ctypes.set_errno(errno.EINVAL)
                return -1
            if self._index >= len(self.tags):
                ctypes.set_errno(errno.EINVAL)
                return -1
            ctypes.cast(entry, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(
                self._index + 1
            )
            return 0

        def get_tag(entry, pointer):
            if self.tag_result:
                return self.tag_result
            index = entry.value - 1
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))[0] = self.tags[index]
            return 0

        self.acl_get_fd_np = _NativeCall(get_acl)
        self.acl_valid = _NativeCall(lambda _acl: valid_result)
        self.acl_get_entry = _NativeCall(get_entry)
        self.acl_get_tag_type = _NativeCall(get_tag)
        self.acl_free = _NativeCall(lambda _acl: free_result)


@pytest.mark.parametrize(
    "tags",
    ((), (auth_file._MACOS_ACL_EXTENDED_DENY,), (auth_file._MACOS_ACL_EXTENDED_DENY,) * 3),
)
def test_macos_acl_ctypes_boundary_counts_extended_entries(monkeypatch, tags):
    library = _MacAclLibrary(tags=tags)
    monkeypatch.setattr(
        auth_file,
        "_macos_acl_library",
        lambda: (ctypes, library),
    )
    assert auth_file._macos_extended_acl_entries(7) == len(tags)
    assert library.acl_get_fd_np.argtypes
    assert library.acl_valid.restype is ctypes.c_int
    assert library.acl_get_entry.restype is ctypes.c_int
    assert library.acl_get_tag_type.argtypes
    assert library.acl_free.argtypes


def test_macos_acl_ctypes_boundary_accepts_deny_only_ancestor(monkeypatch):
    library = _MacAclLibrary(tags=(auth_file._MACOS_ACL_EXTENDED_DENY,))
    monkeypatch.setattr(auth_file, "_macos_acl_library", lambda: (ctypes, library))

    assert auth_file._macos_extended_acl_entries(7, ancestor_allow_deny_only=True) == 1


@pytest.mark.parametrize(
    "tags",
    (
        (auth_file._MACOS_ACL_EXTENDED_ALLOW,),
        (auth_file._MACOS_ACL_EXTENDED_DENY, auth_file._MACOS_ACL_EXTENDED_ALLOW),
    ),
)
def test_macos_acl_ctypes_boundary_rejects_allow_or_mixed_ancestor(monkeypatch, tags):
    library = _MacAclLibrary(tags=tags)
    monkeypatch.setattr(auth_file, "_macos_acl_library", lambda: (ctypes, library))

    with pytest.raises(auth_file.AuthFileError, match="access-expanding"):
        auth_file._macos_extended_acl_entries(7, ancestor_allow_deny_only=True)


@pytest.mark.parametrize(
    "library, errno_value",
    (
        (_MacAclLibrary(pointer=0, get_errno=errno.EPERM), 0),
        (_MacAclLibrary(valid_result=-1), 0),
        (_MacAclLibrary(entry_result=-1, entry_errno=errno.EPERM), 0),
        (_MacAclLibrary(free_result=-1), 0),
    ),
)
def test_macos_acl_ctypes_boundary_fails_closed(monkeypatch, library, errno_value):
    monkeypatch.setattr(
        auth_file,
        "_macos_acl_library",
        lambda: (ctypes, library),
    )
    ctypes.set_errno(errno_value)

    with pytest.raises(auth_file.AuthFileError, match="inspection is unavailable"):
        auth_file._macos_extended_acl_entries(7)


def test_macos_acl_ctypes_boundary_fails_closed_when_ancestor_tag_is_unavailable(monkeypatch):
    library = _MacAclLibrary(tags=(auth_file._MACOS_ACL_EXTENDED_DENY,), tag_result=-1)
    monkeypatch.setattr(auth_file, "_macos_acl_library", lambda: (ctypes, library))

    with pytest.raises(auth_file.AuthFileError, match="inspection is unavailable"):
        auth_file._macos_extended_acl_entries(7, ancestor_allow_deny_only=True)


@pytest.mark.parametrize("pointer, entry_errno", ((0, None), (1, None)))
def test_macos_acl_ctypes_boundary_clears_stale_errno(monkeypatch, pointer, entry_errno):
    library = _MacAclLibrary(pointer=pointer, entry_result=-1, entry_errno=entry_errno)
    monkeypatch.setattr(auth_file, "_macos_acl_library", lambda: (ctypes, library))
    ctypes.set_errno(errno.ENOENT if not pointer else errno.EINVAL)

    with pytest.raises(auth_file.AuthFileError, match="inspection is unavailable"):
        auth_file._macos_extended_acl_entries(7)


@pytest.mark.skipif(sys.platform != "darwin", reason="native Darwin extended ACL validation")
def test_macos_native_private_file_accepts_no_acl_and_rejects_extended_acl(tmp_path):
    candidate = tmp_path / "controller.token"
    candidate.write_bytes(b"credential")
    candidate.chmod(0o600)
    descriptor = os.open(candidate, os.O_RDONLY)
    try:
        auth_file._require_posix_private_descriptor(descriptor)
        subprocess.run(["chmod", "+a", "everyone allow read", str(candidate)], check=True)
        with pytest.raises(auth_file.AuthFileError, match="extended ACL"):
            auth_file._require_posix_private_descriptor(descriptor)
    finally:
        os.close(descriptor)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux executes the macOS-style held-fd walker")
def test_macos_held_fd_walker_rejects_leaf_swaps_and_reads_only_held_file(tmp_path, monkeypatch):
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    candidate = parent / "controller.token"
    candidate.write_bytes(b"first")
    candidate.chmod(0o600)
    monkeypatch.setattr(auth_file.sys, "platform", "darwin")
    monkeypatch.setattr(auth_file, "_macos_extended_acl_entries", lambda _descriptor, **_kwargs: 0)
    original_read = auth_file.os.read
    swapped = False

    def swapping_read(descriptor, count):
        nonlocal swapped
        value = original_read(descriptor, count)
        if value and not swapped:
            swapped = True
            candidate.write_bytes(b"other")
        return value

    monkeypatch.setattr(auth_file.os, "read", swapping_read)

    with pytest.raises(auth_file.AuthFileError, match="changed while reading"):
        auth_file._read_macos_private_auth_file(str(candidate), max_bytes=64)
    assert swapped


@pytest.mark.skipif(sys.platform != "linux", reason="Linux executes the macOS-style held-fd walker")
def test_macos_full_reader_accepts_deny_only_home_ancestor(tmp_path, monkeypatch):
    home = tmp_path / "Users" / "operator"
    secrets = home / ".local" / "share" / "anvil" / "secrets"
    secrets.mkdir(parents=True, mode=0o700)
    for parent in (
        tmp_path / "Users", home, home / ".local", home / ".local" / "share",
        home / ".local" / "share" / "anvil", secrets,
    ):
        parent.chmod(0o700)
    candidate = secrets / "controller.token"
    candidate.write_bytes(b"credential\n")
    candidate.chmod(0o600)
    calls = []

    def acl_entries(_descriptor, *, ancestor_allow_deny_only=False):
        calls.append(ancestor_allow_deny_only)
        return 1 if ancestor_allow_deny_only else 0

    monkeypatch.setattr(auth_file.sys, "platform", "darwin")
    monkeypatch.setattr(auth_file, "_macos_extended_acl_entries", acl_entries)

    assert auth_file.read_private_auth_file(str(candidate), max_bytes=64) == b"credential\n"
    assert calls.count(True) >= 1 and calls.count(False) >= 1


@pytest.mark.skipif(sys.platform != "linux", reason="Linux executes the macOS-style held-fd walker")
def test_macos_held_fd_walker_refuses_symlink_leaf(tmp_path, monkeypatch):
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    target = parent / "target.token"
    target.write_bytes(b"credential")
    target.chmod(0o600)
    candidate = parent / "controller.token"
    candidate.symlink_to(target)
    monkeypatch.setattr(auth_file.sys, "platform", "darwin")
    monkeypatch.setattr(auth_file, "_macos_extended_acl_entries", lambda _descriptor, **_kwargs: 0)

    with pytest.raises(auth_file.AuthFileError):
        auth_file._read_macos_private_auth_file(str(candidate), max_bytes=64)


def test_windows_acl_policy_accepts_only_current_system_and_administrators(monkeypatch):
    current = bootstrap_shim._sid(5, 21, 1, 2, 3, 4)
    dacl = _acl(
        (
            bootstrap_shim._ACCESS_ALLOWED_ACE_TYPE,
            0,
            0x80000000,
            current,
        ),
        (
            bootstrap_shim._ACCESS_ALLOWED_ACE_TYPE,
            0,
            0x80000000,
            bootstrap_shim._SYSTEM_SID,
        ),
    )
    monkeypatch.setattr(
        auth_file,
        "_windows_descriptor_security",
        lambda _descriptor: (current, current, dacl),
    )

    auth_file._require_windows_private_descriptor(7)


@pytest.mark.parametrize(
    "owner, dacl",
    (
        (
            bootstrap_shim._sid(5, 21, 9, 8, 7, 6),
            _acl(),
        ),
        (
            bootstrap_shim._SYSTEM_SID,
            _acl(
                (
                    bootstrap_shim._ACCESS_ALLOWED_ACE_TYPE,
                    0,
                    0x80000000,
                    bootstrap_shim._sid(1, 0),
                ),
            ),
        ),
        (bootstrap_shim._SYSTEM_SID, None),
    ),
)
def test_windows_acl_policy_rejects_foreign_owner_or_allow_ace(monkeypatch, owner, dacl):
    current = bootstrap_shim._sid(5, 21, 1, 2, 3, 4)
    monkeypatch.setattr(
        auth_file,
        "_windows_descriptor_security",
        lambda _descriptor: (owner, current, dacl),
    )

    with pytest.raises(auth_file.AuthFileError):
        auth_file._require_windows_private_descriptor(7)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows ACL check")
def test_live_windows_protected_file_is_read_without_exposing_contents():
    from tests.bootstrap_windows_fixtures import windows_fixture_tree

    with windows_fixture_tree() as tree:
        candidate = tree.file("controller.token")
        candidate.write_text("credential\n", encoding="utf-8")

        assert auth_file.read_private_auth_file(
            str(candidate).replace("\\", "/"), max_bytes=64
        ) == b"credential\r\n"
