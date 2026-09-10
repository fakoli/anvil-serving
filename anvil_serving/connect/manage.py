"""Bounded, ownership-preserving lifecycle helpers for Anvil Connect.

This module deliberately has no command-line parser. The command family owns
argument parsing and confirmation; these functions default to a preview and
return structured data suitable for one CLI envelope.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import resources
import json
import os
import platform
from pathlib import Path
import re
import selectors
import signal
import secrets
import stat
import subprocess
import tempfile
import time
from typing import Any, Callable, Iterator, Literal
from contextlib import contextmanager

from .config import read_manifest, require_isolated, role_identity
from .render import plan, plan_for_inspection, render as render_config, stage

# The package and command help remain portable.  The actual ownership model
# below deliberately depends on Linux uid/gid, openat-style flags, flock and
# systemd, so bind those imports only on its supported platform.
if os.name == "posix" and platform.system() == "Linux":
    import fcntl
    import grp
    import pwd
else:  # pragma: no cover - imported only to keep Windows help/wheel safe.
    fcntl = grp = pwd = None

_SYSTEMCTL = "/usr/bin/systemctl"
_JOURNALCTL = "/usr/bin/journalctl"
_MAX_OUTPUT = 64 * 1024
_MAX_BINARY = 512 * 1024 * 1024
# Generated services have a 20-second hard stop deadline. A systemctl restart
# must also wait for the replacement process to launch, so its command bound
# carries a fixed margin instead of racing TimeoutStopSec. Caddy's finite
# 15-second HTTP grace period keeps long-lived WebSocket drains below that
# supervisor deadline.
_SYSTEMD_TIMEOUT = 30.0
_VALIDATE_TIMEOUT = 10.0
_ID = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")


class ManageError(RuntimeError):
    """A lifecycle operation failed; configuration parsing remains ManifestError."""

    def __init__(self, message: str, *, may_have_executed: bool = False) -> None:
        super().__init__(message)
        self.may_have_executed = may_have_executed


class UnsupportedPlatformError(ManageError):
    """Connect native lifecycle is intentionally Linux amd64 only."""


def supported_platform() -> bool:
    return (
        os.name == "posix"
        and platform.system() == "Linux"
        and platform.machine().lower() in {"x86_64", "amd64"}
    )


def _require_supported_platform() -> None:
    if not supported_platform():
        raise UnsupportedPlatformError("Connect lifecycle requires Linux amd64")


def _executed_error(exc: Exception) -> ManageError:
    if isinstance(exc, ManageError) and exc.may_have_executed:
        return exc
    return ManageError(str(exc) or "managed operation failed", may_have_executed=True)


@dataclass(frozen=True)
class Target:
    """One exact managed role; arbitrary unit names are never accepted."""

    kind: Literal["gateway", "connector", "client"]
    name: str | None = None

    @classmethod
    def parse(cls, value: str) -> "Target":
        if value == "gateway":
            return cls("gateway")
        kind, separator, name = value.partition(":")
        if separator != ":" or not name or ":" in name or kind not in {"connector", "client"}:
            raise ManageError("target must be gateway, connector:<id>, or client:<id>")
        return cls(kind, name)  # declaration matching validates the identifier.

    def text(self) -> str:
        return self.kind if self.name is None else self.kind + ":" + self.name


@dataclass(frozen=True)
class ServiceIdentity:
    uid: int
    gid: int


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""
    output_limited: bool = False


Runner = Callable[[tuple[str, ...], float, ServiceIdentity | None], RunResult]


def _bounded_run(argv: tuple[str, ...], timeout: float, identity: ServiceIdentity | None = None) -> RunResult:
    """Run a fixed argv in one killable process group with bounded pipe drain."""
    if not argv or timeout <= 0:
        raise ManageError("invalid managed subprocess")
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "close_fds": True,
        "start_new_session": True,
        "env": {"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    }
    if identity is not None:
        # Popen performs these after fork and before exec without an arbitrary
        # Python preexec callback. Empty supplementary groups matter for the
        # owner-only Unix admin socket and private state directory.
        options.update({"user": identity.uid, "group": identity.gid, "extra_groups": []})
    try:
        process = subprocess.Popen(argv, **options)
    except OSError as exc:
        raise ManageError("managed subprocess unavailable") from exc
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    values = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    drain_deadline: float | None = None
    killed = False
    limited = False

    def kill_group() -> None:
        nonlocal killed, drain_deadline
        if killed:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        killed = True
        drain_deadline = time.monotonic() + 0.25

    try:
        while selector.get_map():
            now = time.monotonic()
            if now >= deadline or limited:
                kill_group()
            if drain_deadline is not None and now >= drain_deadline:
                break
            wait = 0.05
            if drain_deadline is None:
                wait = min(wait, max(0.0, deadline - now))
            else:
                wait = min(wait, max(0.0, drain_deadline - now))
            for key, _ in selector.select(wait):
                try:
                    chunk = os.read(key.fileobj.fileno(), 8192)
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                value = values[key.data]
                available = _MAX_OUTPUT - len(value)
                if len(chunk) > available:
                    value.extend(chunk[:max(available, 0)])
                    limited = True
                else:
                    value.extend(chunk)
            # A child can exit while a descendant retains stdout/stderr. The
            # original deadline still kills that descendant process group.
        if process.poll() is None and not killed:
            if limited:
                kill_group()
            else:
                # A short-lived child may close both output descriptors before
                # the kernel records its exit. Draining those descriptors is
                # not a completion signal: preserve the original command
                # deadline before terminating its process group.
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    try:
                        process.wait(timeout=remaining)
                    except subprocess.TimeoutExpired:
                        kill_group()
                else:
                    kill_group()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            kill_group()
            process.wait(timeout=1)
    except (OSError, subprocess.SubprocessError) as exc:
        kill_group()
        try:
            process.wait(timeout=1)
        except subprocess.SubprocessError:
            pass
        raise ManageError("managed subprocess failed") from exc
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    if killed:
        raise ManageError("managed subprocess exceeded its bound")
    return RunResult(process.returncode, bytes(values["stdout"]), bytes(values["stderr"]), limited)


def _run(runner: Runner | None, argv: tuple[str, ...], timeout: float, identity: ServiceIdentity | None = None) -> RunResult:
    result = (runner or _bounded_run)(argv, timeout, identity)
    if not isinstance(result, RunResult):
        raise ManageError("managed subprocess returned an invalid result")
    if result.output_limited:
        raise ManageError("managed subprocess exceeded its output bound")
    return result


def _fail(result: RunResult, message: str) -> None:
    if result.returncode != 0:
        # Component output can contain credentials, URLs, or application data.
        raise ManageError(message)


def _action(runner: Runner | None, argv: tuple[str, ...], timeout: float, message: str, identity: ServiceIdentity | None = None) -> None:
    """Invoke a mutating command; any failure may follow a partial execution."""
    try:
        _fail(_run(runner, argv, timeout, identity), message)
    except Exception as exc:
        raise _executed_error(exc) from exc


def _digest(path: Path) -> str:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ManageError("managed executable is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size < 1 or info.st_size > _MAX_BINARY:
            raise ManageError("managed executable is not a bounded regular file")
        if info.st_mode & 0o6022 or not info.st_mode & 0o111 or info.st_uid not in {0, os.geteuid()}:
            raise ManageError("managed executable has unsafe ownership or mode")
        digest = hashlib.sha256()
        while True:
            part = os.read(descriptor, 65536)
            if not part:
                break
            digest.update(part)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _component_lock() -> dict[str, str]:
    try:
        raw = json.loads(resources.files("anvil_serving.connect").joinpath("components.lock.json").read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema") != "anvil-connect.edge-tools/v1" or raw.get("platform") != "linux-amd64":
            raise ValueError
        components = raw["components"]
        if not isinstance(components, list):
            raise ValueError
        result = {}
        for item in components:
            if not isinstance(item, dict) or item.get("name") not in {"caddy", "authelia"}:
                raise ValueError
            digest = item.get("binary_sha256")
            if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError
            result[item["name"]] = digest
        if set(result) != {"caddy", "authelia"}:
            raise ValueError
        return result
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ManageError("component lock is invalid") from exc


def _selected_targets(data: dict[str, Any], target: Target | tuple[Target, ...] | None) -> tuple[Target, ...]:
    """Resolve one closed target, a closed nonempty target set, or all roles."""
    declared = _targets(data, None)
    if target is None:
        return declared
    if isinstance(target, Target):
        if target not in declared:
            raise ManageError("target is not declared")
        return (target,)
    if not isinstance(target, tuple) or not target or any(not isinstance(item, Target) for item in target):
        raise ManageError("selected target set is invalid")
    if len(set(target)) != len(target) or any(item not in declared for item in target):
        raise ManageError("selected target set is invalid")
    # Canonical declaration order preserves Authelia -> Caddy -> gateway before
    # any selected connector/client, regardless of CLI input order.
    return tuple(item for item in declared if item in target)


def _verified_binaries(data: dict[str, Any], target: Target | tuple[Target, ...] | None) -> dict[str, str]:
    result = {"native": _digest(Path(data["binary"]))}
    if any(item.kind == "gateway" for item in _selected_targets(data, target)):
        locked = _component_lock()
        for name in ("caddy", "authelia"):
            value = _digest(Path(data["components"][name]))
            if value != locked[name]:
                raise ManageError("managed component digest does not match its lock")
            result[name] = value
    return result


def _targets(data: dict[str, Any], target: Target | None) -> tuple[Target, ...]:
    all_targets = [Target("gateway")]
    all_targets.extend(Target("connector", item["id"]) for item in data["connectors"])
    all_targets.extend(Target("client", item["rule"]["id"]) for item in data["clients"])
    if target is None:
        return tuple(all_targets)
    if target not in all_targets:
        raise ManageError("target is not declared")
    return (target,)


def _target_units(targets: tuple[Target, ...], *, start: bool = True) -> tuple[str, ...]:
    return tuple(unit for target in targets for unit in _units(target, start=start))


def _target_files(targets: tuple[Target, ...]) -> set[str]:
    return set().union(*(_files(target) for target in targets))


def _units(target: Target, *, start: bool = True) -> tuple[str, ...]:
    if target.kind == "gateway":
        ordered = (
            "anvil-connect-authelia.service",
            "anvil-connect-caddy.service",
            "anvil-connect-gateway.service",
        )
    elif target.kind == "connector":
        assert target.name is not None
        ordered = ("anvil-connect-connector-" + target.name + ".service",)
    else:
        assert target.name is not None
        ordered = ("anvil-connect-client-" + target.name + ".service",)
    return ordered if start else tuple(reversed(ordered))


def _files(target: Target) -> set[str]:
    if target.kind == "gateway":
        return {
            "gateway.json", "caddy.json", "authelia/configuration.yml",
            "systemd/anvil-connect-gateway.service", "systemd/anvil-connect-caddy.service",
            "systemd/anvil-connect-authelia.service",
        }
    if target.kind == "connector":
        assert target.name is not None
        return {"connectors/" + target.name + ".json", "systemd/anvil-connect-connector-" + target.name + ".service"}
    assert target.name is not None
    return {"clients/" + target.name + ".json", "systemd/anvil-connect-client-" + target.name + ".service"}


def _config_path(data: dict[str, Any], target: Target) -> Path:
    root = Path(data["config_root"])
    if target.kind == "gateway":
        return root / "gateway.json"
    if target.kind == "connector":
        assert target.name is not None
        return root / "connectors" / (target.name + ".json")
    assert target.name is not None
    return root / "clients" / (target.name + ".json")


def _mode(target: Target) -> str:
    return {"gateway": "gateway", "connector": "connector", "client": "client"}[target.kind]


def _inspection_plan(data: dict[str, Any]) -> dict[str, Any]:
    """Inspect an owned legacy or isolated generation without publishing either."""
    return plan_for_inspection(data, data["config_root"])


def _target_identity(data: dict[str, Any], target: Target) -> ServiceIdentity | None:
    role = target.kind
    identifier = target.name if role in {"connector", "client"} else None
    return _role_service_identity(data, role, identifier)


def _role_account(data: dict[str, Any], role: str, identifier: str | None = None) -> tuple[int, int, str]:
    """Resolve one declared numeric identity through NSS at a manager boundary."""
    uid, gid = role_identity(data, role, identifier)
    try:
        account = pwd.getpwuid(uid)
        group = grp.getgrgid(gid)
        account_uid, account_gid, account_name = account.pw_uid, account.pw_gid, account.pw_name
        group_gid = group.gr_gid
    except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
        raise ManageError("declared service identity is unavailable") from exc
    if (type(account_uid) is not int or type(account_gid) is not int or type(group_gid) is not int
            or account_uid != uid or account_gid != gid or group_gid != gid
            or uid == 0 or gid == 0 or not isinstance(account_name, str) or not account_name):
        raise ManageError("declared service identity is unsafe")
    return uid, gid, account_name


def _role_supplementary_groups(data: dict[str, Any], role: str) -> set[int]:
    """Return the sole allowed supplementary group set for a declared role."""
    if role == "edge":
        return {data["service_identities"]["ingress"]["group_id"]}
    return set()


def _role_service_identity(data: dict[str, Any], role: str, identifier: str | None = None) -> ServiceIdentity | None:
    """Return an exact numeric service identity, or verify current-role execution."""
    uid, gid, _ = _role_account(data, role, identifier)
    if os.geteuid() == uid:
        try:
            effective_gid = os.getegid()
            groups = os.getgroups()
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            raise ManageError("current service credentials are unavailable") from exc
        expected = _role_supplementary_groups(data, role)
        if (type(effective_gid) is not int or effective_gid != gid
                or not isinstance(groups, list) or any(type(value) is not int for value in groups)
                or len(groups) != len(set(groups)) or set(groups) != expected):
            raise ManageError("current service credentials are unsafe")
        return None
    if os.geteuid() != 0:
        raise ManageError("native authority command must run as its declared service user")
    return ServiceIdentity(uid, gid)


def _legacy_recovery_identity(data: dict[str, Any]) -> ServiceIdentity | None:
    """Resolve the retired shared identity only for legacy offline recovery."""
    service_user = data.get("service_user")
    if not isinstance(service_user, str) or not service_user:
        raise ManageError("declared legacy recovery identity is unavailable")
    try:
        account = pwd.getpwnam(service_user)
        group = grp.getgrnam(service_user)
        uid, gid, group_gid = account.pw_uid, account.pw_gid, group.gr_gid
    except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
        raise ManageError("declared legacy recovery identity is unavailable") from exc
    if (type(uid) is not int or type(gid) is not int or type(group_gid) is not int
            or uid == 0 or gid == 0 or group_gid != gid):
        raise ManageError("declared legacy recovery identity is unsafe")
    if os.geteuid() == uid:
        return None
    if os.geteuid() != 0:
        raise ManageError("native authority command must run as its declared service user")
    return ServiceIdentity(uid, gid)


def _role_private_paths(data: dict[str, Any], target: Target) -> tuple[tuple[str, str, str | None], ...]:
    if target.kind == "gateway":
        return (
            ("gateway", data["gateway"]["state_directory"], None),
            ("edge", data["caddy"]["state_directory"], None),
            ("idp", data["authelia"]["state_directory"], None),
        )
    assert target.name is not None
    if target.kind == "connector":
        connector = next(item for item in data["connectors"] if item["id"] == target.name)
        return (("connector", connector["state_directory"], target.name),)
    return ()


def _target_roles(data: dict[str, Any], target: Target) -> tuple[tuple[str, str | None], ...]:
    if target.kind == "gateway":
        return (("gateway", None), ("edge", None), ("idp", None))
    assert target.name is not None
    return ((target.kind, target.name),)


def _safe_root_ancestors(path: Path) -> None:
    """Require a root-owned, non-writable path to a role-owned leaf.

    Role-owned parents are intentionally not accepted: a role state or secret
    leaf is isolated only when no role can replace a directory above it.
    """
    current = path.parent
    while True:
        try:
            info = current.lstat()
        except OSError as exc:
            raise ManageError("managed runtime directory is unavailable") from exc
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid != 0 or info.st_mode & 0o022):
            raise ManageError("managed runtime directory is unsafe")
        if current == current.parent:
            return
        current = current.parent


def _safe_private_runtime_directory(path: Path, uid: int, gid: int) -> None:
    _safe_root_ancestors(path)
    try:
        info = path.lstat()
    except OSError as exc:
        raise ManageError("managed runtime directory is unavailable") from exc
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != uid or info.st_gid != gid or stat.S_IMODE(info.st_mode) != 0o700):
        raise ManageError("managed runtime directory is unsafe")


def _safe_ingress_directory(data: dict[str, Any]) -> None:
    policy = data["service_identities"]["ingress"]
    gateway_uid, _ = role_identity(data, "gateway")
    path = Path(policy["directory"])
    _safe_root_ancestors(path)
    try:
        info = path.lstat()
    except OSError as exc:
        raise ManageError("managed ingress directory is unavailable") from exc
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != gateway_uid or info.st_gid != policy["group_id"]
            or stat.S_IMODE(info.st_mode) != 0o2710):
        raise ManageError("managed ingress directory is unsafe")


def _validate_closed_memberships(data: dict[str, Any]) -> None:
    """Permit only the edge account as a declared supplementary group member."""
    accounts: dict[str, str] = {}
    for role, identifier in [("gateway", None), ("edge", None), ("idp", None)]:
        _, _, account = _role_account(data, role, identifier)
        accounts[role] = account
    for connector in data["connectors"]:
        _, _, account = _role_account(data, "connector", connector["id"])
        accounts["connector:" + connector["id"]] = account
    for client in data["clients"]:
        name = client["rule"]["id"]
        _, _, account = _role_account(data, "client", name)
        accounts["client:" + name] = account
    ingress_gid = data["service_identities"]["ingress"]["group_id"]
    expected = {accounts["edge"]}
    try:
        groups = grp.getgrall()
    except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
        raise ManageError("declared service membership is unavailable") from exc
    if not isinstance(groups, list):
        raise ManageError("declared service membership is unavailable")
    declared = set(accounts.values())
    ingress_matches = 0
    for group in groups:
        try:
            group_gid, group_members = group.gr_gid, group.gr_mem
        except AttributeError as exc:
            raise ManageError("declared service membership is unavailable") from exc
        if type(group_gid) is not int or not isinstance(group_members, list) or any(not isinstance(member, str) or not member for member in group_members):
            raise ManageError("declared service membership is unavailable")
        members = set(group_members)
        if group_gid == ingress_gid:
            ingress_matches += 1
            if members != expected or len(group_members) != len(members):
                raise ManageError("declared ingress membership is unsafe")
        elif members.intersection(declared):
            raise ManageError("declared service membership is unsafe")
    if ingress_matches != 1:
        raise ManageError("declared ingress membership is unavailable")


def _validate_target_memberships(data: dict[str, Any], targets: tuple[Target, ...]) -> None:
    """Validate NSS-resolved supplementary groups for every selected role."""
    for target in targets:
        for role, identifier in _target_roles(data, target):
            uid, gid, account = _role_account(data, role, identifier)
            expected = {gid, *_role_supplementary_groups(data, role)}
            try:
                groups = os.getgrouplist(account, gid)
            except (AttributeError, OSError, TypeError, ValueError) as exc:
                raise ManageError("declared service membership is unavailable") from exc
            if (not isinstance(groups, list) or any(type(value) is not int for value in groups)
                    or len(groups) != len(set(groups)) or set(groups) != expected):
                raise ManageError("declared service membership is unsafe")


def _safe_consumed_file(path: Path, uid: int, gid: int, *, public: bool = False, root_only: bool = False) -> None:
    """Validate metadata for a role-read file without opening its contents."""
    _safe_root_ancestors(path)
    try:
        info = path.lstat()
    except OSError as exc:
        raise ManageError("declared service file is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ManageError("declared service file is unsafe")
    mode = stat.S_IMODE(info.st_mode)
    if public:
        if mode & 0o022:
            raise ManageError("declared service file is unsafe")
        return
    root_private = info.st_uid == 0 and mode == 0o600
    role_private = info.st_uid == uid and info.st_gid == gid and mode == 0o600
    root_role_readable = info.st_uid == 0 and info.st_gid == gid and mode == 0o640
    safe = root_private if root_only else role_private or root_role_readable
    if not safe:
        raise ManageError("declared service file is unsafe")


def _validate_gateway_files(data: dict[str, Any]) -> None:
    edge_uid, edge_gid = role_identity(data, "edge")
    idp_uid, idp_gid = role_identity(data, "idp")
    tls = data["caddy"]["tls"]
    if tls["mode"] == "provided":
        _safe_consumed_file(Path(tls["certificate_file"]), edge_uid, edge_gid, public=True)
        _safe_consumed_file(Path(tls["key_file"]), edge_uid, edge_gid)
    for name in (
        "users_file", "client_secret_file", "session_secret_file", "storage_encryption_key_file",
        "identity_validation_secret_file", "oidc_hmac_secret_file", "oidc_rsa_private_key_file",
    ):
        _safe_consumed_file(Path(data["authelia"][name]), idp_uid, idp_gid)


def _validate_isolated_runtime(data: dict[str, Any], targets: tuple[Target, ...]) -> None:
    """Validate role accounts and their provisioned runtime metadata, never values."""
    require_isolated(data)
    _validate_target_memberships(data, targets)
    for target in targets:
        for role, path, identifier in _role_private_paths(data, target):
            uid, gid, _ = _role_account(data, role, identifier)
            _safe_private_runtime_directory(Path(path), uid, gid)
    if any(target.kind == "gateway" for target in targets):
        _validate_closed_memberships(data)
        _safe_ingress_directory(data)
        _validate_gateway_files(data)


def _materialize(files: dict[str, str], root: Path) -> None:
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        os.chmod(target, 0o644)


def _validate_environment_files(data: dict[str, Any], target: Target | tuple[Target, ...] | None) -> None:
    """Verify only metadata; credentials are never opened by lifecycle code."""
    selected = _selected_targets(data, target)
    env = data["environment_files"]
    paths: list[tuple[Path, str, str | None, bool]] = []
    for item in selected:
        if item.kind == "gateway":
            paths.append((Path(env["gateway"]), "gateway", None, False))
            if "gateway_identity" in env:
                # The signing material is separately provisioned root-only
                # state, never a role-managed general gateway env file.
                paths.append((Path(env["gateway_identity"]), "gateway", None, True))
        elif item.kind == "connector":
            assert item.name is not None
            paths.append((Path(env["connectors"][item.name]), "connector", item.name, False))
        else:
            assert item.name is not None
            paths.append((Path(env["clients"][item.name]), "client", item.name, False))
    for path, role, identifier, root_only in paths:
        uid, gid = role_identity(data, role, identifier)
        _safe_root_ancestors(path)
        try:
            info = path.lstat()
        except OSError as exc:
            raise ManageError("declared EnvironmentFile is unavailable") from exc
        root_private = info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600
        role_private = info.st_uid == uid and info.st_gid == gid and stat.S_IMODE(info.st_mode) == 0o600
        root_role_readable = info.st_uid == 0 and info.st_gid == gid and stat.S_IMODE(info.st_mode) == 0o640
        safe_metadata = root_private if root_only else root_private or role_private or root_role_readable
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_nlink != 1 or not safe_metadata):
            raise ManageError("declared EnvironmentFile has unsafe ownership or mode")


def _validate_data(data: dict[str, Any], target: Target | tuple[Target, ...] | None, runner: Runner | None) -> dict[str, Any]:
    require_isolated(data)
    selected = _selected_targets(data, target)
    _validate_isolated_runtime(data, selected)
    _validate_environment_files(data, target)
    digests = _verified_binaries(data, target)
    generated = render_config(data)
    with tempfile.TemporaryDirectory(prefix="anvil-connect-validate-") as temporary:
        root = Path(temporary)
        _materialize(generated["files"], root)
        # Validators run with the service UID, so this temporary public
        # declaration must be traversable just like installed rendered config.
        _make_public(root)
        for item in selected:
            result = _run(runner, (data["binary"], "preflight", "--mode", _mode(item), "--config", str(_config_path({**data, "config_root": str(root)}, item))), _VALIDATE_TIMEOUT, _target_identity(data, item))
            _fail(result, "native declaration validation failed")
        if any(item.kind == "gateway" for item in selected):
            caddy = _run(runner, (data["components"]["caddy"], "validate", "--config", str(root / "caddy.json")), _VALIDATE_TIMEOUT, _role_service_identity(data, "edge"))
            _fail(caddy, "Caddy configuration validation failed")
            authelia = _run(runner, (data["components"]["authelia"], "--config", str(root / "authelia" / "configuration.yml"), "--config.experimental.filters", "template", "config", "validate"), _VALIDATE_TIMEOUT, _role_service_identity(data, "idp"))
            _fail(authelia, "Authelia configuration validation failed")
    return {"targets": [item.text() for item in selected], "digests": digests, "generation": generated["generation"]}


def validate(manifest_path: str | Path, target: Target | None = None, *, runner: Runner | None = None) -> dict[str, Any]:
    """Validate one declaration without writing a generation or running services."""
    _require_supported_platform()
    data = read_manifest(manifest_path)
    if "service_identities" not in data:
        selected = _targets(data, target)
        return {
            "schema": "anvil-connect.manage/v1", "action": "validate", "applied": False,
            "targets": [item.text() for item in selected], "migration_required": True,
            "plan": _inspection_plan(data),
        }
    result = _validate_data(data, target, runner)
    result.update({"schema": "anvil-connect.manage/v1", "action": "validate", "applied": False, "plan": plan(data, data["config_root"])})
    return result


def render_generation(manifest_path: str | Path, *, apply: bool = False, runner: Runner | None = None) -> dict[str, Any]:
    """Preview or write only a sibling staged generation; never activate it."""
    _require_supported_platform()
    data = read_manifest(manifest_path)
    require_isolated(data)
    checked = _validate_data(data, None, runner)
    result: dict[str, Any] = {"schema": "anvil-connect.manage/v1", "action": "render", "applied": bool(apply), "plan": plan(data, data["config_root"]), **checked}
    if apply:
        result["stage"] = stage(data, data["config_root"])
    return result


# ``render`` is the public lifecycle spelling. Keep the renderer import private.
def render(manifest_path: str | Path, *, apply: bool = False, runner: Runner | None = None) -> dict[str, Any]:
    return render_generation(manifest_path, apply=apply, runner=runner)


def _safe_dir(path: Path, *, strict: bool = True) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ManageError("managed directory is unavailable") from exc
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid not in {0, os.geteuid()} or (strict and info.st_mode & 0o022)):
        raise ManageError("managed directory is unsafe")


def _read_regular(path: Path, maximum: int) -> bytes | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ManageError("managed file is unsafe") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ManageError("managed file is unsafe")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            value = os.read(descriptor, min(8192, remaining))
            if not value:
                break
            chunks.append(value)
            remaining -= len(value)
        value = b"".join(chunks)
        if len(value) > maximum:
            raise ManageError("managed file is unsafe")
        return value
    finally:
        os.close(descriptor)


def _read_unit(path: Path) -> bytes | None:
    return _read_regular(path, _MAX_OUTPUT)


def _strict_json(raw: bytes, message: str) -> dict[str, Any]:
    def duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = child
        return value
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=duplicate)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ManageError(message) from exc
    if not isinstance(value, dict):
        raise ManageError(message)
    return value


def _owned_marker(root: Path) -> tuple[str, dict[str, str]]:
    raw = _read_regular(root / "managed.json", _MAX_OUTPUT)
    if raw is None:
        raise ManageError("active ownership marker is invalid")
    value = _strict_json(raw, "active ownership marker is invalid")
    if set(value) != {"schema", "generation", "files"} or value.get("schema") != "anvil-connect.ownership/v1":
        raise ManageError("active ownership marker is invalid")
    generation, files = value.get("generation"), value.get("files")
    if not isinstance(generation, str) or len(generation) != 64 or any(char not in "0123456789abcdef" for char in generation):
        raise ManageError("active ownership marker is invalid")
    if not isinstance(files, dict) or len(files) > 128:
        raise ManageError("active ownership marker is invalid")
    checked: dict[str, str] = {}
    for name, digest in files.items():
        candidate = Path(name)
        if (not isinstance(name, str) or not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
                or candidate.is_absolute() or ".." in candidate.parts or not name or name.startswith(".")):
            raise ManageError("active ownership marker is invalid")
        checked[name] = digest
    return generation, checked


def _unit_sources(root: Path) -> dict[str, bytes]:
    _, names = _owned_marker(root)
    result: dict[str, bytes] = {}
    for name, digest in names.items():
        if not name.startswith("systemd/") or not name.endswith(".service"):
            continue
        value = _read_unit(root / name)
        if value is None or hashlib.sha256(value).hexdigest() != digest:
            raise ManageError("active owned unit source drifted")
        result[name.removeprefix("systemd/")] = value
    return result


def _write_atomic(path: Path, data: bytes, mode: int = 0o644) -> None:
    """Replace one owned file without deleting a pre-existing foreign temp."""
    temporary = path.with_name("." + path.name + ".anvil-connect-" + secrets.token_hex(16) + ".new")
    descriptor: int | None = None
    temporary_inode: tuple[int, int] | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, mode)
        info = os.fstat(descriptor)
        temporary_inode = (info.st_dev, info.st_ino)
        # os.open's creation mode is filtered through the caller's umask. The
        # caller selected this file's public or private contract explicitly,
        # so apply it to the just-created no-follow descriptor before durable
        # publication.
        os.fchmod(descriptor, mode)
        offset = 0
        while offset < len(data):
            count = os.write(descriptor, data[offset:])
            if count <= 0:
                raise OSError("short write")
            offset += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise ManageError("managed file write failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_inode is not None:
            try:
                info = temporary.lstat()
            except FileNotFoundError:
                pass
            else:
                if (info.st_dev, info.st_ino) == temporary_inode:
                    try:
                        os.unlink(temporary)
                    except FileNotFoundError:
                        pass


def _verify_unit(unit_root: Path, unit: str, expected: bytes | None, *, allow_missing: bool = False) -> None:
    if not unit.endswith(".service") or "/" in unit:
        raise ManageError("invalid managed unit")
    value = _read_unit(unit_root / unit)
    if expected is None:
        if value is not None:
            raise ManageError("refusing to replace an unowned unit")
    elif value is None and allow_missing:
        pass
    elif value != expected:
        raise ManageError("managed unit drifted or is foreign")
    dropins = unit_root / (unit + ".d")
    if os.path.lexists(dropins):
        raise ManageError("managed unit has an unowned drop-in")


def _unit_metadata(runner: Runner | None, unit_root: Path, units: tuple[str, ...], *, present: bool) -> None:
    for unit in units:
        result = _run(runner, (_SYSTEMCTL, "show", "--property=LoadState,FragmentPath,DropInPaths", unit), _VALIDATE_TIMEOUT)
        _fail(result, "managed unit metadata is unavailable")
        fields = dict(line.split("=", 1) for line in result.stdout.decode("utf-8", "replace").splitlines() if "=" in line)
        if set(fields) != {"LoadState", "FragmentPath", "DropInPaths"}:
            raise ManageError("managed unit metadata is invalid")
        if present:
            if fields["LoadState"] != "loaded" or fields["FragmentPath"] != str(unit_root / unit) or fields["DropInPaths"]:
                raise ManageError("managed unit fragment is not owned")
        elif fields["LoadState"] != "not-found" or fields["FragmentPath"] or fields["DropInPaths"]:
            # A vendor/runtime fragment may exist outside /etc; that must never
            # be overwritten simply because the selected destination is absent.
            raise ManageError("managed unit name is already owned elsewhere")


def _verify_owned_tree(root: Path, *, strict: bool = True) -> tuple[str, dict[str, str]]:
    _safe_dir(root, strict=strict)
    generation, names = _owned_marker(root)
    expected: dict[Path, set[str]] = {root: {"managed.json"}}
    for name, digest in names.items():
        candidate = root / name
        current = candidate.parent
        expected.setdefault(current, set()).add(candidate.name)
        while current != root:
            parent = current.parent
            expected.setdefault(parent, set()).add(current.name)
            current = parent
        _safe_dir(candidate.parent, strict=strict)
        value = _read_regular(candidate, 2 * 1024 * 1024)
        if value is None or hashlib.sha256(value).hexdigest() != digest:
            raise ManageError("owned generation drifted")
    for directory, allowed in expected.items():
        _safe_dir(directory, strict=strict)
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise ManageError("owned generation is unavailable") from exc
        if len(entries) > 256 or any(entry.name not in allowed for entry in entries):
            raise ManageError("owned generation contains foreign paths")
    return generation, names


def _make_public(root: Path) -> None:
    """Set only verified generated paths public; never recurse an untrusted tree."""
    _, names = _verify_owned_tree(root, strict=False)
    directories = {root}
    for name in names:
        candidate = root / name
        directories.add(candidate.parent)
        descriptor = os.open(candidate, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            os.fchmod(descriptor, 0o644)
        finally:
            os.close(descriptor)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _safe_dir(directory, strict=False)
        os.chmod(directory, 0o755)


def _remove_owned_tree(root: Path) -> None:
    """Remove only a verified generated tree; never walk foreign descendants."""
    _, names = _verify_owned_tree(root)
    for name in sorted(names, key=lambda item: len(Path(item).parts), reverse=True):
        try:
            os.unlink(root / name)
        except FileNotFoundError:
            pass
    try:
        os.unlink(root / "managed.json")
    except FileNotFoundError:
        pass
    directories = {root}
    for name in names:
        candidate = root / name
        while candidate.parent != root.parent:
            candidate = candidate.parent
            directories.add(candidate)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        try:
            os.rmdir(directory)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ManageError("owned generation cleanup is unsafe") from exc


@contextmanager
def _deployment_lock(root: Path) -> Iterator[None]:
    _safe_dir(root.parent)
    lock = root.parent / ("." + root.name + ".anvil-connect.lock")
    descriptor: int | None = None
    try:
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o077:
            raise ManageError("lifecycle lock is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except OSError as exc:
        raise ManageError("lifecycle lock is unavailable") from exc
    finally:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _activation_record(root: Path) -> Path:
    return root.parent / ("." + root.name + ".anvil-connect-activation.json")


def _valid_prior_record(raw: bytes | None, generation: str | None) -> None:
    if raw is None:
        return
    value = _strict_json(raw, "activation record is not owned")
    if set(value) != {"schema", "generation", "native_sha256", "components"} or value.get("schema") != "anvil-connect.activation/v1":
        raise ManageError("activation record is not owned")
    components = value.get("components")
    if (value.get("generation") != generation or not isinstance(components, dict)
            or set(components) not in (set(), {"caddy", "authelia"})):
        raise ManageError("activation record is not owned")
    for digest in [value.get("native_sha256"), *components.values()]:
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ManageError("activation record is not owned")


def _bound_active(data: dict[str, Any], target: Target, digests: dict[str, str], *, allow_unpinned_gateway: bool = False) -> dict[str, Any]:
    """Bind a current operation to the generation and binaries that activated it."""
    root = Path(data["config_root"])
    generation, _ = _verify_owned_tree(root)
    raw = _read_regular(_activation_record(root), _MAX_OUTPUT)
    _valid_prior_record(raw, generation)
    if raw is None:
        raise ManageError("current generation has no owned activation record")
    record = _strict_json(raw, "activation record is not owned")
    if record["native_sha256"] != digests.get("native"):
        raise ManageError("managed native binary changed since activation")
    if target.kind == "gateway":
        expected = {name: digests.get(name) for name in ("caddy", "authelia")}
        if any(value is None for value in expected.values()):
            raise ManageError("managed component binary is unavailable")
        if record["components"] != expected:
            if not (allow_unpinned_gateway and record["components"] == {}):
                raise ManageError("managed component binary changed since activation")
    return record


def _unit_exec_path(source: bytes) -> Path:
    """Read the deliberately plain rendered ExecStart argv without shell parsing."""
    try:
        lines = source.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ManageError("owned prior unit is invalid") from exc
    values = [line.removeprefix("ExecStart=") for line in lines if line.startswith("ExecStart=")]
    if len(values) != 1:
        raise ManageError("owned prior unit is invalid")
    command = values[0].split(" ", 1)[0]
    path = Path(command)
    if not command or not path.is_absolute() or ".." in path.parts:
        raise ManageError("owned prior unit is invalid")
    return path


def _verify_upgrade_prior(data: dict[str, Any], targets: tuple[Target, ...], digests: dict[str, str]) -> None:
    """Prove old pinned bytes remain available before an explicit path upgrade.

    Replacing a binary in place cannot be rolled back from public configuration;
    this path therefore accepts only a new executable pathname for an artifact
    whose digest changed.  The old pathname is read from the active owned unit,
    then checked against the activation record before any config is moved.
    """
    root = Path(data["config_root"])
    generation, _ = _verify_owned_tree(root)
    raw = _read_regular(_activation_record(root), _MAX_OUTPUT)
    _valid_prior_record(raw, generation)
    if raw is None:
        raise ManageError("current generation has no owned activation record")
    record = _strict_json(raw, "activation record is not owned")
    sources = _unit_sources(root)
    native_paths = [_unit_exec_path(source) for unit, source in sources.items() if unit not in {"anvil-connect-caddy.service", "anvil-connect-authelia.service"}]
    if not native_paths or any(_digest(path) != record["native_sha256"] for path in native_paths):
        raise ManageError("prior native executable is unavailable for rollback")
    if digests["native"] != record["native_sha256"] and any(Path(data["binary"]) == path for path in native_paths):
        raise ManageError("native binary upgrade requires a new executable path")
    if not any(target.kind == "gateway" for target in targets):
        return
    components = record["components"]
    if components == {}:
        return
    for name, unit in (("caddy", "anvil-connect-caddy.service"), ("authelia", "anvil-connect-authelia.service")):
        source = sources.get(unit)
        if source is None:
            raise ManageError("prior component unit is unavailable for rollback")
        old_path = _unit_exec_path(source)
        if _digest(old_path) != components[name]:
            raise ManageError("prior component executable is unavailable for rollback")
        if digests[name] != components[name] and Path(data["components"][name]) == old_path:
            raise ManageError("component upgrade requires a new executable path")


def _write_activation_record(root: Path, record: dict[str, Any]) -> None:
    _write_atomic(_activation_record(root), (json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))


@dataclass
class _Activation:
    root: Path
    unit_root: Path
    prior_units: dict[str, bytes | None]
    prior_record: bytes | None
    backup: Path | None
    root_moved: bool
    new_root: bool = False
    committed: bool = False
    cleanup_retained: bool = False
    pending_record: dict[str, Any] | None = None

    def rollback(self, runner: Runner | None) -> None:
        """Restore public config/unit bytes. Preserve backup if restoration fails."""
        failure: Exception | None = None
        try:
            for unit, value in self.prior_units.items():
                if value is None:
                    try:
                        os.unlink(self.unit_root / unit)
                    except FileNotFoundError:
                        pass
                else:
                    _write_atomic(self.unit_root / unit, value)
            if self.new_root and self.root.exists():
                _remove_owned_tree(self.root)
            if self.root_moved:
                if self.backup is None:
                    raise ManageError("activation rollback artifact is unavailable")
                os.replace(self.backup, self.root)
            if self.prior_record is None:
                try:
                    os.unlink(_activation_record(self.root))
                except FileNotFoundError:
                    pass
            else:
                _write_atomic(_activation_record(self.root), self.prior_record)
            _fail(_run(runner, (_SYSTEMCTL, "daemon-reload"), _SYSTEMD_TIMEOUT), "systemd rollback failed")
        except Exception as exc:  # preserve any untouched backup for operator recovery
            failure = exc
        if failure is not None:
            raise ManageError("activation rollback failed; recovery artifact retained", may_have_executed=True) from failure

    def commit(self) -> None:
        if self.committed:
            return
        # A post-start cleanup failure must not turn an already working new
        # generation into a rollback against a partly removed old tree.
        self.committed = True
        if self.backup is not None and self.backup.exists():
            try:
                _remove_owned_tree(self.backup)
            except ManageError:
                self.cleanup_retained = True


def _activate(data: dict[str, Any], targets: tuple[Target, ...], stage_path: Path, report: dict[str, Any], digests: dict[str, str], runner: Runner | None, unit_root: Path, *, upgrade: bool = False) -> _Activation:
    root = Path(data["config_root"])
    _safe_dir(root.parent)
    _safe_dir(unit_root)
    state = report["state"]
    if state not in {"absent", "update"}:
        raise ManageError("rendered ownership is not safe to activate")
    changed = set(report["changes"]) - {"managed.json"}
    if state == "update" and not changed <= _target_files(targets):
        raise ManageError("activation would change an unselected target")
    root_exists = root.exists()
    old_sources: dict[str, bytes] = {}
    old_generation: str | None = None
    selected_gateway = any(target.kind == "gateway" for target in targets)
    if root_exists:
        if upgrade:
            _verify_upgrade_prior(data, targets, digests)
        else:
            _bound_active(data, Target("gateway") if selected_gateway else targets[0], digests, allow_unpinned_gateway=selected_gateway)
        old_generation, _ = _verify_owned_tree(root)
        old_sources = _unit_sources(root)
    _verify_owned_tree(stage_path, strict=False)
    desired_sources = _unit_sources(stage_path)
    copy_units = tuple(unit for unit in _target_units(targets) if unit in desired_sources and (state == "absent" or "systemd/" + unit in changed or not (unit_root / unit).exists()))
    for unit in copy_units:
        _verify_unit(unit_root, unit, old_sources.get(unit), allow_missing=True)
        _unit_metadata(runner, unit_root, (unit,), present=(unit_root / unit).exists())
    prior_units = {unit: _read_unit(unit_root / unit) for unit in copy_units}
    prior_record = _read_regular(_activation_record(root), _MAX_OUTPUT)
    _valid_prior_record(prior_record, old_generation)
    prior_components: dict[str, str] = {}
    if prior_record is not None:
        prior_components = _strict_json(prior_record, "activation record is not owned")["components"]
    transaction = _Activation(root, unit_root, prior_units, prior_record, None, False)
    try:
        if root_exists:
            backup = Path(tempfile.mkdtemp(prefix="." + root.name + ".anvil-connect-rollback-", dir=root.parent))
            os.rmdir(backup)
            transaction.backup = backup
            os.replace(root, backup)
            transaction.root_moved = True
        _make_public(stage_path)
        os.replace(stage_path, root)
        transaction.new_root = True
        for unit in copy_units:
            _write_atomic(unit_root / unit, desired_sources[unit])
        _fail(_run(runner, (_SYSTEMCTL, "daemon-reload"), _SYSTEMD_TIMEOUT), "systemd did not accept the owned generation")
        for unit in copy_units:
            _unit_metadata(runner, unit_root, (unit,), present=True)
        transaction.pending_record = {
            "schema": "anvil-connect.activation/v1",
            "generation": render_config(data)["generation"],
            "native_sha256": digests["native"],
            "components": ({key: digests[key] for key in ("caddy", "authelia")} if selected_gateway else prior_components),
        }
        return transaction
    except Exception as exc:
        try:
            transaction.rollback(runner)
        except ManageError as rollback_error:
            # The named rollback directory remains exactly because it is unsafe
            # to discard the sole known-good generation after a failed restore.
            raise rollback_error from exc
        raise _executed_error(exc) from exc


def _install_missing_units(root: Path, targets: tuple[Target, ...], runner: Runner | None, unit_root: Path) -> _Activation:
    """Install only selected absent units from a current owned generation."""
    _safe_dir(unit_root)
    sources = _unit_sources(root)
    missing = tuple(unit for unit in _target_units(targets) if not (unit_root / unit).exists())
    for unit in missing:
        expected = sources.get(unit)
        if expected is None:
            raise ManageError("selected unit is not declared by the active generation")
        _verify_unit(unit_root, unit, expected, allow_missing=True)
        _unit_metadata(runner, unit_root, (unit,), present=False)
    transaction = _Activation(root, unit_root, {unit: None for unit in missing}, _read_regular(_activation_record(root), _MAX_OUTPUT), None, False)
    try:
        for unit in missing:
            _write_atomic(unit_root / unit, sources[unit])
        if missing:
            _fail(_run(runner, (_SYSTEMCTL, "daemon-reload"), _SYSTEMD_TIMEOUT), "systemd did not accept the owned unit")
        for unit in missing:
            _unit_metadata(runner, unit_root, (unit,), present=True)
        return transaction
    except Exception as exc:
        try:
            transaction.rollback(runner)
        except ManageError as rollback_error:
            raise rollback_error from exc
        raise _executed_error(exc) from exc


def _unit_state(runner: Runner | None, unit: str) -> tuple[bool, str]:
    result = _run(runner, (_SYSTEMCTL, "show", "--property=ActiveState,UnitFileState", unit), _VALIDATE_TIMEOUT)
    _fail(result, "managed unit state is unavailable")
    fields = dict(line.split("=", 1) for line in result.stdout.decode("utf-8", "replace").splitlines() if "=" in line)
    state = fields.get("UnitFileState")
    if set(fields) != {"ActiveState", "UnitFileState"} or state not in {"disabled", "enabled", "enabled-runtime"}:
        raise ManageError("managed unit state is invalid")
    return fields["ActiveState"] == "active", state


def _restore_running(runner: Runner | None, units: tuple[str, ...], prior: dict[str, tuple[bool, str]],
                     gateway_ready: Callable[[], None] | None = None) -> None:
    """Restore observed units in dependency order after owned bytes are restored."""
    failure: ManageError | None = None
    mutated = False

    # Return previously inactive units to their exact enabled/runtime state
    # before restarting active dependencies.  This avoids leaving a failed new
    # generation running while preserving a manually running disabled unit.
    for unit in reversed(units):
        state = prior.get(unit)
        if state is None:
            continue
        was_active, unit_file_state = state
        try:
            if not was_active:
                mutated = True
                _action(runner, (_SYSTEMCTL, "disable", "--now", unit), _SYSTEMD_TIMEOUT, "managed unit restoration failed")
                if unit_file_state == "enabled":
                    mutated = True
                    _action(runner, (_SYSTEMCTL, "enable", unit), _SYSTEMD_TIMEOUT, "managed unit restoration failed")
                elif unit_file_state == "enabled-runtime":
                    mutated = True
                    _action(runner, (_SYSTEMCTL, "enable", "--runtime", unit), _SYSTEMD_TIMEOUT, "managed unit restoration failed")
        except ManageError as exc:
            failure = exc
    gateway_units = _units(Target("gateway"))
    for unit in (*gateway_units, *(item for item in units if item not in gateway_units)):
        state = prior.get(unit)
        if state is None or not state[0]:
            continue
        try:
            # Restart under the restored generation but do not turn a manually
            # running, disabled unit into an enabled unit.
            mutated = True
            _action(runner, (_SYSTEMCTL, "restart", unit), _SYSTEMD_TIMEOUT, "managed unit restoration failed")
            if unit == "anvil-connect-gateway.service" and gateway_ready is not None:
                gateway_ready()
        except ManageError as exc:
            failure = exc
            if unit == "anvil-connect-gateway.service":
                break
    if failure is not None:
        raise ManageError("managed unit restoration failed", may_have_executed=mutated or failure.may_have_executed) from failure


def _started_units(runner: Runner | None, unit_root: Path, units: tuple[str, ...]) -> None:
    """Require stable supervised processes before discarding activation rollback.

    This is a bounded process check, not origin or application readiness.
    """
    previous: dict[str, str] = {}
    for attempt in range(6):
        observed: dict[str, str] = {}
        for unit in units:
            result = _run(runner, (_SYSTEMCTL, "show", "--property=ActiveState,SubState,MainPID,FragmentPath,DropInPaths", unit), _VALIDATE_TIMEOUT)
            _fail(result, "managed unit status failed after startup")
            fields = dict(line.split("=", 1) for line in result.stdout.decode("utf-8").splitlines() if "=" in line)
            if (set(fields) != {"ActiveState", "SubState", "MainPID", "FragmentPath", "DropInPaths"}
                    or fields["FragmentPath"] != str(unit_root / unit) or fields["DropInPaths"]
                    or fields["ActiveState"] in {"failed", "inactive"}):
                raise ManageError("managed unit failed after startup")
            if fields["ActiveState"] == "active" and fields["SubState"] == "running" and fields["MainPID"].isdigit() and int(fields["MainPID"]) > 0:
                observed[unit] = fields["MainPID"]
        if len(observed) == len(units) and observed == previous:
            return
        previous = observed
        if attempt < 5:
            time.sleep(1)
    raise ManageError("managed units did not stabilize after startup")


def _closed_gateway_status(raw: bytes) -> None:
    """Accept only the secret-free response from the native admin status RPC."""
    value = _strict_json(raw, "gateway readiness response is invalid")
    fields = {
        "operation", "epoch", "secret", "key_id", "principal", "grants",
        "invitation", "installation", "role", "resources", "generation",
        "fingerprint", "status",
    }
    if set(value) != fields or value.get("operation") != "status":
        raise ManageError("gateway readiness response is invalid")
    epoch = value.get("epoch")
    empty = ("secret", "key_id", "principal", "invitation", "installation", "role", "fingerprint")
    status = value.get("status")
    if (not isinstance(epoch, str) or len(epoch) != 64 or any(char not in "0123456789abcdef" for char in epoch)
            or any(value.get(name) != "" for name in empty)
            or value.get("grants") != [] or value.get("resources") != []
            or type(value.get("generation")) is not int or value["generation"] != 0
            or not isinstance(status, dict)
            or set(status) != {"id", "status", "fingerprint", "epoch", "generation", "resources"}
            or status.get("id") != "" or status.get("status") != "" or status.get("fingerprint") != ""
            or status.get("epoch") != "" or type(status.get("generation")) is not int or status["generation"] != 0
            or status.get("resources") != []):
        raise ManageError("gateway readiness response is invalid")


def _gateway_ready(data: dict[str, Any], runner: Runner | None) -> None:
    """Require the restarted gateway's own same-user admin socket before dependents."""
    root = Path(data["config_root"])
    _safe_dir(root.parent)
    with tempfile.TemporaryDirectory(prefix=".anvil-connect-status-", dir=root.parent) as temporary:
        request = Path(temporary) / "status.json"
        _write_atomic(request, b'{"operation":"status"}\n')
        # The status request has no credentials or mutating fields.  The parent
        # remains non-writable, while the service identity can read this exact
        # declaration and its owner-only admin socket.
        os.chmod(request.parent, 0o755)
        command = (
            data["binary"], "admin", "--socket",
            str(Path(data["gateway"]["state_directory"]) / "admin.sock"),
            "--request", str(request),
        )
        for attempt in range(6):
            observed = _run(runner, command, _VALIDATE_TIMEOUT, _role_service_identity(data, "gateway"))
            if observed.returncode == 0:
                _closed_gateway_status(observed.stdout)
                return
            if attempt < 5:
                time.sleep(1)
    raise ManageError("gateway did not become ready before dependent activation")


