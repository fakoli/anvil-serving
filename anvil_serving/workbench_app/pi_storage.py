"""Provision and prove the fixed local storage boundary for Pi runners.

The pool is deliberately a loop-backed ext4 filesystem.  It is a host-side
capacity boundary for runner-owned checkouts and native Pi state, while the
Workbench journal and evidence store remain on their normal server-owned
paths.  This module never accepts a path from a browser request.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Callable, Mapping


GIB = 1024**3
DEFAULT_SIZE_BYTES = 8 * GIB
MIN_SIZE_BYTES = GIB
MAX_SIZE_BYTES = 16 * GIB
_COMMAND_TIMEOUT_SECONDS = 60
_OUTPUT_LIMIT = 16 * 1024


class PiStorageError(ValueError):
    """The installed Pi storage pool cannot be proven safe."""


def _require_linux(platform: str | None = None) -> None:
    """Keep host storage provisioning explicitly Linux-only after portable parsing."""
    if (sys.platform if platform is None else platform) != "linux":
        raise PiStorageError("Pi storage provisioning is supported only on Linux")


@dataclass(frozen=True)
class PiStorageConfig:
    image_path: Path
    pool_path: Path
    size_bytes: int
    uid: int
    gid: int
    runner_storage_root: Path
    runner_roots: tuple[Path, ...]


@dataclass(frozen=True)
class MountRecord:
    mountpoint: Path
    source: str
    filesystem: str
    options: frozenset[str]


def _absolute_path(value: object, label: str) -> Path:
    if type(value) is not str:
        raise PiStorageError(f"{label} must be an absolute private path")
    path = Path(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path == Path("/")
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise PiStorageError(f"{label} must be a safe absolute private path")
    return path


def _integer(value: object, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise PiStorageError(f"{label} is out of range")
    return value


def storage_config(value: Mapping[str, object]) -> PiStorageConfig:
    """Parse only the protected configuration used by the storage boundary."""
    if not isinstance(value, Mapping):
        raise PiStorageError("Workbench configuration must be an object")
    storage = value.get("pi_storage")
    pi = value.get("pi")
    if not isinstance(storage, Mapping) or not isinstance(pi, Mapping):
        raise PiStorageError("Pi storage and Pi runner configuration are required")
    allowed = {"image_path", "pool_path", "size_bytes"}
    if set(storage) - allowed or not {"image_path", "pool_path"} <= set(storage):
        raise PiStorageError("Pi storage configuration has unsupported or missing fields")
    image = _absolute_path(storage["image_path"], "pi_storage.image_path")
    pool = _absolute_path(storage["pool_path"], "pi_storage.pool_path")
    if image == pool or image.parent == pool or pool.parent == image:
        raise PiStorageError("Pi image and mount paths must be distinct")
    size = _integer(storage.get("size_bytes", DEFAULT_SIZE_BYTES), "pi_storage.size_bytes", MIN_SIZE_BYTES, MAX_SIZE_BYTES)
    uid = _integer(pi.get("uid"), "pi.uid", 1, 2**31 - 1)
    gid = _integer(pi.get("gid"), "pi.gid", 1, 2**31 - 1)
    runner_storage = _absolute_path(pi.get("runner_storage_root"), "pi.runner_storage_root")
    state_path = _absolute_path(value.get("state_path"), "state_path")
    pi_state_root = _absolute_path(pi.get("state_root"), "pi.state_root")
    _disjoint(pool, state_path, "Workbench journal")
    _disjoint(pool, pi_state_root, "Pi server journal")
    roots: list[Path] = [runner_storage]
    projects = value.get("projects", [])
    if not isinstance(projects, list):
        raise PiStorageError("Workbench projects must be a list")
    for index, project in enumerate(projects):
        if not isinstance(project, Mapping):
            raise PiStorageError("Workbench project must be an object")
        runner_root = project.get("runner_root")
        if runner_root is not None:
            roots.append(_absolute_path(runner_root, f"projects[{index}].runner_root"))
    for root in roots:
        _contained(pool, root, "Pi writable path")
    return PiStorageConfig(image, pool, size, uid, gid, runner_storage, tuple(roots))


def _contained(parent: Path, child: Path, label: str) -> None:
    try:
        child.relative_to(parent)
    except ValueError as exc:
        raise PiStorageError(f"{label} must be inside the configured Pi storage pool") from exc


def _disjoint(first: Path, second: Path, label: str) -> None:
    for parent, child in ((first, second), (second, first)):
        try:
            child.relative_to(parent)
        except ValueError:
            continue
        raise PiStorageError(f"{label} must remain outside and disjoint from the Pi storage pool")


def _unescape_mount(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 3 < len(value) and value[index + 1:index + 4].isdigit():
            try:
                result.append(chr(int(value[index + 1:index + 4], 8)))
                index += 4
                continue
            except ValueError:
                pass
        result.append(value[index])
        index += 1
    return "".join(result)


def read_mounts(path: Path = Path("/proc/self/mountinfo")) -> tuple[MountRecord, ...]:
    """Return a minimal mount table without invoking host commands."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PiStorageError("cannot read the active mount table") from exc
    records: list[MountRecord] = []
    for line in lines:
        left, separator, right = line.partition(" - ")
        fields = left.split()
        right_fields = right.split()
        if not separator or len(fields) < 6 or len(right_fields) < 3:
            continue
        options = frozenset(fields[5].split(",")) | frozenset(right_fields[2].split(","))
        records.append(MountRecord(Path(_unescape_mount(fields[4])), _unescape_mount(right_fields[1]), right_fields[0], options))
    return tuple(records)


