"""Hermetic opened-descriptor permission tests for the bootstrap receiver."""

from __future__ import annotations

import os
import stat
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from anvil_serving.control_plane import bootstrap_shim as shim
from anvil_serving.fleet_bootstrap import BootstrapPermissionVerdict as Verdict


def _sid(authority: int, *subauthorities: int) -> bytes:
    return bytes((1, len(subauthorities))) + authority.to_bytes(6, "big") + b"".join(
        value.to_bytes(4, "little") for value in subauthorities
    )


CURRENT = _sid(5, 21, 1)
OTHER = _sid(5, 21, 2)


def _ace(
    ace_type: int, sid: bytes, mask: int, *, flags: int = 0
) -> bytes:
    size = 8 + len(sid)
    return struct.pack("<BBHI", ace_type, flags, size, mask) + sid


def _acl(*aces: bytes) -> bytes:
    payload = b"".join(aces)
    return struct.pack("<BBHHH", 2, 0, 8 + len(payload), len(aces), 0) + payload


@pytest.mark.parametrize(
    ("mode", "owner", "uid", "ancestor", "expected"),
    (
        (stat.S_IFREG | 0o400, 1000, 1000, False, Verdict.OWNER_READONLY),
        (stat.S_IFREG | 0o600, 1000, 1000, False, Verdict.OWNER_WRITABLE),
        (stat.S_IFREG | 0o600, 0, 1000, False, Verdict.OWNER_WRITABLE),
        (stat.S_IFREG | 0o600, 2000, 1000, False, Verdict.UNTRUSTED_WRITABLE),
        (stat.S_IFREG | 0o620, 1000, 1000, False, Verdict.UNTRUSTED_WRITABLE),
        (stat.S_IFREG | 0o602, 1000, 1000, False, Verdict.UNTRUSTED_WRITABLE),
        (
            stat.S_IFDIR | stat.S_ISVTX | stat.S_IWGRP | 0o500,
            1000,
            1000,
            True,
            Verdict.OWNER_READONLY,
        ),
        (stat.S_IFDIR | stat.S_IWGRP | 0o500, 1000, 1000, True, Verdict.UNTRUSTED_WRITABLE),
        (stat.S_IFREG | 0o400, 1000, 1000, True, Verdict.INDETERMINATE),
        (stat.S_IFIFO | 0o600, 1000, 1000, False, Verdict.INDETERMINATE),
    ),
)
def test_linux_classifier_uses_only_fstat_mode_and_effective_uid(
    mode: int, owner: int, uid: int, ancestor: bool, expected: Verdict
) -> None:
    assert shim._classify_linux_mode(mode, owner, uid, ancestor=ancestor) is expected


@pytest.mark.parametrize("bad", (None, True, -1, 1.5, "1"))
def test_linux_classifier_rejects_malformed_metadata(bad: object) -> None:
    assert (
        shim._classify_linux_mode(bad, 1000, 1000, ancestor=False)
        is Verdict.INDETERMINATE
    )


@pytest.mark.parametrize(
    "mask",
    (
        shim._FILE_WRITE_DATA,
        shim._FILE_APPEND_DATA,
        shim._FILE_WRITE_EA,
        shim._FILE_WRITE_ATTRIBUTES,
        shim._FILE_DELETE_CHILD,
        shim._DELETE,
        shim._WRITE_DAC,
        shim._WRITE_OWNER,
        shim._GENERIC_WRITE,
        shim._GENERIC_ALL,
    ),
)
def test_windows_classifier_rejects_every_untrusted_mutation_grant(mask: int) -> None:
    dacl = _acl(_ace(shim._ACCESS_ALLOWED_ACE_TYPE, OTHER, mask))
    assert (
        shim._classify_windows_acl(CURRENT, CURRENT, dacl, ancestor=False)
        is Verdict.UNTRUSTED_WRITABLE
    )


def test_windows_classifier_handles_owner_trust_and_ancestor_creation_exception() -> None:
    readonly = _acl()
    assert (
        shim._classify_windows_acl(CURRENT, CURRENT, readonly, ancestor=False)
        is Verdict.OWNER_READONLY
    )
    assert (
        shim._classify_windows_acl(OTHER, CURRENT, readonly, ancestor=False)
        is Verdict.UNTRUSTED_WRITABLE
    )
    only_child_creation = _acl(
        _ace(
            shim._ACCESS_ALLOWED_ACE_TYPE,
            CURRENT,
            shim._FILE_WRITE_DATA | shim._FILE_APPEND_DATA,
        )
    )
    assert (
        shim._classify_windows_acl(CURRENT, CURRENT, only_child_creation, ancestor=True)
        is Verdict.OWNER_WRITABLE
    )
    assert (
        shim._classify_windows_acl(CURRENT, CURRENT, only_child_creation, ancestor=False)
        is Verdict.OWNER_WRITABLE
    )


