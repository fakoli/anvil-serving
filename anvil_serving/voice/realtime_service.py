"""Persistent process lifecycle for the Mini-owned Realtime voice proxy."""
from __future__ import annotations

import math
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .serves.native import NativeServe, NativeServeConfig


DEFAULT_RUN_DIR = "~/.anvil-serving/run"
DEFAULT_READY_TIMEOUT = 15.0
DEFAULT_STOP_TIMEOUT = 5.0
DEFAULT_LOG_LINES = 200
MAX_LOG_LINES = 5000
MAX_LOG_BYTES = 1024 * 1024


def _command_text(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


@dataclass(frozen=True)
class ProxyProcessConfig:
    config_path: str
    topology_path: str
    profile: str | None
    host: str
    port: int
    owner: str = "mini"
    command_host: str | None = None
    command_runtime: str | None = None
    target: str | None = None
    topology_overlay: str | None = None
    pid_file: str = os.path.join(DEFAULT_RUN_DIR, "voice-proxy.pid")
    log_file: str = os.path.join(DEFAULT_RUN_DIR, "voice-proxy.log")
    ready_timeout: float = DEFAULT_READY_TIMEOUT
    stop_timeout: float = DEFAULT_STOP_TIMEOUT

    def __post_init__(self) -> None:
        if self.host != "127.0.0.1":
            raise ValueError("Mini realtime proxy process must bind 127.0.0.1")
        if not isinstance(self.port, int) or isinstance(self.port, bool) or not 0 < self.port < 65536:
            raise ValueError("realtime proxy port must be an integer from 1 through 65535")
        if not self.config_path or not self.topology_path:
            raise ValueError("config_path and topology_path are required")
        if not self.owner:
            raise ValueError("proxy owner is required")
        for name, value in (
            ("ready_timeout", self.ready_timeout),
            ("stop_timeout", self.stop_timeout),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise ValueError("%s must be a positive finite number" % name)

    @property
    def endpoint(self) -> str:
        return "ws://%s:%d/v1/realtime" % (self.host, self.port)

    @property
    def health_url(self) -> str:
        return "http://%s:%d/usage" % (self.host, self.port)

    def run_argv(self) -> list[str]:
        argv = [
            sys.executable,
            "-m",
            "anvil_serving.cli",
            "voice",
            "proxy",
            "run",
            "--config",
            self.config_path,
            "--topology",
            self.topology_path,
            "--transport",
            "local",
        ]
        for flag, value in (
            ("--profile", self.profile),
            ("--topology-overlay", self.topology_overlay),
            ("--command-host", self.command_host),
            ("--command-runtime", self.command_runtime),
            ("--target", self.target),
        ):
            if value:
                argv.extend((flag, value))
        return argv


class _RealtimeNativeProcess(NativeServe):
    def __init__(
        self,
        config: NativeServeConfig,
        health_url: str,
        *,
        opener: Callable[..., object] = urllib.request.urlopen,
        **kwargs,
    ) -> None:
        super().__init__(config, **kwargs)
        self._health_url = health_url
        self._health_opener = opener

    def ready(self) -> bool:
        try:
            with self._health_opener(self._health_url, timeout=2.0) as response:
                status = getattr(response, "status", None) or response.getcode()
        except urllib.error.HTTPError as exc:
            return exc.code < 500
        except (OSError, ValueError, urllib.error.URLError):
            return False
        return status < 500


class RealtimeProxyProcessService:
    """Own a detached foreground-proxy process using verified PID metadata."""

    def __init__(self, config: ProxyProcessConfig, **native_kwargs) -> None:
        self.config = config
        argv = config.run_argv()
        native_config = NativeServeConfig(
            kind="realtime-proxy",
            base_url=config.health_url,
            model="anvil-realtime-proxy",
            start_command=_command_text(argv),
            pid_file=config.pid_file,
            log_file=config.log_file,
            ready_timeout=config.ready_timeout,
            stop_timeout=config.stop_timeout,
        )
        self._process = _RealtimeNativeProcess(
            native_config,
            config.health_url,
            **native_kwargs,
        )

    def up(self, *, dry_run: bool = False) -> dict:
        return self._result("up", self._process.bring_up(dry_run=dry_run))

    def down(self, *, dry_run: bool = False) -> dict:
        return self._result("down", self._process.tear_down(dry_run=dry_run))

    def restart(self, *, dry_run: bool = False) -> dict:
        if dry_run:
            return {
                "action": "restart",
                "returncode": 0,
                "dry_run": True,
                "down": self.down(dry_run=True),
                "up": self.up(dry_run=True),
                "endpoint": self.config.endpoint,
                "owner": self.config.owner,
            }
        down = self.down()
        if down["returncode"] != 0:
            return {
                "action": "restart",
                "returncode": down["returncode"],
                "down": down,
                "up": None,
                "endpoint": self.config.endpoint,
                "owner": self.config.owner,
            }
        up = self.up()
        return {
            "action": "restart",
            "returncode": up["returncode"],
            "down": down,
            "up": up,
            "endpoint": self.config.endpoint,
            "owner": self.config.owner,
        }

    def status(self) -> dict:
        return self._result("status", self._process.status())

    def logs(self, *, tail: int = DEFAULT_LOG_LINES) -> dict:
        if not isinstance(tail, int) or isinstance(tail, bool) or not 1 <= tail <= MAX_LOG_LINES:
            raise ValueError("tail must be an integer from 1 through %d" % MAX_LOG_LINES)
        path = self._process.log_file
        try:
            with open(path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - MAX_LOG_BYTES))
                raw = handle.read(MAX_LOG_BYTES)
        except FileNotFoundError:
            raw = b""
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()[-tail:]
        return {
            "action": "logs",
            "owner": self.config.owner,
            "endpoint": self.config.endpoint,
            "path": path,
            "tail": tail,
            "max_bytes": MAX_LOG_BYTES,
            "truncated": len(raw) == MAX_LOG_BYTES,
            "lines": lines,
            "returncode": 0,
        }

    def _result(self, action: str, result: dict) -> dict:
        return {
            **result,
            "action": action,
            "owner": self.config.owner,
            "endpoint": self.config.endpoint,
        }
