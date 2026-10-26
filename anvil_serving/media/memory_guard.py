"""Windows host-memory signals and fail-closed supervision of one worker child."""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import tomllib

from ..paths import config_path


DEFAULTS = {
    "critical_available_gib": 4,
    "soft_available_gib": 8,
    "soft_seconds": 15,
    "min_commit_gib": 16,
    "max_age_seconds": 5,
    "timeout_seconds": 2700,
}


def load_policy(path: str | Path) -> dict:
    source = Path(path)
    if source.stat().st_size > 65536:
        raise ValueError("memory guard configuration is too large")
    raw = source.read_bytes()
    values = tomllib.loads(raw.decode("utf-8"))
    if (values.get("schema") != "anvil-serving.media-memory-guard/v1"
            or set(values) - {"schema", "lease_path", *DEFAULTS}
            or not isinstance(values.get("lease_path"), str)
            or not 1 <= len(values["lease_path"]) <= 4096):
        raise ValueError("invalid memory guard configuration")
    policy = {**DEFAULTS, **values}
    if any(type(policy[key]) not in (int, float) or not math.isfinite(policy[key])
           or not 0 < policy[key] <= 8192 for key in DEFAULTS):
        raise ValueError("memory guard thresholds must be finite and positive")
    if (policy["critical_available_gib"] > policy["soft_available_gib"]
            or not 2 <= policy["max_age_seconds"] <= 30
            or not 30 <= policy["timeout_seconds"] <= 7200):
        raise ValueError("invalid memory guard threshold order or time limits")
    # A code/policy update must restart the host publisher before a worker can start.
    policy["sha256"] = hashlib.sha256(raw + Path(__file__).read_bytes()).hexdigest()
    return policy


def pressure_reason(available: float, commit_free: float, low_seconds: float, policy: dict) -> str:
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
           for value in (available, commit_free, low_seconds)):
        return "invalid host memory counters"
    if available < policy["critical_available_gib"]:
        return "Windows available RAM below critical threshold"
    if commit_free < policy["min_commit_gib"]:
        return "Windows commit headroom below threshold"
    if available < policy["soft_available_gib"] and low_seconds >= policy["soft_seconds"]:
        return "Windows available RAM below sustained threshold"
    return ""


