"""Offline compaction for one exact Docker Desktop data VHDX on Windows.

The operation is intentionally narrow: it accepts only a plain file matching a
known Docker Desktop data-disk layout, stops Docker Desktop when necessary,
requires the VHDX to detach, mounts it read-only for ``Optimize-VHD -Mode Full``,
and leaves Docker Desktop stopped afterward.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


DEFAULT_TIMEOUT_SECONDS = 30
STOP_TIMEOUT_SECONDS = 150
COMPACT_TIMEOUT_SECONDS = 60 * 60
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_COMPRESSED = 0x800
FILE_ATTRIBUTE_ENCRYPTED = 0x4000
FILE_ATTRIBUTE_SPARSE_FILE = 0x200


class DockerDiskCompactionError(ValueError):
    """The requested Docker Desktop data-disk compaction is not safe to run."""


def _known_docker_data_layout(path: Path) -> bool:
    lowered = tuple(part.lower() for part in path.parts)
    return (
        len(lowered) >= 2
        and lowered[-2:] == ("disk", "docker_data.vhdx")
    ) or (
        len(lowered) >= 3
        and lowered[-3:] == ("disk", "docker", "_data.vhdx")
    )


def validate_docker_data_disk(path: str | os.PathLike[str]) -> Path:
    """Resolve and validate one explicit Docker Desktop data VHDX path."""
    raw = str(path or "").strip()
    if not raw:
        raise DockerDiskCompactionError("an explicit Docker data VHDX path is required")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise DockerDiskCompactionError("Docker data VHDX path must be absolute")
    if any(character in raw for character in "*?[]"):
        raise DockerDiskCompactionError("Docker data VHDX path must not contain wildcards")
    if candidate.is_symlink():
        raise DockerDiskCompactionError("Docker data VHDX path must not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
        stat = resolved.stat()
    except OSError as exc:
        raise DockerDiskCompactionError("Docker data VHDX is unavailable: %s" % exc) from exc
    if not resolved.is_file():
        raise DockerDiskCompactionError("Docker data VHDX path must identify a plain file")
    attributes = int(getattr(stat, "st_file_attributes", 0))
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise DockerDiskCompactionError("Docker data VHDX must not be a reparse point")
    if not _known_docker_data_layout(resolved):
        raise DockerDiskCompactionError(
            "path does not match a known Docker Desktop data disk layout "
            "(.../disk/docker_data.vhdx or .../disk/docker/_data.vhdx)"
        )
    blocked_attributes = []
    if attributes & FILE_ATTRIBUTE_COMPRESSED:
        blocked_attributes.append("compressed")
    if attributes & FILE_ATTRIBUTE_ENCRYPTED:
        blocked_attributes.append("encrypted")
    if attributes & FILE_ATTRIBUTE_SPARSE_FILE:
        blocked_attributes.append("sparse")
    if blocked_attributes:
        raise DockerDiskCompactionError(
            "Optimize-VHD cannot compact a %s data disk" % "/".join(blocked_attributes)
        )
    return resolved


def _run_checked(argv, *, runner, timeout, label, env=None):
    kwargs = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": timeout,
    }
    if env is not None:
        kwargs["env"] = env
    try:
        result = runner(argv, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DockerDiskCompactionError("%s unavailable: %s" % (label, exc)) from exc
    if result is None:
        raise DockerDiskCompactionError("%s returned no result" % label)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "%s failed" % label).strip()
        raise DockerDiskCompactionError("%s: %s" % (label, detail))
    return result


def _powershell_json(script, path, *, runner):
    environment = os.environ.copy()
    environment["ANVIL_SERVING_DOCKER_VHDX_PATH"] = str(path)
    result = _run_checked(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        runner=runner,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        label="Hyper-V VHD inspection",
        env=environment,
    )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise DockerDiskCompactionError("Hyper-V VHD inspection returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise DockerDiskCompactionError("Hyper-V VHD inspection returned invalid metadata")
    return payload


def _inspect_vhd(path, *, runner):
    script = (
        "$ErrorActionPreference='Stop'; "
        "$disk=Get-VHD -Path $env:ANVIL_SERVING_DOCKER_VHDX_PATH -ErrorAction Stop; "
        "[pscustomobject]@{"
        "Path=$disk.Path; VhdType=[string]$disk.VhdType; "
        "VhdFormat=[string]$disk.VhdFormat; FileSize=[int64]$disk.FileSize; "
        "Size=[int64]$disk.Size; Attached=[bool]$disk.Attached; "
        "FragmentationPercentage=[int]$disk.FragmentationPercentage; "
        "OptimizeVhdAvailable=[bool](Get-Command Optimize-VHD -ErrorAction SilentlyContinue)"
        "} | ConvertTo-Json -Compress"
    )
    payload = _powershell_json(script, path, runner=runner)
    required = {
        "Path", "VhdType", "VhdFormat", "FileSize", "Size", "Attached",
        "FragmentationPercentage", "OptimizeVhdAvailable",
    }
    if not required.issubset(payload):
        raise DockerDiskCompactionError("Hyper-V VHD inspection omitted required metadata")
    if str(payload["VhdFormat"]).upper() != "VHDX":
        raise DockerDiskCompactionError("Docker data disk is not VHDX format")
    if str(payload["VhdType"]).lower() == "fixed":
        raise DockerDiskCompactionError("fixed VHDX files cannot be compacted")
    if not payload["OptimizeVhdAvailable"]:
        raise DockerDiskCompactionError("Optimize-VHD is unavailable; enable the Hyper-V module")
    return {
        "path": str(Path(str(payload["Path"])).resolve()),
        "vhd_type": str(payload["VhdType"]),
        "vhd_format": str(payload["VhdFormat"]),
        "file_size_bytes": int(payload["FileSize"]),
        "virtual_size_bytes": int(payload["Size"]),
        "attached": bool(payload["Attached"]),
        "fragmentation_percentage": int(payload["FragmentationPercentage"]),
    }


def _docker_desktop_status(*, runner, vhd_attached):
    argv = ["docker", "desktop", "status", "--format", "json"]
    try:
        result = runner(
            argv,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DockerDiskCompactionError("Docker Desktop status unavailable: %s" % exc) from exc
    if result is None:
        raise DockerDiskCompactionError("Docker Desktop status returned no result")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        known_stopped = "Could not retrieve status. Is Docker Desktop running?" in detail
        if known_stopped and not vhd_attached:
            return "stopped"
        raise DockerDiskCompactionError(
            "Docker Desktop status: %s" % (detail or "command failed")
        )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise DockerDiskCompactionError("Docker Desktop status returned invalid JSON") from exc
    status = str(payload.get("Status") or "").strip().lower()
    if status not in {"running", "stopped"}:
        raise DockerDiskCompactionError("Docker Desktop returned unknown status %r" % status)
    return status


def inspect_docker_disk_compaction(path, *, runner=subprocess.run):
    """Return a read-only compaction preview for one exact Docker data disk."""
    if os.name != "nt":
        raise DockerDiskCompactionError("Docker Desktop VHDX compaction is Windows-only")
    resolved = validate_docker_data_disk(path)
    stat = resolved.stat()
    vhd = _inspect_vhd(resolved, runner=runner)
    status = _docker_desktop_status(runner=runner, vhd_attached=vhd["attached"])
    return {
        "schema": "docker-data-vhdx-compaction/v1",
        "path": str(resolved),
        "identity": {
            "device": int(stat.st_dev),
            "inode": int(stat.st_ino),
        },
        "docker_desktop_status": status,
        "would_stop_docker_desktop": status == "running",
        "vhd": vhd,
        "expected_effect": (
            "reclaim already-discarded free blocks; virtual capacity is unchanged and "
            "the compact operation may reclaim zero bytes"
        ),
        "recovery": "restart Docker Desktop; the VHDX remains the same Docker data disk",
    }


def _stop_docker_desktop(*, runner):
    _run_checked(
        ["docker", "desktop", "stop", "--timeout", "120"],
        runner=runner,
        timeout=STOP_TIMEOUT_SECONDS,
        label="Docker Desktop stop",
    )


def _optimize_vhd(path, *, runner):
    full_script = (
        "$ErrorActionPreference='Stop'; $mounted=$false; "
        "try { "
        "Mount-VHD -Path $env:ANVIL_SERVING_DOCKER_VHDX_PATH -ReadOnly -NoDriveLetter -ErrorAction Stop; "
        "$mounted=$true; "
        "Optimize-VHD -Path $env:ANVIL_SERVING_DOCKER_VHDX_PATH -Mode Full -ErrorAction Stop "
        "} finally { "
        "if ($mounted) { Dismount-VHD -Path $env:ANVIL_SERVING_DOCKER_VHDX_PATH -ErrorAction SilentlyContinue } "
        "}"
    )
    environment = os.environ.copy()
    environment["ANVIL_SERVING_DOCKER_VHDX_PATH"] = str(path)
    argv_prefix = [
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
    ]
    try:
        _run_checked(
            [*argv_prefix, full_script],
            runner=runner,
            timeout=COMPACT_TIMEOUT_SECONDS,
            label="Optimize-VHD full compaction",
            env=environment,
        )
        return "full-read-only"
    except DockerDiskCompactionError as exc:
        detail = str(exc).lower()
        privilege_missing = "required privilege is not held" in detail or "0x80070522" in detail
        if not privilege_missing:
            raise
    prezeroed_script = (
        "$ErrorActionPreference='Stop'; "
        "Optimize-VHD -Path $env:ANVIL_SERVING_DOCKER_VHDX_PATH "
        "-Mode Prezeroed -ErrorAction Stop"
    )
    _run_checked(
        [*argv_prefix, prezeroed_script],
        runner=runner,
        timeout=COMPACT_TIMEOUT_SECONDS,
        label="Optimize-VHD detached prezeroed compaction",
        env=environment,
    )
    return "detached-prezeroed"


def compact_docker_data_disk(
    path, *, confirm=False, dry_run=False, runner=subprocess.run,
):
    """Stop Docker Desktop and compact one twice-verified exact data VHDX."""
    first = inspect_docker_disk_compaction(path, runner=runner)
    result = {
        "schema": first["schema"],
        "applied": False,
        "dry_run": bool(dry_run),
        "outcome": "preview",
        "inspection": first,
    }
    if dry_run or not confirm:
        return result

    stopped_by_operation = first["docker_desktop_status"] == "running"
    if stopped_by_operation:
        _stop_docker_desktop(runner=runner)
    second = inspect_docker_disk_compaction(path, runner=runner)
    result["verification"] = second
    result["docker_stopped_by_operation"] = stopped_by_operation
    if second["docker_desktop_status"] != "stopped":
        result["outcome"] = "blocked"
        result["error"] = "Docker Desktop must be stopped before compaction"
        return result
    if second["vhd"]["attached"]:
        result["outcome"] = "blocked"
        result["error"] = "Docker data VHDX is still attached after Docker Desktop stopped"
        return result
    if second["identity"] != first["identity"]:
        result["outcome"] = "identity-drift"
        result["error"] = "Docker data VHDX identity changed after the preview"
        return result

    before_bytes = second["vhd"]["file_size_bytes"]
    compact_mode = _optimize_vhd(Path(second["path"]), runner=runner)
    final = inspect_docker_disk_compaction(path, runner=runner)
    result["final"] = final
    if final["docker_desktop_status"] != "stopped" or final["vhd"]["attached"]:
        result["outcome"] = "failed"
        result["error"] = "post-compaction state is not detached with Docker Desktop stopped"
        return result
    if final["identity"] != second["identity"]:
        result["outcome"] = "failed"
        result["error"] = "Docker data VHDX identity changed during compaction"
        return result
    after_bytes = final["vhd"]["file_size_bytes"]
    result.update({
        "applied": True,
        "outcome": "compacted",
        "before_bytes": before_bytes,
        "after_bytes": after_bytes,
        "reclaimed_bytes": max(0, before_bytes - after_bytes),
        "compact_mode": compact_mode,
        "docker_desktop_status": "stopped",
        "recovery": final["recovery"],
    })
    return result