def test_windows_owner_and_untrusted_masks_have_distinct_ancestor_semantics() -> None:
    owner_delete = _acl(
        _ace(shim._ACCESS_ALLOWED_ACE_TYPE, CURRENT, shim._DELETE)
    )
    assert (
        shim._classify_windows_acl(CURRENT, CURRENT, owner_delete, ancestor=False)
        is Verdict.OWNER_READONLY
    )
    untrusted_child_creation = _acl(
        _ace(
            shim._ACCESS_ALLOWED_ACE_TYPE,
            OTHER,
            shim._FILE_WRITE_DATA | shim._FILE_APPEND_DATA,
        )
    )
    assert (
        shim._classify_windows_acl(
            CURRENT, CURRENT, untrusted_child_creation, ancestor=True
        )
        is Verdict.OWNER_READONLY
    )
    assert (
        shim._classify_windows_acl(
            CURRENT, CURRENT, untrusted_child_creation, ancestor=False
        )
        is Verdict.UNTRUSTED_WRITABLE
    )


def test_windows_classifier_keeps_deny_conservative_and_validates_inherit_only() -> None:
    dacl = _acl(
        _ace(shim._ACCESS_DENIED_ACE_TYPE, OTHER, shim._FILE_WRITE_DATA),
        _ace(shim._ACCESS_ALLOWED_ACE_TYPE, OTHER, shim._FILE_WRITE_DATA),
    )
    assert (
        shim._classify_windows_acl(CURRENT, CURRENT, dacl, ancestor=False)
        is Verdict.UNTRUSTED_WRITABLE
    )
    inherited = _acl(
        _ace(
            shim._ACCESS_ALLOWED_ACE_TYPE,
            OTHER,
            shim._FILE_WRITE_DATA,
            flags=shim._INHERIT_ONLY_ACE,
        )
    )
    assert (
        shim._classify_windows_acl(CURRENT, CURRENT, inherited, ancestor=False)
        is Verdict.OWNER_READONLY
    )


@pytest.mark.parametrize("ancestor", (False, True))
def test_windows_os_service_owner_is_trusted_only_for_ancestors(ancestor: bool) -> None:
    service = _sid(5, 80, 956008885, 3418522649, 1831038044, 1853292631, 2271478464)
    expected = Verdict.OWNER_READONLY if ancestor else Verdict.UNTRUSTED_WRITABLE
    assert shim._classify_windows_acl(service, CURRENT, _acl(), ancestor=ancestor) is expected
    grant = _acl(_ace(0, service, 0x2))
    expected = Verdict.OWNER_WRITABLE if ancestor else Verdict.UNTRUSTED_WRITABLE
    assert shim._classify_windows_acl(service, CURRENT, grant, ancestor=ancestor) is expected
    # A trusted grant does not imply that the current user can write the object.
    grant = _acl(_ace(0, service, 0x40000))
    expected = Verdict.OWNER_READONLY if ancestor else Verdict.UNTRUSTED_WRITABLE
    assert shim._classify_windows_acl(CURRENT, CURRENT, grant, ancestor=ancestor) is expected


@pytest.mark.parametrize("component", range(6))
def test_windows_ancestor_exception_does_not_trust_similar_service_sids(component: int) -> None:
    components = [80, 956008885, 3418522649, 1831038044, 1853292631, 2271478464]
    components[component] += 1
    unknown = _sid(5, *components)
    assert (
        shim._classify_windows_acl(unknown, CURRENT, _acl(), ancestor=True)
        is Verdict.UNTRUSTED_WRITABLE
    )
    grant = _acl(_ace(0, unknown, 0x40000))
    assert (
        shim._classify_windows_acl(CURRENT, CURRENT, grant, ancestor=True)
        is Verdict.UNTRUSTED_WRITABLE
    )


def test_windows_os_owned_ancestor_still_refuses_untrusted_mutation_or_unknown_acl() -> None:
    service = _sid(5, 80, 956008885, 3418522649, 1831038044, 1853292631, 2271478464)
    for mask in (0x10, 0x100, 0x40, 0x10000, 0x40000, 0x80000, 0x40000000, 0x10000000):
        dacl = _acl(_ace(0, service, 0x10000000), _ace(0, OTHER, mask))
        assert (
            shim._classify_windows_acl(service, CURRENT, dacl, ancestor=True)
            is Verdict.UNTRUSTED_WRITABLE
        )
    assert (
        shim._classify_windows_acl(service, CURRENT, _acl(_ace(2, service, 0)), ancestor=True)
        is Verdict.INDETERMINATE
    )
    assert (
        shim._classify_windows_acl(service, CURRENT, None, ancestor=True)
        is Verdict.UNTRUSTED_WRITABLE
    )


