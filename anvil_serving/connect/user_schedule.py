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


def _release_manager(release_name: str) -> Path:
    """Verify one immutable installer-owned manager release."""
    if _RELEASE.fullmatch(release_name) is None:
        raise _invalid("Installed Connect manager verification failed.")
    release = _PREFIX / release_name
    for path in (_PREFIX, _PREFIX / "releases", release, release / "bin"):
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


def _service(manager: Path, writable: tuple[Path, ...]) -> bytes:
    paths = " ".join(_unit_argument(str(path)) for path in writable)
    command = " ".join(_unit_argument(value) for value in (str(manager), "users", "backup", "--confirm"))
    return ("\n".join((
        "[Unit]", "Description=Anvil Connect daily authentication backup", "After=network-online.target", "Wants=network-online.target", "",
        "[Service]", "Type=oneshot", "User=root", "Group=root", "UMask=0077", "NoNewPrivileges=true",
        "ProtectSystem=strict", "ProtectHome=true", "PrivateTmp=true", "ReadWritePaths=" + paths,
        "ExecStart=" + command, "TimeoutStartSec=120", "",
    ))).encode()


def _timer() -> bytes:
    return ("\n".join((
        "[Unit]", "Description=Anvil Connect daily authentication backup schedule", "",
        "[Timer]", "OnCalendar=*-*-* 03:17:00 UTC", "Persistent=true", "Unit=" + _SERVICE, "",
        "[Install]", "WantedBy=timers.target", "",
    ))).encode()


def _prior_service(current: bytes, desired: bytes) -> bool:
    """Accept only the prior generated service with a verified release manager."""
    start = desired.find(b"ExecStart=")
    if start < 0:
        raise AssertionError("generated service is missing ExecStart")
    start += len(b"ExecStart=")
    end = desired.find(b"\n", start)
    current_end = current.find(b"\n", start)
    if end < 0 or current_end < 0 or current[:start] != desired[:start] or current[current_end:] != desired[end:]:
        return False
    arguments = b" users backup --confirm"
    if not current[start:current_end].endswith(arguments):
        return False
    try:
        manager = current[start:current_end - len(arguments)].decode("ascii")
        relative = Path(manager).relative_to(_PREFIX)
    except (UnicodeDecodeError, ValueError):
        return False
    if (len(relative.parts) != 4 or relative.parts[0] != "releases" or relative.parts[2:] != ("bin", "anvil-connect-ctl")):
        return False
    try:
        return str(_release_manager("releases/" + relative.parts[1])) == manager
    except Exception:
        return False


def _verify(unit_root: Path, units: dict[str, bytes], runner) -> dict[str, bytes | None]:
    prior = {}
    for name, desired in units.items():
        current = manage._read_unit(unit_root / name)
        if current is not None and current != desired and (name != _SERVICE or not _prior_service(current, desired)):
            raise _invalid("Authentication backup schedule is owned by another unit definition.")
        manage._unit_metadata(runner, unit_root, (name,), present=current is not None)
        prior[name] = current
    return prior


def _rollback(unit_root: Path, prior: dict[str, bytes | None], units: dict[str, bytes], runner) -> None:
    # A failed enable --now can have created timer symlinks and started the
    # timer even when systemctl reports failure.  Remove that state before
    # removing the freshly owned unit file.
    if prior[_TIMER] is None:
        manage._action(runner, (manage._SYSTEMCTL, "disable", "--now", _TIMER), manage._SYSTEMD_TIMEOUT,
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
             unit_root: str | Path = "/etc/systemd/system") -> dict:
    """Preview or install the fixed daily default-manifest backup schedule."""
    _require_root()
    manager = _manager()
    _, root, backup = _manifest(manifest)
    writable = tuple(sorted({root.parent, backup.parent}, key=str))
    units = {_SERVICE: _service(manager, writable), _TIMER: _timer()}
    result = {
        "schema": "anvil-connect.auth-backup-schedule/v1", "action": "schedule", "applied": bool(apply),
        "manifest": manifest, "manager": str(manager), "units": sorted(units), "calendar": "daily 03:17 UTC",
        "persistent": True, "retention_days": 14, "retention_recent_copies": 7,
        "impact": "Daily authentication backup briefly stops and restarts Authelia.",
    }
    if not apply:
        return result
    destination = Path(unit_root)
    manage._safe_dir(destination)
    with manage._deployment_lock(root):
        prior = _verify(destination, units, runner)
        timer_state = manage._unit_state(runner, _TIMER) if prior[_TIMER] is not None else (False, "disabled")
        try:
            for name, desired in units.items():
                if prior[name] != desired:
                    manage._write_atomic(destination / name, desired)
            manage._fail(manage._run(runner, (manage._SYSTEMCTL, "daemon-reload"), manage._SYSTEMD_TIMEOUT),
                         "systemd did not accept the authentication backup schedule")
            for name in units:
                manage._unit_metadata(runner, destination, (name,), present=True)
            if not timer_state[0] or timer_state[1] == "disabled":
                manage._action(runner, (manage._SYSTEMCTL, "enable", "--now", _TIMER), manage._SYSTEMD_TIMEOUT,
                               "authentication backup timer failed to start")
            active, enabled = manage._unit_state(runner, _TIMER)
            if not active or enabled == "disabled":
                raise manage.ManageError("authentication backup timer did not become active and enabled", may_have_executed=True)
        except BaseException as exc:
            try:
                _rollback(destination, prior, units, runner)
                if prior[_TIMER] is not None:
                    manage._restore_running(runner, (_TIMER,), {_TIMER: timer_state})
            except BaseException as rollback_error:
                raise manage.ManageError("authentication backup schedule rollback failed", may_have_executed=True) from rollback_error
            raise manage.ManageError("authentication backup schedule installation failed", may_have_executed=True) from exc
    return result