def _prior_gateway_identity(data: dict[str, Any], source: bytes) -> ServiceIdentity | None:
    """Resolve only the identity recorded in a restored owned gateway unit."""
    try:
        text = source.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ManageError("owned prior gateway unit is invalid") from exc
    values = {
        key: [line.removeprefix(key + "=") for line in text.splitlines() if line.startswith(key + "=")]
        for key in ("User", "Group")
    }
    if any(len(value) != 1 or not value[0] for value in values.values()):
        raise ManageError("owned prior gateway unit is invalid")
    user, group = values["User"][0], values["Group"][0]
    if user.isdecimal() and group.isdecimal():
        uid, gid = int(user), int(group)
        if uid <= 0 or gid <= 0:
            raise ManageError("owned prior gateway identity is unsafe")
        prior_identities = dict(data["service_identities"])
        prior_identities["gateway"] = {"uid": uid, "gid": gid}
        return _role_service_identity({**data, "service_identities": prior_identities}, "gateway")
    if _ID.fullmatch(user) is None or _ID.fullmatch(group) is None:
        raise ManageError("owned prior gateway identity is invalid")
    if user != group:
        raise ManageError("owned prior gateway identity is invalid")
    # Legacy units had one service user for every role.  Resolve it through the
    # existing current-credential checks rather than trusting a rendered name.
    return _legacy_recovery_identity({**data, "service_user": user})


