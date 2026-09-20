"""Install and operate the pinned Pi Web session UI as one managed loopback service.

Pi Web is the third-party ``@agegr/pi-web`` browser UI over the operator's
existing local Pi sessions. The service is deliberately narrow: one pinned npm
install below the operator home, one fixed systemd unit bound to loopback, and
no runtime state inside this repository. Reverse-proxy exposure is owned by
Anvil Connect, whose connector reaches the origin on the same host, so a wider
bind is not part of this contract. Provider credentials stay in Pi's own
credential storage; this module never reads, copies, or prints them.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat as stat_module
import subprocess
import sys
import tarfile
import time
import uuid
import urllib.error
import urllib.request
from typing import Callable, Mapping

from .. import paths

PACKAGE = "@agegr/pi-web"
PACKAGE_BIN = "node_modules/@agegr/pi-web/bin/pi-web.js"
UNIT_NAME = "anvil-pi-web.service"
DEFAULT_VERSION = "0.9.0"
DEFAULT_PORT = 30141
DEFAULT_IDLE_TIMEOUT_MS = 600_000
MAX_IDLE_TIMEOUT_MS = 2_147_483_647
MIN_NODE = (22, 19, 0)
MAX_ALLOWED_HOSTS = 8
_COMMAND_TIMEOUT_SECONDS = 900
_OUTPUT_LIMIT = 16 * 1024
_PROBE_TIMEOUT_SECONDS = 10
_PROBE_ATTEMPTS = 8
_PROBE_RETRY_SECONDS = 2.0

_PIN_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")
_HOSTNAME_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_BRIDGE_ORIGIN_RE = re.compile(
    r"https://[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::([1-9][0-9]{0,4}))?"
)


def _valid_host_literal(value: str) -> bool:
    """Accept one exact IP literal or DNS host name, never a pattern or port."""
    if "*" in value:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        pass
    return _HOSTNAME_RE.fullmatch(value) is not None


def _valid_bridge_origin(value: object) -> bool:
    if type(value) is not str:
        return False
    matched = _BRIDGE_ORIGIN_RE.fullmatch(value)
    return matched is not None and (matched.group(1) is None or int(matched.group(1)) <= 65535)


class PiWebError(ValueError):
    """The Pi Web service configuration, install, or state is not usable."""


def default_config_path() -> Path:
    """Conventional operator-home configuration for the managed session UI."""
    return Path(paths.config_path("workbench", "pi-web.json"))


def default_install_root() -> Path:
    return Path(paths.config_path("pi-web"))


def _absolute_path(value: object, label: str) -> Path:
    if type(value) is not str:
        raise PiWebError(f"{label} must be an absolute private path")
    path = Path(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path == Path("/")
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise PiWebError(f"{label} must be a safe absolute private path")
    return path


def _integer(value: object, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise PiWebError(f"{label} is out of range")
    return value


@dataclass(frozen=True)
class PiWebConfig:
    """One reviewed, pinned Pi Web service declaration."""

    version: str = DEFAULT_VERSION
    port: int = DEFAULT_PORT
    hostname: str = "127.0.0.1"
    allowed_hosts: tuple[str, ...] = ()
    idle_timeout_ms: int = DEFAULT_IDLE_TIMEOUT_MS
    password_env_file: Path | None = None
    service_user: str | None = None
    install_root: Path | None = None
    node_path: str | None = None
    bridge_source: Path | None = None
    bridge_parent_origin: str | None = None
    bridge_token_env_file: Path | None = None


def pi_web_config(value: Mapping[str, object]) -> PiWebConfig:
    """Parse only the declared Pi Web configuration fields, strictly."""
    if not isinstance(value, Mapping):
        raise PiWebError("Pi Web configuration must be an object")
    declared = value.get("pi_web", value)
    if not isinstance(declared, Mapping):
        raise PiWebError("Pi Web configuration must be an object")
    allowed = {
        "version", "port", "hostname", "allowed_hosts", "idle_timeout_ms",
        "password_env_file", "service_user", "install_root", "node_path",
        "bridge_source", "bridge_parent_origin", "bridge_token_env_file",
    }
    if set(declared) - allowed:
        raise PiWebError("Pi Web configuration has unsupported fields")
    version = declared.get("version", DEFAULT_VERSION)
    if type(version) is not str or not _PIN_RE.fullmatch(version):
        raise PiWebError("pi_web.version must be one exact pinned release, not a range")
    hostname = declared.get("hostname", "127.0.0.1")
    if type(hostname) is not str or hostname == "localhost" or not _valid_host_literal(hostname):
        raise PiWebError("pi_web.hostname must be an exact loopback IP or resolvable host name")
    if hostname != "127.0.0.1":
        # The Connect connector reaches this origin on the same host; a wider
        # bind is not part of the managed contract.
        raise PiWebError("pi_web.hostname must be the loopback address 127.0.0.1 for the managed service")
    hosts = declared.get("allowed_hosts", ())
    if not isinstance(hosts, (list, tuple)) or len(hosts) > MAX_ALLOWED_HOSTS:
        raise PiWebError("pi_web.allowed_hosts must be a list of at most 8 exact host names")
    for entry in hosts:
        if type(entry) is not str or not _valid_host_literal(entry):
            raise PiWebError("pi_web.allowed_hosts entries must be exact host names without ports or wildcards")
    idle = _integer(
        declared.get("idle_timeout_ms", DEFAULT_IDLE_TIMEOUT_MS),
        "pi_web.idle_timeout_ms", 0, MAX_IDLE_TIMEOUT_MS,
    )
    password_file = declared.get("password_env_file")
    if password_file is not None:
        password_file = _absolute_path(password_file, "pi_web.password_env_file")
    service_user = declared.get("service_user")
    if service_user is not None:
        if type(service_user) is not str or not _USER_RE.fullmatch(service_user):
            raise PiWebError("pi_web.service_user must be a plain POSIX account name")
    install_root = declared.get("install_root")
    if install_root is not None:
        install_root = _absolute_path(install_root, "pi_web.install_root")
    node_path = declared.get("node_path")
    if node_path is not None:
        node_path_value = _absolute_path(node_path, "pi_web.node_path")
        if not node_path_value.is_file():
            raise PiWebError(f"declared pi_web.node_path does not exist: {node_path_value}")
        node_path = str(node_path_value)
    bridge_source = declared.get("bridge_source")
    bridge_parent_origin = declared.get("bridge_parent_origin")
    bridge_token_env_file = declared.get("bridge_token_env_file")
    bridge_values = (bridge_source, bridge_parent_origin, bridge_token_env_file)
    if any(value is not None for value in bridge_values):
        if any(value is None for value in bridge_values):
            raise PiWebError("Pi Web bridge source, parent origin, and token reference must be declared together")
        bridge_source = _absolute_path(bridge_source, "pi_web.bridge_source")
        bridge_token_env_file = _absolute_path(bridge_token_env_file, "pi_web.bridge_token_env_file")
        if not _valid_bridge_origin(bridge_parent_origin):
            raise PiWebError("pi_web.bridge_parent_origin must be one exact HTTPS origin")
    return PiWebConfig(
        version=version,
        port=_integer(declared.get("port", DEFAULT_PORT), "pi_web.port", 1024, 65535),
        hostname=hostname,
        allowed_hosts=tuple(hosts),
        idle_timeout_ms=idle,
        password_env_file=password_file,
        service_user=service_user,
        install_root=install_root,
        node_path=node_path,
        bridge_source=bridge_source,
        bridge_parent_origin=bridge_parent_origin,
        bridge_token_env_file=bridge_token_env_file,
    )


def load_config(path: str | os.PathLike[str] | None = None, *, required: bool) -> PiWebConfig:
    """Load the explicit or conventional Pi Web configuration, if present."""
    source = Path(path).expanduser() if path is not None else default_config_path()
    if not source.is_file():
        if required:
            raise PiWebError(
                "Pi Web configuration is required; create "
                f"{default_config_path()} with the pinned version and allowed hosts"
            )
        return pi_web_config({})
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PiWebError(f"cannot read Pi Web configuration {source}: {exc}") from exc
    return pi_web_config(value)


def _bounded_run(
    run: Callable[..., subprocess.CompletedProcess[str]], argv: list[str], *, cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = run(argv, check=False, text=True, capture_output=True, timeout=_COMMAND_TIMEOUT_SECONDS, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PiWebError(f"cannot run {argv[0]} safely") from exc
    stdout = (result.stdout or "")[:_OUTPUT_LIMIT]
    stderr = (result.stderr or "")[:_OUTPUT_LIMIT]
    if result.returncode:
        detail = stderr.strip() or stdout.strip() or f"exit {result.returncode}"
        raise PiWebError(f"{argv[0]} failed: {detail}")
    return result


def _node_details(
    node_path: str | None,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[str, tuple[int, int, int]]:
    """Resolve one Node.js runtime and prove the Pi Web engine floor."""
    resolved = node_path or shutil.which("node") or ""
    if not resolved or not Path(resolved).is_file():
        raise PiWebError("Node.js was not found on PATH; declare pi_web.node_path in the configuration")
    result = _bounded_run(run, [resolved, "--version"])
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", (result.stdout or "").strip())
    if match is None:
        raise PiWebError(f"cannot parse the installed Node.js version near {resolved}")
    version = tuple(int(part) for part in match.groups())
    if version < MIN_NODE:
        required = ".".join(str(part) for part in MIN_NODE)
        raise PiWebError(f"Node.js {required} or newer is required; found v{'.'.join(map(str, version))}")
    return resolved, version  # type: ignore[return-value]


def npm_script_for(node_path: str) -> Path:
    """Resolve the npm CLI script shipped beside one Node.js runtime.

    Running ``node <npm-cli.js>`` directly keeps the pinned npm and the pinned
    runtime paired, without depending on the invoking user's PATH or npm's
    ``env node`` shebang inside a root session.
    """
    runtime = Path(node_path).resolve()
    candidate = runtime.parent.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    if not candidate.is_file():
        raise PiWebError(
            f"cannot find the npm CLI beside {runtime}; install npm with the pinned Node.js runtime"
        )
    return candidate


def _service_identity(user: str) -> tuple[int, str]:
    """Resolve one existing POSIX account for the managed service."""
    try:
        record = __import__("pwd").getpwnam(user)
    except KeyError as exc:
        raise PiWebError(f"configured pi_web.service_user does not exist: {user}") from exc
    home = record.pw_dir
    if not home or not home.startswith("/"):
        raise PiWebError(f"configured pi_web.service_user has no absolute home directory: {user}")
    return record.pw_uid, home


def _unit_value(path: Path) -> str:
    """Encode a filesystem path as one systemd unit value, never raw text."""
    result: list[str] = []
    for byte in os.fsencode(str(path)):
        if 48 <= byte <= 57 or 65 <= byte <= 90 or 97 <= byte <= 122 or byte in b"/._-":
            result.append(chr(byte))
        else:
            result.append(f"\\x{byte:02x}")
    return "".join(result)


def install_root_for(config: PiWebConfig) -> Path:
    return config.install_root or default_install_root()


def _password_file_proof(path: Path) -> None:
    try:
        details = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise PiWebError(f"declared pi_web.password_env_file does not exist: {path}") from exc
    if not stat_module.S_ISREG(details.st_mode):
        raise PiWebError("pi_web.password_env_file must be a regular file")
    if details.st_mode & 0o077:
        raise PiWebError("pi_web.password_env_file must not be group- or world-readable")


def unit_content(
    config: PiWebConfig,
    *,
    node_path: str,
    entry_script: Path,
    user: str,
    user_home: Path,
    node_bin_dir: Path,
) -> str:
    """Render the exact reviewed systemd unit for the managed session UI."""
    if config.hostname != "127.0.0.1":
        raise PiWebError("the managed Pi Web unit binds only the loopback address")
    lines = [
        "[Unit]",
        "Description=Anvil Workbench Pi Web session UI",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"User={user}",
        f"ExecStart={_unit_value(Path(node_path))} {_unit_value(entry_script)} "
        f"--port {config.port} --hostname 127.0.0.1 --no-open",
        f"Environment=HOME={_unit_value(user_home)}",
        f'Environment="PATH={_unit_value(node_bin_dir)}:/usr/local/bin:/usr/bin:/bin"',
        "Environment=PI_WEB_NO_OPEN=1",
        "Environment=PI_WEB_SKIP_VERSION_CHECK=1",
    ]
    if config.allowed_hosts:
        lines.append(f"Environment=PI_WEB_ALLOWED_HOSTS={','.join(config.allowed_hosts)}")
    lines.append(f"Environment=PI_WEB_IDLE_TIMEOUT_MS={config.idle_timeout_ms}")
    if config.password_env_file is not None:
        lines.append(f"EnvironmentFile={_unit_value(config.password_env_file)}")
    if config.bridge_token_env_file is not None:
        lines.append(f"EnvironmentFile={_unit_value(config.bridge_token_env_file)}")
    lines.extend(
        [
            "NoNewPrivileges=yes",
            "PrivateTmp=yes",
            "ProtectSystem=full",
            "UMask=0077",
            "Restart=on-failure",
            "RestartSec=5s",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )
    return "\n".join(lines)


def _build_env(*, node: str, user_home: str, parent_origin: str | None = None) -> list[str]:
    """Return the deliberately small environment used for an unprivileged build."""
    values = [
        "env", "-i", f"HOME={user_home}",
        f"PATH={Path(node).parent}:/usr/local/bin:/usr/bin:/bin",
        "NPM_CONFIG_USERCONFIG=/dev/null",
    ]
    if parent_origin is not None:
        values.append(f"NEXT_PUBLIC_WORKBENCH_ORIGIN={parent_origin}")
    return values


def plan(config: PiWebConfig, *, node_path: str | None = None) -> dict[str, object]:
    """Read-only exact plan for the managed install."""
    root = install_root_for(config)
    version_dir = root / config.version
    node, node_version = _node_details(node_path or config.node_path, run=subprocess.run)
    npm_script = npm_script_for(node)
    if config.service_user is None:
        raise PiWebError("pi_web.service_user is required to install the managed service")
    _, user_home = _service_identity(config.service_user)
    if config.password_env_file is not None:
        _password_file_proof(config.password_env_file)
    bridge = config.bridge_source is not None
    entry = version_dir / ("bin/pi-web.js" if bridge else PACKAGE_BIN)
    prefix = ["runuser", "-u", config.service_user, "--", *_build_env(
        node=node, user_home=user_home, parent_origin=config.bridge_parent_origin if bridge else None,
    )]
    commands: list[list[str]]
    if bridge:
        commands = [
            ["verify-pinned-source", str(config.bridge_source)],
            prefix + [node, str(npm_script), "ci"],
            prefix + [node, str(npm_script), "run", "build"],
        ]
    else:
        commands = [prefix + [node, str(npm_script), "install", "--prefix", str(version_dir), f"{PACKAGE}@{config.version}"]]
    commands.extend([
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", UNIT_NAME],
        ["systemctl", "restart", UNIT_NAME],
    ])
    return {
        "package": f"{PACKAGE}@{config.version}",
        "version": config.version,
        "node": {"path": node, "version": ".".join(str(part) for part in node_version)},
        "npm_script": str(npm_script),
        "install_root": str(version_dir),
        "entry_script": str(entry),
        "bridge": bridge,
        "port": config.port,
        "hostname": config.hostname,
        "allowed_hosts": list(config.allowed_hosts),
        "unit_path": str(Path("/etc/systemd/system") / UNIT_NAME),
        "commands": commands,
    }


def probe(
    port: int,
    *,
    opener: Callable[[str], object] | None = None,
    attempts: int = _PROBE_ATTEMPTS,
    retry_seconds: float = _PROBE_RETRY_SECONDS,
) -> dict[str, object]:
    """One bounded loopback readiness probe of the managed service."""
    url = f"http://127.0.0.1:{port}/"

    def _default_opener(target: str) -> object:
        with urllib.request.urlopen(target, timeout=_PROBE_TIMEOUT_SECONDS) as response:
            return getattr(response, "status", None)

    fetch = opener or _default_opener
    last_error: str | None = None
    for attempt in range(attempts):
        try:
            fetch(url)
            return {"ready": True, "url": url, "attempts": attempt + 1}
        except (urllib.error.URLError, OSError) as exc:
            last_error = str(exc)
            if attempt + 1 < attempts:
                time.sleep(retry_seconds)
    return {"ready": False, "url": url, "attempts": attempts, "error": last_error}


def _read_installed_version(root: Path) -> str | None:
    manifest = root / "install-manifest.json"
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = value.get("version") if isinstance(value, dict) else None
    return version if type(version) is str else None


def service_state(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, object]:
    """Read-only systemd state for the fixed managed unit."""
    properties = ("ActiveState", "SubState", "MainPID", "UnitFileState", "ExecMainStartTimestamp")
    result = _bounded_run(run, ["systemctl", "show", UNIT_NAME, "--no-pager", *[
        f"--property={name}" for name in properties
    ]])
    state: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            state[key.strip()] = value.strip()
    return {
        "unit": UNIT_NAME,
        "active": state.get("ActiveState", "unknown"),
        "sub": state.get("SubState", "unknown"),
        "pid": state.get("MainPID", "0"),
        "enabled": state.get("UnitFileState", "unknown"),
        "started": state.get("ExecMainStartTimestamp", ""),
    }


def status(config: PiWebConfig) -> dict[str, object]:
    """Read-only service state, installed pin, and loopback readiness."""
    root = install_root_for(config)
    state = service_state()
    return {
        **state,
        "configured_version": config.version,
        "installed_version": _read_installed_version(root),
        "port": config.port,
        "probe": probe(config.port, attempts=1),
    }


def logs(config: PiWebConfig, *, tail: int = 200) -> str:
    """Bounded journal tail for the managed unit."""
    result = _bounded_run(subprocess.run, ["journalctl", "-u", UNIT_NAME, "--no-pager", "-n", str(tail)])
    return result.stdout or ""


_MAX_BRIDGE_SOURCE_FILES = 50_000
_MAX_BRIDGE_SOURCE_BYTES = 512 * 1024 * 1024
_MAX_BRIDGE_ARTIFACT_FILES = 100_000
_MAX_BRIDGE_ARTIFACT_BYTES = 3 * 1024 * 1024 * 1024


def _regular_file(path: Path) -> bool:
    try:
        return stat_module.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _bridge_origin(value: object) -> str:
    if not _valid_bridge_origin(value):
        raise PiWebError("the Pi Web bridge build requires one exact HTTPS parent origin")
    return str(value)


def _git_result(
    run: Callable[..., subprocess.CompletedProcess[str]], argv: list[str], source: Path,
) -> subprocess.CompletedProcess[str]:
    try:
        result = run(argv, cwd=source, check=False, text=True, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PiWebError("cannot verify the pinned Pi Web source") from exc
    if result.returncode:
        raise PiWebError("the Pi Web bridge source does not match the reviewed pin")
    return result


def _verify_bridge_source(source: Path, *, run: Callable[..., subprocess.CompletedProcess[str]]) -> dict[str, str]:
    """Prove one complete, clean checkout before copying any source bytes."""
    manifest = bridge_manifest()
    source = Path(source)
    package_json = source / "package.json"
    app_shell = source / "components" / "AppShell.tsx"
    if not source.is_dir() or not package_json.is_file() or not app_shell.is_file():
        raise PiWebError("the Pi Web bridge source tree is incomplete")
    if (hashlib.sha256(package_json.read_bytes()).hexdigest() != manifest["package_json_sha256"]
            or hashlib.sha256(app_shell.read_bytes()).hexdigest() != manifest["app_shell_sha256"]):
        raise PiWebError("the Pi Web bridge source does not match the reviewed pin")
    revision = _git_result(run, ["git", "rev-parse", "HEAD"], source)
    status = _git_result(run, ["git", "status", "--porcelain=v1", "--untracked-files=all"], source)
    if (revision.stdout or "").strip() != manifest["source_commit"] or (status.stdout or "").strip():
        raise PiWebError("the Pi Web bridge source must be a clean complete checkout")
    return manifest


def _safe_bridge_relative(name: str) -> Path:
    relative = Path(name)
    if (
        not name
        or relative.is_absolute()
        or ".." in relative.parts
        or any(part in {"", ".", ".git", ".npmrc"} for part in relative.parts)
    ):
        raise PiWebError("the pinned Pi Web source has an unsafe tracked path")
    return relative


def _archive_tracked_bridge_source(
    source: Path,
    destination: Path,
    *,
    manifest: Mapping[str, str],
    run: Callable[..., subprocess.CompletedProcess[str]],
    chown: Callable[[Path, int, int], None],
    uid: int,
) -> None:
    """Extract only immutable blobs from the reviewed commit into fresh staging.

    ``git archive`` reads the named commit object rather than the mutable
    checkout files.  This makes a post-validation edit unable to alter build
    inputs, while the strict extractor keeps ignored files, hooks, and links
    out of the service-owned tree.
    """
    if destination.exists():
        raise PiWebError("the Pi Web bridge staging directory already exists")
    archive = destination.with_name(f".{destination.name}.source-{uuid.uuid4().hex}.tar")
    try:
        _git_result(
            run,
            ["git", "archive", "--format=tar", f"--output={archive}", manifest["source_commit"]],
            source,
        )
        destination.mkdir(mode=0o755)
        chown(destination, uid, uid)
        copied = 0
        count = 0
        with tarfile.open(archive, mode="r:") as bundle:
            for member in bundle.getmembers():
                relative = _safe_bridge_relative(member.name.rstrip("/"))
                count += 1
                if count > _MAX_BRIDGE_SOURCE_FILES:
                    raise PiWebError("the pinned Pi Web source has an unsafe tracked-file inventory")
                target = destination / relative
                if member.isdir():
                    target.mkdir(mode=0o755, parents=True, exist_ok=True)
                    chown(target, uid, uid)
                    continue
                if not member.isfile() or member.size < 0:
                    raise PiWebError("the pinned Pi Web source contains a non-regular tracked file")
                copied += member.size
                if copied > _MAX_BRIDGE_SOURCE_BYTES:
                    raise PiWebError("the pinned Pi Web source exceeds the staging byte limit")
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                payload = bundle.extractfile(member)
                if payload is None:
                    raise PiWebError("cannot read the pinned Pi Web source archive")
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o755 if member.mode & 0o111 else 0o644,
                )
                try:
                    with os.fdopen(descriptor, "wb", closefd=False) as output:
                        shutil.copyfileobj(payload, output, length=64 * 1024)
                    os.fsync(descriptor)
                finally:
                    payload.close()
                    os.close(descriptor)
                chown(target, uid, uid)
        for directory, _, _ in os.walk(destination, followlinks=False):
            chown(Path(directory), uid, uid)
    except (OSError, tarfile.TarError) as exc:
        raise PiWebError("cannot copy the pinned Pi Web source safely") from exc
    finally:
        try:
            archive.unlink()
        except FileNotFoundError:
            pass


def _artifact_tree_sha256(root: Path) -> str:
    """Boundedly hash the promoted artifact, including symlink targets without following them."""
    digest = hashlib.sha256()
    count = 0
    total = 0
    for directory, names, files in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in sorted([*names, *files]):
            path = base / name
            relative = path.relative_to(root).as_posix().encode("utf-8", "surrogateescape")
            details = os.lstat(path)
            count += 1
            if count > _MAX_BRIDGE_ARTIFACT_FILES:
                raise PiWebError("the Pi Web bridge artifact exceeds the file limit")
            if stat_module.S_ISLNK(details.st_mode):
                target = os.readlink(path).encode("utf-8", "surrogateescape")
                after = os.lstat(path)
                if (after.st_dev, after.st_ino, after.st_mode) != (details.st_dev, details.st_ino, details.st_mode):
                    raise PiWebError("the Pi Web bridge artifact changed while hashing")
                digest.update(b"L\0" + relative + b"\0" + target + b"\0")
            elif stat_module.S_ISREG(details.st_mode):
                total += details.st_size
                if total > _MAX_BRIDGE_ARTIFACT_BYTES:
                    raise PiWebError("the Pi Web bridge artifact exceeds the byte limit")
                digest.update(f"F {details.st_mode & 0o777:o} {details.st_size} ".encode() + relative + b"\0")
                try:
                    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                    opened = os.fstat(descriptor)
                    if (
                        not stat_module.S_ISREG(opened.st_mode)
                        or (opened.st_dev, opened.st_ino, opened.st_size) != (details.st_dev, details.st_ino, details.st_size)
                    ):
                        raise PiWebError("the Pi Web bridge artifact changed while hashing")
                    while True:
                        chunk = os.read(descriptor, 64 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                    after = os.fstat(descriptor)
                    if (after.st_dev, after.st_ino, after.st_size) != (opened.st_dev, opened.st_ino, opened.st_size):
                        raise PiWebError("the Pi Web bridge artifact changed while hashing")
                except OSError as exc:
                    raise PiWebError("cannot hash the Pi Web bridge artifact safely") from exc
                finally:
                    try:
                        os.close(descriptor)
                    except (UnboundLocalError, OSError):
                        pass
            elif stat_module.S_ISDIR(details.st_mode):
                digest.update(b"D\0" + relative + b"\0")
            else:
                raise PiWebError("the Pi Web bridge artifact contains an unsafe file")
    return digest.hexdigest()


def _apply_bridge_patch(source: Path, *, run: Callable[..., subprocess.CompletedProcess[str]]) -> None:
    from importlib.resources import files
    patch = files(_BRIDGE_PACKAGE).joinpath(_BRIDGE_DIR, "0.9.0-host-bridge.patch")
    try:
        result = run(["patch", "--batch", "--forward", "-p1", "-i", str(patch)], cwd=source, check=False, text=True, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PiWebError("cannot stage the pinned Pi Web bridge") from exc
    if result.returncode:
        raise PiWebError("cannot apply the pinned Pi Web bridge to the reviewed source")


def _bridge_install_descriptor(*, parent_origin: str, artifact_sha256: str) -> dict[str, object]:
    return {"bridge": bridge_manifest(), "parent_origin": parent_origin, "artifact_sha256": artifact_sha256}


def _build_staged_bridge(
    source: Path, destination: Path, *, node: str, parent_origin: str, service_user: str, user_home: str,
    run: Callable[..., subprocess.CompletedProcess[str]], chown: Callable[[Path, int, int], None], uid: int,
) -> dict[str, object]:
    manifest = _verify_bridge_source(source, run=run)
    _archive_tracked_bridge_source(
        source, destination, manifest=manifest, run=run, chown=chown, uid=uid,
    )
    _apply_bridge_patch(destination, run=run)
    npm = npm_script_for(node)
    prefix = ["runuser", "-u", service_user, "--"]
    _bounded_run(run, prefix + _build_env(node=node, user_home=user_home) + [node, str(npm), "ci"], cwd=destination)
    _bounded_run(run, prefix + _build_env(node=node, user_home=user_home, parent_origin=parent_origin) + [node, str(npm), "run", "build"], cwd=destination)
    entry = destination / "bin" / "pi-web.js"
    if not _regular_file(entry):
        raise PiWebError("the pinned Pi Web bridge did not produce its entry script")
    return _bridge_install_descriptor(parent_origin=parent_origin, artifact_sha256=_artifact_tree_sha256(destination))


def _safe_install_root(root: Path) -> None:
    """Require a non-link, private directory beneath protected ancestors."""
    root = Path(root)
    expected_uid = os.geteuid()
    parent = root.parent
    try:
        parent_details = os.lstat(parent)
    except OSError as exc:
        raise PiWebError("the Pi Web install root parent is unavailable") from exc
    if not stat_module.S_ISDIR(parent_details.st_mode) or stat_module.S_ISLNK(parent_details.st_mode):
        raise PiWebError("the Pi Web install root parent is not protected for this installer")
    if parent_details.st_uid != expected_uid or parent_details.st_mode & 0o022:
        raise PiWebError("the Pi Web install root parent is not protected for this installer")
    try:
        os.mkdir(root, 0o755)
    except FileExistsError:
        pass
    except OSError as exc:
        raise PiWebError("cannot create the protected Pi Web install root") from exc
    try:
        details = os.lstat(root)
    except OSError as exc:
        raise PiWebError("the Pi Web install root is unavailable") from exc
    if (
        not stat_module.S_ISDIR(details.st_mode)
        or stat_module.S_ISLNK(details.st_mode)
        or details.st_uid != expected_uid
        or details.st_mode & 0o022
    ):
        raise PiWebError("the Pi Web install root is not protected for this installer")
    for ancestor in root.parents:
        try:
            ancestor_details = os.lstat(ancestor)
        except OSError as exc:
            raise PiWebError("a Pi Web install root ancestor is unavailable") from exc
        if not stat_module.S_ISDIR(ancestor_details.st_mode) or stat_module.S_ISLNK(ancestor_details.st_mode):
            raise PiWebError("a Pi Web install root ancestor is not protected for this installer")
        if ancestor_details.st_mode & 0o022:
            # A sticky root-owned /tmp remains safe for an already-owned,
            # non-writable private child; any other writable ancestor can
            # replace the configured root between path operations.
            if not (ancestor_details.st_mode & stat_module.S_ISVTX and ancestor_details.st_uid in {0, expected_uid}):
                raise PiWebError("a Pi Web install root ancestor is not protected for this installer")


def _read_regular_private_file(root: Path, name: str) -> bytes | None:
    """Read one regular root child without following a replacement symlink."""
    directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        try:
            before = os.lstat(name, dir_fd=directory)
        except FileNotFoundError:
            return None
        if not stat_module.S_ISREG(before.st_mode):
            raise PiWebError("the Pi Web install metadata is not a regular file")
        descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
        try:
            opened = os.fstat(descriptor)
            if not stat_module.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise PiWebError("the Pi Web install metadata changed while reading")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise PiWebError("cannot read the Pi Web install metadata safely") from exc
    finally:
        os.close(directory)


def _atomic_write_private_file(root: Path, name: str, rendered: bytes, *, mode: int = 0o644) -> None:
    """Atomically replace one regular root child through an opened directory."""
    directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    temporary = f".{name}.{uuid.uuid4().hex}.tmp"
    try:
        try:
            existing = os.lstat(name, dir_fd=directory)
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat_module.S_ISREG(existing.st_mode):
            raise PiWebError("the Pi Web install metadata is not a regular file")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=directory,
        )
        try:
            offset = 0
            while offset < len(rendered):
                offset += os.write(descriptor, rendered[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    except OSError as exc:
        raise PiWebError("cannot write the Pi Web install metadata safely") from exc
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except (FileNotFoundError, OSError):
            pass
        finally:
            os.close(directory)


def _remove_private_file(root: Path, name: str) -> None:
    directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        try:
            details = os.lstat(name, dir_fd=directory)
        except FileNotFoundError:
            return
        if not stat_module.S_ISREG(details.st_mode):
            raise PiWebError("the Pi Web install metadata is not a regular file")
        os.unlink(name, dir_fd=directory)
        os.fsync(directory)
    except OSError as exc:
        raise PiWebError("cannot remove the Pi Web install metadata safely") from exc
    finally:
        os.close(directory)


@dataclass(frozen=True)
class _BridgePromotion:
    version_dir: Path
    backup: Path | None


class PiWebInstaller:
    """One-shot, idempotent, root-gated install of the pinned session UI.

    A bridge artifact is built in a private same-filesystem staging directory.
    The running version is left intact until both the source proof and build
    proof succeed; promotion restores it if the final rename cannot complete.
    """

    def __init__(
        self,
        config: PiWebConfig,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        systemd_root: Path = Path("/etc/systemd/system"),
        geteuid: Callable[[], int] | None = None,
        probe_opener: Callable[[str], object] | None = None,
        node_path: str | None = None,
        platform: str | None = None,
        chown: Callable[[Path, int, int], None] | None = None,
    ) -> None:
        self.config = config
        self._run = run
        self._systemd_root = systemd_root
        self._geteuid = geteuid
        self._probe_opener = probe_opener
        self._node_path = node_path
        self._platform = platform
        self._chown = chown or os.chown

    def _bridge_descriptor(self, version_dir: Path) -> dict[str, object]:
        return _bridge_install_descriptor(
            parent_origin=_bridge_origin(self.config.bridge_parent_origin),
            artifact_sha256=_artifact_tree_sha256(version_dir),
        )

    def _installed_bridge_matches(self, root: Path, version_dir: Path) -> bool:
        try:
            rendered = _read_regular_private_file(root, "install-manifest.json")
            installed = json.loads(rendered) if rendered is not None else None
        except (PiWebError, ValueError, UnicodeDecodeError):
            return False
        try:
            return installed.get("bridge_install") == self._bridge_descriptor(version_dir)
        except PiWebError:
            return False

    def _promote_bridge(self, staged: Path, version_dir: Path) -> _BridgePromotion:
        backup = version_dir.with_name(version_dir.name + ".previous-" + uuid.uuid4().hex)
        previous = version_dir.exists()
        try:
            if previous:
                os.replace(version_dir, backup)
            os.replace(staged, version_dir)
        except OSError as exc:
            if previous and backup.exists() and not version_dir.exists():
                try:
                    os.replace(backup, version_dir)
                except OSError:
                    pass
            raise PiWebError("cannot promote the built Pi Web bridge artifact") from exc
        return _BridgePromotion(version_dir=version_dir, backup=backup if previous else None)

    @staticmethod
    def _restore_promoted_bridge(promotion: _BridgePromotion) -> None:
        try:
            if promotion.version_dir.exists():
                shutil.rmtree(promotion.version_dir)
            if promotion.backup is not None and promotion.backup.exists():
                os.replace(promotion.backup, promotion.version_dir)
        except OSError as exc:
            raise PiWebError("cannot restore the prior Pi Web bridge artifact") from exc

    @staticmethod
    def _finalize_promoted_bridge(promotion: _BridgePromotion) -> None:
        if promotion.backup is None:
            return
        try:
            shutil.rmtree(promotion.backup)
        except OSError as exc:
            raise PiWebError("the prior Pi Web bridge artifact could not be removed") from exc

    def _install_bridge(self, root: Path, version_dir: Path, *, node: str, uid: int, user_home: str) -> tuple[bool, dict[str, object], _BridgePromotion | None]:
        config = self.config
        if config.bridge_source is None or config.service_user is None:
            raise PiWebError("the Pi Web bridge needs a source and service user")
        expected_origin = _bridge_origin(config.bridge_parent_origin)
        if version_dir.is_dir() and _regular_file(version_dir / "bin" / "pi-web.js") and self._installed_bridge_matches(root, version_dir):
            return False, self._bridge_descriptor(version_dir), None
        _safe_install_root(root)
        staged = root / f".{config.version}.bridge-staging-{uuid.uuid4().hex}"
        try:
            descriptor = _build_staged_bridge(
                config.bridge_source, staged, node=node, parent_origin=expected_origin,
                service_user=config.service_user, user_home=user_home, run=self._run,
                chown=self._chown, uid=uid,
            )
            return True, descriptor, self._promote_bridge(staged, version_dir)
        finally:
            if staged.exists():
                shutil.rmtree(staged, ignore_errors=True)

    def _unit_snapshot(self) -> bytes | None:
        target = self._systemd_root / UNIT_NAME
        try:
            details = os.lstat(target)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise PiWebError("cannot read the managed Pi Web unit safely") from exc
        if not stat_module.S_ISREG(details.st_mode):
            raise PiWebError("existing Pi Web unit is not a regular file; refusing to manage it")
        try:
            return target.read_bytes()
        except OSError as exc:
            raise PiWebError("cannot read the managed Pi Web unit safely") from exc

    def _restore_unit_snapshot(self, previous: bytes | None) -> None:
        target = self._systemd_root / UNIT_NAME
        if previous is None:
            try:
                target.unlink()
            except FileNotFoundError:
                return
            return
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temporary, "xb") as output:
                output.write(previous)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _rollback_bridge_install(
        self,
        promotion: _BridgePromotion,
        *,
        root: Path,
        manifest_before: bytes | None,
        unit_before: bytes | None,
        was_active: bool,
    ) -> None:
        """Restore every post-promotion input, attempting each even after a failure."""
        failures: list[str] = []
        for label, action in (
            ("artifact", lambda: self._restore_promoted_bridge(promotion)),
            ("manifest", lambda: _remove_private_file(root, "install-manifest.json") if manifest_before is None else _atomic_write_private_file(root, "install-manifest.json", manifest_before)),
            ("unit", lambda: self._restore_unit_snapshot(unit_before)),
            ("daemon", lambda: _bounded_run(self._run, ["systemctl", "daemon-reload"])),
            ("service", lambda: _bounded_run(self._run, ["systemctl", "restart" if was_active else "stop", UNIT_NAME])),
        ):
            try:
                action()
            except (PiWebError, OSError):
                failures.append(label)
        if failures:
            joined = ", ".join(failures)
            raise PiWebError(f"Pi Web bridge rollback is incomplete ({joined}); retained private recovery state requires inspection")

    def install(self, *, confirm: bool) -> dict[str, object]:
        if not confirm:
            return {"dry_run": True, **plan(self.config, node_path=self._node_path or self.config.node_path)}
        if (self._platform if self._platform is not None else sys.platform) != "linux":
            raise PiWebError("Pi Web service installation is supported only on Linux")
        geteuid = self._geteuid or getattr(os, "geteuid", None)
        if geteuid is None or geteuid() != 0:
            raise PiWebError("Pi Web installation requires root; inspect the dry-run first")
        config = self.config
        if config.service_user is None:
            raise PiWebError("pi_web.service_user is required to install the managed service")
        if config.password_env_file is not None:
            _password_file_proof(config.password_env_file)
        if config.bridge_token_env_file is not None:
            _password_file_proof(config.bridge_token_env_file)
        root = install_root_for(config)
        _safe_install_root(root)
        version_dir = root / config.version
        node, node_version = _node_details(self._node_path or config.node_path, run=self._run)
        uid, user_home = _service_identity(config.service_user)
        bridge = config.bridge_source is not None
        entry = version_dir / ("bin/pi-web.js" if bridge else PACKAGE_BIN)
        artifact_changed = False
        promotion: _BridgePromotion | None = None
        state = service_state(run=self._run)
        was_active = state["active"] == "active"
        manifest_before = _read_regular_private_file(root, "install-manifest.json")
        unit_before = self._unit_snapshot()
        try:
            if bridge:
                artifact_changed, bridge_install, promotion = self._install_bridge(
                    root, version_dir, node=node, uid=uid, user_home=user_home,
                )
            else:
                bridge_install = None
                if not entry.is_file():
                    version_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
                    self._chown(version_dir, uid, uid)
                    npm_script = npm_script_for(node)
                    _bounded_run(self._run, [
                        "runuser", "-u", config.service_user, "--",
                        *_build_env(node=node, user_home=user_home),
                        node, str(npm_script), "install", "--prefix", str(version_dir), f"{PACKAGE}@{config.version}",
                    ])
                    artifact_changed = True
            content = unit_content(
                config, node_path=node, entry_script=entry, user=config.service_user,
                user_home=Path(user_home), node_bin_dir=Path(node).parent,
            )
            if not _regular_file(entry):
                raise PiWebError("the pinned Pi Web package did not produce its entry script")
            self._record_manifest(root, node=node, node_version=node_version, bridge_install=bridge_install)
            unit_changed = self._write_unit(content)
            if unit_changed:
                _bounded_run(self._run, ["systemctl", "daemon-reload"])
            _bounded_run(self._run, ["systemctl", "enable", UNIT_NAME])
            if was_active and not unit_changed and not artifact_changed:
                lifecycle = "unchanged"
            elif was_active:
                _bounded_run(self._run, ["systemctl", "restart", UNIT_NAME])
                lifecycle = "restarted"
            else:
                _bounded_run(self._run, ["systemctl", "start", UNIT_NAME])
                lifecycle = "started"
            readback = service_state(run=self._run)
            if readback["active"] != "active":
                raise PiWebError("the managed Pi Web unit did not reach the active state; inspect the journal")
            proof = probe(config.port, opener=self._probe_opener)
            if proof["ready"] is not True:
                raise PiWebError("the managed Pi Web unit did not pass its loopback readiness probe")
        except Exception as exc:
            if promotion is not None:
                try:
                    self._rollback_bridge_install(
                        promotion, root=root, manifest_before=manifest_before,
                        unit_before=unit_before, was_active=was_active,
                    )
                except PiWebError as rollback_error:
                    raise rollback_error from exc
            raise
        if promotion is not None:
            self._finalize_promoted_bridge(promotion)
        return {
            "installed": True, "version": config.version,
            "unit_path": str(self._systemd_root / UNIT_NAME), "unit_changed": unit_changed,
            "package_installed": artifact_changed, "lifecycle": lifecycle,
            "node": {"path": node, "version": ".".join(str(part) for part in node_version)}, "probe": proof,
        }

    def _record_manifest(
        self, root: Path, *, node: str, node_version: tuple[int, int, int], bridge_install: dict[str, object] | None,
    ) -> None:
        """Record the exact installed pin and output proof without timestamp churn."""
        _safe_install_root(root)
        previous: dict[str, object] = {}
        try:
            rendered_previous = _read_regular_private_file(root, "install-manifest.json")
            candidate = json.loads(rendered_previous) if rendered_previous is not None else None
            if isinstance(candidate, dict):
                previous = candidate
        except (ValueError, UnicodeDecodeError):
            pass
        manifest = {
            "schema": "anvil-serving.pi-web-install/v1", "package": PACKAGE,
            "version": self.config.version, "port": self.config.port, "hostname": self.config.hostname,
            "allowed_hosts": list(self.config.allowed_hosts), "service_user": self.config.service_user,
            "node": {"path": node, "version": ".".join(str(part) for part in node_version)},
            "installed_at": previous.get("installed_at") if type(previous.get("installed_at")) is str else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **({"bridge_install": bridge_install} if bridge_install is not None else {}),
        }
        rendered = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        existing = _read_regular_private_file(root, "install-manifest.json")
        if existing is not None and existing.decode("utf-8") == rendered:
            return
        _atomic_write_private_file(root, "install-manifest.json", rendered.encode("utf-8"))

    def _write_unit(self, content: str) -> bool:
        self._systemd_root.mkdir(parents=True, exist_ok=True, mode=0o755)
        target = self._systemd_root / UNIT_NAME
        if target.exists():
            if target.is_symlink():
                raise PiWebError("existing Pi Web unit is a symlink; refusing to manage it")
            if target.read_text(encoding="utf-8") == content:
                return False
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        except FileExistsError:
            raise PiWebError("Pi Web unit changed during installation") from None
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
        return True


def start_service(*, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
    _bounded_run(run, ["systemctl", "start", UNIT_NAME])


def stop_service(*, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
    _bounded_run(run, ["systemctl", "stop", UNIT_NAME])
_BRIDGE_PACKAGE = "anvil_serving"
_BRIDGE_DIR = "_pi_web_bridge"


def bridge_manifest():
    """Return the installed immutable bridge descriptor without staging it."""
    from importlib.resources import files
    try:
        raw = files(_BRIDGE_PACKAGE).joinpath(_BRIDGE_DIR, "0.9.0-host-bridge.json").read_bytes()
        manifest = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise PiWebError("the pinned Pi Web bridge asset is unavailable") from exc
    if (type(manifest) is not dict or manifest.get("schema") != "anvil-serving.pi-web-bridge/v1"
            or manifest.get("package") != PACKAGE or manifest.get("version") != DEFAULT_VERSION
            or not isinstance(manifest.get("patch_sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", manifest["patch_sha256"])):
        raise PiWebError("the pinned Pi Web bridge asset is invalid")
    patch = files(_BRIDGE_PACKAGE).joinpath(_BRIDGE_DIR, "0.9.0-host-bridge.patch").read_bytes()
    if __import__("hashlib").sha256(patch).hexdigest() != manifest["patch_sha256"]:
        raise PiWebError("the pinned Pi Web bridge asset changed")
    for field in ("source_commit", "package_json_sha256", "app_shell_sha256"):
        if type(manifest.get(field)) is not str or not re.fullmatch(r"[a-f0-9]{40,64}", manifest[field]):
            raise PiWebError("the pinned Pi Web bridge asset is invalid")
    return manifest
