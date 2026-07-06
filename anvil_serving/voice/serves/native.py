"""Native same-host audio serve lifecycle for small edge hosts.

`managed` voice serves delegate to :mod:`anvil_serving.serves` and Docker.
Fakoli Mini runs STT/TTS as native MLX Audio processes instead, so this module
provides the narrow process lifecycle used by ``anvil-serving voice up/down``:
start a trusted manifest command in the background, write a PID file, probe the
OpenAI-compatible ``/models`` endpoint, and stop the PID it started.

No shell is used. Commands are parsed with ``shlex`` and treated as trusted
operator manifest content, like ``serves.toml``'s ``up`` command.
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ._common import DEFAULT_READY_TIMEOUT, _probe_models_endpoint


DEFAULT_STOP_TIMEOUT = 5.0
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def _expand_path(path: str) -> str:
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


def parse_command(command: str) -> list[str]:
    """Parse a manifest command into argv. Raises ``ValueError`` if empty."""
    argv = shlex.split(command or "")
    if not argv:
        raise ValueError("native lifecycle command must not be empty")
    return argv


@dataclass(frozen=True)
class NativeServeConfig:
    kind: str
    base_url: str
    model: str
    start_command: str
    stop_command: str = ""
    workdir: str = ""
    pid_file: str = ""
    log_file: str = ""
    ready_timeout: float = DEFAULT_READY_TIMEOUT
    stop_timeout: float = DEFAULT_STOP_TIMEOUT

    @classmethod
    def from_table(cls, kind: str, table: dict) -> "NativeServeConfig":
        return cls(
            kind=kind,
            base_url=table.get("base_url", ""),
            model=table.get("model", ""),
            start_command=table.get("start_command", ""),
            stop_command=table.get("stop_command", ""),
            workdir=table.get("workdir", ""),
            pid_file=table.get("pid_file", "") or "/tmp/anvil-voice-%s.pid" % kind,
            log_file=table.get("log_file", "") or "/tmp/anvil-voice-%s.log" % kind,
            ready_timeout=float(table.get("ready_timeout", DEFAULT_READY_TIMEOUT)),
            stop_timeout=float(table.get("stop_timeout", DEFAULT_STOP_TIMEOUT)),
        )


class NativeServe:
    """Lifecycle manager for one native STT/TTS process."""

    def __init__(
        self,
        config: NativeServeConfig,
        *,
        _open: Optional[Callable[..., Any]] = None,
        _popen: Optional[Callable[..., Any]] = None,
        _run: Optional[Callable[..., Any]] = None,
        _kill: Optional[Callable[[int, int], Any]] = None,
        _sleep: Callable[[float], None] = time.sleep,
        _monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._open = _open
        self._popen = _popen or subprocess.Popen
        self._run = _run or subprocess.run
        self._kill = _kill or os.kill
        self._sleep = _sleep
        self._monotonic = _monotonic

    @property
    def pid_file(self) -> str:
        return _expand_path(self.config.pid_file)

    @property
    def log_file(self) -> str:
        return _expand_path(self.config.log_file)

    @property
    def workdir(self) -> Optional[str]:
        return _expand_path(self.config.workdir) if self.config.workdir else None

    def _pid_running(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            self._kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    def _read_pid(self) -> Optional[int]:
        try:
            text = open(self.pid_file, "r", encoding="utf-8").read().strip()
        except OSError:
            return None
        return int(text) if text.isdigit() else None

    def _write_pid(self, pid: int) -> None:
        parent = os.path.dirname(self.pid_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.pid_file, "w", encoding="utf-8") as f:
            f.write("%d\n" % pid)

    def _remove_pid(self) -> None:
        try:
            os.remove(self.pid_file)
        except FileNotFoundError:
            return
        except OSError:
            return

    def ready(self) -> bool:
        opener = self._open
        if opener is None:
            import urllib.request

            opener = urllib.request.urlopen
        return _probe_models_endpoint(self.config.base_url, 2.0, opener)

    def _wait_ready(self, timeout: float) -> bool:
        deadline = self._monotonic() + max(0.0, timeout)
        while True:
            if self.ready():
                return True
            if self._monotonic() >= deadline:
                return False
            self._sleep(0.25)

    def status(self) -> dict:
        pid = self._read_pid()
        running = self._pid_running(pid) if pid is not None else False
        return {
            "kind": self.config.kind,
            "lifecycle": "native",
            "base_url": self.config.base_url,
            "model": self.config.model,
            "pid": pid,
            "pid_running": running,
            "pid_file": self.pid_file,
            "log_file": self.log_file,
            "ready": self.ready(),
        }

    def bring_up(self, *, dry_run: bool = False) -> dict:
        argv = parse_command(self.config.start_command)
        status = self.status()
        if status["ready"]:
            return {
                "action": "up",
                "returncode": 0,
                "applied": False,
                "reason": "already_ready",
                "dry_run": dry_run,
                "command": argv,
                **status,
            }
        if status["pid_running"]:
            ready = self._wait_ready(self.config.ready_timeout)
            return {
                "action": "up",
                "returncode": 0 if ready else 1,
                "applied": False,
                "reason": "pid_running",
                "dry_run": dry_run,
                "command": argv,
                **dict(status, ready=ready),
            }
        if dry_run:
            return {
                "action": "up",
                "returncode": 0,
                "applied": False,
                "dry_run": True,
                "command": argv,
                **status,
            }

        log_parent = os.path.dirname(self.log_file)
        if log_parent:
            os.makedirs(log_parent, exist_ok=True)
        with open(self.log_file, "ab") as log:
            kwargs = {
                "cwd": self.workdir,
                "stdout": log,
                "stderr": subprocess.STDOUT,
                "stdin": subprocess.DEVNULL,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                kwargs["start_new_session"] = True
            proc = self._popen(argv, **kwargs)
        self._write_pid(int(proc.pid))
        ready = self._wait_ready(self.config.ready_timeout)
        return {
            "action": "up",
            "returncode": 0 if ready else 1,
            "applied": True,
            "reason": "started",
            "command": argv,
            "pid": int(proc.pid),
            "pid_running": self._pid_running(int(proc.pid)),
            "pid_file": self.pid_file,
            "log_file": self.log_file,
            "ready": ready,
            "base_url": self.config.base_url,
            "model": self.config.model,
            "kind": self.config.kind,
            "lifecycle": "native",
        }

    def tear_down(self, *, dry_run: bool = False) -> dict:
        status = self.status()
        pid = status["pid"]
        if dry_run:
            return {
                "action": "down",
                "returncode": 0,
                "applied": False,
                "dry_run": True,
                "stop_command": parse_command(self.config.stop_command) if self.config.stop_command else None,
                **status,
            }
        if pid and status["pid_running"]:
            self._kill(int(pid), signal.SIGTERM)
            deadline = self._monotonic() + max(0.0, self.config.stop_timeout)
            while self._pid_running(int(pid)) and self._monotonic() < deadline:
                self._sleep(0.1)
            if self._pid_running(int(pid)):
                self._kill(int(pid), _SIGKILL)
            self._remove_pid()
            stopped = not self._pid_running(int(pid))
            return {
                "action": "down",
                "returncode": 0 if stopped else 1,
                "applied": True,
                "reason": "pid_file",
                **dict(status, pid_running=not stopped, ready=self.ready()),
            }

        if not status["ready"]:
            self._remove_pid()
            return {
                "action": "down",
                "returncode": 0,
                "applied": False,
                "reason": "already_down",
                **status,
            }

        if self.config.stop_command:
            argv = parse_command(self.config.stop_command)
            proc = self._run(argv, cwd=self.workdir, capture_output=True, text=True)
            self._remove_pid()
            return {
                "action": "down",
                "returncode": int(proc.returncode),
                "applied": proc.returncode == 0,
                "reason": "stop_command",
                "command": argv,
                "stdout": (proc.stdout or "").strip(),
                "stderr": (proc.stderr or "").strip(),
                **dict(status, ready=self.ready()),
            }

        return {
            "action": "down",
            "returncode": 1,
            "applied": False,
            "reason": "ready_but_unmanaged",
            "error": "endpoint is ready but no running pid_file or stop_command is configured",
            **status,
        }
