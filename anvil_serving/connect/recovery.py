"""Offline authority recovery; configuration activation is a separate operation."""

from __future__ import annotations

from copy import deepcopy
import hmac
import os
import stat
from pathlib import Path
from typing import Any

from .config import ManifestError, read_manifest, validate_manifest
from . import manage


def _path(value: str | Path) -> Path:
    text = str(value)
    result = Path(text)
    if (not result.is_absolute() or str(result) != text or text.startswith("//")
            or text == "/" or ".." in result.parts or len(text) > 512
            or any(character in text for character in "\x00\r\n\t")):
        raise ManifestError("invalid recovery path")
    return result


def _digest(value: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ManifestError("invalid recovery digest")
    return value


def _private_directory(path: Path, uid: int) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise manage.ManageError("recovery parent is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != uid:
        raise manage.ManageError("recovery parent must be an existing private service directory")


def _recovery_identity(data: dict[str, Any]) -> tuple[manage.ServiceIdentity | None, int]:
    identity = manage._service_identity(data)
    return identity, os.geteuid() if identity is None else identity.uid


def _response(raw: bytes, *, operation: str) -> dict[str, str]:
    value = manage._strict_json(raw, "native recovery response is invalid")
    fields = {"mode", "status", "sha256"} | ({"grants"} if operation == "restore" else set())
    if (set(value) != fields or value.get("mode") != "gateway"
            or value.get("status") != {"backup": "backup-created", "restore": "restored"}[operation]
            or (operation == "restore" and value.get("grants") != "disabled")):
        raise manage.ManageError("native recovery response is invalid", may_have_executed=True)
    try:
        _digest(value["sha256"])
    except ManifestError as exc:
        raise manage.ManageError("native recovery response is invalid", may_have_executed=True) from exc
    return value


def backup(manifest_path: str | Path, *, output_path: str | Path, apply: bool = False, runner: manage.Runner | None = None) -> dict[str, Any]:
    """Back up an existing gateway offline, preserving every existing output."""
    data = read_manifest(manifest_path)
    output = _path(output_path)
    identity, uid = _recovery_identity(data)
    _private_directory(output.parent, uid)
    if os.path.lexists(output):
        raise manage.ManageError("backup output already exists")
    target = manage.Target("gateway")
    manage._current(data)
    digests = manage._verified_binaries(data, target)
    manage._bound_active(data, target, digests)
    result: dict[str, Any] = {"schema": "anvil-connect.recovery-plan/v1", "action": "backup", "applied": False,
        "native_sha256": digests["native"], "output": str(output), "requires_gateway_stopped": True,
        "maximum_archive_bytes": 1024 * 1024, "contains_private_authorities": True}
    if not apply:
        return result
    with manage._deployment_lock(Path(data["config_root"])):
        manage._current(data)
        manage._bound_active(data, target, manage._verified_binaries(data, target))
        try:
            response = manage._run(runner, (data["binary"], "backup", "--config", str(manage._config_path(data, target)), "--output", str(output)), 20, identity)
            manage._fail(response, "native backup failed")
            metadata = _response(response.stdout, operation="backup")
        except (OSError, RuntimeError) as exc:
            raise manage.ManageError("backup may have created its exclusive output; inspect recovery status", may_have_executed=True) from exc
    result.update({"applied": True, "sha256": metadata["sha256"], "retain_digest_separately": True})
    return result


def restore(manifest_path: str | Path, *, input_path: str | Path, destination: str | Path, sha256: str, native_sha256: str, apply: bool = False, runner: manage.Runner | None = None) -> dict[str, Any]:
    """Restore into a fresh private directory without starting or cutting over.

    The operator retains the archive digest independently. The native executable
    digest is explicit too, so this remains usable after the active config and
    activation record are lost without silently accepting another executable.
    """
    data = read_manifest(manifest_path)
    input_file, target_dir = _path(input_path), _path(destination)
    expected, expected_native = _digest(sha256), _digest(native_sha256)
    identity, uid = _recovery_identity(data)
    _private_directory(input_file.parent, uid)
    _private_directory(target_dir.parent, uid)
    try:
        info = input_file.lstat()
    except OSError as exc:
        raise manage.ManageError("private backup is unavailable") from exc
    if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != uid or info.st_nlink != 1 or not 0 < info.st_size <= 1024 * 1024):
        raise manage.ManageError("backup must be a bounded private service file")
    if os.path.lexists(target_dir):
        raise manage.ManageError("restore destination already exists")
    native = manage._native_verified(data)
    if not hmac.compare_digest(native, expected_native):
        raise manage.ManageError("native binary does not match the approved recovery digest")
    changed = deepcopy(data)
    changed["gateway"]["state_directory"] = str(target_dir)
    validate_manifest(changed)
    result: dict[str, Any] = {"schema": "anvil-connect.recovery-plan/v1", "action": "restore", "applied": False,
        "native_sha256": native, "sha256": expected, "input": str(input_file), "destination": str(target_dir),
        "grants": "disabled", "historical_credentials": "discarded", "config_change_required": True,
        "activation": "separate", "archive_integrity": "checked-by-native-before-write"}
    if not apply:
        return result
    with manage._temporary_declaration(changed, manage.Target("gateway")) as config:
        if not hmac.compare_digest(manage._native_verified(data), expected_native):
            raise manage.ManageError("native binary changed before recovery")
        try:
            response = manage._run(runner, (data["binary"], "restore", "--config", str(config), "--input", str(input_file), "--sha256", expected), 20, identity)
            manage._fail(response, "native restore failed")
            metadata = _response(response.stdout, operation="restore")
            if not hmac.compare_digest(metadata["sha256"], expected):
                raise manage.ManageError("native recovery digest mismatch", may_have_executed=True)
        except (OSError, RuntimeError) as exc:
            raise manage.ManageError("restore may have created an inert destination; inspect it before retrying", may_have_executed=True) from exc
    result["applied"] = True
    return result