def _assert_no_symlink(path: Path, *, allow_missing_leaf: bool) -> None:
    """Reject an existing symlink in a configured path without resolving it."""
    current = Path(path.root)
    parts = path.parts[1:]
    for position, part in enumerate(parts):
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            if allow_missing_leaf or position == len(parts) - 1:
                return
            return
        except OSError as exc:
            raise PiStorageError(f"cannot inspect configured path {path}") from exc
        if stat.S_ISLNK(mode):
            raise PiStorageError(f"configured path contains a symlink: {path}")


def _loop_backing(source: str, sys_block: Path) -> Path:
    device = Path(source)
    if device.parent != Path("/dev") or not device.name.startswith("loop"):
        raise PiStorageError("Pi storage mount must use a loop device")
    backing = sys_block / device.name / "loop" / "backing_file"
    try:
        value = backing.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PiStorageError("cannot prove the loop device backing file") from exc
    if not value.startswith("/"):
        raise PiStorageError("loop backing file is not an absolute path")
    return Path(value)


def validate_pool(
    value: Mapping[str, object],
    *,
    mountinfo_path: Path = Path("/proc/self/mountinfo"),
    sys_block: Path = Path("/sys/block"),
    platform: str | None = None,
) -> dict[str, object]:
    """Read-only proof that the configured Pi writable paths have a hard bound."""
    _require_linux(platform)
    config = storage_config(value)
    _assert_no_symlink(config.image_path, allow_missing_leaf=False)
    _assert_no_symlink(config.pool_path, allow_missing_leaf=False)
    try:
        image_stat = os.stat(config.image_path, follow_symlinks=False)
    except OSError as exc:
        raise PiStorageError("configured Pi image does not exist") from exc
    if not stat.S_ISREG(image_stat.st_mode) or image_stat.st_size != config.size_bytes:
        raise PiStorageError("configured Pi image is not the exact fixed-size regular file")
    if image_stat.st_uid != 0 or stat.S_IMODE(image_stat.st_mode) != 0o600:
        raise PiStorageError("configured Pi image must be root-owned mode 0600")
    matching = [row for row in read_mounts(mountinfo_path) if row.mountpoint == config.pool_path]
    if len(matching) != 1:
        raise PiStorageError("configured Pi pool is not mounted exactly once")
    mount = matching[0]
    for row in read_mounts(mountinfo_path):
        if row.mountpoint == config.pool_path:
            continue
        try:
            row.mountpoint.relative_to(config.pool_path)
        except ValueError:
            continue
        raise PiStorageError("Pi storage pool contains a nested mountpoint")
    if mount.filesystem != "ext4" or not {"nodev", "nosuid"} <= mount.options:
        raise PiStorageError("Pi pool must be an ext4 mount with nodev,nosuid")
    backing = _loop_backing(mount.source, sys_block)
    if backing != config.image_path:
        raise PiStorageError("Pi loop device does not use the configured image")
    try:
        pool_stat = os.stat(config.pool_path, follow_symlinks=False)
    except OSError as exc:
        raise PiStorageError("configured Pi pool is unavailable") from exc
    if not stat.S_ISDIR(pool_stat.st_mode) or pool_stat.st_uid != config.uid or pool_stat.st_gid != config.gid or stat.S_IMODE(pool_stat.st_mode) != 0o700:
        raise PiStorageError("Pi pool ownership or mode is not the configured 0700 boundary")
    for writable in config.runner_roots:
        _assert_no_symlink(writable, allow_missing_leaf=True)
    return {
        "pool_path": str(config.pool_path),
        "image_path": str(config.image_path),
        "size_bytes": config.size_bytes,
        "filesystem": mount.filesystem,
        "mount_options": sorted({"nodev", "nosuid"}),
        "runner_storage_root": str(config.runner_storage_root),
        "runner_roots": [str(path) for path in config.runner_roots],
    }


