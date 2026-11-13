"""Install the fixed, local daily authentication-backup timer."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

from . import manage
from .config import _json_load, read_manifest
from .render import _unit_argument
from .users import DEFAULT_MANIFEST, _invalid, _require_root


_PREFIX = Path("/opt/anvil-connect")
_MANAGER = _PREFIX / "bin/anvil-connect-ctl"
_SERVICE = "anvil-connect-auth-backup.service"
_TIMER = "anvil-connect-auth-backup.timer"
_RELEASE = re.compile(r"releases/[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?-[a-f0-9]{12}\Z")


def _regular(path: Path, maximum: int) -> bytes:
    value = manage._read_regular(path, maximum)
    if value is None:
        raise _invalid("Installed Connect manager verification failed.")
    return value


def _installed_directory(path: Path) -> None:
    manage._safe_root_ancestors(path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o755:
        raise _invalid("Installed Connect manager verification failed.")


def _installed_manager_file(path: Path) -> None:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o755):
        raise _invalid("Installed Connect manager verification failed.")


def _release_manager(release_name: str, *, prefix: Path | None = None) -> Path:
    """Verify one immutable installer-owned manager release."""
    if _RELEASE.fullmatch(release_name) is None:
        raise _invalid("Installed Connect manager verification failed.")
    prefix = _PREFIX if prefix is None else prefix
    release = prefix / release_name
    for path in (prefix, prefix / "releases", release, release / "bin"):
        _installed_directory(path)
    target = release / "bin/anvil-connect-ctl"
    _installed_manager_file(target)
    manage._safe_root_ancestors(target)
    receipt = _json_load(_regular(release / "installation.json", 128 * 1024).decode())
    bundle_raw = _regular(release / "bundle.json", 128 * 1024)
    if (not isinstance(receipt, dict) or receipt.get("schema") != "anvil-connect.installation/v1"
            or receipt.get("role") != "gateway" or receipt.get("release") != release_name
            or "bin/anvil-connect-ctl" not in receipt.get("files", [])
            or hashlib.sha256(bundle_raw).hexdigest() != receipt.get("manifest_sha256")):
        raise _invalid("Installed Connect manager verification failed.")
    bundle = _json_load(bundle_raw.decode())
    entry = bundle.get("files", {}).get("bin/anvil-connect-ctl") if isinstance(bundle, dict) else None
    raw = _regular(target, 256 * 1024 * 1024)
    if (not isinstance(entry, dict) or entry.get("mode") != 0o755 or entry.get("size") != len(raw)
            or entry.get("sha256") != hashlib.sha256(raw).hexdigest()):
        raise _invalid("Installed Connect manager verification failed.")
    return target


def _manager() -> Path:
    """Resolve only the installer-owned immutable current manager release."""
    for path in (_PREFIX, _PREFIX / "bin", _PREFIX / "releases"):
        _installed_directory(path)
    if not _MANAGER.is_symlink() or os.readlink(_MANAGER) != "../current/bin/anvil-connect-ctl":
        raise _invalid("Installed Connect manager verification failed.")
    current = _PREFIX / "current"
    if not current.is_symlink() or _RELEASE.fullmatch(os.readlink(current)) is None:
        raise _invalid("Installed Connect manager verification failed.")
    target = _release_manager(os.readlink(current))
    try:
        same_target = os.path.samefile(_MANAGER, target)
    except OSError as exc:
        raise _invalid("Installed Connect manager verification failed.") from exc
    if not same_target:
        raise _invalid("Installed Connect manager verification failed.")
    return target


def _selected_manager() -> Path:
    """Verify the selected release even when its installer prefix differs."""
    manage._safe_root_ancestors(_MANAGER)
    alias = _MANAGER.lstat()
    if not stat.S_ISLNK(alias.st_mode) or alias.st_uid != 0:
        raise _invalid("Installed Connect manager selection is unsafe.")
    target = _MANAGER.resolve(strict=True)
    if len(target.parents) < 4 or target.parts[-2:] != ("bin", "anvil-connect-ctl"):
        raise _invalid("Installed Connect manager selection is invalid.")
    prefix = target.parents[3]
    release_name = str(target.parent.parent.relative_to(prefix))
    if _release_manager(release_name, prefix=prefix) != target:
        raise _invalid("Installed Connect manager selection is invalid.")
    return target


_BACKUP_PROPERTIES = (
    "ActiveState", "SubState", "MainPID", "ControlPID", "Job", "Result", "ExecMainCode",
    "ExecMainStatus", "ExecMainStartTimestampMonotonic", "InvocationID", "NeedDaemonReload",
)


def _backup_worker_state(runner) -> dict[str, str]:
    observed = manage._run(runner, (manage._SYSTEMCTL, "show", "--property=" + ",".join(_BACKUP_PROPERTIES),
                                   _SERVICE), manage._VALIDATE_TIMEOUT)
    manage._fail(observed, "Authentication backup worker state is unavailable.")
    rows = [line.split("=", 1) for line in observed.stdout.decode("utf-8").splitlines()]
    if any(len(row) != 2 for row in rows) or len(rows) != len(_BACKUP_PROPERTIES):
        raise _invalid("Authentication backup worker state is invalid.")
    state = dict(rows)
    if (set(state) != set(_BACKUP_PROPERTIES)
            or any(not state[name].isdigit() for name in ("MainPID", "ControlPID", "ExecMainCode",
                                                         "ExecMainStatus", "ExecMainStartTimestampMonotonic"))
            or (state["InvocationID"] and not re.fullmatch("[a-f0-9]{32}", state["InvocationID"]))):
        raise _invalid("Authentication backup worker state is invalid.")
    return state


def _idle_backup_worker(state: dict[str, str]) -> bool:
    return (state["ActiveState"] in {"inactive", "failed"} and state["SubState"] in {"dead", "failed"}
            and state["MainPID"] == state["ControlPID"] == "0" and not state["Job"]
            and state["NeedDaemonReload"] == "no")


def run_backup(manifest: str = DEFAULT_MANIFEST, *, apply: bool = False, runner=None,
               unit_root: str | Path = "/etc/systemd/system") -> dict:
    """Run only the exact installed worker; its child owns the deployment lock."""
    _require_root()
    manager = _selected_manager()
    data, root, backup = _manifest(manifest)
    manifest_bytes = _regular(Path(manifest), 2 * 1024 * 1024)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    manager_digest = manage._digest(manager)
    writable = tuple(sorted({root.parent, backup.parent, Path(data["gateway"]["state_directory"]).parent}, key=str))
    expected = _service(manager, writable)
    destination = Path(unit_root)
    unit = destination / _SERVICE
    manage._safe_root_ancestors(unit)
    metadata = unit.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o644):
        raise _invalid("Authentication backup worker ownership is invalid.")
    manage._verify_unit(destination, _SERVICE, expected)
    manage._unit_metadata(runner, destination, (_SERVICE,), present=True)
    before = _backup_worker_state(runner)
    if not _idle_backup_worker(before):
        raise _invalid("Authentication backup worker is running, queued or needs a daemon reload.")
    preview = manage._run(runner, (str(manager), "users", "backup", "--include-gateway", "--dry-run"), 20)
    manage._fail(preview, "Installed authentication backup preview failed.")
    receipt = manage._strict_json(preview.stdout, "Installed authentication backup preview is invalid.")
    detail = receipt.get("data")
    if (receipt.get("ok") is not True or not isinstance(detail, dict) or detail.get("applied") is not False
            or detail.get("operation") != "backup" or detail.get("include_gateway") is not True):
        raise _invalid("Installed authentication backup preview is invalid.")
    result = {"schema": "anvil-connect.scheduled-backup/v1", "applied": False, "unit": _SERVICE,
              "manager": str(manager), "manager_sha256": manager_digest, "manifest_sha256": manifest_digest,
              "previous_invocation_id": before["InvocationID"], "completion_verified": False,
              "impact": "Runs the installed full backup worker: briefly restarts Authelia, stops and restores the gateway, and applies backup retention. Existing gateway connections can disconnect."}
    if not apply:
        return result
    # Recheck after the child preview; never retain a deployment lock while
    # systemd waits for a child that acquires that same lock.
    if (_selected_manager() != manager or manage._digest(manager) != manager_digest
            or _regular(Path(manifest), 2 * 1024 * 1024) != manifest_bytes):
        raise _invalid("Authentication backup selection changed during preparation.")
    manage._verify_unit(destination, _SERVICE, expected)
    manage._unit_metadata(runner, destination, (_SERVICE,), present=True)
    current = _backup_worker_state(runner)
    if not _idle_backup_worker(current) or current != before:
        raise _invalid("Authentication backup worker changed during preparation.")
    try:
        # Killing a timed-out systemctl client does not stop the systemd worker.
        manage._action(runner, (manage._SYSTEMCTL, "start", _SERVICE), 130,
                       "Authentication backup worker did not finish successfully.")
        after = _backup_worker_state(runner)
        if (not _idle_backup_worker(after) or after["ActiveState"] != "inactive"
                or after["Result"] != "success" or after["ExecMainCode"] != "1" or after["ExecMainStatus"] != "0"
                or not after["InvocationID"] or after["InvocationID"] == before["InvocationID"]
                or int(after["ExecMainStartTimestampMonotonic"]) <= int(before["ExecMainStartTimestampMonotonic"])):
            raise manage.ManageError("Authentication backup worker completion is not verified.")
    except BaseException as exc:
        error = manage.ManageError("Backup worker may still be running or may have failed; inspect its state and retained backup receipts before retrying.", may_have_executed=True)
        error.recovery = {**result, "recovery_hint": "Do not retry until the backup worker has stopped and its result and retained receipts have been inspected."}
        raise error from exc
    return {**result, "applied": True, "completion_verified": True, "invocation_id": after["InvocationID"],
            "result": after["Result"], "exit_status": int(after["ExecMainStatus"])}


def _manifest(path: str) -> tuple[dict, Path, Path]:
    if path != DEFAULT_MANIFEST:
        raise _invalid("Daily authentication backup scheduling is available only for the installed default manifest.")
    manifest = Path(path)
    manage._safe_root_ancestors(manifest)
    info = manifest.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0 or info.st_mode & 0o022):
        raise _invalid("Installed default manifest verification failed.")
    data = read_manifest(path)
    root = Path(data["config_root"])
    backup = manifest.parent / "backups"
    for candidate in (root.parent, backup.parent):
        manage._safe_root_ancestors(candidate)
    return data, root, backup


def _service(manager: Path, writable: tuple[Path, ...], *, deletions: bool = False) -> bytes:
    paths = " ".join(_unit_argument(str(path)) for path in writable)
    operation = ("process-deletions", "--confirm") if deletions else ("backup", "--include-gateway", "--confirm")
    command = " ".join(_unit_argument(value) for value in (str(manager), "users", *operation))
    return ("\n".join((
        "[Unit]", "Description=Anvil Connect account reconciliation worker" if deletions else "Description=Anvil Connect daily authentication backup", "After=network-online.target", "Wants=network-online.target", "",
        "[Service]", "Type=oneshot", "User=root", "Group=root", "UMask=0077", "NoNewPrivileges=true",
        "ProtectSystem=strict", "ProtectHome=true", "PrivateTmp=true", "ReadWritePaths=" + paths,
        "ExecStart=" + command, "TimeoutStartSec=120", "",
    ))).encode()


def _timer(*, deletions: bool = False) -> bytes:
    if deletions:
        return b"[Unit]\nDescription=Anvil Connect account deletion schedule\n\n[Timer]\nOnBootSec=10s\nOnUnitInactiveSec=10s\nAccuracySec=1s\nUnit=anvil-connect-user-delete.service\n\n[Install]\nWantedBy=timers.target\n"
    return ("\n".join((
        "[Unit]", "Description=Anvil Connect daily authentication backup schedule", "",
        "[Timer]", "OnCalendar=*-*-* 03:17:00 UTC", "Persistent=true", "Unit=" + _SERVICE, "",
        "[Install]", "WantedBy=timers.target", "",
    ))).encode()


def _prior_service(current: bytes, desired: bytes) -> bool:
    """Accept only the prior generated service with a verified release manager."""
    legacy = b"Description=Anvil Connect account deletion worker\n"
    replacement = b"Description=Anvil Connect account reconciliation worker\n"
    if replacement in desired and current.count(legacy) == 1:
        current = current.replace(legacy, replacement, 1)
    start = desired.find(b"ExecStart=")
    if start < 0:
        raise AssertionError("generated service is missing ExecStart")
    start += len(b"ExecStart=")
    end = desired.find(b"\n", start)
    current_end = current.find(b"\n", start)
    if end < 0 or current_end < 0 or current[:start] != desired[:start] or current[current_end:] != desired[end:]:
        return False
    arguments = (b" users backup --confirm", b" users backup --include-gateway --confirm", b" users process-deletions --confirm")
    matched = next((value for value in arguments if current[start:current_end].endswith(value)), None)
    if matched is None:
        return False
    try:
        manager = current[start:current_end - len(matched)].decode("ascii")
        relative = Path(manager).relative_to(_PREFIX)
    except (UnicodeDecodeError, ValueError):
        return False
    if (len(relative.parts) != 4 or relative.parts[0] != "releases" or relative.parts[2:] != ("bin", "anvil-connect-ctl")):
        return False
    try:
        return str(_release_manager("releases/" + relative.parts[1])) == manager
    except Exception:
        return False


def _verify(unit_root: Path, units: dict[str, bytes], runner, service: str = _SERVICE) -> dict[str, bytes | None]:
    prior = {}
    for name, desired in units.items():
        current = manage._read_unit(unit_root / name)
        if current is not None and current != desired and (name != service or not _prior_service(current, desired)):
            raise _invalid("Authentication backup schedule is owned by another unit definition.")
        manage._unit_metadata(runner, unit_root, (name,), present=current is not None)
        prior[name] = current
    return prior


def _rollback(unit_root: Path, prior: dict[str, bytes | None], units: dict[str, bytes], runner, timer: str = _TIMER) -> None:
    # A failed enable --now can have created timer symlinks and started the
    # timer even when systemctl reports failure.  Remove that state before
    # removing the freshly owned unit file.
    if prior[timer] is None:
        manage._action(runner, (manage._SYSTEMCTL, "disable", "--now", timer), manage._SYSTEMD_TIMEOUT,
                       "authentication backup timer rollback failed")
    for name, current in prior.items():
        if current is None:
            if manage._read_unit(unit_root / name) == units[name]:
                (unit_root / name).unlink()
        else:
            manage._write_atomic(unit_root / name, current)
    manage._fail(manage._run(runner, (manage._SYSTEMCTL, "daemon-reload"), manage._SYSTEMD_TIMEOUT),
                 "authentication backup schedule rollback failed")


def schedule(manifest: str = DEFAULT_MANIFEST, *, apply: bool = False, runner=None,
             unit_root: str | Path = "/etc/systemd/system", deletions: bool = False) -> dict:
    """Preview or install the fixed daily default-manifest backup schedule."""
    _require_root()
    manager = _manager()
    data, root, backup = _manifest(manifest)
    writable = tuple(sorted({root.parent, backup.parent, Path(data["gateway"]["state_directory"]).parent}, key=str))
    service = "anvil-connect-user-delete.service" if deletions else _SERVICE
    timer = "anvil-connect-user-delete.timer" if deletions else _TIMER
    units = {service: _service(manager, writable, deletions=deletions), timer: _timer(deletions=deletions)}
    result = {
        "schema": "anvil-connect.auth-backup-schedule/v1", "action": "schedule", "applied": bool(apply),
        "manifest": manifest, "manager": str(manager), "units": sorted(units), "calendar": "daily 03:17 UTC",
        "persistent": True, "retention_days": 14, "retention_recent_copies": 7,
        "impact": "Daily backup briefly restarts Authelia, then stops and restores the native gateway while Caddy remains running.",
    }
    if deletions:
        result.update(schema="anvil-connect.user-deletion-schedule/v1", action="deletion-schedule", calendar="every 10 seconds", persistent=False, impact="Reconciles opted-in native application roles and the Connect-operator group before independently processing one authorized permanent account deletion per run.")
        result.pop("retention_days")
        result.pop("retention_recent_copies")
    if not apply:
        return result
    destination = Path(unit_root)
    manage._safe_dir(destination)
    with manage._deployment_lock(root):
        prior = _verify(destination, units, runner, service)
        timer_state = manage._unit_state(runner, timer) if prior[timer] is not None else (False, "disabled")
        try:
            for name, desired in units.items():
                if prior[name] != desired:
                    manage._write_atomic(destination / name, desired)
            manage._fail(manage._run(runner, (manage._SYSTEMCTL, "daemon-reload"), manage._SYSTEMD_TIMEOUT),
                         "systemd did not accept the authentication backup schedule")
            for name in units:
                manage._unit_metadata(runner, destination, (name,), present=True)
            if not timer_state[0] or timer_state[1] == "disabled":
                manage._action(runner, (manage._SYSTEMCTL, "enable", "--now", timer), manage._SYSTEMD_TIMEOUT,
                               "authentication backup timer failed to start")
            active, enabled = manage._unit_state(runner, timer)
            if not active or enabled == "disabled":
                raise manage.ManageError("authentication backup timer did not become active and enabled", may_have_executed=True)
        except BaseException as exc:
            try:
                _rollback(destination, prior, units, runner, timer)
                if prior[timer] is not None:
                    manage._restore_running(runner, (timer,), {timer: timer_state})
            except BaseException as rollback_error:
                raise manage.ManageError("authentication backup schedule rollback failed", may_have_executed=True) from rollback_error
            raise manage.ManageError("authentication backup schedule installation failed", may_have_executed=True) from exc
    return result