@pytest.mark.parametrize(
    "dacl",
    (
        None,
        b"",
        b"\x02\x00\x08\x00\x01\x00\x00\x00",
        _acl(_ace(shim._ACCESS_ALLOWED_ACE_TYPE, CURRENT, 0x200)),
        _acl(_ace(2, CURRENT, shim._FILE_WRITE_DATA)),
        _acl(*(_ace(shim._ACCESS_ALLOWED_ACE_TYPE, CURRENT, 0) for _ in range(257))),
    ),
)
def test_windows_classifier_refuses_missing_or_malformed_dacl(dacl: bytes | None) -> None:
    expected = Verdict.UNTRUSTED_WRITABLE if dacl is None else Verdict.INDETERMINATE
    assert shim._classify_windows_acl(CURRENT, CURRENT, dacl, ancestor=False) is expected


def test_windows_classifier_copies_only_sid_bytes_and_never_exposes_acl_values() -> None:
    seeded = b"private-acl-path-token"
    dacl = _acl(_ace(shim._ACCESS_ALLOWED_ACE_TYPE, OTHER, shim._FILE_WRITE_DATA))
    assert seeded not in dacl
    verdict = shim._classify_windows_acl(CURRENT, CURRENT, dacl, ancestor=False)
    assert verdict.value == "untrusted-writable"
    assert seeded.decode("ascii") not in verdict.value


@pytest.mark.skipif(sys.platform != "linux", reason="Linux descriptor semantics")
def test_linux_native_descriptor_stays_open_and_at_its_original_offset(tmp_path: Path) -> None:
    path = tmp_path / "receiver.pyz"
    path.write_bytes(b"abcdef")
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.lseek(descriptor, 2, os.SEEK_SET)
        os.fchmod(descriptor, 0o600)
        assert shim.inspect_opened_permissions(descriptor) is Verdict.OWNER_WRITABLE
        assert os.lseek(descriptor, 0, os.SEEK_CUR) == 2
        assert os.read(descriptor, 1) == b"c"
        os.lseek(descriptor, 2, os.SEEK_SET)
        os.fchmod(descriptor, 0o400)
        assert shim.inspect_opened_permissions(descriptor) is Verdict.OWNER_READONLY
        assert os.lseek(descriptor, 0, os.SEEK_CUR) == 2
    finally:
        os.close(descriptor)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux descriptor semantics")
def test_linux_native_pipe_and_closed_descriptor_are_indeterminate() -> None:
    read_end, write_end = os.pipe()
    try:
        assert shim.inspect_opened_permissions(read_end) is Verdict.INDETERMINATE
    finally:
        os.close(read_end)
        os.close(write_end)
    assert shim.inspect_opened_permissions(read_end) is Verdict.INDETERMINATE


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle ACL semantics")
def test_windows_native_temp_acl_matrix_uses_the_borrowed_descriptor(tmp_path: Path) -> None:
    """Exercise read/write/Everyone DACLs only on a disposable temporary file."""

    import ctypes

    path = tmp_path / "receiver.pyz"
    path.write_bytes(b"abcdef")
    current_sid = shim._windows_current_sid()
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    convert = advapi32.ConvertSidToStringSidW
    convert.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    convert.restype = ctypes.c_int
    rendered = ctypes.c_wchar_p()
    sid_buffer = ctypes.create_string_buffer(current_sid)
    assert convert(ctypes.cast(sid_buffer, ctypes.c_void_p), ctypes.byref(rendered))
    try:
        current = "*" + rendered.value
    finally:
        ctypes.WinDLL("kernel32", use_last_error=True).LocalFree(rendered)

    def set_acl(*entries: str) -> None:
        completed = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", *entries],
            check=False,
            capture_output=True,
            text=False,
        )
        assert completed.returncode == 0

    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        os.lseek(descriptor, 2, os.SEEK_SET)
        set_acl(f"{current}:R")
        assert shim.inspect_opened_permissions(descriptor) is Verdict.OWNER_READONLY
        set_acl(f"{current}:W")
        assert shim.inspect_opened_permissions(descriptor) is Verdict.OWNER_WRITABLE
        set_acl(f"{current}:R", "*S-1-1-0:W")
        assert shim.inspect_opened_permissions(descriptor) is Verdict.UNTRUSTED_WRITABLE
        assert os.lseek(descriptor, 0, os.SEEK_CUR) == 2
    finally:
        os.close(descriptor)
        subprocess.run(
            ["icacls", str(path), "/reset"],
            check=False,
            capture_output=True,
            text=False,
        )


def test_public_dispatch_validates_exact_types_before_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shim.sys, "platform", "darwin")
    assert shim.inspect_opened_permissions(0) is Verdict.UNSUPPORTED
    for descriptor, ancestor in ((True, False), (0, 1), (-1, False), ("0", False)):
        assert shim.inspect_opened_permissions(descriptor, ancestor=ancestor) is Verdict.INDETERMINATE  # type: ignore[arg-type]


def test_giant_borrowed_descriptor_is_indeterminate_without_closing() -> None:
    assert shim.inspect_opened_permissions(1 << 200) is Verdict.INDETERMINATE
