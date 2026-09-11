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
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat as stat_module
import subprocess
import sys
import time
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
    run: Callable[..., subprocess.CompletedProcess[str]], argv: list[str]
) -> subprocess.CompletedProcess[str]:
    try:
        result = run(argv, check=False, text=True, capture_output=True, timeout=_COMMAND_TIMEOUT_SECONDS)
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
    entry = version_dir / PACKAGE_BIN
    npm_command = [
        "runuser", "-u", config.service_user, "--",
        "env", f"PATH={Path(node).parent}:/usr/local/bin:/usr/bin:/bin",
        node, str(npm_script), "install", "--prefix", str(version_dir), f"{PACKAGE}@{config.version}",
    ]
    return {
        "package": f"{PACKAGE}@{config.version}",
        "version": config.version,
        "node": {"path": node, "version": ".".join(str(part) for part in node_version)},
        "npm_script": str(npm_script),
        "install_root": str(version_dir),
        "entry_script": str(entry),
        "port": config.port,
        "hostname": config.hostname,
        "allowed_hosts": list(config.allowed_hosts),
        "unit_path": str(Path("/etc/systemd/system") / UNIT_NAME),
        "commands": [
            npm_command,
            ["systemctl", "daemon-reload"],
            ["systemctl", "enable", UNIT_NAME],
            ["systemctl", "restart", UNIT_NAME],
        ],
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


class PiWebInstaller:
    """One-shot, idempotent, root-gated install of the pinned session UI.

    The npm tree is installed as the configured service user (its postinstall
    runs unprivileged), the reviewed unit is written byte-exactly, and an
    already-correct install converges without restarting the service.
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
        root = install_root_for(config)
        version_dir = root / config.version
        node, node_version = _node_details(self._node_path or config.node_path, run=self._run)
        uid, user_home = _service_identity(config.service_user)
        entry = version_dir / PACKAGE_BIN
        content = unit_content(
            config,
            node_path=node,
            entry_script=entry,
            user=config.service_user,
            user_home=Path(user_home),
            node_bin_dir=Path(node).parent,
        )
        existed = entry.is_file()
        if not existed:
            version_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
            self._chown(version_dir, uid, uid)
            npm_script = npm_script_for(node)
            _bounded_run(self._run, [
                "runuser", "-u", config.service_user, "--",
                "env", f"PATH={Path(node).parent}:/usr/local/bin:/usr/bin:/bin",
                node, str(npm_script), "install", "--prefix", str(version_dir), f"{PACKAGE}@{config.version}",
            ])
            if not entry.is_file():
                raise PiWebError("the pinned Pi Web package did not produce its entry script")
        self._record_manifest(root, node=node, node_version=node_version)
        unit_changed = self._write_unit(content)
        if unit_changed:
            _bounded_run(self._run, ["systemctl", "daemon-reload"])
        _bounded_run(self._run, ["systemctl", "enable", UNIT_NAME])
        state = service_state(run=self._run)
        was_active = state["active"] == "active"
        if was_active and not unit_changed:
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
        return {
            "installed": True,
            "version": config.version,
            "unit_path": str(self._systemd_root / UNIT_NAME),
            "unit_changed": unit_changed,
            "package_installed": not existed,
            "lifecycle": lifecycle,
            "node": {"path": node, "version": ".".join(str(part) for part in node_version)},
            "probe": proof,
        }

    def _record_manifest(self, root: Path, *, node: str, node_version: tuple[int, int, int]) -> None:
        """Record the exact installed pin and runtime beside the npm tree."""
        root.mkdir(parents=True, exist_ok=True, mode=0o755)
        manifest = {
            "schema": "anvil-serving.pi-web-install/v1",
            "package": PACKAGE,
            "version": self.config.version,
            "port": self.config.port,
            "hostname": self.config.hostname,
            "allowed_hosts": list(self.config.allowed_hosts),
            "service_user": self.config.service_user,
            "node": {"path": node, "version": ".".join(str(part) for part in node_version)},
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        target = root / "install-manifest.json"
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(manifest, output, indent=2, sort_keys=True)
            output.write("\n")

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