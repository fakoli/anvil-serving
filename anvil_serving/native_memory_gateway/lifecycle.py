"""Managed systemd lifecycle for one native Memory OIDC sidecar.

The deployment declaration contains provenance, never credential bytes.  This
module owns only its three derived units and its private runtime/config copies;
the Connect manifest and both LoadCredential sources remain externally owned.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import time

from ..connect import application_roles, manage
from ..native_agents.auth import protected_read, strict_json
from .server import validate_config


SCHEMA = "anvil-native-memory-service/v1"
RESULT_SCHEMA = "anvil-native-memory-service-result/v1"
_SYSTEMCTL = "/usr/bin/systemctl"
_PYTHON_LINK = Path("/usr/bin/python3")
_UNIT_ROOT = Path("/etc/systemd/system")
_STATE_ROOT = Path("/var/lib/anvil-native-memory")
_LOCK = Path("/run/lock/anvil-native-memory-service.lock")
_PROC_ROOT = Path("/proc")
_SHA = re.compile(r"[a-f0-9]{64}")
_ID = re.compile(r"[a-z][a-z0-9-]{0,20}")
_ACCOUNT = re.compile(r"[a-z_][a-z0-9_-]{0,30}")


class LifecycleError(ValueError):
    def __init__(self, message: str, *, may_have_executed: bool = False):
        super().__init__(message)
        self.may_have_executed = may_have_executed


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _protected(path: Path, maximum: int) -> bytes:
    try:
        return protected_read(path, maximum)
    except Exception:
        raise LifecycleError("protected deployment input is unavailable") from None


def _file(path: str, digest: str, maximum: int) -> bytes:
    candidate = Path(path)
    if not candidate.is_absolute() or _SHA.fullmatch(digest) is None:
        raise LifecycleError("deployment file reference is invalid")
    raw = _protected(candidate, maximum)
    if _sha(raw) != digest:
        raise LifecycleError("deployment file provenance does not match")
    return raw


def _root_file(path: str | Path, digest: str, maximum: int, modes: set[int]) -> bytes:
    candidate = Path(path)
    if not candidate.is_absolute() or _SHA.fullmatch(digest) is None:
        raise LifecycleError("deployment executable reference is invalid")
    try:
        for parent in reversed(candidate.parents):
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise LifecycleError("deployment executable custody is invalid")
        fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
                    or stat.S_IMODE(before.st_mode) not in modes or not 1 <= before.st_size <= maximum):
                raise LifecycleError("deployment executable custody is invalid")
            chunks, size = [], 0
            while size <= maximum:
                part = os.read(fd, min(65536, maximum + 1 - size))
                if not part:
                    break
                chunks.append(part)
                size += len(part)
            raw = b"".join(chunks)
            after = os.fstat(fd)
        finally:
            os.close(fd)
    except OSError:
        raise LifecycleError("deployment executable custody is invalid") from None
    def identity(value):
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                value.st_uid, value.st_gid, value.st_mode, value.st_nlink)
    if len(raw) > maximum or identity(before) != identity(after) or _sha(raw) != digest:
        raise LifecycleError("deployment executable provenance does not match")
    return raw


def _executable(path: str | Path, digest: str, maximum: int) -> bytes:
    return _root_file(path, digest, maximum, {0o555, 0o755})


def _artifact(path: str | Path, digest: str, maximum: int) -> bytes:
    return _root_file(path, digest, maximum, {0o444, 0o644})


def _python(digest: str) -> str:
    """Resolve the fixed distro interpreter and pin its regular target."""
    try:
        target = _PYTHON_LINK.resolve(strict=True)
        link = _PYTHON_LINK.lstat()
    except OSError:
        raise LifecycleError("system Python provenance is unavailable") from None
    if (target.parent != Path("/usr/bin") or target == _PYTHON_LINK
            or not stat.S_ISLNK(link.st_mode) or link.st_uid != 0):
        raise LifecycleError("system Python provenance is invalid")
    _executable(target, digest, 64 * 1024 * 1024)
    return str(target)


def _exact_dict(value, fields: set[str], message: str) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise LifecycleError(message)
    return value


def load_declaration(path: str | Path) -> tuple[dict, str]:
    raw = _protected(Path(path), 1024 * 1024)
    try:
        value = strict_json(raw)
    except Exception:
        raise LifecycleError("native Memory service declaration is invalid") from None
    fields = {"schema", "deployment_id", "identity", "runtime", "config", "credentials", "authority", "limits"}
    value = _exact_dict(value, fields, "native Memory service declaration is invalid")
    if value["schema"] != SCHEMA or type(value["deployment_id"]) is not str or _ID.fullmatch(value["deployment_id"]) is None:
        raise LifecycleError("native Memory service declaration is invalid")
    identity = _exact_dict(value["identity"], {"user", "uid", "group", "gid"}, "service identity is invalid")
    if (type(identity["user"]) is not str or _ACCOUNT.fullmatch(identity["user"]) is None
            or type(identity["group"]) is not str or _ACCOUNT.fullmatch(identity["group"]) is None
            or type(identity["uid"]) is not int or isinstance(identity["uid"], bool) or not 1000 <= identity["uid"] < 2**31
            or type(identity["gid"]) is not int or isinstance(identity["gid"], bool) or not 1000 <= identity["gid"] < 2**31):
        raise LifecycleError("service identity is invalid")
    runtime = _exact_dict(value["runtime"], {"source", "sha256", "python_sha256"}, "runtime declaration is invalid")
    config = _exact_dict(value["config"], {"source", "sha256"}, "configuration declaration is invalid")
    credentials = _exact_dict(value["credentials"], {"memory", "memory_sha256", "introspection", "introspection_sha256"}, "credential references are invalid")
    authority = _exact_dict(value["authority"], {"manager", "manager_sha256", "manifest", "resource"}, "authority declaration is invalid")
    limits = _exact_dict(value["limits"], {"memory_max_bytes", "tasks_max"}, "service limits are invalid")
    if (type(limits["memory_max_bytes"]) is not int or not 64 * 1024**2 <= limits["memory_max_bytes"] <= 1024**3
            or type(limits["tasks_max"]) is not int or not 16 <= limits["tasks_max"] <= 256):
        raise LifecycleError("service limits are invalid")
    _artifact(runtime["source"], runtime["sha256"], 64 * 1024 * 1024)
    value["_python"] = _python(runtime["python_sha256"])
    config_raw = _file(config["source"], config["sha256"], 1024 * 1024)
    memory_raw = _file(credentials["memory"], credentials["memory_sha256"], 1024 * 1024)
    _file(credentials["introspection"], credentials["introspection_sha256"], 4096)
    _executable(authority["manager"], authority["manager_sha256"], 256 * 1024 * 1024)
    manifest = Path(authority["manifest"])
    if not manifest.is_absolute():
        raise LifecycleError("authority declaration is invalid")
    _protected(manifest, 1024 * 1024)
    try:
        connect = application_roles._read_declaration(str(manifest))
        root = Path(connect["config_root"])
        value["_connect_root"] = str(root)
        value["_authority_lock"] = str(root.parent / ("." + root.name + ".anvil-connect.lock"))
        value["_authority_state_directories"] = tuple(
            str(Path(connect[role]["state_directory"])) for role in ("authelia", "gateway"))
    except Exception:
        raise LifecycleError("authority declaration is invalid") from None
    if type(authority["resource"]) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", authority["resource"]) is None:
        raise LifecycleError("authority declaration is invalid")
    try:
        gateway = validate_config(strict_json(config_raw))
        receipt = strict_json(memory_raw)
    except Exception:
        raise LifecycleError("gateway inputs are invalid") from None
    deployment = value["deployment_id"]
    unit = f"anvil-native-memory-{deployment}.service"
    authority_output = f"/run/anvil-native-memory/{deployment}/authority/current.json"
    expected_secret = f"/run/credentials/{unit}/introspection-secret"
    if (gateway["deployment_id"] != deployment or gateway["credential_sha256"] != credentials["memory_sha256"]
            or gateway["auth"]["secret_file"] != expected_secret
            or gateway["auth"]["authority_file"] != authority_output
            or gateway["auth"]["source_resource"] != authority["resource"]
            or type(receipt) is not dict or receipt.get("schema") != "anvil-user-memory-key/v1"
            or receipt.get("principal") != gateway["expected_principal"]):
        raise LifecycleError("gateway inputs do not describe one principal deployment")
    return value, _sha(raw)


def _paths(value: dict, *, unit_root: Path = _UNIT_ROOT, state_root: Path = _STATE_ROOT) -> dict[str, Path]:
    deployment = value["deployment_id"]
    base = Path("/opt/anvil-native-memory") / deployment
    config = Path("/etc/anvil-native-memory") / deployment
    runtime = Path("/run/anvil-native-memory") / deployment
    service = f"anvil-native-memory-{deployment}.service"
    authority = f"anvil-native-memory-{deployment}-authority.service"
    timer = f"anvil-native-memory-{deployment}-authority.timer"
    return {
        "artifact_dir": base, "artifact": base / "gateway.pyz",
        "config_dir": config, "config": config / "native-memory.json",
        "memory_credential": config / "memory-key.credential",
        "introspection_credential": config / "introspection-secret.credential",
        "runtime_dir": runtime, "authority_dir": runtime / "authority",
        "authority": runtime / "authority/current.json",
        "state_dir": state_root / deployment, "receipt": state_root / deployment / "receipt.json",
        "service_unit": unit_root / service, "authority_unit": unit_root / authority,
        "timer_unit": unit_root / timer,
    }


def _quote(value: str) -> str:
    if not value or any(c in value for c in "\n\r\x00%$"):
        raise LifecycleError("unit argument is invalid")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_units(value: dict, paths: dict[str, Path]) -> dict[str, bytes]:
    dep = value["deployment_id"]
    identity, authority, limits = value["identity"], value["authority"], value["limits"]
    service_name = paths["service_unit"].name
    authority_name = paths["authority_unit"].name
    timer_name = paths["timer_unit"].name
    gateway_command = " ".join(_quote(item) for item in (
        value["_python"], "-I", "-B", str(paths["artifact"]), "--config", str(paths["config"])))
    authority_command = " ".join(_quote(item) for item in (
        authority["manager"], "native-authority", "--manifest", authority["manifest"],
        "--resource", authority["resource"], "--output", str(paths["authority"]),
        "--reader-gid", str(identity["gid"]), "--confirm"))
    common = (
        "NoNewPrivileges=true", "ProtectSystem=strict", "ProtectHome=true", "PrivateTmp=true",
        "PrivateDevices=true", "ProtectKernelTunables=true", "ProtectKernelModules=true",
        "ProtectControlGroups=true", "RestrictSUIDSGID=true", "LockPersonality=true",
        "RestrictRealtime=true", "CapabilityBoundingSet=", "AmbientCapabilities=",
        "SystemCallArchitectures=native", "UMask=0077",
    )
    gateway = "\n".join((
        "[Unit]", "Description=Anvil native Memory OIDC gateway", "After=network-online.target " + authority_name,
        "Wants=network-online.target " + timer_name, "", "[Service]", "Type=simple",
        "User=" + identity["user"], "Group=" + identity["group"],
        "ExecStart=" + gateway_command,
        "LoadCredential=memory-key:" + str(paths["memory_credential"]),
        "LoadCredential=introspection-secret:" + str(paths["introspection_credential"]),
        *common, "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "MemoryMax=" + str(limits["memory_max_bytes"]), "MemorySwapMax=0",
        "TasksMax=" + str(limits["tasks_max"]),
        "Restart=on-failure", "RestartSec=2s", "TimeoutStopSec=15s", "", "[Install]",
        "WantedBy=multi-user.target", "",
    )).encode()
    projector = "\n".join((
        "[Unit]", "Description=Anvil native Memory authority projection", "After=network-online.target",
        "Wants=network-online.target", "", "[Service]", "Type=oneshot", "User=root", "Group=root",
        "ExecStart=" + authority_command,
        "NoNewPrivileges=true", "ProtectSystem=strict", "ProtectHome=true", "PrivateTmp=true",
        "PrivateDevices=true", "ProtectKernelTunables=true", "ProtectKernelModules=true",
        "ProtectControlGroups=true", "RestrictSUIDSGID=true", "LockPersonality=true",
        # The maintained projector exports identifiers and inspects grants as
        # the declared role users, then removes their private temporary files.
        "RestrictRealtime=true",
        "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER CAP_SETUID CAP_SETGID CAP_KILL",
        "AmbientCapabilities=",
        "SystemCallArchitectures=native", "UMask=0077",
        "RuntimeDirectory=anvil-native-memory/" + dep + "/authority", "RuntimeDirectoryMode=0755",
        "RuntimeDirectoryPreserve=yes", "ReadWritePaths=" + " ".join(
            _quote(item) for item in (str(paths["authority_dir"]), value["_authority_lock"],
                                     *value["_authority_state_directories"])),
        "TimeoutStartSec=25s", "",
    )).encode()
    timer = "\n".join((
        "[Unit]", "Description=Anvil native Memory authority refresh schedule", "", "[Timer]",
        "OnBootSec=1s", "OnUnitInactiveSec=10s", "AccuracySec=1s", "Unit=" + authority_name,
        "", "[Install]", "WantedBy=timers.target", "",
    )).encode()
    return {service_name: gateway, authority_name: projector, timer_name: timer}


def _run(runner, argv: tuple[str, ...], timeout: float = 30) -> manage.RunResult:
    result = manage._run(runner, argv, timeout)
    return result


def _must(result: manage.RunResult, message: str) -> None:
    if result.returncode != 0:
        raise LifecycleError(message, may_have_executed=True)


def _state(runner, name: str) -> dict[str, str]:
    names = ("LoadState", "FragmentPath", "DropInPaths", "ActiveState", "SubState", "UnitFileState")
    # MainPID is a service property; real systemd timers never report it.
    if name.endswith(".service"):
        names += ("MainPID",)
    elif not name.endswith(".timer"):
        raise LifecycleError("managed unit kind is invalid")
    result = _run(runner, (_SYSTEMCTL, "show", "--property=" + ",".join(names), name), 10)
    fields = dict(line.split("=", 1) for line in result.stdout.decode("utf-8", "replace").splitlines() if "=" in line)
    if set(fields) != set(names) or result.returncode != 0 and fields.get("LoadState") != "not-found":
        raise LifecycleError("managed unit state is invalid")
    return fields


def _gateway_effective(runner, value: dict, name: str) -> bool:
    names = ("User", "Group", "NoNewPrivileges", "ProtectSystem", "ProtectHome", "PrivateTmp",
             "PrivateDevices", "ProtectKernelTunables", "ProtectKernelModules", "ProtectControlGroups",
             "RestrictSUIDSGID", "LockPersonality", "RestrictRealtime", "CapabilityBoundingSet",
             "AmbientCapabilities", "MemoryMax", "MemorySwapMax", "TasksMax")
    result = _run(runner, (_SYSTEMCTL, "show", "--property=" + ",".join(names), name), 10)
    if result.returncode != 0:
        return False
    fields = dict(line.split("=", 1) for line in result.stdout.decode("utf-8", "replace").splitlines() if "=" in line)
    expected = {
        "User": value["identity"]["user"], "Group": value["identity"]["group"],
        "NoNewPrivileges": "yes", "ProtectSystem": "strict", "ProtectHome": "yes",
        "PrivateTmp": "yes", "PrivateDevices": "yes", "ProtectKernelTunables": "yes",
        "ProtectKernelModules": "yes", "ProtectControlGroups": "yes", "RestrictSUIDSGID": "yes",
        "LockPersonality": "yes", "RestrictRealtime": "yes", "CapabilityBoundingSet": "",
        "AmbientCapabilities": "", "MemoryMax": str(value["limits"]["memory_max_bytes"]),
        "MemorySwapMax": "0",
        "TasksMax": str(value["limits"]["tasks_max"]),
    }
    return fields == expected


def _identity(value: dict) -> tuple[bool, bool]:
    expected = value["identity"]
    try:
        group = grp.getgrnam(expected["group"])
    except KeyError:
        group = None
    try:
        user = pwd.getpwnam(expected["user"])
    except KeyError:
        user = None
    if group is not None and (group.gr_gid != expected["gid"] or group.gr_mem):
        raise LifecycleError("service group identity is occupied or has supplementary members")
    if user is not None and (user.pw_uid != expected["uid"] or user.pw_gid != expected["gid"]
                             or user.pw_dir != "/nonexistent" or user.pw_shell not in {"/usr/sbin/nologin", "/sbin/nologin"}):
        raise LifecycleError("service user identity is occupied")
    for entry in pwd.getpwall():
        if entry.pw_uid == expected["uid"] and (user is None or entry.pw_name != user.pw_name):
            raise LifecycleError("service uid is occupied")
        if entry.pw_gid == expected["gid"] and entry.pw_name != expected["user"]:
            raise LifecycleError("service group is a primary group for another user")
    for entry in grp.getgrall():
        if entry.gr_gid == expected["gid"] and (group is None or entry.gr_name != group.gr_name):
            raise LifecycleError("service gid is occupied")
    return user is not None, group is not None


@contextmanager
def _lock(path: Path = _LOCK):
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise LifecycleError("native Memory lifecycle lock is unsafe")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _write(path: Path, raw: bytes, mode: int, gid: int = 0) -> bool:
    if path.exists() or path.is_symlink():
        current = manage._read_regular(path, len(raw) + 1)
        if current != raw:
            raise LifecycleError("managed deployment file differs from its declaration")
        return False
    manage._write_atomic(path, raw, mode)
    os.chown(path, 0, gid)
    os.chmod(path, mode)
    return True


def _owned_digest(path: Path, digest: str, *, mode: int, gid: int, maximum: int) -> bool:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_gid != gid
                    or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != mode
                    or not 1 <= before.st_size <= maximum):
                return False
            chunks, size = [], 0
            while size <= maximum:
                part = os.read(fd, min(65536, maximum + 1 - size))
                if not part:
                    break
                chunks.append(part); size += len(part)
            after = os.fstat(fd)
        finally:
            os.close(fd)
    except OSError:
        return False
    def identity(item):
        return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns,
                item.st_uid, item.st_gid, item.st_mode, item.st_nlink)
    return size <= maximum and identity(before) == identity(after) and _sha(b"".join(chunks)) == digest


def _owned_regular(path: Path, *, mode: int, gid: int, maximum: int) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == gid
            and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == mode
            and 1 <= info.st_size <= maximum)


def _directory(path: Path, *, mode: int, gid: int, names: set[str], recovery: bool) -> None:
    try:
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != gid
                or stat.S_IMODE(info.st_mode) != mode):
            raise LifecycleError("managed deployment directory changed before removal")
        members = {item.name for item in path.iterdir()}
    except FileNotFoundError:
        if recovery:
            return
        raise LifecycleError("managed deployment directory is missing") from None
    except OSError:
        raise LifecycleError("managed deployment directory changed before removal") from None
    if members - names or (not recovery and members != names):
        raise LifecycleError("managed deployment directory membership changed before removal")


def _preflight_remove(value: dict, paths: dict[str, Path], *, recovery: bool) -> None:
    identity = value["identity"]
    files = (
        (paths["artifact"], value["runtime"]["sha256"], 0o444, 0, 64 * 1024 * 1024),
        (paths["config"], value["config"]["sha256"], 0o640, identity["gid"], 1024 * 1024),
        (paths["memory_credential"], value["credentials"]["memory_sha256"], 0o400, 0, 1024 * 1024),
        (paths["introspection_credential"], value["credentials"]["introspection_sha256"], 0o400, 0, 4096),
    )
    for path, digest, mode, gid, maximum in files:
        if not os.path.lexists(path):
            if recovery:
                continue
            raise LifecycleError("managed deployment file is missing")
        if not _owned_digest(path, digest, mode=mode, gid=gid, maximum=maximum):
            raise LifecycleError("managed deployment file changed before removal")
    _directory(paths["artifact_dir"], mode=0o755, gid=0,
               names={paths["artifact"].name}, recovery=recovery)
    _directory(paths["config_dir"], mode=0o750, gid=identity["gid"], names={
        paths["config"].name, paths["memory_credential"].name,
        paths["introspection_credential"].name}, recovery=recovery)
    _directory(paths["state_dir"], mode=0o700, gid=0,
               names={paths["receipt"].name}, recovery=False)
    _directory(paths["runtime_dir"], mode=0o755, gid=0,
               names={paths["authority_dir"].name}, recovery=recovery)
    _directory(paths["authority_dir"], mode=0o755, gid=0,
               names={paths["authority"].name}, recovery=True)
    if os.path.lexists(paths["authority"]) and not _owned_regular(
            paths["authority"], mode=0o640, gid=identity["gid"], maximum=1024 * 1024):
        raise LifecycleError("authority output changed before removal")


def _remove_authority_runtime(value: dict, paths: dict[str, Path]) -> None:
    _directory(paths["runtime_dir"], mode=0o755, gid=0,
               names={paths["authority_dir"].name}, recovery=True)
    _directory(paths["authority_dir"], mode=0o755, gid=0,
               names={paths["authority"].name}, recovery=True)
    if os.path.lexists(paths["authority"]):
        if not _owned_regular(paths["authority"], mode=0o640, gid=value["identity"]["gid"], maximum=1024 * 1024):
            raise LifecycleError("authority output changed before removal", may_have_executed=True)
        paths["authority"].unlink()
    for directory in (paths["authority_dir"], paths["runtime_dir"]):
        if directory.exists():
            directory.rmdir()


def _finish_receipt(paths: dict[str, Path], digest: str, phase: str, installed_at: int) -> None:
    _directory(paths["state_dir"], mode=0o700, gid=0,
               names={paths["receipt"].name}, recovery=False)
    paths["receipt"].unlink()
    try:
        paths["state_dir"].rmdir()
    except OSError:
        if paths["state_dir"].is_dir() and not os.path.lexists(paths["receipt"]):
            _save_receipt(paths["receipt"], digest, phase, installed_at)
        raise LifecycleError("managed deployment receipt cleanup failed", may_have_executed=True) from None


def _mkdir(path: Path, mode: int, gid: int = 0) -> bool:
    created = False
    if not path.exists():
        path.mkdir(mode=mode)
        os.chown(path, 0, gid)
        os.chmod(path, mode)
        created = True
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != gid or stat.S_IMODE(info.st_mode) != mode:
        raise LifecycleError("managed deployment directory is unsafe")
    return created


def _receipt(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        value = strict_json(_protected(path, 64 * 1024))
    except Exception:
        raise LifecycleError("managed deployment receipt is invalid") from None
    if type(value) is not dict or set(value) != {"schema", "declaration_sha256", "phase", "installed_at"} or value["schema"] != SCHEMA:
        raise LifecycleError("managed deployment receipt is invalid")
    return value


def _save_receipt(path: Path, digest: str, phase: str, installed_at: int) -> None:
    raw = (json.dumps({"schema": SCHEMA, "declaration_sha256": digest, "phase": phase,
                       "installed_at": installed_at}, sort_keys=True) + "\n").encode()
    manage._write_atomic(path, raw, 0o600)


def _status(value: dict, digest: str, runner, paths: dict[str, Path]) -> dict:
    units = render_units(value, paths)
    installed = True
    details = {}
    for name, raw in units.items():
        path = paths["service_unit"] if name == paths["service_unit"].name else paths["authority_unit"] if name == paths["authority_unit"].name else paths["timer_unit"]
        current = manage._read_unit(path)
        state = _state(runner, name)
        exact = current == raw and state["FragmentPath"] == str(path) and state["DropInPaths"] == ""
        if name == paths["service_unit"].name:
            operational = (state["ActiveState"], state["SubState"], state["UnitFileState"]) == (
                "active", "running", "enabled")
        elif name == paths["timer_unit"].name:
            operational = state["ActiveState"] == "active" and state["UnitFileState"] == "enabled"
        else:
            operational = True
        installed &= exact and state["LoadState"] == "loaded" and operational
        details[name] = {"exact": exact, "operational": operational, "active": state["ActiveState"],
                         "substate": state["SubState"], "enabled": state["UnitFileState"]}
    gateway_effective = _gateway_effective(runner, value, paths["service_unit"].name)
    details[paths["service_unit"].name]["effective"] = gateway_effective
    installed &= gateway_effective
    receipt = _receipt(paths["receipt"])
    receipt_ready = receipt is not None and receipt.get("declaration_sha256") == digest and receipt.get("phase") == "ready"
    identity_ready = _identity(value) == (True, True)
    files_ready = all((
        _owned_digest(paths["artifact"], value["runtime"]["sha256"], mode=0o444, gid=0, maximum=64 * 1024 * 1024),
        _owned_digest(paths["config"], value["config"]["sha256"], mode=0o640, gid=value["identity"]["gid"], maximum=1024 * 1024),
        _owned_digest(paths["memory_credential"], value["credentials"]["memory_sha256"], mode=0o400, gid=0, maximum=1024 * 1024),
        _owned_digest(paths["introspection_credential"], value["credentials"]["introspection_sha256"], mode=0o400, gid=0, maximum=4096),
    ))
    authority_ready = _owned_regular(
        paths["authority"], mode=0o640, gid=value["identity"]["gid"], maximum=1024 * 1024)
    installed &= receipt_ready and identity_ready and files_ready and authority_ready
    return {"schema": RESULT_SCHEMA, "action": "status", "applied": False, "deployment_id": value["deployment_id"],
            "installed": installed, "ready": installed, "receipt_ready": receipt_ready,
            "identity": identity_ready, "files": files_ready, "authority": authority_ready, "units": details}


def _unit_path(paths: dict[str, Path], name: str) -> Path:
    for key in ("service_unit", "authority_unit", "timer_unit"):
        if paths[key].name == name:
            return paths[key]
    raise AssertionError("unknown managed unit")


def _preflight_install(value: dict, runner, paths: dict[str, Path], units: dict[str, bytes], receipt: dict | None) -> tuple[bool, bool]:
    had_user, had_group = _identity(value)
    if receipt is None and (had_user or had_group):
        raise LifecycleError("service identity exists without an owning receipt")
    for name, raw in units.items():
        target = _unit_path(paths, name)
        current = manage._read_unit(target)
        state = _state(runner, name)
        if state["DropInPaths"]:
            raise LifecycleError("managed unit has unowned drop-ins")
        if receipt is None:
            if current is not None or state["LoadState"] != "not-found" or state["FragmentPath"]:
                raise LifecycleError("managed unit already exists without an owning receipt")
        elif (current not in {None, raw}
              or current is None and state["LoadState"] != "not-found"
              or current is not None and (state["LoadState"] != "loaded" or state["FragmentPath"] != str(target))):
            raise LifecycleError("receipt-owned unit differs from its declaration")
    for target in (paths["artifact"], paths["config"], paths["memory_credential"], paths["introspection_credential"]):
        if receipt is None and os.path.lexists(target):
            raise LifecycleError("deployment destination already exists without an owning receipt")
    if receipt is None and any(os.path.lexists(paths[key]) for key in ("artifact_dir", "config_dir", "runtime_dir")):
        raise LifecycleError("deployment directory already exists without an owning receipt")
    if receipt is None and os.path.lexists(paths["state_dir"]):
        raise LifecycleError("deployment state already exists without an owning receipt")
    return had_user, had_group


def _install(value: dict, digest: str, runner, paths: dict[str, Path], apply: bool,
             declaration: str | None = None) -> dict:
    units = render_units(value, paths)
    preview = {"schema": RESULT_SCHEMA, "action": "install", "applied": False,
               "deployment_id": value["deployment_id"], "declaration_sha256": digest, "units": sorted(units)}
    if not apply:
        return preview
    identity = value["identity"]
    installed_at = int(time.time())
    with _lock():
        if declaration is not None:
            fresh, fresh_digest = load_declaration(declaration)
            if fresh_digest != digest or fresh != value:
                raise LifecycleError("native Memory declaration changed after preview")
        existing = _receipt(paths["receipt"])
        if existing is not None and (existing["declaration_sha256"] != digest or existing["phase"] not in {"installing", "ready"}):
            raise LifecycleError("another native Memory deployment owns this identity")
        if existing is not None and existing["phase"] == "installing":
            raise LifecycleError("interrupted native Memory installation requires managed removal")
        if existing is not None and existing["phase"] == "ready":
            current = _status(value, digest, runner, paths)
            if not current["installed"]:
                raise LifecycleError("installed native Memory service has drifted")
            return {**preview, "already_installed": True}
        had_user, had_group = _preflight_install(value, runner, paths, units, existing)
        created_user = created_group = False
        created_files: list[Path] = []
        created_dirs: list[Path] = []
        timer_enabled = gateway_enabled = authority_started = False
        state_dir_created = False
        if existing is None:
            _mkdir(paths["state_dir"].parent, 0o700)
            state_dir_created = _mkdir(paths["state_dir"], 0o700)
            _save_receipt(paths["receipt"], digest, "installing", installed_at)
        else:
            installed_at = existing["installed_at"]
        try:
            # Ensure the exact maintained Connect lock exists, then release it
            # before starting the projector, which acquires the same lock.
            with manage._deployment_lock(Path(value["_connect_root"])):
                pass
            if not had_group:
                _must(_run(runner, ("/usr/sbin/groupadd", "--system", "--gid", str(identity["gid"]), identity["group"])), "service group creation failed")
                created_group = True
            if not had_user:
                _must(_run(runner, ("/usr/sbin/useradd", "--system", "--uid", str(identity["uid"]), "--gid", identity["group"],
                                            "--home-dir", "/nonexistent", "--shell", "/usr/sbin/nologin", "--no-create-home", identity["user"])), "service user creation failed")
                created_user = True
            if _identity(value) != (True, True):
                raise LifecycleError("service identity creation did not match its declaration", may_have_executed=True)
            for directory, mode, gid in ((paths["artifact_dir"].parent, 0o755, 0),
                                         (paths["artifact_dir"], 0o755, 0),
                                         (paths["config_dir"].parent, 0o755, 0),
                                         (paths["config_dir"], 0o750, identity["gid"])):
                if _mkdir(directory, mode, gid):
                    created_dirs.append(directory)
            if _write(paths["artifact"], _artifact(value["runtime"]["source"], value["runtime"]["sha256"], 64 * 1024 * 1024), 0o444):
                created_files.append(paths["artifact"])
            if _write(paths["config"], _file(value["config"]["source"], value["config"]["sha256"], 1024 * 1024), 0o640, identity["gid"]):
                created_files.append(paths["config"])
            if _write(paths["memory_credential"], _file(value["credentials"]["memory"], value["credentials"]["memory_sha256"], 1024 * 1024), 0o400):
                created_files.append(paths["memory_credential"])
            if _write(paths["introspection_credential"], _file(value["credentials"]["introspection"], value["credentials"]["introspection_sha256"], 4096), 0o400):
                created_files.append(paths["introspection_credential"])
            for name, raw in units.items():
                target = _unit_path(paths, name)
                current = manage._read_unit(target)
                if current is not None and current != raw:
                    raise LifecycleError("managed unit is owned by another deployment")
                if _write(target, raw, 0o644):
                    created_files.append(target)
            _must(_run(runner, (_SYSTEMCTL, "daemon-reload")), "systemd did not accept native Memory units")
            authority_started = True
            _must(_run(runner, (_SYSTEMCTL, "start", paths["authority_unit"].name)), "initial authority projection failed")
            if not _owned_regular(paths["authority"], mode=0o640, gid=identity["gid"], maximum=1024 * 1024):
                raise LifecycleError("initial authority projection did not produce its owned output", may_have_executed=True)
            timer_enabled = True
            _must(_run(runner, (_SYSTEMCTL, "enable", "--now", paths["timer_unit"].name)), "authority timer failed to start")
            gateway_enabled = True
            _must(_run(runner, (_SYSTEMCTL, "enable", "--now", paths["service_unit"].name)), "native Memory gateway failed to start")
            gateway = _state(runner, paths["service_unit"].name)
            timer = _state(runner, paths["timer_unit"].name)
            if (gateway["ActiveState"], gateway["SubState"], gateway["UnitFileState"]) != ("active", "running", "enabled"):
                raise LifecycleError("native Memory gateway did not become active", may_have_executed=True)
            if timer["ActiveState"] != "active" or timer["UnitFileState"] != "enabled":
                raise LifecycleError("native Memory authority timer did not become active", may_have_executed=True)
            if not _gateway_effective(runner, value, paths["service_unit"].name):
                raise LifecycleError("native Memory gateway effective policy differs", may_have_executed=True)
            _save_receipt(paths["receipt"], digest, "ready", installed_at)
        except BaseException as exc:
            try:
                if gateway_enabled:
                    _must(_run(runner, (_SYSTEMCTL, "disable", "--now", paths["service_unit"].name)),
                          "gateway rollback shutdown failed")
                if timer_enabled:
                    _must(_run(runner, (_SYSTEMCTL, "disable", "--now", paths["timer_unit"].name)),
                          "timer rollback shutdown failed")
                if authority_started:
                    _must(_run(runner, (_SYSTEMCTL, "stop", paths["authority_unit"].name)),
                          "authority rollback shutdown failed")
                for key in ("service_unit", "timer_unit", "authority_unit"):
                    if _unit_path(paths, paths[key].name).exists():
                        state = _state(runner, paths[key].name)
                        if (state["ActiveState"] not in {"inactive", "failed"}
                                or key != "authority_unit" and state["UnitFileState"] not in {"disabled", "static"}):
                            raise LifecycleError("native Memory rollback did not quiesce its units", may_have_executed=True)
                _remove_authority_runtime(value, paths)
                for path in reversed(created_files):
                    if path.exists() and not path.is_symlink():
                        path.unlink()
                _must(_run(runner, (_SYSTEMCTL, "daemon-reload")), "native Memory rollback reload failed")
                for path in reversed(created_dirs):
                    if path.exists():
                        path.rmdir()
                if created_user:
                    if not _no_processes(identity["uid"]):
                        raise LifecycleError("service uid still owns processes during rollback", may_have_executed=True)
                    _must(_run(runner, ("/usr/sbin/userdel", identity["user"])), "service user rollback failed")
                if created_group and _identity(value)[1]:
                    _must(_run(runner, ("/usr/sbin/groupdel", identity["group"])), "service group rollback failed")
                if state_dir_created and paths["receipt"].exists():
                    _finish_receipt(paths, digest, "installing", installed_at)
            except BaseException as rollback:
                raise LifecycleError("native Memory installation rollback failed", may_have_executed=True) from rollback
            if isinstance(exc, LifecycleError):
                raise
            raise LifecycleError("native Memory installation failed", may_have_executed=True) from exc
    return {**preview, "applied": True}


def _no_processes(uid: int) -> bool:
    root = _PROC_ROOT
    if not root.is_dir():
        return False
    for child in root.iterdir():
        if not child.name.isdigit():
            continue
        try:
            fields = (child / "status").read_text(errors="replace").splitlines()
            row = next(line for line in fields if line.startswith("Uid:"))
            if uid in [int(item) for item in row.split()[1:]]:
                return False
        except FileNotFoundError:
            # Unrelated processes can exit during the scan. Missing status on
            # a still-present PID must continue to fail closed.
            try:
                child.stat()
            except FileNotFoundError:
                continue
            except OSError:
                return False
            return False
        except (OSError, StopIteration, ValueError):
            return False
    return True


def _remove(value: dict, digest: str, runner, paths: dict[str, Path], apply: bool,
            declaration: str | None = None) -> dict:
    preview = {"schema": RESULT_SCHEMA, "action": "remove", "applied": False,
               "deployment_id": value["deployment_id"], "declaration_sha256": digest}
    if not apply:
        return preview
    units = render_units(value, paths)
    with _lock():
        if declaration is not None:
            fresh, fresh_digest = load_declaration(declaration)
            if fresh_digest != digest or fresh != value:
                raise LifecycleError("native Memory declaration changed after preview")
        receipt = _receipt(paths["receipt"])
        if receipt is None or receipt["declaration_sha256"] != digest or receipt["phase"] not in {"installing", "ready", "removing"}:
            raise LifecycleError("native Memory deployment receipt does not authorize removal")
        recovery = receipt["phase"] in {"installing", "removing"}
        for name, raw in units.items():
            target = _unit_path(paths, name)
            current = manage._read_unit(target)
            if current != raw and not (recovery and current is None):
                raise LifecycleError("managed unit changed before removal")
            state = _state(runner, name)
            if state["DropInPaths"]:
                raise LifecycleError("managed unit has unowned drop-ins")
            cached_owned = (recovery and current is None and state["LoadState"] == "loaded"
                            and state["FragmentPath"] == str(target)
                            and state["ActiveState"] in {"inactive", "failed"}
                            and (name.endswith(".timer") or state["MainPID"] == "0")
                            and state["UnitFileState"] in {"disabled", "static"})
            absent = current is None and state["LoadState"] == "not-found" and not state["FragmentPath"]
            if (current is None and not (absent or cached_owned)
                    or current is not None and (state["LoadState"] != "loaded" or state["FragmentPath"] != str(target))):
                raise LifecycleError("managed unit state changed before removal")
        had_user, had_group = _identity(value)
        _preflight_remove(value, paths, recovery=recovery)
        _save_receipt(paths["receipt"], digest, "removing", receipt["installed_at"])
        for key in ("service_unit", "timer_unit"):
            if paths[key].exists():
                _must(_run(runner, (_SYSTEMCTL, "disable", "--now", paths[key].name)), "native Memory unit shutdown failed")
        if paths["authority_unit"].exists():
            _must(_run(runner, (_SYSTEMCTL, "stop", paths["authority_unit"].name)), "authority projection shutdown failed")
        for key in ("service_unit", "timer_unit", "authority_unit"):
            if not paths[key].exists():
                continue
            state = _state(runner, paths[key].name)
            if (state["ActiveState"] not in {"inactive", "failed"}
                    or key != "authority_unit" and state["UnitFileState"] not in {"disabled", "static"}):
                raise LifecycleError("native Memory removal did not quiesce its units", may_have_executed=True)
        for name, raw in units.items():
            target = _unit_path(paths, name)
            current = manage._read_unit(target)
            if current is None and recovery:
                continue
            if current != raw:
                raise LifecycleError("managed unit changed before removal", may_have_executed=True)
            target.unlink()
        _must(_run(runner, (_SYSTEMCTL, "daemon-reload")), "systemd removal reload failed")
        if not _no_processes(value["identity"]["uid"]):
            raise LifecycleError("service uid still owns processes", may_have_executed=True)
        for path, expected in ((paths["config"], value["config"]["sha256"]),
                               (paths["artifact"], value["runtime"]["sha256"]),
                               (paths["memory_credential"], value["credentials"]["memory_sha256"]),
                               (paths["introspection_credential"], value["credentials"]["introspection_sha256"])):
            raw = manage._read_regular(path, 64 * 1024 * 1024)
            if raw is None and recovery:
                continue
            if raw is None or _sha(raw) != expected:
                raise LifecycleError("managed deployment file changed before removal", may_have_executed=True)
            path.unlink()
        _remove_authority_runtime(value, paths)
        for directory in (paths["config_dir"], paths["artifact_dir"]):
            if directory.exists():
                directory.rmdir()
        if had_user:
            _must(_run(runner, ("/usr/sbin/userdel", value["identity"]["user"])), "service user removal failed")
        if had_group and _identity(value)[1]:
            _must(_run(runner, ("/usr/sbin/groupdel", value["identity"]["group"])), "service group removal failed")
        _finish_receipt(paths, digest, "removing", receipt["installed_at"])
    return {**preview, "applied": True}


def operate(action: str, declaration: str, *, apply: bool = False, runner=None,
            unit_root: str | Path = _UNIT_ROOT, state_root: str | Path = _STATE_ROOT) -> dict:
    if os.name != "posix" or os.geteuid() != 0:
        raise LifecycleError("native Memory service lifecycle requires root on Linux")
    if action not in {"install", "status", "remove"}:
        raise LifecycleError("native Memory service action is invalid")
    value, digest = load_declaration(declaration)
    paths = _paths(value, unit_root=Path(unit_root), state_root=Path(state_root))
    if action == "status":
        return _status(value, digest, runner, paths)
    if action == "install":
        return _install(value, digest, runner, paths, apply, declaration)
    return _remove(value, digest, runner, paths, apply, declaration)