def _restored_gateway_ready(data: dict[str, Any], root: Path, runner: Runner | None) -> None:
    """Probe a restored gateway using only its prior owned binary and identity."""
    generation, _ = _verify_owned_tree(root)
    raw = _read_regular(_activation_record(root), _MAX_OUTPUT)
    _valid_prior_record(raw, generation)
    if raw is None:
        raise ManageError("restored generation has no owned activation record")
    record = _strict_json(raw, "activation record is not owned")
    source = _unit_sources(root).get("anvil-connect-gateway.service")
    if source is None:
        raise ManageError("owned prior gateway unit is unavailable")
    binary = _unit_exec_path(source)
    if _digest(binary) != record["native_sha256"]:
        raise ManageError("prior gateway executable is unavailable for rollback")
    gateway = _strict_json(_read_regular(root / "gateway.json", _MAX_OUTPUT) or b"", "owned prior gateway declaration is invalid")
    state_directory = gateway.get("state_directory")
    socket = Path(state_directory) / "admin.sock" if isinstance(state_directory, str) else Path()
    if not socket.is_absolute() or ".." in socket.parts:
        raise ManageError("owned prior gateway declaration is invalid")
    identity = _prior_gateway_identity(data, source)
    _safe_dir(root.parent)
    with tempfile.TemporaryDirectory(prefix=".anvil-connect-status-", dir=root.parent) as temporary:
        request = Path(temporary) / "status.json"
        _write_atomic(request, b'{"operation":"status"}\n')
        os.chmod(request.parent, 0o755)
        command = (str(binary), "admin", "--socket", str(socket), "--request", str(request))
        for attempt in range(6):
            observed = _run(runner, command, _VALIDATE_TIMEOUT, identity)
            if observed.returncode == 0:
                _closed_gateway_status(observed.stdout)
                return
            if attempt < 5:
                time.sleep(1)
    raise ManageError("restored gateway did not become ready before dependent restoration")