def write_signal(path: str | Path, value: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=".signal-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_signal(policy: dict) -> dict:
    path = Path(policy["lease_path"])
    if path.stat().st_size > 16384:
        raise ValueError("host memory signal is too large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid host memory signal")
    return value


def signal_reason(policy: dict, *, now: float | None = None) -> str:
    try:
        value = _read_signal(policy)
        sampled = value.get("sampled_at")
        if (value.get("schema") != "anvil-serving.host-memory-signal/v1"
                or value.get("policy_sha256") != policy["sha256"]
                or type(sampled) not in (int, float) or not math.isfinite(sampled)
                or type(value.get("allow")) is not bool):
            return "invalid or mismatched host memory signal"
        age = (time.time() if now is None else now) - sampled
        if not -policy["max_age_seconds"] <= age <= policy["max_age_seconds"]:
            return "host memory signal is stale or clock differs"
        if not value["allow"]:
            reason = value.get("reason")
            return reason if isinstance(reason, str) and 1 <= len(reason) <= 256 else "host memory guard refused"
        return pressure_reason(value.get("available_gib"), value.get("commit_free_gib"),
                               value.get("low_seconds"), policy)
    except (OSError, ValueError, TypeError):
        return "host memory signal is unavailable or invalid"


def windows_memory() -> tuple[float, float]:
    if sys.platform != "win32":
        raise ValueError("host memory publisher requires Windows")
    class PerformanceInfo(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_uint32)] + [
            (name, ctypes.c_size_t) for name in (
                "CommitTotal", "CommitLimit", "CommitPeak", "PhysicalTotal",
                "PhysicalAvailable", "SystemCache", "KernelTotal", "KernelPaged",
                "KernelNonpaged", "PageSize")
        ] + [(name, ctypes.c_uint32) for name in ("HandleCount", "ProcessCount", "ThreadCount")]
    value = PerformanceInfo()
    value.cb = ctypes.sizeof(value)
    query = ctypes.WinDLL("psapi", use_last_error=True).GetPerformanceInfo
    query.argtypes = [ctypes.POINTER(PerformanceInfo), ctypes.c_uint32]
    query.restype = ctypes.c_int
    if not query(ctypes.byref(value), value.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return (value.PhysicalAvailable * value.PageSize / 2**30,
            (value.CommitLimit - value.CommitTotal) * value.PageSize / 2**30)


def run_host(policy: dict) -> int:
    if sys.platform != "win32":
        raise ValueError("host memory publisher requires Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    identity = hashlib.sha256(str(Path(policy["lease_path"]).resolve()).casefold().encode()).hexdigest()[:24]
    ctypes.set_last_error(0)
    handle = kernel.CreateMutexW(None, False, "Global\\AnvilMediaMemoryGuard-" + identity)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    duplicate = ctypes.get_last_error() == 183
    if duplicate:
        kernel.CloseHandle(handle)
        return 0
    low_since = None
    stop = Path(policy["lease_path"]).with_suffix(".stop")
    try:
        while not stop.exists():
            value = {"schema": "anvil-serving.host-memory-signal/v1", "sampled_at": time.time(),
                     "policy_sha256": policy["sha256"], "publisher_pid": os.getpid()}
            try:
                available, commit_free = windows_memory()
                if available < policy["soft_available_gib"]:
                    if low_since is None:
                        low_since = time.monotonic()
                else:
                    low_since = None
                low = 0 if low_since is None else time.monotonic() - low_since
                reason = pressure_reason(available, commit_free, low, policy)
                value.update(available_gib=available, commit_free_gib=commit_free, low_seconds=low,
                             allow=not reason, reason=reason)
            except (OSError, ValueError):
                value.update(allow=False, reason="host memory sampling failed")
            write_signal(policy["lease_path"], value)
            time.sleep(1)
        return 0
    finally:
        try:
            write_signal(policy["lease_path"], {
                "schema": "anvil-serving.host-memory-signal/v1", "sampled_at": time.time(),
                "policy_sha256": policy["sha256"], "publisher_pid": os.getpid(),
                "allow": False, "reason": "guard stopped",
            })
        finally:
            kernel.CloseHandle(handle)


def supervise(policy: dict, command: list[str], *, poll_seconds: float = 1) -> int:
    if (not command or not Path(command[0]).is_absolute()
            or any(not isinstance(item, str) or not item or "\x00" in item for item in command)):
        raise ValueError("worker command must use an absolute executable and valid arguments")
    reason = signal_reason(policy)
    if reason:
        print("[media-memory-guard] " + reason, file=sys.stderr, flush=True)
        return 74
    child = subprocess.Popen(command, start_new_session=os.name != "nt")
    started = time.monotonic()
    try:
        while child.poll() is None:
            reason = signal_reason(policy)
            if time.monotonic() - started > policy["timeout_seconds"]:
                reason = "worker runtime deadline reached"
            if reason:
                print("[media-memory-guard] " + reason, file=sys.stderr, flush=True)
                return 74
            try:
                return child.wait(timeout=poll_seconds)
            except subprocess.TimeoutExpired:
                pass
        return child.returncode
    finally:
        # Emergency containment is immediate and scoped to this newly created child group.
        if child.poll() is None:
            if os.name == "nt":
                child.kill()
            else:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            child.wait(timeout=10)


def _registration(config: Path) -> tuple[str, str, list[str]]:
    executable = Path(sys.executable).with_name("pythonw.exe")
    args = [str(executable), "-m", "anvil_serving.cli", "media", "memory-guard", "run",
            "--config", str(config.resolve()), "--confirm"]
    name = "AnvilMediaGuard-" + hashlib.sha256(str(config.resolve()).casefold().encode()).hexdigest()[:12]
    return name, subprocess.list2cmdline(args), args


def install(policy: dict, config: Path, *, remove: bool = False) -> dict:
    if sys.platform != "win32":
        raise ValueError("memory guard login registration requires Windows")
    import winreg
    name, command, args = _registration(config)
    if not Path(args[0]).is_file():
        raise ValueError("the selected environment must provide pythonw.exe")
    parent = Path(policy["lease_path"]).resolve().parent
    private_home = (Path.home() / ".anvil-serving").resolve()
    if not parent.is_relative_to(private_home) or parent == private_home:
        raise ValueError("installed guard signal needs a dedicated directory below the operator home")
    parent.mkdir(parents=True, exist_ok=True)
    marker = parent / ".anvil-media-memory-guard"
    if not marker.exists() and any(parent.iterdir()):
        raise ValueError("guard signal directory is not empty or owned by this guard")
    if not remove:
        owner = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True,
                               text=True, timeout=10, check=True)
        rows = list(csv.reader(owner.stdout.splitlines()))
        if len(rows) != 1 or len(rows[0]) != 2 or not re.fullmatch(r"S-1-[0-9-]+", rows[0][1]):
            raise ValueError("could not identify Windows guard owner")
        subprocess.run(["icacls", str(parent), "/setowner", f"*{rows[0][1]}"],
                       capture_output=True, check=True, timeout=10)
        subprocess.run(["icacls", str(parent), "/reset"], capture_output=True, check=True, timeout=10)
        subprocess.run(["icacls", str(parent), "/inheritance:r", "/grant:r",
                        f"*{rows[0][1]}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F",
                        "*S-1-5-32-544:(OI)(CI)F"], capture_output=True, check=True, timeout=10)
        for entry in parent.iterdir():
            if entry.is_symlink() or not entry.is_file() or entry.stat().st_nlink != 1:
                raise ValueError("guard directory must contain only unlinked regular files")
            subprocess.run(["icacls", str(entry), "/setowner", f"*{rows[0][1]}", "/L"],
                           capture_output=True, check=True, timeout=10)
            subprocess.run(["icacls", str(entry), "/reset", "/L"],
                           capture_output=True, check=True, timeout=10)
        marker.write_text("Anvil Media memory guard\n")
    stop = Path(policy["lease_path"]).with_suffix(".stop")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        try:
            existing = winreg.QueryValueEx(key, name)[0]
        except FileNotFoundError:
            existing = None
        if existing is not None and existing != command:
            raise ValueError("guard login entry belongs to a different executable; preserve it")
        if remove:
            if existing is not None:
                winreg.DeleteValue(key, name)
            stop.write_text("stop\n")
            return {"installed": False, "stop_requested": True, "registry_value": name}
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
    stop.unlink(missing_ok=True)
    launched_at = time.time()
    with (parent / "publisher.log").open("ab") as log:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            value = _read_signal(policy)
            if (value.get("policy_sha256") == policy["sha256"]
                    and type(value.get("sampled_at")) in (int, float)
                    and value["sampled_at"] >= launched_at
                    and abs(time.time() - value["sampled_at"]) < policy["max_age_seconds"]):
                return {"installed": True, "registry_value": name,
                        "signal_fresh": True, "safe": not signal_reason(policy),
                        "publisher_pid": value.get("publisher_pid")}
        except (OSError, ValueError):
            pass
        if process.poll() is not None and process.returncode != 0:
            break
        time.sleep(0.1)
    raise ValueError("guard did not publish a fresh signal; inspect publisher.log or restart after a policy update")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "supervise", "install", "uninstall", "status", "sample"))
    parser.add_argument("--config", type=Path, default=Path(config_path("media-guard.toml")))
    parser.add_argument("--lease", help="Container-local path to the same host signal")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args, command = parser.parse_known_args(argv)
    if command and args.action != "supervise":
        parser.error("unexpected arguments")
    if args.lease and args.action != "supervise":
        parser.error("--lease is a supervisor-only path mapping")
    try:
        if args.action == "sample":
            available, commit = windows_memory()
            result = {"available_gib": available, "commit_free_gib": commit}
        else:
            policy = load_policy(args.config)
            if args.lease:
                policy["lease_path"] = args.lease
            if args.action == "status":
                reason = signal_reason(policy)
                result = {"safe": not reason, "reason": reason}
            elif args.dry_run or not args.confirm:
                result = {"dry_run": True, "action": args.action, "policy_sha256": policy["sha256"]}
            elif args.action in {"install", "uninstall"}:
                result = install(policy, args.config, remove=args.action == "uninstall")
            elif args.action == "run":
                return run_host(policy)
            else:
                if command and command[0] == "--":
                    command = command[1:]
                def terminated(signum, _frame):
                    raise SystemExit(128 + signum)
                signal.signal(signal.SIGTERM, terminated)
                return supervise(policy, command)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
