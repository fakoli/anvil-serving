"""Restore SOURCE TARGET offline; copy by default, or consume SOURCE with --move."""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import uuid

from . import docker_disk, guard


def _validate(path):
    candidate = Path(path)
    if (not candidate.is_absolute() or candidate.suffix.lower() != ".vhdx"
            or any(c in str(path) for c in "*?[]") or ".." in candidate.parts
            or any(":" in part for part in candidate.parts[1:])):
        raise ValueError("require an exact absolute .vhdx path without wildcards or streams")
    for part in (*reversed(candidate.parents), candidate):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("VHDX paths and ancestors must not be links or reparse points")
    info = candidate.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("VHDX must be a plain file with no hard links")
    return candidate.resolve(), info


def _identity(info):
    return info.st_dev, info.st_ino


def _open_locked(path, *, allow_replace=False):
    """Deny writes/mounts; allow read sharing and optional same-volume rename."""
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0x80000000, 1 | (4 if allow_replace else 0),
                    None, 3, 0x08000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, "rb")


def _replace_with_backup(target, staged, backup):
    if backup.exists():
        raise ValueError("refusing to overwrite a backup")
    # Windows rename refuses an occupied destination and honors our write-denying handles.
    # ReplaceFileW needs incompatible sharing rights on the replacement handle.
    target.rename(backup)
    try:
        staged.rename(target)
    except OSError:
        if not target.exists():
            backup.rename(target)
        raise


def _copy_verified(source, staged, expected_bytes):
    digest = hashlib.sha256()
    copied = 0
    with staged.open("wb") as output:
        while block := source.read(8 * 1024 * 1024):
            output.write(block)
            digest.update(block)
            copied += len(block)
        output.flush()
        os.fsync(output.fileno())
    if copied != expected_bytes:
        raise ValueError("source size changed during copy")
    with staged.open("rb") as copied_file:
        verified = hashlib.file_digest(copied_file, "sha256").hexdigest()
    if digest.hexdigest() != verified:
        raise ValueError("copied VHDX SHA-256 verification failed")
    return verified


def _require_engine_stopped(runner):
    result = runner(["docker", "--context", "desktop-linux", "info", "--format", "{{.ID}}"],
                    capture_output=True, text=True, errors="replace", timeout=15)
    if result is None or result.returncode == 0:
        raise ValueError("Docker desktop-linux engine still answers; restore refused")


def restore_docker_data_disk(source, target, *, confirm=False, dry_run=False,
                             move=False, runner=subprocess.run):
    """Copy and verify, or move on one volume, preserving the previous target."""
    if not docker_disk._is_windows():
        raise ValueError("Docker Desktop VHDX restore is Windows-only")
    source, source_info = _validate(source)
    target, target_info = _validate(target)
    if source == target or _identity(source_info) == _identity(target_info):
        raise ValueError("source and target must be different files")
    free = shutil.disk_usage(target.parent).free
    if move and source_info.st_dev != target_info.st_dev:
        raise ValueError("--move requires source and target on the same volume")
    if not move and free < source_info.st_size + 1024 * 1024 * 1024:
        raise ValueError("insufficient free space for the full copy plus 1 GiB reserve")
    status = docker_disk._docker_desktop_status(runner=runner, vhd_attached=False)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = target.with_name(f"{target.stem}.before-restore-{stamp}-{uuid.uuid4().hex[:8]}.vhdx")
    result = {
        "schema": "docker-data-vhdx-restore/v1", "applied": False,
        "outcome": "preview", "dry_run": bool(dry_run),
        "mode": "move" if move else "copy",
        "source": str(source), "target": str(target), "backup": str(backup),
        "copy_bytes": 0 if move else source_info.st_size, "free_bytes": free,
        "docker_desktop_status": status, "would_stop_docker_desktop": True,
        "verification": "same-volume rename; bytes unchanged" if move else "SHA-256 during copy and independent full read before replacement",
        "source_preserved": not move,
        "recovery": "Docker is left stopped. Restore the backup to the target with this command. In move mode the old source path is absent and Docker can modify the moved image after restart.",
    }
    if dry_run or not confirm:
        return result
    try:
        # Desktop's status can say stopped while its engine is still running.
        docker_disk._stop_docker_desktop(runner=runner)
        result["docker_desktop_status"] = docker_disk._docker_desktop_status(
            runner=runner, vhd_attached=False)
        if result["docker_desktop_status"] != "stopped":
            raise ValueError("Docker Desktop must be stopped before restore")
        _require_engine_stopped(runner)
        source, current_source = _validate(source)
        target, current_target = _validate(target)
        if (_identity(source_info) != _identity(current_source)
                or _identity(target_info) != _identity(current_target)
                or source_info.st_size != current_source.st_size
                or source_info.st_mtime_ns != current_source.st_mtime_ns):
            raise ValueError("VHDX identity or source contents changed after preview")
        with _open_locked(source, allow_replace=move) as source_file, _open_locked(target, allow_replace=True) as target_file:
            if (_identity(os.fstat(source_file.fileno())) != _identity(current_source)
                    or _identity(os.fstat(target_file.fileno())) != _identity(current_target)):
                raise ValueError("VHDX identity changed before locking")
            if source_file.read(8) != b"vhdxfile" or target_file.read(8) != b"vhdxfile":
                raise ValueError("source and target must have VHDX file signatures")
            source_file.seek(0)
            staged = source
            if not move:
                descriptor, staged_name = tempfile.mkstemp(prefix=".anvil-restore-", suffix=".vhdx", dir=target.parent)
                os.close(descriptor)
                staged = Path(staged_name)
                result["staged"] = str(staged)
                result["sha256"] = _copy_verified(source_file, staged, current_source.st_size)
            result["docker_desktop_status"] = docker_disk._docker_desktop_status(runner=runner, vhd_attached=False)
            if result["docker_desktop_status"] != "stopped":
                raise ValueError("Docker Desktop restarted during copy; replacement refused")
            _require_engine_stopped(runner)
            _, final_target = _validate(target)
            if _identity(final_target) != _identity(current_target):
                raise ValueError("target identity changed during copy")
            _, final_source = _validate(source)
            if (_identity(final_source) != _identity(current_source)
                    or final_source.st_size != current_source.st_size
                    or final_source.st_mtime_ns != current_source.st_mtime_ns):
                raise ValueError("source identity changed before replacement")
            # Keep the replacement locked against writes through both renames.
            if move:
                _replace_with_backup(target, staged, backup)
            else:
                with _open_locked(staged, allow_replace=True):
                    _replace_with_backup(target, staged, backup)
        result.update(applied=True, outcome="restored", docker_desktop_status="stopped")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result.update(outcome="failed", error=str(exc))
        result["recovery"] = (
            "Do not start Docker until source/target/backup paths are inspected. "
            "any staged copy is retained for diagnosis. Restore the backup with this command if needed."
        )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("target")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--move", action="store_true", help="Same-volume rename without a full copy; source path is consumed.")
    args = parser.parse_args(argv)
    try:
        result = restore_docker_data_disk(
            args.source, args.target, confirm=args.confirm or guard.confirmation_authorized(),
            dry_run=args.dry_run, move=args.move)
    except (OSError, ValueError) as exc:
        result = {"applied": False, "outcome": "invalid-or-unavailable", "error": str(exc)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["outcome"] in {"preview", "restored"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