def _bounded_run(run: Callable[..., subprocess.CompletedProcess[str]], argv: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        result = run(argv, check=False, text=True, capture_output=True, timeout=_COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PiStorageError(f"cannot run {argv[0]} safely") from exc
    stdout = (result.stdout or "")[:_OUTPUT_LIMIT]
    stderr = (result.stderr or "")[:_OUTPUT_LIMIT]
    if result.returncode:
        detail = stderr.strip() or stdout.strip() or f"exit {result.returncode}"
        raise PiStorageError(f"{argv[0]} failed: {detail}")
    return result


def _mount_unit_name(pool: Path) -> str:
    encoded: list[str] = []
    for component in pool.parts[1:]:
        for byte in os.fsencode(component):
            if 48 <= byte <= 57 or 65 <= byte <= 90 or 97 <= byte <= 122 or byte in b"_.":
                encoded.append(chr(byte))
            elif byte == ord("-"):
                encoded.append("\\x2d")
            else:
                encoded.append("\\x%02x" % byte)
        encoded.append("-")
    return "".join(encoded).rstrip("-") + ".mount"


def _unit_path_value(path: Path) -> str:
    """Encode a filesystem path as one systemd unit value, never raw text."""
    result: list[str] = []
    for byte in os.fsencode(str(path)):
        if 48 <= byte <= 57 or 65 <= byte <= 90 or 97 <= byte <= 122 or byte in b"/._-":
            result.append(chr(byte))
        else:
            result.append(f"\\x{byte:02x}")
    return "".join(result)


class PiStorageManager:
    """One-shot, idempotent provisioning of a preconfigured private pool."""

    def __init__(
        self,
        value: Mapping[str, object],
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        systemd_root: Path = Path("/etc/systemd/system"),
        geteuid: Callable[[], int] | None = None,
        mountinfo_path: Path = Path("/proc/self/mountinfo"),
        sys_block: Path = Path("/sys/block"),
        platform: str | None = None,
    ) -> None:
        self.config = storage_config(value)
        self._value = value
        self._run = run
        self._systemd_root = systemd_root
        self._geteuid = geteuid
        self._mountinfo_path = mountinfo_path
        self._sys_block = sys_block
        self._platform = platform
        self._created_image_identity: tuple[int, int] | None = None

    def plan(self) -> dict[str, object]:
        config = self.config
        return {
            "image_path": str(config.image_path),
            "pool_path": str(config.pool_path),
            "size_bytes": config.size_bytes,
            "filesystem": "ext4",
            "mount_options": ["loop", "nodev", "nosuid"],
            "runner_storage_root": str(config.runner_storage_root),
            "runner_roots": [str(path) for path in config.runner_roots],
            "unit_path": str(self._systemd_root / _mount_unit_name(config.pool_path)),
            "commands": [
                ["mkfs.ext4", "-F", str(config.image_path)],
                ["mount", "-o", "loop,nodev,nosuid", str(config.image_path), str(config.pool_path)],
                ["systemctl", "daemon-reload"],
                ["systemctl", "enable", _mount_unit_name(config.pool_path)],
            ],
        }

    def _ensure_parent(self, path: Path) -> None:
        _assert_no_symlink(path.parent, allow_missing_leaf=True)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _assert_no_symlink(path.parent, allow_missing_leaf=False)

    def _existing_image_is_ext4(self) -> None:
        config = self.config
        _assert_no_symlink(config.image_path, allow_missing_leaf=False)
        try:
            details = os.stat(config.image_path, follow_symlinks=False)
        except OSError as exc:
            raise PiStorageError("cannot inspect existing Pi image") from exc
        if not stat.S_ISREG(details.st_mode) or details.st_size != config.size_bytes:
            raise PiStorageError("existing Pi image is not the configured regular fixed-size file")
        if details.st_uid != 0 or stat.S_IMODE(details.st_mode) != 0o600:
            raise PiStorageError("existing Pi image must be root-owned mode 0600")
        result = _bounded_run(self._run, ["blkid", "-p", "-o", "value", "-s", "TYPE", str(config.image_path)])
        if (result.stdout or "").strip() != "ext4":
            raise PiStorageError("existing Pi image is not proven ext4; refusing to reformat it")

    def _create_image(self) -> bool:
        config = self.config
        self._ensure_parent(config.image_path)
        try:
            descriptor = os.open(config.image_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            self._existing_image_is_ext4()
            return False
        try:
            os.ftruncate(descriptor, config.size_bytes)
        finally:
            os.close(descriptor)
        _bounded_run(self._run, ["mkfs.ext4", "-F", str(config.image_path)])
        os.chmod(config.image_path, 0o600)
        details = os.stat(config.image_path, follow_symlinks=False)
        self._created_image_identity = (details.st_dev, details.st_ino)
        return True

    def _mounted_here(self) -> MountRecord | None:
        matches = [row for row in read_mounts(self._mountinfo_path) if row.mountpoint == self.config.pool_path]
        if len(matches) > 1:
            raise PiStorageError("multiple mounts exist at the configured Pi pool")
        return matches[0] if matches else None

    def _write_unit(self) -> Path:
        config = self.config
        name = _mount_unit_name(config.pool_path)
        target = self._systemd_root / name
        content = (
            "[Unit]\nDescription=Anvil Workbench Pi bounded storage pool\n"
            "[Mount]\n"
            f"What={_unit_path_value(config.image_path)}\nWhere={_unit_path_value(config.pool_path)}\nType=ext4\nOptions=loop,nodev,nosuid\n"
            "[Install]\nWantedBy=local-fs.target\n"
        )
        self._systemd_root.mkdir(parents=True, exist_ok=True, mode=0o755)
        if target.exists():
            if target.is_symlink() or target.read_text(encoding="utf-8") != content:
                raise PiStorageError("existing Pi storage mount unit is not the reviewed configuration")
            return target
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            raise PiStorageError("Pi storage mount unit changed during installation")
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
        return target

    def _rollback_new_image(self) -> None:
        """Delete only this invocation's unmounted, regular newly-created image."""
        identity = self._created_image_identity
        if identity is None:
            return
        try:
            mounted = self._mounted_here()
            details = os.stat(self.config.image_path, follow_symlinks=False)
        except (OSError, PiStorageError):
            return
        if (
            mounted is None
            and stat.S_ISREG(details.st_mode)
            and stat.S_IMODE(details.st_mode) == 0o600
            and (details.st_dev, details.st_ino) == identity
        ):
            try:
                os.unlink(self.config.image_path)
            except OSError:
                pass

    def provision(self, *, confirm: bool) -> dict[str, object]:
        """Create, mount, verify, and persist the pool after an explicit gate."""
        if not confirm:
            return {"dry_run": True, **self.plan()}
        _require_linux(self._platform)
        geteuid = self._geteuid or getattr(os, "geteuid", None)
        if geteuid is None or geteuid() != 0:
            raise PiStorageError("Pi storage provisioning requires root; inspect the dry-run first")
        config = self.config
        _assert_no_symlink(config.pool_path, allow_missing_leaf=True)
        self._ensure_parent(config.pool_path)
        if not config.pool_path.exists():
            config.pool_path.mkdir(mode=0o700)
        elif not config.pool_path.is_dir():
            raise PiStorageError("configured Pi pool path is not a directory")
        mounted = self._mounted_here()
        if mounted is not None:
            # A pre-existing mount is never "converged" by changing its
            # ownership or mode.  Prove its exact backing, mount flags, pool
            # owner, writable roots, and journal separation before the only
            # remaining mutation (writing the reviewed boot unit).
            proof = validate_pool(self._value, mountinfo_path=self._mountinfo_path, sys_block=self._sys_block)
            unit = self._write_unit()
            _bounded_run(self._run, ["systemctl", "daemon-reload"])
            _bounded_run(self._run, ["systemctl", "enable", unit.name])
            return {"provisioned": True, "unit_path": str(unit), **proof}
        if mounted is None and any(config.pool_path.iterdir()):
            raise PiStorageError("refusing to mount over a non-empty Pi pool directory")
        # The parent is a configured, private setup path.  It must exist before
        # statvfs can prove capacity, but no image is created until after that
        # proof succeeds.
        self._ensure_parent(config.image_path)
        free = os.statvfs(config.image_path.parent).f_bavail * os.statvfs(config.image_path.parent).f_frsize
        if not config.image_path.exists() and free < config.size_bytes:
            raise PiStorageError("insufficient free space for the fixed Pi image")
        created = False
        try:
            created = self._create_image()
            if mounted is None:
                _bounded_run(self._run, ["mount", "-o", "loop,nodev,nosuid", str(config.image_path), str(config.pool_path)])
            os.chown(config.pool_path, config.uid, config.gid)
            os.chmod(config.pool_path, 0o700)
            proof = validate_pool(self._value, mountinfo_path=self._mountinfo_path, sys_block=self._sys_block)
            unit = self._write_unit()
            _bounded_run(self._run, ["systemctl", "daemon-reload"])
            _bounded_run(self._run, ["systemctl", "enable", unit.name])
            return {"provisioned": True, "unit_path": str(unit), **proof}
        except Exception:
            if created:
                self._rollback_new_image()
            raise