def _active_generation_isolated(data: dict[str, Any]) -> bool:
    """Prove the active owned generation has numeric role units before a partial update."""
    root = Path(data["config_root"])
    raw = _read_regular(root / "gateway.json", _MAX_OUTPUT)
    if raw is None:
        return False
    try:
        gateway = _strict_json(raw, "active declaration is invalid")
        ingress = gateway.get("ingress")
        if not isinstance(ingress, dict) or set(ingress) != {"directory", "gateway_uid", "edge_uid", "group_id"}:
            return False
        if (not isinstance(ingress["directory"], str)
                or any(isinstance(ingress[name], bool) or not isinstance(ingress[name], int) or ingress[name] <= 0
                       for name in ("gateway_uid", "edge_uid", "group_id"))):
            return False
        sources = _unit_sources(root)
    except (ManageError, UnicodeDecodeError):
        return False
    declared = _target_units(_targets(data, None))
    for unit in declared:
        source = sources.get(unit)
        if source is None:
            return False
        try:
            text = source.decode("utf-8", "strict")
        except UnicodeDecodeError:
            return False
        if re.search(r"^User=[1-9][0-9]*$", text, flags=re.MULTILINE) is None or re.search(r"^Group=[1-9][0-9]*$", text, flags=re.MULTILINE) is None:
            return False
    return True


