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

POSIX temporary roots also satisfy native custody and remain outside Git.
An unsuitable system temp directory falls back to the user runtime directory
or home; only the unique run directory is created or removed, never a parent.
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
import time
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


def create_test_temporary_directory() -> str:
    """Select a short, private fixture root without relaxing product custody."""
    if sys.platform == "win32":
        return tempfile.mkdtemp(prefix="as-test-")
    from anvil_serving.propagation_native_acceptance import NativeAcceptanceError, _custody

    problems = []
    def runtime_directory() -> str:
        return os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.geteuid()}"

    for label, candidate in (("system temp", tempfile.gettempdir),
                             ("user runtime", runtime_directory), ("home", Path.home)):
        parent = label
        directory = None
        try:
            parent = Path(candidate()).resolve()
            _custody(parent, directory=True)
            if any(os.path.lexists(item / ".git") for item in (parent, *parent.parents)):
                raise ValueError("temporary root must be outside Git")
            directory = tempfile.mkdtemp(prefix="as-test-", dir=parent)
            _custody(directory, directory=True)
            return directory
        except (OSError, ValueError, RuntimeError, NativeAcceptanceError) as error:
            if directory is not None:
                shutil.rmtree(directory)
            problems.append(f"{parent}: {error}")
    raise ValueError("no suitable test temporary root; set TMPDIR to a trusted, non-Git directory: "
                     + "; ".join(problems))


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


def plan_launches(args: list[str], shards: int, coverage: bool, base: Path) -> list[tuple]:
    """Return (command, extra_env, log) per pytest process; one process when not sharding."""
    runner = [sys.executable, "-m", *(["coverage", "run", "-p", "-m"] if coverage else []), "pytest"]
    if shards == 1:
        env = {"COVERAGE_FILE": str(base / ".coverage")} if coverage else {}
        return [([*runner, *args, "--basetemp", str(base / "fixtures")], env, None)]
    # ponytail: option values that are existing paths would be mistaken for roots; none are used today.
    roots = [a for a in args if not a.startswith("-") and Path(a.split("::")[0]).exists()] or ["tests"]
    flags = [a for a in args if a not in roots]
    files = sorted({str(f) for r in roots
                    for f in (Path(r).rglob("test_*.py") if Path(r).is_dir() else [Path(r)])} | {r for r in roots if "::" in r})
    count = min(shards, len(files))
    launches = []
    for index in range(count):
        work = base / f"shard{index}"
        work.mkdir(mode=0o700)
        # JUnit goes under the private dir so the run never dirties the tree; main() copies it out.
        shard_flags = [f"--junitxml={work / 'junit.xml'}" if a.startswith("--junitxml=") else a for a in flags]
        env = {name: str(work) for name in ("TMPDIR", "TEMP", "TMP")}
        if coverage:
            env["COVERAGE_FILE"] = str(work / ".coverage")
        launches.append(([*runner, *shard_flags, *files[index::count], "--basetemp", str(work / "fixtures")],
                         env, base / f"shard{index}.log"))
    return launches


def publish_junit(args: list[str], base: Path) -> None:
    """Copy each shard's junit file next to the requested --junitxml path as <stem>-shardK.xml."""
    wanted = next((a.split("=", 1)[1] for a in args if a.startswith("--junitxml=")), None)
    for source in sorted(base.glob("shard*/junit.xml")):
        if wanted:
            shutil.copyfile(source, wanted.removesuffix(".xml") + f"-{source.parent.name}.xml")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--receipt", type=Path, default=Path(".pytest_cache/run-tests-receipt.json"))
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--coverage", action="store_true")
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
    processes: list = []
    printed: set = set()
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
        if options.shards < 1:
            problems.append("--shards must be at least 1")
        if options.timeout is not None and (not 0 < options.timeout < float("inf")):
            problems.append("--timeout must be finite and positive")
        env = dict(os.environ)
        env["ANVIL_SERVING_NO_HOME_ENV"] = "1"
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        problems.extend(preflight(Path.cwd(), env))
        if not problems:
            try:
                base_temp = create_test_temporary_directory()
                receipt["base_temp"] = base_temp
            except (OSError, ValueError) as error:
                problems.append(str(error))
        if problems:
            receipt.update(state="preflight_failed", exit_code=2, problems=problems)
            print("run_tests.py: " + "\n".join(problems), file=sys.stderr)
            return 2
        if options.preflight:
            receipt.update(state="preflight_passed", exit_code=0)
            return 0
        args = args or ["tests/", "-q"]
        for name in ("TMPDIR", "TEMP", "TMP"):
            env[name] = base_temp
        launches = plan_launches(args, options.shards, options.coverage, Path(base_temp))
        receipt.update(state="running", command=launches[0][0], commands=[item[0] for item in launches])
        write_receipt(options.receipt, receipt)
        if options.coverage:
            subprocess.run([sys.executable, "-m", "coverage", "erase"], env=env, check=True)
        deadline = None if options.timeout is None else time.monotonic() + options.timeout
        for command, shard_env, log in launches:
            stream = open(log, "wb") if log else None
            process = subprocess.Popen(command, env={**env, **shard_env}, start_new_session=os.name != "nt",
                                       stdout=stream, stderr=subprocess.STDOUT if stream else None)
            processes.append((process, log, stream))
        code = 0
        for process, log, stream in processes:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            shard_code = process.wait(timeout=remaining)
            code = code or shard_code
            if stream:
                stream.close()
                printed.add(log)
                print(f"===== shard {log.name} exit {shard_code} =====", flush=True)
                print(log.read_text(encoding="utf-8", errors="replace"), end="", flush=True)
        if options.coverage and code == 0:
            # ponytail: combined data lands in cwd/.coverage (gitignored); the report enforces fail_under.
            data = [os.path.dirname(item[1]["COVERAGE_FILE"]) for item in launches if "COVERAGE_FILE" in item[1]]
            for step in (["combine", *data], ["report"]):
                code = code or subprocess.run([sys.executable, "-m", "coverage", *step], env=env).returncode
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
        for process, _log, _stream in processes:
            if process.poll() is None or receipt["state"] in {"timed_out", "interrupted"}:
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
        for _process, log, stream in processes:
            if log and log not in printed and log.exists():
                if stream:
                    stream.close()
                print(f"===== shard {log.name} (interrupted) =====", flush=True)
                print(log.read_text(encoding="utf-8", errors="replace"), end="", flush=True)
        if base_temp:
            publish_junit(args, Path(base_temp))
            shutil.rmtree(base_temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
