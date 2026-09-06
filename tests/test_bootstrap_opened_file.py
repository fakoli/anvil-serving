"""Native, held-descriptor tests for the bootstrap file-read boundary."""

from __future__ import annotations

import os
import stat
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from tempfile import mkstemp

import pytest

from anvil_serving.control_plane import bootstrap_shim as shim
from anvil_serving.fleet_bootstrap import BootstrapContractError, BootstrapErrorCode


@contextmanager
def _trusted(
    path: Path,
    *,
    max_bytes: int = 64,
    require_readonly: bool = False,
):
    with shim.open_trusted_file(
        str(path), max_bytes=max_bytes, require_readonly=require_readonly
    ) as opened:
        yield opened


@pytest.fixture
def trusted_tmp() -> Path:
    """An owned disposable file under the worktree's already-trusted ancestry."""
    descriptor, name = mkstemp(prefix="anvil-opened-file-", suffix=".pyz", dir=Path.cwd())
    os.close(descriptor)
    path = Path(name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def _refusal(code: BootstrapErrorCode, call: object) -> None:
    with pytest.raises(BootstrapContractError) as raised:
        call()  # type: ignore[operator]
    assert raised.value.code == code.value


def test_held_file_repeats_bounded_reads_and_becomes_closed(
    trusted_tmp: Path,
) -> None:
    path = trusted_tmp
    path.write_bytes(b"fixed-bytes")
    with _trusted(path, max_bytes=len(b"fixed-bytes")) as opened:
        assert repr(opened) == "OpenedTrustedFile(open)"
        assert opened.read_verified() == b"fixed-bytes"
        assert opened.read_verified() == b"fixed-bytes"
    assert repr(opened) == "OpenedTrustedFile(closed)"
    _refusal(BootstrapErrorCode.PRECONDITION_FAILED, opened.read_verified)
    assert str(path) not in repr(opened)


def test_empty_and_cap_overflow_are_distinguished(
    trusted_tmp: Path,
) -> None:
    empty = trusted_tmp
    empty.write_bytes(b"")
    with _trusted(empty) as opened:
        assert opened.read_verified() == b""

    oversized = trusted_tmp
    oversized.write_bytes(b"abcd")
    _refusal(
        BootstrapErrorCode.PRECONDITION_FAILED,
        lambda: _enter_and_read(oversized, max_bytes=3),
    )


def _enter_and_read(path: Path, *, max_bytes: int, require_readonly: bool = False) -> bytes:
    with _trusted(path, max_bytes=max_bytes, require_readonly=require_readonly) as opened:
        return opened.read_verified()


@pytest.mark.parametrize(
    "path,max_bytes,readonly,expected",
    (
        (None, 1, False, BootstrapErrorCode.INVALID_CONTRACT),
        (True, 1, False, BootstrapErrorCode.INVALID_CONTRACT),
        ("relative.pyz", 1, False, BootstrapErrorCode.UNSAFE_PATH),
        ("C:\\bad/receiver.pyz", 1, False, BootstrapErrorCode.UNSAFE_PATH),
        ("/tmp/receiver.pyz", 0, False, BootstrapErrorCode.INVALID_CONTRACT),
        ("/tmp/receiver.pyz", True, False, BootstrapErrorCode.INVALID_CONTRACT),
        ("/tmp/receiver.pyz", 1, 0, BootstrapErrorCode.INVALID_CONTRACT),
    ),
)
def test_reader_rejects_exact_primitive_and_lexical_inputs(
    path: object, max_bytes: object, readonly: object, expected: BootstrapErrorCode
) -> None:
    _refusal(
        expected,
        lambda: shim.open_trusted_file(  # type: ignore[arg-type]
            path, max_bytes=max_bytes, require_readonly=readonly
        ).__enter__(),
    )


def test_reader_rejects_directory_and_hardlinked_leaf(
    trusted_tmp: Path,
) -> None:
    _refusal(
        BootstrapErrorCode.UNSAFE_PATH,
        lambda: _enter_and_read(Path.cwd(), max_bytes=64),
    )
    first = trusted_tmp
    second = trusted_tmp.with_name(trusted_tmp.stem + "-linked.pyz")
    first.write_bytes(b"one")
    try:
        os.link(first, second)
    except OSError as exc:
        pytest.skip(f"hard links unavailable for test fixture: {exc.__class__.__name__}")
    try:
        _refusal(
            BootstrapErrorCode.UNSAFE_PATH,
            lambda: _enter_and_read(first, max_bytes=64),
        )
    finally:
        second.unlink(missing_ok=True)


def test_require_readonly_refuses_owned_writable_leaf(trusted_tmp: Path) -> None:
    trusted_tmp.write_bytes(b"fixed")
    _refusal(
        BootstrapErrorCode.PRECONDITION_FAILED,
        lambda: _enter_and_read(trusted_tmp, max_bytes=64, require_readonly=True),
    )


@pytest.mark.skipif(sys.platform != "linux", reason="Linux no-follow descriptor semantics")
def test_linux_fifo_does_not_block_and_symlinks_refuse(
    trusted_tmp: Path,
) -> None:
    fifo = trusted_tmp.with_suffix(".fifo")
    os.mkfifo(fifo)
    _refusal(
        BootstrapErrorCode.UNSAFE_PATH,
        lambda: _enter_and_read(fifo, max_bytes=64),
    )
    target = trusted_tmp
    target.write_bytes(b"fixed")
    link = trusted_tmp.with_name(trusted_tmp.stem + "-link.pyz")
    link.symlink_to(target)
    try:
        _refusal(
            BootstrapErrorCode.UNSAFE_PATH,
            lambda: _enter_and_read(link, max_bytes=64),
        )
    finally:
        fifo.unlink(missing_ok=True)
        link.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux identity-drift semantics")
def test_linux_content_change_after_open_refuses(
    trusted_tmp: Path,
) -> None:
    path = trusted_tmp
    path.write_bytes(b"fixed")
    with _trusted(path) as opened:
        with path.open("wb") as replacement:
            replacement.write(b"changed")
        _refusal(BootstrapErrorCode.PRECONDITION_FAILED, opened.read_verified)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows retained-handle semantics")
def test_windows_held_handle_blocks_write_until_context_exit(
    trusted_tmp: Path,
) -> None:
    path = trusted_tmp
    path.write_bytes(b"fixed")
    with _trusted(path) as opened:
        with pytest.raises(PermissionError):
            path.write_bytes(b"changed")
        assert opened.read_verified() == b"fixed"
    path.write_bytes(b"changed")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows retained-handle semantics")
def test_windows_final_rename_is_blocked_then_released(trusted_tmp: Path) -> None:
    trusted_tmp.write_bytes(b"fixed")
    replacement = trusted_tmp.with_name(trusted_tmp.stem + "-renamed.pyz")
    with _trusted(trusted_tmp) as opened:
        with pytest.raises(PermissionError):
            os.replace(trusted_tmp, replacement)
        assert opened.read_verified() == b"fixed"
    try:
        os.replace(trusted_tmp, replacement)
        assert replacement.read_bytes() == b"fixed"
    finally:
        if replacement.exists():
            os.replace(replacement, trusted_tmp)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows retained-handle semantics")
def test_windows_ancestor_rename_is_blocked_then_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise actual prefix locks; permission enforcement is covered above."""
    parent = tmp_path / "anchor"
    parent.mkdir()
    path = parent / "receiver.pyz"
    path.write_bytes(b"fixed")
    replacement = tmp_path / "renamed"
    monkeypatch.setattr(shim, "_permission_is_allowed", lambda *args, **kwargs: True)
    with _trusted(path) as opened:
        with pytest.raises(PermissionError):
            os.replace(parent, replacement)
        assert opened.read_verified() == b"fixed"
    os.replace(parent, replacement)
    assert (replacement / "receiver.pyz").read_bytes() == b"fixed"
    os.replace(replacement, parent)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows reparse semantics")
def test_windows_leaf_and_ancestor_reparse_refuse(
    trusted_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = uuid.uuid4().hex
    target = Path.cwd() / "anvil_serving"
    leaf = trusted_tmp.with_name(f"opened-leaf-{token}.pyz")
    ancestor = Path.cwd() / f"opened-ancestor-{token}"
    try:
        trusted_tmp.unlink()
        os.symlink(Path(shim.__file__), leaf)
        _refusal(
            BootstrapErrorCode.UNSAFE_PATH,
            lambda: _enter_and_read(leaf, max_bytes=64),
        )
        os.symlink(target, ancestor, target_is_directory=True)
        monkeypatch.setattr(shim, "_permission_is_allowed", lambda *args, **kwargs: True)
        _refusal(
            BootstrapErrorCode.UNSAFE_PATH,
            lambda: _enter_and_read(ancestor / "__init__.py", max_bytes=64),
        )
    except OSError as exc:
        pytest.skip(f"Windows link privilege unavailable: {exc.__class__.__name__}")
    finally:
        leaf.unlink(missing_ok=True)
        if ancestor.exists() or ancestor.is_symlink():
            ancestor.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native failure semantics")
def test_windows_injected_permission_and_partial_open_failures_release_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path(shim.__file__)
    monkeypatch.setattr(shim, "_permission_is_allowed", lambda *args, **kwargs: False)
    _refusal(
        BootstrapErrorCode.PRECONDITION_FAILED,
        lambda: _enter_and_read(path, max_bytes=shim.MAX_BUNDLE_BYTES),
    )

    monkeypatch.undo()
    original = shim._windows_open_prefix
    descriptors: list[int] = []

    def failing_prefix(value: str, *, directory: bool) -> int:
        if len(descriptors) >= 2:
            raise OSError("synthetic")
        descriptor = original(value, directory=directory)
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(shim, "_windows_open_prefix", failing_prefix)
    _refusal(
        BootstrapErrorCode.PRECONDITION_FAILED,
        lambda: _enter_and_read(path, max_bytes=shim.MAX_BUNDLE_BYTES),
    )
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_partial_open_cleanup_closes_owned_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if sys.platform != "linux":
        pytest.skip("descriptor-relative partial-open seam is Linux-specific")
    path = tmp_path / "one" / "two" / "receiver.pyz"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fixed")
    original_open = shim.os.open
    descriptors: list[int] = []

    def failing_open(*args: object, **kwargs: object) -> int:
        if len(descriptors) >= 2:
            raise OSError("synthetic")
        descriptor = original_open(*args, **kwargs)  # type: ignore[arg-type]
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(shim.os, "open", failing_open)
    _refusal(
        BootstrapErrorCode.PRECONDITION_FAILED,
        lambda: _enter_and_read(path, max_bytes=64),
    )
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_unsupported_platform_refuses_before_path_io(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shim.sys, "platform", "darwin")
    _refusal(
        BootstrapErrorCode.UNSUPPORTED_PLATFORM,
        lambda: shim.open_trusted_file("/not/read", max_bytes=1, require_readonly=False).__enter__(),
    )


@pytest.mark.skipif(sys.platform != "linux", reason="Linux readonly permission semantics")
def test_linux_require_readonly_refuses_owner_writable_file(
    trusted_tmp: Path,
) -> None:
    path = trusted_tmp
    path.write_bytes(b"fixed")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    _refusal(
        BootstrapErrorCode.PRECONDITION_FAILED,
        lambda: _enter_and_read(path, max_bytes=64),
    )
    os.chmod(path, stat.S_IRUSR)
    with _trusted(path, require_readonly=True) as opened:
        assert opened.read_verified() == b"fixed"