def _require_complete_isolated_migration(data: dict[str, Any], targets: tuple[Target, ...], report: dict[str, Any]) -> None:
    """Reject an identity migration that would leave an old unit mixed in."""
    if report.get("state") != "update" or not Path(data["config_root"]).exists() or _active_generation_isolated(data):
        return
    if set(targets) != set(_targets(data, None)):
        raise ManageError("isolated migration requires every declared target")


def _up_selected(data: dict[str, Any], targets: tuple[Target, ...], *, apply: bool, upgrade: bool, runner: Runner | None, unit_root: str | Path, action: str) -> dict[str, Any]:
    """Activate one closed role set in a single reversible transaction."""
    _require_supported_platform()
    require_isolated(data)
    checked = _validate_data(data, targets, runner)
    report = plan(data, data["config_root"])
    _require_complete_isolated_migration(data, targets, report)
    units = _target_units(targets)
    result: dict[str, Any] = {
        "schema": "anvil-connect.manage/v1", "action": action,
        "targets": [target.text() for target in targets], "applied": bool(apply),
        "upgrade": bool(upgrade), "plan": report, "units": list(units), **checked,
    }
    if not apply:
        return result
    root, system_root = Path(data["config_root"]), Path(unit_root)
    with _deployment_lock(root):
        # Re-plan under the lock; a concurrent render must not change what this
        # request decided was target-scoped.
        report = plan(data, data["config_root"])
        _require_complete_isolated_migration(data, targets, report)
        if report["state"] not in {"absent", "update", "current"}:
            raise ManageError("rendered ownership is not safe to activate")
        transaction: _Activation | None = None
        active_record: dict[str, Any] | None = None
        prior: dict[str, tuple[bool, str]] = {}
        selected_gateway = any(target.kind == "gateway" for target in targets)
        try:
            if report["state"] != "current":
                staged = stage(data, data["config_root"])
                transaction = _activate(data, targets, Path(staged["path"]), report, checked["digests"], runner, system_root, upgrade=upgrade)
            else:
                if upgrade:
                    _verify_upgrade_prior(data, targets, checked["digests"])
                    active_record = _strict_json(_read_regular(_activation_record(root), _MAX_OUTPUT) or b"", "activation record is not owned")
                else:
                    active_record = _bound_active(data, Target("gateway") if selected_gateway else targets[0], checked["digests"], allow_unpinned_gateway=selected_gateway)
                transaction = _install_missing_units(root, targets, runner, system_root)
            sources = _unit_sources(root)
            for unit in units:
                _verify_unit(system_root, unit, sources.get(unit))
                _unit_metadata(runner, system_root, (unit,), present=True)
                prior[unit] = _unit_state(runner, unit)
            gateway_units = _target_units((Target("gateway"),)) if selected_gateway else ()
            dependent_units = tuple(unit for unit in units if unit not in gateway_units)
            for unit in gateway_units:
                active, enabled = prior[unit]
                if active:
                    _action(runner, (_SYSTEMCTL, "restart", unit), _SYSTEMD_TIMEOUT, "managed unit failed to restart")
                else:
                    _action(runner, (_SYSTEMCTL, "enable", "--now", unit), _SYSTEMD_TIMEOUT, "managed unit failed to start")
            if selected_gateway:
                _gateway_ready(data, runner)
            for unit in dependent_units:
                active, enabled = prior[unit]
                if active:
                    _action(runner, (_SYSTEMCTL, "restart", unit), _SYSTEMD_TIMEOUT, "managed unit failed to restart")
                else:
                    _action(runner, (_SYSTEMCTL, "enable", "--now", unit), _SYSTEMD_TIMEOUT, "managed unit failed to start")
            _started_units(runner, system_root, units)
            if transaction.pending_record is not None:
                # Do not bless newly supplied artifact bytes until the complete
                # selected start/restart sequence has succeeded.
                _write_activation_record(root, transaction.pending_record)
            elif selected_gateway and active_record is not None and active_record["components"] == {}:
                active_record = dict(active_record)
                active_record["components"] = {name: checked["digests"][name] for name in ("caddy", "authelia")}
                _write_activation_record(root, active_record)
            transaction.commit()
        except Exception as exc:
            if transaction is None:
                raise
            if not transaction.committed:
                try:
                    transaction.rollback(runner)
                except ManageError as rollback_error:
                    raise rollback_error from exc
                if prior:
                    try:
                        _restore_running(
                            runner,
                            units,
                            prior,
                            (lambda: _restored_gateway_ready(data, root, runner)) if selected_gateway else None,
                        )
                    except ManageError as restore_error:
                        raise restore_error from exc
            raise _executed_error(exc) from exc
    result["activated"] = report["state"] != "current"
    if transaction is not None and transaction.cleanup_retained:
        result["recovery_artifact_retained"] = True
    return result


