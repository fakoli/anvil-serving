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

import os
import shutil
import stat
import subprocess
import sys
import tempfile
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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if any(item == "--basetemp" or item.startswith("--basetemp=") for item in args):
        print("run_tests.py owns --basetemp; remove the explicit option", file=sys.stderr)
        return 2
    if not args:
        args = ["tests/", "-q"]
    untrusted = untrusted_ancestry(Path.cwd())
    if untrusted:
        print(
            "run_tests.py: working-directory ancestry violates the bootstrap trust"
            " contract; native boundary tests would fail with PRECONDITION_FAILED:",
            file=sys.stderr,
        )
        for problem in untrusted:
            print(f"  {problem}", file=sys.stderr)
        print(
            "remediate each directory above, e.g. `chmod g-w,o-w <directory>`"
            " (shared trees may need `find <root> -type d -exec chmod g-w,o-w {} +`)",
            file=sys.stderr,
        )
        return 2
    base_temp = tempfile.mkdtemp(prefix="anvil-serving-pytest-")
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", *args, "--basetemp", base_temp]
        )
        return completed.returncode
    finally:
        shutil.rmtree(base_temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
