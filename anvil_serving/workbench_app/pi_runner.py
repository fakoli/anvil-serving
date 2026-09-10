"""Server-owned construction of the isolated Pi runner command.

The Workbench facade creates this policy from protected configuration.  It
never accepts argv, cwd, paths, or environment names from a browser request.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Callable, Mapping
from urllib.parse import urlsplit


_NAME = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


@dataclass(frozen=True)
class PiRunnerLaunch:
    """Configured argv plus the minimal child-only environment for one runner."""

    argv: tuple[str, ...]
    environment: Mapping[str, str]


@dataclass(frozen=True)
class PiRunnerPolicy:
    """A CPU-only, unprivileged container invocation for one task session."""

    engine_argv: tuple[str, ...]
    image: str
    worktree: Path
    agent_dir: Path
    uid: int
    gid: int
    cpu_limit: float = 4.0
    memory_limit_bytes: int = 4 * 1024 * 1024 * 1024
    pids_limit: int = 256
    network: str = "none"
    proxy_url: str | None = None
    container_name: str = ""
    session_id: str = ""
    native_args: tuple[str, ...] = ()
    credential_names: tuple[str, ...] = ()
    network_id: str | None = None
    egress_ready: Callable[[], object] | None = None

    def __post_init__(self) -> None:
        if not self.engine_argv or not self.image or "\n" in self.image or "\x00" in self.image:
            raise ValueError("Pi runner engine and image must be configured")
        if not self.worktree.is_absolute() or not self.agent_dir.is_absolute():
            raise ValueError("Pi runner worktree and agent directory must be absolute")
        if self.worktree.exists() and (self.worktree / ".git").is_file():
            raise ValueError("Pi runner checkout must be an isolated full clone, not a linked worktree")
        if self.uid < 1 or self.gid < 1:
            raise ValueError("Pi runner uid and gid must be explicitly configured")
        if not _NAME.fullmatch(self.container_name) or not _NAME.fullmatch(self.session_id):
            raise ValueError("Pi runner container and session identities must be configured")
        if self.cpu_limit <= 0 or self.memory_limit_bytes < 64 * 1024 * 1024 or self.pids_limit < 16:
            raise ValueError("Pi runner limits are invalid")
        if self.network == "none":
            if self.proxy_url is not None:
                raise ValueError("Pi runner proxy requires an isolated network")
        elif not re.fullmatch(r"isolated-[A-Za-z0-9_.-]{1,96}", self.network):
            raise ValueError("Pi runner network must be none or an approved isolated network")
        elif not isinstance(self.proxy_url, str) or not self._safe_proxy(self.proxy_url):
            raise ValueError("Pi runner isolated network requires an approved proxy URL")

    def policy_digest(self, session_dir: Path) -> str:
        value = {"image": self.image, "worktree": str(self.worktree.resolve()), "agent_dir": str(self.agent_dir.resolve()),
                 "session_dir": str(session_dir.resolve()), "uid": self.uid, "gid": self.gid, "cpu": self.cpu_limit,
                 "memory": self.memory_limit_bytes, "pids": self.pids_limit, "network": self.network,
                 "proxy": self.proxy_url, "network_id": self.network_id, "session": self.session_id, "native_args": self.native_args, "credential_names": self.credential_names}
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def argv(self, session_dir: Path, *, credential_names: tuple[str, ...], resume_file_name: str | None = None, detached: bool = False) -> tuple[str, ...]:
        """Build the configured container command without exposing host Pi state."""
        if not session_dir.is_absolute():
            raise ValueError("Pi session directory must be absolute")
        for path in (self.worktree, self.agent_dir, session_dir):
            if "," in str(path) or "\n" in str(path) or "\x00" in str(path):
                raise ValueError("Pi runner mount path is invalid")
        arguments: list[str] = [
            *self.engine_argv,
            "run",
            "--rm",
            "--interactive",
            "--name",
            self.container_name,
            "--label",
            f"anvil.pi.session={self.session_id}",
            "--label",
            f"anvil.pi.policy={self.policy_digest(session_dir)}",
            "--log-driver",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            f"{self.uid}:{self.gid}",
            "--cpus",
            str(self.cpu_limit),
            "--memory",
            str(self.memory_limit_bytes),
            "--pids-limit",
            str(self.pids_limit),
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,source={self.worktree},target=/workspace",
            "--mount",
            f"type=bind,source={self.agent_dir},target=/home/pi/.pi/agent",
            "--mount",
            f"type=bind,source={session_dir},target=/sessions",
            "--env",
            "PI_CODING_AGENT_DIR=/home/pi/.pi/agent",
        ]
        if detached:
            arguments.append("--detach")
        arguments.extend(["--network", "none" if self.network == "none" else self.network])

        # Docker resolves these from the child environment. Never put a secret
        # value in argv where process inspection could expose it.
        if credential_names:
            raise ValueError("Provider credentials must remain in the trusted gateway")
        for name in sorted(credential_names):
            if not _NAME.fullmatch(name):
                raise ValueError("credential environment key is invalid")
            arguments.extend(["--env", name])
        arguments.extend([self.image, *self.native_args])
        if not self.native_args:
            arguments.extend(["--mode", "rpc", "--no-extensions", "--session-dir", "/sessions", "--session-id", self.session_id])
        return tuple(arguments)

    def launch(self, session_dir: Path, *, credential_environment: Mapping[str, str], resume_file_name: str | None = None, detached: bool = False) -> PiRunnerLaunch:
        """Return argv and a minimal resolved environment for ``PiRpcClient``.

        The configuration resolver supplies only named secret references for
        this runner. Values never enter session JSON, events, logs, or argv.
        """
        for name, value in credential_environment.items():
            if not _NAME.fullmatch(name) or not isinstance(value, str):
                raise ValueError("credential environment is invalid")
        return PiRunnerLaunch(
            argv=self.argv(session_dir, credential_names=tuple(credential_environment), resume_file_name=resume_file_name, detached=detached),
            environment=dict(credential_environment),
        )

    @staticmethod
    def _safe_proxy(value: str) -> bool:
        parsed = urlsplit(value)
        return parsed.scheme == "http" and bool(parsed.hostname) and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment


class PiContainerInspector:
    """Reconcile a server-generated container identity before recovery."""

    def __init__(self, engine_binary: str, run=subprocess.run) -> None:
        self._engine = engine_binary
        self._run = run

    def state(self, policy: PiRunnerPolicy, session_dir: Path, *, require_egress_ready: bool = True) -> str:
        try:
            result = self._run([self._engine, "inspect", "--type", "container", policy.container_name], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return "unavailable"
        if result.returncode != 0:
            # Docker daemon/permission/transport failures are never evidence of
            # absence. Match its exact container-specific not-found response.
            error = result.stderr.strip()
            absent = {f"Error: No such container: {policy.container_name}", f"Error response from daemon: No such container: {policy.container_name}"}
            return "absent" if error in absent else "unavailable"
        try:
            value = json.loads(result.stdout)
            if len(value) != 1:
                return "unsafe"
            item = value[0]
            config, host = item["Config"], item["HostConfig"]
            labels = config.get("Labels") or {}
            if config.get("Image") != policy.image or labels.get("anvil.pi.session") != policy.session_id or labels.get("anvil.pi.policy") != policy.policy_digest(session_dir):
                return "unsafe"
            # Resolve the immutable configured image to its engine identity too.
            image = self._run([self._engine, "image", "inspect", policy.image], capture_output=True, text=True, timeout=5)
            if image.returncode:
                return "unsafe"
            image_facts = json.loads(image.stdout)[0]
            if item.get("Image") != image_facts.get("Id"):
                return "unsafe"
            expected_mounts = {"/sessions": session_dir, "/workspace": policy.worktree, "/home/pi/.pi/agent": policy.agent_dir}
            mounts = item.get("Mounts", [])
            bound = [m for m in mounts if m.get("Type") == "bind"]
            if len(bound) != len(expected_mounts) or any(m.get("Destination") not in expected_mounts or Path(m.get("Source", "")).resolve() != expected_mounts[m["Destination"]].resolve() or m.get("RW") is not True for m in bound):
                return "unsafe"
            if any(m.get("Type") not in {"bind", "tmpfs"} or (m.get("Type") == "tmpfs" and m.get("Destination") != "/tmp") for m in mounts):
                return "unsafe"
            if config.get("Cmd") != list(policy.native_args) or config.get("Entrypoint") != ["/opt/pi-runner/node_modules/.bin/pi"] or config.get("WorkingDir") != "/workspace" or config.get("User") != f"{policy.uid}:{policy.gid}":
                return "unsafe"
            if (host.get("LogConfig", {}).get("Type") != "none" or host.get("ReadonlyRootfs") is not True or host.get("Privileged") is not False
                    or host.get("CapAdd") or host.get("CapDrop") != ["ALL"]
                    or host.get("SecurityOpt") not in (["no-new-privileges"], ["no-new-privileges=true"])
                    or host.get("NanoCpus") != int(policy.cpu_limit * 10**9)
                    or host.get("Memory") != policy.memory_limit_bytes or host.get("PidsLimit") != policy.pids_limit
                    or host.get("NetworkMode") != policy.network or host.get("PidMode", "") != ""
                    or host.get("Devices") or host.get("DeviceRequests") or host.get("Binds")
                    or host.get("Tmpfs") != {"/tmp": "rw,nosuid,nodev,size=64m"}):
                return "unsafe"
            env = dict(entry.split("=", 1) for entry in config.get("Env", []) if "=" in entry)
            allowed_env = {entry.split("=", 1)[0] for entry in image_facts.get("Config", {}).get("Env", [])}
            allowed_env.update(policy.credential_names)
            allowed_env.add("PI_CODING_AGENT_DIR")

            if set(env) != allowed_env:
                return "unsafe"
            if env.get("PI_CODING_AGENT_DIR") != "/home/pi/.pi/agent":
                return "unsafe"
            if policy.network != "none":
                network = self._run([self._engine, "network", "inspect", policy.network], capture_output=True, text=True, timeout=5)
                if network.returncode:
                    return "unavailable"
                facts = json.loads(network.stdout)[0]
                if facts.get("Internal") is not True or facts.get("Id") != policy.network_id or any(facts.get("Options", {}).get("com.docker.network.bridge.gateway_mode_" + family) != "isolated" for family in ("ipv4", "ipv6")):
                    return "unsafe"
                membership = item.get("NetworkSettings", {}).get("Networks", {})
                if set(membership) != {policy.network} or membership[policy.network].get("NetworkID") != policy.network_id:
                    return "unsafe"
                if any(env.get(key) for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")):
                    return "unsafe"
                if require_egress_ready:
                    if policy.egress_ready is None:
                        return "unsafe"
                    try:
                        policy.egress_ready()
                    except Exception:
                        return "unavailable"
                return "running" if item.get("State", {}).get("Running") is True else "stopped"
            if any(env.get(key) for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")):
                return "unsafe"
            return "running" if item.get("State", {}).get("Running") is True else "stopped"
        except (ValueError, KeyError, TypeError, IndexError, OSError, subprocess.TimeoutExpired):
            return "unsafe"