def up(manifest_path: str | Path, target: Target, *, apply: bool = False, runner: Runner | None = None, unit_root: str | Path = "/etc/systemd/system") -> dict[str, Any]:
    """Activate and start one role; ordinary one-role updates stay strict."""
    _require_supported_platform()
    data = read_manifest(manifest_path)
    targets = _selected_targets(data, target)
    result = _up_selected(data, targets, apply=apply, upgrade=False, runner=runner, unit_root=unit_root, action="up")
    result["target"] = target.text()
    return result


def up_many(manifest_path: str | Path, targets: tuple[Target, ...], *, upgrade: bool = False, apply: bool = False, runner: Runner | None = None, unit_root: str | Path = "/etc/systemd/system") -> dict[str, Any]:
    """Explicitly coordinate a declared role set, optionally changing artifacts.

    ``upgrade`` is intentionally separate from ordinary updates. Changed native
    or edge artifacts must use versioned new paths while the prior ExecStart
    paths still verify against the active record, so rollback remains possible.
    """
    _require_supported_platform()
    data = read_manifest(manifest_path)
    selected = _selected_targets(data, targets)
    return _up_selected(data, selected, apply=apply, upgrade=upgrade, runner=runner, unit_root=unit_root, action="up-many")


def down(manifest_path: str | Path, target: Target, *, apply: bool = False, runner: Runner | None = None, unit_root: str | Path = "/etc/systemd/system") -> dict[str, Any]:
    """Stop/disable one owned role without changing public configuration."""
    _require_supported_platform()
    data = read_manifest(manifest_path)
    _targets(data, target)
    root = Path(data["config_root"])
    report = _inspection_plan(data)
    units = _units(target, start=False)
    result: dict[str, Any] = {"schema": "anvil-connect.manage/v1", "action": "down", "target": target.text(), "applied": bool(apply), "plan": report, "units": list(units)}
    if not apply:
        return result
    with _deployment_lock(root):
        if _inspection_plan(data)["state"] != "current":
            raise ManageError("refusing lifecycle mutation while generated configuration drifts")
        digests = _verified_binaries(data, target)
        _bound_active(data, target, digests, allow_unpinned_gateway=target.kind == "gateway")
        sources = _unit_sources(root)
        for unit in units:
            _verify_unit(Path(unit_root), unit, sources.get(unit))
            _unit_metadata(runner, Path(unit_root), (unit,), present=True)
        for unit in units:
            _action(runner, (_SYSTEMCTL, "disable", "--now", unit), _SYSTEMD_TIMEOUT, "managed unit failed to stop")
    return result


