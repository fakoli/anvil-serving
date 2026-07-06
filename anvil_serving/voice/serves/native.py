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

import json
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ._common import DEFAULT_READY_TIMEOUT, _probe_models_endpoint


DEFAULT_STOP_TIMEOUT = 5.0
PID_FILE_SCHEMA = "anvil-voice-native/v1"
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def _expand_path(path: str) -> str:
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


def parse_command(command: str) -> list[str]:
    """Parse a manifest command into argv. Raises ``ValueError`` if empty."""
    argv = shlex.split(command or "", posix=(os.name != "nt"))
    if os.name == "nt":
        argv = [
            part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in ("'", '"') else part
            for part in argv
        ]
    if not argv:
        raise ValueError("native lifecycle command must not be empty")
    return argv


@dataclass(frozen=True)
class NativeServeConfig:
    kind: str
    base_url: str
    model: str
    start_command: str
    api_key_env: str = ""
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
            api_key_env=table.get("api_key_env", ""),
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
        _killpg: Optional[Callable[[int, int], Any]] = None,
        _process_argv: Optional[Callable[[int], Optional[list[str]]]] = None,
        _sleep: Callable[[float], None] = time.sleep,
        _monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._open = _open
        self._popen = _popen or subprocess.Popen
        self._run = _run or subprocess.run
        self._kill = _kill or os.kill
        self._killpg = _killpg or getattr(os, "killpg", None)
        self._process_argv = _process_argv
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

    def _default_process_argv(self, pid: int) -> Optional[list[str]]:
        proc_cmdline = os.path.join("/proc", str(pid), "cmdline")
        try:
            raw = open(proc_cmdline, "rb").read()
        except OSError:
            raw = b""
        if raw:
            return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
        if os.name == "nt":
            return None
        try:
            proc = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        text = (proc.stdout or "").strip()
        if proc.returncode != 0 or not text:
            return None
        try:
            return shlex.split(text, posix=True)
        except ValueError:
            return [text]

    def _read_pid_record(self) -> dict:
        try:
            text = open(self.pid_file, "r", encoding="utf-8").read().strip()
        except OSError:
            return {"pid": None, "owned": False, "reason": "missing_pid_file"}
        if text.isdigit():
            return {"pid": int(text), "owned": False, "reason": "legacy_pid_file"}
        try:
            record = json.loads(text)
        except ValueError:
            return {"pid": None, "owned": False, "reason": "bad_pid_file"}
        if not isinstance(record, dict):
            return {"pid": None, "owned": False, "reason": "bad_pid_file"}
        pid = record.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return {"pid": None, "owned": False, "reason": "bad_pid_file"}
        record["owned"] = self._record_matches_config(record) and self._process_matches_record(pid, record)
        record["reason"] = "owned" if record["owned"] else "pid_record_mismatch"
        return record

    def _record_matches_config(self, record: dict) -> bool:
        try:
            command = parse_command(self.config.start_command)
        except ValueError:
            return False
        return (
            record.get("schema") == PID_FILE_SCHEMA
            and record.get("kind") == self.config.kind
            and record.get("base_url") == self.config.base_url
            and record.get("model") == self.config.model
            and record.get("command") == command
        )

    def _process_matches_record(self, pid: int, record: dict) -> bool:
        expected = record.get("command")
        if not isinstance(expected, list) or not expected:
            return False
        reader = self._process_argv or self._default_process_argv
        actual = reader(pid)
        if actual is None:
            return os.name == "nt"
        if not actual:
            return False
        expected_exe = os.path.basename(str(expected[0]))
        actual_exe = os.path.basename(str(actual[0]))
        exe_matches = (
            not expected_exe
            or expected_exe == actual_exe
            or actual_exe.startswith(expected_exe)
            or expected_exe.startswith(actual_exe)
        )
        if not exe_matches:
            return False
        actual_text = " ".join(str(part) for part in actual)
        return all(str(part) in actual_text for part in expected[1:])

    def _read_pid(self) -> Optional[int]:
        return self._read_pid_record().get("pid")

    def _write_pid(self, pid: int, argv: list[str]) -> None:
        parent = os.path.dirname(self.pid_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
        record = {
            "schema": PID_FILE_SCHEMA,
            "pid": pid,
            "kind": self.config.kind,
            "base_url": self.config.base_url,
            "model": self.config.model,
            "command": argv,
            "created_at": time.time(),
        }
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(self.pid_file, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, sort_keys=True)
            f.write("\n")

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
        token = os.environ.get(self.config.api_key_env, "").strip() if self.config.api_key_env else ""
        if token:
            raw_open = opener

            def opener(url, timeout):
                import urllib.request

                req = urllib.request.Request(url, headers={"Authorization": "Bearer %s" % token})
                return raw_open(req, timeout=timeout)

        return _probe_models_endpoint(self.config.base_url, 2.0, opener)

    def _wait_ready(self, timeout: float) -> bool:
        deadline = self._monotonic() + max(0.0, timeout)
        while True:
            if self.ready():
                return True
            if self._monotonic() >= deadline:
                return False
            self._sleep(0.25)

    def _wait_down(self, timeout: float) -> bool:
        deadline = self._monotonic() + max(0.0, timeout)
        while True:
            if not self.ready():
                return True
            if self._monotonic() >= deadline:
                return False
            self._sleep(0.25)

    def _signal_owned_process(self, pid: int, sig: int) -> None:
        if os.name != "nt" and self._killpg is not None:
            try:
                self._killpg(pid, sig)
                return
            except ProcessLookupError:
                raise
            except OSError:
                pass
        self._kill(pid, sig)

    def status(self) -> dict:
        record = self._read_pid_record()
        pid = record.get("pid")
        raw_running = self._pid_running(pid) if pid is not None else False
        owned = bool(record.get("owned")) and raw_running
        return {
            "kind": self.config.kind,
            "lifecycle": "native",
            "base_url": self.config.base_url,
            "model": self.config.model,
            "pid": pid,
            "pid_running": owned,
            "pid_running_raw": raw_running,
            "pid_owner_valid": bool(record.get("owned")),
            "pid_file_reason": record.get("reason"),
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
        if status["pid"] and status["pid_running_raw"] and not status["pid_owner_valid"]:
            self._remove_pid()
            status = self.status()
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
        try:
            self._write_pid(int(proc.pid), argv)
        except OSError as exc:
            try:
                self._signal_owned_process(int(proc.pid), signal.SIGTERM)
            except OSError:
                pass
            return {
                "action": "up",
                "returncode": 1,
                "applied": False,
                "reason": "pid_file_write_failed",
                "error": str(exc),
                "command": argv,
                "pid": int(proc.pid),
                "pid_file": self.pid_file,
                "log_file": self.log_file,
                "ready": self.ready(),
                "base_url": self.config.base_url,
                "model": self.config.model,
                "kind": self.config.kind,
                "lifecycle": "native",
            }
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
            self._signal_owned_process(int(pid), signal.SIGTERM)
            deadline = self._monotonic() + max(0.0, self.config.stop_timeout)
            while self._pid_running(int(pid)) and self._monotonic() < deadline:
                self._sleep(0.1)
            if self._pid_running(int(pid)):
                self._signal_owned_process(int(pid), _SIGKILL)
                while self._pid_running(int(pid)) and self._monotonic() < deadline:
                    self._sleep(0.1)
            stopped = not self._pid_running(int(pid))
            endpoint_down = self._wait_down(self.config.stop_timeout)
            if stopped and endpoint_down:
                self._remove_pid()
            return {
                "action": "down",
                "returncode": 0 if stopped and endpoint_down else 1,
                "applied": True,
                "reason": "pid_file",
                **dict(status, pid_running=not stopped, ready=not endpoint_down),
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
            try:
                proc = self._run(
                    argv,
                    cwd=self.workdir,
                    capture_output=True,
                    text=True,
                    timeout=self.config.stop_timeout,
                )
            except subprocess.TimeoutExpired as exc:
                return {
                    "action": "down",
                    "returncode": 1,
                    "applied": False,
                    "reason": "stop_command_timeout",
                    "command": argv,
                    "error": "stop_command timed out after %.2fs" % float(exc.timeout or 0),
                    **status,
                }
            endpoint_down = self._wait_down(self.config.stop_timeout)
            if proc.returncode == 0 and endpoint_down:
                self._remove_pid()
            return {
                "action": "down",
                "returncode": 0 if proc.returncode == 0 and endpoint_down else 1,
                "applied": proc.returncode == 0 and endpoint_down,
                "reason": "stop_command",
                "command": argv,
                "stdout": (proc.stdout or "").strip(),
                "stderr": (proc.stderr or "").strip(),
                **dict(status, ready=not endpoint_down),
            }

        return {
            "action": "down",
            "returncode": 1,
            "applied": False,
            "reason": "ready_but_unmanaged",
            "error": "endpoint is ready but no running pid_file or stop_command is configured",
            **status,
        }
