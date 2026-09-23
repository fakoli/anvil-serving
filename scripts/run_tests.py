"""Run pytest with a process-unique base temp directory.

Windows pytest sessions otherwise share ``pytest-of-USER/pytest-current`` and
can turn an entirely green run into a cleanup-time ``PermissionError`` when
several worktrees test concurrently.

The wrapper also refuses to start when the working-directory ancestry is
untrusted: the native bootstrap file-read boundary walks every ancestor from
``/`` and refuses group/other-writable, non-sticky directories. A tree that
fails that contract turns ``test_bootstrap_opened_file`` cases into a cryptic
``PRECONDITION_FAILED`` deep inside the suite, so the wrapper reports the
offending directory and the remediation up front.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


def untrusted_ancestry(cwd: Path) -> list[str]:
    """Return human-readable reasons the cwd ancestry violates the trust contract."""

    if sys.platform == "win32":
        # Unix ownership and mode checks do not describe Windows ACLs.
        return []
    problems: list[str] = []
    effective_uid = os.geteuid()
    for directory in [cwd, *cwd.parents]:
        try:
            metadata = directory.stat()
        except OSError as error:
            problems.append(f"{directory}: unreadable metadata ({error})")
            continue
        if metadata.st_uid not in (effective_uid, 0):
            problems.append(
                f"{directory}: owned by uid {metadata.st_uid}, not the effective"
                f" user ({effective_uid}) or root"
            )
            continue
        writable = bool(metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
        sticky = bool(metadata.st_mode & stat.S_ISVTX)
        if writable and not sticky:
            problems.append(
                f"{directory}: group/other-writable, not sticky"
                f" (mode {stat.filemode(metadata.st_mode)})"
            )
    return problems


def preflight(cwd: Path, env: dict[str, str]) -> list[str]:
    problems = untrusted_ancestry(cwd)
    selected_python = shutil.which("python", path=str(Path(sys.executable).parent))
    if not selected_python or Path(selected_python).resolve() != Path(sys.executable).resolve():
        problems.append("the selected interpreter directory must provide a matching python for subprocess tests")
    if importlib.util.find_spec("pytest") is None:
        problems.append("pytest is missing from the selected interpreter; install .[dev]")
    # A direct scripts/ invocation otherwise searches the installed package first.
    sys.path.insert(0, str(cwd))
    package = importlib.util.find_spec("anvil_serving")
    if package is None or not package.origin or Path(package.origin).resolve() != cwd / "anvil_serving/__init__.py":
        problems.append("run from the intended repository root; anvil_serving resolves elsewhere")
    return problems


def source_identity() -> dict:
    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL, timeout=10)
    try:
        return {
            "head": git("rev-parse", "HEAD").decode().strip(),
            "tracked_diff_sha256": hashlib.sha256(git("diff", "HEAD", "--binary")).hexdigest(),
            "dirty": bool(git("status", "--porcelain")),
        }
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("cannot establish Git source identity") from error


def write_receipt(path: Path, receipt: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--receipt", type=Path, default=Path(".pytest_cache/run-tests-receipt.json"))
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--preflight", action="store_true")
    options, args = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    receipt = {
        "schema": "anvil-serving.test-run/v1", "run_id": str(uuid.uuid4()),
        "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
        "state": "preflight", "exit_code": None, "interpreter": sys.executable,
        "cwd": str(Path.cwd()), "source": None, "pytest_args": args,
    }
    # Overwrite any stale success before checking inputs or starting tests.
    write_receipt(options.receipt, receipt)
    base_temp = None
    process = None
    previous_handler = signal.getsignal(signal.SIGTERM)

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, interrupted)
        problems = []
        try:
            receipt["source"] = source_identity()
        except ValueError as error:
            problems.append(str(error))
        if any(item == "--basetemp" or item.startswith("--basetemp=") for item in args):
            problems.append("run_tests.py owns --basetemp; remove the explicit option")
        if options.timeout is not None and (not 0 < options.timeout < float("inf")):
            problems.append("--timeout must be finite and positive")
        env = dict(os.environ)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        problems.extend(preflight(Path.cwd(), env))
        if problems:
            receipt.update(state="preflight_failed", exit_code=2, problems=problems)
            print("run_tests.py: " + "\n".join(problems), file=sys.stderr)
            return 2
        if options.preflight:
            receipt.update(state="preflight_passed", exit_code=0)
            return 0
        args = args or ["tests/", "-q"]
        base_temp = tempfile.mkdtemp(prefix="anvil-serving-pytest-")
        command = [sys.executable, "-m", "pytest", *args, "--basetemp", base_temp]
        receipt.update(state="running", command=command)
        write_receipt(options.receipt, receipt)
        process = subprocess.Popen(command, env=env, start_new_session=os.name != "nt")
        code = process.wait(timeout=options.timeout)
        try:
            receipt["source_after"] = source_identity()
        except ValueError as error:
            receipt.update(state="source_unverified", exit_code=2, error=str(error))
            return 2
        if receipt["source_after"] != receipt["source"]:
            receipt.update(state="source_changed", exit_code=2)
            return 2
        receipt.update(state="passed" if code == 0 else "failed", exit_code=code)
        return code
    except subprocess.TimeoutExpired:
        receipt.update(state="timed_out", exit_code=124)
        return 124
    except KeyboardInterrupt:
        receipt.update(state="interrupted", exit_code=130)
        return 130
    except OSError as error:
        receipt.update(state="failed", exit_code=2, error=str(error))
        return 2
    finally:
        if process is not None and (process.poll() is None or receipt["state"] in {"timed_out", "interrupted"}):
            # Stop the entire isolated pytest process group on POSIX.
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        signal.signal(signal.SIGTERM, previous_handler)
        receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_receipt(options.receipt, receipt)
        if base_temp:
            shutil.rmtree(base_temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