def _unit_status(runner: Runner | None, units: tuple[str, ...]) -> list[dict[str, Any]]:
    result = []
    for unit in units:
        observed = _run(runner, (_SYSTEMCTL, "show", "--property=Id,ActiveState,SubState,UnitFileState", unit), _VALIDATE_TIMEOUT)
        fields: dict[str, str] = {}
        if observed.returncode == 0:
            fields = dict(line.split("=", 1) for line in observed.stdout.decode("utf-8", "replace").splitlines() if "=" in line)
        result.append({"unit": unit, "available": observed.returncode == 0, "active": fields.get("ActiveState", "unknown"), "substate": fields.get("SubState", "unknown"), "enabled": fields.get("UnitFileState", "unknown")})
    return result


def status(manifest_path: str | Path, target: Target | None = None, *, runner: Runner | None = None) -> dict[str, Any]:
    """Return bounded unit metadata and renderer drift, without a readiness claim."""
    _require_supported_platform()
    data = read_manifest(manifest_path)
    selected = _targets(data, target)
    return {"schema": "anvil-connect.manage/v1", "action": "status", "applied": False, "plan": _inspection_plan(data), "targets": [{"target": item.text(), "units": _unit_status(runner, _units(item))} for item in selected]}


def doctor(manifest_path: str | Path, target: Target | None = None, *, runner: Runner | None = None) -> dict[str, Any]:
    """Combine no-write validation, drift inspection, and supervisor metadata."""
    _require_supported_platform()
    checked = validate(manifest_path, target, runner=runner)
    observed = status(manifest_path, target, runner=runner)
    return {"schema": "anvil-connect.manage/v1", "action": "doctor", "applied": False, "validation": checked, "status": observed}


def logs(manifest_path: str | Path, target: Target, *, tail: int = 200, runner: Runner | None = None) -> dict[str, Any]:
    """Return metadata only: journal text is not safe to expose generically."""
    _require_supported_platform()
    if not isinstance(tail, int) or not 1 <= tail <= 200:
        raise ManageError("log tail must be between 1 and 200")
    data = read_manifest(manifest_path)
    _targets(data, target)
    events = []
    for unit in _units(target):
        observed = _run(runner, (_JOURNALCTL, "--no-pager", "--output=json", "--lines=" + str(tail), "--unit", unit), _VALIDATE_TIMEOUT)
        events.append({"unit": unit, "available": observed.returncode == 0, "bytes": len(observed.stdout) + len(observed.stderr), "lines_requested": tail})
    return {"schema": "anvil-connect.manage/v1", "action": "logs", "target": target.text(), "applied": False, "events": events}


def _current(data: dict[str, Any]) -> None:
    require_isolated(data)
    if plan(data, data["config_root"])["state"] != "current":
        raise ManageError("native authority command requires the current owned generation")


@contextmanager
def _temporary_declaration(data: dict[str, Any], target: Target) -> Iterator[Path]:
    """Materialize the exact rendered public declaration for bootstrap only."""
    require_isolated(data)
    generated = render_config(data)
    with tempfile.TemporaryDirectory(prefix="anvil-connect-init-") as temporary:
        root = Path(temporary)
        _materialize(generated["files"], root)
        _make_public(root)
        yield _config_path({**data, "config_root": str(root)}, target)


def _native_verified(data: dict[str, Any]) -> str:
    return _digest(Path(data["binary"]))


def native_init(manifest_path: str | Path, target: Target, *, bundle: str | Path | None = None, apply: bool = False, runner: Runner | None = None) -> dict[str, Any]:
    """Initialize a declaration before service activation, using no active root."""
    _require_supported_platform()
    data = read_manifest(manifest_path)
    require_isolated(data)
    _targets(data, target)
    if target.kind == "client" or (target.kind == "gateway" and bundle is not None) or (target.kind == "connector" and bundle is None):
        raise ManageError("initialization requires gateway without a bundle or connector with a private bundle")
    # Validation and binary pinning precede all native state changes. This has
    # no dependency on an already running Caddy/Authelia/gateway stack.
    checked = _validate_data(data, target, runner)
    args = [data["binary"], "init", "--mode", _mode(target)]
    if bundle is not None:
        value = Path(bundle)
        if not value.is_absolute() or str(value) != str(bundle):
            raise ManageError("private enrollment bundle path is invalid")
        args.extend(("--bundle", str(value)))
    result = {"schema": "anvil-connect.manage/v1", "action": "init", "target": target.text(), "applied": bool(apply), "native_sha256": checked["digests"]["native"]}
    if apply:
        with _temporary_declaration(data, target) as config:
            _action(runner, tuple([*args, "--config", str(config)]), _SYSTEMD_TIMEOUT, "native initialization failed", _target_identity(data, target))
    return result


def _closed_identity(raw: bytes) -> dict[str, Any]:
    value = _strict_json(raw, "native identity output is invalid")
    allowed = {"id", "status", "fingerprint", "epoch", "generation", "resources"}
    if set(value) != allowed or not isinstance(value["id"], str) or _ID.fullmatch(value["id"]) is None or not isinstance(value["status"], str):
        raise ManageError("native identity output is invalid")
    # ConnectorIdentity reports the local connector-state lifecycle.  A
    # completed local enrollment is "enrolled"; it does not assert that a
    # gateway operator has approved the installation for any resource.
    if (value["status"] not in {"pending", "enrolled"}
            or not isinstance(value["fingerprint"], str) or len(value["fingerprint"]) != 43
            or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for char in value["fingerprint"])
            or not isinstance(value["epoch"], str) or len(value["epoch"]) != 64 or any(char not in "0123456789abcdef" for char in value["epoch"])
            or isinstance(value["generation"], bool) or not isinstance(value["generation"], int) or value["generation"] < 1
            or not isinstance(value["resources"], list) or not value["resources"] or len(value["resources"]) > 64
            or any(not isinstance(item, str) or _ID.fullmatch(item) is None for item in value["resources"]) or len(set(value["resources"])) != len(value["resources"])):
        raise ManageError("native identity output is invalid")
    return value


def identity(manifest_path: str | Path, target: Target, *, runner: Runner | None = None) -> dict[str, Any]:
    """Read a closed public connector identity through the native owner command."""
    _require_supported_platform()
    if target.kind != "connector":
        raise ManageError("identity is defined only for a connector")
    data = read_manifest(manifest_path)
    _targets(data, target)
    native_digest = _native_verified(data)
    # Enrollment deliberately precedes activation. Read the declared persisted
    # identity using a temporary public declaration, without creating an active
    # generation or requiring approval of the identity we are trying to inspect.
    with _temporary_declaration(data, target) as config:
        result = _run(runner, (data["binary"], "identity", "--config", str(config)), _VALIDATE_TIMEOUT, _target_identity(data, target))
    _fail(result, "native identity read failed")
    observed = _closed_identity(result.stdout)
    connector = next(item for item in data["connectors"] if item["id"] == target.name)
    expected_resources = sorted(resource["envelope"]["rule"]["id"] for resource in connector["resources"])
    if observed["id"] != target.name or observed["resources"] != expected_resources:
        raise ManageError("native identity does not match its declared connector")
    return {"schema": "anvil-connect.manage/v1", "action": "identity", "target": target.text(), "applied": False, "native_sha256": native_digest, "identity": observed}


def _admin_preview(request: Path) -> dict[str, str]:
    raw = _read_regular(request, 32 * 1024)
    if raw is None:
        raise ManageError("administrative request is unavailable")
    value = _strict_json(raw, "administrative request is invalid")
    allowed = {"operation", "principal", "grants", "disabled", "key_id", "installation", "role", "resources", "lifetime_seconds", "fingerprint", "issuer", "subject"}
    operation = value.get("operation")
    operations = {"status", "principal-set", "api-key-issue", "api-key-revoke", "invite", "approve", "installation-revoke", "installation-status", "human-set", "authority-reset"}
    if set(value) - allowed or not isinstance(operation, str) or operation not in operations:
        raise ManageError("administrative request is invalid")
    fingerprint = value.get("fingerprint")
    if fingerprint is not None and (not isinstance(fingerprint, str) or len(fingerprint) != 43 or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for char in fingerprint)):
        raise ManageError("administrative request is invalid")
    if operation == "approve" and fingerprint is None:
        raise ManageError("administrative request is invalid")
    scope = "authority" if operation == "authority-reset" else ("installation" if operation in {"invite", "approve", "installation-revoke", "installation-status"} else ("api-key" if operation.startswith("api-key") else "principal"))
    return {"operation": operation, "scope": scope}


def admin(manifest_path: str | Path, *, request_path: str | Path, output_path: str | Path | None = None, apply: bool = False, runner: Runner | None = None) -> dict[str, Any]:
    """Call the derived same-user gateway admin socket; never accepts a socket."""
    _require_supported_platform()
    data = read_manifest(manifest_path)
    _current(data)
    native_digest = _native_verified(data)
    _bound_active(data, Target("gateway"), _verified_binaries(data, Target("gateway")))
    request = Path(request_path)
    if not request.is_absolute() or str(request) != str(request_path):
        raise ManageError("administrative request path is invalid")
    preview = _admin_preview(request)
    args = [data["binary"], "admin", "--socket", str(Path(data["gateway"]["state_directory"]) / "admin.sock"), "--request", str(request)]
    if output_path is not None:
        output = Path(output_path)
        if not output.is_absolute() or str(output) != str(output_path):
            raise ManageError("administrative output path is invalid")
        args.extend(("--output", str(output)))
    result = {"schema": "anvil-connect.manage/v1", "action": "admin", "applied": bool(apply), "native_sha256": native_digest, **preview}
    if apply:
        _action(runner, tuple(args), _SYSTEMD_TIMEOUT, "native administrative operation failed", _role_service_identity(data, "gateway"))
    return result


def keygen(manifest_path: str | Path, target: Target, *, output_path: str | Path, apply: bool = False, runner: Runner | None = None) -> dict[str, Any]:
    """Create one local client key through the native private-output contract."""
    _require_supported_platform()
    if target.kind != "client":
        raise ManageError("key generation is defined only for a local client")
    data = read_manifest(manifest_path)
    _targets(data, target)
    _current(data)
    native_digest = _native_verified(data)
    _bound_active(data, target, {"native": native_digest})
    output = Path(output_path)
    if not output.is_absolute() or str(output) != str(output_path):
        raise ManageError("private key output path is invalid")
    result = {"schema": "anvil-connect.manage/v1", "action": "keygen", "target": target.text(), "applied": bool(apply), "native_sha256": native_digest}
    if apply:
        _action(runner, (data["binary"], "keygen", "--output", str(output)), _SYSTEMD_TIMEOUT, "native client key generation failed", _target_identity(data, target))
    return result
